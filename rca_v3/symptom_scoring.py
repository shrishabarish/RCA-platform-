from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timezone

from rca_v3.models import CandidateDiagnosis, get_event_field
from rca_v3.causal_graph import CausalGraphV3
from rca_v3.config import RCAv3Config


SEVERITY_WEIGHTS = {
    "CRITICAL": 1.0,
    "HIGH": 0.8,
    "MEDIUM": 0.5,
    "LOW": 0.2
}


class SymptomScorer:
    """
    Step 2b:
    Computes time-consistent directed coverage of observed symptoms for each candidate.
    Features:
    - Directed failure-propagation paths from candidate to symptom service.
    - Time-consistency: symptom onset >= candidate onset - clock_skew_tolerance.
    - Maximum delay bound: symptom onset - candidate onset <= max_propagation_delay.
    - Weighted coverage of observed symptoms.
    - Combined score keeping 5-factor ranking_score as input.
    - Deterministic tie-breaking.
    """

    def __init__(self, graph_v3: CausalGraphV3, config: RCAv3Config):
        self.graph_v3 = graph_v3
        self.config = config

    def compute_symptom_coverage(
        self,
        candidates: List[CandidateDiagnosis],
        events: List[Any]
    ) -> List[CandidateDiagnosis]:
        if not candidates or not events:
            return candidates

        # 1. Identify all observed symptom services and their earliest onset times and max severity
        symptom_services: Dict[str, Dict[str, Any]] = {}
        for e in events:
            svc = get_event_field(e, "service")
            if not svc:
                continue
            ts_str = get_event_field(e, "event_time")
            dt = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            sev = str(get_event_field(e, "severity", "HIGH")).upper()
            w = self.config.severity_weights.get(sev, 0.5)

            if svc not in symptom_services:
                symptom_services[svc] = {
                    "earliest_dt": dt,
                    "max_weight": w,
                    "count": 1
                }
            else:
                if dt < symptom_services[svc]["earliest_dt"]:
                    symptom_services[svc]["earliest_dt"] = dt
                if w > symptom_services[svc]["max_weight"]:
                    symptom_services[svc]["max_weight"] = w
                symptom_services[svc]["count"] += 1

        total_symptom_weight = sum(s["max_weight"] for s in symptom_services.values())
        if total_symptom_weight == 0.0:
            total_symptom_weight = 1.0

        skew_tol = self.config.clock_skew_tolerance_sec
        max_delay = self.config.max_propagation_delay_sec

        # 2. For each candidate, evaluate explainable symptoms
        for cand in candidates:
            cand_svc = cand.service
            try:
                c_dt = datetime.fromisoformat(cand.earliest_event_time)
                if c_dt.tzinfo is None:
                    c_dt = c_dt.replace(tzinfo=timezone.utc)
            except Exception:
                c_dt = datetime.now(timezone.utc)

            explained_weight = 0.0
            explained_symptoms: List[str] = []

            for sym_svc, s_info in symptom_services.items():
                s_dt = s_info["earliest_dt"]
                s_weight = s_info["max_weight"]

                if sym_svc == cand_svc:
                    # Candidate explains its own symptom
                    explained_weight += s_weight
                    explained_symptoms.append(sym_svc)
                else:
                    # Check directed propagation path from candidate to symptom
                    if self.graph_v3.is_ancestor_cause(cand_svc, sym_svc):
                        delta_sec = (s_dt - c_dt).total_seconds()
                        hops = self.graph_v3.get_propagation_distance(cand_svc, sym_svc) or 1
                        allowed_delay = max_delay * max(1, hops)

                        # Time-consistency check
                        if delta_sec >= -skew_tol and delta_sec <= allowed_delay:
                            explained_weight += s_weight
                            explained_symptoms.append(sym_svc)

            cov = round(explained_weight / total_symptom_weight, 4)
            cand.symptom_coverage = cov

            # Compute combined score for ranking:
            # If enabled: balance 5-factor score (ranking_score) with symptom_coverage
            if self.config.enable_symptom_scoring:
                if cand.is_unobserved:
                    # Unobserved candidate: ranking_score is 0, score comes from symptom coverage with discount
                    cand.combined_score = round(100.0 * cov * self.config.symptom_discount_factor, 2)
                else:
                    # Weighted combination from config parameters
                    cand.combined_score = round(
                        self.config.weight_ranking_score * cand.ranking_score +
                        self.config.weight_symptom_coverage * (100.0 * cov),
                        2
                    )
            else:
                # Ablation: pure 5-factor ranking_score
                cand.combined_score = cand.ranking_score

        # 3. Deterministic tie-breaking (Step 2b Parsimony):
        # Never rank an unobserved candidate above an observed candidate that explains the same symptoms.
        # Observed candidate strictly precedes unobserved candidate when combined_score ties.
        candidates.sort(key=lambda c: (-c.combined_score, c.is_unobserved, c.earliest_event_time, c.service))

        # Re-assign ranks
        for idx, cand in enumerate(candidates, start=1):
            cand.rank = idx

        return candidates

    def get_explained_symptoms_for_candidate(
        self,
        candidate_service: str,
        candidate_onset: str,
        events: List[Any]
    ) -> Set[str]:
        """Returns the set of symptom service names explained by this candidate."""
        explained: Set[str] = set()
        cand_svc = candidate_service
        try:
            c_dt = datetime.fromisoformat(candidate_onset)
            if c_dt.tzinfo is None:
                c_dt = c_dt.replace(tzinfo=timezone.utc)
        except Exception:
            c_dt = datetime.now(timezone.utc)

        symptom_times: Dict[str, datetime] = {}
        for e in events:
            svc = getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)
            if not svc:
                continue
            ts_str = getattr(e, "event_time", None) or (e.get("event_time") if isinstance(e, dict) else None)
            dt = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if svc not in symptom_times or dt < symptom_times[svc]:
                symptom_times[svc] = dt

        skew_tol = self.config.clock_skew_tolerance_sec
        max_delay = self.config.max_propagation_delay_sec

        for sym_svc, s_dt in symptom_times.items():
            if sym_svc == cand_svc:
                explained.add(sym_svc)
            elif self.graph_v3.is_ancestor_cause(cand_svc, sym_svc):
                delta_sec = (s_dt - c_dt).total_seconds()
                hops = self.graph_v3.get_propagation_distance(cand_svc, sym_svc) or 1
                if delta_sec >= -skew_tol and delta_sec <= (max_delay * max(1, hops)):
                    explained.add(sym_svc)

        return explained
