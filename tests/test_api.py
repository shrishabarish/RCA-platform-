import pytest


def test_api_health(test_client):
    res = test_client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "active_incidents" in data


def test_api_scenarios_list(test_client):
    res = test_client.get("/api/scenarios")
    assert res.status_code == 200
    scenarios = res.json()
    assert len(scenarios) >= 4
    ids = [s["id"] for s in scenarios]
    assert "cascade" in ids
    assert "db_exhaustion" in ids


def test_api_simulate_scenario(test_client):
    res = test_client.post("/api/scenarios/db_exhaustion/simulate")
    assert res.status_code == 200
    data = res.json()
    assert data["scenario"] == "db_exhaustion"
    assert data["events_loaded"] > 0
    assert data["incidents_detected"] > 0


def test_api_topology(test_client):
    res = test_client.get("/api/topology")
    assert res.status_code == 200
    data = res.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) >= 5


def test_api_incidents(test_client):
    test_client.post("/api/scenarios/cascade/simulate")
    res = test_client.get("/api/incidents")
    assert res.status_code == 200
    incidents = res.json()
    assert len(incidents) > 0
    inc_id = incidents[0]["incident_id"]

    # Test single incident detail
    detail_res = test_client.get(f"/api/incidents/{inc_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["probable_root_cause"] == "EC2"

    # Test remediation
    rem_res = test_client.get(f"/api/incidents/{inc_id}/remediation")
    assert rem_res.status_code == 200
    rem = rem_res.json()
    assert rem["target_service"] == "EC2"


def test_api_ingest_event(test_client):
    event_payload = {
        "event_id": "api_test_evt_999",
        "timestamp": "2026-09-01T20:00:00Z",
        "source": "application",
        "event_type": "high_error_spike",
        "service": "Worker",
        "resource_id": "worker-pool-1",
        "severity": "CRITICAL",
        "metadata": {"test": True}
    }
    res = test_client.post("/api/events", json=event_payload)
    assert res.status_code == 201
    created = res.json()
    assert created["event_id"] == "api_test_evt_999"
