from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import networkx as nx

from backend.app.models.event import CloudEvent, Severity
from rca_engine.causal_graph import CausalGraphEngine
from rca_engine.scoring import score_candidate
from rca_v2.models import CandidateDiagnosis


class CandidateRanker:
    """
    Ranks RCA candidates using the 5-factor scoring engine with deterministic tie-breaking.
    Tie-break order: (ranking_score DESC, earliest_event_time ASC, service_name ASC).
    """

    def __init__(self, graph_engine: Optional[CausalGraphEngine] = None):
        self.graph_engine = graph_engine

    def rank_candidates(
        self,
        events: List[CloudEvent],
        all_candidates: Optional[List[str]] = None
    ) -> List[CandidateDiagnosis]:
        if not events:
            return []

        # Distinct candidates from events unless explicit candidate list supplied
        if all_candidates is None:
            candidates = sorted(list(set(e.service for e in events if e.service)))
        else:
            candidates = sorted(list(set(all_candidates)))

        earliest_times: Dict[str, str] = {}
        for c in candidates:
            c_events = [e for e in events if e.service == c]
            if c_events:
                earliest_dt = min(e.timestamp for e in c_events)
                earliest_times[c] = earliest_dt.isoformat()
            else:
                earliest_times[c] = datetime.max.replace(tzinfo=timezone.utc).isoformat()

        scored_candidates: List[Tuple[str, float, str]] = []
        for c in candidates:
            res = score_candidate(c, events, graph_engine=self.graph_engine)
            score = float(res.get("total", 0.0))
            scored_candidates.append((c, score, earliest_times[c]))

        # Deterministic tie-break: ranking_score desc, earliest_event_time asc, service_name asc
        scored_candidates.sort(key=lambda item: (-item[1], item[2], item[0]))

        diagnoses: List[CandidateDiagnosis] = []
        for rank_idx, (svc, score, earliest_t) in enumerate(scored_candidates, start=1):
            diagnoses.append(CandidateDiagnosis(
                service=svc,
                ranking_score=score,
                earliest_event_time=earliest_t,
                rank=rank_idx
            ))

        return diagnoses
