from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from rca_v2.models import SourceHealthStatus


class SourceHealthTracker:
    """Tracks source health states over time per telemetry source."""

    def __init__(self, records: Optional[List[Any]] = None):
        # source -> sorted list of (datetime, status)
        self.history: Dict[str, List[tuple]] = {}
        if records:
            for r in records:
                source = getattr(r, "source", None) or r.get("source")
                ts_str = getattr(r, "timestamp", None) or r.get("timestamp")
                status_str = getattr(r, "status", None) or r.get("status")
                
                try:
                    dt = datetime.fromisoformat(ts_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = datetime.min.replace(tzinfo=timezone.utc)
                    
                status = SourceHealthStatus(status_str.upper()) if status_str else SourceHealthStatus.HEALTHY
                if source not in self.history:
                    self.history[source] = []
                self.history[source].append((dt, status))

        for s in self.history:
            self.history[s].sort(key=lambda x: x[0])

    def get_source_status(self, source: str, as_of: Optional[datetime] = None) -> SourceHealthStatus:
        if source not in self.history:
            return SourceHealthStatus.HEALTHY  # Default healthy if unmentioned

        records = self.history[source]
        if as_of is None:
            # Latest known status
            return records[-1][1]

        as_of_utc = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        effective = SourceHealthStatus.HEALTHY
        for dt, st in records:
            if dt <= as_of_utc:
                effective = st
            else:
                break
        return effective

    def get_summary(self) -> Dict[str, str]:
        return {s: records[-1][1].value for s, records in self.history.items()}
