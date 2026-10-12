import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import (
    EvaluationCase,
    TopologyData,
    TopologyNode,
    EventData,
    SourceHealthRecord,
    GroundTruth,
    AcceptableOutcome
)


def build_independent_cases():
    indep_dir = PROJECT_ROOT / "evaluation" / "cases" / "independent"
    indep_dir.mkdir(parents=True, exist_ok=True)

    # 1. Real 6-node topology (Family 001)
    topo_6_nodes = [
        TopologyNode(id="EC2", type="compute", label="EC2 Fleet", criticality=0.9),
        TopologyNode(id="DynamoDB", type="database", label="DynamoDB Table", criticality=0.95),
        TopologyNode(id="Auth", type="auth", label="Auth Service", criticality=0.9),
        TopologyNode(id="Worker", type="worker", label="Worker Fleet", criticality=0.8),
        TopologyNode(id="API", type="service", label="API Gateway", criticality=0.85),
        TopologyNode(id="Application", type="service", label="Web Client", criticality=0.7)
    ]
    topo_6_edges = [
        ["API", "EC2"],
        ["API", "DynamoDB"],
        ["API", "Auth"],
        ["Worker", "DynamoDB"],
        ["Worker", "EC2"],
        ["Application", "API"],
        ["Application", "Worker"]
    ]
    topo_6 = TopologyData(nodes=topo_6_nodes, edges=topo_6_edges, topology_version="real-6node")

    # 2. 12-node topology
    topo_12_nodes = [
        TopologyNode(id="APIGateway", type="api_gateway", label="Ingress Gateway", criticality=0.95),
        TopologyNode(id="OrderService", type="service", label="Order API", criticality=0.9),
        TopologyNode(id="CatalogService", type="service", label="Catalog API", criticality=0.8),
        TopologyNode(id="AuthService", type="auth", label="Token Broker", criticality=0.9),
        TopologyNode(id="PaymentService", type="service", label="Payment Processor", criticality=0.95),
        TopologyNode(id="InventoryService", type="service", label="Inventory API", criticality=0.85),
        TopologyNode(id="NotificationService", type="service", label="Notification Worker", criticality=0.7),
        TopologyNode(id="PostgresPrimary", type="database", label="Relational Database", criticality=0.95),
        TopologyNode(id="DynamoTable", type="database", label="Ledger Database", criticality=0.95),
        TopologyNode(id="RedisCache", type="cache", label="Memory Cache", criticality=0.8),
        TopologyNode(id="K8sCluster", type="compute", label="Worker Pool", criticality=0.85),
        TopologyNode(id="KafkaCluster", type="queue", label="Streaming Bus", criticality=0.9)
    ]
    topo_12_edges = [
        ["APIGateway", "OrderService"],
        ["APIGateway", "CatalogService"],
        ["APIGateway", "AuthService"],
        ["OrderService", "PaymentService"],
        ["OrderService", "InventoryService"],
        ["OrderService", "PostgresPrimary"],
        ["CatalogService", "RedisCache"],
        ["PaymentService", "DynamoTable"],
        ["InventoryService", "PostgresPrimary"],
        ["NotificationService", "KafkaCluster"],
        ["OrderService", "NotificationService"],
        ["OrderService", "K8sCluster"],
        ["CatalogService", "K8sCluster"]
    ]
    topo_12 = TopologyData(nodes=topo_12_nodes, edges=topo_12_edges, topology_version="enterprise-12node")

    t_base = datetime(2026, 10, 16, 14, 0, 0, tzinfo=timezone.utc)

    def default_sources():
        return [
            SourceHealthRecord(source=s, timestamp=t_base.isoformat(), status="HEALTHY")
            for s in ["cloudwatch", "cloudtrail", "otel", "syslog", "application"]
        ]

    # Clean existing cases in directory
    for f in indep_dir.glob("*.json"):
        f.unlink()

    case_specs = []

    # =========================================================================
    # 1. ROOT NOT EARLIEST (slow burn / early decoy) x6 (3 on 6-node, 3 on 12-node)
    # Rule: non-root anomalous event is FIRST; true root's first anomalous event is >= 10s LATER.
    # Metric names must NOT contain node id or label words.
    # =========================================================================
    # indep_001 (6-node): DynamoDB is root, decoy on API at t=0s, root anom at t=25s
    case_specs.append({
        "id": "indep_001_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "Transient HTTP 504 burst on API at t=0s acts as early decoy; true root DynamoDB latent lock contention breaches threshold at t=25s (>10s later).",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="API", metric="TransientResponseSpike", value=540.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageThrottleRate", value=92.5, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="application", service="API", metric="ConnectionPoolStarvation", value=100.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_002 (6-node): EC2 is root, decoy on Worker at t=0s, root anom at t=20s
    case_specs.append({
        "id": "indep_002_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "Worker queue lag spike at t=0s acts as decoy; true root EC2 memory leak triggers OOM killer at t=20s (>10s later).",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Worker", metric="BacklogLagSpike", value=85.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="PhysicalMemorySaturation", value=99.2, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="UpstreamTimeoutRate", value=45.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_003 (6-node): EC2 is root, decoy on Application at t=0s, root anom at t=18s
    case_specs.append({
        "id": "indep_003_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "Client ingress gateway timeout on Application at t=0s; true root EC2 CPU saturation occurs at t=18s (>10s later). DynamoDB and Auth emit healthy heartbeats.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="Application", metric="GatewayTimeout", value=14.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="EC2", metric="ExecutionOverload", value=96.4, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="cloudwatch", service="API", metric="InternalServerErrors", value=30.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_004 (12-node): PostgresPrimary is root, decoy on OrderService at t=0s, root anom at t=22s
    case_specs.append({
        "id": "indep_004_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "Transient 503 error on OrderService at t=0s; PostgresPrimary database connection pool exhaustion surfaces at t=22s (>10s later).",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="OrderService", metric="TransientDropRate", value=42.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="MaxConnectionsReached", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=32)).isoformat(), observed_time=(t_base + timedelta(seconds=33)).isoformat(), source="cloudwatch", service="APIGateway", metric="HTTP500Rate", value=50.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_005 (12-node): RedisCache is root, decoy on CatalogService at t=0s, root anom at t=24s
    case_specs.append({
        "id": "indep_005_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["RedisCache"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["RedisCache"], "max_level": "CONFIRMED"}],
        "rationale": "CatalogService latency spike at t=0s; RedisCache eviction storm and memory exhaustion surfaces at t=24s (>10s later).",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="CatalogService", metric="LookupLatencyFluctuation", value=220.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=24)).isoformat(), observed_time=(t_base + timedelta(seconds=25)).isoformat(), source="cloudwatch", service="RedisCache", metric="KeyEvictionSurge", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=34)).isoformat(), observed_time=(t_base + timedelta(seconds=35)).isoformat(), source="cloudwatch", service="APIGateway", metric="HTTP500Rate", value=45.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_006 (12-node): KafkaCluster is root, decoy on NotificationService at t=0s, root anom at t=26s
    case_specs.append({
        "id": "indep_006_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["KafkaCluster"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["KafkaCluster"], "max_level": "CONFIRMED"}],
        "rationale": "NotificationService task timeout at t=0s; KafkaCluster partition broker disk out-of-space at t=26s (>10s later).",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="NotificationService", metric="TaskExecutionTimeout", value=18.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=26)).isoformat(), observed_time=(t_base + timedelta(seconds=27)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="DiskCapacityExhausted", value=99.8, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=36)).isoformat(), observed_time=(t_base + timedelta(seconds=37)).isoformat(), source="cloudwatch", service="OrderService", metric="PublishingBackpressure", value=75.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 2. SILENT ROOT x4 (2 on 6-node, 2 on 12-node)
    # Rule: True root has ZERO events. Competing candidates have >= 2 healthy heartbeats inside window.
    # At least 2 dependents are anomalous.
    # Metric names must NOT contain node id or label words. Max verdict is PROBABLE.
    # =========================================================================
    # indep_007 (6-node): EC2 is silent root; API and Worker fail; DynamoDB and Auth have >= 2 heartbeats
    case_specs.append({
        "id": "indep_007_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "true_roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "PROBABLE"}],
        "rationale": "EC2 emits zero events (kernel hard crash); dependents API and Worker report socket connection timeouts. Competing candidates DynamoDB and Auth emit verified healthy heartbeats inside window.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="SocketConnectionRefused", value=45.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="Worker", metric="HostUnreachable", value=30.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="Application", metric="HTTP502Rate", value=25.0, severity="HIGH"),
            # Competing candidates DynamoDB and Auth have >= 2 heartbeats inside window
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_008 (6-node): DynamoDB is silent root; API and Worker fail; EC2 and Auth have >= 2 heartbeats
    case_specs.append({
        "id": "indep_008_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "PROBABLE"}],
        "rationale": "DynamoDB partition silent drop emits zero events; dependents API and Worker report storage timeouts. Competing candidates EC2 and Auth emit verified healthy heartbeats inside window.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="WriteTimeout", value=50.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="Worker", metric="CommitFailure", value=40.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="Application", metric="HTTP502Rate", value=20.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_009 (12-node): PostgresPrimary is silent root; OrderService and InventoryService fail
    case_specs.append({
        "id": "indep_009_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "PROBABLE"}],
        "rationale": "PostgresPrimary connection exhaustion silent freeze; dependents OrderService and InventoryService fail. Alternative backend dependencies emit healthy heartbeats inside window.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="OrderService", metric="DatabaseTransactionTimeout", value=60.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="InventoryService", metric="QueryTimeout", value=55.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="APIGateway", metric="HTTP500Rate", value=40.0, severity="HIGH"),
            # Competing candidates have >= 2 heartbeats inside window
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_07", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_08", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_09", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_10", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_11", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_12", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_13", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_14", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_15", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_16", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_010 (12-node): K8sCluster is silent root; OrderService and CatalogService fail
    case_specs.append({
        "id": "indep_010_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "true_roots": ["K8sCluster"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["K8sCluster"], "max_level": "PROBABLE"}],
        "rationale": "K8sCluster compute worker pool silent freeze emits zero telemetry; dependents OrderService and CatalogService report connection failures. Competing candidates PostgresPrimary and RedisCache emit healthy heartbeats inside window.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="OrderService", metric="SocketConnectionRefused", value=70.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="CatalogService", metric="HostUnreachable", value=65.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="APIGateway", metric="HTTP500Rate", value=45.0, severity="HIGH"),
            # Competing candidates for OrderService and CatalogService have >= 2 heartbeats inside window
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_07", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_08", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_09", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_10", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_11", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_12", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_13", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_14", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_15", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_16", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # =========================================================================
    # 3. IDLE HEALTHY x4 (2 on 6-node, 2 on 12-node)
    # Rule: Diagnosable failure case with real root failure PLUS idle healthy non-roots
    # with heartbeats inside the incident window.
    # =========================================================================
    # indep_011 (6-node): DynamoDB fails; Auth and EC2 are idle healthy non-roots
    case_specs.append({
        "id": "indep_011_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "DynamoDB experiences critical write throttle failure; non-roots Auth and EC2 are idle and emit healthy heartbeats inside window.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageThrottleRate", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="API", metric="InternalServerErrorRate", value=50.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_012 (6-node): EC2 fails; DynamoDB and Auth are idle healthy non-roots
    case_specs.append({
        "id": "indep_012_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "true_roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "EC2 hardware degradation causes critical compute failure; non-roots DynamoDB and Auth are idle with healthy heartbeats.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="EC2", metric="CPULoadAverage", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="API", metric="UpstreamTimeoutRate", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_013 (12-node): PostgresPrimary fails; RedisCache and NotificationService are idle healthy
    case_specs.append({
        "id": "indep_013_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "PostgresPrimary lock escalation failure; RedisCache and NotificationService are idle healthy non-roots emitting heartbeats.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="MaxConnectionsReached", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="DatabaseTransactionTimeout", value=60.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_014 (12-node): PaymentService fails; CatalogService and KafkaCluster are idle healthy
    case_specs.append({
        "id": "indep_014_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "true_roots": ["PaymentService"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PaymentService"], "max_level": "CONFIRMED"}],
        "rationale": "PaymentService third-party gateway timeout failure; CatalogService and KafkaCluster are idle healthy non-roots emitting heartbeats.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PaymentService", metric="ExternalHTTP500", value=85.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="CheckoutFailureRate", value=55.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 4. TWO INDEPENDENT ROOTS x4 (2 on 6-node, 2 on 12-node)
    # =========================================================================
    # indep_015 (6-node): EC2 and DynamoDB fail simultaneously
    case_specs.append({
        "id": "indep_015_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "true_roots": ["EC2", "DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["EC2", "DynamoDB"], "max_level": "CONFIRMED"},
            {"verdict": "DIAGNOSE", "roots": ["DynamoDB", "EC2"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Independent simultaneous outages on compute fleet EC2 and database DynamoDB.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="PhysicalMemorySaturation", value=98.5, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageThrottleRate", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="InternalServerErrorRate", value=80.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_016 (6-node): Auth and DynamoDB fail simultaneously
    case_specs.append({
        "id": "indep_016_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "true_roots": ["Auth", "DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["Auth", "DynamoDB"], "max_level": "CONFIRMED"},
            {"verdict": "DIAGNOSE", "roots": ["DynamoDB", "Auth"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Independent simultaneous outages on Auth microservice and DynamoDB primary table.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="CertificateExpired", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=14)).isoformat(), observed_time=(t_base + timedelta(seconds=15)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageThrottleRate", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="InternalServerErrorRate", value=85.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_017 (12-node): PostgresPrimary and RedisCache fail simultaneously
    case_specs.append({
        "id": "indep_017_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "true_roots": ["PostgresPrimary", "RedisCache"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["PostgresPrimary", "RedisCache"], "max_level": "CONFIRMED"},
            {"verdict": "DIAGNOSE", "roots": ["RedisCache", "PostgresPrimary"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Dual root cause incident: PostgresPrimary database disk full plus RedisCache out of memory.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="DiskCapacityExhausted", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="RedisCache", metric="KeyEvictionSurge", value=96.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="OrderService", metric="DatabaseTransactionTimeout", value=70.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=24)).isoformat(), observed_time=(t_base + timedelta(seconds=25)).isoformat(), source="cloudwatch", service="CatalogService", metric="LookupLatencyFluctuation", value=80.0, severity="CRITICAL")
        ]
    })

    # indep_018 (12-node): PaymentService and KafkaCluster fail simultaneously
    case_specs.append({
        "id": "indep_018_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "true_roots": ["PaymentService", "KafkaCluster"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["PaymentService", "KafkaCluster"], "max_level": "CONFIRMED"},
            {"verdict": "DIAGNOSE", "roots": ["KafkaCluster", "PaymentService"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Dual root cause incident: PaymentService external failure plus KafkaCluster broker outage.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PaymentService", metric="ExternalHTTP500", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=13)).isoformat(), observed_time=(t_base + timedelta(seconds=14)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="DiskCapacityExhausted", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="OrderService", metric="CheckoutFailureRate", value=65.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=26)).isoformat(), observed_time=(t_base + timedelta(seconds=27)).isoformat(), source="cloudwatch", service="NotificationService", metric="TaskExecutionTimeout", value=70.0, severity="CRITICAL")
        ]
    })

    # =========================================================================
    # 5. EDGE ROOT x3 (1 on 6-node, 2 on 12-node)
    # Rule: Root is an entrypoint service (API, gateway, frontend) whose callees are noisy.
    # Leaf backends do not count.
    # =========================================================================
    # indep_019 (6-node): Application (frontend client ingress) flood causes downstream errors
    case_specs.append({
        "id": "indep_019_edge_root_entrypoint_6node",
        "topo": topo_6,
        "category": "edge_root",
        "true_roots": ["Application"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["Application"], "max_level": "CONFIRMED"}],
        "rationale": "Frontend entrypoint Application client bug generates excessive flood volume (10k req/s), causing downstream API rate limits and Worker saturation.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="application", service="Application", metric="RequestFloodVolume", value=10000.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="API", metric="RateLimitThrottling", value=250.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Worker", metric="QueueSaturated", value=95.0, severity="HIGH")
        ]
    })

    # indep_020 (12-node): APIGateway entrypoint routing bug causes OrderService and CatalogService errors
    case_specs.append({
        "id": "indep_020_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "true_roots": ["APIGateway"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["APIGateway"], "max_level": "CONFIRMED"}],
        "rationale": "Edge ingress APIGateway misconfiguration routes malformed payload headers causing downstream OrderService and CatalogService crashes.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="APIGateway", metric="MalformedHeaderRate", value=500.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=16)).isoformat(), observed_time=(t_base + timedelta(seconds=17)).isoformat(), source="cloudwatch", service="OrderService", metric="PayloadDecodeErrors", value=120.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="CatalogService", metric="PayloadDecodeErrors", value=110.0, severity="HIGH")
        ]
    })

    # indep_021 (12-node): APIGateway entrypoint auth-header stripping causes downstream errors
    case_specs.append({
        "id": "indep_021_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "true_roots": ["APIGateway"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["APIGateway"], "max_level": "CONFIRMED"}],
        "rationale": "Edge ingress APIGateway strips required session tokens, triggering auth rejections across OrderService and CatalogService.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="APIGateway", metric="HeaderDropCount", value=350.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="OrderService", metric="ValidationErrorCount", value=90.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=17)).isoformat(), observed_time=(t_base + timedelta(seconds=18)).isoformat(), source="cloudwatch", service="AuthService", metric="VerificationSpike", value=80.0, severity="HIGH")
        ]
    })

    # =========================================================================
    # 6. SOURCE DOWN x3 (2 on 6-node, 1 on 12-node) -> ABSTAIN REQUIRED
    # =========================================================================
    # indep_022 (6-node): cloudwatch is DOWN
    src_down_cw = default_sources()
    for s in src_down_cw:
        if s.source == "cloudwatch":
            s.status = "DOWN"
    case_specs.append({
        "id": "indep_022_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "ABSTAIN",
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Telemetry source cloudwatch is DOWN; engine cannot observe primary metrics for database and must ABSTAIN.",
        "source_health": src_down_cw,
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="API", metric="InternalServerErrorRate", value=50.0, severity="CRITICAL")
        ]
    })

    # indep_023 (6-node): syslog is DOWN
    src_down_sys = default_sources()
    for s in src_down_sys:
        if s.source == "syslog":
            s.status = "DOWN"
    case_specs.append({
        "id": "indep_023_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "true_roots": ["EC2"],
        "expected_verdict": "ABSTAIN",
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Telemetry source syslog is DOWN; compute cluster system logs are missing, engine must ABSTAIN.",
        "source_health": src_down_sys,
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="API", metric="SocketConnectionRefused", value=40.0, severity="CRITICAL")
        ]
    })

    # indep_024 (12-node): cloudwatch is DOWN
    case_specs.append({
        "id": "indep_024_source_down_abstain_12node",
        "topo": topo_12,
        "category": "source_down",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "ABSTAIN",
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Telemetry source cloudwatch is DOWN; database alarms cannot be validated, engine must ABSTAIN.",
        "source_health": src_down_cw,
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="OrderService", metric="DatabaseTransactionTimeout", value=55.0, severity="CRITICAL")
        ]
    })

    # =========================================================================
    # 7. CONTRADICTORY x2 (1 on 6-node, 1 on 12-node) -> Multiple acceptable outcomes
    # Rule: True root emits healthy heartbeats, but caller blames it.
    # Defensible answers: ABSTAIN, or diagnose the blaming caller at PROBABLE.
    # Metric names must NOT contain node id or label words.
    # =========================================================================
    # indep_025 (6-node): API blames database dependency, but DynamoDB emits healthy heartbeats
    case_specs.append({
        "id": "indep_025_contradiction_blamed_healthy_6node",
        "topo": topo_6,
        "category": "contradictory",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "ABSTAIN",
        "outcomes": [
            {"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"},
            {"verdict": "DIAGNOSE", "roots": ["API"], "max_level": "PROBABLE"}
        ],
        "rationale": "Contradiction: API reports critical storage connection blame, but DynamoDB emits verified healthy heartbeats. Engine must ABSTAIN or diagnose API at PROBABLE.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="API", metric="ConnectionTimeout", value=100.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_026 (12-node): OrderService blames database, but PostgresPrimary emits healthy heartbeats
    case_specs.append({
        "id": "indep_026_contradiction_blamed_healthy_12node",
        "topo": topo_12,
        "category": "contradictory",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "ABSTAIN",
        "outcomes": [
            {"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"},
            {"verdict": "DIAGNOSE", "roots": ["OrderService"], "max_level": "PROBABLE"}
        ],
        "rationale": "Contradiction: OrderService reports database lock blame, but PostgresPrimary emits verified healthy heartbeats. Engine must ABSTAIN or diagnose OrderService at PROBABLE.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="OrderService", metric="LockWaitTimeout", value=100.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 8. BENIGN HEARTBEATS x2 (1 on 6-node, 1 on 12-node)
    # =========================================================================
    # indep_027 (6-node): DynamoDB fails with write throttle; routine heartbeats on all nodes
    case_specs.append({
        "id": "indep_027_benign_heartbeats_6node",
        "topo": topo_6,
        "category": "benign_heartbeats",
        "true_roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "Clear failure on DynamoDB with concurrent periodic benign heartbeats on non-roots.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageThrottleRate", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="API", metric="InternalServerErrorRate", value=50.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # indep_028 (12-node): PostgresPrimary fails; routine heartbeats on non-roots
    case_specs.append({
        "id": "indep_028_benign_heartbeats_12node",
        "topo": topo_12,
        "category": "benign_heartbeats",
        "true_roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "Clear failure on PostgresPrimary with concurrent periodic benign heartbeats on non-roots.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="MaxConnectionsReached", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="DatabaseTransactionTimeout", value=55.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 9. NO ANOMALY (all-quiet negative control) x2 (1 on 6-node, 1 on 12-node) -> ABSTAIN REQUIRED
    # =========================================================================
    # indep_029 (6-node): All nodes emit only healthy heartbeats
    case_specs.append({
        "id": "indep_029_no_anomaly_6node",
        "topo": topo_6,
        "category": "no_anomaly",
        "true_roots": [],
        "expected_verdict": "ABSTAIN",
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Entire 6-node system is completely healthy; zero anomalies observed; engine must ABSTAIN.",
        "events": [
            EventData(event_id=f"hb_6_{n.id}", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service=n.id, metric="Heartbeat", value=1.0, severity="LOW")
            for n in topo_6_nodes
        ]
    })

    # indep_030 (12-node): All nodes emit only healthy heartbeats
    case_specs.append({
        "id": "indep_030_no_anomaly_12node",
        "topo": topo_12,
        "category": "no_anomaly",
        "true_roots": [],
        "expected_verdict": "ABSTAIN",
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Entire 12-node system is completely healthy; zero anomalies observed; engine must ABSTAIN.",
        "events": [
            EventData(event_id=f"hb_12_{n.id}", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service=n.id, metric="Heartbeat", value=1.0, severity="LOW")
            for n in topo_12_nodes
        ]
    })

    # =========================================================================
    # Write cases to JSON files
    # =========================================================================
    assert len(case_specs) == 30, f"Expected exactly 30 cases, got {len(case_specs)}"

    for s in case_specs:
        outcomes_objs = [AcceptableOutcome(**o) for o in s["outcomes"]]
        prim_verdict = s.get("expected_verdict", outcomes_objs[0].verdict)
        prim_max = outcomes_objs[0].max_level or "CONFIRMED"

        gt = GroundTruth(
            true_root_causes=s.get("true_roots", []),
            onset_times={},
            symptom_to_cause={},
            expected_verdict=prim_verdict,
            acceptable_verdict_level=prim_max,
            acceptable_outcomes=outcomes_objs,
            identifiability_note=s.get("rationale", "")
        )

        case = EvaluationCase(
            case_id=s["id"],
            family_id="indep_eval",
            base_case_id=None,
            generator_version="independent_handwritten_2.0",
            seed=20261016,
            topology=s["topo"],
            events=s["events"],
            source_health=s.get("source_health", default_sources()),
            ground_truth=gt,
            perturbations=[s["category"]],
            tags=[s["category"], "independent_test"],
            is_hard=False,
            telemetry_policy=None,
            identifiability_note=s.get("rationale", ""),
            acceptable_verdict_level=prim_max,
            acceptable_outcomes=outcomes_objs
        )

        p = indep_dir / f"{s['id']}.json"
        p.write_text(case.model_dump_json(indent=2), encoding="utf-8")

    print(f"Successfully generated {len(case_specs)} independent cases in {indep_dir}.")


if __name__ == "__main__":
    build_independent_cases()
