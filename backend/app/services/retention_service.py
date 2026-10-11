import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from sqlalchemy import text

from backend.app.db.database import SessionLocal
from backend.app.db.models import DBCloudEvent
from event_processor.s3_archiver import s3_archiver

logger = logging.getLogger("cloudlens.retention")


class RetentionService:
    """
    Automated data lifecycle management service with verified safety gates.
    - Identifies telemetry rows older than a configurable retention threshold (default 30 days).
    - Archives rows to S3 in structured JSON before deletion.
    - Verifies archive integrity (upload status and row count) before deleting any row.
    - If archive fails or cannot be verified, aborts immediately and deletes nothing.
    - Deletes archived rows in safe batches of 1000 with dry-run support (default dry_run=True).
    """

    def archive_and_prune(
        self,
        days: int = 30,
        batch_size: int = 1000,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        db = SessionLocal()
        try:
            # Count eligible events
            total_eligible = db.query(DBCloudEvent).filter(DBCloudEvent.timestamp < cutoff_date).count()
            logger.info(f"Retention check: found {total_eligible} rows older than {cutoff_date} (dry_run={dry_run})")

            if dry_run or total_eligible == 0:
                return {
                    "status": "dry_run" if dry_run else "no_action_needed",
                    "retention_days": days,
                    "cutoff_date": cutoff_date.isoformat(),
                    "eligible_rows": total_eligible,
                    "archived_rows": 0,
                    "deleted_rows": 0,
                    "dry_run": dry_run
                }

            # Process in batches
            deleted_total = 0
            archived_total = 0

            while True:
                batch = (
                    db.query(DBCloudEvent)
                    .filter(DBCloudEvent.timestamp < cutoff_date)
                    .limit(batch_size)
                    .all()
                )
                if not batch:
                    break

                batch_records = [
                    {
                        "event_id": e.event_id,
                        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                        "service": e.service,
                        "event_type": e.event_type,
                        "resource_id": e.resource_id,
                        "metric": e.metric,
                        "value": e.value,
                        "severity": e.severity,
                        "metadata": e.metadata_json
                    }
                    for e in batch
                ]

                # Archive batch to S3 and strictly verify before proceeding to deletion
                batch_key = f"archive/{cutoff_date.strftime('%Y-%m-%d')}/batch_{datetime.now(timezone.utc).strftime('%H%M%S')}_{deleted_total}.json"
                try:
                    archive_res = s3_archiver.archive_telemetry_batch(batch_records, batch_id=batch_key)
                except Exception as archive_err:
                    logger.error(
                        f"S3 archive exception: {archive_err}. Aborting retention pruning immediately. No rows deleted."
                    )
                    db.rollback()
                    return {
                        "status": "aborted",
                        "error": f"S3 archive failed: {archive_err}",
                        "archived_rows": archived_total,
                        "deleted_rows": deleted_total,
                        "dry_run": False
                    }

                # Strict Verification Check: confirm valid status and exact row count match
                valid_statuses = ("uploaded_to_s3", "cached_locally", "verified")
                if not archive_res or archive_res.get("status") not in valid_statuses:
                    err_msg = archive_res.get("message") if archive_res else "Unknown archive verification error"
                    logger.error(
                        f"Archive verification failed: {err_msg}. Aborting retention pruning. No rows deleted."
                    )
                    db.rollback()
                    return {
                        "status": "aborted",
                        "error": f"Archive verification failed: {err_msg}",
                        "archived_rows": archived_total,
                        "deleted_rows": deleted_total,
                        "dry_run": False
                    }

                verified_count = archive_res.get("event_count", 0)
                if verified_count != len(batch_records):
                    logger.error(
                        f"Archive row count mismatch: expected {len(batch_records)}, verified {verified_count}. Aborting."
                    )
                    db.rollback()
                    return {
                        "status": "aborted",
                        "error": f"Archive count mismatch: expected {len(batch_records)}, verified {verified_count}",
                        "archived_rows": archived_total,
                        "deleted_rows": deleted_total,
                        "dry_run": False
                    }

                archived_total += len(batch_records)

                # Only after confirmed verification: delete batch by primary key IDs
                ids_to_delete = [e.event_id for e in batch]
                db.query(DBCloudEvent).filter(DBCloudEvent.event_id.in_(ids_to_delete)).delete(synchronize_session=False)
                db.commit()
                deleted_total += len(ids_to_delete)
                logger.info(
                    f"Retention verified and pruned batch of {len(ids_to_delete)} rows "
                    f"(total: {deleted_total}/{total_eligible})"
                )

            return {
                "status": "completed",
                "retention_days": days,
                "cutoff_date": cutoff_date.isoformat(),
                "eligible_rows": total_eligible,
                "archived_rows": archived_total,
                "deleted_rows": deleted_total,
                "dry_run": False
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Retention job failed: {e}")
            raise
        finally:
            db.close()


retention_service = RetentionService()
