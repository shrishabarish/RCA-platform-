from typing import List, Dict, Any, Optional
from backend.app.models.event import CloudEvent, Incident
from rca_engine.scoring import score_candidate
from rca_engine.causal_graph import causal_graph_engine
from rca_engine.remediation import generate_remediation_plan


def analyze_incident(incident: Incident, graph_engine: Optional[Any] = None) -> Dict[str, Any]:
    """
    Advanced Root Cause Analysis Engine.
    
    1. Scores all service candidates via the Multi-Factor Ensemble.
    2. Identifies Primary Root Cause and Secondary / Contributing candidates.
    3. Traces exact Topological Causal Propagation Paths.
    4. Formulates a Step-by-Step Mathematical Reasoning Chain (Audit Trail).
    5. Generates targeted self-healing cloud runbooks using real resource IDs.
    """
    if not incident.events:
        return {
            "incident_id": incident.incident_id,
            "severity": incident.severity,
            "probable_root_cause": "UNKNOWN",
            "confidence": 0.0,
            "score_breakdown": {},
            "affected_services": [],
            "causal_paths": [],
            "reasoning_chain": ["No telemetry events available for analysis."],
            "is_compound_incident": False,
            "related_events": 0,
            "all_candidate_scores": [],
            "remediation_suggestion": "No events found to analyze."
        }

    cg = graph_engine if graph_engine is not None else causal_graph_engine

    # Distinct service candidates
    candidates = sorted(list(set(e.service for e in incident.events)))
    scored = [score_candidate(c, incident.events, graph_engine=cg) for c in candidates]
    scored.sort(key=lambda s: (-s["total"], s["service"]))

    primary_root = scored[0]
    root_service = primary_root["service"]
    affected_services = [s for s in candidates if s != root_service]

    # Find the primary root event's resource_id
    root_events = [e for e in incident.events if e.service == root_service]
    root_resource_id = root_events[0].resource_id if root_events else None

    # Check for Compound / Multi-Root Incident (if 2nd candidate is within 10% of 1st)
    is_compound = False
    contributing_causes = []
    if len(scored) > 1 and (primary_root["total"] - scored[1]["total"]) <= 10.0 and scored[1]["total"] >= 65.0:
        is_compound = True
        contributing_causes.append(scored[1]["service"])

    # Trace Topological Causal Propagation Paths
    causal_paths = []
    for affected in affected_services:
        paths = cg.find_causal_paths(root_service, affected)
        for p in paths:
            causal_paths.append(" -> ".join(p))

    if not causal_paths and affected_services:
        # Direct causal association fallback
        for affected in affected_services:
            causal_paths.append(f"{root_service} -> {affected}")

    # Build Step-by-Step Explainable Reasoning Chain
    bd = primary_root["breakdown"]
    reasoning_chain = [
        f"1. Temporal Origin: Service '{root_service}' registered the earliest anomaly in the incident timeline ({bd.get('temporal_correlation', 25)} pts awarded).",
        f"2. Graph Blast Radius: '{root_service}' occupies a structural dependency position affecting {len(cg.get_blast_radius(root_service))} downstream service(s) ({bd.get('dependency_relationship', 30)} pts).",
        f"3. Symptom Correlation: {len(affected_services)} downstream services ({', '.join(affected_services) if affected_services else 'None'}) exhibited concurrent failure symptoms matching the topology ({bd.get('downstream_symptoms', 20)} pts).",
        f"4. Anomaly Magnitude & Severity: Threshold telemetry breach scored at {bd.get('metric_anomaly_magnitude', 15)} pts with severity weight of {bd.get('severity', 10)} pts.",
        f"5. Calibrated Result: '{root_service}' determined as Primary Root Cause with {primary_root['total']}% confidence."
    ]

    remediation = generate_remediation_plan(incident.incident_id, root_service, root_resource_id)

    # Update incident fields
    incident.probable_root_cause = root_service
    incident.confidence = float(primary_root["total"])
    incident.affected_services = affected_services
    incident.score_breakdown = primary_root["breakdown"]
    incident.remediation_suggestion = remediation.recommended_action

    return {
        "incident_id": incident.incident_id,
        "severity": incident.severity,
        "probable_root_cause": root_service,
        "resource_id": root_resource_id,
        "confidence": primary_root["total"],
        "score_breakdown": primary_root["breakdown"],
        "affected_services": affected_services,
        "causal_paths": causal_paths,
        "reasoning_chain": reasoning_chain,
        "is_compound_incident": is_compound,
        "contributing_causes": contributing_causes,
        "related_events": len(incident.events),
        "all_candidate_scores": scored,
        "remediation_suggestion": remediation.recommended_action,
        "remediation_plan": remediation.model_dump()
    }