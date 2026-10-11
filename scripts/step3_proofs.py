#!/usr/bin/env python3
"""
Step 3 Proofs: Comprehensive Stability, Durability, Concurrency, and Load Benchmarks.
Executes:
1. SIGKILL Durability Proof
2. Concurrency Proof (100 cascades across 2 workers + 4 scenarios concurrent)
3. Load Benchmarks (Saturation + Fixed-rate 50, 100, 200 req/s for 1 and 2 workers)
"""

import os
import sys
import time
import uuid
import signal
import psutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
import numpy as np
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.core.safety import check_database_safety, print_database_host

POSTGRES_URL = os.getenv("POSTGRES_TEST_DATABASE_URL", "postgresql+pg8000://postgres:postgres@localhost:5432/cloudlens_test")
check_database_safety(POSTGRES_URL, entry_point="step3_proofs")
print_database_host(POSTGRES_URL, entry_point="step3_proofs")
engine = create_engine(POSTGRES_URL)


def reset_db():
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE outbox_events, cloud_events, incidents CASCADE;"))
        conn.commit()


def wait_for_server(port: int, max_retries: int = 40) -> bool:
    for _ in range(max_retries):
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.2)
    return False


def run_sigkill_proof(port: int = 8101):
    print("\n" + "=" * 80)
    print("STEP 3 PROOF 1: SIGKILL DURABILITY TEST")
    print("=" * 80)
    reset_db()

    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_URL
    env["PORT"] = str(port)
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["WORKERS"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env
    )
    assert wait_for_server(port), "Server failed to start on port 8101"

    # Ingest 3 events of a cascade
    base_ts = datetime(2026, 10, 11, 14, 0, 0, tzinfo=timezone.utc)
    cascade_events = [
        {
            "event_id": f"sigkill_ev_{i}",
            "timestamp": (base_ts + timedelta(seconds=i * 5)).isoformat(),
            "source": "application",
            "event_type": "Http500" if i > 0 else "CpuSpike",
            "service": "EC2" if i == 0 else "API",
            "resource_id": f"res-{i}",
            "severity": "CRITICAL"
        }
        for i in range(3)
    ]

    for ev in cascade_events:
        r = requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=5.0)
        assert r.status_code == 201

    # Immediately SIGKILL the server process
    print("Killing server process immediately with SIGKILL (kill -9)...")
    proc.kill()
    proc.wait()

    # Verify events are stored in DB
    with engine.connect() as conn:
        stored_cnt = conn.execute(text("SELECT COUNT(*) FROM cloud_events")).scalar()
        print(f"Verified after SIGKILL: {stored_cnt}/3 events stored in database.")

    # Restart the server
    print("Restarting server on port 8101...")
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env
    )
    assert wait_for_server(port), "Server failed to restart"

    # Wait for correlator consumer to process the unassigned events
    time.sleep(2.0)

    with engine.connect() as conn:
        unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()
        outbox_count = conn.execute(text("SELECT COUNT(*) FROM outbox_events")).scalar()

    proc2.kill()
    proc2.wait()

    print(f"Results after restart:")
    print(f"  Unassigned events: {unassigned} (Expected 0)")
    print(f"  Incidents created: {inc_count} (Expected exactly 1)")
    print(f"  Outbox rows:       {outbox_count} (Expected 2: 1 S3 + 1 SNS)")

    assert unassigned == 0, f"Expected 0 unassigned events, got {unassigned}"
    assert inc_count == 1, f"Expected exactly 1 incident, got {inc_count}"
    assert outbox_count == 2, f"Expected 2 outbox rows (S3+SNS), got {outbox_count}"
    print("[PASS] SIGKILL durability proof succeeded!")


def run_concurrency_proof(port: int = 8102):
    print("\n" + "=" * 80)
    print("STEP 3 PROOF 2: CONCURRENCY TEST (2 SERVER WORKERS, 100 PARALLEL CASCADES)")
    print("=" * 80)
    reset_db()

    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_URL
    env["PORT"] = str(port)
    env["CLOUDLENS_MOCK_AWS"] = "true"
    env["WEB_CONCURRENCY"] = "2"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--workers", "2", "--log-level", "warning"],
        env=env
    )
    assert wait_for_server(port, max_retries=60), "2-worker server failed to start"

    # Generate 100 distinct cascades, each with 2 related events
    all_events = []
    base_ts = datetime(2026, 10, 11, 15, 0, 0, tzinfo=timezone.utc)
    for c_idx in range(100):
        c_time = base_ts + timedelta(minutes=c_idx * 10) # 10 minutes apart so cascades don't cross
        ev1 = {
            "event_id": f"conc_c{c_idx}_e1_{uuid.uuid4().hex[:6]}",
            "timestamp": c_time.isoformat(),
            "source": "application",
            "event_type": "CpuSpike",
            "service": "EC2",
            "resource_id": f"i-conc-{c_idx}",
            "severity": "HIGH"
        }
        ev2 = {
            "event_id": f"conc_c{c_idx}_e2_{uuid.uuid4().hex[:6]}",
            "timestamp": (c_time + timedelta(seconds=15)).isoformat(),
            "source": "application",
            "event_type": "Http500",
            "service": "API",
            "resource_id": f"api-conc-{c_idx}",
            "severity": "CRITICAL"
        }
        all_events.extend([ev1, ev2])

    print(f"Firing {len(all_events)} events across 100 cascades simultaneously with 30 threads...")
    def _send(ev):
        return requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=10.0).status_code

    with ThreadPoolExecutor(max_workers=30) as executor:
        results = list(executor.map(_send, all_events))

    assert all(code in (200, 201) for code in results), "Some requests failed"

    # Wait for correlator consumer to drain
    time.sleep(3.0)

    with engine.connect() as conn:
        total_ev = conn.execute(text("SELECT COUNT(*) FROM cloud_events")).scalar()
        unassigned = conn.execute(text("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL")).scalar()
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()

    print(f"100 Cascades Results:")
    print(f"  Total events ingested: {total_ev}")
    print(f"  Unassigned events:     {unassigned}")
    print(f"  Total incidents:       {inc_count} (Expected 100)")

    assert inc_count == 100, f"Expected exactly 100 incidents for 100 cascades, got {inc_count}"

    proc.kill()
    proc.wait()

    # Now fire 4 scenarios concurrently
    print("\nFiring 4 synthetic scenarios concurrently against 2 workers...")
    reset_db()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--workers", "2", "--log-level", "warning"],
        env=env
    )
    assert wait_for_server(port, max_retries=60), "2-worker server failed to restart"

    from event_processor.normalizer import load_synthetic_events
    sc_events = []
    for sc in ["cascade", "db_exhaustion", "auth_outage", "memory_leak"]:
        fpath = PROJECT_ROOT / "data" / "synthetic_events" / f"{sc}.json"
        for ev in load_synthetic_events(fpath):
            sc_events.append(ev.model_dump(mode="json"))

    import random
    random.seed(42)
    random.shuffle(sc_events)

    with ThreadPoolExecutor(max_workers=20) as executor:
        sc_res = list(executor.map(_send, sc_events))

    time.sleep(3.0)
    with engine.connect() as conn:
        sc_inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents")).scalar()

    print(f"  4 Concurrent scenarios produced: {sc_inc_count} incidents (Expected 4)")
    assert sc_inc_count == 4, f"Expected exactly 4 incidents for 4 scenarios, got {sc_inc_count}"

    proc.kill()
    proc.wait()
    print("[PASS] Concurrency proof succeeded!")


def run_load_benchmarks():
    print("\n" + "=" * 80)
    print("STEP 3 PROOF 3: LOAD BENCHMARKS (1 WORKER VS 2 WORKERS)")
    print("=" * 80)

    results_table = []

    for num_workers in [1, 2]:
        port = 8100 + num_workers
        print(f"\n--- Testing with {num_workers} Worker(s) on port {port} ---")
        reset_db()

        env = os.environ.copy()
        env["DATABASE_URL"] = POSTGRES_URL
        env["PORT"] = str(port)
        env["CLOUDLENS_MOCK_AWS"] = "true"
        env["WEB_CONCURRENCY"] = str(num_workers)

        cmd = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"]
        if num_workers > 1:
            cmd.extend(["--workers", str(num_workers)])

        proc = subprocess.Popen(cmd, env=env)
        assert wait_for_server(port, max_retries=60), f"Server failed to start with {num_workers} workers"

        # 1. Closed-loop saturation test (50 clients, 1000 requests)
        print("  Running closed-loop saturation test (50 clients, 1,000 requests)...")
        sat_events = [
            {
                "event_id": f"sat_w{num_workers}_{i}_{uuid.uuid4().hex[:6]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "application",
                "event_type": "SaturationLoad",
                "service": "API",
                "resource_id": f"api-{i % 10}",
                "severity": "MEDIUM"
            }
            for i in range(1000)
        ]

        sat_latencies = []
        sat_errors = 0
        t_sat_start = time.perf_counter()

        def _send_sat(ev):
            nonlocal sat_errors
            t0 = time.perf_counter()
            try:
                r = requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=5.0)
                dur = (time.perf_counter() - t0) * 1000.0
                if r.status_code in (200, 201):
                    return dur, False
                return dur, True
            except Exception:
                return (time.perf_counter() - t0) * 1000.0, True

        with ThreadPoolExecutor(max_workers=50) as executor:
            for dur, err in executor.map(_send_sat, sat_events):
                sat_latencies.append(dur)
                if err:
                    sat_errors += 1

        sat_wall = time.perf_counter() - t_sat_start
        sat_tps = len(sat_events) / sat_wall
        sat_p50 = np.percentile(sat_latencies, 50)
        sat_p95 = np.percentile(sat_latencies, 95)
        sat_p99 = np.percentile(sat_latencies, 99)
        sat_err_pct = (sat_errors / len(sat_events)) * 100

        print(f"    Saturation: {sat_tps:.1f} req/s | p50={sat_p50:.1f}ms | p95={sat_p95:.1f}ms | p99={sat_p99:.1f}ms | errors={sat_err_pct:.1f}%")
        results_table.append({
            "workers": num_workers, "mode": "Saturation (50 clients)", "rate": f"{sat_tps:.1f} req/s",
            "p50": sat_p50, "p95": sat_p95, "p99": sat_p99, "errors": sat_err_pct
        })

        # 2. Fixed-rate tests at 50, 100, 200 req/s (10 seconds each)
        for target_rps in [50, 100, 200]:
            print(f"  Running fixed-rate test at {target_rps} req/s (10s duration = {target_rps * 10} requests)...")
            total_reqs = target_rps * 10
            interval = 1.0 / target_rps
            fx_latencies = []
            fx_errors = 0

            fx_events = [
                {
                    "event_id": f"fx_w{num_workers}_r{target_rps}_{i}_{uuid.uuid4().hex[:6]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "application",
                    "event_type": "FixedRateLoad",
                    "service": "Worker",
                    "resource_id": f"worker-{i % 5}",
                    "severity": "HIGH"
                }
                for i in range(total_reqs)
            ]

            pool = ThreadPoolExecutor(max_workers=50)
            t_test_start = time.perf_counter()
            futures = []

            for i, ev in enumerate(fx_events):
                expected_time = t_test_start + (i * interval)
                now = time.perf_counter()
                if expected_time > now:
                    time.sleep(expected_time - now)
                futures.append(pool.submit(_send_sat, ev))

            for f in futures:
                dur, err = f.result()
                fx_latencies.append(dur)
                if err:
                    fx_errors += 1
            pool.shutdown(wait=False)

            actual_wall = time.perf_counter() - t_test_start
            actual_rps = total_reqs / actual_wall
            fx_p50 = np.percentile(fx_latencies, 50)
            fx_p95 = np.percentile(fx_latencies, 95)
            fx_p99 = np.percentile(fx_latencies, 99)
            fx_err_pct = (fx_errors / total_reqs) * 100

            print(f"    Fixed {target_rps} req/s: actual={actual_rps:.1f} req/s | p50={fx_p50:.1f}ms | p95={fx_p95:.1f}ms | p99={fx_p99:.1f}ms | errors={fx_err_pct:.1f}%")
            results_table.append({
                "workers": num_workers, "mode": f"Fixed-Rate {target_rps} req/s", "rate": f"{actual_rps:.1f} req/s",
                "p50": fx_p50, "p95": fx_p95, "p99": fx_p99, "errors": fx_err_pct
            })

        proc.kill()
        proc.wait()

    return results_table


if __name__ == "__main__":
    run_sigkill_proof(8101)
    run_concurrency_proof(8102)
    tbl = run_load_benchmarks()
    print("\n" + "=" * 80)
    print(f"{'Workers':<8} | {'Mode':<24} | {'Achieved Rate':<15} | {'p50 (ms)':<10} | {'p95 (ms)':<10} | {'p99 (ms)':<10} | {'Error %':<8}")
    print("-" * 80)
    for row in tbl:
        print(f"{row['workers']:<8} | {row['mode']:<24} | {row['rate']:<15} | {row['p50']:<10.1f} | {row['p95']:<10.1f} | {row['p99']:<10.1f} | {row['errors']:<8.1f}")
