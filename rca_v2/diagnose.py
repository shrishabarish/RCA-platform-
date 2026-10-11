from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from backend.app.models.event import CloudEvent, Severity
from rca_engine.causal_graph import CausalGraphEngine
from rca_v2.models import (
    Verdict,
    DiagnosisResult,
    CandidateDiagnosis,
    StructuredExplanation,
    SignalEvidence,
    EvidenceState,
    Contradiction,
)
from rca_v2.config import RCAv2Config
from rca_v2.telemetry_policy import TelemetryPolicy
from rca_v2.source_health import SourceHealthTracker
from rca_v2.evidence import EvidenceEvaluator
from rca_v2.ranker import CandidateRanker
from rca_v2.verifier import EvidenceVerifier


def diagnose(
    events: List[Any],
    topology: Any,
    source_health: Optional[List[Any]] = None,
    telemetry_policy: Optional[TelemetryPolicy] = None,
    config: Optional[RCAv2Config] = None
) -> DiagnosisResult:
    """
    Pure-function RCA diagnosis engine:
    diagnose(events, topology, source_health, telemetry_policy, config) -> DiagnosisResult

    Guarantees:
    - Zero globals, zero database, zero network calls.
    - Deterministic tie-breaking on candidate ranking.
    - Explicit evidence verification and contradiction detection.
    """
    cfg = config or RCAv2Config()
    policy = telemetry_policy or TelemetryPolicy()

    # 1. Initialize isolated in-memory components
    health_tracker = SourceHealthTracker(source_health if cfg.enable_source_health else [])
    cg = CausalGraphEngine.from_topology(topology)
    evaluator = EvidenceEvaluator(policy=policy, health_tracker=health_tracker)
    ranker = CandidateRanker(graph_engine=cg)
    verifier = EvidenceVerifier(evaluator=evaluator, graph_engine=cg, health_tracker=health_tracker)

    # 2. Extract topology nodes
    topo_nodes = getattr(topology, "nodes", []) if hasattr(topology, "nodes") else topology.get("nodes", [])

    # 3. Convert input events to CloudEvent models
    cloud_events: List[CloudEvent] = []
    valid_sources = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}
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

    # 4. Rank candidates
    candidates = ranker.rank_candidates(cloud_events)

    # 5. Verify evidence features
    coverage, reachable_cov, margin, contradictions, sh_summary = verifier.compute_features(
        candidates=candidates,
        events=events,
        topology_nodes=topo_nodes
    )

    # Filter contradictions if disabled by ablation
    effective_contradictions = contradictions if cfg.enable_contradictions else []

    # 6. Apply verdict decision rules
    if not candidates:
        verdict = Verdict.INSUFFICIENT_EVIDENCE
        reason = "No candidate root causes identified in incident telemetry."
    else:
        top_cand = candidates[0]
        
        # Check for critical blocking contradictions (e.g. source outage on root, quiet timeline)
        has_critical_contra = any(c.severity == "CRITICAL" for c in effective_contradictions)
        has_quiet = any(c.contradiction_type == "QUIET_TIMELINE_NO_ANOMALY" for c in effective_contradictions)

        if cfg.block_on_contradiction and (has_critical_contra or has_quiet):
            verdict = Verdict.INSUFFICIENT_EVIDENCE
            reason = f"Abstention triggered: {effective_contradictions[0].description}"
        elif top_cand.ranking_score < cfg.min_score_diagnose:
            verdict = Verdict.INSUFFICIENT_EVIDENCE
            reason = f"Top candidate ranking_score ({top_cand.ranking_score:.1f}) is below diagnosis floor ({cfg.min_score_diagnose:.1f})."
        else:
            # Check Confirmed conditions
            score_pass = (top_cand.ranking_score >= cfg.min_score_confirmed)
            cov_pass = (not cfg.enable_coverage) or (coverage >= cfg.min_coverage_confirmed) or (top_cand.expected_signals == 0)
            margin_pass = (not cfg.enable_margin) or (margin >= cfg.min_margin_confirmed) or (len(candidates) == 1)
            contra_pass = (len(effective_contradictions) == 0)

            if score_pass and cov_pass and margin_pass and contra_pass:
                verdict = Verdict.CONFIRMED
                reason = (f"Confirmed: Candidate '{top_cand.service}' scored {top_cand.ranking_score:.1f} "
                          f"with margin {margin:.1f} and coverage {coverage*100:.1f}%.")
            else:
                verdict = Verdict.PROBABLE
                reason = (f"Probable: Candidate '{top_cand.service}' scored {top_cand.ranking_score:.1f} "
                          f"(margin {margin:.1f}, coverage {coverage*100:.1f}%).")

    # 7. Build structured explanation
    predicted_roots = [c.service for c in candidates if c.ranking_score >= cfg.min_score_diagnose]
    if not predicted_roots and candidates:
        predicted_roots = [candidates[0].service]

    evidence_table = []
    if candidates:
        top_ev = evaluator.evaluate_service_evidence(candidates[0].service, events, topo_nodes)
        for ev in top_ev:
            evidence_table.append({
                "service": ev.service,
                "signal": ev.signal_name,
                "source": ev.source,
                "state": ev.state.value,
                "event_ref": ev.event_ref,
                "details": ev.details
            })

    summary_text = (
        f"Verdict: {verdict.value}. "
        f"Primary candidate: {candidates[0].service if candidates else 'None'} "
        f"(score: {candidates[0].ranking_score if candidates else 0.0:.1f}, margin: {margin:.1f}). "
        f"{reason}"
    )

    explanation = StructuredExplanation(
        verdict_reason=reason,
        evidence_table=evidence_table,
        contradictions=[c.model_dump() for c in effective_contradictions],
        summary_text=summary_text
    )

    return DiagnosisResult(
        verdict=verdict,
        predicted_root_causes=predicted_roots,
        candidate_diagnoses=candidates,
        top_candidate=candidates[0] if candidates else None,
        ranking_scores={c.service: c.ranking_score for c in candidates},
        margin=margin,
        coverage=coverage,
        reachable_symptom_coverage=reachable_cov,
        contradictions=effective_contradictions,
        source_health_summary=sh_summary,
        explanation=explanation,
        metadata={
            "config_hash": cfg.sha256_hash(),
            "event_count": len(cloud_events),
            "candidate_count": len(candidates)
        }
    )
