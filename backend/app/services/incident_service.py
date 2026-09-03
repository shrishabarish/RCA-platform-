import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid

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
        self.events: List[CloudEvent] = []
        self.incidents: Dict[str, Incident] = {}
        self.remediations: Dict[str, RemediationPlan] = {}
        self.correlator = TimeWindowCorrelator(time_window_seconds=60)
        self.project_root = Path(__file__).resolve().parent.parent.parent.parent
        self._load_default_scenario()

    def _load_default_scenario(self):
        default_file = self.project_root / "data" / "synthetic_events" / "cascade.json"
        if default_file.exists():
            self.load_scenario("cascade")

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
        self.events = []
        self.incidents = {}
        self.remediations = {}

    def load_scenario(self, scenario_name: str) -> Dict[str, Any]:
        scenario_file = self.project_root / "data" / "synthetic_events" / f"{scenario_name}.json"
        if not scenario_file.exists():
            raise FileNotFoundError(f"Scenario '{scenario_name}' not found at {scenario_file}")

        self.reset_state()
        new_events = load_synthetic_events(scenario_file)
        self.events = new_events
        self._recompute_incidents()

        return {
            "scenario": scenario_name,
            "events_loaded": len(self.events),
            "incidents_detected": len(self.incidents),
            "incidents": [inc.model_dump() for inc in self.incidents.values()]
        }

    def ingest_event(self, raw_event: Dict[str, Any]) -> CloudEvent:
        event = normalize_event(raw_event)
        self.events.append(event)
        self._recompute_incidents()
        return event

    def ingest_batch(self, raw_events: List[Dict[str, Any]]) -> List[CloudEvent]:
        added = []
        for raw in raw_events:
            event = normalize_event(raw)
            self.events.append(event)
            added.append(event)
        self._recompute_incidents()
        return added

    def _recompute_incidents(self):
        if not self.events:
            self.incidents = {}
            return

        correlated = self.correlator.correlate(self.events)
        self.incidents = {}
        for inc in correlated:
            analysis = analyze_incident(inc)
            self.incidents[inc.incident_id] = inc
            # Generate remediation
            if inc.probable_root_cause:
                remediation = generate_remediation_plan(inc.incident_id, inc.probable_root_cause)
                self.remediations[inc.incident_id] = remediation

    def get_all_events(self) -> List[CloudEvent]:
        return sorted(self.events, key=lambda e: e.timestamp, reverse=True)

    def get_all_incidents(self) -> List[Incident]:
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        return self.incidents.get(incident_id)

    def get_remediation(self, incident_id: str) -> Optional[RemediationPlan]:
        return self.remediations.get(incident_id)

    def mitigate_incident(self, incident_id: str) -> Dict[str, Any]:
        incident = self.incidents.get(incident_id)
        if not incident:
            raise KeyError(f"Incident {incident_id} not found")

        incident.status = IncidentStatus.RESOLVED
        incident.updated_at = datetime.now(timezone.utc)

        return {
            "incident_id": incident_id,
            "status": incident.status,
            "message": f"Remediation successfully applied to {incident.probable_root_cause}. Incident resolved."
        }

    def get_topology(self, incident_id: Optional[str] = None) -> Dict[str, Any]:
        root_cause = None
        affected = []
        if incident_id and incident_id in self.incidents:
            inc = self.incidents[incident_id]
            root_cause = inc.probable_root_cause
            affected = inc.affected_services
        elif self.incidents:
            # Pick first open incident
            first_inc = next(iter(self.incidents.values()))
            root_cause = first_inc.probable_root_cause
            affected = first_inc.affected_services

        return export_topology(root_cause=root_cause, affected_services=affected)


# Global singleton instance
incident_service = IncidentService()
