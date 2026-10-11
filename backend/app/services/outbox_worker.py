import time
import uuid
import logging
import threading
import queue
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal
from backend.app.db.models import DBIncident, DBOutboxItem
from backend.app.core.metrics import metrics
from backend.app.core.config import config
from event_processor.s3_archiver import s3_archiver
from event_processor.sns_notifier import sns_notifier

logger = logging.getLogger("cloudlens.outbox_worker")

# 8 backoff delays over roughly 1 hour (sum ~ 4085s ~ 68 mins)
BACKOFF_SCHEDULE = [5, 15, 45, 120, 300, 600, 1200, 1800]


class OutboxWorker:
    """
    Hardened Durable Outbox Service.
    - Persists side-effects (S3 archiving, SNS alerting) atomically in the DB outbox.
    - Delivers asynchronously with multi-worker-safe row claiming (SELECT ... FOR UPDATE SKIP LOCKED).
    - Uses a PROCESSING status and lease timeout to recover items stuck after worker crash.
    - Drains gracefully on shutdown and recovers pending/retrying/stuck rows on startup.
    - Never drops work when the queue is full: work remains persisted in DB and worker polls it.
    - 8 attempts over ~1 hour backoff schedule before marking FAILED.
    - Exposes endpoints to inspect and replay FAILED items.
    """

    def __init__(self, max_queue_size: int = 1000, max_attempts: int = 8, lease_timeout_seconds: Optional[int] = None):
        import os
        timeout_sec = lease_timeout_seconds if lease_timeout_seconds is not None else int(os.getenv("OUTBOX_LEASE_TIMEOUT_SECONDS", "30"))
        self.max_attempts = max_attempts
        self.lease_timeout = timedelta(seconds=timeout_sec)
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, name="cloudlens-outbox-worker", daemon=True)
            self._thread.start()
            logger.info("Hardened outbox worker thread started.")
            self._recover_pending_items()

    def stop(self, timeout: float = 5.0):
        with self._lock:
            if not self._running:
                return
            self._running = False

        logger.info(f"Draining outbox worker (timeout={timeout}s)...")
        start_time = time.time()
        while not self._queue.empty() and (time.time() - start_time) < timeout:
            time.sleep(0.1)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("Outbox worker stopped cleanly.")

    def enqueue_side_effects(self, db: Session, incident_analysis: Dict[str, Any]):
        """
        Creates outbox records in the database within the current transaction,
        then enqueues notification for background delivery without losing rows on queue full.
        """
        inc_id = incident_analysis.get("incident_id")
        if not inc_id:
            return

        now = datetime.now(timezone.utc)

        # 1. S3 Archive Outbox Item
        s3_item = DBOutboxItem(
            id=f"outbox_s3_{uuid.uuid4().hex[:12]}",
            incident_id=inc_id,
            event_type="S3_ARCHIVE",
            payload_json=incident_analysis,
            status="PENDING",
            attempts=0,
            max_attempts=self.max_attempts,
            created_at=now,
            updated_at=now
        )
        db.add(s3_item)

        # 2. SNS Notification Outbox Item
        sns_item = DBOutboxItem(
            id=f"outbox_sns_{uuid.uuid4().hex[:12]}",
            incident_id=inc_id,
            event_type="SNS_NOTIFICATION",
            payload_json=incident_analysis,
            status="PENDING",
            attempts=0,
            max_attempts=self.max_attempts,
            created_at=now,
            updated_at=now
        )
        db.add(sns_item)

        metrics.inc_outbox_pending(2)

        # Non-blocking enqueue signal: if full, rows remain safe in DB and will be polled
        for item_id in [s3_item.id, sns_item.id]:
            try:
                self._queue.put_nowait(item_id)
                metrics.set_queue_depth(self._queue.qsize())
            except queue.Full:
                logger.info(f"Outbox in-memory queue full; item {item_id} stored in DB for worker polling.")

    def _recover_pending_items(self):
        """Scans database for PENDING, RETRYING, or expired PROCESSING items on startup."""
        try:
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                pending_count = db.query(DBOutboxItem).filter(
                    or_(
                        DBOutboxItem.status == "PENDING",
                        and_(DBOutboxItem.status == "RETRYING", DBOutboxItem.attempts < DBOutboxItem.max_attempts),
                        and_(DBOutboxItem.status == "PROCESSING", DBOutboxItem.lease_until < now)
                    )
                ).count()
                logger.info(f"Startup outbox recovery check: {pending_count} pending/retrying items found in DB.")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not check pending outbox items on startup: {e}")

    def claim_next_item(self) -> Optional[Dict[str, Any]]:
        """
        Atomically claims a single outbox row using SELECT ... FOR UPDATE SKIP LOCKED
        (with graceful fallback on SQLite), setting status to PROCESSING with lease timeout.
        Safe for multiple concurrent worker processes.
        """
        now = datetime.now(timezone.utc)
        db = SessionLocal()
        try:
            query = db.query(DBOutboxItem).filter(
                or_(
                    DBOutboxItem.status == "PENDING",
                    and_(
                        DBOutboxItem.status == "RETRYING",
                        or_(DBOutboxItem.next_retry_at.is_(None), DBOutboxItem.next_retry_at <= now),
                        DBOutboxItem.attempts < DBOutboxItem.max_attempts
                    ),
                    and_(
                        DBOutboxItem.status == "PROCESSING",
                        DBOutboxItem.lease_until.isnot(None),
                        DBOutboxItem.lease_until < now,
                        DBOutboxItem.attempts < DBOutboxItem.max_attempts
                    )
                )
            ).order_by(DBOutboxItem.created_at.asc())

            # Multi-worker lock safety: FOR UPDATE SKIP LOCKED
            dialect = db.bind.dialect.name if db.bind else ""
            if dialect in ("postgresql", "postgres"):
                item = query.with_for_update(skip_locked=True).first()
            else:
                item = query.first()

            if not item:
                return None

            item.status = "PROCESSING"
            item.lease_until = now + self.lease_timeout
            item.updated_at = now
            db.commit()

            return {
                "id": item.id,
                "incident_id": item.incident_id,
                "event_type": item.event_type,
                "payload_json": item.payload_json,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Error claiming outbox item: {e}")
            return None
        finally:
            db.close()

    def _run_loop(self):
        while self._running:
            # Drain signaled items or poll database
            try:
                # Wait briefly for in-memory signal
                try:
                    self._queue.get(timeout=0.5)
                    self._queue.task_done()
                except queue.Empty:
                    pass

                # Claim and process next available DB item
                claimed = self.claim_next_item()
                if claimed:
                    self._process_claimed_item(claimed)
                else:
                    # No work ready, sleep briefly to avoid busy loop
                    time.sleep(0.5)

                metrics.set_queue_depth(self._queue.qsize())
            except Exception as loop_err:
                logger.error(f"Error in outbox worker loop: {loop_err}")
                time.sleep(0.5)

    def _process_outbox_item(self, item_id: str, inc_id: str, ev_type: str):
        """Processes a specific outbox item (used in deterministic unit tests and manual execution)."""
        db = SessionLocal()
        try:
            item = db.query(DBOutboxItem).filter(DBOutboxItem.id == item_id).first()
            if not item:
                return
            claimed = {
                "id": item.id,
                "incident_id": item.incident_id,
                "event_type": item.event_type,
                "payload_json": item.payload_json,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts
            }
        finally:
            db.close()
        self._process_claimed_item(claimed)

    def _process_claimed_item(self, claimed: Dict[str, Any]):
        item_id = claimed["id"]
        inc_id = claimed["incident_id"]
        ev_type = claimed["event_type"]
        payload = claimed["payload_json"]

        db = SessionLocal()
        try:
            item = db.query(DBOutboxItem).filter(DBOutboxItem.id == item_id).first()
            if not item or item.status == "SENT":
                return

            incident = db.query(DBIncident).filter(DBIncident.incident_id == inc_id).first()
            item.attempts += 1
            now = datetime.now(timezone.utc)
            item.updated_at = now

            success = False
            error_msg = None

            if ev_type == "S3_ARCHIVE":
                res = s3_archiver.archive_incident(payload)
                if res and res.get("status") in ("uploaded_to_s3", "SENT"):
                    success = True
                else:
                    error_msg = res.get("note") or res.get("error") or res.get("message") or "S3 upload failed"
            elif ev_type == "SNS_NOTIFICATION":
                res = sns_notifier.publish_incident_alert(payload)
                if res and res.get("status") in ("delivered", "SENT"):
                    success = True
                else:
                    error_msg = res.get("note") or res.get("error") or res.get("message") or "SNS publish failed"

            if success:
                item.status = "SENT"
                item.lease_until = None
                item.next_retry_at = None
                item.last_error = None
                metrics.dec_outbox_pending(1)
                if incident:
                    if ev_type == "S3_ARCHIVE":
                        incident.archive_status = "SENT"
                    else:
                        incident.notification_status = "SENT"
                logger.info(f"Outbox delivered {ev_type} for incident {inc_id}")
            else:
                item.last_error = error_msg
                if item.attempts >= item.max_attempts:
                    item.status = "FAILED"
                    item.lease_until = None
                    item.next_retry_at = None
                    metrics.dec_outbox_pending(1)
                    metrics.inc_outbox_failed(1)
                    if incident:
                        if ev_type == "S3_ARCHIVE":
                            incident.archive_status = "FAILED"
                        else:
                            incident.notification_status = "FAILED"
                        incident.delivery_error = f"{ev_type}: {error_msg}"
                    logger.warning(
                        f"Outbox item {item_id} marked FAILED after {item.attempts} attempts: {error_msg}"
                    )
                else:
                    item.status = "RETRYING"
                    item.lease_until = None
                    # Progressive backoff schedule (8 steps over ~1 hour)
                    delay_idx = min(item.attempts - 1, len(BACKOFF_SCHEDULE) - 1)
                    delay_sec = BACKOFF_SCHEDULE[delay_idx]
                    item.next_retry_at = now + timedelta(seconds=delay_sec)
                    metrics.inc_outbox_retries(1)
                    if incident:
                        if ev_type == "S3_ARCHIVE":
                            incident.archive_status = "RETRYING"
                        else:
                            incident.notification_status = "RETRYING"
                    logger.info(
                        f"Outbox item {item_id} scheduled for retry in {delay_sec}s "
                        f"(attempt {item.attempts}/{item.max_attempts})"
                    )

            db.commit()
        except Exception as e:
            logger.error(f"Error processing outbox item {item_id}: {e}")
            db.rollback()
        finally:
            db.close()

    def get_failed_items(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns list of outbox items that have failed all attempts."""
        db = SessionLocal()
        try:
            items = db.query(DBOutboxItem).filter(
                DBOutboxItem.status.in_(["FAILED", "DEAD_LETTER"])
            ).order_by(DBOutboxItem.updated_at.desc()).limit(limit).all()

            return [
                {
                    "id": it.id,
                    "incident_id": it.incident_id,
                    "event_type": it.event_type,
                    "attempts": it.attempts,
                    "max_attempts": it.max_attempts,
                    "last_error": it.last_error,
                    "status": it.status,
                    "created_at": it.created_at.isoformat() if it.created_at else None,
                    "updated_at": it.updated_at.isoformat() if it.updated_at else None,
                }
                for it in items
            ]
        finally:
            db.close()

    def replay_failed_item(self, item_id: str) -> bool:
        """Resets a FAILED outbox item to PENDING for re-delivery."""
        db = SessionLocal()
        try:
            item = db.query(DBOutboxItem).filter(
                DBOutboxItem.id == item_id,
                DBOutboxItem.status.in_(["FAILED", "DEAD_LETTER"])
            ).first()
            if not item:
                return False

            item.status = "PENDING"
            item.attempts = 0
            item.lease_until = None
            item.next_retry_at = None
            item.last_error = None
            item.updated_at = datetime.now(timezone.utc)
            db.commit()

            metrics.inc_outbox_pending(1)
            try:
                self._queue.put_nowait(item.id)
            except queue.Full:
                pass
            return True
        finally:
            db.close()

    def replay_all_failed(self) -> int:
        """Resets all FAILED outbox items to PENDING."""
        db = SessionLocal()
        try:
            items = db.query(DBOutboxItem).filter(
                DBOutboxItem.status.in_(["FAILED", "DEAD_LETTER"])
            ).all()
            replayed = len(items)
            now = datetime.now(timezone.utc)
            for it in items:
                it.status = "PENDING"
                it.attempts = 0
                it.lease_until = None
                it.next_retry_at = None
                it.last_error = None
                it.updated_at = now
            db.commit()
            if replayed > 0:
                metrics.inc_outbox_pending(replayed)
            return replayed
        finally:
            db.close()


outbox_worker = OutboxWorker()
