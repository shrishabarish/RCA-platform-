import asyncio
import time
import json
from pathlib import Path
import statistics
import numpy as np
import httpx
from datetime import datetime, timezone, timedelta
import subprocess
import sys
import os

SERVER_URL = "http://127.0.0.1:8000"
TOTAL_BURST_EVENTS = 1000
CONCURRENCY = 50


async def run_load_test():
    # Start uvicorn server in subprocess with isolated test environment
    print("Starting uvicorn server with isolated test database...")
    test_db = Path(__file__).resolve().parent / "test_load.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{test_db}"
    env["AWS_ACCESS_KEY_ID"] = "mock-testing-key"
    env["AWS_SECRET_ACCESS_KEY"] = "mock-testing-secret"
    env["APP_ENV"] = "testing"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    try:
        # Wait for server to come up
        async with httpx.AsyncClient(timeout=10.0) as client:
            ready = False
            for _ in range(30):
                try:
                    res = await client.get(f"{SERVER_URL}/api/health")
                    if res.status_code == 200:
                        ready = True
                        break
                except Exception:
                    await asyncio.sleep(0.5)

            if not ready:
                print("Failed to start server!")
                return

            print("Server is UP and ready.")

            # 1. Cold Start measurement (first RCA request)
            t0 = time.perf_counter()
            cold_res = await client.post(f"{SERVER_URL}/api/scenarios/cascade/simulate")
            cold_start_time_ms = (time.perf_counter() - t0) * 1000.0
            cold_status = cold_res.status_code
            print(f"Cold Start RCA Request: {cold_start_time_ms:.2f} ms (Status: {cold_status})")

            # 2. Warm Run: 1000-event burst with 50 concurrent clients
            print(f"Launching burst of {TOTAL_BURST_EVENTS} events across {CONCURRENCY} concurrent clients...")
            base_time = datetime.now(timezone.utc)
            events = [
                {
                    "event_id": f"load_evt_{i}_{int(time.time()*1000)}",
                    "timestamp": (base_time + timedelta(milliseconds=i * 5)).isoformat(),
                    "source": "application",
                    "event_type": "high_latency_spike",
                    "service": "API" if i % 2 == 0 else "DynamoDB",
                    "resource_id": f"res-{i % 10}",
                    "metric": "Latency",
                    "value": 150.0 + (i % 50),
                    "severity": "HIGH",
                    "metadata": {"load_test": True}
                }
                for i in range(TOTAL_BURST_EVENTS)
            ]

            sem = asyncio.Semaphore(CONCURRENCY)
            latencies = []
            errors = 0

            async def send_event(ev):
                nonlocal errors
                async with sem:
                    t_start = time.perf_counter()
                    try:
                        resp = await client.post(f"{SERVER_URL}/api/events", json=ev, timeout=5.0)
                        duration_ms = (time.perf_counter() - t_start) * 1000.0
                        if resp.status_code in (200, 201):
                            latencies.append(duration_ms)
                        else:
                            errors += 1
                    except Exception:
                        errors += 1

            t_burst_start = time.perf_counter()
            await asyncio.gather(*[send_event(ev) for ev in events])
            t_burst_total = (time.perf_counter() - t_burst_start) * 1000.0

            # 3. Warm RCA Retrieval Latency over 50 requests
            warm_rca_latencies = []
            warm_errors = 0
            for _ in range(50):
                t_rca = time.perf_counter()
                try:
                    rca_resp = await client.get(f"{SERVER_URL}/api/incidents", timeout=5.0)
                    rca_dur = (time.perf_counter() - t_rca) * 1000.0
                    if rca_resp.status_code == 200:
                        warm_rca_latencies.append(rca_dur)
                    else:
                        warm_errors += 1
                except Exception:
                    warm_errors += 1

            # Stats calculation
            event_p50 = np.percentile(latencies, 50) if latencies else 0
            event_p95 = np.percentile(latencies, 95) if latencies else 0
            event_p99 = np.percentile(latencies, 99) if latencies else 0
            event_err_rate = (errors / TOTAL_BURST_EVENTS) * 100.0

            rca_p50 = np.percentile(warm_rca_latencies, 50) if warm_rca_latencies else 0
            rca_p95 = np.percentile(warm_rca_latencies, 95) if warm_rca_latencies else 0
            rca_p99 = np.percentile(warm_rca_latencies, 99) if warm_rca_latencies else 0
            rca_err_rate = (warm_errors / 50.0) * 100.0

            output = {
                "cold_start_ms": round(cold_start_time_ms, 2),
                "cold_start_status": cold_status,
                "burst_total_time_ms": round(t_burst_total, 2),
                "throughput_events_per_sec": round((TOTAL_BURST_EVENTS / (t_burst_total / 1000.0)), 2),
                "event_ingestion": {
                    "count": TOTAL_BURST_EVENTS,
                    "concurrency": CONCURRENCY,
                    "p50_ms": round(float(event_p50), 2),
                    "p95_ms": round(float(event_p95), 2),
                    "p99_ms": round(float(event_p99), 2),
                    "error_rate_pct": round(event_err_rate, 2)
                },
                "rca_endpoint_warm": {
                    "requests": 50,
                    "p50_ms": round(float(rca_p50), 2),
                    "p95_ms": round(float(rca_p95), 2),
                    "p99_ms": round(float(rca_p99), 2),
                    "error_rate_pct": round(rca_err_rate, 2)
                }
            }
            print("\nLOAD TEST RESULTS:")
            print(json.dumps(output, indent=2))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(run_load_test())
