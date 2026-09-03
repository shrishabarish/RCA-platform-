from datetime import timedelta
from typing import List, Dict
from app.models.event import CloudEvent


class Incident:
    """A group of correlated events, treated as one incident."""
    def __init__(self, incident_id: str):
        self.incident_id = incident_id
        self.events: List[CloudEvent] = []

    def add(self, event: CloudEvent):
        self.events.append(event)

    @property
    def services(self):
        return sorted(set(e.service for e in self.events))

    def __repr__(self):
        return f"<Incident {self.incident_id}: {len(self.events)} events, services={self.services}>"


def correlate_events(
    events: List[CloudEvent],
    time_window_seconds: int = 30
) -> List[Incident]:
    """
    Deterministic rule-based correlation (Phase 3 — no LLM):

    Two events are considered part of the same incident if they
    fall within `time_window_seconds` of the previous event added
    to that incident's timeline. This is a simple chaining rule —
    it deliberately does NOT yet use the dependency graph (that's
    Phase 4/5); it only uses temporal proximity across all events.

    This is intentionally the simplest correlation rule that could
    work, so we have a baseline before adding dependency-awareness.
    """
    sorted_events = sorted(events, key=lambda e: e.timestamp)
    incidents: List[Incident] = []
    current_incident = None
    counter = 0

    for event in sorted_events:
        if current_incident is None:
            counter += 1
            current_incident = Incident(f"INC-{counter:03d}")
            current_incident.add(event)
            incidents.append(current_incident)
            continue

        last_event_time = current_incident.events[-1].timestamp
        gap = event.timestamp - last_event_time

        if gap <= timedelta(seconds=time_window_seconds):
            current_incident.add(event)
        else:
            counter += 1
            current_incident = Incident(f"INC-{counter:03d}")
            current_incident.add(event)
            incidents.append(current_incident)

    return incidents