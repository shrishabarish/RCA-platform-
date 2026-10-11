-- =============================================================================
-- CLOUDLENS MIGRATION: ADDITIVE COLUMNS AND INDEXES (STEP A)
-- TARGET: PostgreSQL (Production RDS / Local PostgreSQL)
-- SAFE / NON-BLOCKING: Safe to apply on live databases with IF NOT EXISTS
-- =============================================================================

BEGIN;

-- 1. Additive columns on 'incidents' table
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS archive_status VARCHAR(32) DEFAULT 'PENDING';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS notification_status VARCHAR(32) DEFAULT 'PENDING';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS delivery_error TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(32) DEFAULT 'PENDING';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP;

-- 2. Additive indexes on 'incidents' table
CREATE INDEX IF NOT EXISTS ix_incidents_status_created_at ON incidents (status, created_at);
CREATE INDEX IF NOT EXISTS ix_incidents_archive_status ON incidents (archive_status);
CREATE INDEX IF NOT EXISTS ix_incidents_notification_status ON incidents (notification_status);
CREATE INDEX IF NOT EXISTS ix_incidents_analysis_status ON incidents (analysis_status);

-- 3. Composite index on 'cloud_events' for high-throughput query filtering
CREATE INDEX IF NOT EXISTS ix_cloud_events_service_timestamp ON cloud_events (service, timestamp);

-- 4. Additive columns and indexes on 'outbox_events' for multi-worker claiming
ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS lease_until TIMESTAMP;
ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS ix_outbox_events_lease_until ON outbox_events (lease_until);
CREATE INDEX IF NOT EXISTS ix_outbox_events_next_retry_at ON outbox_events (next_retry_at);

COMMIT;
