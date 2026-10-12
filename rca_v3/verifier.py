from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime, timezone

from rca_v2.models import (
    SignalEvidence,
    SourceHealthStatus,
    EvidenceState,
)
from rca_v2.evidence import EvidenceEvaluator
from rca_v2.source_health import SourceHealthTracker
from rca_v3.models import (
    Verdict,
    CandidateDiagnosis,
    Contradiction,
    DiagnosisResult,
    RootHypothesis,
    StructuredExplanation
)
from rca_v3.causal_graph import CausalGraphV3
from rca_v3.config import RCAv3Config


class EvidenceVerifierV3:
    """
    Step 2e:
    Principled verdict decision rules:
    - Evidence coverage must be able to prevent CONFIRMED.
    - Source DOWN for any source that carries signals for candidate or ancestors prevents CONFIRMED.
    - Unobserved candidate or unobserved ancestors prevents CONFIRMED (capped at PROBABLE).
    - Competing unresolved disjoint roots trigger contradiction / abstention.
    - Healthy evidence note: The case schema lacks positive healthy telemetry heartbeats;
      services without events only have source health status and downstream symptoms.
    """

    def __init__(
        self,
        evaluator: EvidenceEvaluator,
        graph_v3: CausalGraphV3,
        health_tracker: SourceHealthTracker,
        config: RCAv3Config
    ):
        self.evaluator = evaluator
        self.graph_v3 = graph_v3
        self.health_tracker = health_tracker
        self.config = config

    def verify_and_decide(
        self,
        candidates: List[CandidateDiagnosis],
        events: List[Any],
        topology_nodes: List[Any],
        predicted_roots: List[str],
        root_hypotheses: List[RootHypothesis],
        residual_symptoms: List[str]
    ) -> DiagnosisResult:
        source_health_summary = self.health_tracker.get_summary()

        if not candidates:
            return DiagnosisResult(
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                predicted_root_causes=[],
                candidate_diagnoses=[],
                explanation=StructuredExplanation(
                    verdict_reason="No candidate root causes identified in incident telemetry.",
                    evidence_table=[],
                    contradictions=[],
                    summary_text="Verdict: INSUFFICIENT_EVIDENCE. No candidates."
                ),
                metadata={"reason": "empty_candidates"}
            )

        top_cand = candidates[0]
        top_svc = top_cand.service

        # 1. Compute evidence coverage for top candidate
        top_evidence = self.evaluator.evaluate_service_evidence(top_svc, events, topology_nodes)
        obs_k, exp_k, top_coverage = self.evaluator.compute_observable_coverage(top_evidence)
        top_cand.expected_signals = exp_k
        top_cand.observed_signals = obs_k
        top_cand.coverage = top_coverage

        # 2. Compute margin
        if len(candidates) >= 2:
            margin = round(top_cand.combined_score - candidates[1].combined_score, 2)
        else:
            margin = round(top_cand.combined_score, 2)

        # 3. Detect Contradictions
        contradictions: List[Contradiction] = []

        # Check A: Primary root's telemetry source is DOWN (collector outage)
        for ev in top_evidence:
            if ev.state == EvidenceState.UNOBSERVED:
                source_st = self.health_tracker.get_source_status(ev.source)
                if source_st == SourceHealthStatus.DOWN:
                    contradictions.append(Contradiction(
                        contradiction_type="TELEMETRY_SOURCE_UNAVAILABLE",
                        description=f"Primary telemetry source '{ev.source}' for candidate '{top_svc}' is DOWN.",
                        involved_services=[top_svc],
                        severity="CRITICAL"
                    ))
                    break

        # Check B: Quiet timeline (no anomalous breaches in events)
        severities = set(
            str(getattr(e, "severity", None) or (e.get("severity") if isinstance(e, dict) else "")).upper()
            for e in events
        )
        if not ("HIGH" in severities or "CRITICAL" in severities or "MEDIUM" in severities):
            contradictions.append(Contradiction(
                contradiction_type="QUIET_TIMELINE_NO_ANOMALY",
                description="Timeline contains zero anomalous breaches; only healthy baseline signals observed.",
                involved_services=[top_svc],
                severity="CRITICAL"
            ))

        # Check C (Step 2a): Contradiction blocks CONFIRMED only when evidence conflicts about the SAME candidate.
        # Two independent roots reported side by side are NOT a contradiction; they are handled by RootSetSelector as multi-roots.
        if not self.config.contradiction_same_candidate_only:
            # Legacy ablation behavior (disabled by default)
            if len(candidates) >= 2:
                c2 = candidates[1]
                c2_svc = c2.service
                if not self.graph_v3.are_services_related(top_svc, c2_svc, enable_directed=self.config.enable_directed_relatedness):
                    try:
                        t1 = datetime.fromisoformat(top_cand.earliest_event_time)
                        t2 = datetime.fromisoformat(c2.earliest_event_time)
                        delta_onset = abs((t1 - t2).total_seconds())
                    except Exception:
                        delta_onset = 0.0
                    top_explains_c2 = self.graph_v3.is_ancestor_cause(top_svc, c2_svc)
                    c2_explains_top = self.graph_v3.is_ancestor_cause(c2_svc, top_svc)
                    if (not top_explains_c2) and (not c2_explains_top) and delta_onset <= self.config.clock_skew_tolerance_sec:
                        if c2.ranking_score >= 65.0 or c2.combined_score >= 60.0:
                            contradictions.append(Contradiction(
                                contradiction_type="COMPETING_DISJOINT_ROOTS",
                                description=(f"Competing independent anomalies on disjoint services '{top_svc}' and '{c2_svc}' "
                                             f"at identical/near onset (delta={delta_onset:.1f}s) cannot be causally reconciled."),
                                involved_services=[top_svc, c2_svc],
                                severity="CRITICAL"
                            ))
        else:
            # Principled Step 2a Check: Real contradiction format can represent
            # (root looks healthy while dependents blame it)
            # If top candidate has zero anomalies (only healthy baseline/quiet) yet dependents report critical errors blaming it
            top_has_anomalies = any(
                (getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)) == top_svc
                and str(getattr(e, "severity", "") or (e.get("severity") if isinstance(e, dict) else "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
                for e in events
            )
            dependents = self.graph_v3.get_all_downstream_dependents(top_svc)
            dependents_blaming = any(
                (getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)) in dependents
                and str(getattr(e, "severity", "") or (e.get("severity") if isinstance(e, dict) else "")).upper() in ("CRITICAL", "HIGH")
                for e in events
            )
            # If top candidate is observed and looks healthy/quiet while dependents blame it:
            if (not top_cand.is_unobserved) and (not top_has_anomalies) and dependents_blaming and any(ev.state == EvidenceState.QUIET for ev in top_evidence):
                contradictions.append(Contradiction(
                    contradiction_type="ROOT_HEALTHY_WHILE_DEPENDENTS_BLAME",
                    description=f"Contradiction: Candidate '{top_svc}' telemetry indicates healthy/quiet state while downstream dependents report critical failures.",
                    involved_services=[top_svc],
                    severity="CRITICAL"
                ))

        # Check D: Ancestor source DOWN
        # "source DOWN for any source that carries signals for the candidate or its ancestors prevents CONFIRMED"
        ancestor_source_down = False
        ancestors = self.graph_v3.get_all_upstream_dependencies(top_svc)
        for anc in ancestors:
            anc_ev = self.evaluator.evaluate_service_evidence(anc, events, topology_nodes)
            for ev in anc_ev:
                if self.health_tracker.get_source_status(ev.source) == SourceHealthStatus.DOWN:
                    ancestor_source_down = True
                    contradictions.append(Contradiction(
                        contradiction_type="ANCESTOR_SOURCE_DOWN",
                        description=f"Telemetry source '{ev.source}' for upstream ancestor '{anc}' is DOWN.",
                        involved_services=[top_svc, anc],
                        severity="MEDIUM"
                    ))
                    break
            if ancestor_source_down:
                break

        # 4. Apply Verdict Decision Rules
        effective_contradictions = contradictions if self.config.enable_principled_verdicts else []
        has_critical_contra = any(c.severity == "CRITICAL" for c in effective_contradictions)

        if self.config.block_on_contradiction and has_critical_contra:
            verdict = Verdict.INSUFFICIENT_EVIDENCE
            reason = f"Abstention triggered: {effective_contradictions[0].description}"
        elif top_cand.combined_score < self.config.min_score_diagnose:
            verdict = Verdict.INSUFFICIENT_EVIDENCE
            reason = f"Top candidate combined_score ({top_cand.combined_score:.1f}) is below diagnosis floor ({self.config.min_score_diagnose:.1f})."
        else:
            # Check Principled CONFIRMED Conditions:
            # 1. Score floor
            score_pass = (top_cand.combined_score >= self.config.min_score_confirmed)

            # 2. Coverage must be able to prevent CONFIRMED (no bypass if expected_signals == 0)
            cov_pass = (top_cand.coverage >= self.config.min_coverage_confirmed and top_cand.expected_signals > 0)

            # 3. Margin pass
            margin_pass = (margin >= self.config.min_margin_confirmed or len(candidates) == 1)

            # 4. Observability: top candidate must NOT be unobserved, nor have unobserved ancestors
            observed_pass = (not top_cand.is_unobserved)

            # 5. Source DOWN on candidate or ancestors prevents CONFIRMED
            candidate_source_down = any(
                self.health_tracker.get_source_status(ev.source) == SourceHealthStatus.DOWN
                for ev in top_evidence
            )
            source_down_pass = (not ancestor_source_down) and (not candidate_source_down)

            # 6. Contradictions pass
            contra_pass = (len(effective_contradictions) == 0)

            if score_pass and cov_pass and margin_pass and observed_pass and source_down_pass and contra_pass:
                verdict = Verdict.CONFIRMED
                reason = (f"Confirmed: Candidate '{top_svc}' scored {top_cand.combined_score:.1f} "
                          f"with margin {margin:.1f} and coverage {top_cand.coverage*100:.1f}%.")
            else:
                verdict = Verdict.PROBABLE
                fail_reasons = []
                if not score_pass:
                    fail_reasons.append(f"score {top_cand.combined_score:.1f} < {self.config.min_score_confirmed}")
                if not cov_pass:
                    fail_reasons.append(f"coverage {top_cand.coverage*100:.1f}% < {self.config.min_coverage_confirmed*100:.0f}%")
                if not margin_pass:
                    fail_reasons.append(f"margin {margin:.1f} < {self.config.min_margin_confirmed}")
                if not observed_pass:
                    fail_reasons.append("candidate is unobserved")
                if not source_down_pass:
                    fail_reasons.append("telemetry source for candidate or ancestor is DOWN")
                if not contra_pass:
                    fail_reasons.append(f"contradiction: {effective_contradictions[0].contradiction_type}")

                reason = f"Probable: Candidate '{top_svc}' ({'; '.join(fail_reasons)})."

        # 5. Build Structured Explanation
        evidence_table = []
        for ev in top_evidence:
            evidence_table.append({
                "service": ev.service,
                "signal": ev.signal_name,
                "source": ev.source,
                "state": ev.state.value,
                "event_ref": ev.event_ref,
                "details": ev.details
            })

        contra_table = [
            {
                "type": c.contradiction_type,
                "description": c.description,
                "involved_services": c.involved_services,
                "severity": c.severity
            }
            for c in effective_contradictions
        ]

        summary_text = (
            f"Verdict: {verdict.value}. "
            f"Primary candidate: {top_svc} (score {top_cand.combined_score:.1f}, margin {margin:.1f}). "
            f"Root set: {predicted_roots}. "
            f"Residual symptoms: {residual_symptoms}."
        )

        return DiagnosisResult(
            verdict=verdict,
            predicted_root_causes=predicted_roots,
            candidate_diagnoses=candidates,
            top_candidate=top_cand,
            root_hypotheses=root_hypotheses,
            residual_symptoms=residual_symptoms,
            ranking_scores={c.service: c.combined_score for c in candidates},
            margin=margin,
            coverage=top_cand.coverage,
            reachable_symptom_coverage=top_cand.reachable_coverage,
            contradictions=effective_contradictions,
            source_health_summary=source_health_summary,
            explanation=StructuredExplanation(
                verdict_reason=reason,
                evidence_table=evidence_table,
                contradictions=contra_table,
                summary_text=summary_text
            ),
            metadata={
                "unobserved_root": top_cand.is_unobserved,
                "residual_symptom_count": len(residual_symptoms)
            }
        )
