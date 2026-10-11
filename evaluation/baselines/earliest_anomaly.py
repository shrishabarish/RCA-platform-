from datetime import datetime
from typing import Dict, List
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult


class EarliestAnomalyBaseline(RCABaselineAdapter):
    """
    Earliest Anomaly Baseline:
    Ranks candidates strictly by their earliest observed anomaly timestamp.
    Assumes that the earliest telemetry event in the timeline is the primary root cause.
    """

    @property
    def name(self) -> str:
        return "Earliest_Anomaly"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        if not case.events:
            return BaselineResult(
                baseline_name=self.name,
                case_id=case.case_id,
                verdict="DIAGNOSE",
                predicted_root_causes=[],
                candidate_scores={},
                confidence=0.0
            )

        earliest_per_service: Dict[str, datetime] = {}
        for e in case.events:
            dt = datetime.fromisoformat(e.event_time)
            if e.service not in earliest_per_service or dt < earliest_per_service[e.service]:
                earliest_per_service[e.service] = dt

        # Sort services ascending by earliest timestamp
        ranked = sorted(earliest_per_service.keys(), key=lambda s: earliest_per_service[s])
        
        t0 = earliest_per_service[ranked[0]]
        scores: Dict[str, float] = {}
        for s in ranked:
            delta_sec = max(0.0, (earliest_per_service[s] - t0).total_seconds())
            scores[s] = max(10.0, round(100.0 / (1.0 + 0.05 * delta_sec), 1))

        # Calibrated confidence: based on separation between top candidate and 2nd candidate
        if len(ranked) > 1:
            t_first = earliest_per_service[ranked[0]]
            t_second = earliest_per_service[ranked[1]]
            lead_gap = max(0.0, (t_second - t_first).total_seconds())
            # Gap of 0s -> 50% confidence; gap of >=10s -> 100% confidence
            top_confidence = min(100.0, round(50.0 + 5.0 * lead_gap, 1))
        elif len(ranked) == 1:
            top_confidence = 75.0
        else:
            top_confidence = 0.0

        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict="DIAGNOSE",
            predicted_root_causes=ranked,
            candidate_scores=scores,
            confidence=top_confidence,
            metadata={"earliest_timestamps": {s: earliest_per_service[s].isoformat() for s in ranked}}
        )
