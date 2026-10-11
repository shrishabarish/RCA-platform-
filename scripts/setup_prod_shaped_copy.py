#!/usr/bin/env python3
"""
Sets up a local PostgreSQL database (cloudlens_prod_copy) replicating the exact production shape:
- Old baseline production schema (from 0001_baseline_prod_schema).
- 42,033 OPEN incidents with timestamps between Sept 1 and Sept 12.
- 106,586 cloud_events with incident_id IS NULL.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.core.safety import check_database_safety, print_database_host

DB_URL = "postgresql://cloudlens_user:cloudlens_test_pass@localhost:5432/cloudlens_prod_copy"

BASELINE_DDL = """
DROP TABLE IF EXISTS outbox_events CASCADE;
DROP TABLE IF EXISTS cloud_events CASCADE;
DROP TABLE IF EXISTS incidents CASCADE;

CREATE TABLE incidents (
    incident_id VARCHAR PRIMARY KEY,
    status VARCHAR,
    severity VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    probable_root_cause VARCHAR,
    confidence DOUBLE PRECISION,
    affected_services JSON,
    score_breakdown JSON,
    remediation_suggestion VARCHAR
);
CREATE INDEX ix_incidents_incident_id ON incidents (incident_id);
CREATE INDEX ix_incidents_severity ON incidents (severity);
CREATE INDEX ix_incidents_status ON incidents (status);

CREATE TABLE cloud_events (
    event_id VARCHAR PRIMARY KEY,
    timestamp TIMESTAMP WITHOUT TIME ZONE,
    source VARCHAR,
    event_type VARCHAR,
    service VARCHAR,
    resource_id VARCHAR,
    metric VARCHAR,
    value DOUBLE PRECISION,
    severity VARCHAR,
    metadata_json JSON,
    incident_id VARCHAR REFERENCES incidents(incident_id)
);
CREATE INDEX ix_cloud_events_event_id ON cloud_events (event_id);
CREATE INDEX ix_cloud_events_service ON cloud_events (service);
CREATE INDEX ix_cloud_events_timestamp ON cloud_events (timestamp);

CREATE TABLE outbox_events (
    id VARCHAR PRIMARY KEY,
    incident_id VARCHAR,
    topic_arn VARCHAR,
    payload_json JSON,
    status VARCHAR,
    attempts INTEGER,
    max_attempts INTEGER,
    last_attempt_at TIMESTAMP WITHOUT TIME ZONE,
    error_message VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE
);
CREATE INDEX ix_outbox_events_status ON outbox_events (status);
"""

def generate_prod_copy():
    check_database_safety(DB_URL, entry_point="setup_prod_shaped_copy")
    print_database_host(DB_URL, entry_point="setup_prod_shaped_copy")
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    print("Applying baseline production schema...")
    cur.execute(BASELINE_DDL)
    conn.commit()

    print("Generating 42,033 OPEN legacy incidents (Sept 1 - Sept 12)...")
    base_time = datetime(2026, 9, 1, 0, 0, 0)
    incidents = []
    for i in range(42033):
        inc_id = f"inc-prod-{i:06d}"
        created = base_time + timedelta(seconds=(i * 22)) # spreads across ~11 days
        incidents.append((
            inc_id,
            "OPEN",
            "MEDIUM" if i % 2 == 0 else "HIGH",
            created,
            created,
            None,
            None,
            '["API", "Worker"]',
            None,
            None
        ))
    
    execute_values(
        cur,
        """INSERT INTO incidents (
            incident_id, status, severity, created_at, updated_at, 
            probable_root_cause, confidence, affected_services, score_breakdown, remediation_suggestion
        ) VALUES %s""",
        incidents,
        page_size=5000
    )
    conn.commit()
    print("42,033 OPEN incidents inserted.")

    print("Generating 106,586 legacy cloud_events with incident_id NULL...")
    events = []
    event_start = datetime(2026, 9, 1, 0, 0, 0)
    for i in range(106586):
        evt_id = f"evt-prod-{i:07d}"
        ts = event_start + timedelta(seconds=(i * 10))
        events.append((
            evt_id,
            ts,
            "CloudWatch",
            "metric_alarm",
            "API" if i % 3 == 0 else ("Worker" if i % 3 == 1 else "Database"),
            f"res-{i % 20}",
            "ErrorRate",
            5.0,
            "HIGH" if i % 4 == 0 else "MEDIUM",
            '{"env": "production"}',
            None
        ))
        if len(events) >= 10000:
            execute_values(
                cur,
                """INSERT INTO cloud_events (
                    event_id, timestamp, source, event_type, service, resource_id,
                    metric, value, severity, metadata_json, incident_id
                ) VALUES %s""",
                events,
                page_size=5000
            )
            conn.commit()
            events = []
            print(f"  Inserted {i+1}/106586 events...")
            
    if events:
        execute_values(
            cur,
            """INSERT INTO cloud_events (
                event_id, timestamp, source, event_type, service, resource_id,
                metric, value, severity, metadata_json, incident_id
            ) VALUES %s""",
            events,
            page_size=5000
        )
        conn.commit()
    print("106,586 cloud_events inserted.")

    cur.execute("ANALYZE incidents;")
    cur.execute("ANALYZE cloud_events;")
    cur.execute("ANALYZE outbox_events;")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM incidents WHERE status = 'OPEN';")
    open_inc_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM cloud_events WHERE incident_id IS NULL;")
    unassigned_evt_count = cur.fetchone()[0]
    print(f"Verification: {open_inc_count} OPEN incidents, {unassigned_evt_count} unassigned cloud_events.")

    conn.close()

if __name__ == "__main__":
    generate_prod_copy()
