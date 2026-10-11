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
        generator_version="1.1.0",
        seed=seed,
        topology=topology,
        events=events,
        source_health=source_health,
        ground_truth=GroundTruth(
            true_root_causes=chosen_roots,
            onset_times=onset_times,
            symptom_to_cause=symptom_to_cause,
            expected_verdict="DIAGNOSE",
            incident_groups=[[e.event_id for e in events if e.event_id]]
        ),
        perturbations=[],
        tags=["generated", f"{len(topology.nodes)}-node", "multi-root" if num_roots > 1 else "single-root"]
    )


def create_hard_variant(base_case: EvaluationCase, hard_type: str, case_id: str, seed: int) -> EvaluationCase:
    """
    Creates an adversarial hard case where the root cause is NOT the earliest anomaly.
    Target: Earliest Anomaly Top-1 accuracy on hard cases must be < 60%.
    """
    import copy
    rng = random.Random(seed)
    c = copy.deepcopy(base_case)
    c.case_id = case_id
    c.base_case_id = base_case.case_id
    c.generator_version = "1.1.0"
    c.is_hard = True
    c.tags.append("hard")
    
    root = c.ground_truth.true_root_causes[0]
    base_t = datetime.fromisoformat(c.events[0].event_time)

    if hard_type == "slow_burn":
        # Root metrics slowly degrade and cross threshold AFTER callers alert
        c.tags.append("slow-burn")
        for e in c.events:
            if e.service == root:
                dt = datetime.fromisoformat(e.event_time)
                e.event_time = (dt + timedelta(seconds=rng.uniform(30.0, 50.0))).isoformat()
                e.observed_time = e.event_time
        c.events.sort(key=lambda e: e.event_time)

    elif hard_type == "pre_incident_alert":
        # An unrelated leaf service emits a transient severe anomaly 15-25s before incident
        c.tags.append("pre-incident-decoy")
        all_nodes = [n.id for n in c.topology.nodes]
        unrelated = [n for n in all_nodes if n not in c.ground_truth.true_root_causes and n not in c.ground_truth.symptom_to_cause]
        decoy_node = rng.choice(unrelated) if unrelated else "UnrelatedLeafService"
        decoy_t = base_t - timedelta(seconds=rng.uniform(15.0, 30.0))
        decoy_ev = EventData(
            event_id=f"evt_pre_{case_id}",
            event_time=decoy_t.isoformat(),
            observed_time=decoy_t.isoformat(),
            source="cloudwatch",
            service=decoy_node,
            metric="CPUUtilization",
            value=98.5,
            severity="CRITICAL"
        )
        c.events.append(decoy_ev)
        c.events.sort(key=lambda e: e.event_time)

    elif hard_type == "flapping_decoy":
        # Decoy service flaps right before incident
        c.tags.append("flapping-decoy")
        all_nodes = [n.id for n in c.topology.nodes]
        unrelated = [n for n in all_nodes if n not in c.ground_truth.true_root_causes]
        decoy_node = rng.choice(unrelated) if unrelated else "DecoyService"
        for i in range(2):
            t_flap = base_t - timedelta(seconds=(25.0 - i * 10.0))
            c.events.append(EventData(
                event_id=f"evt_flap_{case_id}_{i}",
                event_time=t_flap.isoformat(),
                observed_time=t_flap.isoformat(),
                source="datadog",
                service=decoy_node,
                metric="ErrorRate",
                value=88.0,
                severity="HIGH"
            ))
        c.events.sort(key=lambda e: e.event_time)

    elif hard_type == "root_clock_skew":
        # Root telemetry collector clock is skewed forward by +45s
        c.tags.append("root-clock-skew")
        for e in c.events:
            if e.service == root:
                dt = datetime.fromisoformat(e.event_time)
                e.event_time = (dt + timedelta(seconds=45.0)).isoformat()
                e.observed_time = e.event_time
        c.events.sort(key=lambda e: e.event_time)

    elif hard_type == "missing_root_telemetry":
        # Root telemetry is absent; only caller symptoms present
        c.tags.append("missing-root-telemetry")
        c.events = [e for e in c.events if e.service != root]

    c.perturbations.append(f"hard_{hard_type}")
    return c


def create_abstain_variant(base_case: EvaluationCase, abstain_type: str, case_id: str, seed: int) -> EvaluationCase:
    """
    Creates an abstention case where the RCA engine MUST abstain.
    """
    import copy
    rng = random.Random(seed)
    c = copy.deepcopy(base_case)
    c.case_id = case_id
    c.base_case_id = base_case.case_id
    c.generator_version = "1.1.0"
    c.ground_truth.expected_verdict = "ABSTAIN"
    c.tags.append("abstain-required")

    root = c.ground_truth.true_root_causes[0] if c.ground_truth.true_root_causes else "Unknown"
    base_t = datetime.fromisoformat(c.events[0].event_time) if c.events else datetime.now(timezone.utc)

    if abstain_type == "source_down":
        # Root's telemetry source goes down entirely, leaving telemetry unconfirmed
        c.tags.append("source-down")
        root_sources = list(set(e.source for e in c.events if e.service == root))
        target_source = root_sources[0] if root_sources else "cloudwatch"
        c.events = [e for e in c.events if e.source != target_source]
        c.source_health.append(SourceHealthRecord(source=target_source, timestamp=base_t.isoformat(), status="DOWN"))

    elif abstain_type == "pipeline_outage":
        # Telemetry ingestion pipeline outage: root and middle tier drop, only edge symptoms arrive
        c.tags.append("pipeline-outage")
        c.events = [e for e in c.events if e.service != root and e.severity in ("LOW", "MEDIUM")]
        for s in SOURCES:
            c.source_health.append(SourceHealthRecord(source=s, timestamp=base_t.isoformat(), status="DOWN"))

    elif abstain_type == "contradictory":
        # Contradictory telemetry signals pointing to independent mutually incompatible services
        c.tags.append("contradictory-evidence")
        all_nodes = [n.id for n in c.topology.nodes]
        candidates = [n for n in all_nodes if n != root]
        alt_root = rng.choice(candidates) if candidates else "AltRoot"
        t_alt = base_t
        c.events.append(EventData(
            event_id=f"evt_contra_{case_id}",
            event_time=t_alt.isoformat(),
            observed_time=t_alt.isoformat(),
            source="otel",
            service=alt_root,
            metric="CPUUtilization",
            value=99.0,
            severity="CRITICAL"
        ))
        c.events.sort(key=lambda e: e.event_time)

    elif abstain_type == "quiet":
        # Quiet timeline: no anomalies present, only routine low-severity pings
        c.tags.append("quiet-no-anomalies")
        c.ground_truth.true_root_causes = []
        c.events = [
            EventData(
                event_id=f"evt_quiet_{case_id}_{i}",
                event_time=(base_t + timedelta(seconds=i*10)).isoformat(),
                observed_time=(base_t + timedelta(seconds=i*10)).isoformat(),
                source="cloudwatch",
                service=c.topology.nodes[i % len(c.topology.nodes)].id,
                metric="RoutineHeartbeat",
                value=5.0,
                severity="LOW"
            )
            for i in range(3)
        ]

    c.perturbations.append(f"abstain_{abstain_type}")
    return c


def create_multi_incident_variant(base_case: EvaluationCase, multi_type: str, case_id: str, seed: int) -> EvaluationCase:
    """
    Creates a case containing two distinct incidents to measure False Merge Rate.
    """
    import copy
    rng = random.Random(seed)
    c = copy.deepcopy(base_case)
    c.case_id = case_id
    c.base_case_id = base_case.case_id
    c.generator_version = "1.1.0"
    c.tags.extend(["multi-incident", "disjoint-failures"])
    
    all_nodes = [n.id for n in c.topology.nodes]
    available = [n for n in all_nodes if n not in c.ground_truth.true_root_causes]
    second_root = rng.choice(available) if available else "IndependentServiceB"
    
    inc1_ids = [e.event_id for e in c.events if e.event_id]
    
    base_t = datetime.fromisoformat(c.events[0].event_time)
    if multi_type == "separated_time":
        # 300 seconds gap between incident 1 and incident 2
        t_inc2 = base_t + timedelta(seconds=300.0)
    else:
        # Overlapping in time, but topologically independent
        t_inc2 = base_t + timedelta(seconds=15.0)

    inc2_ev1 = EventData(
        event_id=f"evt_inc2_root_{case_id}",
        event_time=t_inc2.isoformat(),
        observed_time=t_inc2.isoformat(),
        source="datadog",
        service=second_root,
        metric="CPUUtilization",
        value=97.0,
        severity="CRITICAL"
    )
    inc2_ev2 = EventData(
        event_id=f"evt_inc2_sym_{case_id}",
        event_time=(t_inc2 + timedelta(seconds=5.0)).isoformat(),
        observed_time=(t_inc2 + timedelta(seconds=5.0)).isoformat(),
        source="cloudwatch",
        service=second_root,
        metric="ThrottleRequests",
        value=1500.0,
        severity="HIGH"
    )
    inc2_ids = [inc2_ev1.event_id, inc2_ev2.event_id]
    
    c.events.extend([inc2_ev1, inc2_ev2])
    c.events.sort(key=lambda e: e.event_time)
    
    c.ground_truth.true_root_causes.append(second_root)
    c.ground_truth.onset_times[second_root] = t_inc2.isoformat()
    c.ground_truth.incident_groups = [inc1_ids, inc2_ids]
    c.perturbations.append(f"multi_incident_{multi_type}")
    return c
