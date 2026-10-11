#!/usr/bin/env python3
"""
Migration Step A: Additive Schema Verification & Execution Script
Single Source of Truth: Alembic (alembic/versions/0002_add_missing_columns_and_indexes.py)

DEFAULT IS DRY-RUN ONLY. Execution requires explicit --execute flag.
To stamp production baseline before executing:
    alembic stamp 0001_baseline_prod_schema
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.core.safety import check_database_safety, print_database_host


def get_alembic_sql() -> str:
    """Generates the exact static SQL emitted by Alembic for revision 0002."""
    cmd = [
        sys.executable, "-m", "alembic", "upgrade",
        "0001_baseline_prod_schema:0002_add_columns_and_indexes",
        "--sql"
    ]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Alembic SQL generation failed: {res.stderr}")
    return res.stdout


def migrate_step_a(database_url: str, execute: bool = False):
    check_database_safety(database_url, entry_point="migrate_step_a")
    print_database_host(database_url, entry_point="migrate_step_a")
    print("=" * 80)
    print("MIGRATION STEP A: ADDITIVE COLUMNS & INDEXES")
    print("SINGLE SOURCE OF TRUTH: Alembic (0002_add_missing_columns_and_indexes.py)")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN (READ ONLY)'}")
    print("=" * 80)

    # 1. Inspect live DB state
    engine = create_engine(database_url)
    insp = inspect(engine)

    inc_cols = {c['name'] for c in insp.get_columns('incidents')}
    inc_indexes = {i['name'] for i in insp.get_indexes('incidents')}
    needed_inc_cols = ['archive_status', 'notification_status', 'delivery_error', 'analysis_status', 'analyzed_at']
    needed_inc_indexes = ['ix_incidents_status_created_at', 'ix_incidents_open_created_at', 'ix_incidents_archive_status', 'ix_incidents_notification_status', 'ix_incidents_analysis_status']

    missing_inc_cols = [c for c in needed_inc_cols if c not in inc_cols]
    missing_inc_indexes = [i for i in needed_inc_indexes if i not in inc_indexes]

    ev_indexes = {i['name'] for i in insp.get_indexes('cloud_events')}
    needed_ev_indexes = ['ix_cloud_events_service_timestamp', 'ix_cloud_events_unassigned']
    missing_ev_indexes = [i for i in needed_ev_indexes if i not in ev_indexes]

    outbox_cols = {c['name'] for c in insp.get_columns('outbox_events')}
    outbox_indexes = {i['name'] for i in insp.get_indexes('outbox_events')}
    needed_outbox_cols = ['event_type', 'lease_until', 'next_retry_at', 'last_error']
    needed_outbox_indexes = ['ix_outbox_events_lease_until', 'ix_outbox_events_next_retry_at']

    missing_outbox_cols = [c for c in needed_outbox_cols if c not in outbox_cols]
    missing_outbox_indexes = [i for i in needed_outbox_indexes if i not in outbox_indexes]

    total_missing = len(missing_inc_cols) + len(missing_inc_indexes) + len(missing_ev_indexes) + len(missing_outbox_cols) + len(missing_outbox_indexes)

    print("\n--- SCHEMA DRIFT INSPECTION ---")
    print(f"Missing incident columns : {missing_inc_cols}")
    print(f"Missing incident indexes : {missing_inc_indexes}")
    print(f"Missing event indexes    : {missing_ev_indexes}")
    print(f"Missing outbox columns   : {missing_outbox_cols}")
    print(f"Missing outbox indexes   : {missing_outbox_indexes}")
    print(f"Total missing items      : {total_missing}")

    # 2. Alembic-generated DDL
    print("\n--- ALEMBIC GENERATED DDL (Single Source of Truth) ---")
    alembic_sql = get_alembic_sql()
    print(alembic_sql)

    if not execute:
        print("\n[DRY RUN COMPLETE] Zero changes executed.")
        print("To apply via Alembic after taking an RDS snapshot:")
        print("  1. Stamp the production baseline:")
        print("     alembic stamp 0001_baseline_prod_schema")
        print("  2. Apply the additive migration:")
        print("     alembic upgrade head")
        print("     OR run this script with --execute")
        return total_missing

    if total_missing == 0:
        print("\nDatabase is already up to date. Stamping Alembic version if needed.")
        subprocess.run([sys.executable, "-m", "alembic", "stamp", "head"], cwd=str(PROJECT_ROOT), check=True)
        return 0

    print("\nExecuting Alembic upgrade head...")
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    res = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Alembic upgrade failed: {res.stderr}")
    print(res.stdout)
    print("[SUCCESS] All additive migrations applied via Alembic.")
    return total_missing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migration Step A: Additive Schema (Alembic Source of Truth)")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL"), help="Database connection URL")
    parser.add_argument("--execute", action="store_true", default=False, help="Execute migrations (default is dry-run)")
    args = parser.parse_args()

    if not args.db_url:
        print("Error: DATABASE_URL not specified and --db-url not provided.")
        sys.exit(1)

    migrate_step_a(args.db_url, execute=args.execute)
