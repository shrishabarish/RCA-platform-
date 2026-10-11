import uuid
import hashlib
from datetime import timedelta, datetime, timezone
from typing import List, Optional, Any
from backend.app.models.event import CloudEvent, Incident, Severity, IncidentStatus
from rca_engine.noise_filter import NoiseFilterEngine
from backend.app.core.config import config


from rca_engine.causal_graph import causal_graph_engine


def _ensure_utc(dt: datetime) -> datetime:
    """Ensures datetime is timezone-aware UTC for safe comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class TimeWindowCorrelator:
    """
    Robust Multi-Dimensional Time-Window Event Correlator.
    - Pre-filters noise, compacts bursty duplicates, and sorts chronologically.
    - Caps incident window duration and events per incident to prevent unbounded growth.
    - Assigns stable incident ID once at creation; joins open incidents by sliding time gap and topology.
    - Robustly handles late, out-of-order, and bursty events without crashing.
    """

    def __init__(
        self,
        time_window_seconds: Optional[int] = None,
        max_duration_seconds: Optional[int] = None,
        max_events: Optional[int] = None,
        enable_noise_filter: bool = True
    ):
        self.time_window = timedelta(
            seconds=time_window_seconds or config.correlator.window_seconds
        )
        self.max_incident_duration = timedelta(
            seconds=max_duration_seconds or config.correlator.max_incident_window_duration_seconds
        )
        self.max_events_per_incident = max_events or config.correlator.max_events_per_incident
        self.noise_filter = NoiseFilterEngine() if enable_noise_filter else None

    def correlate(self, events: List[CloudEvent], graph_engine: Optional[Any] = None) -> List[Incident]:
        if not events:
            return []

        # 1. Noise filtering and compaction
        if self.noise_filter:
            filtered_events, _ = self.noise_filter.filter_and_compact(events)
        else:
            filtered_events = events

        if not filtered_events:
            return []

        # 2. Sort events chronologically by timestamp (handling late/out-of-order events)
        sorted_events = sorted(filtered_events, key=lambda e: _ensure_utc(e.timestamp))

        cg = graph_engine if graph_engine is not None else causal_graph_engine
        incidents: List[Incident] = []

        for event in sorted_events:
            cur_ts = _ensure_utc(event.timestamp)
            matched_inc = None

            for inc in incidents:
                last_ts = _ensure_utc(inc.updated_at)
                first_ts = _ensure_utc(inc.created_at)

                sliding_diff = abs((cur_ts - last_ts).total_seconds())
                duration_diff = abs((cur_ts - first_ts).total_seconds())

                within_sliding_gap = sliding_diff <= self.time_window.total_seconds()
                within_max_duration = duration_diff <= self.max_incident_duration.total_seconds()
                under_max_events = len(inc.events) < self.max_events_per_incident

                if within_sliding_gap and within_max_duration and under_max_events:
                    # Check topological relation
                    is_related = (
                        not inc.affected_services
                        or any(cg.are_services_related(event.service, s) for s in inc.affected_services)
                    )
                    if is_related:
                        matched_inc = inc
                        break

            if matched_inc is not None:
                # Event joins existing open incident: ID is NEVER recomputed
                matched_inc.events.append(event)
                matched_inc.updated_at = max(matched_inc.updated_at, event.timestamp)
                if event.service and event.service not in matched_inc.affected_services:
                    matched_inc.affected_services = sorted(list(set(matched_inc.affected_services + [event.service])))
                # Update severity if higher
                sev_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
                cur_val = matched_inc.severity.value if hasattr(matched_inc.severity, "value") else matched_inc.severity
                new_val = event.severity.value if hasattr(event.severity, "value") else event.severity
                if sev_order.get(new_val, 0) > sev_order.get(cur_val, 0):
                    matched_inc.severity = event.severity
            else:
                incidents.append(self._create_incident([event]))

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

        # Assign ID ONCE based on initial creation event; persistent and never recomputed
        first_event = events[0]
        first_ts = _ensure_utc(first_event.timestamp)
        epoch_sec = int(first_ts.timestamp())
        event_hash = hashlib.sha256(f"{first_event.event_id}_{epoch_sec}".encode()).hexdigest()[:8]
        incident_id = f"inc_{first_ts.strftime('%Y%m%d%H%M')}_{event_hash}"

        services = sorted(list({e.service for e in events if e.service}))

        return Incident(
            incident_id=incident_id,
            events=events,
            status=IncidentStatus.OPEN,
            severity=highest_severity,
            created_at=events[0].timestamp,
            updated_at=events[-1].timestamp,
            affected_services=services,
            analysis_status="PENDING"
        )
