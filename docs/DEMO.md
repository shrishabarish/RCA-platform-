# 🖥️ CloudLens Cockpit & Demonstration Guide

CloudLens is an autonomous, explainable incident correlation and root cause analysis (RCA) platform. This guide explains how to run and present the frontend cockpit in demo mode with zero database or cloud dependencies.

---

## 🌟 Demo Features Overview

1. **Topology & Diagnostics Cockpit (`#tab-overview`)**:
   - Dynamic topological DAG rendering scaling smoothly from 6-node to 12-node enterprise graphs.
   - Live KPI bar tracking event rates, correlation windows, and hypothesis ranking scores.
   - Explainable diagnosis cards breaking down the primary root cause, verdict confidence (`CONFIRMED`, `PROBABLE`, `INSUFFICIENT_EVIDENCE`), and ensemble candidate ranking scores.
   - Verbatim Backend Evidence State Legend matching `rca_v3.models.EvidenceState`:
     - `PRESENT` (Active anomaly)
     - `STALE` (Delayed heartbeat)
     - `MISSING` (Expected signal absent)
     - `QUIET` (Verified nominal)
     - `UNOBSERVED` (Telemetry source down or uncollected)
   - Baseline Engine Comparison box (`#baselineEngineBox`) demonstrating comparison against the legacy single-pass analyzer using the **identical cleaned telemetry stream**.
   - Suggested (not executed) Remediation Card with server-side safety guards and unsafe command detection (`FLUSHDB`, `set-alarm-state`).

2. **Incident Timeline Replay (`#tab-timeline`)**:
   - Chronological event progression with severity indicators (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).
   - Signal state tracking across distributed microservices.

3. **Fault Injection Lab (`#tab-fault-lab`)**:
   - Interactive scenario cards (`EC2 Cascade`, `DynamoDB Exhaustion`, `Auth Outage`, `Memory Leak`).
   - Custom anomaly injection modal to trigger arbitrary telemetry anomalies in real time.

4. **Evaluation & Benchmarks (`#tab-evaluation`)**:
   - Verified benchmark runs on committed frozen splits (`stage2_dev.csv`, `stage2_handwritten.csv`, `stage3c_dev3_20261012_095646.csv`, `stage3e_dev3_fresh_v3.csv`).
   - Displays exact recomputed case counts, Top-1 accuracy, confident-wrong rate, and git commit hashes.

---

## 🚀 Running the Cockpit Locally (Zero-Database Demo Mode)

No PostgreSQL, Redis, or AWS access is required. The platform operates safely in mock demo mode:

```bash
# 1. Start the FastAPI backend with in-memory SQLite demo store
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 2. Open the cockpit in your browser
# Directly via http://127.0.0.1:8000 or opening frontend/index.html
```

---

## 🛡️ Remediation Safety Guards & Approval Workflow

Automated remediation is safely guarded server-side and **fails closed (HTTP 403 Forbidden)** unless all 5 conditions are met:
1. `AUTO_REMEDIATION_ENABLED` defaults to `False` via configuration.
2. Human approval token validated using constant-time comparison (`secrets.compare_digest`) against configured secret.
3. Telemetry source health verified nominal (`HEALTHY`).
4. RCA v3 diagnosis verdict is `CONFIRMED`.
5. Root cause has no unobserved ancestors in the causal dependency graph.
