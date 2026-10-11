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

> [!IMPORTANT]
> **Dependency on Step 1 Item 1**: As verified via live read-only inspection, production RDS already contains revision `0002_add_columns_and_indexes` in `alembic_version` with all additive columns and indexes present.
> Running `alembic upgrade head` in this environment is a safe zero-operation verification (`No new upgrade operations detected`).
> If deploying against a newly provisioned RDS clone or historical snapshot created prior to revision 0002:
> 1. Stamp baseline: `alembic stamp 0001_baseline_prod_schema`
> 2. Upgrade: `alembic upgrade head`

```bash
alembic check
alembic upgrade head
```

---

### Step 5: Deploy Application Services (2 Workers, Embedded Mode)

Deploy FastAPI web application in embedded mode (`CORRELATOR_EMBEDDED=true`) with conservative connection pool sizing:
```bash
export CORRELATOR_EMBEDDED=true
export DEPLOY_CUTOFF_TIMESTAMP="<CURRENT_UTC_ISO8601_TIMESTAMP>"
export WORKERS=2
export DB_POOL_SIZE=12
export DB_MAX_OVERFLOW=8
# Total connections: 2 workers * (12 + 8) = 40 conn + 3 correlator conn = 43 conn <= 47 (under 60% of RDS max_connections 79)
uvicorn backend.app.main:app --workers 2 --host 0.0.0.0 --port 8000
```

---

### Step 6: Post-Deployment Verification & CloudWatch Alarms

1. **Re-Enable EventBridge Rule**:
   ```bash
   aws events enable-rule --name cloudlens-synthetic-alarm-generator
   ```

2. **Readiness Probe**:
   ```bash
   curl -f http://<PROD_HOST>:8000/api/ready
   ```
   Must return HTTP 200 with `"status": "ready"`. Returns HTTP 503 `"status": "degraded"` if oldest unassigned event age > 30s or leader heartbeat age > 10s.

3. **CloudWatch Alarm Configuration (Documentation Only)**:
   - **Alarm Name**: `CloudLens-Correlator-Backlog-Age`
   - **Metric**: `Custom/CloudLens -> CorrelatorOldestUnassignedAgeSeconds`
   - **Statistic**: `Maximum`
   - **Threshold**: `> 30 seconds for 2 consecutive 1-minute periods`
   - **Action**: Alert PagerDuty / SRE SNS topic (`arn:aws:sns:eu-north-1:123456789012:cloudlens-sre-oncall`)
   - **Remediation**: Check ECS task logs for deadlocks, restart stuck leader container, or verify RDS IOPS capacity.

3. **CloudWatch Alarm Configuration**:
   Create a CloudWatch Metric Alarm monitoring backlog health:
   - **Metric**: `OldestUnassignedEventAgeSeconds`
   - **Namespace**: `CloudLens/Pipeline`
   - **Threshold**: `> 60 seconds` for 2 consecutive datapoints.
   - **Action**: Alert On-Call PagerDuty / SRE SNS topic.

4. **Verify Zero Backfill**:
   Confirm all 106,586 legacy events remain unassigned (`incident_id IS NULL`) and legacy incidents remain clean.
