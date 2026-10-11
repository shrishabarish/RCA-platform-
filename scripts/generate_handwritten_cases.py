import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import (
    EvaluationCase,
    TopologyData,
    TopologyNode,
    EventData,
    SourceHealthRecord,
    GroundTruth
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "evaluation" / "cases" / "handwritten"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_NODES = [
    TopologyNode(id="EC2", type="compute", label="EC2 Compute Host", criticality=0.9),
    TopologyNode(id="DynamoDB", type="database", label="DynamoDB Database", criticality=0.95),
    TopologyNode(id="Auth", type="auth", label="Auth & IdP Service", criticality=0.85),
    TopologyNode(id="Worker", type="worker", label="Background Worker", criticality=0.7),
    TopologyNode(id="API", type="service", label="Backend API Gateway", criticality=0.85),
    TopologyNode(id="Application", type="frontend", label="Web Application UI", criticality=0.6),
]

BASE_EDGES = [
    ["API", "EC2"],
    ["API", "DynamoDB"],
    ["API", "Auth"],
    ["Worker", "DynamoDB"],
    ["Worker", "EC2"],
    ["Application", "API"],
    ["Application", "Worker"]
]

def make_base_topology(version: str = "1.0", extra_nodes=None, extra_edges=None):
    nodes = list(BASE_NODES)
    if extra_nodes:
        nodes.extend(extra_nodes)
    edges = list(BASE_EDGES)
    if extra_edges:
        edges.extend(extra_edges)
    return TopologyData(nodes=nodes, edges=edges, topology_version=version)


def build_handwritten_cases():
    cases = []

    # 1. Converted Scenario: Cascade (EC2 root)
    t0 = datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_scenario_cascade",
        family_id="fam_hw_scenarios",
        seed=1001,
        topology=make_base_topology(),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=98.5, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=10)).isoformat(), observed_time=(t0 + timedelta(seconds=10)).isoformat(), source="datadog", service="API", metric="Latency", value=2500.0, severity="MEDIUM"),
            EventData(event_time=(t0 + timedelta(seconds=15)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="datadog", service="API", metric="ErrorRate", value=10.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=20)).isoformat(), observed_time=(t0 + timedelta(seconds=20)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=15.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=35)).isoformat(), observed_time=(t0 + timedelta(seconds=35)).isoformat(), source="otel", service="Application", metric="CheckoutFailed", value=5.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["EC2"],
            onset_times={"EC2": t0.isoformat()},
            symptom_to_cause={"API": "EC2", "Application": "EC2"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=[],
        tags=["handwritten", "canonical-scenario", "single-root"]
    ))

    # 2. Converted Scenario: DB Exhaustion (DynamoDB root)
    t0 = datetime(2026, 9, 1, 16, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_scenario_db_exhaustion",
        family_id="fam_hw_scenarios",
        seed=1002,
        topology=make_base_topology(),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="DynamoDB", metric="ThrottleRequests", value=1250.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=5)).isoformat(), observed_time=(t0 + timedelta(seconds=5)).isoformat(), source="datadog", service="API", metric="Latency", value=3200.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=12)).isoformat(), observed_time=(t0 + timedelta(seconds=12)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=25.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB"],
            onset_times={"DynamoDB": t0.isoformat()},
            symptom_to_cause={"API": "DynamoDB", "Application": "DynamoDB"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=[],
        tags=["handwritten", "canonical-scenario", "single-root"]
    ))

    # 3. Converted Scenario: Auth Outage (Auth root)
    t0 = datetime(2026, 9, 1, 17, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_scenario_auth_outage",
        family_id="fam_hw_scenarios",
        seed=1003,
        topology=make_base_topology(),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="datadog", service="Auth", metric="AuthTokenErrorRate", value=65.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="datadog", service="API", metric="Unauthorized401Rate", value=80.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=15)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="otel", service="Application", metric="LoginFailureRate", value=90.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="datadog", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["Auth"],
            onset_times={"Auth": t0.isoformat()},
            symptom_to_cause={"API": "Auth", "Application": "Auth"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=[],
        tags=["handwritten", "canonical-scenario", "single-root"]
    ))

    # 4. Converted Scenario: Memory Leak (Worker root)
    t0 = datetime(2026, 9, 1, 18, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_scenario_memory_leak",
        family_id="fam_hw_scenarios",
        seed=1004,
        topology=make_base_topology(),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="Worker", metric="MemoryUtilization", value=95.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=20)).isoformat(), observed_time=(t0 + timedelta(seconds=20)).isoformat(), source="otel", service="Application", metric="AsyncReportTimeoutRate", value=30.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["Worker"],
            onset_times={"Worker": t0.isoformat()},
            symptom_to_cause={"Application": "Worker"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=[],
        tags=["handwritten", "canonical-scenario", "single-root"]
    ))

    # 5. Adversarial: Severe Decoy
    t0 = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_severe_decoy",
        family_id="fam_hw_adv_decoy",
        seed=1005,
        topology=make_base_topology(),
        events=[
            # True root: DynamoDB throttling at t0
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="DynamoDB", metric="ThrottleRequests", value=1500.0, severity="MEDIUM"),
            # API latency at t0 + 5s
            EventData(event_time=(t0 + timedelta(seconds=5)).isoformat(), observed_time=(t0 + timedelta(seconds=5)).isoformat(), source="datadog", service="API", metric="Latency", value=2200.0, severity="MEDIUM"),
            # Unrelated severe decoy: Worker node emits CRITICAL CPU 99.9% at t0 + 8s
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="cloudwatch", service="Worker", metric="CPUUtilization", value=99.9, severity="CRITICAL"),
            # Downstream app symptom
            EventData(event_time=(t0 + timedelta(seconds=15)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=35.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB"],
            onset_times={"DynamoDB": t0.isoformat()},
            symptom_to_cause={"API": "DynamoDB", "Application": "DynamoDB"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["severe_decoy"],
        tags=["handwritten", "adversarial", "severe-decoy"]
    ))

    # 6. Adversarial: Two Independent Roots Sharing API
    t0 = datetime(2026, 9, 2, 11, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_two_roots_shared_api",
        family_id="fam_hw_adv_multi_root",
        seed=1006,
        topology=make_base_topology(),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="DynamoDB", metric="ThrottleRequests", value=1200.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=2)).isoformat(), observed_time=(t0 + timedelta(seconds=2)).isoformat(), source="datadog", service="Auth", metric="AuthTokenErrorRate", value=70.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="datadog", service="API", metric="Latency", value=2600.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=14)).isoformat(), observed_time=(t0 + timedelta(seconds=14)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=40.0, severity="CRITICAL"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB", "Auth"],
            onset_times={"DynamoDB": t0.isoformat(), "Auth": (t0 + timedelta(seconds=2)).isoformat()},
            symptom_to_cause={"API": "DynamoDB", "Application": "Auth"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["second_independent_failure"],
        tags=["handwritten", "adversarial", "multi-root"]
    ))

    # 7. Adversarial: Root Telemetry Completely Missing (Silent Failure) -> Must ABSTAIN
    t0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_missing_root_telemetry",
        family_id="fam_hw_adv_silent_root",
        seed=1007,
        topology=make_base_topology(),
        events=[
            # EC2 completely died without sending any metric/alarm
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="datadog", service="API", metric="Latency", value=2400.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=6)).isoformat(), observed_time=(t0 + timedelta(seconds=6)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=20.0, severity="CRITICAL"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="DOWN")],
        ground_truth=GroundTruth(
            true_root_causes=["EC2"],
            onset_times={"EC2": (t0 - timedelta(seconds=10)).isoformat()},
            symptom_to_cause={"API": "EC2", "Application": "EC2"},
            expected_verdict="ABSTAIN"
        ),
        perturbations=["drop_all_signals_one_source"],
        tags=["handwritten", "adversarial", "abstain-required", "missing-root"]
    ))

    # 8. Adversarial: Whole Source Down
    t0 = datetime(2026, 9, 2, 13, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_whole_source_down",
        family_id="fam_hw_adv_source_down",
        seed=1008,
        topology=make_base_topology(),
        events=[
            # CloudWatch agent down; only OTEL frontend logs present
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=55.0, severity="CRITICAL"),
        ],
        source_health=[
            SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="DOWN"),
            SourceHealthRecord(source="datadog", timestamp=t0.isoformat(), status="DOWN"),
            SourceHealthRecord(source="otel", timestamp=t0.isoformat(), status="HEALTHY"),
        ],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB"],
            onset_times={"DynamoDB": (t0 - timedelta(seconds=30)).isoformat()},
            symptom_to_cause={"Application": "DynamoDB"},
            expected_verdict="ABSTAIN"
        ),
        perturbations=["drop_all_signals_one_source"],
        tags=["handwritten", "adversarial", "abstain-required", "source-outage"]
    ))

    # 9. Adversarial: Late Upstream Event (Out of order arrival)
    t0 = datetime(2026, 9, 2, 14, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_late_upstream_event",
        family_id="fam_hw_adv_late_arrival",
        seed=1009,
        topology=make_base_topology(),
        events=[
            # Symptoms observed first due to buffer delay
            EventData(event_time=(t0 + timedelta(seconds=10)).isoformat(), observed_time=(t0 + timedelta(seconds=12)).isoformat(), source="datadog", service="API", metric="Latency", value=2300.0, severity="MEDIUM"),
            EventData(event_time=(t0 + timedelta(seconds=20)).isoformat(), observed_time=(t0 + timedelta(seconds=22)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=15.0, severity="HIGH"),
            # Root event has earlier event_time but arrived 25s late
            EventData(event_time=t0.isoformat(), observed_time=(t0 + timedelta(seconds=25)).isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=97.0, severity="CRITICAL"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["EC2"],
            onset_times={"EC2": t0.isoformat()},
            symptom_to_cause={"API": "EC2", "Application": "EC2"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["delay_and_reorder"],
        tags=["handwritten", "adversarial", "late-arrival"]
    ))

    # 10. Adversarial: Clock Skew on Symptoms
    t0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_clock_skew",
        family_id="fam_hw_adv_clock_skew",
        seed=1010,
        topology=make_base_topology(),
        events=[
            # Sensed with 35s backward clock skew on frontend host
            EventData(event_time=(t0 - timedelta(seconds=20)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=18.0, severity="HIGH"),
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="DynamoDB", metric="ThrottleRequests", value=1100.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=5)).isoformat(), observed_time=(t0 + timedelta(seconds=5)).isoformat(), source="datadog", service="API", metric="Latency", value=2100.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB"],
            onset_times={"DynamoDB": t0.isoformat()},
            symptom_to_cause={"API": "DynamoDB", "Application": "DynamoDB"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["clock_skew"],
        tags=["handwritten", "adversarial", "clock-skew"]
    ))

    # 11. Adversarial: Duplicate Burst on Symptom Service
    t0 = datetime(2026, 9, 2, 16, 0, 0, tzinfo=timezone.utc)
    dup_events = [
        EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=96.0, severity="HIGH")
    ]
    for i in range(15):
        dup_events.append(EventData(
            event_time=(t0 + timedelta(seconds=5 + i)).isoformat(),
            observed_time=(t0 + timedelta(seconds=5 + i)).isoformat(),
            source="datadog",
            service="API",
            metric="Latency",
            value=2500.0 + (i % 10),
            severity="CRITICAL"
        ))
    cases.append(EvaluationCase(
        case_id="hw_adv_duplicate_burst",
        family_id="fam_hw_adv_dup_burst",
        seed=1011,
        topology=make_base_topology(),
        events=dup_events,
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["EC2"],
            onset_times={"EC2": t0.isoformat()},
            symptom_to_cause={"API": "EC2"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["duplicate"],
        tags=["handwritten", "adversarial", "duplicate-burst"]
    ))

    # 12. Adversarial: Topology Change Mid-Incident
    t0 = datetime(2026, 9, 2, 17, 0, 0, tzinfo=timezone.utc)
    extra_node = [TopologyNode(id="PaymentGateway", type="service", label="Payment Service", criticality=0.8)]
    extra_edge = [["API", "PaymentGateway"], ["PaymentGateway", "DynamoDB"]]
    cases.append(EvaluationCase(
        case_id="hw_adv_topology_change_mid_incident",
        family_id="fam_hw_adv_topo_change",
        seed=1012,
        topology=make_base_topology(version="2.0", extra_nodes=extra_node, extra_edges=extra_edge),
        events=[
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="DynamoDB", metric="ThrottleRequests", value=1400.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=4)).isoformat(), observed_time=(t0 + timedelta(seconds=4)).isoformat(), source="datadog", service="PaymentGateway", metric="Latency", value=2200.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="datadog", service="API", metric="Latency", value=2100.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=12)).isoformat(), observed_time=(t0 + timedelta(seconds=12)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=25.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["DynamoDB"],
            onset_times={"DynamoDB": t0.isoformat()},
            symptom_to_cause={"PaymentGateway": "DynamoDB", "API": "DynamoDB", "Application": "DynamoDB"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["change_topology_mid_incident"],
        tags=["handwritten", "adversarial", "topology-change"]
    ))

    # 13. Adversarial: Two Disjoint Cascades in Same Window
    t0 = datetime(2026, 9, 2, 18, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_two_disjoint_cascades",
        family_id="fam_hw_adv_disjoint",
        seed=1013,
        topology=make_base_topology(),
        events=[
            # Cascade 1: Auth down -> API 401
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="datadog", service="Auth", metric="AuthTokenErrorRate", value=75.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=5)).isoformat(), observed_time=(t0 + timedelta(seconds=5)).isoformat(), source="datadog", service="API", metric="401Rate", value=80.0, severity="HIGH"),
            # Cascade 2: Worker memory leak -> App async timeout
            EventData(event_time=(t0 + timedelta(seconds=10)).isoformat(), observed_time=(t0 + timedelta(seconds=10)).isoformat(), source="cloudwatch", service="Worker", metric="MemoryUtilization", value=98.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=15)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="otel", service="Application", metric="AsyncTimeoutRate", value=40.0, severity="HIGH"),
        ],
        source_health=[SourceHealthRecord(source="datadog", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["Auth", "Worker"],
            onset_times={"Auth": t0.isoformat(), "Worker": (t0 + timedelta(seconds=10)).isoformat()},
            symptom_to_cause={"API": "Auth", "Application": "Worker"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["second_independent_failure"],
        tags=["handwritten", "adversarial", "disjoint-failures", "multi-root"]
    ))

    # 14. Adversarial: Flapping Service Decoy (No Causal Link)
    t0 = datetime(2026, 9, 2, 19, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_flapping_service_decoy",
        family_id="fam_hw_adv_flapping",
        seed=1014,
        topology=make_base_topology(),
        events=[
            # Real root: Auth failure at t0
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="datadog", service="Auth", metric="AuthTokenErrorRate", value=60.0, severity="HIGH"),
            # Flapping unrelated noise on Worker at t0 + 2s, t0 + 4s, t0 + 6s
            EventData(event_time=(t0 + timedelta(seconds=2)).isoformat(), observed_time=(t0 + timedelta(seconds=2)).isoformat(), source="cloudwatch", service="Worker", metric="JobRetries", value=5.0, severity="LOW"),
            EventData(event_time=(t0 + timedelta(seconds=4)).isoformat(), observed_time=(t0 + timedelta(seconds=4)).isoformat(), source="cloudwatch", service="Worker", metric="JobRetries", value=15.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=6)).isoformat(), observed_time=(t0 + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="Worker", metric="JobRetries", value=2.0, severity="LOW"),
            # Downstream real symptoms
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="datadog", service="API", metric="401Rate", value=85.0, severity="CRITICAL"),
            EventData(event_time=(t0 + timedelta(seconds=12)).isoformat(), observed_time=(t0 + timedelta(seconds=12)).isoformat(), source="otel", service="Application", metric="LoginFailureRate", value=90.0, severity="CRITICAL"),
        ],
        source_health=[SourceHealthRecord(source="datadog", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["Auth"],
            onset_times={"Auth": t0.isoformat()},
            symptom_to_cause={"API": "Auth", "Application": "Auth"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["severe_decoy"],
        tags=["handwritten", "adversarial", "flapping-decoy"]
    ))

    # 15. Adversarial: Drop Single Critical Signal (EC2 CPU dropped, only secondary disk error left)
    t0 = datetime(2026, 9, 2, 20, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_drop_single_signal",
        family_id="fam_hw_adv_drop_signal",
        seed=1015,
        topology=make_base_topology(),
        events=[
            # Primary EC2 CPU spike was dropped; only low-severity disk latency event remains at t0
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="cloudwatch", service="EC2", metric="DiskQueueDepth", value=12.0, severity="LOW"),
            EventData(event_time=(t0 + timedelta(seconds=8)).isoformat(), observed_time=(t0 + timedelta(seconds=8)).isoformat(), source="datadog", service="API", metric="Latency", value=2800.0, severity="HIGH"),
            EventData(event_time=(t0 + timedelta(seconds=15)).isoformat(), observed_time=(t0 + timedelta(seconds=15)).isoformat(), source="otel", service="Application", metric="5xxErrorRate", value=22.0, severity="CRITICAL"),
        ],
        source_health=[SourceHealthRecord(source="cloudwatch", timestamp=t0.isoformat(), status="HEALTHY")],
        ground_truth=GroundTruth(
            true_root_causes=["EC2"],
            onset_times={"EC2": t0.isoformat()},
            symptom_to_cause={"API": "EC2", "Application": "EC2"},
            expected_verdict="DIAGNOSE"
        ),
        perturbations=["drop_one_signal"],
        tags=["handwritten", "adversarial", "dropped-signal"]
    ))

    # 16. Adversarial: Silent Disjoint Partition -> Must ABSTAIN
    t0 = datetime(2026, 9, 2, 21, 0, 0, tzinfo=timezone.utc)
    cases.append(EvaluationCase(
        case_id="hw_adv_silent_partition_abstain",
        family_id="fam_hw_adv_partition",
        seed=1016,
        topology=make_base_topology(),
        events=[
            # Random disconnected low-confidence blips with no causal path or root
            EventData(event_time=t0.isoformat(), observed_time=t0.isoformat(), source="datadog", service="Auth", metric="TokenValidationLatency", value=120.0, severity="LOW"),
            EventData(event_time=(t0 + timedelta(seconds=10)).isoformat(), observed_time=(t0 + timedelta(seconds=10)).isoformat(), source="cloudwatch", service="Worker", metric="HeartbeatLag", value=5.0, severity="LOW"),
        ],
        source_health=[SourceHealthRecord(source="datadog", timestamp=t0.isoformat(), status="DEGRADED")],
        ground_truth=GroundTruth(
            true_root_causes=[],
            onset_times={},
            symptom_to_cause={},
            expected_verdict="ABSTAIN"
        ),
        perturbations=[],
        tags=["handwritten", "adversarial", "abstain-required", "no-root"]
    ))

    # Save each case as immutable JSON
    for case in cases:
        out_file = OUTPUT_DIR / f"{case.case_id}.json"
        out_file.write_text(json.dumps(case.model_dump(), indent=2), encoding="utf-8")
        print(f"Generated handwritten case: {out_file.name}")

    print(f"\nTotal handwritten cases generated: {len(cases)}")


if __name__ == "__main__":
    build_handwritten_cases()
