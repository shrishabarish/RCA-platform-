import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from backend.app.main import app
from backend.app.core.config import config, RemediationConfig
from backend.app.services.incident_service import incident_service
from backend.app.models.event import Incident, CloudEvent, Severity, IncidentStatus
from rca_v3.models import DiagnosisResult, Verdict, CandidateDiagnosis, StructuredExplanation


@pytest.fixture
def client():
    return TestClient(app)


def test_auto_remediation_defaults_to_false_in_config():
    """Verify that AUTO_REMEDIATION_ENABLED defaults to False via config."""
    # Test default model instantiation without environment override
    default_rem = RemediationConfig(auto_remediation_enabled=False)
    assert default_rem.auto_remediation_enabled is False
    assert config.remediation.auto_remediation_enabled is False


def test_mitigate_guard_fails_when_globally_disabled(client, monkeypatch):
    """Guard 1: Must fail closed with 403 when auto-remediation is disabled."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", False)

    incident_service.reset_state()
    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_01",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        ranking_score=95.0,
        events=[]
    )
    incident_service._active_incidents.append(inc)

    response = client.post(
        f"/api/incidents/{inc.incident_id}/mitigate",
        json={"human_approval_token": "HUMAN_APPROVED_ROOT_CONFIRMED"}
    )
    assert response.status_code == 403
    assert "globally disabled" in response.json()["detail"].lower()


def test_mitigate_guard_fails_on_bad_token(client, monkeypatch):
    """Guard 2: Must fail closed with 403 when approval token is bad or missing."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", True)

    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_02",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        events=[]
    )
    incident_service._active_incidents.append(inc)

    # Missing token
    resp_missing = client.post(f"/api/incidents/{inc.incident_id}/mitigate", json={})
    assert resp_missing.status_code == 403
    assert "human approval token" in resp_missing.json()["detail"].lower()

    # Bad token (constant-time compare fails)
    resp_bad = client.post(
        f"/api/incidents/{inc.incident_id}/mitigate",
        json={"human_approval_token": "FORGED_UNAUTHORIZED_TOKEN"}
    )
    assert resp_bad.status_code == 403
    assert "human approval token" in resp_bad.json()["detail"].lower()


def test_mitigate_guard_fails_when_source_unhealthy(client, monkeypatch):
    """Guard 3: Must fail closed with 403 when telemetry source is unhealthy."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr(
        incident_service,
        "get_source_health",
        lambda: [{"source": "cloudwatch", "status": "DOWN"}]
    )

    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_03",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        events=[]
    )
    incident_service._active_incidents.append(inc)

    response = client.post(
        f"/api/incidents/{inc.incident_id}/mitigate",
        json={"human_approval_token": config.remediation.human_approval_secret}
    )
    assert response.status_code == 403
    assert "source is unhealthy" in response.json()["detail"].lower()


def test_mitigate_guard_fails_on_non_confirmed_verdict(client, monkeypatch):
    """Guard 4: Must fail closed with 403 when RCA verdict is PROBABLE or INSUFFICIENT_EVIDENCE."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr(
        incident_service,
        "get_source_health",
        lambda: [{"source": "cloudwatch", "status": "HEALTHY"}]
    )

    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_04",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        events=[]
    )
    incident_service._active_incidents.append(inc)

    # Mock diagnose_v3 returning PROBABLE verdict
    mock_probable_result = DiagnosisResult(
        verdict=Verdict.PROBABLE,
        predicted_root_causes=["EC2"],
        root_hypotheses=[],
        candidate_diagnoses=[
            CandidateDiagnosis(
                service="EC2",
                ranking_score=68.0,
                earliest_event_time=now.isoformat(),
                rank=1
            )
        ],
        explanation=StructuredExplanation(
            verdict_reason="Test reason",
            evidence_table=[],
            contradictions=[],
            summary_text="Test summary"
        )
    )

    with patch("rca_v3.diagnose.diagnose", return_value=mock_probable_result):
        response = client.post(
            f"/api/incidents/{inc.incident_id}/mitigate",
            json={"human_approval_token": config.remediation.human_approval_secret}
        )
        assert response.status_code == 403
        assert "must be confirmed" in response.json()["detail"].lower()


def test_mitigate_guard_fails_on_unobserved_ancestors(client, monkeypatch):
    """Guard 5: Must fail closed with 403 when root cause has unobserved ancestors in causal topology."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr(
        incident_service,
        "get_source_health",
        lambda: [{"source": "cloudwatch", "status": "HEALTHY"}]
    )

    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_05",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        events=[]
    )
    incident_service._active_incidents.append(inc)

    # Mock diagnose_v3 returning CONFIRMED verdict but with is_unobserved = True
    mock_unobserved_result = DiagnosisResult(
        verdict=Verdict.CONFIRMED,
        predicted_root_causes=["EC2"],
        root_hypotheses=[],
        candidate_diagnoses=[
            CandidateDiagnosis(
                service="EC2",
                ranking_score=85.0,
                earliest_event_time=now.isoformat(),
                rank=1,
                is_unobserved=True  # Unobserved candidate
            )
        ],
        explanation=StructuredExplanation(
            verdict_reason="Confirmed with unobserved ancestor",
            evidence_table=[],
            contradictions=[],
            summary_text="Confirmed with unobserved ancestor"
        )
    )

    with patch("rca_v3.diagnose.diagnose", return_value=mock_unobserved_result):
        response = client.post(
            f"/api/incidents/{inc.incident_id}/mitigate",
            json={"human_approval_token": config.remediation.human_approval_secret}
        )
        assert response.status_code == 403
        assert "unobserved ancestors" in response.json()["detail"].lower()


def test_mitigate_happy_path_aws_mocked(client, monkeypatch):
    """Happy path: All 5 safety conditions met; AWS interactions mocked; succeeds with 200."""
    monkeypatch.setattr("backend.app.api.endpoints.AUTO_REMEDIATION_ENABLED", True)
    monkeypatch.setattr(
        incident_service,
        "get_source_health",
        lambda: [{"source": "cloudwatch", "status": "HEALTHY"}]
    )

    now = datetime.now(timezone.utc)
    inc = Incident(
        incident_id="inc_guard_test_happy",
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=now,
        updated_at=now,
        probable_root_cause="EC2",
        events=[]
    )
    incident_service._active_incidents.append(inc)

    mock_confirmed_result = DiagnosisResult(
        verdict=Verdict.CONFIRMED,
        predicted_root_causes=["EC2"],
        root_hypotheses=[],
        candidate_diagnoses=[
            CandidateDiagnosis(
                service="EC2",
                ranking_score=92.0,
                earliest_event_time=now.isoformat(),
                rank=1,
                is_unobserved=False
            )
        ],
        explanation=StructuredExplanation(
            verdict_reason="Confirmed root cause EC2",
            evidence_table=[],
            contradictions=[],
            summary_text="Confirmed root cause EC2"
        )
    )

    mock_mitigation_response = {
        "status": "success",
        "action_taken": "REBOOT_INSTANCE",
        "service": "EC2",
        "resource_id": "i-0abcdef1234567890",
        "mock_aws": True,
        "message": "EC2 instance reboot triggered successfully via mocked AWS Boto3 client."
    }

    with patch("rca_v3.diagnose.diagnose", return_value=mock_confirmed_result), \
         patch.object(incident_service, "mitigate_incident", return_value=mock_mitigation_response):
        
        response = client.post(
            f"/api/incidents/{inc.incident_id}/mitigate",
            json={"human_approval_token": config.remediation.human_approval_secret}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mock_aws"] is True
        assert data["action_taken"] == "REBOOT_INSTANCE"
