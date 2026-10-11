import random
import networkx as nx
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple
from evaluation.schema import (
    EvaluationCase,
    TopologyData,
    EventData,
    SourceHealthRecord,
    GroundTruth
)

SOURCES = ["cloudwatch", "datadog", "otel", "syslog"]

METRICS_BY_TYPE = {
    "compute": [("CPUUtilization", 92.0, 99.5, "HIGH", "CRITICAL"), ("MemoryPressure", 85.0, 96.0, "HIGH", "HIGH")],
    "database": [("ThrottleRequests", 1100.0, 2400.0, "HIGH", "CRITICAL"), ("ConnectionTimeout", 30.0, 90.0, "HIGH", "CRITICAL")],
    "cache": [("CacheEvictionRate", 5000.0, 15000.0, "HIGH", "HIGH"), ("RedisCPUSpike", 95.0, 100.0, "CRITICAL", "CRITICAL")],
    "storage": [("S3SlowDown503", 200.0, 800.0, "HIGH", "HIGH"), ("IOPSExhausted", 80.0, 100.0, "HIGH", "CRITICAL")],
    "auth": [("AuthTokenErrorRate", 60.0, 95.0, "HIGH", "CRITICAL"), ("JWTSignatureFailures", 50.0, 85.0, "HIGH", "HIGH")],
    "service": [("Latency", 2100.0, 4500.0, "MEDIUM", "HIGH"), ("HTTP500Errors", 15.0, 45.0, "HIGH", "CRITICAL")],
    "worker": [("MemoryUtilization", 90.0, 99.0, "HIGH", "CRITICAL"), ("JobQueueLag", 150.0, 600.0, "HIGH", "HIGH")],
    "frontend": [("5xxErrorRate", 15.0, 50.0, "HIGH", "CRITICAL"), ("CheckoutFailures", 10.0, 35.0, "HIGH", "CRITICAL")],
    "queue": [("KafkaLag", 5000.0, 25000.0, "HIGH", "HIGH"), ("PartitionOffline", 1.0, 3.0, "CRITICAL", "CRITICAL")],
    "lambda": [("ConcurrencyThrottle", 800.0, 2000.0, "HIGH", "CRITICAL"), ("ExecutionTimeout", 25.0, 60.0, "HIGH", "CRITICAL")],
}


def simulate_case(
    topology: TopologyData,
    family_id: str,
    case_id: str,
    seed: int,
    is_multi_root: bool = False
) -> EvaluationCase:
    rng = random.Random(seed)
    
    # Build NetworkX DiGraph where edges are (caller, dependency)
    g = nx.DiGraph()
    for n in topology.nodes:
        g.add_node(n.id, **n.model_dump())
    for src, dst in topology.edges:
        g.add_edge(src, dst)
        
    # In reversed graph, edges point from dependency to caller (failure propagation flow)
    rev_g = g.reverse()
    
    # Pick root candidate(s): prefer nodes with higher criticality (databases, compute, auth, etc.)
    eligible_roots = [n.id for n in topology.nodes if n.criticality >= 0.8]
    if not eligible_roots:
        eligible_roots = [n.id for n in topology.nodes]
        
    num_roots = 2 if is_multi_root and len(eligible_roots) >= 2 else 1
    chosen_roots = rng.sample(eligible_roots, num_roots)
    
    base_t = datetime(2026, 9, 3, 10, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 1000))
    
    events: List[EventData] = []
    onset_times: Dict[str, str] = {}
    symptom_to_cause: Dict[str, str] = {}
    
    event_counter = 1
    
    for r_idx, root in enumerate(chosen_roots):
        r_onset = base_t + timedelta(seconds=(r_idx * rng.uniform(2.0, 8.0)))
        onset_times[root] = r_onset.isoformat()
        
        # 1. Emit root event
        node_type = g.nodes[root].get("type", "service")
        metric_opts = METRICS_BY_TYPE.get(node_type, [("ErrorRate", 55.0, 95.0, "HIGH", "CRITICAL")])
        m_name, v_min, v_max, s_norm, s_crit = rng.choice(metric_opts)
        val = round(rng.uniform(v_min, v_max), 2)
        sev = s_crit if rng.random() > 0.4 else s_norm
        
        root_ev = EventData(
            event_id=f"evt_{case_id}_{event_counter:03d}",
            event_time=r_onset.isoformat(),
            observed_time=r_onset.isoformat(),
            source=rng.choice(SOURCES),
            service=root,
            metric=m_name,
            value=val,
            severity=sev
        )
        events.append(root_ev)
        event_counter += 1
        
        # 2. Propagate failures along reverse graph (caller dependencies)
        # Find all reachable callers from root
        try:
            callers = nx.descendants(rev_g, root)
        except Exception:
            callers = set()
            
        current_time = r_onset
        # Sort callers by topological distance from root
        caller_distances = []
        for c in callers:
            try:
                shortest_p = nx.shortest_path_length(rev_g, source=root, target=c)
                caller_distances.append((shortest_p, c))
            except nx.NetworkXNoPath:
                pass
        caller_distances.sort(key=lambda x: x[0])
        
        for dist, caller in caller_distances:
            if caller in chosen_roots:
                continue
            symptom_to_cause[caller] = root
            
            # Propagation delay per hop
            delay = rng.uniform(3.0 * dist, 8.0 * dist)
            c_time = r_onset + timedelta(seconds=delay)
            
            c_type = g.nodes[caller].get("type", "service")
            c_metric_opts = METRICS_BY_TYPE.get(c_type, [("Latency", 2100.0, 4200.0, "MEDIUM", "HIGH")])
            cm_name, cv_min, cv_max, cs_norm, cs_crit = rng.choice(c_metric_opts)
            c_val = round(rng.uniform(cv_min, cv_max), 2)
            c_sev = cs_crit if rng.random() > 0.6 else cs_norm
            
            symptom_ev = EventData(
                event_id=f"evt_{case_id}_{event_counter:03d}",
                event_time=c_time.isoformat(),
                observed_time=c_time.isoformat(),
                source=rng.choice(SOURCES),
                service=caller,
                metric=cm_name,
                value=c_val,
                severity=c_sev
            )
            events.append(symptom_ev)
            event_counter += 1
            
    # 3. Add realistic background noise: 1-2 harmless low-severity events on unaffected nodes
    unaffected = [n.id for n in topology.nodes if n.id not in chosen_roots and n.id not in symptom_to_cause]
    if unaffected and rng.random() > 0.5:
        noise_node = rng.choice(unaffected)
        n_time = base_t + timedelta(seconds=rng.uniform(1.0, 30.0))
        noise_ev = EventData(
            event_id=f"evt_{case_id}_{event_counter:03d}",
            event_time=n_time.isoformat(),
            observed_time=n_time.isoformat(),
            source=rng.choice(SOURCES),
            service=noise_node,
            metric="RoutineHealthCheckLatency",
            value=12.0,
            severity="LOW"
        )
        events.append(noise_ev)
        event_counter += 1

    # Sort events chronologically by event_time
    events.sort(key=lambda e: e.event_time)
    
    source_health = [
        SourceHealthRecord(source=s, timestamp=base_t.isoformat(), status="HEALTHY")
        for s in SOURCES
    ]
    
    return EvaluationCase(
        case_id=case_id,
        family_id=family_id,
        generator_version="1.0",
        seed=seed,
        topology=topology,
        events=events,
        source_health=source_health,
        ground_truth=GroundTruth(
            true_root_causes=chosen_roots,
            onset_times=onset_times,
            symptom_to_cause=symptom_to_cause,
            expected_verdict="DIAGNOSE"
        ),
        perturbations=[],
        tags=["generated", f"{len(topology.nodes)}-node", "multi-root" if num_roots > 1 else "single-root"]
    )
