from typing import Optional
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from rca_v3 import diagnose, RCAv3Config, Verdict


class RCAv3Baseline(RCABaselineAdapter):
    """
    Adapter wrapping rca_v3 pure function for evaluation in the harness.
    Maps INSUFFICIENT_EVIDENCE -> "ABSTAIN", CONFIRMED/PROBABLE -> "DIAGNOSE".
    """

    def __init__(self, config: Optional[RCAv3Config] = None, name_suffix: str = ""):
        self.config = config or RCAv3Config()
        self.suffix = name_suffix

    @property
    def name(self) -> str:
        base = "rca_v3"
        return f"{base}_{self.suffix}" if self.suffix else base

    def predict(self, case: EvaluationCase) -> BaselineResult:
        diag = diagnose(
            events=case.events,
            topology=case.topology,
            source_health=case.source_health,
            config=self.config
        )

        harness_verdict = "ABSTAIN" if diag.verdict == Verdict.INSUFFICIENT_EVIDENCE else "DIAGNOSE"
        top_conf = diag.top_candidate.combined_score if diag.top_candidate else 0.0

        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict=harness_verdict,
            predicted_root_causes=diag.predicted_root_causes,
            candidate_scores=diag.ranking_scores,
            confidence=top_conf,
            metadata={
                "rca_v3_verdict": diag.verdict.value,
                "coverage": diag.coverage,
                "symptom_coverage": diag.top_candidate.symptom_coverage if diag.top_candidate else 0.0,
                "reachable_symptom_coverage": diag.reachable_symptom_coverage,
                "margin": diag.margin,
                "contradictions_count": len(diag.contradictions),
                "residual_symptoms": diag.residual_symptoms,
                "root_hypotheses": [h.model_dump() for h in diag.root_hypotheses],
                "verdict_reason": diag.explanation.verdict_reason,
            }
        )
