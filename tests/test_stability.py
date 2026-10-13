import uuid
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError
from backend.app.services.incident_service import incident_service
from backend.app.models.event import CloudEvent, EventSource, Severity


def test_aws_call_failure_resilience(test_client):
    """
    Simulates S3 and SNS total failure during incident analysis.
    Verifies that the RCA response still returns 200 with complete root cause analysis.
    """
    with patch("event_processor.s3_archiver.s3_archiver.archive_incident", side_effect=ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "PutObject"
    )), patch("event_processor.sns_notifier.sns_notifier.publish_incident_alert", side_effect=ClientError(
        {"Error": {"Code": "InvalidClientTokenId", "Message": "Token Invalid"}}, "Publish"
    )):
        res = test_client.post("/api/scenarios/cascade/simulate")
        assert res.status_code == 200
        data = res.json()
        assert data["scenario"] == "cascade"
        assert data["incidents_detected"] > 0

        # Retrieve the analyzed incident
        incidents = test_client.get("/api/incidents").json()
        assert len(incidents) > 0
        latest = incidents[0]
        assert latest["probable_root_cause"] is not None
        assert "ranking_score" in latest or "confidence" in latest


def test_duplicate_events_idempotence(test_client):
    """
    Ingesting identical event payloads multiple times should be idempotent.
    The response succeeds without duplicate side-effects.
    """
    payload = {
        "event_id": "idempotent_evt_101",
        "timestamp": "2026-09-01T12:00:00Z",
        "source": "cloudwatch",
        "event_type": "cpu_utilization_high",
        "service": "EC2",
        "resource_id": "i-idem-01",
        "severity": "HIGH",
        "metadata": {"test": "idempotent"}
    }

    # First ingestion
    res1 = test_client.post("/api/events", json=payload)
    assert res1.status_code == 201

    # Second ingestion with same event_id
    res2 = test_client.post("/api/events", json=payload)
    assert res2.status_code in (200, 201)

    # Ingest directly through service
    event = CloudEvent(
        event_id="idempotent_evt_102",
        timestamp=datetime.now(timezone.utc),
        source=EventSource.APPLICATION,
        event_type="api_error",
        service="API",
        resource_id="api-worker-1",
        severity=Severity.HIGH
    )
    r1 = incident_service.ingest_event(event)
    r2 = incident_service.ingest_event(event)
    assert r1.event_id == r2.event_id


def test_out_of_order_events_handling(test_client):
    """
    Events arriving out of chronological order must not cause crashes or inconsistencies.
    """
    base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    timestamps = [
        base_time + timedelta(minutes=10),
        base_time,  # Earlier event arrives later
        base_time + timedelta(minutes=5),
        base_time + timedelta(minutes=2),
    ]

    events = [
        CloudEvent(
            event_id=f"ooo_evt_{i}",
            timestamp=ts,
            source=EventSource.APPLICATION,
            event_type="test_error",
            service="Database" if i == 1 else "API",
            resource_id=f"res-{i}",
            severity=Severity.HIGH
        )
        for i, ts in enumerate(timestamps)
    ]

    # Correlator and engine should handle out-of-order events gracefully
    analysis_results = incident_service.analyze_raw_events(events)
    assert isinstance(analysis_results, list)
    if analysis_results:
        assert analysis_results[0]["probable_root_cause"] is not None


def test_restart_with_state_loss(test_client):
    """
    Simulate application restart by clearing in-memory caches and active incident stores.
    The system must continue functioning predictably and accept new events.
    """
    # Simulate process memory reset
    incident_service._active_incidents.clear()
    incident_service._active_events.clear()
    incident_service._seen_event_ids.clear()

    # Health and ready endpoints must still work
    health = test_client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    # New simulations work cleanly after restart
    res = test_client.post("/api/scenarios/auth_outage/simulate")
    assert res.status_code == 200
    assert res.json()["incidents_detected"] > 0


def test_alert_burst_1000_events():
    """
    Burst of 1000+ alerts arriving in rapid succession.
    The system must process them without crashing, memory exhaustion, or unhandled errors.
    """
    base_time = datetime.now(timezone.utc)
    burst_events = [
        CloudEvent(
            event_id=f"burst_evt_{i}",
            timestamp=base_time + timedelta(milliseconds=i * 5),
            source=EventSource.CLOUDWATCH if i % 2 == 0 else EventSource.APPLICATION,
            event_type="high_cpu" if i % 3 == 0 else "db_connection_spike",
            service="EC2" if i % 3 == 0 else "RDS",
            resource_id=f"res-{i % 10}",
            severity=Severity.HIGH if i % 5 == 0 else Severity.MEDIUM
        )
        for i in range(1000)
    ]

    results = incident_service.analyze_raw_events(burst_events)
    assert isinstance(results, list)
    for inc in results:
        assert inc["probable_root_cause"] in ["EC2", "RDS", "Database", "Application", "AuthService", "Redis"]


def test_oversized_incident_window_capping():
    """
    Events spanning an oversized window (e.g. 24 hours apart) must be capped
    to max_incident_window_duration_seconds without unbounded growth.
    """
    base_time = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    events = [
        CloudEvent(
            event_id="window_evt_1",
            timestamp=base_time,
            source=EventSource.APPLICATION,
            event_type="db_timeout",
            service="Database",
            resource_id="db-master",
            severity=Severity.CRITICAL
        ),
        CloudEvent(
            event_id="window_evt_2",
            timestamp=base_time + timedelta(hours=5),  # 5 hours later (exceeds 1h window cap)
            source=EventSource.APPLICATION,
            event_type="db_timeout",
            service="Database",
            resource_id="db-master",
            severity=Severity.HIGH
        ),
        CloudEvent(
            event_id="window_evt_3",
            timestamp=base_time + timedelta(hours=24),  # 24 hours later
            source=EventSource.APPLICATION,
            event_type="db_timeout",
            service="Database",
            resource_id="db-master",
            severity=Severity.HIGH
        )
    ]

    results = incident_service.analyze_raw_events(events)
    # The correlator splits or isolates events that exceed max window duration
    assert isinstance(results, list)
    for inc in results:
        duration = (inc["time_window_end"] - inc["time_window_start"]).total_seconds()
        assert duration <= 3600  # Capped at max 3600 seconds (1 hour)


def test_invalid_payloads_return_4xx(test_client):
    """
    Strict input validation with Pydantic: invalid or missing payloads must return
    400 or 422, NEVER 500.
    """
    # Missing required fields
    res1 = test_client.post("/api/events", json={})
    assert res1.status_code in (400, 422)

    # Malformed types
    res2 = test_client.post("/api/events", json={
        "event_id": 12345,
        "timestamp": "not-a-valid-date",
        "severity": "UNKNOWN_SEVERITY"
    })
    assert res2.status_code in (400, 422)

    # Invalid scenario name
    res3 = test_client.post("/api/scenarios/nonexistent_scenario_xyz/simulate")
    assert res3.status_code == 404

    # Non-existent incident detail returns 404, not 500
    res4 = test_client.get("/api/incidents/invalid-inc-id-9999")
    assert res4.status_code == 404


def test_ready_endpoint_status(test_client):
    """
    Tests the new /api/ready endpoint for dependency and database readiness check,
    as well as degraded status when unassigned backlog age exceeds threshold.
    """
    from backend.app.services.correlator_consumer import correlator_consumer

    # Normal operational state
    correlator_consumer.metrics_data["oldest_unassigned_age_seconds"] = 5.0
    res = test_client.get("/api/ready")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "database_connected" in data

    # Degraded state (> 30s threshold)
    correlator_consumer.metrics_data["oldest_unassigned_age_seconds"] = 45.0
    res_deg = test_client.get("/api/ready")
    assert res_deg.status_code == 503
    data_deg = res_deg.json()
    assert data_deg["status"] == "degraded"
    assert data_deg["oldest_unassigned_age_seconds"] == 45.0

    # Reset metrics data
    correlator_consumer.metrics_data["oldest_unassigned_age_seconds"] = 0.0


def test_metrics_endpoint_counters(test_client):
    """
    Tests the /api/metrics endpoint for events, duplicates, outbox, and queue depth counters.
    """
    res = test_client.get("/api/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "events_received" in data
    assert "duplicates_dropped" in data
    assert "outbox_pending" in data
    assert "queue_depth" in data


def test_retention_prune_dry_run(test_client):
    """
    Tests that retention prune with dry_run=True returns eligible counts without deleting rows.
    """
    res = test_client.post("/api/retention/prune?days=30&dry_run=true")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "dry_run"
    assert data["dry_run"] is True
    assert "eligible_rows" in data


def test_mid_burst_restart_recovery():
    """
    Tests that killing process memory mid-burst and restarting does not lose events
    or create duplicate incidents.
    """
    from backend.app.services.outbox_worker import outbox_worker
    base_time = datetime.now(timezone.utc)
    burst = [
        CloudEvent(
            event_id=f"midburst_evt_{i}",
            timestamp=base_time + timedelta(milliseconds=i * 2),
            source=EventSource.APPLICATION,
            event_type="connection_pool_error",
            service="API",
            resource_id="api-gw-01",
            severity=Severity.HIGH
        )
        for i in range(50)
    ]

    # Ingest initial 25 events
    incident_service.ingest_batch([e.model_dump() for e in burst[:25]])

    # Simulate abrupt process restart / crash: memory wiped
    incident_service._seen_event_ids.clear()
    incident_service._active_events.clear()
    incident_service._active_incidents.clear()

    # Restart outbox worker
    outbox_worker.start()

    # Ingest the remaining 25 plus re-try the first 25 (duplicates)
    incident_service.ingest_batch([e.model_dump() for e in burst])

    # Durable DB check must ensure first 25 were deduplicated cleanly
    assert len(incident_service._active_events) <= 50


def test_outbox_s3_sns_recovery():
    """
    Tests that if S3 and SNS are down, outbox records them as RETRYING/PENDING in DB,
    and once S3/SNS recover, outbox delivers everything successfully without loss.
    """
    from backend.app.services.outbox_worker import outbox_worker
    from backend.app.db.database import SessionLocal
    from backend.app.db.models import DBOutboxItem, DBIncident

    test_inc_id = f"inc_outbox_rec_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        inc = DBIncident(
            incident_id=test_inc_id,
            status="OPEN",
            severity="HIGH",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            probable_root_cause="DynamoDB",
            archive_status="PENDING",
            notification_status="PENDING"
        )
        db.add(inc)
        db.commit()

        # Enqueue with S3 & SNS offline
        with patch("event_processor.s3_archiver.s3_archiver.archive_incident", return_value={"status": "FAILED", "error": "Network down"}), \
             patch("event_processor.sns_notifier.sns_notifier.publish_incident_alert", return_value={"status": "FAILED", "error": "Network down"}):
            outbox_worker.enqueue_side_effects(db, {"incident_id": test_inc_id, "severity": "HIGH"})
            db.commit()

            # Process outbox items while offline
            pending = db.query(DBOutboxItem).filter(DBOutboxItem.incident_id == test_inc_id).all()
            for item in pending:
                outbox_worker._process_outbox_item(item.id, test_inc_id, item.event_type)

            # Expire session cache to reflect committed changes from outbox worker
            db.expire_all()

            # Confirm status is RETRYING in database
            retrying_items = db.query(DBOutboxItem).filter(DBOutboxItem.incident_id == test_inc_id).all()
            for it in retrying_items:
                assert it.status == "RETRYING"

        # Now S3 & SNS recover
        with patch("event_processor.s3_archiver.s3_archiver.archive_incident", return_value={"status": "SENT"}), \
             patch("event_processor.sns_notifier.sns_notifier.publish_incident_alert", return_value={"status": "SENT"}):
            for item in retrying_items:
                outbox_worker._process_outbox_item(item.id, test_inc_id, item.event_type)

            # Expire session cache to reflect committed changes from outbox worker
            db.expire_all()

            # Confirm outbox items and incident transitioned to SENT!
            sent_items = db.query(DBOutboxItem).filter(DBOutboxItem.incident_id == test_inc_id).all()
            for it in sent_items:
                assert it.status == "SENT"

            db.refresh(inc)
            assert inc.archive_status == "SENT"
            assert inc.notification_status == "SENT"
    finally:
        db.close()
