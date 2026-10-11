import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """
    Standardized structured JSON log formatter with correlation & incident context.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include contextual fields if present
        if hasattr(record, "correlation_id") and record.correlation_id:
            log_entry["correlation_id"] = record.correlation_id
        if hasattr(record, "incident_id") and record.incident_id:
            log_entry["incident_id"] = record.incident_id
        if hasattr(record, "service") and record.service:
            log_entry["service"] = record.service

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class SafeStreamHandler(logging.StreamHandler):
    """Stream handler that safely ignores writes to closed stdout streams."""
    def emit(self, record):
        try:
            if self.stream and not self.stream.closed:
                super().emit(record)
        except (ValueError, OSError):
            pass

    def handleError(self, record):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ValueError, OSError):
            return
        super().handleError(record)


def setup_structured_logging(level_name: str = "INFO"):
    """Configures structured JSON logging for all cloudlens loggers."""
    root_logger = logging.getLogger()
    handler = SafeStreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    # Avoid duplicate handlers
    root_logger.handlers = [handler]
    level = getattr(logging, level_name.upper(), logging.INFO)
    root_logger.setLevel(level)
