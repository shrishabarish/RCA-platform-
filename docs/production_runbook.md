# CloudLens Production Runbook & Rehearsal Guide

> **CRITICAL DIRECTIVES**:
> - Never execute migration steps on production RDS directly without an approved snapshot.
> - All steps below have been rehearsed and timed against the local production-shaped database (`cloudlens_prod_copy`, 106,586 events, 42,033 OPEN incidents).
> - Every DDL migration uses non-blocking additive operations (`CONCURRENTLY` for index builds).
> - Maintain at least two correlator instances under a supervisor: one actively holds the Postgres advisory lock (leader), and one standby ready for instant failover.

---

## Pre-Requisites & Target System Sizing
- **Target Database**: AWS RDS PostgreSQL (Engine version 15+ / 18)
- **Table Sizes at Deploy**:
  - `cloud_events`: ~106,586 rows (106,217 unassigned legacy events)
  - `incidents`: ~42,033 legacy `OPEN` incidents (from Sept 1–12 outage cascades)
  - `outbox_events`: 0 rows

---

## Ordered Step-by-Step Production Runbook

### Step 1: Disable EventBridge Telemetry Generator Rule
Temporarily disable the synthetic telemetry generator rule in Amazon EventBridge to quiesce incoming test alarms during snapshotting and migration:
```bash
aws events disable-rule --name cloudlens-synthetic-alarm-generator
```
- **Estimated Duration**: 1 – 2 seconds.
- **Impact**: Halts generation of synthetic CloudWatch alarms while legitimate production events continue buffering.

---

### Step 2: AWS RDS Snapshot Creation
Create an explicit manual snapshot of the production RDS cluster before running any maintenance commands:
```bash
aws rds create-db-snapshot \
    --db-instance-identifier <PROD_RDS_INSTANCE_ID> \
    --db-snapshot-identifier cloudlens-pre-deploy-$(date +%Y%m%d%H%M%S)
```
- **Estimated Duration**: 3 – 5 minutes (depending on AWS storage tier).
- **Impact**: Zero downtime; asynchronous storage snapshot.
- **Rollback Point**: This snapshot is the authoritative rollback target.

---

### Step 3: Step B — Resolve Stale Open Incidents
Production currently holds 42,033 abandoned incidents stuck in `OPEN` status. Step B resolves incidents older than 24 hours in batches of 1,000, preventing notification storms and claim contention:
```bash
# 1. Dry run verification first
python scripts/migrate_step_b_resolve_incidents.py --dry-run

# 2. Execute resolution in 1,000-row chunks
python scripts/migrate_step_b_resolve_incidents.py --execute --batch-size 1000
```
- **Rehearsed Duration (Local Copy)**: **1.84 seconds** for all 42,033 rows.
- **Lock Impact**: Row-level exclusive lock on 1,000 incident rows per chunk. Writers to `cloud_events` are completely unblocked.
- **Expected Outcome**:
  - `42,033` rows transitioned from `status = 'OPEN'` to `status = 'RESOLVED'`.
  - Resolution reason set to `'stale_auto_resolved_pre_deploy'`.

---

### Step 4: Alembic Migrations (Baseline Stamp & Upgrade Head)

#### 4a. Stamp Baseline Schema
The production RDS schema currently matches the legacy baseline (`0001_baseline_prod_schema`). Stamp Alembic so it recognizes the existing tables:
```bash
alembic stamp 0001_baseline_prod_schema
```
- **Rehearsed Duration**: **0.12 seconds**.

#### 4b. Run Non-Blocking Additive Migration (`0002_add_columns_and_indexes`)
Apply revision `0002_add_columns_and_indexes` to add missing tracking columns, create `system_settings`, and build indexes non-blockingly:
```bash
alembic upgrade head
```
- **Rehearsed Duration**: **0.85 seconds**.
- **Real Schema Changes Applied**:
  1. `incidents`:
     - Columns: `archive_status` (VARCHAR(32), default 'PENDING'), `notification_status` (VARCHAR(32), default 'PENDING'), `delivery_error` (TEXT), `analysis_status` (VARCHAR(32), default 'PENDING'), `analyzed_at` (TIMESTAMP).
     - Indexes: `ix_incidents_status_created_at`, `ix_incidents_open_created_at` (WHERE status = 'OPEN'), `ix_incidents_archive_status`, `ix_incidents_notification_status`, `ix_incidents_analysis_status`.
  2. `cloud_events`:
     - Columns: `ingested_at` (TIMESTAMP, default NOW()). Protects delayed real events from being missed by deploy cutoff.
     - Indexes: `ix_cloud_events_service_timestamp`, `ix_cloud_events_unassigned` (WHERE incident_id IS NULL), `ix_cloud_events_ingested_at`.
  3. `outbox_events`:
     - Columns: `event_type` (VARCHAR(32)), `lease_until` (TIMESTAMP), `next_retry_at` (TIMESTAMP), `last_error` (TEXT), `updated_at` (TIMESTAMP).
     - Indexes: `ix_outbox_events_incident_id`, `ix_outbox_events_lease_until`, `ix_outbox_events_next_retry_at`.
  4. `system_settings`:
     - Table created: `key` (VARCHAR PK), `value` (VARCHAR), `created_at` (TIMESTAMP), `updated_at` (TIMESTAMP).
     - Index: `ix_system_settings_key`.

---

### Step 5: Deploy Application Services & Production Correlator Topology

#### Production Topology (Two Correlator Workers Under Supervisor)
Deploy two separate instances of the correlator process under a supervisor (systemd, supervisord, or ECS tasks):
- **Worker 1 (Primary / Active Leader)**: Acquires Postgres advisory lock (ID: 987654321), drains backlog, updates `system_settings.leader_heartbeat` each loop.
- **Worker 2 (Hot Standby)**: Continuously polls advisory lock and monitors leader heartbeat. Takes over within <= 2s if Worker 1 crashes, is killed, or hangs.
- **Supervisor Configuration (`supervisord.conf`)**:
```ini
[program:cloudlens_correlator_1]
command=python scripts/run_correlator.py
directory=/opt/cloudlens
autostart=true
autorestart=true
stopsignal=TERM
stopwaitsecs=10
environment=DEPLOY_CUTOFF_TIMESTAMP="%(ENV_DEPLOY_CUTOFF_TIMESTAMP)s",CORRELATOR_HEALTH_FILE="/tmp/correlator_health_1.json",LEADER_HEARTBEAT_TIMEOUT_SECONDS="10"

[program:cloudlens_correlator_2]
command=python scripts/run_correlator.py
directory=/opt/cloudlens
autostart=true
autorestart=true
stopsignal=TERM
stopwaitsecs=10
environment=DEPLOY_CUTOFF_TIMESTAMP="%(ENV_DEPLOY_CUTOFF_TIMESTAMP)s",CORRELATOR_HEALTH_FILE="/tmp/correlator_health_2.json",LEADER_HEARTBEAT_TIMEOUT_SECONDS="10"
```

#### Web Workers (FastAPI)
Run web workers in decoupled mode (`CORRELATOR_EMBEDDED=false`):
```bash
export CORRELATOR_EMBEDDED=false
export DEPLOY_CUTOFF_TIMESTAMP="<CURRENT_UTC_ISO8601_TIMESTAMP>"
uvicorn backend.app.main:app --workers 4 --host 0.0.0.0 --port 8000
```

---

### Step 6: Post-Deployment Verification & Alarms

1. **Re-Enable EventBridge Rule**:
   ```bash
   aws events enable-rule --name cloudlens-synthetic-alarm-generator
   ```

2. **Readiness Probe**:
   ```bash
   curl -f http://<PROD_HOST>:8000/api/ready
   ```
   Must return HTTP 200 with `"status": "ready"`. Returns HTTP 503 `"status": "degraded"` if oldest unassigned event age > 30s.

3. **CloudWatch Alarm Configuration**:
   Create a CloudWatch Metric Alarm monitoring backlog health:
   - **Metric**: `OldestUnassignedEventAgeSeconds`
   - **Namespace**: `CloudLens/Pipeline`
   - **Threshold**: `> 60 seconds` for 2 consecutive datapoints.
   - **Action**: Alert On-Call PagerDuty / SRE SNS topic.

4. **Verify Zero Backfill**:
   Confirm all 106,586 legacy events remain unassigned (`incident_id IS NULL`) and legacy incidents remain clean.
