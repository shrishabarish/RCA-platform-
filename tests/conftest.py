import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Pattern-based safety configuration reading from environment
PROD_HOST_PATTERN = os.getenv("PROD_DB_HOST_PATTERN", "rds.amazonaws.com")

# Switch environment to isolated test settings BEFORE loading any backend modules
TEST_DB_PATH = PROJECT_ROOT / "test_cloudlens.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["AWS_ACCESS_KEY_ID"] = "mock-testing-key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock-testing-secret"
os.environ["AWS_DEFAULT_REGION"] = "eu-north-1"
os.environ["APP_ENV"] = "testing"


from backend.app.core.safety import check_database_safety, print_database_host


def pytest_configure(config):
    """Safety guard: pattern-based detection to prevent accidental production connection."""
    db_url = os.environ.get("DATABASE_URL", "")
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    if db_url:
        try:
            check_database_safety(db_url, entry_point="test_config")
        except RuntimeError as e:
            pytest.exit(str(e), returncode=1)
    if aws_key and aws_key.startswith("AKIA") and not aws_key.startswith("mock"):
        pytest.exit("FATAL SAFETY GUARD: Real AWS credentials detected in test execution environment!", returncode=1)


import socket

def _is_postgres_running():
    try:
        s = socket.create_connection(("localhost", 5432), timeout=0.2)
        s.close()
        return True
    except OSError:
        return False

PG_DEPENDENT_FILES = {
    "test_alembic_schema_diff.py",
    "test_integration_proofs.py",
    "test_postgres_integration.py",
    "test_sigkill_recovery.py"
}

def pytest_collection_modifyitems(session, config, items):
    """Mark PostgreSQL-dependent tests skip-if-unavailable."""
    if not _is_postgres_running():
        skip_pg = pytest.mark.skip(reason="Local PostgreSQL not running on localhost:5432 (zero-DB evaluation mode)")
        for item in items:
            if Path(item.fspath).name in PG_DEPENDENT_FILES:
                item.add_marker(skip_pg)


from backend.app.models.event import CloudEvent, EventSource, Severity
from backend.app.db.database import Base, engine
from backend.app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Initializes a separate isolated test database and cleans up on session exit."""
    Base.metadata.create_all(bind=engine)
    from backend.app.services.correlator_consumer import correlator_consumer
    correlator_consumer.start()
    yield
    correlator_consumer.stop(timeout=1.0)
    from backend.app.services.outbox_worker import outbox_worker
    outbox_worker.stop(timeout=1.0)
    Base.metadata.drop_all(bind=engine)
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def mock_aws_services(monkeypatch):
    """Guarantees all AWS calls in tests are safely stubbed and never touch real AWS."""
    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    mock_s3.create_bucket.return_value = {}
    mock_s3.put_object.return_value = {"ETag": "mock-etag"}
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"incident_summary": {"probable_root_cause": "DynamoDB"}}')}
    mock_s3.list_objects_v2.return_value = {"Contents": []}

    mock_sns = MagicMock()
    mock_sns.publish.return_value = {"MessageId": "mock-sns-msg-123"}
    mock_sns.list_topics.return_value = {"Topics": [{"TopicArn": "arn:aws:sns:eu-north-1:123456789012:test-topic"}]}
    mock_sns.create_topic.return_value = {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:test-topic"}

    mock_sqs = MagicMock()
    mock_sqs.send_message.return_value = {"MessageId": "mock-sqs-msg-123"}
    mock_sqs.receive_message.return_value = {"Messages": []}
    mock_sqs.delete_message.return_value = {}

    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {"Reservations": []}
    mock_ec2.reboot_instances.return_value = {}

    mock_cw = MagicMock()
    mock_cw.describe_alarms.return_value = {"MetricAlarms": []}
    mock_cw.put_metric_data.return_value = {}

    def fake_boto3_client(service_name, *args, **kwargs):
        if service_name == "s3":
            return mock_s3
        elif service_name == "sns":
            return mock_sns
        elif service_name == "sqs":
            return mock_sqs
        elif service_name == "ec2":
            return mock_ec2
        elif service_name == "cloudwatch":
            return mock_cw
        return MagicMock()

    import boto3
    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    # Attach to existing module singletons
    from event_processor.s3_archiver import s3_archiver
    from event_processor.sns_notifier import sns_notifier
    from event_processor.sqs_consumer import sqs_consumer
    from event_processor.aws_discovery import aws_discovery

    s3_archiver.client = mock_s3
    s3_archiver.is_connected = True
    sns_notifier.client = mock_sns
    sns_notifier.is_connected = True
    sqs_consumer.client = mock_sqs
    sqs_consumer.is_connected = True
    aws_discovery.ec2_client = mock_ec2


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
