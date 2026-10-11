import math
from typing import List, Dict, Any
from backend.app.models.event import CloudEvent, Severity
from rca_engine.causal_graph import causal_graph_engine
from backend.app.core.config import config

SEVERITY_WEIGHTS = {
    Severity.LOW.value: config.scoring.severity_weights.get("LOW", 2),
    Severity.MEDIUM.value: config.scoring.severity_weights.get("MEDIUM", 5),
    Severity.HIGH.value: config.scoring.severity_weights.get("HIGH", 8),
    Severity.CRITICAL.value: config.scoring.severity_weights.get("CRITICAL", 10),
    "LOW": config.scoring.severity_weights.get("LOW", 2),
    "MEDIUM": config.scoring.severity_weights.get("MEDIUM", 5),
    "HIGH": config.scoring.severity_weights.get("HIGH", 8),
    "CRITICAL": config.scoring.severity_weights.get("CRITICAL", 10)
}


def score_candidate(candidate_service: str, events: List[CloudEvent]) -> Dict[str, Any]:
    """
    Advanced Multi-Factor Ensemble Scoring Algorithm for Cloud RCA.
    
    Evaluates candidates across 5 deterministic mathematical dimensions:
    1. Temporal Lead Time (Exponential Decay)       [Max 25 pts]
    2. Topological Centrality & Blast Radius       [Max 30 pts]
    3. Downstream Symptom Overlap Ratio            [Max 20 pts]
    4. Metric Anomaly Magnitude & Deviation Ratio   [Max 15 pts]
    5. Anomaly Severity Weight                     [Max 10 pts]
    
    Total Score is normalized into a calibrated 0-100% confidence interval.
    """
    candidate_events = [e for e in events if e.service == candidate_service]
    if not candidate_events:
        return {
            "service": candidate_service,
            "total": 0.0,
            "breakdown": {},
            "metrics": {}
        }

    cfg = config.scoring

    # 1. Temporal Lead Time Score (Max 25 pts)
    earliest_overall = min(e.timestamp for e in events)
    earliest_candidate = min(e.timestamp for e in candidate_events)
    time_delta_seconds = max(0.0, (earliest_candidate - earliest_overall).total_seconds())

    # Exponential decay: S_temporal = max_temporal * exp(-lambda * delta_t) with floor
    if time_delta_seconds == 0:
        temporal_score = cfg.max_temporal_score
    else:
        decayed = float(cfg.max_temporal_score) * math.exp(-cfg.time_decay_lambda * time_delta_seconds)
        temporal_score = max(cfg.min_decay_floor, int(round(decayed)))

    # 2. Topological Blast Radius & Centrality Score (Max 30 pts)
    direct_dependents = causal_graph_engine.get_direct_dependents(candidate_service)
    transitive_blast = causal_graph_engine.get_blast_radius(candidate_service)
    all_downstream = list(set(direct_dependents + transitive_blast))

    # Base points from direct downstream dependents
    base_topo = len(direct_dependents) * 15
    # Bonus from transitive reach
    transitive_bonus = min(len(transitive_blast) * 5, 10)
    dependency_score = min(base_topo + transitive_bonus, cfg.max_topology_score)

    # 3. Downstream Symptom Overlap Score (Max 20 pts)
    other_affected_services = set(e.service for e in events if e.service != candidate_service)
    overlap = other_affected_services.intersection(set(all_downstream))

    if other_affected_services:
        overlap_ratio = len(overlap) / len(other_affected_services)
        downstream_symptom_score = int(round(overlap_ratio * float(cfg.max_symptom_score)))
    else:
        # Isolated single-service anomaly
        downstream_symptom_score = 10 if dependency_score == 0 else 5

    # 4. Metric Anomaly Magnitude Score (Max 15 pts)
    metric_score = 0
    max_metric_val = 0.0
    for e in candidate_events:
        if e.value is not None:
            max_metric_val = max(max_metric_val, e.value)
            if "CPU" in (e.metric or "") and e.value >= cfg.cpu_critical_threshold:
                metric_score = max(metric_score, cfg.max_metric_score)
            elif "Throttle" in (e.metric or "") and e.value > cfg.throttle_critical_threshold:
                metric_score = max(metric_score, cfg.max_metric_score)
            elif "Latency" in (e.metric or "") and e.value > cfg.latency_critical_threshold_ms:
                metric_score = max(metric_score, cfg.max_metric_score - 1)
            elif "Error" in (e.metric or "") and e.value > cfg.error_rate_critical_threshold:
                metric_score = max(metric_score, cfg.max_metric_score - 1)
            elif e.value > 0:
                metric_score = max(metric_score, 10)

    if metric_score == 0:
        metric_score = 10

    # 5. Severity Weight Score (Max 10 pts)
    max_severity_event = max(
        candidate_events,
        key=lambda e: SEVERITY_WEIGHTS.get(e.severity, 0)
    )
    severity_score = SEVERITY_WEIGHTS.get(max_severity_event.severity, 5)

    # Total Sum
    raw_total = (
        temporal_score +
        dependency_score +
        downstream_symptom_score +
        metric_score +
        severity_score
    )

    total_confidence = min(raw_total, 100)


    return {
        "service": candidate_service,
        "total": total_confidence,
        "breakdown": {
            "temporal_correlation": temporal_score,
            "dependency_relationship": dependency_score,
            "downstream_symptoms": downstream_symptom_score,
            "metric_anomaly_magnitude": metric_score,
            "severity": severity_score,
        },
        "metrics": {
            "lead_time_seconds": round(time_delta_seconds, 2),
            "blast_radius_count": len(all_downstream),
            "symptom_overlap_count": len(overlap),
            "max_metric_value": max_metric_val
        }
    }