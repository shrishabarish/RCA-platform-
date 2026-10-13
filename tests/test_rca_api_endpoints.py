import pytest
from fastapi.testclient import TestClient
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


def test_get_rca_cases_endpoint():
    resp = client.get("/api/rca/cases")
    assert resp.status_code == 200
    cases = resp.json()
    assert isinstance(cases, list)
    assert len(cases) >= 30, f"Expected at least 30 synthetic cases, got {len(cases)}"
    assert_no_confidence_key(cases, path="/api/rca/cases")

    first = cases[0]
    assert "case_id" in first
    assert "category" in first
    assert "node_count" in first
    assert "event_count" in first
    assert first.get("is_synthetic") is True


def test_get_rca_diagnose_endpoints():
    case_id = "indep_001_slow_burn_decoy_6node"

    # Test rca_v3 diagnosis
    resp_v3 = client.get(f"/api/rca/diagnose?case_id={case_id}&engine=rca_v3")
    assert resp_v3.status_code == 200
    data_v3 = resp_v3.json()
    assert_no_confidence_key(data_v3, path="/api/rca/diagnose?engine=rca_v3")
    assert data_v3["engine"] == "rca_v3"
    assert "verdict" in data_v3
    assert "predicted_root_causes" in data_v3
    assert "ranking_scores" in data_v3
    assert "note" in data_v3

    # Test current_scorer diagnosis
    resp_cs = client.get(f"/api/rca/diagnose?case_id={case_id}&engine=current_scorer")
    assert resp_cs.status_code == 200
    data_cs = resp_cs.json()
    assert_no_confidence_key(data_cs, path="/api/rca/diagnose?engine=current_scorer")
    assert data_cs["engine"] == "current_scorer"
    assert "verdict" in data_cs
    assert "predicted_root_causes" in data_cs
    assert "ranking_scores" in data_cs


def test_get_rca_replay_endpoint():
    case_id = "indep_001_slow_burn_decoy_6node"
    resp = client.get(f"/api/rca/replay?case_id={case_id}&engine=rca_v3")
    assert resp.status_code == 200
    data = resp.json()
    assert_no_confidence_key(data, path="/api/rca/replay")
    assert data["case_id"] == case_id
    assert data["is_synthetic"] is True
    assert data["node_count"] == 6
    assert data["frame_count"] > 0
    assert len(data["frames"]) == data["frame_count"]

    frame = data["frames"][0]
    assert "frame_index" in frame
    assert "timestamp" in frame
    assert "current_event" in frame
    assert "verdict" in frame
    assert "ranking_score" in frame


def test_get_rca_replay_12node_case():
    case_id = "indep_004_slow_burn_decoy_12node"
    resp = client.get(f"/api/rca/replay?case_id={case_id}&engine=rca_v3")
    assert resp.status_code == 200
    data = resp.json()
    assert_no_confidence_key(data, path="/api/rca/replay_12node")
    assert data["node_count"] == 12
    assert data["frame_count"] > 0


def test_get_rca_compare_endpoint():
    case_id = "indep_001_slow_burn_decoy_6node"
    resp = client.get(f"/api/rca/compare?case_id={case_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert_no_confidence_key(data, path="/api/rca/compare")
    assert "current_scorer" in data
    assert "rca_v3" in data
    assert "differences" in data
    assert "diff_summary_sentence" in data
    assert "plain_note" in data
    assert "rca_v3's added value" in data["plain_note"]


def test_get_rca_compare_12node_case():
    case_id = "indep_004_slow_burn_decoy_12node"
    resp = client.get(f"/api/rca/compare?case_id={case_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert_no_confidence_key(data, path="/api/rca/compare_12node")
    assert data["node_count"] == 12
    assert "diff_summary_sentence" in data


def test_get_eval_summary_endpoint():
    resp = client.get("/api/eval/summary")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) > 0, "Evaluation summary should return rows from evaluation/results"
    assert_no_confidence_key(rows, path="/api/eval/summary")

    first = rows[0]
    assert "engine" in first
    assert "split" in first
    assert "denominator" in first
    assert "csv_path" in first
    assert "commit" in first
    assert "top1_accuracy_pct" in first
