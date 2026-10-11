from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from backend.app.models.event import CloudEvent, Severity, Incident, IncidentStatus
from rca_engine.causal_graph import CausalGraphEngine
from rca_engine.analyzer import analyze_incident


def _run_current_scorer(case: EvaluationCase, baseline_name: str, use_case_topology: bool = True) -> BaselineResult:
    if not case.events:
        return BaselineResult(
            baseline_name=baseline_name,
            case_id=case.case_id,
            verdict="DIAGNOSE",
            predicted_root_causes=[],
            candidate_scores={},
            confidence=0.0
        )

    # Isolated graph engine if case topology requested, otherwise default static graph
    cg = CausalGraphEngine.from_topology(case.topology) if use_case_topology else None

    # Convert EventData to CloudEvent models
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

    analysis = analyze_incident(inc, graph_engine=cg)
    ranked_candidates = [c["service"] for c in analysis.get("all_candidate_scores", [])]
    scores = {c["service"]: float(c["total"]) for c in analysis.get("all_candidate_scores", [])}
    top_confidence = float(analysis.get("confidence", 0.0))

    return BaselineResult(
        baseline_name=baseline_name,
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=ranked_candidates,
        candidate_scores=scores,
        confidence=top_confidence,
        metadata={
            "is_compound": analysis.get("is_compound_incident", False),
            "contributing_causes": analysis.get("contributing_causes", []),
            "causal_paths": analysis.get("causal_paths", [])
        }
    )


class CurrentScorerCaseTopologyBaseline(RCABaselineAdapter):
    """Production 5-factor ensemble RCA engine evaluated with case topology."""

    @property
    def name(self) -> str:
        return "CurrentScorer_CaseTopology"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        return _run_current_scorer(case, self.name, use_case_topology=True)


class CurrentScorerStaticGraphBaseline(RCABaselineAdapter):
    """Production 5-factor ensemble RCA engine evaluated with fixed static 6-node graph."""

    @property
    def name(self) -> str:
        return "CurrentScorer_StaticGraph"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        return _run_current_scorer(case, self.name, use_case_topology=False)


class CurrentScorerBaseline(CurrentScorerCaseTopologyBaseline):
    """Default CurrentScorer baseline wrapping case topology."""

    @property
    def name(self) -> str:
        return "Current_5Factor_Ensemble"
