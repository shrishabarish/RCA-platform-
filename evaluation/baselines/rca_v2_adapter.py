from typing import Optional
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from rca_v2 import diagnose, RCAv2Config, Verdict


class RCAv2Baseline(RCABaselineAdapter):
    """
    Adapter wrapping rca_v2 pure function for evaluation in the harness.
    Maps INSUFFICIENT_EVIDENCE -> "ABSTAIN", CONFIRMED/PROBABLE -> "DIAGNOSE".
    """

    def __init__(self, config: Optional[RCAv2Config] = None, name_suffix: str = ""):
        self.config = config or RCAv2Config()
        self.suffix = name_suffix

    @property
    def name(self) -> str:
        base = "rca_v2"
        return f"{base}_{self.suffix}" if self.suffix else base

    def predict(self, case: EvaluationCase) -> BaselineResult:
        diag = diagnose(
            events=case.events,
            topology=case.topology,
            source_health=case.source_health,
            config=self.config
        )

        harness_verdict = "ABSTAIN" if diag.verdict == Verdict.INSUFFICIENT_EVIDENCE else "DIAGNOSE"
        top_conf = diag.top_candidate.ranking_score if diag.top_candidate else 0.0

        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict=harness_verdict,
            predicted_root_causes=diag.predicted_root_causes,
            candidate_scores=diag.ranking_scores,
            confidence=top_conf,
            metadata={
                "rca_v2_verdict": diag.verdict.value,
                "coverage": diag.coverage,
                "reachable_symptom_coverage": diag.reachable_symptom_coverage,
                "margin": diag.margin,
                "contradictions_count": len(diag.contradictions),
                "verdict_reason": diag.explanation.verdict_reason,
                "structured_explanation": diag.explanation.model_dump()
            }
        )
