import threading
import time
from typing import Dict, Any, List
from collections import deque


class MetricsCollector:
    """Thread-safe collector for CloudLens operational stability metrics."""
    def __init__(self):
        self._lock = threading.Lock()
        self.events_received = 0
        self.duplicates_dropped = 0
        self.outbox_pending = 0
        self.outbox_failed = 0
        self.outbox_retries = 0
        self.queue_depth = 0

        # Rolling per-stage ingestion timings (last 100 samples each)
        self._timings: Dict[str, deque] = {
            "validation_ms": deque(maxlen=100),
            "db_insert_ms": deque(maxlen=100),
            "commit_ms": deque(maxlen=100),
            "total_ingest_ms": deque(maxlen=100)
        }

    def inc_events_received(self, count: int = 1):
        with self._lock:
            self.events_received += count

    def inc_duplicates_dropped(self, count: int = 1):
        with self._lock:
            self.duplicates_dropped += count

    def inc_outbox_pending(self, count: int = 1):
        with self._lock:
            self.outbox_pending += count

    def dec_outbox_pending(self, count: int = 1):
        with self._lock:
            self.outbox_pending = max(0, self.outbox_pending - count)

    def inc_outbox_failed(self, count: int = 1):
        with self._lock:
            self.outbox_failed += count

    def inc_outbox_retries(self, count: int = 1):
        with self._lock:
            self.outbox_retries += count

    def set_queue_depth(self, depth: int):
        with self._lock:
            self.queue_depth = depth

    def record_stage_timing(self, stage: str, dur_ms: float):
        with self._lock:
            if stage in self._timings:
                self._timings[stage].append(dur_ms)

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            stage_stats = {}
            for stage, dq in self._timings.items():
                if dq:
                    stage_stats[f"{stage}_avg"] = round(sum(dq) / len(dq), 2)
                    stage_stats[f"{stage}_last"] = round(dq[-1], 2)
                else:
                    stage_stats[f"{stage}_avg"] = 0.0
                    stage_stats[f"{stage}_last"] = 0.0

            base = {
                "events_received": self.events_received,
                "duplicates_dropped": self.duplicates_dropped,
                "outbox_pending": self.outbox_pending,
                "outbox_failed": self.outbox_failed,
                "outbox_retries": self.outbox_retries,
                "queue_depth": self.queue_depth,
                "ingestion_stages_ms": stage_stats
            }

        # Dynamic pool metrics
        try:
            from backend.app.db.database import engine
            pool = getattr(engine, "pool", None)
            if pool:
                base["database_pool"] = {
                    "size": getattr(pool, "size", lambda: 0)(),
                    "checked_in": getattr(pool, "checkedin", lambda: 0)(),
                    "checked_out": getattr(pool, "checkedout", lambda: 0)(),
                    "overflow": getattr(pool, "overflow", lambda: 0)()
                }
        except Exception:
            pass

        # Dynamic incident status & RCA staleness metrics
        try:
            from backend.app.services.incident_service import incident_service
            incs = incident_service._active_incidents
            open_cnt = sum(1 for i in incs if (i.status.value if hasattr(i.status, "value") else i.status) == "OPEN")
            res_cnt = sum(1 for i in incs if (i.status.value if hasattr(i.status, "value") else i.status) == "RESOLVED")
            pending_rca = sum(1 for i in incs if getattr(i, "analysis_status", "PENDING") == "PENDING")
            done_rca = sum(1 for i in incs if getattr(i, "analysis_status", "PENDING") == "DONE")
            stale_rca = sum(1 for i in incs if getattr(i, "analysis_status", "PENDING") == "STALE")

            base["incidents_by_status"] = {
                "OPEN": open_cnt,
                "RESOLVED": res_cnt,
                "TOTAL": len(incs)
            }
            base["rca_staleness"] = {
                "PENDING": pending_rca,
                "DONE": done_rca,
                "STALE": stale_rca
            }
        except Exception:
            pass

        return base


metrics = MetricsCollector()
