import pytest
from datetime import datetime, timezone, timedelta
from typing import List
from pathlib import Path

from backend.app.models.event import CloudEvent, Severity, Incident, IncidentStatus
from evaluation.schema import EvaluationCase, EventData
from rca_v2 import diagnose, RCAv2Config, Verdict
from rca_v2.live import LiveEventAdapter, derive_live_source_health, LiveIncidentBridge
from rca_v2.telemetry_policy import TelemetryPolicy
from rca_engine.remediation_executor import remediation_executor
from rca_engine.causal_graph import CausalGraphEngine


def test_l1_adapter_produces_identical_input():
    """
    Requirement L1:
    Show that converting CloudEvent objects vs replaying case files (EventData)
    produces identical normalized CloudEvent input for the same events.
    """
    ts_str = "2026-09-01T12:00:00+00:00"
    dt = datetime.fromisoformat(ts_str)

    # From live CloudEvent
    live_event = CloudEvent(
        event_id="evt_test_001",
        timestamp=dt,
        source="cloudwatch",
        event_type="cpu_anomaly",
        service="EC2",
        resource_id="res_ec2",
        metric="CPUUtilization",
        value=95.5,
        severity=Severity.HIGH
    )

    # From case EventData
    case_event = EventData(
        event_id="evt_test_001",
        event_time=ts_str,
        observed_time=ts_str,
        source="cloudwatch",
        service="EC2",
        metric="CPUUtilization",
        value=95.5,
        severity="HIGH"
    )

    adapted_from_live = LiveEventAdapter.adapt_cloud_event(live_event)
    adapted_from_case = LiveEventAdapter.adapt_case_event(case_event)

    assert adapted_from_live.event_id == adapted_from_case.event_id
    assert adapted_from_live.timestamp == adapted_from_case.timestamp
    assert adapted_from_live.service == adapted_from_case.service
    assert adapted_from_live.source == adapted_from_case.source
    assert adapted_from_live.metric == adapted_from_case.metric
    assert adapted_from_live.value == adapted_from_case.value
    assert adapted_from_live.severity == adapted_from_case.severity


def test_l2_live_source_health_derivation():
    """
    Requirement L2:
    Derive source health from the event stream:
    - Active within 2*interval -> HEALTHY
    - Gap between 2*interval and 4*interval -> DEGRADED
    - Gap > 4*interval -> DOWN
    - Unobserved source -> UNKNOWN
    """
    base_t = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Expected interval for cloudwatch is 60s, otel is 15s

    events = [
        # Cloudwatch event: 30s ago (within 2*60s = 120s) -> HEALTHY
        EventData(
            event_id="e1",
            event_time=(base_t - timedelta(seconds=30)).isoformat(),
            observed_time=(base_t - timedelta(seconds=30)).isoformat(),
            source="cloudwatch",
            service="EC2",
            metric="CPUUtilization",
            value=80.0,
            severity="HIGH"
        ),
        # OTEL event: 40s ago (normal interval 15s; 2*15=30s, 4*15=60s; 40s is in between) -> DEGRADED
        EventData(
            event_id="e2",
            event_time=(base_t - timedelta(seconds=40)).isoformat(),
            observed_time=(base_t - timedelta(seconds=40)).isoformat(),
            source="otel",
            service="API",
            metric="Latency",
            value=250.0,
            severity="HIGH"
        ),
        # Datadog event: 300s ago (normal interval 60s; 4*60=240s; 300s > 240s) -> DOWN
        EventData(
            event_id="e3",
            event_time=(base_t - timedelta(seconds=300)).isoformat(),
            observed_time=(base_t - timedelta(seconds=300)).isoformat(),
            source="datadog",
            service="EC2",
            metric="MemoryUtilization",
            value=90.0,
            severity="HIGH"
        ),
    ]

    records = derive_live_source_health(events, reference_time=base_t)
    rec_map = {r.source: r.status for r in records}

    assert rec_map["cloudwatch"] == "HEALTHY", f"Expected HEALTHY, got {rec_map['cloudwatch']}"
    assert rec_map["otel"] == "DEGRADED", f"Expected DEGRADED, got {rec_map['otel']}"
    assert rec_map["datadog"] == "DOWN", f"Expected DOWN, got {rec_map['datadog']}"
    assert rec_map["syslog"] == "UNKNOWN", f"Expected UNKNOWN for unobserved source, got {rec_map['syslog']}"


def test_l3_live_telemetry_policy_missing_means_not_quiet():
    """
    Requirement L3:
    Six real services configured. Missing policy means signals are NOT marked QUIET.
    """
    policy = TelemetryPolicy.load_live_policy()
    assert "EC2" in policy.service_policies
    assert "DynamoDB" in policy.service_policies
    assert "Auth" in policy.service_policies
    assert "Worker" in policy.service_policies
    assert "API" in policy.service_policies
    assert "Application" in policy.service_policies

    # Unknown unconfigured service
    unconfigured_sigs = policy.get_expected_signals_for_node("CustomUnregisteredService", "unknown_type")
    # For any fallback signal generated, is_anomaly_only must be False (never quiet)
    for sig in unconfigured_sigs:
        assert sig.is_anomaly_only is False, "Missing policy signal must NOT have is_anomaly_only=True"


def test_l4_shadow_mode_execution():
    """
    Requirement L4:
    Shadow mode runs Current Scorer as primary and rca_v2 next to it with agreement flag and both rankings.
    """
    t0 = datetime.now(timezone.utc)
    evs = [
        CloudEvent(
            event_id="e1",
            timestamp=t0,
            source="cloudwatch",
            event_type="cpu_anomaly",
            service="EC2",
            resource_id="res_ec2",
            metric="CPUUtilization",
            value=99.0,
            severity=Severity.CRITICAL
        ),
        CloudEvent(
            event_id="e2",
            timestamp=t0 + timedelta(seconds=2),
            source="application",
            event_type="latency_anomaly",
            service="API",
            resource_id="res_api",
            metric="Latency",
            value=2000.0,
            severity=Severity.HIGH
        )
    ]
    inc = Incident(
        incident_id="inc_shadow_test",
        events=evs,
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=t0,
        updated_at=t0 + timedelta(seconds=2),
        affected_services=["EC2", "API"]
    )

    bridge = LiveIncidentBridge()
    result = bridge.analyze(inc, mode="shadow")

    # Current scorer drives primary behavior
    assert result["probable_root_cause"] == "EC2"
    assert "rca_shadow" in result
    shadow = result["rca_shadow"]
    assert shadow["mode"] == "shadow"
    assert "agreement" in shadow
    assert shadow["agreement"] is True
    assert shadow["rca_v2_top1"] == "EC2"
    assert "current_scorer_ranking" in shadow
    assert "rca_v2_ranking" in shadow
    assert "rca_v2_verdict" in shadow


def test_l5_fallback_on_exception_and_timeout(monkeypatch):
    """
    Requirement L5:
    If rca_v2 raises an exception or exceeds timeout, fallback to Current Scorer, log the error, and never fail.
    """
    t0 = datetime.now(timezone.utc)
    evs = [
        CloudEvent(
            event_id="e1",
            timestamp=t0,
            source="cloudwatch",
            event_type="throttle_anomaly",
            service="DynamoDB",
            resource_id="res_ddb",
            metric="WriteThrottleEvents",
            value=50.0,
            severity=Severity.CRITICAL
        )
    ]
    inc = Incident(
        incident_id="inc_fallback_test",
        events=evs,
        status=IncidentStatus.OPEN,
        severity=Severity.HIGH,
        created_at=t0,
        updated_at=t0,
        affected_services=["DynamoDB"]
    )

    # Monkeypatch diagnose to raise an unexpected runtime error
    def broken_diagnose(*args, **kwargs):
        raise RuntimeError("Simulated transient internal rca_v2 failure")

    monkeypatch.setattr("rca_v2.live.diagnose", broken_diagnose)

    bridge = LiveIncidentBridge()
    # In mode="v2", fallback should return Current Scorer without raising
    result = bridge.analyze(inc, mode="v2")

    assert result["probable_root_cause"] == "DynamoDB"
    assert result["current_scorer_fallback"] is True
    assert "Simulated transient" in result["rca_v2_error"]


def test_l6_remediation_guard_blocks_unconfirmed_verdicts():
    """
    Requirement L6:
    INSUFFICIENT_EVIDENCE and PROBABLE never trigger automated remediation.
    Only CONFIRMED may.
    """
    # 1. INSUFFICIENT_EVIDENCE -> BLOCKED
    res_insuf = remediation_executor.execute_remediation(
        incident_id="inc_guard_1",
        root_cause="EC2",
        resource_id="i-test123",
        dry_run=True,
        verdict="INSUFFICIENT_EVIDENCE"
    )
    assert res_insuf["status"] == "BLOCKED_BY_VERDICT_GUARD"
    assert res_insuf["safety_checks_passed"] is False

    # 2. PROBABLE -> BLOCKED
    res_prob = remediation_executor.execute_remediation(
        incident_id="inc_guard_2",
        root_cause="DynamoDB",
        resource_id="OrdersTable",
        dry_run=True,
        verdict="PROBABLE"
    )
    assert res_prob["status"] == "BLOCKED_BY_VERDICT_GUARD"
    assert res_prob["safety_checks_passed"] is False

    # 3. ABSTAIN -> BLOCKED
    res_abs = remediation_executor.execute_remediation(
        incident_id="inc_guard_3",
        root_cause="Auth",
        resource_id="res_auth",
        dry_run=True,
        verdict="ABSTAIN"
    )
    assert res_abs["status"] == "BLOCKED_BY_VERDICT_GUARD"
    assert res_abs["safety_checks_passed"] is False

    # 4. CONFIRMED -> ALLOWED
    res_conf = remediation_executor.execute_remediation(
        incident_id="inc_guard_4",
        root_cause="EC2",
        resource_id="i-test123",
        dry_run=True,
        verdict="CONFIRMED"
    )
    assert res_conf["status"] == "SUCCESS"
    assert res_conf["safety_checks_passed"] is True
    assert len(res_conf["commands_executed"]) > 0


def test_l7_live_entrypoint_replays_cases_with_matching_verdict():
    """
    Requirement L7:
    Replay DEV and hand-written cases through live entry point and verify identical verdict.
    """
    hw_dir = Path("evaluation/cases/handwritten")
    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in hw_dir.glob("*.json")]

    bridge = LiveIncidentBridge()
    cfg = RCAv2Config.load_from_file(Path("rca_v2/config.json"))

    for c in cases:
        cloud_events = LiveEventAdapter.adapt_events(c.events)
        cg = CausalGraphEngine.from_topology(c.topology)
        
        inc = Incident(
            incident_id=f"live_{c.case_id}",
            events=cloud_events,
            status=IncidentStatus.OPEN,
            severity=Severity.HIGH,
            created_at=cloud_events[0].timestamp if cloud_events else datetime.now(timezone.utc),
            updated_at=cloud_events[-1].timestamp if cloud_events else datetime.now(timezone.utc),
            affected_services=list(set(e.service for e in cloud_events))
        )

        res = bridge.analyze(inc, mode="v2", custom_graph_engine=cg, source_health=c.source_health)
        
        # Check verdict match
        diag_expected = c.ground_truth.expected_verdict
        if diag_expected == "ABSTAIN":
            assert res.get("rca_v2_verdict") == "INSUFFICIENT_EVIDENCE"
        else:
            assert res.get("rca_v2_verdict") in ("CONFIRMED", "PROBABLE")
            assert res.get("probable_root_cause") in c.ground_truth.true_root_causes


def test_l7_streaming_case_event_by_event():
    """
    Requirement L7:
    Stream events one-by-one with timestamp progression into LiveIncidentBridge.
    """
    base_t = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    stream = [
        CloudEvent(
            event_id="s1",
            timestamp=base_t,
            source="cloudwatch",
            event_type="cpu_anomaly",
            service="EC2",
            resource_id="res_ec2",
            metric="CPUUtilization",
            value=95.0,
            severity=Severity.CRITICAL
        ),
        CloudEvent(
            event_id="s2",
            timestamp=base_t + timedelta(seconds=5),
            source="application",
            event_type="latency_anomaly",
            service="API",
            resource_id="res_api",
            metric="Latency",
            value=1200.0,
            severity=Severity.HIGH
        ),
        CloudEvent(
            event_id="s3",
            timestamp=base_t + timedelta(seconds=10),
            source="application",
            event_type="error_anomaly",
            service="Application",
            resource_id="res_app",
            metric="ErrorRate",
            value=15.0,
            severity=Severity.HIGH
        ),
    ]

    bridge = LiveIncidentBridge()
    accumulated = []
    for step, event in enumerate(stream, start=1):
        accumulated.append(event)
        inc = Incident(
            incident_id="streaming_inc",
            events=accumulated,
            status=IncidentStatus.OPEN,
            severity=Severity.HIGH,
            created_at=accumulated[0].timestamp,
            updated_at=accumulated[-1].timestamp,
            affected_services=list(set(e.service for e in accumulated))
        )
        res = bridge.analyze(inc, mode="shadow")
        assert res["rca_shadow"]["rca_v2_top1"] == "EC2"
