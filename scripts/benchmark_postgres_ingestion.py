#!/usr/bin/env python3
"""
PostgreSQL Ingestion Benchmark Suite
Benchmarks single-event and batch ingestion under 50 and 100 concurrent clients.
Compares pg8000 vs psycopg (v3).
Reports raw PER-REQUEST latency (p50, p95, p99), throughput (req/s and evt/s), and error rate.
"""

import os
import sys
import time
import uuid
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests
import numpy as np


def benchmark_driver(driver_name: str, db_url_scheme: str, port: int = 8099):
    print("\n" + "=" * 80)
    print(f"BENCHMARKING DRIVER: {driver_name.upper()} (on port {port})")
    print("=" * 80)

    env = os.environ.copy()
    env["DATABASE_URL"] = f"{db_url_scheme}://postgres:postgres@localhost:5432/cloudlens_test"
    env["DB_DRIVER"] = driver_name
    env["PORT"] = str(port)
    env["CLOUDLENS_MOCK_AWS"] = "true"

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port), "--host", "127.0.0.1", "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    ready = False
    for _ in range(40):
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.2)

    if not ready:
        print(f"ERROR: Server failed to start on port {port}")
        server_proc.kill()
        return []

    print(f"Server ready with driver {driver_name}. Beginning load bursts...")

    def _benchmark_single_burst(num_clients: int, total_events: int, tag: str):
        events = [
            {
                "event_id": f"bench_{driver_name}_{tag}_{i}_{uuid.uuid4().hex[:6]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "application",
                "event_type": "LatencyAnomaly",
                "service": "API",
                "resource_id": f"api-{i % 5}",
                "metric": "latency_ms",
                "value": 120.0 + (i % 50),
                "severity": "HIGH"
            }
            for i in range(total_events)
        ]

        latencies = []
        errors = 0
        start_wall = time.perf_counter()

        def _worker(ev):
            nonlocal errors
            t0 = time.perf_counter()
            try:
                res = requests.post(f"http://127.0.0.1:{port}/api/events", json=ev, timeout=10.0)
                dur_ms = (time.perf_counter() - t0) * 1000.0
                if res.status_code in (200, 201):
                    return dur_ms, False
                else:
                    return dur_ms, True
            except Exception:
                return (time.perf_counter() - t0) * 1000.0, True

        with ThreadPoolExecutor(max_workers=num_clients) as executor:
            futures = [executor.submit(_worker, ev) for ev in events]
            for f in as_completed(futures):
                dur, err = f.result()
                latencies.append(dur)
                if err:
                    errors += 1

        total_wall_sec = time.perf_counter() - start_wall
        throughput = total_events / total_wall_sec if total_wall_sec > 0 else 0
        p50 = np.percentile(latencies, 50)
        p95 = np.percentile(latencies, 95)
        p99 = np.percentile(latencies, 99)
        err_rate = (errors / total_events) * 100.0

        return {
            "driver": driver_name,
            "mode": f"Single ({tag})",
            "clients": num_clients,
            "requests": total_events,
            "duration_s": round(total_wall_sec, 2),
            "throughput_req_s": round(throughput, 1),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "error_rate_pct": round(err_rate, 2)
        }

    def _benchmark_batch_burst(num_clients: int, total_events: int, batch_size: int, tag: str):
        num_batches = total_events // batch_size
        batches = [
            [
                {
                    "event_id": f"bench_{driver_name}_bat_{tag}_{b}_{i}_{uuid.uuid4().hex[:6]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "application",
                    "event_type": "MetricSpike",
                    "service": "API",
                    "resource_id": f"api-{i % 5}",
                    "metric": "cpu_utilization",
                    "value": 85.0,
                    "severity": "HIGH"
                }
                for i in range(batch_size)
            ]
            for b in range(num_batches)
        ]

        latencies = []
        errors = 0
        start_wall = time.perf_counter()

        def _batch_worker(batch):
            nonlocal errors
            t0 = time.perf_counter()
            try:
                res = requests.post(f"http://127.0.0.1:{port}/api/events/batch", json=batch, timeout=15.0)
                dur_ms = (time.perf_counter() - t0) * 1000.0
                if res.status_code in (200, 201):
                    return dur_ms, False
                else:
                    return dur_ms, True
            except Exception:
                return (time.perf_counter() - t0) * 1000.0, True

        with ThreadPoolExecutor(max_workers=num_clients) as executor:
            futures = [executor.submit(_batch_worker, batch) for batch in batches]
            for f in as_completed(futures):
                dur, err = f.result()
                latencies.append(dur)
                if err:
                    errors += 1

        total_wall_sec = time.perf_counter() - start_wall
        throughput_req = num_batches / total_wall_sec if total_wall_sec > 0 else 0
        p50 = np.percentile(latencies, 50)
        p95 = np.percentile(latencies, 95)
        p99 = np.percentile(latencies, 99)
        err_rate = (errors / num_batches) * 100.0

        return {
            "driver": driver_name,
            "mode": f"Batch ({tag}, N={batch_size})",
            "clients": num_clients,
            "requests": num_batches,
            "duration_s": round(total_wall_sec, 2),
            "throughput_req_s": round(throughput_req, 1),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "error_rate_pct": round(err_rate, 2)
        }

    results = []
    # 50 Clients
    print("Running Single Ingest (50 clients) COLD...")
    results.append(_benchmark_single_burst(50, 1000, "COLD"))
    print("Running Single Ingest (50 clients) WARM...")
    results.append(_benchmark_single_burst(50, 1000, "WARM"))

    print("Running Batch Ingest (50 clients, 1000 events, batch_size=20) COLD...")
    results.append(_benchmark_batch_burst(50, 1000, 20, "COLD"))
    print("Running Batch Ingest (50 clients, 1000 events, batch_size=20) WARM...")
    results.append(_benchmark_batch_burst(50, 1000, 20, "WARM"))

    # 100 Clients
    print("Running Single Ingest (100 clients) WARM...")
    results.append(_benchmark_single_burst(100, 1000, "100c"))
    print("Running Batch Ingest (100 clients, batch_size=20) WARM...")
    results.append(_benchmark_batch_burst(100, 1000, 20, "100c"))

    # Cleanup
    server_proc.terminate()
    try:
        server_proc.wait(timeout=3.0)
    except Exception:
        server_proc.kill()

    return results


def main():
    # 1. Benchmark pg8000
    res_pg8000 = benchmark_driver("pg8000", "postgresql+pg8000", port=8096)

    # 2. Benchmark psycopg (v3)
    res_psycopg = benchmark_driver("psycopg", "postgresql+psycopg", port=8097)

    all_res = res_pg8000 + res_psycopg

    print("\n" + "=" * 105)
    print("INGESTION BENCHMARK RESULTS (RAW PER-REQUEST LATENCIES ON LOCAL POSTGRESQL)")
    print("=" * 105)
    fmt = "{:<8} | {:<25} | {:<7} | {:<10} | {:<10} | {:<10} | {:<12} | {:<8}"
    print(fmt.format("Driver", "Test Run", "Clients", "p50 (ms)", "p95 (ms)", "p99 (ms)", "Throughput", "Error %"))
    print("-" * 105)
    for r in all_res:
        print(fmt.format(
            r["driver"],
            r["mode"],
            r["clients"],
            f"{r['p50_ms']} ms",
            f"{r['p95_ms']} ms",
            f"{r['p99_ms']} ms",
            f"{r['throughput_req_s']} req/s",
            f"{r['error_rate_pct']}%"
        ))
    print("=" * 105)


if __name__ == "__main__":
    main()
