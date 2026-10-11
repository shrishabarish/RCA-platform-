import networkx as nx
from datetime import datetime, timezone
from typing import List, Dict, Any

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from backend.app.models.event import CloudEvent, Severity, Incident, IncidentStatus
from rca_engine.causal_graph import causal_graph_engine
from rca_engine.scoring import score_candidate
from rca_engine.analyzer import analyze_incident


class CurrentScorerBaseline(RCABaselineAdapter):
    """
    Adapter wrapping the production 5-factor ensemble RCA engine.
    Dimensions:
    1. Temporal Lead Time (Exponential Decay, 25 pts)
    2. Topological Centrality & Blast Radius (30 pts)
    3. Downstream Symptom Overlap (20 pts)
    4. Metric Anomaly Magnitude (15 pts)
    5. Severity Weight (10 pts)
    """

    @property
    def name(self) -> str:
        return "Current_5Factor_Ensemble"

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

        # 1. Configure causal graph topology for this case
        orig_graph = causal_graph_engine.graph
        try:
            g = nx.DiGraph()
            for n in case.topology.nodes:
                g.add_node(n.id, **n.model_dump())
            for src, dst in case.topology.edges:
                g.add_edge(src, dst)
            causal_graph_engine.graph = g
            causal_graph_engine.invalidate_cache()

            # 2. Convert EventData to CloudEvent models
            valid_sources = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}
            cloud_events: List[CloudEvent] = []
            for i, ed in enumerate(case.events):
                try:
                    sev = Severity(ed.severity)
                except Exception:
                    sev = Severity.HIGH
                dt = datetime.fromisoformat(ed.event_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                src = ed.source.lower() if ed.source.lower() in valid_sources else 'application'
                cloud_events.append(CloudEvent(
                    event_id=ed.event_id or f"evt_{i}",
                    timestamp=dt,
                    source=src,
                    event_type=f"{ed.metric.lower()}_anomaly",
                    service=ed.service,
                    resource_id=f"res_{ed.service.lower()}",
                    metric=ed.metric,
                    value=ed.value,
                    severity=sev
                ))

            # 3. Create Incident in-memory
            first_dt = cloud_events[0].timestamp
            last_dt = cloud_events[-1].timestamp
            distinct_services = sorted(list(set(e.service for e in cloud_events if e.service)))
            
            inc = Incident(
                incident_id=f"eval_inc_{case.case_id}",
                events=cloud_events,
                status=IncidentStatus.OPEN,
                severity=Severity.HIGH,
                created_at=first_dt,
                updated_at=last_dt,
                affected_services=distinct_services,
                analysis_status="PENDING"
            )

            # 4. Analyze incident
            analysis = analyze_incident(inc)
            ranked_candidates = [c["service"] for c in analysis.get("all_candidate_scores", [])]
            scores = {c["service"]: float(c["total"]) for c in analysis.get("all_candidate_scores", [])}
            top_confidence = float(analysis.get("confidence", 0.0))

            return BaselineResult(
                baseline_name=self.name,
                case_id=case.case_id,
                verdict="DIAGNOSE",  # Never abstains per requirement
                predicted_root_causes=ranked_candidates,
                candidate_scores=scores,
                confidence=top_confidence,
                metadata={
                    "is_compound": analysis.get("is_compound_incident", False),
                    "contributing_causes": analysis.get("contributing_causes", []),
                    "causal_paths": analysis.get("causal_paths", [])
                }
            )
        finally:
            causal_graph_engine.graph = orig_graph
            causal_graph_engine.invalidate_cache()
