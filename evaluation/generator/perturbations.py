import random
import copy
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from evaluation.schema import EvaluationCase, EventData, SourceHealthRecord, TopologyNode

def apply_drop_one_signal(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Drops exactly one event from the event sequence."""
    c = copy.deepcopy(case)
    if len(c.events) > 1:
        idx_to_drop = rng.randint(0, len(c.events) - 1)
        dropped = c.events.pop(idx_to_drop)
        c.perturbations.append("drop_one_signal")
        c.tags.append(f"dropped_event:{dropped.service}")
    return c


def apply_drop_all_signals_one_source(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Drops all events originating from a chosen telemetry source and marks it DOWN."""
    c = copy.deepcopy(case)
    sources = list(set(e.source for e in c.events))
    if not sources:
        return c
    target_source = rng.choice(sources)
    c.events = [e for e in c.events if e.source != target_source]
    
    # Mark in source health
    c.source_health.append(SourceHealthRecord(
        source=target_source,
        timestamp=c.ground_truth.onset_times.get(c.ground_truth.true_root_causes[0], "2026-09-01T12:00:00Z"),
        status="DOWN"
    ))
    c.perturbations.append("drop_all_signals_one_source")
    c.tags.append(f"source_down:{target_source}")

    # If all events for true root causes were eliminated, model must ABSTAIN
    remaining_services = set(e.service for e in c.events)
    if not any(r in remaining_services for r in c.ground_truth.true_root_causes):
        c.ground_truth.expected_verdict = "ABSTAIN"
        c.tags.append("abstain-required")
    return c


def apply_delay_and_reorder(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Artificially delays observed_time and shuffles arrival order."""
    c = copy.deepcopy(case)
    for e in c.events:
        delay_sec = rng.uniform(5.0, 30.0)
        dt = datetime.fromisoformat(e.observed_time)
        e.observed_time = (dt + timedelta(seconds=delay_sec)).isoformat()
    # Shuffle events to simulate out-of-order queue delivery
    rng.shuffle(c.events)
    c.perturbations.append("delay_and_reorder")
    c.tags.append("out_of_order")
    return c


def apply_duplicate(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Injects a sudden burst of duplicates for symptom events."""
    c = copy.deepcopy(case)
    if not c.events:
        return c
    # Pick a symptom event (prefer non-root)
    symptom_events = [e for e in c.events if e.service not in c.ground_truth.true_root_causes]
    target = rng.choice(symptom_events) if symptom_events else c.events[-1]
    
    dup_count = rng.randint(5, 12)
    duplicates = []
    base_t = datetime.fromisoformat(target.event_time)
    for i in range(dup_count):
        dup_ev = copy.deepcopy(target)
        dup_ev.event_id = f"{target.event_id or 'evt'}_dup_{i}"
        dup_ev.event_time = (base_t + timedelta(milliseconds=rng.randint(50, 800))).isoformat()
        dup_ev.observed_time = dup_ev.event_time
        duplicates.append(dup_ev)
    
    c.events.extend(duplicates)
    c.events.sort(key=lambda e: e.event_time)
    c.perturbations.append("duplicate")
    c.tags.append("duplicate_burst")
    return c


def apply_clock_skew(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Applies unsynchronized clock skew (shifts event_time ahead or behind by 20-45s)."""
    c = copy.deepcopy(case)
    if not c.events:
        return c
    services = list(set(e.service for e in c.events))
    skew_service = rng.choice(services)
    skew_sec = rng.choice([-35.0, -20.0, 25.0, 40.0])
    
    for e in c.events:
        if e.service == skew_service:
            dt = datetime.fromisoformat(e.event_time)
            e.event_time = (dt + timedelta(seconds=skew_sec)).isoformat()
            
    c.events.sort(key=lambda e: e.event_time)
    c.perturbations.append("clock_skew")
    c.tags.append(f"clock_skew:{skew_service}:{int(skew_sec)}s")
    return c


def apply_second_independent_failure(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Adds an independent second root failure occurring within the same time window."""
    c = copy.deepcopy(case)
    # Pick a node in topology that is not currently a root cause
    all_nodes = [n.id for n in c.topology.nodes]
    available = [n for n in all_nodes if n not in c.ground_truth.true_root_causes]
    if not available:
        return c
    second_root = rng.choice(available)
    base_t = datetime.fromisoformat(c.events[0].event_time)
    t_second = base_t + timedelta(seconds=rng.uniform(2.0, 10.0))
    
    sec_event = EventData(
        event_id=f"evt_sec_root_{second_root}",
        event_time=t_second.isoformat(),
        observed_time=t_second.isoformat(),
        source="datadog",
        service=second_root,
        metric="CPUUtilization" if "Compute" in second_root or "EC2" in second_root else "ErrorRate",
        value=95.0,
        severity="CRITICAL"
    )
    c.events.append(sec_event)
    c.events.sort(key=lambda e: e.event_time)
    
    c.ground_truth.true_root_causes.append(second_root)
    c.ground_truth.onset_times[second_root] = t_second.isoformat()
    c.perturbations.append("second_independent_failure")
    c.tags.append("multi-root")
    return c


def apply_severe_decoy(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Injects a severe unrelated decoy event later in timeline on a non-root service."""
    c = copy.deepcopy(case)
    all_nodes = [n.id for n in c.topology.nodes]
    non_roots = [n for n in all_nodes if n not in c.ground_truth.true_root_causes]
    if not non_roots:
        return c
    decoy_service = rng.choice(non_roots)
    base_t = datetime.fromisoformat(c.events[-1].event_time)
    decoy_t = base_t + timedelta(seconds=rng.uniform(5.0, 15.0))
    
    decoy_event = EventData(
        event_id=f"evt_decoy_{decoy_service}",
        event_time=decoy_t.isoformat(),
        observed_time=decoy_t.isoformat(),
        source="cloudwatch",
        service=decoy_service,
        metric="CPUUtilization",
        value=99.9,
        severity="CRITICAL"
    )
    c.events.append(decoy_event)
    c.perturbations.append("severe_decoy")
    c.tags.append(f"decoy:{decoy_service}")
    return c


def apply_change_topology_mid_incident(case: EvaluationCase, rng: random.Random) -> EvaluationCase:
    """Simulates dynamic topology expansion mid-incident by introducing a dynamic node & edge."""
    c = copy.deepcopy(case)
    new_node_id = f"SidecarProxy_{rng.randint(100, 999)}"
    c.topology.nodes.append(TopologyNode(id=new_node_id, type="service", label="Dynamic Envoy Proxy", criticality=0.7))
    
    # Connect between an existing caller and dependency
    if c.topology.edges:
        chosen_edge = rng.choice(c.topology.edges)
        src, dst = chosen_edge[0], chosen_edge[1]
        c.topology.edges.append([src, new_node_id])
        c.topology.edges.append([new_node_id, dst])
        
    base_t = datetime.fromisoformat(c.events[0].event_time)
    t_proxy = base_t + timedelta(seconds=rng.uniform(3.0, 8.0))
    c.events.append(EventData(
        event_id=f"evt_{new_node_id}",
        event_time=t_proxy.isoformat(),
        observed_time=t_proxy.isoformat(),
        source="otel",
        service=new_node_id,
        metric="Latency",
        value=1800.0,
        severity="HIGH"
    ))
    c.events.sort(key=lambda e: e.event_time)
    c.topology.topology_version = "dyn-v2"
    c.perturbations.append("change_topology_mid_incident")
    c.tags.append("topology_change")
    return c
