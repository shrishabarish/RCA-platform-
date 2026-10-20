import os
import time
import uuid
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Set, Tuple

import numpy as np
from sqlalchemy import text, create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal, engine
from backend.app.db.models import DBCloudEvent, DBIncident, DBOutboxItem, DBSystemSetting
from backend.app.models.event import (
    CloudEvent,
    Incident,
    IncidentStatus,
    Severity,
    EventSource
)
from backend.app.core.config import config
from backend.app.core.metrics import metrics
from backend.app.services.outbox_worker import outbox_worker
from rca_engine.correlation import TimeWindowCorrelator, _ensure_utc
from rca_engine.analyzer import analyze_incident
from rca_engine.causal_graph import causal_graph_engine

logger = logging.getLogger("cloudlens.correlator_consumer")

CORRELATOR_ADVISORY_LOCK_ID = 987654321


def get_deploy_cutoff(db: Session) -> datetime:
    """
    Returns the deploy cutoff datetime.
    Events and incidents older than this cutoff are ignored by the runtime correlator,
    auto-resolve job, and outbox notification dispatch.
    1. Check config.correlator.deploy_cutoff_timestamp or DEPLOY_CUTOFF_TIMESTAMP env.
    2. Check system_settings table for 'deploy_cutoff'.
    3. If not found, persist current UTC time into system_settings as 'deploy_cutoff'.
    """
    override_str = config.correlator.deploy_cutoff_timestamp or os.getenv("DEPLOY_CUTOFF_TIMESTAMP")
    if override_str:
        try:
            return datetime.fromisoformat(override_str.replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"Failed to parse DEPLOY_CUTOFF_TIMESTAMP '{override_str}': {e}")

    if config.env == "testing":
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    try:
        row = db.query(DBSystemSetting).filter(DBSystemSetting.key == "deploy_cutoff").first()
        if row:
            return datetime.fromisoformat(row.value.replace("Z", "+00:00"))

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        new_row = DBSystemSetting(
            key="deploy_cutoff",
            value=now_iso,
            created_at=now,
            updated_at=now
        )
        db.add(new_row)
        db.commit()
        logger.info(f"Recorded initial deploy cutoff timestamp: {now_iso}")
        return now
    except Exception as e:
        db.rollback()
        # Fallback if system_settings table is not yet migrated
        logger.debug(f"system_settings check fallback: {e}")
        return datetime.now(timezone.utc)


class CorrelatorConsumer:
    """
    Durable, Hardened Serialized Correlator Consumer.
    - Claims unassigned events (incident_id IS NULL) in batches >= deploy_cutoff.
    - In ONE transaction: assigns incident IDs, creates/updates incidents, writes outbox rows.
    - Serialized across multiple Uvicorn workers via Postgres advisory lock on a DEDICATED non-pooled connection.
    - Automatic takeover if leader dies or its connection is severed.
    - Health checks the advisory lock connection periodically.
    - Guardrails and metrics: monitors unassigned counts, lag p50/p95/max, and batch processing time.
    - Resumes safely from database after crash or restart.
    - Bounded to recent active incidents; ignores legacy unmigrated history.
    """

    def __init__(self, db_engine=None, lock_id: int = CORRELATOR_ADVISORY_LOCK_ID):
        self._db_engine = db_engine
        self._lock_id = lock_id
        self.correlator = TimeWindowCorrelator(
            time_window_seconds=config.correlator.window_seconds,
            max_duration_seconds=config.correlator.max_incident_window_duration_seconds,
            max_events=config.correlator.max_events_per_incident
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._batch_lock = threading.Lock()
        self._lock_conn = None
        self._lock_engine = None
        self._is_leader = False
        self._worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._last_analyzed_time: Dict[str, float] = {}
        self._last_auto_resolve: float = 0.0
        self._last_health_check: float = 0.0
        self._last_heartbeat_time: float = 0.0
        self._last_progress_time: float = time.monotonic()
        self.heartbeat_timeout = float(os.getenv("LEADER_HEARTBEAT_TIMEOUT_SECONDS", "30.0"))

        # Guardrails & Metrics
        self.metrics_data: Dict[str, Any] = {
            "lag_p50_ms": 0.0,
            "lag_p95_ms": 0.0,
            "lag_max_ms": 0.0,
            "unassigned_count": 0,
            "oldest_unassigned_age_seconds": 0.0,
            "last_batch_duration_ms": 0.0,
            "batches_processed": 0,
            "events_processed": 0,
            "leader_id": None
        }

    @property
    def target_engine(self):
        return self._db_engine or engine

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def leader_info(self) -> Dict[str, Any]:
        return {
            "is_leader": self._is_leader,
            "worker_id": self._worker_id,
            "pid": os.getpid(),
            "metrics": self.metrics_data
        }

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._last_progress_time = time.monotonic()
            self._thread = threading.Thread(target=self._run_loop, name=f"correlator-{self._worker_id}", daemon=True)
            self._thread.start()
            self._watchdog_thread = threading.Thread(target=self._run_watchdog, name=f"watchdog-{self._worker_id}", daemon=True)
            self._watchdog_thread.start()
            logger.info(f"Correlator consumer worker started [{self._worker_id}].")

    def stop(self, timeout: float = 5.0):
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)

        self._release_advisory_lock()
        logger.info(f"Correlator consumer stopped cleanly [{self._worker_id}].")

    def _get_dedicated_lock_connection(self):
        """Returns a dedicated non-pooled connection with TCP keepalives outside SQLAlchemy pool."""
        target_eng = self.target_engine
        dialect = target_eng.dialect.name
        if dialect not in ("postgresql", "postgres"):
            return None

        if self._lock_engine is None:
            connect_args = {}
            drivername = target_eng.url.drivername
            if "pg8000" in drivername:
                connect_args = {"tcp_keepalive": True}
            elif "psycopg" in drivername or drivername in ("postgresql", "postgres"):
                connect_args = {
                    "keepalives": 1,
                    "keepalives_idle": 10,
                    "keepalives_interval": 5,
                    "keepalives_count": 3
                }
            self._lock_engine = create_engine(
                target_eng.url,
                poolclass=NullPool,
                connect_args=connect_args
            )
        try:
            return self._lock_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        except Exception:
            fallback_engine = create_engine(target_eng.url, poolclass=NullPool)
            return fallback_engine.connect().execution_options(isolation_level="AUTOCOMMIT")

    def _acquire_advisory_lock(self) -> bool:
        """Attempts to acquire the Postgres advisory lock on a dedicated connection."""
        try:
            dialect = self.target_engine.dialect.name
            if dialect not in ("postgresql", "postgres"):
                self._is_leader = True
                self.metrics_data["leader_id"] = self._worker_id
                return True

            if self._lock_conn is None or self._lock_conn.closed:
                self._lock_conn = self._get_dedicated_lock_connection()

            res = self._lock_conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": self._lock_id}
            ).scalar()

            self._is_leader = bool(res)
            if self._is_leader:
                self.metrics_data["leader_id"] = self._worker_id
                self._last_progress_time = time.monotonic()
                logger.info(f"[{self._worker_id}] Acquired Postgres advisory lock (LEADER).")
            return self._is_leader
        except Exception as e:
            logger.warning(f"[{self._worker_id}] Error acquiring Postgres advisory lock: {e}")
            self._release_advisory_lock()
            return False

    def _health_check_lock(self) -> bool:
        """Verifies the dedicated lock connection is active and healthy."""
        dialect = self.target_engine.dialect.name
        if dialect not in ("postgresql", "postgres"):
            return True

        if not self._is_leader or self._lock_conn is None or self._lock_conn.closed:
            return False

        try:
            self._lock_conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"[{self._worker_id}] Advisory lock connection health check failed: {e}. Relinquishing leadership.")
            self._release_advisory_lock()
            return False

    def _release_advisory_lock(self):
        try:
            if self._lock_conn and not self._lock_conn.closed and self._is_leader:
                try:
                    self._lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": self._lock_id}
                    )
                except Exception:
                    pass
                self._lock_conn.close()
        except Exception as e:
            logger.debug(f"[{self._worker_id}] Error releasing advisory lock: {e}")
        finally:
            self._lock_conn = None
            self._is_leader = False
            self.metrics_data["leader_id"] = None

    def _run_watchdog(self):
        """Leader watchdog: detects stuck loop and forces lock release so standby can take over."""
        while self._running:
            try:
                if self._is_leader:
                    stalled = time.monotonic() - self._last_progress_time
                    if stalled > self.heartbeat_timeout:
                        logger.error(
                            f"[{self._worker_id}] LEADER WATCHDOG TRIGGERED: Main loop stalled for {stalled:.1f}s > "
                            f"{self.heartbeat_timeout}s! Releasing advisory lock so standby can take over."
                        )
                        self._release_advisory_lock()
            except Exception as e:
                logger.debug(f"Watchdog exception: {e}")
            time.sleep(0.5)

    def _record_heartbeat(self):
        """Records progress heartbeat to system_settings."""
        try:
            from sqlalchemy.orm import sessionmaker
            maker = sessionmaker(bind=self._db_engine) if self._db_engine else SessionLocal
            with maker() as db:
                now_utc = datetime.now(timezone.utc)
                hb_val = f"{self._worker_id}:{now_utc.isoformat()}"
                row = db.query(DBSystemSetting).filter(DBSystemSetting.key == "leader_heartbeat").first()
                if row:
                    row.value = hb_val
                    row.updated_at = now_utc
                else:
                    db.add(DBSystemSetting(key="leader_heartbeat", value=hb_val, created_at=now_utc, updated_at=now_utc))
                db.commit()
        except Exception:
            pass

    def _check_stale_leader(self):
        """Standby check: if leader heartbeat in DB is stale > timeout * 2, terminates stale holding session."""
        try:
            dialect = self.target_engine.dialect.name
            if dialect not in ("postgresql", "postgres"):
                return
            from sqlalchemy.orm import sessionmaker
            maker = sessionmaker(bind=self._db_engine) if self._db_engine else SessionLocal
            with maker() as db:
                row = db.query(DBSystemSetting).filter(DBSystemSetting.key == "leader_heartbeat").first()
                if not row or not row.updated_at:
                    return
                hb_ts = row.updated_at
                if hb_ts.tzinfo is None:
                    hb_ts = hb_ts.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - hb_ts).total_seconds()
                if age > (self.heartbeat_timeout * 2):
                    logger.warning(f"[{self._worker_id}] Stale leader detected (heartbeat age={age:.1f}s). Terminating backend session.")
                    db.execute(text("""
                        SELECT pg_terminate_backend(pid) 
                        FROM pg_locks 
                        WHERE locktype = 'advisory' AND objid = :lock_id;
                    """), {"lock_id": self._lock_id})
                    db.commit()
        except Exception as e:
            logger.debug(f"Stale leader check error: {e}")

    def _run_loop(self):
        while self._running:
            try:
                now_mono = time.monotonic()

                # Standby worker: attempt to acquire lock
                if not self._is_leader:
                    if not self._acquire_advisory_lock():
                        self._check_stale_leader()
                        time.sleep(0.5)
                        continue

                # Leader worker: record progress & check connection health every 5s
                self._last_progress_time = now_mono
                if (now_mono - self._last_heartbeat_time) >= 5.0:
                    self._last_heartbeat_time = now_mono
                    self._record_heartbeat()

                if (now_mono - self._last_health_check) >= 5.0:
                    self._last_health_check = now_mono
                    if not self._health_check_lock():
                        time.sleep(0.5)
                        continue

                # Test hook to simulate stuck leader loop
                if getattr(self, "_stuck_for_test", False):
                    time.sleep(10.0)

                count = self.process_batch(batch_size=500)
                if count > 0:
                    continue  # Backlog draining: iterate immediately
                else:
                    time.sleep(0.5)
            except Exception as e:
                logger.warning(f"[{self._worker_id}] Error in correlator consumer loop: {e}")
                time.sleep(0.5)

    def drain(self, max_batches: int = 50) -> int:
        """Synchronously processes unassigned events until backlog is clear."""
        total = 0
        for _ in range(max_batches):
            cnt = self.process_batch(batch_size=500)
            total += cnt
            if cnt == 0:
                break
        return total

    def process_batch(self, db: Optional[Session] = None, batch_size: int = 500) -> int:
        with self._batch_lock:
            return self._process_batch_unlocked(db=db, batch_size=batch_size)

    def _process_batch_unlocked(self, db: Optional[Session] = None, batch_size: int = 500) -> int:
        """
        Executes one atomic batch correlation step in ONE transaction:
        1. Determines deploy_cutoff (ignores legacy history older than cutoff).
        2. Claims up to batch_size unassigned events using FOR UPDATE SKIP LOCKED.
        3. Loads candidate OPEN incidents bounded by recent time window and >= deploy_cutoff.
        4. Assigns events, creates/updates incidents, runs debounced RCA, writes Outbox records.
        5. Computes lag metrics and enforces guardrail alert thresholds.
        6. Sets cloud_events.incident_id and commits atomically.
        """
        t_start = time.perf_counter()
        manage_session = (db is None)
        if manage_session:
            if self._db_engine:
                from sqlalchemy.orm import sessionmaker
                db = sessionmaker(bind=self._db_engine)()
            else:
                db = SessionLocal()

        try:
            deploy_cutoff = get_deploy_cutoff(db)
            dialect = db.bind.dialect.name if db.bind else ""

            # Claim unassigned events >= deploy_cutoff based on ingested_at (so late real events are not missed)
            cutoff_col = DBCloudEvent.ingested_at if hasattr(DBCloudEvent, "ingested_at") else DBCloudEvent.timestamp
            query = db.query(DBCloudEvent).filter(
                DBCloudEvent.incident_id.is_(None),
                cutoff_col >= deploy_cutoff
            ).order_by(cutoff_col.asc()).limit(batch_size)

            if dialect in ("postgresql", "postgres"):
                query = query.with_for_update(skip_locked=True)

            unassigned_db_events = query.all()
            if not unassigned_db_events:
                if manage_session:
                    db.commit()
                return 0

            from backend.app.services.incident_service import incident_service

            # Convert to Pydantic events
            unassigned_events = [
                incident_service._convert_to_pydantic_event(e)
                for e in unassigned_db_events
            ]

            # Bounded load of candidate OPEN incidents from DB (>= deploy_cutoff)
            earliest_ts = min(_ensure_utc(e.timestamp) for e in unassigned_events)
            cutoff = max(deploy_cutoff, earliest_ts - timedelta(seconds=config.correlator.window_seconds))

            open_db_incidents = db.query(DBIncident).filter(
                DBIncident.status == "OPEN",
                DBIncident.created_at >= deploy_cutoff,
                DBIncident.updated_at >= cutoff
            ).all()

            open_incidents = [
                incident_service._convert_to_pydantic_incident(db_inc)
                for db_inc in open_db_incidents
            ]

            assignments: Dict[str, str] = {}
            incidents_by_id: Dict[str, Incident] = {inc.incident_id: inc for inc in open_incidents}
            created_incident_ids: Set[str] = set()
            modified_incident_ids: Set[str] = set()

            sev_order = {
                "LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3,
                Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3
            }

            # Correlate events chronologically
            sorted_events = sorted(unassigned_events, key=lambda e: _ensure_utc(e.timestamp))
            for event in sorted_events:
                causal_graph_engine.auto_register_from_event(event)
                cur_ts = _ensure_utc(event.timestamp)
                matched_inc: Optional[Incident] = None

                for inc in incidents_by_id.values():
                    last_ts = _ensure_utc(inc.updated_at)
                    first_ts = _ensure_utc(inc.created_at)

                    sliding_diff = abs((cur_ts - last_ts).total_seconds())
                    duration_diff = abs((cur_ts - first_ts).total_seconds())

                    within_sliding_gap = sliding_diff <= self.correlator.time_window.total_seconds()
                    within_max_duration = duration_diff <= self.correlator.max_incident_duration.total_seconds()
                    under_max_events = len(inc.events) < self.correlator.max_events_per_incident

                    if within_sliding_gap and within_max_duration and under_max_events:
                        is_related = (
                            not inc.affected_services
                            or any(causal_graph_engine.are_services_related(event.service, s) for s in inc.affected_services)
                        )
                        if is_related:
                            matched_inc = inc
                            break

                if matched_inc is not None:
                    matched_inc.events.append(event)
                    matched_inc.created_at = min(matched_inc.created_at, event.timestamp)
                    matched_inc.updated_at = max(matched_inc.updated_at, event.timestamp)
                    if event.service and event.service not in matched_inc.affected_services:
                        matched_inc.affected_services = sorted(list(set(matched_inc.affected_services + [event.service])))

                    cur_sev = matched_inc.severity
                    new_sev = event.severity
                    if sev_order.get(new_sev, 0) > sev_order.get(cur_sev, 0):
                        matched_inc.severity = new_sev

                    assignments[event.event_id] = matched_inc.incident_id
                    if matched_inc.incident_id not in created_incident_ids:
                        modified_incident_ids.add(matched_inc.incident_id)
                else:
                    new_inc = self.correlator._create_incident([event])
                    incidents_by_id[new_inc.incident_id] = new_inc
                    created_incident_ids.add(new_inc.incident_id)
                    assignments[event.event_id] = new_inc.incident_id

            now_utc = datetime.now(timezone.utc)
            now_mono = time.monotonic()
            debounce_sec = config.correlator.rca_debounce_seconds

            # 1. Persist newly created incidents and enqueue outbox records
            for inc_id in created_incident_ids:
                inc = incidents_by_id[inc_id]
                analysis = analyze_incident(inc)
                inc.probable_root_cause = analysis.get("probable_root_cause")
                inc.confidence = analysis.get("confidence")
                inc.score_breakdown = analysis.get("score_breakdown")
                inc.remediation_suggestion = analysis.get("remediation_suggestion")
                inc.analysis_status = "DONE"
                inc.analyzed_at = now_utc
                self._last_analyzed_time[inc_id] = now_mono

                db_existing = db.query(DBIncident).filter(DBIncident.incident_id == inc.incident_id).first()
                if db_existing:
                    db_existing.status = incident_service._get_val(inc.status)
                    db_existing.severity = incident_service._get_val(inc.severity)
                    db_existing.updated_at = inc.updated_at
                    db_existing.probable_root_cause = inc.probable_root_cause
                    db_existing.confidence = inc.confidence
                    db_existing.affected_services = inc.affected_services
                    db_existing.score_breakdown = inc.score_breakdown
                    db_existing.remediation_suggestion = inc.remediation_suggestion
                    db_existing.analysis_status = inc.analysis_status
                    db_existing.analyzed_at = inc.analyzed_at
                else:
                    db_new_inc = DBIncident(
                        incident_id=inc.incident_id,
                        status=incident_service._get_val(inc.status),
                        severity=incident_service._get_val(inc.severity),
                        created_at=inc.created_at,
                        updated_at=inc.updated_at,
                        probable_root_cause=inc.probable_root_cause,
                        confidence=inc.confidence,
                        affected_services=inc.affected_services,
                        score_breakdown=inc.score_breakdown,
                        remediation_suggestion=inc.remediation_suggestion,
                        archive_status="PENDING",
                        notification_status="PENDING",
                        analysis_status=inc.analysis_status,
                        analyzed_at=inc.analyzed_at
                    )
                    db.merge(db_new_inc)
                    db.flush()
                    outbox_worker.enqueue_side_effects(db, analysis)

            # 2. Update modified existing incidents (with debounced RCA)
            for inc_id in modified_incident_ids:
                inc = incidents_by_id[inc_id]
                last_time = self._last_analyzed_time.get(inc_id, 0.0)
                should_rerun_rca = (now_mono - last_time) >= debounce_sec

                db_inc = db.query(DBIncident).filter(DBIncident.incident_id == inc_id).first()
                if db_inc:
                    db_inc.created_at = min(db_inc.created_at, inc.created_at)
                    db_inc.updated_at = max(db_inc.updated_at, inc.updated_at)
                    db_inc.affected_services = inc.affected_services
                    db_inc.severity = incident_service._get_val(inc.severity)

                    if should_rerun_rca:
                        analysis = analyze_incident(inc)
                        db_inc.probable_root_cause = analysis.get("probable_root_cause")
                        db_inc.confidence = analysis.get("confidence")
                        db_inc.score_breakdown = analysis.get("score_breakdown")
                        db_inc.remediation_suggestion = analysis.get("remediation_suggestion")
                        db_inc.analysis_status = "DONE"
                        db_inc.analyzed_at = now_utc
                        self._last_analyzed_time[inc_id] = now_mono
                    else:
                        db_inc.analysis_status = "STALE"

            # 3. Update all claimed events with their assigned incident_id
            for ev in unassigned_db_events:
                ev.incident_id = assignments[ev.event_id]

            # 4. Check auto-resolve for inactive incidents periodically (every 15s)
            if (now_mono - self._last_auto_resolve) >= 15.0:
                self._last_auto_resolve = now_mono
                incident_service.auto_resolve_inactive_incidents(db, deploy_cutoff=deploy_cutoff)

            # 5. Commit everything in ONE atomic transaction
            db.commit()

            # 6. Metrics & Guardrail Evaluation
            t_batch_ms = (time.perf_counter() - t_start) * 1000.0
            lags = [(now_utc - _ensure_utc(e.timestamp)).total_seconds() * 1000.0 for e in unassigned_events]
            p50_lag = float(np.percentile(lags, 50)) if lags else 0.0
            p95_lag = float(np.percentile(lags, 95)) if lags else 0.0
            max_lag = float(max(lags)) if lags else 0.0

            self.metrics_data["lag_p50_ms"] = p50_lag
            self.metrics_data["lag_p95_ms"] = p95_lag
            self.metrics_data["lag_max_ms"] = max_lag
            self.metrics_data["last_batch_duration_ms"] = t_batch_ms
            self.metrics_data["batches_processed"] += 1
            self.metrics_data["events_processed"] += len(unassigned_db_events)

            # Check remaining unassigned and oldest unassigned age
            unassigned_query = db.query(cutoff_col).filter(
                DBCloudEvent.incident_id.is_(None),
                cutoff_col >= deploy_cutoff
            ).order_by(cutoff_col.asc())

            unassigned_count = unassigned_query.count()
            self.metrics_data["unassigned_count"] = unassigned_count

            oldest_first = unassigned_query.first()
            if oldest_first and oldest_first[0]:
                oldest_ts = oldest_first[0]
                if oldest_ts.tzinfo is None:
                    oldest_ts = oldest_ts.replace(tzinfo=timezone.utc)
                oldest_age = (now_utc - oldest_ts).total_seconds()
            else:
                oldest_age = 0.0
            self.metrics_data["oldest_unassigned_age_seconds"] = oldest_age

            # Guardrails warning triggers
            warn_lag_ms = config.correlator.lag_warning_threshold_seconds * 1000.0
            if max_lag > warn_lag_ms:
                logger.warning(
                    f"CORRELATOR GUARDRAIL ALERT: Max event lag {max_lag:.1f}ms exceeds threshold {warn_lag_ms:.1f}ms!"
                )
            if unassigned_count > config.correlator.unassigned_warning_threshold:
                logger.warning(
                    f"CORRELATOR GUARDRAIL ALERT: Unassigned backlog ({unassigned_count}) exceeds threshold {config.correlator.unassigned_warning_threshold}!"
                )

            return len(unassigned_db_events)
        except Exception as e:
            db.rollback()
            logger.warning(f"Error during correlator batch processing: {e}")
            raise
        finally:
            if manage_session:
                db.close()


# Global singleton correlator consumer instance
correlator_consumer = CorrelatorConsumer()
