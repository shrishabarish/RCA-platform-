import pytest
from event_processor.sns_notifier import sns_notifier
from event_processor.s3_archiver import s3_archiver
from event_processor.aws_config_collector import aws_config_collector
from event_processor.sqs_consumer import sqs_consumer
from rca_engine.remediation_executor import remediation_executor


def test_sns_notifier_alert():
    test_incident = {
        "incident_id": "inc_unit_test_01",
        "severity": "CRITICAL",
        "probable_root_cause": "EC2",
        "confidence": 94.0,
        "affected_services": ["API", "Application"],
        "remediation_suggestion": "Reboot instance and scale out ASG"
    }
    record = sns_notifier.publish_incident_alert(test_incident, s3_report_url="s3://cloudlens-incident-archive/test.json")
    assert record is not None
    assert record["status"] in ("SENT", "FAILED", "delivered", "simulated_success")


def test_s3_archiver():
    test_report = {
        "incident_id": "inc_unit_test_s3",
        "severity": "HIGH",
        "probable_root_cause": "DynamoDB",
        "confidence": 88.0,
        "affected_services": ["API"],
        "score_breakdown": {"temporal_correlation": 25, "dependency_relationship": 30},
        "reasoning_chain": ["1. Origin DynamoDB", "2. Cascaded to API"],
        "events": []
    }
    archive_res = s3_archiver.archive_incident(test_report)
    assert archive_res is not None
    assert archive_res["incident_id"] == "inc_unit_test_s3"
    assert archive_res["s3_key"] == "incidents/inc_unit_test_s3/post_mortem.json"

    # Test retrieval
    fetched = s3_archiver.get_incident_report("inc_unit_test_s3")
    assert fetched is not None
    assert fetched["incident_summary"]["probable_root_cause"] == "DynamoDB"


def test_aws_config_collector():
    events = aws_config_collector.get_simulated_config_events()
    assert len(events) >= 2
    assert any(e.service == "EC2" for e in events)
    assert any(e.service == "DynamoDB" for e in events)


def test_sqs_consumer_queue_record():
    test_payload = {
        "event_id": "sqs_test_evt_001",
        "service": "Worker",
        "event_type": "pod_evicted",
        "severity": "HIGH"
    }
    rec = sqs_consumer.send_event_to_queue(test_payload)
    assert rec is not None
    assert rec["status"] in ("queued", "buffered_simulated")


def test_remediation_executor():
    result = remediation_executor.execute_remediation(
        incident_id="inc_exec_test",
        root_cause="EC2",
        resource_id="i-testinstance123",
        dry_run=True
    )
    assert result["status"] == "SUCCESS"
    assert len(result["commands_executed"]) > 0
    assert result["safety_checks_passed"] is True
    assert result["mttr_seconds"] > 0


def test_cloud_status_endpoint(test_client):
    res = test_client.get("/api/cloud/status")
    assert res.status_code == 200
    data = res.json()
    assert "components" in data
    assert data["components"]["database"]["service"] == "AWS RDS (PostgreSQL)"
    assert data["components"]["audit_archive"]["service"] == "Amazon S3"
    assert data["components"]["notifications"]["service"] == "Amazon SNS"
    assert data["components"]["config_drift"]["service"] == "AWS Config"
    assert data["components"]["event_buffer"]["service"] == "Amazon SQS"


def test_s3_reports_endpoint(test_client):
    res = test_client.get("/api/s3/reports")
    assert res.status_code == 200
    reports = res.json()
    assert isinstance(reports, list)
