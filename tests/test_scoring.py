import pytest
from rca_engine.scoring import score_candidate
from rca_engine.analyzer import analyze_incident
from rca_engine.correlation import TimeWindowCorrelator


def test_scoring_assigns_higher_score_to_root(sample_events):
    score_ec2 = score_candidate("EC2", sample_events)
    score_api = score_candidate("API", sample_events)
    score_app = score_candidate("Application", sample_events)

    # EC2 starts earliest (25 pts) and affects API
    assert score_ec2["total"] > score_api["total"]
    assert score_ec2["total"] > score_app["total"]
    assert score_ec2["breakdown"]["temporal_correlation"] == 25
    assert score_api["breakdown"]["temporal_correlation"] < score_ec2["breakdown"]["temporal_correlation"]
    assert score_app["breakdown"]["temporal_correlation"] < score_ec2["breakdown"]["temporal_correlation"]


def test_analyze_incident_identifies_root_cause(sample_events):
    correlator = TimeWindowCorrelator(time_window_seconds=60)
    incidents = correlator.correlate(sample_events)
    assert len(incidents) == 1

    analysis = analyze_incident(incidents[0])
    assert analysis["probable_root_cause"] == "EC2"
    assert analysis["confidence"] >= 65.0
    assert "API" in analysis["affected_services"] or "Application" in analysis["affected_services"]
    assert "causal_paths" in analysis
    assert "reasoning_chain" in analysis
    assert "remediation_plan" in analysis
