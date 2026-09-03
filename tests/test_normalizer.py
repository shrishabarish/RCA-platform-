import pytest
from pathlib import Path
from event_processor.normalizer import normalize_event, load_synthetic_events
from backend.app.models.event import CloudEvent, Severity, EventSource


def test_normalize_event_from_dict():
    raw = {
        "event_id": "evt_test_01",
        "timestamp": "2026-09-01T12:00:00Z",
        "source": "cloudwatch",
        "event_type": "high_cpu",
        "service": "EC2",
        "resource_id": "i-12345",
        "severity": "HIGH",
        "metadata": {"region": "us-east-1"}
    }
    event = normalize_event(raw)
    assert isinstance(event, CloudEvent)
    assert event.event_id == "evt_test_01"
    assert event.severity == Severity.HIGH
    assert event.source == EventSource.CLOUDWATCH


def test_load_synthetic_events_all_scenarios():
    scenarios = ["cascade", "db_exhaustion", "auth_outage", "memory_leak"]
    base_path = Path(__file__).resolve().parent.parent / "data" / "synthetic_events"

    for sc in scenarios:
        file_path = base_path / f"{sc}.json"
        events = load_synthetic_events(file_path)
        assert len(events) > 0
        for e in events:
            assert isinstance(e, CloudEvent)
            assert e.service
            assert e.severity
