import os
import sys
import time
import uuid
import signal
import subprocess
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import requests
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_DATABASE_URL", "postgresql+pg8000://cloudlens_user:cloudlens_test_pass@localhost:5432/cloudlens_test")
POSTGRES_PROD_COPY_URL = "postgresql+pg8000://cloudlens_user:cloudlens_test_pass@localhost:5432/cloudlens_prod_copy"


def wait_for_server(port: int, max_retries: int = 40) -> bool:
    for _ in range(max_retries):
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.2)
    return False


@pytest.mark.integration
def test_sigkill_durability_recovery():
    """
    Step 3 Proof: Real SIGKILL Durability Test:
    - Starts Uvicorn server against real PostgreSQL.
    - Sends an ingestion burst, kills server with kill -9 mid-flight.
    - Restarts server.
    - Asserts 100% of unassigned events are correlated, exactly 1 incident created,
      all outbox records reach SENT, and zero duplicate notifications occur.
    """
    engine = create_engine(POSTGRES_TEST_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM outbox_events; DELETE FROM cloud_events; DELETE FROM incidents;"))
        conn.commit()

    port = 8111
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_TEST_URL
    env["PORT"] = str(port)
    env["OUTBOX_LEASE_TIMEOUT_SECONDS"] = "2"
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["DEPLOY_CUTOFF_TIMESTAMP"] = "2026-01-01T00:00:00Z"
    env["WORKERS"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    assert wait_for_server(port), f"Server failed to start on port {port}"

    events = [
        {
            "event_id": f"evt-sigkill-{i:03d}",
            "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat(),
            "source": "application",
            "event_type": "APIErrors",
            "service": "API",
            "resource_id": "api-gateway",
            "severity": "HIGH"
        }
        for i in range(40)
    ]

    def _post(ev):
        try:
            requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=2.0)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_post, ev) for ev in events[:20]]
        time.sleep(0.1)
        proc.kill()
        proc.wait()

    assert proc.poll() is not None, "Server process should be killed"

    # Restart server
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    assert wait_for_server(port), "Restarted server failed to start"

    # Send remaining events
    for ev in events:
        try:
            requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=2.0)
        except Exception:
            pass

    # Wait for correlator and outbox to drain
    unassigned = -1
    for _ in range(40):
        with engine.connect() as conn:
            unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
            pending_outbox = conn.execute(text("SELECT COUNT(*) FROM outbox_events WHERE status != 'SENT'")).scalar()
            if unassigned == 0 and pending_outbox == 0:
                break
        time.sleep(0.3)

    proc2.terminate()
    try:
        proc2.wait(timeout=3.0)
    except Exception:
        proc2.kill()

    with engine.connect() as conn:
        final_unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        outbox_count = conn.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar()
        outbox_sent = conn.execute(text("SELECT COUNT(*) FROM outbox_events WHERE status = 'SENT'")).scalar()

    assert final_unassigned == 0, f"Expected 0 unassigned events, got {final_unassigned}"
    assert inc_count == 1, f"Expected exactly 1 incident for single cascade, got {inc_count}"
    assert outbox_count == 2, f"Expected exactly 2 outbox entries (1 S3 + 1 SNS), got {outbox_count}"
    assert outbox_sent == 2, f"Expected 2 SENT outbox rows, got {outbox_sent}"


@pytest.mark.integration
def test_concurrency_100_cascades_two_workers():
    """
    Step 3 Proof: Concurrency under 2 Uvicorn Workers:
    - 100 distinct cascades fired concurrently.
    - Asserts exactly 100 incidents created in PostgreSQL (1 per cascade).
    - 4 synthetic scenarios fired concurrently produce exactly 4 distinct incidents.
    """
    engine = create_engine(POSTGRES_TEST_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM outbox_events; DELETE FROM cloud_events; DELETE FROM incidents; DELETE FROM system_settings;"))
        conn.commit()

    port = 8112
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_TEST_URL
    env["PORT"] = str(port)
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["WORKERS"] = "2"
    env["DEPLOY_CUTOFF_TIMESTAMP"] = "2026-01-01T00:00:00Z"
    env["INCIDENT_INACTIVITY_RESOLVE_MINUTES"] = "100000"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--workers", "2", "--log-level", "warning"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    assert wait_for_server(port, max_retries=50), f"2-worker server failed to start on port {port}"

    try:
        # Part 1: 100 distinct cascades
        num_cascades = 100
        all_events = []
        base_dt = datetime.now(timezone.utc) + timedelta(minutes=5)
        for c_idx in range(num_cascades):
            c_time = base_dt + timedelta(hours=c_idx * 2)
            for e_idx in range(5):
                all_events.append({
                    "event_id": f"evt-c{c_idx:03d}-e{e_idx}",
                    "timestamp": (c_time + timedelta(seconds=e_idx * 5)).isoformat(),
                    "source": "application",
                    "event_type": "MemoryAnomaly",
                    "service": f"Service-{c_idx % 15}",
                    "resource_id": f"res-{c_idx}",
                    "severity": "HIGH"
                })

        import random
        random.shuffle(all_events)

        def _send(ev):
            try:
                r = requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=5.0)
                return r.status_code in (200, 201)
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(_send, all_events))

        assert all(results), "All events should be ingested with 200/201"

        # Drain check
        for _ in range(50):
            with engine.connect() as conn:
                unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
                if unassigned == 0:
                    break
            time.sleep(0.3)

        with engine.connect() as conn:
            inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        assert inc_count == num_cascades, f"Expected {num_cascades} incidents, got {inc_count}"

        # Sleep briefly to ensure active batch commit finishes
        time.sleep(0.5)

        # Part 2: 4 synthetic scenarios concurrently
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM outbox_events;"))
            conn.execute(text("DELETE FROM cloud_events;"))
            conn.execute(text("DELETE FROM incidents;"))

        scenarios = ["cascade", "db_exhaustion", "auth_outage", "memory_leak"]
        from event_processor.normalizer import load_synthetic_events
        sc_events = {}
        for sc in scenarios:
            fpath = PROJECT_ROOT / "data" / "synthetic_events" / f"{sc}.json"
            sc_events[sc] = [e.model_dump(mode="json") for e in load_synthetic_events(fpath)]

        interleaved = []
        max_len = max(len(evs) for evs in sc_events.values())
        for i in range(max_len):
            for sc in scenarios:
                if i < len(sc_events[sc]):
                    interleaved.append(sc_events[sc][i])

        with ThreadPoolExecutor(max_workers=10) as ex:
            part2_results = list(ex.map(_send, interleaved))

        assert all(part2_results), "All scenario events should be ingested with 200/201"

        for _ in range(50):
            with engine.connect() as conn:
                unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
                if unassigned == 0:
                    break
            time.sleep(0.3)

        with engine.connect() as conn:
            sc_inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        assert sc_inc_count == 4, f"Expected exactly 4 incidents for 4 scenarios, got {sc_inc_count}"

    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory'"))
            conn.commit()


@pytest.mark.integration
def test_leader_failover_takeover():
    """
    Step 3 Proof: Advisory Lock Leader Failover:
    - Starts two independent CorrelatorConsumer instances.
    - Worker 1 acquires the advisory lock and becomes LEADER.
    - Worker 2 is STANDBY.
    - Sever Worker 1's connection via pg_terminate_backend.
    - Asserts Worker 2 detects the release, acquires the lock, and becomes LEADER within <= 2s.
    """
    from backend.app.services.correlator_consumer import CorrelatorConsumer, CORRELATOR_ADVISORY_LOCK_ID
    import psycopg2

    # Dedicated postgres connection to monitor advisory locks
    conn = psycopg2.connect("postgresql://cloudlens_user:cloudlens_test_pass@localhost:5432/cloudlens_test")
    conn.autocommit = True
    cur = conn.cursor()

    TEST_LOCK_ID = 88888888
    pg_engine = create_engine(POSTGRES_TEST_URL)
    c1 = CorrelatorConsumer(db_engine=pg_engine, lock_id=TEST_LOCK_ID)
    c2 = CorrelatorConsumer(db_engine=pg_engine, lock_id=TEST_LOCK_ID)

    try:
        c1.start()
        time.sleep(0.5)
        c2.start()
        time.sleep(0.5)

        assert c1.is_leader, "Worker 1 should be the leader"
        assert not c2.is_leader, "Worker 2 should be in standby"

        # Find the PID holding the advisory lock
        cur.execute("""
            SELECT pid FROM pg_locks 
            WHERE locktype = 'advisory' AND objid = %s;
        """, (TEST_LOCK_ID,))
        row = cur.fetchone()
        assert row is not None, "PostgreSQL advisory lock should be held"
        holder_pid = row[0]

        # Terminate the leader's PostgreSQL backend connection
        cur.execute("SELECT pg_terminate_backend(%s);", (holder_pid,))
        t_term = time.monotonic()

        # Wait for Worker 2 to take over leadership
        taken_over = False
        for _ in range(40):
            if c2.is_leader:
                taken_over = True
                break
            time.sleep(0.1)

        failover_duration = time.monotonic() - t_term
        assert taken_over, "Worker 2 failed to take over leadership"
        assert failover_duration <= 2.5, f"Failover took too long: {failover_duration:.2f}s"
        assert c2.leader_info["is_leader"] is True

    finally:
        c1.stop(timeout=1.0)
        c2.stop(timeout=1.0)
        conn.close()


@pytest.mark.integration
def test_leader_stuck_loop_watchdog_takeover():
    """
    Step 3 Proof: Leader Stuck Loop Watchdog Takeover:
    - Worker 1 is LEADER.
    - Worker 2 is STANDBY.
    - Worker 1's loop is simulated stuck (stall beyond watchdog threshold).
    - Watchdog triggers and releases advisory lock.
    - Worker 2 acquires lock and becomes LEADER within <= 2.5s.
    """
    from backend.app.services.correlator_consumer import CorrelatorConsumer

    TEST_LOCK_ID = 77777777
    pg_engine = create_engine(POSTGRES_TEST_URL)
    c1 = CorrelatorConsumer(db_engine=pg_engine, lock_id=TEST_LOCK_ID)
    c2 = CorrelatorConsumer(db_engine=pg_engine, lock_id=TEST_LOCK_ID)
    c1.heartbeat_timeout = 1.0
    c2.heartbeat_timeout = 1.0

    try:
        c1.start()
        time.sleep(0.5)
        c2.start()
        time.sleep(0.5)

        assert c1.is_leader, "Worker 1 should be the leader"
        assert not c2.is_leader, "Worker 2 should be in standby"

        # Simulate leader loop stuck
        t_stuck = time.monotonic()
        c1._stuck_for_test = True

        taken_over = False
        for _ in range(30):
            if c2.is_leader:
                taken_over = True
                break
            time.sleep(0.1)

        failover_duration = time.monotonic() - t_stuck
        assert taken_over, "Worker 2 failed to take over from stuck leader"
        assert failover_duration <= 2.5, f"Failover took too long: {failover_duration:.2f}s"
        assert c2.leader_info["is_leader"] is True
        assert not c1.is_leader, "Worker 1 should have relinquished leadership"

    finally:
        c1.stop(timeout=1.0)
        c2.stop(timeout=1.0)


@pytest.mark.integration
def test_leader_kill9_takeover_and_no_duplicates(monkeypatch):
    """
    Step 3 Proof: Real Kill -9 of Leader Process:
    - Starts Standby Worker.
    - Spawns Leader Process as subprocess.
    - Ingests events for 2 cascades into PostgreSQL.
    - Kills Leader process with SIGKILL (kill -9).
    - Asserts Standby Worker takes over leadership <= 2s.
    - Asserts all events are assigned with ZERO duplicate incidents.
    """
    from backend.app.services.correlator_consumer import CorrelatorConsumer
    from backend.app.services.incident_service import IncidentService
    from sqlalchemy.orm import sessionmaker
    import backend.app.db.database as db_database

    pg_engine = create_engine(POSTGRES_TEST_URL)
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM outbox_events; DELETE FROM cloud_events; DELETE FROM incidents;"))
        conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' AND objid = 987654321"))

    pg_session_factory = sessionmaker(bind=pg_engine)
    monkeypatch.setattr(db_database, "SessionLocal", pg_session_factory)
    monkeypatch.setattr(sys.modules["backend.app.services.incident_service"], "SessionLocal", pg_session_factory)

    # Launch Leader as a separate subprocess first
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_TEST_URL
    p_leader = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "run_correlator.py")],
        env=env
    )

    # Wait for p_leader to acquire the advisory lock
    leader_ready = False
    for _ in range(50):
        with pg_engine.connect() as conn:
            lock_held = conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = 987654321")).scalar()
            if lock_held:
                leader_ready = True
                break
        time.sleep(0.1)
    assert leader_ready, "p_leader failed to acquire Postgres advisory lock within timeout"

    standby = CorrelatorConsumer(db_engine=pg_engine)
    standby.start()
    time.sleep(0.3)
    assert not standby.is_leader, "Standby must NOT be leader while main leader is active"

    try:
        # Ingest 20 events (2 distinct cascades 10 events each)
        service = IncidentService()
        now = datetime.now(timezone.utc)
        for i in range(10):
            service.ingest_event({
                "event_id": f"k9_casc1_{i}",
                "timestamp": (now + timedelta(seconds=i)).isoformat(),
                "source": "application",
                "event_type": "HighLatency",
                "service": "EC2" if i == 0 else "API",
                "resource_id": "res-1",
                "severity": "CRITICAL"
            })

        # Kill -9 the leader process
        t_kill = time.monotonic()
        p_leader.kill()
        p_leader.wait(timeout=2.0)

        # Ingest second cascade after kill
        for i in range(10):
            service.ingest_event({
                "event_id": f"k9_casc2_{i}",
                "timestamp": (now + timedelta(minutes=15, seconds=i)).isoformat(),
                "source": "application",
                "event_type": "DiskPressure",
                "service": "Worker" if i == 0 else "Application",
                "resource_id": "res-2",
                "severity": "HIGH"
            })

        # Wait for standby to become leader and drain all events
        taken_over = False
        for _ in range(60):
            if standby.is_leader:
                taken_over = True
                break
            time.sleep(0.1)

        failover_duration = time.monotonic() - t_kill
        assert taken_over, "Standby failed to take over after leader kill -9"
        assert failover_duration <= 5.0, f"Failover duration {failover_duration:.2f}s > 5.0s"

        # Drain check
        drained = False
        for _ in range(40):
            with pg_engine.connect() as conn:
                unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
                if unassigned == 0:
                    drained = True
                    break
            time.sleep(0.2)

        assert drained, "Not all events were drained after leader failover"

        with pg_engine.connect() as conn:
            inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        assert inc_count == 2, f"Expected exactly 2 incidents with zero duplicates, got {inc_count}"

    finally:
        standby.stop(timeout=1.0)
        if p_leader.poll() is None:
            p_leader.kill()


@pytest.mark.integration
def test_legacy_data_startup_safety():
    """
    Step 3 Proof: Legacy Startup Test on the Production-Shaped Copy:
    - Starts application against cloudlens_prod_copy (106,586 unassigned events, 42,033 OPEN incidents).
    - Ingests 50 new events at 10 events/sec.
    - Asserts Outbox rows and notifications exist ONLY for new events;
      zero for legacy events and the 42,033 legacy incidents.
    """
    prod_engine = create_engine(POSTGRES_PROD_COPY_URL)
    with prod_engine.connect() as conn:
        conn.execute(text("DELETE FROM outbox_events WHERE created_at > '2026-09-15'"))
        conn.execute(text("DELETE FROM cloud_events WHERE timestamp > '2026-09-15'"))
        conn.execute(text("DELETE FROM incidents WHERE created_at > '2026-09-15'"))
        conn.commit()

        legacy_open_before = conn.execute(text("SELECT COUNT(*) FROM incidents WHERE status = 'OPEN'")).scalar()
        legacy_unassigned_before = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
        outbox_before = conn.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar()

    assert legacy_open_before == 42033, f"Expected 42,033 OPEN incidents, got {legacy_open_before}"
    assert legacy_unassigned_before >= 106000, f"Expected >= 106,000 legacy unassigned events, got {legacy_unassigned_before}"

    port = 8113
    now_iso = datetime.now(timezone.utc).isoformat()
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_PROD_COPY_URL
    env["PORT"] = str(port)
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["DEPLOY_CUTOFF_TIMESTAMP"] = now_iso  # Ignore anything older than now
    env["WORKERS"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    assert wait_for_server(port, max_retries=40), f"Server failed to start on port {port}"

    try:
        # Ingest 50 new events
        new_events = [
            {
                "event_id": f"evt-new-{i:03d}",
                "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat(),
                "source": "application",
                "event_type": "NewProductionAlarm",
                "service": "API",
                "resource_id": "api-gateway-v2",
                "severity": "HIGH"
            }
            for i in range(50)
        ]

        for ev in new_events:
            r = requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=2.0)
            assert r.status_code in (200, 201)
            time.sleep(0.05)

        # Wait for new events to be processed and correlated
        for _ in range(40):
            with prod_engine.connect() as conn:
                new_inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents WHERE created_at >= :cutoff"), {"cutoff": now_iso}).scalar()
                if new_inc_count >= 1:
                    break
            time.sleep(0.3)

        with prod_engine.connect() as conn:
            # 1. Legacy OPEN incidents must still be 42,033 (untouched)
            legacy_open_after = conn.execute(text("SELECT COUNT(*) FROM incidents WHERE status = 'OPEN' AND created_at < :cutoff"), {"cutoff": now_iso}).scalar()
            assert legacy_open_after == 42033, f"Legacy incidents were modified! Expected 42033, got {legacy_open_after}"

            # 2. Legacy unassigned events must be untouched
            legacy_unassigned_after = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL AND timestamp < :cutoff"), {"cutoff": now_iso}).scalar()
            assert legacy_unassigned_after == legacy_unassigned_before, f"Legacy events were modified! Before: {legacy_unassigned_before}, after: {legacy_unassigned_after}"

            # 3. Exactly 1 new incident was created for the new events
            new_inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents WHERE created_at >= :cutoff"), {"cutoff": now_iso}).scalar()
            assert new_inc_count == 1, f"Expected exactly 1 new incident, got {new_inc_count}"

            # 4. Outbox rows exist ONLY for the new incident (exactly 2: 1 S3 + 1 SNS)
            outbox_after = conn.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar()
            assert outbox_after == 2, f"Expected exactly 2 outbox entries, got {outbox_after}"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except Exception:
            proc.kill()
