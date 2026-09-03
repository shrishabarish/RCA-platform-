import sys
from pathlib import Path
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.models.event import CloudEvent, EventSource, Severity
from backend.app.main import app


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def sample_events():
    return [
        CloudEvent(
            event_id="evt_01",
            timestamp=datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc),
            source=EventSource.CLOUDWATCH,
            event_type="cpu_utilization_high",
            service="EC2",
            resource_id="i-test-01",
            metric="CPUUtilization",
            value=95.0,
            severity=Severity.HIGH,
            metadata={"role": "web"}
        ),
        CloudEvent(
            event_id="evt_02",
            timestamp=datetime(2026, 9, 1, 15, 0, 15, tzinfo=timezone.utc),
            source=EventSource.APPLICATION,
            event_type="api_latency_high",
            service="API",
            resource_id="api-gw-01",
            metric="Latency",
            value=2500.0,
            severity=Severity.HIGH,
            metadata={"endpoint": "/orders"}
        ),
        CloudEvent(
            event_id="evt_03",
            timestamp=datetime(2026, 9, 1, 15, 0, 30, tzinfo=timezone.utc),
            source=EventSource.APPLICATION,
            event_type="http_500_errors",
            service="Application",
            resource_id="web-app-01",
            metric="5xxRate",
            value=12.0,
            severity=Severity.CRITICAL,
            metadata={"env": "prod"}
        )
    ]
