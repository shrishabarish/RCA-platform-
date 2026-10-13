import os
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from backend.app.main import app

client = TestClient(app)


def assert_no_confidence_key(data, path=""):
    """Recursively asserts that no dictionary key in data is named 'confidence'."""
    if isinstance(data, dict):
        for k, v in data.items():
            current_path = f"{path}.{k}" if path else k
            assert k.lower() != "confidence", f"Found forbidden key 'confidence' at response path: {current_path}"
            assert_no_confidence_key(v, current_path)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            assert_no_confidence_key(item, f"{path}[{idx}]")


def test_no_confidence_key_across_api_endpoints():
    """Tests that no GET endpoint under /api/** returns a key named 'confidence'."""
    endpoints_to_check = [
        "/api/health",
        "/api/ready",
        "/api/aws/status",
        "/api/scenarios",
        "/api/topology",
        "/api/events",
        "/api/incidents",
        "/api/metrics",
        "/api/cloud/status",
        "/api/aws/config",
        "/api/s3/reports",
        "/api/sns/status",
        "/api/sqs/status",
        "/api/events/webhook/log",
    ]

    for ep in endpoints_to_check:
        resp = client.get(ep)
        assert resp.status_code in (200, 204), f"Endpoint {ep} returned status {resp.status_code}"
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            assert_no_confidence_key(data, path=ep)


def test_diagnose_endpoint_has_no_confidence_key():
    """Simulates a scenario and checks that /api/incidents/{id}/diagnose returns NO 'confidence' key."""
    sc_resp = client.get("/api/scenarios")
    assert sc_resp.status_code == 200
    scenarios = sc_resp.json()
    assert len(scenarios) > 0
    sc_id = scenarios[0]["id"]

    sim_resp = client.post(f"/api/scenarios/{sc_id}/simulate")
    assert sim_resp.status_code == 200
    
    inc_resp = client.get("/api/incidents")
    assert inc_resp.status_code == 200
    incidents = inc_resp.json()
    assert len(incidents) > 0
    inc_id = incidents[0]["incident_id"]

    diag_resp = client.get(f"/api/incidents/{inc_id}/diagnose")
    assert diag_resp.status_code == 200
    diag_data = diag_resp.json()
    assert_no_confidence_key(diag_data, path=f"/api/incidents/{inc_id}/diagnose")
    assert "ranking_score" in diag_data["baseline_engine"]
