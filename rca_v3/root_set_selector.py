from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timezone

from rca_v3.models import CandidateDiagnosis, RootHypothesis
from rca_v3.causal_graph import CausalGraphV3
from rca_v3.symptom_scoring import SymptomScorer
from rca_v3.config import RCAv3Config


class RootSetSelector:
    """
    Step 2c:
    Multi-root separation via candidate SET evaluation:
    - Evaluates candidate sets, not purely greedy score thresholding.
    - Candidate is added only if it explains symptoms already chosen roots cannot (marginal coverage >= threshold).
    - Rejects candidate on the same causal chain as a chosen root that adds no unexplained symptoms.
    - Accepts independently failed downstream service if onset/evidence is not explained by upstream timing.
    - Outputs root hypotheses with linked symptoms and residual unexplained symptoms.
    """

    def __init__(
        self,
        graph_v3: CausalGraphV3,
        symptom_scorer: SymptomScorer,
        config: RCAv3Config
    ):
        self.graph_v3 = graph_v3
        self.symptom_scorer = symptom_scorer
        self.config = config

    def select_root_set(
        self,
        candidates: List[CandidateDiagnosis],
        events: List[Any]
    ) -> Tuple[List[str], List[RootHypothesis], List[str]]:
        if not candidates:
            return [], [], []

        # 1. Identify all observed symptom services
        symptom_services: Set[str] = set()
        service_onsets: Dict[str, datetime] = {}
        for e in events:
            svc = getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)
            if not svc:
                continue
            symptom_services.add(svc)
            ts_str = getattr(e, "event_time", None) or (e.get("event_time") if isinstance(e, dict) else None)
            dt = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if svc not in service_onsets or dt < service_onsets[svc]:
                service_onsets[svc] = dt

        total_symptoms_count = max(1, len(symptom_services))

        # 2. Check ablation flag: if set selection is disabled, use greedy 0.85 rule
        if not self.config.enable_set_root_selection:
            top_cand = candidates[0]
            score_floor = max(self.config.min_score_diagnose, top_cand.combined_score * 0.85)
            greedy_roots = [c.service for c in candidates if c.combined_score >= score_floor]
            if not greedy_roots:
                greedy_roots = [top_cand.service]
            hypotheses = [
                RootHypothesis(
                    root=r,
                    ranking_score=next((c.ranking_score for c in candidates if c.service == r), 0.0),
                    symptom_coverage=next((c.symptom_coverage for c in candidates if c.service == r), 0.0),
                    linked_symptoms=list(self.symptom_scorer.get_explained_symptoms_for_candidate(
                        r, next((c.earliest_event_time for c in candidates if c.service == r), ""), events
                    )),
                    is_unobserved=next((c.is_unobserved for c in candidates if c.service == r), False)
                )
                for r in greedy_roots
            ]
            # Residual symptoms
            explained_all = set()
            for h in hypotheses:
                explained_all.update(h.linked_symptoms)
            residual = sorted(list(symptom_services - explained_all))
            return greedy_roots, hypotheses, residual

        # 3. Principled Set Evaluation:
        top_cand = candidates[0]
        top_explained = self.symptom_scorer.get_explained_symptoms_for_candidate(
            top_cand.service, top_cand.earliest_event_time, events
        ) & symptom_services

        selected_roots: List[str] = [top_cand.service]
        top_cand.marginal_symptom_coverage = round(len(top_explained) / total_symptoms_count, 4)

        hypotheses: List[RootHypothesis] = [
            RootHypothesis(
                root=top_cand.service,
                ranking_score=top_cand.ranking_score,
                symptom_coverage=top_cand.symptom_coverage,
                linked_symptoms=sorted(list(top_explained)),
                is_unobserved=top_cand.is_unobserved
            )
        ]

        unexplained_symptoms: Set[str] = symptom_services - top_explained

        skew_tol = self.config.clock_skew_tolerance_sec
        max_delay = self.config.max_propagation_delay_sec
        threshold = self.config.min_marginal_symptom_coverage

        # Evaluate remaining candidates
        for cand in candidates[1:]:
            cand_svc = cand.service
            if cand_svc in selected_roots:
                continue

            cand_explained = self.symptom_scorer.get_explained_symptoms_for_candidate(
                cand_svc, cand.earliest_event_time, events
            ) & symptom_services

            # Newly explained symptoms that no already selected root can explain
            newly_explained = cand_explained & unexplained_symptoms
            marginal_cov = len(newly_explained) / total_symptoms_count
            cand.marginal_symptom_coverage = round(marginal_cov, 4)

            # Rule: must explain symptoms the already chosen roots cannot
            if marginal_cov < threshold and len(newly_explained) == 0:
                continue

            # Check causal chain with each already selected root
            reject = False
            for prev_root in selected_roots:
                # Is cand on same causal chain as prev_root?
                prev_causes_cand = self.graph_v3.is_ancestor_cause(prev_root, cand_svc)
                cand_causes_prev = self.graph_v3.is_ancestor_cause(cand_svc, prev_root)

                if cand_causes_prev and not prev_causes_cand:
                    # cand is upstream of prev_root; if it adds no unexplained symptoms, reject
                    if len(newly_explained) == 0:
                        reject = True
                        break

                elif prev_causes_cand and not cand_causes_prev:
                    # cand is downstream of prev_root
                    # Check whether cand's onset is explained by upstream timing
                    cand_dt = service_onsets.get(cand_svc)
                    prev_dt = service_onsets.get(prev_root)

                    if cand_dt and prev_dt:
                        delta = (cand_dt - prev_dt).total_seconds()
                        hops = self.graph_v3.get_propagation_distance(prev_root, cand_svc) or 1
                        timing_explained = (delta >= -skew_tol and delta <= (max_delay * max(1, hops)))
                    else:
                        timing_explained = True

                    # If timing IS explained by upstream, and it adds no unexplained symptoms: reject!
                    if timing_explained and len(newly_explained) == 0:
                        reject = True
                        break

            if not reject and len(newly_explained) > 0:
                selected_roots.append(cand_svc)
                hypotheses.append(RootHypothesis(
                    root=cand_svc,
                    ranking_score=cand.ranking_score,
                    symptom_coverage=cand.symptom_coverage,
                    linked_symptoms=sorted(list(newly_explained)),
                    is_unobserved=cand.is_unobserved
                ))
                unexplained_symptoms = unexplained_symptoms - newly_explained

        residual_symptoms = sorted(list(unexplained_symptoms))
        return selected_roots, hypotheses, residual_symptoms
