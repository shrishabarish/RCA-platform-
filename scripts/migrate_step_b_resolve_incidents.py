#!/usr/bin/env python3
"""
Migration Step B: Batch Resolution of Stale Historical OPEN Incidents (>24h)
Reconciles OPEN incidents in batches of 5,000 to prevent replication lag or table locks.
DEFAULT IS DRY-RUN ONLY. Execution requires explicit --execute flag.
"""

import os
import sys
import time
import argparse
from pathlib import Path
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.core.safety import check_database_safety, print_database_host


def migrate_step_b(database_url: str, batch_size: int = 5000, execute: bool = False):
    check_database_safety(database_url, entry_point="migrate_step_b")
    print_database_host(database_url, entry_point="migrate_step_b")
    print("=" * 80)
    print("MIGRATION STEP B: BATCH RESOLUTION OF STALE OPEN INCIDENTS (>24H)")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN (READ ONLY)'}")
    print("=" * 80)

    engine = create_engine(database_url)

    # 1. Count total OPEN incidents older than 24 hours
    count_sql = text("""
        SELECT COUNT(*) 
        FROM incidents 
        WHERE status = 'OPEN' 
          AND created_at < NOW() - INTERVAL '24 HOURS';
    """)

    with engine.connect() as conn:
        stale_count = conn.execute(count_sql).scalar() or 0

    print(f"\n[QUERY RESULT] Stale OPEN incidents (> 24 hours old): {stale_count:,}")

    if not execute:
        print(f"\n[DRY RUN COMPLETE] Zero rows modified.")
        print(f"If executed, {stale_count:,} incidents would be transitioned from OPEN -> RESOLVED in batches of {batch_size:,}.")
        return stale_count

    if stale_count == 0:
        print("\nNo stale OPEN incidents found. Nothing to resolve.")
        return 0

    print(f"\nBeginning execution in batches of {batch_size:,}...")
    resolved_total = 0

    # Batch update statement selecting IDs in chunks
    batch_update_sql = text(f"""
        UPDATE incidents
        SET status = 'RESOLVED',
            updated_at = NOW()
        WHERE incident_id IN (
            SELECT incident_id
            FROM incidents
            WHERE status = 'OPEN'
              AND created_at < NOW() - INTERVAL '24 HOURS'
            LIMIT {batch_size}
        );
    """)

    batch_num = 1
    while True:
        with engine.begin() as conn:
            res = conn.execute(batch_update_sql)
            rows_updated = res.rowcount

        resolved_total += rows_updated
        print(f"Batch {batch_num}: Updated {rows_updated:,} rows (Progress: {resolved_total:,} / {stale_count:,})")

        if rows_updated == 0:
            break

        batch_num += 1
        time.sleep(1.0)  # Pause to avoid replication lag or excessive I/O

    print(f"\n[SUCCESS] Completed Step B: {resolved_total:,} stale incidents marked RESOLVED.")
    return resolved_total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migration Step B: Batch Resolve Stale Incidents")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL"), help="Database connection URL")
    parser.add_argument("--batch-size", type=int, default=5000, help="Batch chunk size (default: 5000)")
    parser.add_argument("--execute", action="store_true", default=False, help="Execute updates (default is dry-run)")
    args = parser.parse_args()

    if not args.db_url:
        print("Error: DATABASE_URL not specified and --db-url not provided.")
        sys.exit(1)

    migrate_step_b(args.db_url, batch_size=args.batch_size, execute=args.execute)
