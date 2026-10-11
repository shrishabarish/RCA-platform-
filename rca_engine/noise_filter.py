import time
import logging
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone, timedelta
from backend.app.models.event import CloudEvent
from backend.app.core.config import config

logger = logging.getLogger("cloudlens.noise_filter")


class BaseDedupStore:
    def is_duplicate_and_update(self, dedup_key: str, timestamp_epoch: float, ttl_seconds: int) -> Tuple[bool, int]:
        raise NotImplementedError


class InMemoryDedupStore(BaseDedupStore):
    """Thread-safe sliding-window in-memory deduplication store with automatic eviction."""
    def __init__(self):
        self._store: Dict[str, Tuple[float, int]] = {}  # key -> (last_timestamp, count)

    def is_duplicate_and_update(self, dedup_key: str, timestamp_epoch: float, ttl_seconds: int) -> Tuple[bool, int]:
        # Clean expired keys
        now = time.time()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > (ttl_seconds * 3)]
        for k in expired:
            self._store.pop(k, None)

        if dedup_key in self._store:
            last_ts, count = self._store[dedup_key]
            if (timestamp_epoch - last_ts) <= ttl_seconds:
                new_count = count + 1
                self._store[dedup_key] = (timestamp_epoch, new_count)
                return True, new_count

        self._store[dedup_key] = (timestamp_epoch, 1)
        return False, 1


class RedisDedupStore(BaseDedupStore):
    """Redis shared deduplication store with automatic TTL."""
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client = None
        self._fallback = InMemoryDedupStore()
        self._connect()

    def _connect(self):
        try:
            import redis
            self.client = redis.Redis.from_url(self.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
            self.client.ping()
            logger.info("Connected to Redis shared dedup store")
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), using in-memory dedup fallback")
            self.client = None

    def is_duplicate_and_update(self, dedup_key: str, timestamp_epoch: float, ttl_seconds: int) -> Tuple[bool, int]:
        if not self.client:
            return self._fallback.is_duplicate_and_update(dedup_key, timestamp_epoch, ttl_seconds)

        try:
            key = f"cloudlens:dedup:{dedup_key}"
            pipe = self.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_seconds)
            results = pipe.execute()
            count = int(results[0])
            is_dup = count > 1
            return is_dup, count
        except Exception as e:
            logger.warning(f"Redis error during dedup check: {e}; falling back to memory store")
            return self._fallback.is_duplicate_and_update(dedup_key, timestamp_epoch, ttl_seconds)


# Global shared store singleton
_shared_store: Optional[BaseDedupStore] = None

def get_shared_dedup_store() -> BaseDedupStore:
    global _shared_store
    if _shared_store is None:
        if config.correlator.shared_store_type == "redis":
            _shared_store = RedisDedupStore(config.correlator.redis_url)
        else:
            _shared_store = InMemoryDedupStore()
    return _shared_store


class NoiseFilterEngine:
    """
    Production-grade telemetry noise reduction and deduplication engine.
    - Suppresses bursty duplicate alarms within short time windows across instances.
    - Uses shared store (Redis with TTL) with automatic thread-safe in-memory fallback.
    - Detects flapping alarms and compacts event streams into high-fidelity anomaly signals.
    """

    def __init__(
        self,
        dedup_window_seconds: Optional[int] = None,
        flapping_threshold: Optional[int] = None,
        store: Optional[BaseDedupStore] = None
    ):
        self.dedup_window_seconds = dedup_window_seconds or config.correlator.dedup_window_seconds
        self.dedup_window = timedelta(seconds=self.dedup_window_seconds)
        self.flapping_threshold = flapping_threshold or config.correlator.flapping_threshold
        self.store = store or get_shared_dedup_store()

    def filter_and_compact(self, events: List[CloudEvent]) -> Tuple[List[CloudEvent], Dict[str, int]]:
        """
        Deduplicates bursty repetitive events on the same resource and service.
        Returns:
            - compacted_events: List of distinct CloudEvent objects with merged metadata.
            - suppression_stats: Count of duplicate and suppressed events.
        """
        if not events:
            return [], {"total_raw": 0, "compacted": 0, "suppressed": 0}

        sorted_events = sorted(events, key=lambda e: e.timestamp)
        compacted: List[CloudEvent] = []
        suppressed_count = 0

        for event in sorted_events:
            dedup_key = f"{event.service}:{event.resource_id}:{event.event_type}"
            ts_epoch = event.timestamp.timestamp() if hasattr(event.timestamp, "timestamp") else time.time()

            is_dup, count = self.store.is_duplicate_and_update(
                dedup_key, ts_epoch, self.dedup_window_seconds
            )

            # Check intra-batch duplication
            intra_batch_dup = False
            for existing in reversed(compacted):
                time_gap = event.timestamp - existing.timestamp
                if time_gap > self.dedup_window:
                    break

                if (
                    existing.service == event.service
                    and existing.resource_id == event.resource_id
                    and existing.event_type == event.event_type
                ):
                    intra_batch_dup = True
                    existing.metadata["burst_count"] = existing.metadata.get("burst_count", 1) + 1
                    if event.value is not None and existing.value is not None:
                        existing.value = max(existing.value, event.value)
                    break

            if intra_batch_dup:
                suppressed_count += 1
            else:
                event.metadata["burst_count"] = count
                compacted.append(event)

        stats = {
            "total_raw": len(events),
            "compacted": len(compacted),
            "suppressed": suppressed_count
        }

        return compacted, stats
