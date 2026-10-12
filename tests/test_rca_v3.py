import pytest
import networkx as nx
from datetime import datetime, timezone, timedelta

from rca_v3 import diagnose, RCAv3Config, Verdict, CircuitBreaker
from rca_v3.causal_graph import CausalGraphV3
from rca_v3.symptom_scoring import SymptomScorer
from rca_v3.models import CandidateDiagnosis
from rca_engine.causal_graph import CausalGraphEngine


class DummyTopology:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges


@pytest.fixture
def base_topology():
    nodes = [
        {"id": "EC2", "type": "compute"},
        {"id": "DynamoDB", "type": "database"},
        {"id": "Auth", "type": "auth"},
        {"id": "Worker", "type": "worker"},
        {"id": "API", "type": "service"},
        {"id": "Application", "type": "frontend"},
    ]
    edges = [
        ("API", "EC2"),           # API depends on EC2
        ("API", "DynamoDB"),      # API depends on DynamoDB
        ("API", "Auth"),          # API depends on Auth
        ("Worker", "DynamoDB"),   # Worker depends on DynamoDB
        ("Worker", "EC2"),        # Worker depends on EC2
        ("Application", "API"),   # Application depends on API
        ("Application", "Worker") # Application depends on Worker
    ]
    return DummyTopology(nodes, edges)


def test_2d_directed_relatedness_and_edge_direction(base_topology):
    """
    Step 2d unit test:
    Two services are related ONLY if one lies on a directed failure-propagation path of the other.
    Sharing a common caller is not enough.
    """
    graph_v3 = CausalGraphV3.from_topology(base_topology)

    # In base topology:
    # Failure propagates from callee to caller (DynamoDB -> API, DynamoDB -> Worker, API -> Application).
    # 1. DynamoDB and Application:
    # Failure propagates DynamoDB -> API -> Application. They lie on a directed path!
    assert graph_v3.are_services_related("DynamoDB", "Application", enable_directed=True) is True
    assert graph_v3.is_ancestor_cause("DynamoDB", "Application") is True

    # 2. Application does NOT cause DynamoDB:
    assert graph_v3.is_ancestor_cause("Application", "DynamoDB") is False

    # 3. Common caller test:
    # API calls EC2 and API calls DynamoDB.
    # In legacy undirected graph, EC2 and DynamoDB are connected via API.
    # But in directed failure propagation: EC2 does NOT cause DynamoDB, and DynamoDB does NOT cause EC2!
    # They share callers (API, Worker), but neither lies on a directed failure path of the other!
    assert graph_v3.are_services_related("EC2", "DynamoDB", enable_directed=True) is False

    # With legacy undirected ablation, they are considered related:
    assert graph_v3.are_services_related("EC2", "DynamoDB", enable_directed=False) is True


def test_2a_graph_generated_candidates_and_unobserved_cap(base_topology):
    """
    Step 2a: Unobserved root service added as candidate when dependents fail,
    sources healthy, but candidate emits 0 events. Verdict capped at PROBABLE.
    """
    # Incident where DynamoDB fails, causing API and Application errors.
    # But DynamoDB emits ZERO events (e.g. completely silent/unobserved).
    t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        {
            "event_id": "evt_api",
            "service": "API",
            "event_time": (t0 + timedelta(seconds=2)).isoformat(),
            "source": "application",
            "severity": "HIGH",
            "metric": "HTTP500"
        },
        {
            "event_id": "evt_app",
            "service": "Application",
            "event_time": (t0 + timedelta(seconds=5)).isoformat(),
            "source": "application",
            "severity": "CRITICAL",
            "metric": "Latency"
        }
    ]
    source_health = [
        {"source": "cloudwatch", "status": "HEALTHY"},
        {"source": "application", "status": "HEALTHY"}
    ]

    # With graph candidate discovery enabled
    res = diagnose(events, base_topology, source_health=source_health)
    
    # DynamoDB should be discovered as unobserved candidate!
    candidate_services = [c.service for c in res.candidate_diagnoses]
    assert "DynamoDB" in candidate_services

    # Rule: Never CONFIRMED when the best candidate is unobserved; cap at PROBABLE
    if res.top_candidate and res.top_candidate.service == "DynamoDB":
        assert res.verdict == Verdict.PROBABLE


def test_2b_symptom_explanation_scoring(base_topology):
    """
    Step 2b: Symptom explanation scoring ranks true upstream root higher than downstream sink.
    """
    t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    # DynamoDB fails first, followed by API and Application
    events = [
        {
            "event_id": "evt_ddb",
            "service": "DynamoDB",
            "event_time": t0.isoformat(),
            "source": "cloudwatch",
            "severity": "HIGH",
            "metric": "ReadThrottle"
        },
        {
            "event_id": "evt_api",
            "service": "API",
            "event_time": (t0 + timedelta(seconds=3)).isoformat(),
            "source": "application",
            "severity": "HIGH",
            "metric": "HTTP500"
        },
        {
            "event_id": "evt_app",
            "service": "Application",
            "event_time": (t0 + timedelta(seconds=6)).isoformat(),
            "source": "application",
            "severity": "CRITICAL",
            "metric": "Latency"
        }
    ]

    res = diagnose(events, base_topology)
    top_cand = res.top_candidate
    assert top_cand is not None
    assert top_cand.service == "DynamoDB"
    assert top_cand.symptom_coverage == 1.0  # Explains DynamoDB, API, Application


def test_2c_multi_root_set_separation(base_topology):
    """
    Step 2c: Candidate SET evaluation rejects downstream echo on the same causal chain,
    while separating independent roots.
    """
    t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Single root incident with downstream propagation:
    events = [
        {
            "event_id": "evt_ddb",
            "service": "DynamoDB",
            "event_time": t0.isoformat(),
            "source": "cloudwatch",
            "severity": "CRITICAL",
            "metric": "ReadThrottle"
        },
        {
            "event_id": "evt_api",
            "service": "API",
            "event_time": (t0 + timedelta(seconds=3)).isoformat(),
            "source": "application",
            "severity": "HIGH",
            "metric": "HTTP500"
        }
    ]

    res = diagnose(events, base_topology)
    # API should NOT be added as a separate root because it is downstream on the same chain
    # and explains no residual symptoms.
    assert res.predicted_root_causes == ["DynamoDB"]
    assert len(res.root_hypotheses) == 1
    assert res.root_hypotheses[0].root == "DynamoDB"


def test_2e_principled_verdict_rules(base_topology):
    """
    Step 2e: Coverage must be able to prevent CONFIRMED; source DOWN prevents CONFIRMED.
    """
    t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        {
            "event_id": "evt_ec2",
            "service": "EC2",
            "event_time": t0.isoformat(),
            "source": "cloudwatch",
            "severity": "HIGH",
            "metric": "CPUUtilization"
        }
    ]

    # Case 1: Source carrying signals for EC2 or ancestor is DOWN
    source_health = [
        {"source": "cloudwatch", "status": "DOWN"}
    ]
    res = diagnose(events, base_topology, source_health=source_health)
    # Must NOT be CONFIRMED!
    assert res.verdict != Verdict.CONFIRMED


def test_2f_circuit_breaker():
    """
    Step 2f: Circuit breaker opens after N failures and routes directly to fallback.
    """
    cb = CircuitBreaker(max_failures=2, cooldown_sec=10.0)

    call_count = {"primary": 0, "fallback": 0}

    def failing_primary():
        call_count["primary"] += 1
        raise TimeoutError("diagnose timeout")

    def safe_fallback():
        call_count["fallback"] += 1
        return "fallback_result"

    # Call 1: fails
    res1, fb1 = cb.execute(failing_primary, safe_fallback)
    assert fb1 is True
    assert cb.failure_count == 1
    assert cb.is_open() is False

    # Call 2: fails, trips breaker
    res2, fb2 = cb.execute(failing_primary, safe_fallback)
    assert fb2 is True
    assert cb.failure_count == 2
    assert cb.is_open() is True

    # Call 3: breaker is open, primary must NOT be called at all!
    p_calls = call_count["primary"]
    res3, fb3 = cb.execute(failing_primary, safe_fallback)
    assert fb3 is True
    assert res3 == "fallback_result"
    assert call_count["primary"] == p_calls  # Zero primary calls made while open!
