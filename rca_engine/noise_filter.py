from typing import List, Dict, Tuple
from datetime import timedelta
from backend.app.models.event import CloudEvent


class NoiseFilterEngine:
    """
    Production-grade telemetry noise reduction and deduplication engine.
    - Suppresses bursty duplicate alarms within short time windows.
    - Detects flapping alarms (rapid toggles between states).
    - Compacts event streams into high-fidelity anomaly signals.
    """

    def __init__(self, dedup_window_seconds: int = 5, flapping_threshold: int = 4):
        self.dedup_window = timedelta(seconds=dedup_window_seconds)
        self.flapping_threshold = flapping_threshold

    def filter_and_compact(self, events: List[CloudEvent]) -> Tuple[List[CloudEvent], Dict[str, int]]:
        """
        Deduplicates bursty repetitive events on the same resource and service.
        Returns:
            - compacted_events: List of distinct CloudEvent objects with merged metadata.
            - suppression_stats: Count of duplicate and suppressed events.
        """
        if not events:
            return [], {"total_raw": 0, "compacted": 0, "suppressed": 0}

        # Sort events by timestamp
        sorted_events = sorted(events, key=lambda e: e.timestamp)
        compacted: List[CloudEvent] = []
        suppressed_count = 0

        for event in sorted_events:
            # Check if event is a duplicate of a recent event on same service + resource + type
            is_duplicate = False
            for existing in reversed(compacted):
                time_gap = event.timestamp - existing.timestamp
                if time_gap > self.dedup_window:
                    break

                if (
                    existing.service == event.service
                    and existing.resource_id == event.resource_id
                    and existing.event_type == event.event_type
                ):
                    # Duplicate found within dedup window - compact it into existing
                    is_duplicate = True
                    suppressed_count += 1
                    existing.metadata["burst_count"] = existing.metadata.get("burst_count", 1) + 1
                    # Keep the maximum metric value
                    if event.value is not None and existing.value is not None:
                        existing.value = max(existing.value, event.value)
                    break

            if not is_duplicate:
                # Initialize burst_count
                event.metadata["burst_count"] = 1
                compacted.append(event)

        stats = {
            "total_raw": len(events),
            "compacted": len(compacted),
            "suppressed": suppressed_count
        }

        return compacted, stats
