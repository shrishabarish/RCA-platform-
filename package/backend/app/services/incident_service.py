import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid

from backend.app.db.database import SessionLocal
from backend.app.db.models import DBCloudEvent, DBIncident
from backend.app.models.event import (
    CloudEvent,
    Incident,
    IncidentStatus,
    RemediationPlan,
    Severity,
    EventSource
)
from rca_engine.correlation import TimeWindowCorrelator
from rca_engine.analyzer import analyze_incident
from rca_engine.remediation import generate_remediation_plan
from rca_engine.dependency_graph import export_topology
from event_processor.normalizer import load_synthetic_events, normalize_event


class IncidentService:
    def __init__(self):
        self.correlator = TimeWindowCorrelator(time_window_seconds=60)
        self.project_root = Path(__file__).resolve().parent.parent.parent.parent
        self._load_default_scenario()

    def _load_default_scenario(self):
        # We only load it if the database is empty
        db = SessionLocal()
        try:
            count = db.query(DBCloudEvent).count()
            if count == 0:
                default_file = self.project_root / "data" / "synthetic_events" / "cascade.json"
                if default_file.exists():
                    self.load_scenario("cascade")
        except Exception:
            pass
        finally:
            db.close()

    def get_available_scenarios(self) -> List[Dict[str, str]]:
        return [
            {
                "id": "cascade",
                "name": "EC2 Overload & Cascading 500 Failure",
                "description": "EC2 CPU spike triggers cascading API gateway latency, 500 errors, and shopping cart checkout failures.",
                "category": "Compute & Infrastructure",
                "root_cause_expected": "EC2"
            },
            {
                "id": "db_exhaustion",
                "name": "DynamoDB Capacity Throttle & Pool Timeout",
                "description": "DynamoDB read throughput throttle triggers API connection pool timeouts and 504 Gateway Timeouts.",
                "category": "Database & Storage",
                "root_cause_expected": "DynamoDB"
            },
            {
                "id": "auth_outage",
                "name": "Auth Identity Provider JWKS Certificate Expiry",
                "description": "Expired OAuth2 JWKS cert leads to instant token validation failure (401s) and mass user logout.",
                "category": "Security & Identity",
                "root_cause_expected": "Auth"
            },
            {
                "id": "memory_leak",
                "name": "Kubernetes Worker Pod OOM Crash Loop",
                "description": "Data analytics worker pod experiences memory leak, gets OOMKilled, resulting in SQS backlog and stalled jobs.",
                "category": "Containers & Queue",
                "root_cause_expected": "Worker"
            }
        ]

    def reset_state(self):
        db = SessionLocal()
        try:
            db.query(DBCloudEvent).delete()
            db.query(DBIncident).delete()
            db.commit()
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def load_scenario(self, scenario_name: str) -> Dict[str, Any]:
        scenario_file = self.project_root / "data" / "synthetic_events" / f"{scenario_name}.json"
        if not scenario_file.exists():
            raise FileNotFoundError(f"Scenario '{scenario_name}' not found at {scenario_file}")

        self.reset_state()
        new_events = load_synthetic_events(scenario_file)
        self.ingest_batch([e.model_dump() for e in new_events])

        incidents = self.get_all_incidents()
        return {
            "scenario": scenario_name,
            "events_loaded": len(new_events),
            "incidents_detected": len(incidents),
            "incidents": [inc.model_dump() for inc in incidents]
        }

    def _get_val(self, obj):
        return obj.value if hasattr(obj, 'value') else obj

    def _convert_to_db_event(self, event: CloudEvent) -> DBCloudEvent:
        return DBCloudEvent(
            event_id=event.event_id,
            timestamp=event.timestamp,
            source=self._get_val(event.source),
            event_type=event.event_type,
            service=event.service,
            resource_id=event.resource_id,
            metric=event.metric,
            value=event.value,
            severity=self._get_val(event.severity),
            metadata_json=event.metadata
        )

    def _convert_to_pydantic_event(self, db_ev: DBCloudEvent) -> CloudEvent:
        return CloudEvent(
            event_id=db_ev.event_id,
            timestamp=db_ev.timestamp,
            source=EventSource(db_ev.source),
            event_type=db_ev.event_type,
            service=db_ev.service,
            resource_id=db_ev.resource_id,
            metric=db_ev.metric,
            value=db_ev.value,
            severity=Severity(db_ev.severity),
            metadata=db_ev.metadata_json or {}
        )

    def _convert_to_pydantic_incident(self, db_inc: DBIncident) -> Incident:
        events = [self._convert_to_pydantic_event(e) for e in db_inc.events]
        return Incident(
            incident_id=db_inc.incident_id,
            events=events,
            status=IncidentStatus(db_inc.status),
            severity=Severity(db_inc.severity),
            created_at=db_inc.created_at,
            updated_at=db_inc.updated_at,
            probable_root_cause=db_inc.probable_root_cause,
            confidence=db_inc.confidence,
            affected_services=db_inc.affected_services or [],
            score_breakdown=db_inc.score_breakdown or {},
            remediation_suggestion=db_inc.remediation_suggestion
        )

    def ingest_event(self, raw_event) -> CloudEvent:
        if isinstance(raw_event, CloudEvent):
            event = raw_event
        else:
            event = normalize_event(raw_event)
            
        db = SessionLocal()
        try:
            db_ev = self._convert_to_db_event(event)
            db.add(db_ev)
            db.commit()
            self._recompute_incidents(db)
        finally:
            db.close()
        return event

    def ingest_batch(self, raw_events: List[Dict[str, Any]]) -> List[CloudEvent]:
        added = []
        db = SessionLocal()
        try:
            for raw in raw_events:
                event = normalize_event(raw)
                added.append(event)
                db_ev = self._convert_to_db_event(event)
                db.add(db_ev)
            db.commit()
            self._recompute_incidents(db)
        finally:
            db.close()
        return added

    def _recompute_incidents(self, db):
        # Fetch all events (in a real app we'd fetch only recent unassociated ones)
        db_events = db.query(DBCloudEvent).order_by(DBCloudEvent.timestamp.asc()).all()
        if not db_events:
            return

        py_events = [self._convert_to_pydantic_event(e) for e in db_events]
        correlated = self.correlator.correlate(py_events)
        
        # Simple upsert logic for incidents
        for inc in correlated:
            analysis = analyze_incident(inc)
            db_inc = db.query(DBIncident).filter(DBIncident.incident_id == inc.incident_id).first()
            
            if not db_inc:
                db_inc = DBIncident(
                    incident_id=inc.incident_id,
                    status=self._get_val(inc.status),
                    severity=self._get_val(inc.severity),
                    created_at=inc.created_at,
                    updated_at=inc.updated_at
                )
                db.add(db_inc)
            
            # Update fields
            db_inc.probable_root_cause = inc.probable_root_cause
            db_inc.confidence = inc.confidence
            db_inc.affected_services = inc.affected_services
            db_inc.score_breakdown = inc.score_breakdown
            db_inc.status = self._get_val(inc.status)
            db_inc.updated_at = inc.updated_at
            
            if inc.probable_root_cause:
                remediation = generate_remediation_plan(inc.incident_id, inc.probable_root_cause)
                db_inc.remediation_suggestion = remediation.model_dump_json()

            # Associate events
            for e in inc.events:
                db_ev = db.query(DBCloudEvent).filter(DBCloudEvent.event_id == e.event_id).first()
                if db_ev:
                    db_ev.incident_id = db_inc.incident_id
            
        db.commit()

    def get_all_events(self) -> List[CloudEvent]:
        db = SessionLocal()
        try:
            db_events = db.query(DBCloudEvent).order_by(DBCloudEvent.timestamp.desc()).all()
            return [self._convert_to_pydantic_event(e) for e in db_events]
        finally:
            db.close()

    def get_all_incidents(self) -> List[Incident]:
        db = SessionLocal()
        try:
            db_incs = db.query(DBIncident).order_by(DBIncident.updated_at.desc()).all()
            return [self._convert_to_pydantic_incident(inc) for inc in db_incs]
        finally:
            db.close()

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        db = SessionLocal()
        try:
            db_inc = db.query(DBIncident).filter(DBIncident.incident_id == incident_id).first()
            if db_inc:
                return self._convert_to_pydantic_incident(db_inc)
            return None
        finally:
            db.close()

    def get_remediation(self, incident_id: str) -> Optional[RemediationPlan]:
        db = SessionLocal()
        try:
            db_inc = db.query(DBIncident).filter(DBIncident.incident_id == incident_id).first()
            if db_inc and db_inc.remediation_suggestion:
                import json
                data = json.loads(db_inc.remediation_suggestion)
                return RemediationPlan(**data)
            return None
        finally:
            db.close()

    def mitigate_incident(self, incident_id: str) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            db_inc = db.query(DBIncident).filter(DBIncident.incident_id == incident_id).first()
            if not db_inc:
                raise KeyError(f"Incident {incident_id} not found")

            db_inc.status = self._get_val(IncidentStatus.RESOLVED)
            db_inc.updated_at = datetime.now(timezone.utc)
            db.commit()

            return {
                "incident_id": incident_id,
                "status": db_inc.status,
                "message": f"Remediation successfully applied to {db_inc.probable_root_cause}. Incident resolved."
            }
        finally:
            db.close()

    def get_topology(self, incident_id: Optional[str] = None) -> Dict[str, Any]:
        root_cause = None
        affected = []
        db = SessionLocal()
        try:
            if incident_id:
                db_inc = db.query(DBIncident).filter(DBIncident.incident_id == incident_id).first()
                if db_inc:
                    root_cause = db_inc.probable_root_cause
                    affected = db_inc.affected_services or []
            else:
                db_inc = db.query(DBIncident).filter(DBIncident.status == "OPEN").first()
                if db_inc:
                    root_cause = db_inc.probable_root_cause
                    affected = db_inc.affected_services or []
        finally:
            db.close()

        return export_topology(root_cause=root_cause, affected_services=affected)


# Global singleton instance
incident_service = IncidentService()
