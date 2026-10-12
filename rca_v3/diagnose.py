from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

from backend.app.models.event import CloudEvent, Severity
from rca_engine.causal_graph import CausalGraphEngine
from rca_engine.scoring import score_candidate
from rca_v2.telemetry_policy import TelemetryPolicy
from rca_v2.source_health import SourceHealthTracker
from rca_v2.evidence import EvidenceEvaluator
from rca_v3.models import (
    Verdict,
    DiagnosisResult,
    CandidateDiagnosis,
    StructuredExplanation,
    SignalEvidence,
    EvidenceState,
    Contradiction,
    RootHypothesis
)
from rca_v3.config import RCAv3Config
from rca_v3.causal_graph import CausalGraphV3
from rca_v3.graph_candidates import GraphCandidateGenerator
from rca_v3.symptom_scoring import SymptomScorer
from rca_v3.root_set_selector import RootSetSelector
from rca_v3.verifier import EvidenceVerifierV3


def diagnose(
    events: List[Any],
    topology: Any,
    source_health: Optional[List[Any]] = None,
    telemetry_policy: Optional[TelemetryPolicy] = None,
    config: Optional[RCAv3Config] = None
) -> DiagnosisResult:
    """
    Pure-function RCA v3 diagnosis engine:
    diagnose(events, topology, source_health, telemetry_policy, config) -> DiagnosisResult

    Guarantees:
    - Zero globals, zero database, zero network calls.
    - Graph-generated unobserved candidate discovery (2a).
    - Time-consistent directed symptom explanation scoring (2b).
    - Multi-root separation via candidate set marginal coverage (2c).
    - Directed failure-propagation relatedness (2d).
    - Principled verdict verification (2e).
    """
    cfg = config or RCAv3Config()
    if isinstance(telemetry_policy, dict):
        policy = TelemetryPolicy.model_validate(telemetry_policy)
    elif telemetry_policy is not None:
        policy = telemetry_policy
    else:
        policy = TelemetryPolicy()

    # 1. Initialize isolated components
    health_tracker = SourceHealthTracker(source_health if True else [])
    graph_v3 = CausalGraphV3.from_topology(topology)
    evaluator = EvidenceEvaluator(policy=policy, health_tracker=health_tracker)
    symptom_scorer = SymptomScorer(graph_v3=graph_v3, config=cfg)
    root_selector = RootSetSelector(graph_v3=graph_v3, symptom_scorer=symptom_scorer, config=cfg)
    verifier = EvidenceVerifierV3(
        evaluator=evaluator,
        graph_v3=graph_v3,
        health_tracker=health_tracker,
        config=cfg
    )

    # 2. Extract topology nodes
    topo_nodes = getattr(topology, "nodes", []) if hasattr(topology, "nodes") else topology.get("nodes", [])

    # 3. Convert input events to CloudEvent models
    cloud_events: List[CloudEvent] = []
    valid_sources = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}
    
    event_services: Set[str] = set()
    service_earliest_times: Dict[str, str] = {}

    for i, e in enumerate(events):
        ev_id = getattr(e, "event_id", None) or (e.get("event_id") if isinstance(e, dict) else f"evt_{i}")
        ts_str = getattr(e, "event_time", None) or (e.get("event_time") if isinstance(e, dict) else None)
        dt = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        src = getattr(e, "source", "application") or (e.get("source", "application") if isinstance(e, dict) else "application")
        src_clean = src.lower() if src.lower() in valid_sources else "application"
        svc = getattr(e, "service", "") or (e.get("service", "") if isinstance(e, dict) else "")
        metric = getattr(e, "metric", "Anomaly") or (e.get("metric", "Anomaly") if isinstance(e, dict) else "Anomaly")
        val = getattr(e, "value", 1.0) or (e.get("value", 1.0) if isinstance(e, dict) else 1.0)
        sev_str = getattr(e, "severity", "HIGH") or (e.get("severity", "HIGH") if isinstance(e, dict) else "HIGH")
        try:
            sev = Severity(sev_str.upper())
        except Exception:
            sev = Severity.HIGH

        if svc:
            event_services.add(svc)
            if svc not in service_earliest_times or dt < datetime.fromisoformat(service_earliest_times[svc]):
                service_earliest_times[svc] = dt.isoformat()

        cloud_events.append(CloudEvent(
            event_id=ev_id,
            timestamp=dt,
            source=src_clean,
            event_type=f"{metric.lower()}_anomaly",
            service=svc,
            resource_id=f"res_{svc.lower()}",
            metric=metric,
            value=float(val),
            severity=sev
        ))

    # 4. Step 2b Parsimony: Score observed candidates from events first
    observed_services = sorted(list(event_services))
    observed_candidates: List[CandidateDiagnosis] = []
    for svc in observed_services:
        res = score_candidate(svc, cloud_events, graph_engine=graph_v3.engine)
        score = float(res.get("total", 0.0))
        earliest_t = service_earliest_times.get(svc, datetime.max.replace(tzinfo=timezone.utc).isoformat())

        observed_candidates.append(CandidateDiagnosis(
            service=svc,
            ranking_score=score,
            combined_score=score,
            earliest_event_time=earliest_t,
            rank=0,
            is_unobserved=False
        ))

    # Evaluate symptoms explained by observed candidates
    observed_candidates = symptom_scorer.compute_symptom_coverage(candidates=observed_candidates, events=events)

    # Step 2b Parsimony:
    # Identify symptoms explained by the best observed candidate(s).
    # If the top observed candidate already explains all symptoms, unobserved ancestors are NOT needed.
    top_observed_explained: Set[str] = set()
    if observed_candidates:
        top_obs = observed_candidates[0]
        top_observed_explained = symptom_scorer.get_explained_symptoms_for_candidate(
            top_obs.service, top_obs.earliest_event_time, events
        ) & event_services

    unexplained_by_top_observed = event_services - top_observed_explained

    # Add unobserved ancestor ONLY if observed candidates leave symptoms unexplained
    candidates: List[CandidateDiagnosis] = list(observed_candidates)
    if cfg.enable_graph_candidates and unexplained_by_top_observed:
        cand_gen = GraphCandidateGenerator(
            graph_v3=graph_v3,
            policy=policy,
            health_tracker=health_tracker
        )
        unobserved_cands = cand_gen.find_unobserved_candidates(
            topology_nodes=topo_nodes,
            event_services=event_services,
            service_earliest_times=service_earliest_times,
            unexplained_symptoms=unexplained_by_top_observed,
            min_marginal_coverage=cfg.min_unobserved_marginal_coverage,
            total_symptoms=max(1, len(event_services))
        )
        if unobserved_cands:
            candidates.extend(unobserved_cands)
            candidates = symptom_scorer.compute_symptom_coverage(candidates=candidates, events=events)

    # 6. Step 2c: Multi-root separation via candidate set marginal coverage
    predicted_roots, root_hypotheses, residual_symptoms = root_selector.select_root_set(
        candidates=candidates,
        events=events
    )

    # 7. Step 2e: Principled evidence verification & verdict decisions
    result = verifier.verify_and_decide(
        candidates=candidates,
        events=events,
        topology_nodes=topo_nodes,
        predicted_roots=predicted_roots,
        root_hypotheses=root_hypotheses,
        residual_symptoms=residual_symptoms
    )

    return result
