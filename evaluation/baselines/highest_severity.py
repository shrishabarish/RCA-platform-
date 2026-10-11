from typing import Dict, List, Any
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult

SEV_RANK = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1
}

SEV_SCORE_MAP = {
    "CRITICAL": 100.0,
    "HIGH": 80.0,
    "MEDIUM": 50.0,
    "LOW": 20.0
}


class HighestSeverityBaseline(RCABaselineAdapter):
    """
    Highest Severity Baseline:
    Ranks candidates purely by the highest anomaly severity level reported in telemetry.
    Ties are broken by max numerical metric value observed.
    """

    @property
    def name(self) -> str:
        return "Highest_Severity"

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

        service_stats: Dict[str, Dict[str, Any]] = {}
        for e in case.events:
            sev_num = SEV_RANK.get(e.severity, 1)
            val = float(e.value or 0.0)
            if e.service not in service_stats:
                service_stats[e.service] = {
                    "max_sev_num": sev_num,
                    "max_sev_str": e.severity,
                    "max_val": val
                }
            else:
                curr = service_stats[e.service]
                if sev_num > curr["max_sev_num"]:
                    curr["max_sev_num"] = sev_num
                    curr["max_sev_str"] = e.severity
                    curr["max_val"] = max(curr["max_val"], val)
                elif sev_num == curr["max_sev_num"]:
                    curr["max_val"] = max(curr["max_val"], val)

        # Sort services descending by (max_sev_num, max_val)
        ranked = sorted(
            service_stats.keys(),
            key=lambda s: (service_stats[s]["max_sev_num"], service_stats[s]["max_val"]),
            reverse=True
        )

        scores: Dict[str, float] = {}
        for s in ranked:
            st = service_stats[s]
            scores[s] = SEV_SCORE_MAP.get(st["max_sev_str"], 20.0)

        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict="DIAGNOSE",
            predicted_root_causes=ranked,
            candidate_scores=scores,
            confidence=scores[ranked[0]] if ranked else 0.0,
            metadata={"max_severities": {s: service_stats[s]["max_sev_str"] for s in ranked}}
        )
