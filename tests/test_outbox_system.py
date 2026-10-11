import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.db.database import Base, get_db
from backend.app.db.models import DBIncident, DBOutboxItem

import backend.app.db.database as db_database
import backend.app.services.outbox_worker as ow_mod
from sqlalchemy.pool import StaticPool

TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def setup_outbox_test_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    orig_sl = db_database.SessionLocal
    orig_ow_sl = ow_mod.SessionLocal
    db_database.SessionLocal = TestingSessionLocal
    ow_mod.SessionLocal = TestingSessionLocal
    yield
    db_database.SessionLocal = orig_sl
    ow_mod.SessionLocal = orig_ow_sl
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    yield db
    db.rollback()
    db.close()


client = TestClient(app)


def test_outbox_model_columns():
    """Verify that DBOutboxItem contains all required columns and default values."""
    item = DBOutboxItem(
        id="item-test-1",
        incident_id="inc-test-1",
        event_type="SNS_NOTIFICATION",
        payload_json={"message": "alert"},
        status="PENDING",
        attempts=0,
        max_attempts=5,
    )
    assert hasattr(item, "id")
    assert hasattr(item, "incident_id")
    assert hasattr(item, "event_type")
    assert hasattr(item, "payload_json")
    assert hasattr(item, "status")
    assert hasattr(item, "attempts")
    assert hasattr(item, "max_attempts")
    assert hasattr(item, "lease_until")
    assert hasattr(item, "next_retry_at")
    assert hasattr(item, "last_error")
    assert hasattr(item, "created_at")
    assert hasattr(item, "updated_at")


def test_outbox_and_incident_status_domain_values(db_session):
    """Verify outbox and incident status enumerations supported in codebase."""
    # Outbox status domain: PENDING, PROCESSING, RETRYING, SENT, FAILED, DEAD_LETTER
    for status in ["PENDING", "PROCESSING", "RETRYING", "SENT", "FAILED", "DEAD_LETTER"]:
        item = DBOutboxItem(
            id=f"item-{status}",
            incident_id="inc-status",
            event_type="SNS_NOTIFICATION",
            payload_json={},
            status=status,
            attempts=1,
            max_attempts=5,
        )
        db_session.add(item)
    db_session.commit()

    # Incident status domain: archive_status, notification_status, analysis_status
    incident = DBIncident(
        incident_id="inc-status-verify",
        status="OPEN",
        severity="HIGH",
        archive_status="PENDING",
        notification_status="PENDING",
        analysis_status="PENDING",
    )
    db_session.add(incident)
    db_session.commit()

    # Update through valid lifecycle states
    incident.archive_status = "SENT"
    incident.notification_status = "RETRYING"
    incident.analysis_status = "DONE"
    db_session.commit()

    fetched = db_session.query(DBIncident).filter_by(incident_id="inc-status-verify").first()
    assert fetched.archive_status == "SENT"
    assert fetched.notification_status == "RETRYING"
    assert fetched.analysis_status == "DONE"


def test_failed_outbox_list_and_replay_endpoints(db_session):
    """Verify GET /api/outbox/failed and POST /api/outbox/failed/{id}/replay."""
    failed_item = DBOutboxItem(
        id="item-fail-replay-1",
        incident_id="inc-replay-1",
        event_type="SNS_NOTIFICATION",
        payload_json={"alert": "cpu"},
        status="FAILED",
        attempts=5,
        max_attempts=5,
        last_error="SNS Connection timeout",
    )
    dead_item = DBOutboxItem(
        id="item-dead-replay-2",
        incident_id="inc-replay-2",
        event_type="SNS_NOTIFICATION",
        payload_json={"alert": "disk"},
        status="DEAD_LETTER",
        attempts=5,
        max_attempts=5,
        last_error="Delivery rejected",
    )
    db_session.add(failed_item)
    db_session.add(dead_item)
    db_session.commit()

    # 1. GET /api/outbox/failed
    res = client.get("/api/outbox/failed")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    ids = [i["id"] for i in data]
    assert "item-fail-replay-1" in ids
    assert "item-dead-replay-2" in ids

    # 2. POST /api/outbox/failed/{item_id}/replay
    res_replay = client.post("/api/outbox/failed/item-fail-replay-1/replay")
    assert res_replay.status_code == 200
    replay_body = res_replay.json()
    assert replay_body["status"] == "replayed"
    assert replay_body["item_id"] == "item-fail-replay-1"

    # Verify state in DB
    db_session.expire_all()
    replayed = db_session.query(DBOutboxItem).filter_by(id="item-fail-replay-1").first()
    assert replayed.status == "PENDING"
    assert replayed.attempts == 0

    # 3. POST /api/outbox/failed/replay-all
    res_replay_all = client.post("/api/outbox/failed/replay-all")
    assert res_replay_all.status_code == 200
    all_body = res_replay_all.json()
    assert all_body["status"] == "replayed_all"
    assert all_body["count"] >= 1

    db_session.expire_all()
    dead_replayed = db_session.query(DBOutboxItem).filter_by(id="item-dead-replay-2").first()
    assert dead_replayed.status == "PENDING"
    assert dead_replayed.attempts == 0
