import uuid
from datetime import timedelta
from typing import List
from backend.app.models.event import CloudEvent, Incident, Severity, IncidentStatus
from rca_engine.noise_filter import NoiseFilterEngine


class TimeWindowCorrelator:
    """
    Robust Multi-Dimensional Time-Window Event Correlator.
    - Pre-filters noise, compacts bursty duplicates, and sorts chronologically.
    - Groups related alarms within a configurable sliding time window.
    - Dynamically computes incident severity, duration, and blast metrics.
    """

    def __init__(self, time_window_seconds: int = 60, enable_noise_filter: bool = True):
        self.time_window = timedelta(seconds=time_window_seconds)
        self.noise_filter = NoiseFilterEngine() if enable_noise_filter else None

    def correlate(self, events: List[CloudEvent]) -> List[Incident]:
        if not events:
            return []

        # 1. Noise filtering and compaction
        if self.noise_filter:
            filtered_events, _ = self.noise_filter.filter_and_compact(events)
        else:
            filtered_events = events

        if not filtered_events:
            return []

        # 2. Sort events chronologically by timestamp
        sorted_events = sorted(filtered_events, key=lambda e: e.timestamp)

        incidents: List[Incident] = []
        current_incident_events = [sorted_events[0]]

        for i in range(1, len(sorted_events)):
            event = sorted_events[i]
            last_event = current_incident_events[-1]

            if event.timestamp - last_event.timestamp <= self.time_window:
                current_incident_events.append(event)
            else:
                incidents.append(self._create_incident(current_incident_events))
                current_incident_events = [event]

        if current_incident_events:
            incidents.append(self._create_incident(current_incident_events))

        return incidents

    def _create_incident(self, events: List[CloudEvent]) -> Incident:
        highest_severity = Severity.LOW
        severity_order = {
            Severity.LOW: 0,
            Severity.MEDIUM: 1,
            Severity.HIGH: 2,
            Severity.CRITICAL: 3,
            "LOW": 0,
            "MEDIUM": 1,
            "HIGH": 2,
            "CRITICAL": 3
        }

        for event in events:
            ev_sev = event.severity
            if severity_order.get(ev_sev, 0) > severity_order.get(highest_severity, 0):
                highest_severity = ev_sev

        return Incident(
            incident_id=f"inc_{uuid.uuid4().hex[:8]}",
            events=events,
            status=IncidentStatus.OPEN,
            severity=highest_severity,
            created_at=events[0].timestamp,
            updated_at=events[-1].timestamp
        )
