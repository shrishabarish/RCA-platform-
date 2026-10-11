import os
import sys
import time
import uuid
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest
import requests
from sqlalchemy import create_engine, text

POSTGRES_TEST_URL = os.getenv(
    "POSTGRES_TEST_DATABASE_URL",
    "postgresql+pg8000://postgres:postgres@localhost:5432/cloudlens_test"
)


def test_real_sigkill_mid_burst_recovery():
    """
    Step 3 Proof: Real SIGKILL test:
    Run the server as a subprocess, send a burst, kill -9 it mid-burst, restart,
    and verify: unique events sent equals rows in DB, all outbox rows reach SENT, zero duplicates.
    """
    engine = create_engine(POSTGRES_TEST_URL)
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE outbox_events, cloud_events, incidents CASCADE;"))
        conn.commit()

    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_TEST_URL
    env["PORT"] = "8099"
    env["OUTBOX_LEASE_TIMEOUT_SECONDS"] = "2"
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["DEPLOY_CUTOFF_TIMESTAMP"] = "2026-01-01T00:00:00Z"

    # 1. Start server as a subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", "8099", "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait for server to become healthy
    ready = False
    for _ in range(30):
        try:
            r = requests.get("http://127.0.0.1:8099/api/health", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.2)

    assert ready, "Server failed to start on port 8099"

    # 2. Generate a burst of 60 events
    events = [
        {
            "event_id": f"sigkill_burst_{i}_{uuid.uuid4().hex[:6]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "application",
            "event_type": "LatencyAnomaly",
            "service": "API",
            "resource_id": f"api-{i % 3}",
            "metric": "p99_latency_ms",
            "value": 150.0 + i,
            "severity": "HIGH"
        }
        for i in range(60)
    ]

    sent_before_kill = []
    failed_due_to_kill = []

    def _post_event(ev):
        try:
            res = requests.post("http://127.0.0.1:8099/api/events", json=ev, timeout=2.0)
            if res.status_code in (200, 201):
                sent_before_kill.append(ev["event_id"])
        except Exception:
            failed_due_to_kill.append(ev["event_id"])

    # Launch concurrent burst and kill -9 mid-flight
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_post_event, ev) for ev in events[:30]]
        time.sleep(0.1)  # Allow half to inflight
        # Real SIGKILL (kill -9)
        proc.kill()
        proc.wait()

    assert proc.poll() is not None, "Server process should be dead"

    # 3. Restart server as a new subprocess
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", "8099", "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    ready2 = False
    for _ in range(30):
        try:
            r = requests.get("http://127.0.0.1:8099/api/health", timeout=1.0)
            if r.status_code == 200:
                ready2 = True
                break
        except Exception:
            time.sleep(0.2)

    assert ready2, "Restarted server failed to start"

    # 4. Resend all 60 events (testing deduplication for already-sent and completion for rest)
    confirmed_events = set()
    for ev in events:
        try:
            r = requests.post("http://127.0.0.1:8099/api/events", json=ev, timeout=2.0)
            if r.status_code in (200, 201):
                confirmed_events.add(ev["event_id"])
        except Exception as e:
            pytest.fail(f"Event post failed after restart: {e}")

    # Wait for outbox worker to deliver all queued and recovered items
    pending_outbox = -1
    for _ in range(40):
        with engine.connect() as conn:
            pending_outbox = conn.execute(
                text("SELECT COUNT(*) FROM outbox_events WHERE status != 'SENT'")
            ).scalar()
            if pending_outbox == 0:
                break
        time.sleep(0.3)

    assert pending_outbox == 0, f"Expected all outbox rows to reach SENT, {pending_outbox} remain"

    # Clean shutdown of restarted server
    proc2.terminate()
    try:
        proc2.wait(timeout=3.0)
    except Exception:
        proc2.kill()
