#!/usr/bin/env python3
"""
Opt-In Backfill Command for Legacy Telemetry Events (< deploy_cutoff).
- Rate-limited and batched to avoid lock contention and replication lag.
- Notifications strictly disabled by default (zero OutboxItem rows created).
- Dry-run by default; requires --execute to apply changes.
"""

import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.db.models import DBCloudEvent, DBIncident, DBSystemSetting
from backend.app.services.correlator_consumer import get_deploy_cutoff
from backend.app.core.safety import check_database_safety, print_database_host


def run_backfill(database_url: str, batch_size: int = 1000, rate_limit: int = 500, execute: bool = False):
    check_database_safety(database_url, entry_point="backfill_legacy_events")
    print("=" * 80)
    print("CLOUDLENS LEGACY EVENT BACKFILL UTILITY")
    print_database_host(database_url, entry_point="backfill_legacy_events")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN (READ ONLY)'}")
    print(f"Batch Size: {batch_size:,} | Rate Limit: {rate_limit:,} events/sec")
    print(f"Notifications: DISABLED (Zero Outbox rows generated)")
    print("=" * 80)

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        cutoff = get_deploy_cutoff(session)
        print(f"\nDeploy Cutoff Timestamp: {cutoff.isoformat()}")

        # Count unassigned legacy events
        legacy_unassigned_count = session.query(DBCloudEvent).filter(
            DBCloudEvent.incident_id.is_(None),
            DBCloudEvent.timestamp < cutoff
        ).count()

        print(f"Total unassigned legacy events (< cutoff): {legacy_unassigned_count:,}")

        if not execute:
            print("\n[DRY RUN COMPLETE] Zero rows modified.")
            print("To execute backfill, re-run with --execute.")
            return legacy_unassigned_count

        if legacy_unassigned_count == 0:
            print("\nNo unassigned legacy events to backfill.")
            return 0

        # Create a single synthetic legacy container incident if none exists
        legacy_inc_id = "inc-legacy-backfill"
        legacy_inc = session.query(DBIncident).filter(DBIncident.incident_id == legacy_inc_id).first()
        if not legacy_inc:
            legacy_inc = DBIncident(
                incident_id=legacy_inc_id,
                status="RESOLVED",
                severity="LOW",
                created_at=cutoff,
                updated_at=cutoff,
                probable_root_cause="HISTORICAL_BACKFILL",
                confidence=100.0,
                affected_services=["Historical"],
                archive_status="SENT",
                notification_status="SENT",
                analysis_status="DONE"
            )
            session.add(legacy_inc)
            session.commit()
            print(f"Created legacy container incident: {legacy_inc_id}")

        processed = 0
        batch_interval = batch_size / float(rate_limit) if rate_limit > 0 else 0.0

        print(f"\nProcessing in batches of {batch_size:,} with {batch_interval:.2f}s delay...")
        while processed < legacy_unassigned_count:
            t0 = time.time()
            batch_events = session.query(DBCloudEvent).filter(
                DBCloudEvent.incident_id.is_(None),
                DBCloudEvent.timestamp < cutoff
            ).limit(batch_size).all()

            if not batch_events:
                break

            for ev in batch_events:
                ev.incident_id = legacy_inc_id

            session.commit()
            processed += len(batch_events)
            print(f"  Backfilled {processed:,} / {legacy_unassigned_count:,} events...")

            elapsed = time.time() - t0
            sleep_time = max(0.0, batch_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        print(f"\n[BACKFILL COMPLETED] Successfully backfilled {processed:,} legacy events.")
        return processed
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill legacy unassigned events")
    parser.add_argument("--db-url", type=str, default=os.getenv("DATABASE_URL", "postgresql://cloudlens_user:cloudlens_test_pass@localhost:5432/cloudlens_prod_copy"))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--rate-limit", type=int, default=500, help="Max events per second")
    parser.add_argument("--execute", action="store_true", help="Execute changes")
    args = parser.parse_args()

    run_backfill(database_url=args.db_url, batch_size=args.batch_size, rate_limit=args.rate_limit, execute=args.execute)
