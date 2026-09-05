import json
import uuid
from pathlib import Path
from typing import List, Union, Dict, Any
from datetime import datetime, timezone
from backend.app.models.event import CloudEvent


def normalize_event(raw_data: Union[Dict[str, Any], CloudEvent]) -> CloudEvent:
    """
    Validates and normalizes a single raw dictionary or CloudEvent object.
    """
    if isinstance(raw_data, CloudEvent):
        return raw_data
    return CloudEvent(**raw_data)


def load_synthetic_events(filepath: Union[str, Path]) -> List[CloudEvent]:
    """
    Reads a JSON file of raw event dicts and validates/normalizes
    each into a CloudEvent. Invalid events are skipped and logged.
    """
    raw_path = Path(filepath)
    if not raw_path.exists():
        raise FileNotFoundError(f"Synthetic events file not found: {raw_path}")

    with raw_path.open("r", encoding="utf-8") as f:
        raw_events = json.load(f)

    normalized: List[CloudEvent] = []
    for raw in raw_events:
        try:
            event = normalize_event(raw)
            normalized.append(event)
        except Exception as e:
            print(f"Skipping invalid event {raw.get('event_id', 'unknown')}: {e}")

    return normalized


# ======================================================================
# External Webhook Normalizer
# Maps structured JSON logs from external apps (e.g. AWS failure app)
# into CloudEvent objects for the RCA engine
# ======================================================================

# Map external service names -> canonical RCA topology node names
EXTERNAL_SERVICE_MAP = {
    "code-review-backend": "API",
    "codereview-backend": "API",
    "backend": "API",
    "frontend": "Application",
    "code-review-frontend": "Application",
    "postgres": "DynamoDB",
    "postgresql": "DynamoDB",
    "mongodb": "Worker",
}

# Map external event names -> RCA event types and affected service overrides
EVENT_TYPE_MAP = {
    "DATABASE_CONNECTION_ERROR": {"event_type": "db_connection_refused", "service_override": "DynamoDB", "metric": "ConnectionRefusalCount", "value": 1.0},
    "DATABASE_QUERY_ERROR": {"event_type": "db_query_error", "service_override": "DynamoDB", "metric": "QueryErrorCount", "value": 1.0},
    "DATABASE_HIGH_LATENCY": {"event_type": "db_latency_high", "service_override": "DynamoDB", "metric": "QueryLatencyMs"},
    "FAULT_INJECTED_API_ERROR": {"event_type": "http_500_internal_error", "service_override": "API", "metric": "5xxErrorCount", "value": 1.0},
    "HTTP_REQUEST_COMPLETED": {"event_type": "http_request_error", "metric": "HttpErrorCount", "value": 1.0},
    "FAULT_INJECTED": {"event_type": "fault_injection_marker", "metric": "FaultInjectionCount", "value": 1.0},
    "SERVICE_CRASH_INJECTED": {"event_type": "service_crash", "service_override": "EC2", "metric": "CrashCount", "value": 1.0},
}

# Map external log levels -> RCA severity
LEVEL_TO_SEVERITY = {
    "CRITICAL": "CRITICAL",
    "ERROR": "HIGH",
    "WARN": "MEDIUM",
    "WARNING": "MEDIUM",
    "INFO": "LOW",
}


def normalize_external_webhook(raw_log: Dict[str, Any]) -> CloudEvent:
    """
    Converts a structured JSON log from an external app (e.g. AWS failure
    code-review-backend) into a CloudEvent for the RCA engine.

    Expected input format (from structuredLogger.js):
    {
      "timestamp": "2026-09-05T05:30:00.000Z",
      "service": "code-review-backend",
      "level": "ERROR",
      "event": "DATABASE_CONNECTION_ERROR",
      "module": "DATABASE",
      "action": "...",
      "request_id": "req-xxx",
      "correlation_id": "corr-xxx",
      "error_type": "DatabaseConnectionError",
      "error_message": "Connection refused",
      "latency_ms": 21,
      "is_fault_injected": true,
      "details": { ... }
    }
    """
    event_name = raw_log.get("event", "UNKNOWN_EVENT")
    event_info = EVENT_TYPE_MAP.get(event_name, {})

    # Determine the RCA service name
    raw_service = raw_log.get("service", "unknown")
    if event_info.get("service_override"):
        rca_service = event_info["service_override"]
    else:
        rca_service = EXTERNAL_SERVICE_MAP.get(raw_service.lower(), raw_service)

    # For HTTP_REQUEST_COMPLETED, only treat 500+ status codes as errors
    details = raw_log.get("details", {})
    if event_name == "HTTP_REQUEST_COMPLETED":
        status_code = details.get("statusCode", 200)
        if status_code < 400:
            # Not an error, but we still normalize it
            pass
        elif status_code >= 500:
            rca_service = "API"

    # Determine severity
    level = raw_log.get("level", "INFO").upper()
    severity = LEVEL_TO_SEVERITY.get(level, "MEDIUM")

    # Determine metric value
    metric = event_info.get("metric", raw_log.get("error_type", "ExternalError"))
    value = event_info.get("value", 1.0)

    # If latency is present, use it as the value for latency events
    latency = raw_log.get("latency_ms")
    if latency and "latency" in event_name.lower():
        value = float(latency)
    elif latency and latency > 2000:
        # High latency is itself an anomaly signal
        value = float(latency)

    # Parse timestamp
    ts_raw = raw_log.get("timestamp")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else datetime.now(timezone.utc)
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)

    # Build metadata with all the rich context from the external log
    metadata = {
        "source_app": raw_service,
        "external_event": event_name,
        "module": raw_log.get("module", ""),
        "action": raw_log.get("action", ""),
        "correlation_id": raw_log.get("correlation_id", ""),
        "request_id": raw_log.get("request_id", ""),
        "error_type": raw_log.get("error_type", ""),
        "error_message": raw_log.get("error_message", ""),
        "is_fault_injected": raw_log.get("is_fault_injected", False),
        "environment": raw_log.get("environment", ""),
    }
    if details:
        metadata["details"] = details
    if latency:
        metadata["latency_ms"] = latency

    return CloudEvent(
        event_id=f"ext_{uuid.uuid4().hex[:8]}",
        timestamp=ts,
        source="application",
        event_type=event_info.get("event_type", event_name.lower()),
        service=rca_service,
        resource_id=f"{raw_service}:{raw_log.get('module', 'app').lower()}",
        metric=metric,
        value=value,
        severity=severity,
        metadata=metadata,
    )