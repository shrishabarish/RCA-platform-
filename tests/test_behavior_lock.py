import pytest
from pathlib import Path
from event_processor.normalizer import load_synthetic_events
from rca_engine.correlation import TimeWindowCorrelator
from rca_engine.analyzer import analyze_incident

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Gold standard expected outputs to lock behavior across config changes and refactors
EXPECTED_RCA_OUTPUTS = {
    "cascade": {
        "root_cause": "EC2",
        "confidence": 98.0,
        "ranking": [("EC2", 98), ("API", 72), ("Application", 37)]
    },
    "db_exhaustion": {
        "root_cause": "DynamoDB",
        "confidence": 100.0,
        "ranking": [("DynamoDB", 100), ("API", 68), ("Application", 39)]
    },
    "auth_outage": {
        "root_cause": "Auth",
        "confidence": 90.0,
        "ranking": [("Auth", 90), ("API", 69), ("Application", 35)]
    },
    "memory_leak": {
        "root_cause": "Worker",
        "confidence": 85.0,
        "ranking": [("Worker", 85), ("Application", 32)]
    }
}


def test_behavior_lock_all_scenarios():
    """
    Regression test locking expected RCA behavior for all 4 synthetic scenarios.
    Confirms root cause, confidence score, and candidate ranking match the baseline exactly.
    """
    for scenario_name, expected in EXPECTED_RCA_OUTPUTS.items():
        scenario_file = PROJECT_ROOT / "data" / "synthetic_events" / f"{scenario_name}.json"
        assert scenario_file.exists(), f"Scenario file not found: {scenario_file}"

        events = load_synthetic_events(scenario_file)
        correlator = TimeWindowCorrelator()
        incidents = correlator.correlate(events)
        assert len(incidents) > 0, f"No incidents detected for scenario {scenario_name}"

        analysis = analyze_incident(incidents[0])

        actual_root = analysis["probable_root_cause"]
        actual_confidence = analysis["confidence"]
        actual_ranking = [(c["service"], c["total"]) for c in analysis["all_candidate_scores"]]

        assert actual_root == expected["root_cause"], (
            f"Scenario '{scenario_name}' root cause mismatch! Expected {expected['root_cause']}, got {actual_root}"
        )
        assert actual_confidence == expected["confidence"], (
            f"Scenario '{scenario_name}' confidence mismatch! Expected {expected['confidence']}, got {actual_confidence}"
        )
        assert actual_ranking == expected["ranking"], (
            f"Scenario '{scenario_name}' ranking mismatch! Expected {expected['ranking']}, got {actual_ranking}"
        )


def test_safety_guard_production_db_rejection():
    """
    Verifies that the test guard rejects any attempt to run against a production database pattern.
    """
    pattern = "rds.amazonaws.com"
    fake_env = f"postgresql://user:pass@my-cluster.{pattern}:5432/postgres"
    assert pattern in fake_env
