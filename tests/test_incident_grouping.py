"""
Incident Grouping Proofs:
Verifies that:
- Cascade event-by-event produces exactly 1 incident with persistent ID
- Shuffled events produce exactly 1 incident
- Duplicates produce zero extra incidents
- Boundary-straddling events produce exactly 1 incident
- All 4 scenarios ingested concurrently produce exactly 4 distinct incidents
"""

import random
from pathlib import Path
from datetime import timedelta
from event_processor.normalizer import load_synthetic_events
from rca_engine.correlation import TimeWindowCorrelator
from backend.app.services.incident_service import IncidentService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_cascade_event_by_event_single_persistent_id():
    cascade_file = PROJECT_ROOT / "data" / "synthetic_events" / "cascade.json"
    events = load_synthetic_events(cascade_file)
    correlator = TimeWindowCorrelator()

    seen_events = []
    generated_ids = []
    for ev in events:
        seen_events.append(ev)
        incs = correlator.correlate(seen_events)
        for inc in incs:
            generated_ids.append(inc.incident_id)

    unique_ids = list(dict.fromkeys(generated_ids))
    assert len(unique_ids) == 1, f"Expected 1 persistent incident ID, got {unique_ids}"
    final_incs = correlator.correlate(seen_events)
    assert len(final_incs) == 1
    assert set(final_incs[0].affected_services) == {"EC2", "API", "Application"}


def test_cascade_shuffled_single_incident():
    cascade_file = PROJECT_ROOT / "data" / "synthetic_events" / "cascade.json"
    events = load_synthetic_events(cascade_file)
    correlator = TimeWindowCorrelator()

    shuffled = list(events)
    random.seed(123)
    random.shuffle(shuffled)

    incs = correlator.correlate(shuffled)
    assert len(incs) == 1
    assert set(incs[0].affected_services) == {"EC2", "API", "Application"}


def test_cascade_with_duplicates_single_incident():
    cascade_file = PROJECT_ROOT / "data" / "synthetic_events" / "cascade.json"
    events = load_synthetic_events(cascade_file)
    correlator = TimeWindowCorrelator()

    duplicated = []
    for ev in events:
        duplicated.append(ev)
        duplicated.append(ev.model_copy()) # exact duplicate

    incs = correlator.correlate(duplicated)
    assert len(incs) == 1


def test_cascade_straddling_bucket_boundary():
    cascade_file = PROJECT_ROOT / "data" / "synthetic_events" / "cascade.json"
    events = load_synthetic_events(cascade_file)
    correlator = TimeWindowCorrelator()

    straddled = []
    # Start at 14:59:50, spanning across 15:00:00 boundary
    base_time = events[0].timestamp.replace(minute=59, second=50)
    for idx, ev in enumerate(events):
        new_ev = ev.model_copy()
        new_ev.timestamp = base_time + timedelta(seconds=idx * 5)
        straddled.append(new_ev)

    incs = correlator.correlate(straddled)
    assert len(incs) == 1, f"Expected boundary straddling to yield 1 incident, got {len(incs)}"


def test_all_four_scenarios_concurrently_produce_four_incidents():
    correlator = TimeWindowCorrelator()
    all_events = []
    for sc in ["cascade", "db_exhaustion", "auth_outage", "memory_leak"]:
        file_path = PROJECT_ROOT / "data" / "synthetic_events" / f"{sc}.json"
        all_events.extend(load_synthetic_events(file_path))

    random.seed(42)
    random.shuffle(all_events)

    incs = correlator.correlate(all_events)
    assert len(incs) == 4, f"Expected 4 distinct incidents for 4 scenarios, got {len(incs)}"
    assert len(set(i.incident_id for i in incs)) == 4
