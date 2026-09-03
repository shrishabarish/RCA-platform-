import pytest
from datetime import datetime, timezone
from backend.app.models.event import CloudEvent, EventSource, Severity, IncidentStatus
from rca_engine.correlation import TimeWindowCorrelator


def test_correlation_groups_events_within_window(sample_events):
    correlator = TimeWindowCorrelator(time_window_seconds=60)
    incidents = correlator.correlate(sample_events)

    assert len(incidents) == 1
    incident = incidents[0]
    assert len(incident.events) == 3
    assert incident.severity == Severity.CRITICAL
    assert incident.status == IncidentStatus.OPEN
    assert incident.created_at == sample_events[0].timestamp
    assert incident.updated_at == sample_events[-1].timestamp


def test_correlation_splits_events_outside_window():
    events = [
        CloudEvent(
            event_id="e1",
            timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
            source=EventSource.SYNTHETIC,
            event_type="test",
            service="API",
            resource_id="res1",
            severity=Severity.LOW
        ),
        CloudEvent(
            event_id="e2",
            timestamp=datetime(2026, 9, 1, 10, 10, 0, tzinfo=timezone.utc), # 10 min gap
            source=EventSource.SYNTHETIC,
            event_type="test",
            service="EC2",
            resource_id="res2",
            severity=Severity.HIGH
        )
    ]

    correlator = TimeWindowCorrelator(time_window_seconds=60)
    incidents = correlator.correlate(events)

    assert len(incidents) == 2
    assert len(incidents[0].events) == 1
    assert len(incidents[1].events) == 1
    assert incidents[0].severity == Severity.LOW
    assert incidents[1].severity == Severity.HIGH


def test_correlation_empty_events():
    correlator = TimeWindowCorrelator(time_window_seconds=60)
    assert correlator.correlate([]) == []
