import pytest
from backend.app.models.event import CloudEvent, EventSource, Severity
from rca_engine.causal_graph import causal_graph_engine
from event_processor.aws_discovery import aws_discovery
from datetime import datetime, timezone


def test_dynamic_service_registration():
    reg = causal_graph_engine.register_service(
        service_name="payment-svc",
        service_type="service",
        label="Payment Gateway Microservice",
        criticality=0.9
    )
    assert reg["service"] == "payment-svc"
    assert "payment-svc" in causal_graph_engine.graph

    # Register dependency: payment-svc depends on DynamoDB
    dep = causal_graph_engine.register_dependency("payment-svc", "DynamoDB")
    assert dep["source"] == "payment-svc"
    assert dep["target"] == "DynamoDB"

    # Verify blast radius of DynamoDB includes payment-svc
    blast = causal_graph_engine.get_blast_radius("DynamoDB")
    assert "payment-svc" in blast


def test_auto_layout_nodes():
    positions = causal_graph_engine.auto_layout_nodes(width=860, height=460)
    assert len(positions) >= 6

    for node_id, pos in positions.items():
        assert "x" in pos
        assert "y" in pos
        assert "icon" in pos
        assert 50 <= pos["x"] <= 850
        assert 40 <= pos["y"] <= 460


def test_auto_register_from_telemetry_event():
    event = CloudEvent(
        event_id="dyn_evt_001",
        timestamp=datetime.now(timezone.utc),
        source=EventSource.APPLICATION,
        event_type="high_error_rate",
        service="fraud-detector-worker",
        resource_id="pod-fraud-01",
        severity=Severity.HIGH,
        metadata={"dependency": "payment-svc"}
    )
    svc = causal_graph_engine.auto_register_from_event(event)
    assert svc == "fraud-detector-worker"
    assert "fraud-detector-worker" in causal_graph_engine.graph

    # Check that fraud-detector-worker depends on payment-svc
    direct_deps = causal_graph_engine.get_direct_dependents("payment-svc")
    assert "fraud-detector-worker" in direct_deps


def test_aws_discovery_service():
    result = aws_discovery.discover_and_register_cloud_assets()
    assert result is not None
    assert result["status"] in ("success", "simulated_success")
    assert result["total_assets_discovered"] >= 1
    assert "graph_topology" in result


def test_api_dynamic_services_endpoint(test_client):
    payload = {
        "service": "recommendation-api",
        "type": "service",
        "label": "AI Recommendation Service",
        "criticality": 0.8,
        "depends_on": ["DynamoDB", "API"]
    }
    res = test_client.post("/api/topology/services", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "registered"
    assert data["service"] == "recommendation-api"

    # Verify present in topology
    topo = data["topology"]
    node_ids = [n["id"] for n in topo["nodes"]]
    assert "recommendation-api" in node_ids


def test_api_topology_discover_endpoint(test_client):
    res = test_client.post("/api/topology/discover")
    assert res.status_code == 200
    data = res.json()
    assert "total_assets_discovered" in data
