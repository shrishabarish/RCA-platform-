from typing import Optional
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline


class AlwaysAbstainBaseline(RCABaselineAdapter):
    """Control baseline that always returns ABSTAIN."""

    @property
    def name(self) -> str:
        return "Control_AlwaysAbstain"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict="ABSTAIN",
            predicted_root_causes=[],
            candidate_scores={},
            confidence=0.0,
            metadata={"abstain_reason": "Always abstain control"}
        )


class NeverAbstainBaseline(RCABaselineAdapter):
    """Control baseline that delegates to a ranking engine but never abstains."""

    def __init__(self, inner: Optional[RCABaselineAdapter] = None):
        self.inner = inner or CurrentScorerCaseTopologyBaseline()

    @property
    def name(self) -> str:
        return "Control_NeverAbstain"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        res = self.inner.predict(case)
        return BaselineResult(
            baseline_name=self.name,
            case_id=res.case_id,
            verdict="DIAGNOSE",  # Enforce DIAGNOSE
            predicted_root_causes=res.predicted_root_causes,
            candidate_scores=res.candidate_scores,
            confidence=res.confidence,
            metadata=res.metadata
        )


class ScoreThresholdGateBaseline(RCABaselineAdapter):
    """
    Control baseline that uses candidate ranking score:
    Abstains if the top candidate score is strictly below threshold t.
    Threshold t is calibrated strictly on DEV (t = 73.0).
    """

    def __init__(self, inner: Optional[RCABaselineAdapter] = None, threshold: float = 73.0):
        self.inner = inner or CurrentScorerCaseTopologyBaseline()
        self.threshold = threshold

    @property
    def name(self) -> str:
        return f"Control_ScoreThresholdGate_t{int(self.threshold)}"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        res = self.inner.predict(case)
        verdict = "ABSTAIN" if res.confidence < self.threshold else "DIAGNOSE"
        return BaselineResult(
            baseline_name=self.name,
            case_id=res.case_id,
            verdict=verdict,
            predicted_root_causes=res.predicted_root_causes,
            candidate_scores=res.candidate_scores,
            confidence=res.confidence,
            metadata={
                **res.metadata,
                "score_threshold": self.threshold,
                "gate_passed": (res.confidence >= self.threshold)
            }
        )
