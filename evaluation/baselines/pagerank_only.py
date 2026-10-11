import networkx as nx
from typing import Dict, List
from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult


class PageRankBaseline(RCABaselineAdapter):
    """
    PageRank Centrality Baseline:
    Ranks candidates purely by topological centrality on the causal propagation graph.
    Services with higher downstream structural blast radius receive higher PageRank.
    """

    @property
    def name(self) -> str:
        return "PageRank_Only"

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

        # Build graph: edges are (caller, dependency)
        g = nx.DiGraph()
        for n in case.topology.nodes:
            g.add_node(n.id)
        for src, dst in case.topology.edges:
            g.add_edge(src, dst)

        # Reversed graph points from dependency -> callers (diffusion direction)
        rev_g = g.reverse()
        try:
            pr = nx.pagerank(rev_g, alpha=0.85)
        except Exception:
            pr = {n.id: 1.0 / len(case.topology.nodes) for n in case.topology.nodes}

        # Restrict to services present in the event timeline
        services_in_events = set(e.service for e in case.events if e.service)
        candidates = [s for s in services_in_events if s in pr]
        if not candidates:
            candidates = list(services_in_events)
            for c in candidates:
                pr[c] = 0.1

        # Sort candidates descending by PageRank score
        ranked = sorted(candidates, key=lambda s: pr.get(s, 0.0), reverse=True)

        max_pr = max((pr.get(s, 0.0) for s in ranked), default=1.0)
        scores: Dict[str, float] = {}
        for s in ranked:
            norm_score = round((pr.get(s, 0.0) / max(max_pr, 1e-6)) * 100.0, 1)
            scores[s] = norm_score

        # Calibrated confidence: based on relative separation between top and second PageRank
        if len(ranked) > 1:
            top_pr = pr.get(ranked[0], 0.0)
            second_pr = pr.get(ranked[1], 0.0)
            rel_gap = (top_pr - second_pr) / max(top_pr, 1e-6)
            top_confidence = min(100.0, round(50.0 + 50.0 * rel_gap, 1))
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
            metadata={"raw_pagerank": {s: round(pr.get(s, 0.0), 4) for s in ranked}}
        )
