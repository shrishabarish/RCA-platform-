from backend.app.models.event import CloudEvent, EventSource, Severity
from datetime import datetime, timezone

def test_manual():
    e = CloudEvent(
        event_id="EVT001",
        timestamp=datetime.now(timezone.utc),
        source=EventSource.CLOUDWATCH,
        event_type="metric_alarm",
        service="EC2",
        resource_id="server-01",
        metric="CPUUtilization",
        value=96.0,
        severity=Severity.HIGH
    )
    print("CloudEvent created successfully:")
    print(e.model_dump_json(indent=2))

if __name__ == "__main__":
    test_manual()
