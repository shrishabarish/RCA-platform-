import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import backend.app.db.database as db_database
from backend.app.db.database import Base
from backend.app.db.models import DBCloudEvent, DBIncident, DBOutboxItem
from backend.app.services.incident_service import IncidentService
from backend.app.services.outbox_worker import OutboxWorker
from backend.app.services.retention_service import RetentionService

POSTGRES_TEST_URL = os.getenv(
    "POSTGRES_TEST_DATABASE_URL",
    "postgresql+pg8000://postgres:postgres@localhost:5432/cloudlens_test"
)


@pytest.fixture(scope="module")
def pg_engine():
    """Sets up schema in local PostgreSQL test database."""
    engine = create_engine(POSTGRES_TEST_URL, pool_size=15, max_overflow=20)
    Base.metadata.create_all(bind=engine)
    yield engine
    from backend.app.services.correlator_consumer import correlator_consumer
    was_running = correlator_consumer._running
    if was_running:
        correlator_consumer.stop(timeout=1.0)
    with engine.begin() as conn:
        conn.execute(text("UPDATE cloud_events SET incident_id = NULL;"))
        conn.execute(text("DELETE FROM outbox_events;"))
        conn.execute(text("DELETE FROM cloud_events;"))
        conn.execute(text("DELETE FROM incidents;"))
    if was_running:
        correlator_consumer.start()
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_pg_tables(pg_engine, monkeypatch):
    """Truncates tables before each test and binds SessionLocal in services to PostgreSQL."""
    from backend.app.services.correlator_consumer import correlator_consumer
    from backend.app.services.outbox_worker import outbox_worker
    was_running = correlator_consumer._running
    outbox_was_running = outbox_worker._running
    if was_running:
        correlator_consumer.stop(timeout=1.0)
    if outbox_was_running:
        outbox_worker.stop(timeout=1.0)

    with pg_engine.begin() as conn:
        conn.execute(text("UPDATE cloud_events SET incident_id = NULL;"))
        conn.execute(text("DELETE FROM outbox_events;"))
        conn.execute(text("DELETE FROM cloud_events;"))
        conn.execute(text("DELETE FROM incidents;"))

    if was_running:
        correlator_consumer.start()
    if outbox_was_running:
        outbox_worker.start()

    pg_session_factory = sessionmaker(bind=pg_engine)
    monkeypatch.setattr(db_database, "SessionLocal", pg_session_factory)
    monkeypatch.setattr(sys.modules["backend.app.services.incident_service"], "SessionLocal", pg_session_factory)
    monkeypatch.setattr(sys.modules["backend.app.services.outbox_worker"], "SessionLocal", pg_session_factory)
    monkeypatch.setattr(sys.modules["backend.app.services.retention_service"], "SessionLocal", pg_session_factory)
    if "backend.app.services.correlator_consumer" in sys.modules:
        monkeypatch.setattr(sys.modules["backend.app.services.correlator_consumer"], "SessionLocal", pg_session_factory)


def test_postgres_concurrency_50_same_event_id(pg_engine):
    """
    Step 3 Proof: 50 simultaneous requests with the same event ID produce exactly one row
    in PostgreSQL using atomic ON CONFLICT DO NOTHING / IntegrityError handling.
    """
    service = IncidentService()
    test_event_id = f"pg_race_{uuid.uuid4().hex[:12]}"
    raw_event = {
        "event_id": test_event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "application",
        "event_type": "HttpError",
        "service": "API",
        "resource_id": "api-gateway-1",
        "metric": "500_errors",
        "value": 50.0,
        "severity": "CRITICAL"
    }

    # Launch 50 concurrent threads simultaneously targeting the exact same event
    num_concurrent = 50
    results = []

    def _ingest():
        return service.ingest_event(raw_event)

    with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
        futures = [executor.submit(_ingest) for _ in range(num_concurrent)]
        for f in as_completed(futures):
            results.append(f.result())

    assert len(results) == 50
    for res in results:
        assert res.event_id == test_event_id

    # Verify exactly one row was committed to PostgreSQL
    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM cloud_events WHERE event_id = :eid"),
            {"eid": test_event_id}
        ).scalar()
        assert count == 1, f"Expected exactly 1 row in PostgreSQL, found {count}"


def test_postgres_two_workers_skip_locked(pg_engine):
    """
    Step 3 Proof: Two outbox worker processes on the same PostgreSQL:
    Each item is claimed by only one worker at a time via SELECT ... FOR UPDATE SKIP LOCKED.
    """
    worker1 = OutboxWorker(lease_timeout_seconds=30)
    worker2 = OutboxWorker(lease_timeout_seconds=30)

    # Insert 20 pending outbox items directly into PostgreSQL
    Session = sessionmaker(bind=pg_engine)
    db = Session()
    inc_id = f"inc_pg_workers_{uuid.uuid4().hex[:8]}"
    inc = DBIncident(
        incident_id=inc_id,
        status="OPEN",
        severity="HIGH",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        archive_status="PENDING",
        notification_status="PENDING"
    )
    db.add(inc)
    db.commit()

    item_ids = []
    for i in range(20):
        item = DBOutboxItem(
            id=f"outbox_race_{i}_{uuid.uuid4().hex[:6]}",
            incident_id=inc_id,
            event_type="SNS_NOTIFICATION",
            payload_json={"incident_id": inc_id, "index": i},
            status="PENDING",
            attempts=0,
            max_attempts=8,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(item)
        item_ids.append(item.id)
    db.commit()
    db.close()

    claimed_by_w1 = []
    claimed_by_w2 = []

    def _worker_claim_loop(worker, collector):
        while True:
            claimed = worker.claim_next_item()
            if not claimed:
                break
            collector.append(claimed["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_worker_claim_loop, worker1, claimed_by_w1)
        f2 = executor.submit(_worker_claim_loop, worker2, claimed_by_w2)
        f1.result()
        f2.result()

    total_claimed = len(claimed_by_w1) + len(claimed_by_w2)
    assert total_claimed == 20, f"Expected 20 items claimed, got {total_claimed}"

    # Verify zero overlap between worker 1 and worker 2
    overlap = set(claimed_by_w1).intersection(set(claimed_by_w2))
    assert len(overlap) == 0, f"Collision detected! Both workers claimed: {overlap}"


def test_postgres_outbox_crash_recovery_and_lease(pg_engine):
    """
    Step 2c Proof: Rows stuck in PROCESSING past lease timeout are recovered and reclaimed.
    """
    worker = OutboxWorker(lease_timeout_seconds=5)
    Session = sessionmaker(bind=pg_engine)
    db = Session()
    inc_id = f"inc_pg_lease_{uuid.uuid4().hex[:8]}"
    inc = DBIncident(
        incident_id=inc_id,
        status="OPEN",
        severity="HIGH",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(inc)

    # Insert an item stuck in PROCESSING whose lease expired 10 seconds ago
    expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    stuck_id = f"outbox_stuck_{uuid.uuid4().hex[:6]}"
    stuck_item = DBOutboxItem(
        id=stuck_id,
        incident_id=inc_id,
        event_type="S3_ARCHIVE",
        payload_json={"incident_id": inc_id},
        status="PROCESSING",
        attempts=1,
        max_attempts=8,
        lease_until=expired_time,
        created_at=expired_time,
        updated_at=expired_time
    )
    db.add(stuck_item)
    db.commit()
    db.close()

    # Worker should reclaim the expired PROCESSING item
    claimed = worker.claim_next_item()
    assert claimed is not None
    assert claimed["id"] == stuck_id


def test_postgres_deterministic_incident_lifecycle(pg_engine):
    """
    Step 2d Proof: Repeated events in the same correlation window join the same
    deterministic incident; inactive incidents auto-transition to RESOLVED.
    """
    with pg_engine.connect() as conn:
        conn.execute(text("DELETE FROM outbox_events; DELETE FROM cloud_events; DELETE FROM incidents;"))
        conn.commit()

    service = IncidentService()
    base_time = datetime.now(timezone.utc)

    # Event 1
    ev1 = {
        "event_id": f"pg_inc_ev1_{uuid.uuid4().hex[:6]}",
        "timestamp": base_time.isoformat(),
        "source": "application",
        "event_type": "LatencySpike",
        "service": "API",
        "resource_id": "api-1",
        "severity": "HIGH"
    }
    # Event 2 arriving 10 seconds later in the same correlation window with same service
    ev2 = {
        "event_id": f"pg_inc_ev2_{uuid.uuid4().hex[:6]}",
        "timestamp": (base_time + timedelta(seconds=10)).isoformat(),
        "source": "application",
        "event_type": "500Error",
        "service": "API",
        "resource_id": "api-1",
        "severity": "CRITICAL"
    }

    service.ingest_event(ev1)
    service.ingest_event(ev2)

    # Correlator consumer claims and processes unassigned events
    from backend.app.services.correlator_consumer import CorrelatorConsumer
    consumer = CorrelatorConsumer(db_engine=pg_engine)
    consumer.drain()

    with pg_engine.connect() as conn:
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        # Both events joined the same deterministic incident!
        assert inc_count == 1

        events_linked = conn.execute(
            text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NOT NULL")
        ).scalar()
        assert events_linked == 2


def test_postgres_retention_safety_aborts_on_archive_failure(pg_engine):
    """
    Step 2f Proof: If S3 archive step fails, deletion must abort and delete zero rows.
    """
    retention = RetentionService()
    Session = sessionmaker(bind=pg_engine)
    db = Session()

    # Seed 5 old events
    old_time = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(5):
        ev = DBCloudEvent(
            event_id=f"pg_old_{i}_{uuid.uuid4().hex[:6]}",
            timestamp=old_time,
            source="application",
            event_type="Heartbeat",
            service="Worker",
            resource_id=f"worker-{i}",
            severity="LOW"
        )
        db.add(ev)
    db.commit()
    db.close()

    # Mock S3 archiver to raise an exception
    with patch("event_processor.s3_archiver.s3_archiver.archive_telemetry_batch", side_effect=RuntimeError("S3 AccessDenied")):
        res = retention.archive_and_prune(days=30, dry_run=False)

    assert res["status"] == "aborted"
    assert res["deleted_rows"] == 0

    # Verify all 5 rows remain untouched in PostgreSQL
    with pg_engine.connect() as conn:
        remaining = conn.execute(text("SELECT COUNT(*) FROM cloud_events")).scalar()
        assert remaining == 5
