from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline
from evaluation.schema import EvaluationCase, EventData, TopologyData, SourceHealthRecord, GroundTruth
from rca_v3.models import CandidateDiagnosis, DiagnosisResult, Verdict
from rca_v3.config import RCAv3Config
from rca_v3.verifier import EvidenceVerifierV3
from rca_v3.causal_graph import CausalGraphV3
from rca_v2.evidence import EvidenceEvaluator
from rca_v2.source_health import SourceHealthTracker
from rca_v2.telemetry_policy import TelemetryPolicy


def diagnose_hybrid(
    events: List[Any],
    topology: Any,
    source_health: Optional[List[Any]] = None,
    telemetry_policy: Optional[TelemetryPolicy] = None,
    config: Optional[RCAv3Config] = None
) -> DiagnosisResult:
    """
    Hybrid 'rca_v3h' engine:
    - CurrentScorer_Clean ranking (scoring candidates on preprocessed anomaly telemetry).
    - rca_v3 verifier and safety guards (coverage, margin, silent ancestor cap, contradiction blocking).
    """
    cfg = config or RCAv3Config(clean_heartbeats=True, enable_silent_upstream_guard=True)
    
    # 1. Clean events (remove heartbeats and non-anomalous LOW telemetry)
    clean_events = [
        e for e in events
        if (str(getattr(e, "severity", "") or (e.get("severity") if isinstance(e, dict) else "")).upper() in {"CRITICAL", "HIGH", "MEDIUM"})
        and (getattr(e, "metric", "") or (e.get("metric") if isinstance(e, dict) else "")).lower() != "heartbeat"
    ]
    if not clean_events:
        clean_events = list(events)

    # 2. Score candidates using CurrentScorer_CaseTopology
    scorer = CurrentScorerCaseTopologyBaseline()
    
    # Wrap into dummy EvaluationCase for baseline call
    nodes = getattr(topology, "nodes", []) if hasattr(topology, "nodes") else topology.get("nodes", [])
    edges = getattr(topology, "edges", []) if hasattr(topology, "edges") else topology.get("edges", [])
    t_data = TopologyData(nodes=nodes, edges=edges)
    
    ev_objs = []
    for i, e in enumerate(clean_events):
        ev_id = getattr(e, "event_id", None) or (e.get("event_id") if isinstance(e, dict) else f"evt_{i}")
        t_str = getattr(e, "event_time", None) or (e.get("event_time") if isinstance(e, dict) else datetime.now(timezone.utc).isoformat())
        svc = getattr(e, "service", "") or (e.get("service", "") if isinstance(e, dict) else "")
        sev = getattr(e, "severity", "HIGH") or (e.get("severity", "HIGH") if isinstance(e, dict) else "HIGH")
        metric = getattr(e, "metric", "Anomaly") or (e.get("metric", "Anomaly") if isinstance(e, dict) else "Anomaly")
        src = getattr(e, "source", "application") or (e.get("source", "application") if isinstance(e, dict) else "application")
        val = getattr(e, "value", 1.0) or (e.get("value", 1.0) if isinstance(e, dict) else 1.0)
        ev_objs.append(EventData(
            event_id=ev_id,
            event_time=t_str,
            observed_time=t_str,
            source=src,
            service=svc,
            metric=metric,
            value=float(val),
            severity=str(sev).upper()
        ))

    dummy_case = EvaluationCase(
        case_id="hybrid_eval",
        family_id="hybrid",
        seed=42,
        topology=t_data,
        events=ev_objs,
        ground_truth=GroundTruth(true_root_causes=[])
    )
    
    scorer_res = scorer.predict(dummy_case)

    # 3. Setup rca_v3 verifier components
    g = CausalGraphV3.from_topology(topology)
    health_tracker = SourceHealthTracker(source_health or [])
    policy = telemetry_policy if isinstance(telemetry_policy, TelemetryPolicy) else TelemetryPolicy()
    evaluator = EvidenceEvaluator(policy=policy, health_tracker=health_tracker)
    verifier = EvidenceVerifierV3(evaluator=evaluator, graph_v3=g, health_tracker=health_tracker, config=cfg)

    # 4. Build CandidateDiagnosis objects
    candidates = []
    for rank, svc in enumerate(scorer_res.predicted_root_causes, start=1):
        score = scorer_res.candidate_scores.get(svc, 0.0)
        svc_evs = [e for e in clean_events if (getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)) == svc]
        earliest_t = min(
            [getattr(e, "event_time", None) or (e.get("event_time") if isinstance(e, dict) else "") for e in svc_evs],
            default="9999-12-31T23:59:59+00:00"
        )
        candidates.append(CandidateDiagnosis(
            service=svc,
            ranking_score=score,
            combined_score=score,
            earliest_event_time=earliest_t,
            earliest_anomalous_time=earliest_t,
            rank=rank,
            is_unobserved=(len(svc_evs) == 0)
        ))

    # 5. Execute verifier
    result = verifier.verify_and_decide(
        candidates=candidates,
        events=events,
        topology_nodes=nodes,
        predicted_roots=scorer_res.predicted_root_causes[:1],
        root_hypotheses=[],
        residual_symptoms=[]
    )

    return result
