import sys
import json
import math
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple
from collections import defaultdict

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

    # Clean existing cases
    for f in indep_dir.glob("*.json"):
        f.unlink()

    # 1. Real 6-node topology
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

    case_specs = []

    # =========================================================================
    # 1. ROOT NOT EARLIEST x6 (3 on 6-node, 3 on 12-node)
    # Severity relationship is randomized/balanced across cases:
    #   indep_001: Root CRITICAL, Decoy HIGH  (Root HIGHER)
    #   indep_002: Root CRITICAL, Decoy CRITICAL (Root EQUAL)
    #   indep_003: Root HIGH, Decoy CRITICAL (Root LOWER)
    #   indep_004: Root CRITICAL, Decoy HIGH  (Root HIGHER)
    #   indep_005: Root CRITICAL, Decoy CRITICAL (Root EQUAL)
    #   indep_006: Root HIGH, Decoy CRITICAL (Root LOWER)
    # Decoys satisfy Identifiability Rule: have healthy heartbeats/metrics AFTER
    # their decoy events inside the symptom window.
    # Decoy onsets are randomized over seeded range (5s..20s) so decoy onset variance is non-zero.
    # =========================================================================
    case_specs.append({
        "id": "indep_001_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["DynamoDB"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "Early transient identity validation anomaly on Auth at t=5s acts as decoy; true root DynamoDB write throttle surfaces at t=45s (>10s later). Auth emits verified healthy recovery metrics (0.0 LOW) at t=28s and t=35s before API symptom at t=88s. DynamoDB has 1 CRITICAL + 1 HIGH, while Auth has 2 HIGH (root higher severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="application", service="Auth", metric="IdentityValidationStall", value=85.0, severity="HIGH"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=19)).isoformat(), observed_time=(t_base + timedelta(seconds=20)).isoformat(), source="application", service="Auth", metric="TokenSignatureReject", value=80.0, severity="HIGH"),
            EventData(event_id="rec_auth_01", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="application", service="Auth", metric="IdentityValidationStall", value=0.0, severity="LOW"),
            EventData(event_id="rec_auth_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="application", service="Auth", metric="TokenSignatureReject", value=0.0, severity="LOW"),
            EventData(event_id="hb_auth_01", event_time=(t_base + timedelta(seconds=38)).isoformat(), observed_time=(t_base + timedelta(seconds=39)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=92.5, severity="CRITICAL"),
            EventData(event_id="hb_auth_02", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=88.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=88)).isoformat(), observed_time=(t_base + timedelta(seconds=89)).isoformat(), source="application", service="API", metric="ServiceRequestHang", value=100.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_002_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["EC2"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "Transient storage lock spike on DynamoDB at t=10s acts as decoy; true root EC2 freeze surfaces at t=68s (>10s later). DynamoDB emits verified healthy recovery metrics (0.0 LOW) at t=38s and t=48s before API symptom at t=118s. Both EC2 and DynamoDB have 2 CRITICAL events (equal severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=94.0, severity="CRITICAL"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=26)).isoformat(), observed_time=(t_base + timedelta(seconds=27)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=95.0, severity="CRITICAL"),
            EventData(event_id="rec_ddb_01", event_time=(t_base + timedelta(seconds=38)).isoformat(), observed_time=(t_base + timedelta(seconds=39)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=0.0, severity="LOW"),
            EventData(event_id="rec_ddb_02", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=0.0, severity="LOW"),
            EventData(event_id="hb_ddb_01", event_time=(t_base + timedelta(seconds=52)).isoformat(), observed_time=(t_base + timedelta(seconds=53)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ddb_02", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=99.2, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=95)).isoformat(), observed_time=(t_base + timedelta(seconds=96)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=118)).isoformat(), observed_time=(t_base + timedelta(seconds=119)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=45.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_003_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "true_roots": ["Auth"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["Auth"], "max_level": "CONFIRMED"}],
        "rationale": "Early thread context storm on EC2 at t=16s acts as decoy; true root Auth token signature reject triggers at t=90s (>10s later). EC2 emits verified healthy recovery metrics (8.0 LOW) at t=50s and t=62s before API symptom at t=138s. EC2 has 2 CRITICAL events, while Auth has 1 HIGH + 1 MEDIUM (root lower severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=16)).isoformat(), observed_time=(t_base + timedelta(seconds=17)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=98.0, severity="CRITICAL"),
            EventData(event_id="rec_ec2_01", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=8.0, severity="LOW"),
            EventData(event_id="rec_ec2_02", event_time=(t_base + timedelta(seconds=62)).isoformat(), observed_time=(t_base + timedelta(seconds=63)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=5.0, severity="LOW"),
            EventData(event_id="hb_ec2_01", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ec2_02", event_time=(t_base + timedelta(seconds=80)).isoformat(), observed_time=(t_base + timedelta(seconds=81)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=90)).isoformat(), observed_time=(t_base + timedelta(seconds=91)).isoformat(), source="application", service="Auth", metric="TokenSignatureReject", value=90.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=118)).isoformat(), observed_time=(t_base + timedelta(seconds=119)).isoformat(), source="application", service="Auth", metric="IdentityValidationStall", value=75.0, severity="MEDIUM"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=138)).isoformat(), observed_time=(t_base + timedelta(seconds=139)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=50.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_004_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["PostgresPrimary"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "Early key lookup stall on RedisCache at t=8s; PostgresPrimary lock contention surfaces at t=112s (>10s later). RedisCache does not lie on dependency path to OrderService and emits healthy heartbeat at t=55s. PostgresPrimary has 1 CRITICAL + 1 HIGH, while RedisCache has 2 HIGH (root higher severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="application", service="RedisCache", metric="KeyLookupStall", value=88.0, severity="HIGH"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="application", service="RedisCache", metric="EvictionSpikeRate", value=85.0, severity="HIGH"),
            EventData(event_id="hb_redis_01", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=80)).isoformat(), observed_time=(t_base + timedelta(seconds=81)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=112)).isoformat(), observed_time=(t_base + timedelta(seconds=113)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=138)).isoformat(), observed_time=(t_base + timedelta(seconds=139)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=85.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=162)).isoformat(), observed_time=(t_base + timedelta(seconds=163)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=50.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_005_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["OrderService"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["OrderService"], "max_level": "CONFIRMED"}],
        "rationale": "Early payment stall on PaymentService at t=12s; OrderService execution hang triggers at t=132s (>10s later). PaymentService emits healthy recovery metrics (0.0 LOW) at t=65s and t=78s before APIGateway symptom at t=182s. Both OrderService and PaymentService have 2 CRITICAL events (equal severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ddb_00", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="application", service="PaymentService", metric="DownstreamCallTimeout", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="PaymentService", metric="ExecutionLatencySpike", value=96.0, severity="CRITICAL"),
            EventData(event_id="rec_pay_01", event_time=(t_base + timedelta(seconds=65)).isoformat(), observed_time=(t_base + timedelta(seconds=66)).isoformat(), source="application", service="PaymentService", metric="DownstreamCallTimeout", value=0.0, severity="LOW"),
            EventData(event_id="rec_pay_02", event_time=(t_base + timedelta(seconds=78)).isoformat(), observed_time=(t_base + timedelta(seconds=79)).isoformat(), source="application", service="PaymentService", metric="ExecutionLatencySpike", value=12.0, severity="LOW"),
            EventData(event_id="hb_pay_01", event_time=(t_base + timedelta(seconds=88)).isoformat(), observed_time=(t_base + timedelta(seconds=89)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_pay_02", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=132)).isoformat(), observed_time=(t_base + timedelta(seconds=133)).isoformat(), source="cloudwatch", service="OrderService", metric="ServiceRequestHang", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=155)).isoformat(), observed_time=(t_base + timedelta(seconds=156)).isoformat(), source="cloudwatch", service="OrderService", metric="EndpointFailureRate", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=182)).isoformat(), observed_time=(t_base + timedelta(seconds=183)).isoformat(), source="cloudwatch", service="APIGateway", metric="NetworkSocketTimeout", value=45.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_006_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "true_roots": ["K8sCluster"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["K8sCluster"], "max_level": "CONFIRMED"}],
        "rationale": "Early queue stall on KafkaCluster at t=20s; K8sCluster worker pool exhaustion surfaces at t=120s (>10s later). KafkaCluster emits healthy recovery metrics (0.0 LOW) at t=55s and t=68s before OrderService symptom at t=168s. KafkaCluster has 2 CRITICAL events, while K8sCluster has 1 HIGH + 1 MEDIUM (root lower severity).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01a", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="KafkaCluster", metric="PartitionLagSurge", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_01b", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="KafkaCluster", metric="MessageDropRate", value=97.0, severity="CRITICAL"),
            EventData(event_id="rec_kafka_01", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="application", service="KafkaCluster", metric="PartitionLagSurge", value=0.0, severity="LOW"),
            EventData(event_id="rec_kafka_02", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="application", service="KafkaCluster", metric="MessageDropRate", value=0.0, severity="LOW"),
            EventData(event_id="hb_kafka_01", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_kafka_02", event_time=(t_base + timedelta(seconds=95)).isoformat(), observed_time=(t_base + timedelta(seconds=96)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=120)).isoformat(), observed_time=(t_base + timedelta(seconds=121)).isoformat(), source="cloudwatch", service="K8sCluster", metric="HostResourceSaturation", value=88.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=145)).isoformat(), observed_time=(t_base + timedelta(seconds=146)).isoformat(), source="cloudwatch", service="K8sCluster", metric="ThreadContextSwitchStorm", value=75.0, severity="MEDIUM"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=168)).isoformat(), observed_time=(t_base + timedelta(seconds=169)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=75.0, severity="HIGH"),
        ]
    })

    # =========================================================================
    # 2. SILENT ROOT x4 (2 on 6-node, 2 on 12-node)
    # True root has 0 events. Dependents fail. Competing candidates have heartbeats inside window.
    # Metric names on Worker and InventoryService use NEUTRAL vocabulary (NO "Storage...").
    # =========================================================================
    case_specs.append({
        "id": "indep_007_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "true_roots": ["EC2"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "PROBABLE"}],
        "rationale": "EC2 emits zero events (hard crash); dependents API and Worker fail between t+35s and t+95s. Competing candidates DynamoDB and Auth emit healthy heartbeats before symptom and inside [t+35s, t+95s].",
        "events": [
            EventData(event_id="hb_ddb_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth_pre", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="API", metric="NetworkSocketTimeout", value=45.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="cloudwatch", service="Worker", metric="ConnectionPoolExhaustion", value=30.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=72)).isoformat(), observed_time=(t_base + timedelta(seconds=73)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=50.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=95)).isoformat(), observed_time=(t_base + timedelta(seconds=96)).isoformat(), source="cloudwatch", service="Worker", metric="TaskExecutionStall", value=40.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=42)).isoformat(), observed_time=(t_base + timedelta(seconds=43)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=82)).isoformat(), observed_time=(t_base + timedelta(seconds=83)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=88)).isoformat(), observed_time=(t_base + timedelta(seconds=89)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_008: DynamoDB silent root. Worker metric renamed to neutral ProcessThreadSaturation and TaskExecutionStall!
    case_specs.append({
        "id": "indep_008_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "true_roots": ["DynamoDB"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "PROBABLE"}],
        "rationale": "DynamoDB partition silent drop emits zero events; dependents API and Worker fail between t+45s and t+115s. Competing candidates EC2 and Auth emit verified healthy heartbeats before symptom and inside [t+45s, t+115s]. Worker uses neutral metrics ProcessThreadSaturation and TaskExecutionStall.",
        "events": [
            EventData(event_id="hb_ec2_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth_pre", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="API", metric="NetworkSocketTimeout", value=50.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=58)).isoformat(), observed_time=(t_base + timedelta(seconds=59)).isoformat(), source="cloudwatch", service="Worker", metric="ProcessThreadSaturation", value=40.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=88)).isoformat(), observed_time=(t_base + timedelta(seconds=89)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=45.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=115)).isoformat(), observed_time=(t_base + timedelta(seconds=116)).isoformat(), source="cloudwatch", service="Worker", metric="TaskExecutionStall", value=35.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=52)).isoformat(), observed_time=(t_base + timedelta(seconds=53)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=98)).isoformat(), observed_time=(t_base + timedelta(seconds=99)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_009: PostgresPrimary silent root. InventoryService uses neutral DownstreamCallTimeout!
    case_specs.append({
        "id": "indep_009_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "true_roots": ["PostgresPrimary"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "PROBABLE"}],
        "rationale": "PostgresPrimary silent stall emits zero events; dependents OrderService and InventoryService fail between t+50s and t+125s. Competing candidates emit heartbeats inside [t+50s, t+125s].",
        "events": [
            EventData(event_id="hb_pay_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_inv_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_k8s_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_notif_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_cat_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ddb_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_redis_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_kafka_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=70.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="OrderService", metric="InternalExecutionFailure", value=50.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=85)).isoformat(), observed_time=(t_base + timedelta(seconds=86)).isoformat(), source="cloudwatch", service="InventoryService", metric="DownstreamCallTimeout", value=55.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="InventoryService", metric="NetworkSocketTimeout", value=40.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_07", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_08", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_09", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_10", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_11", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_12", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_13", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_14", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_15", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_16", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    case_specs.append({
        "id": "indep_010_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "true_roots": ["K8sCluster"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["K8sCluster"], "max_level": "PROBABLE"}],
        "rationale": "K8sCluster host pool freeze emits zero events; dependents OrderService and CatalogService fail between t+60s and t+140s. Competing candidates emit heartbeats inside [t+60s, t+140s].",
        "events": [
            EventData(event_id="hb_pg_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_pay_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_redis_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ddb_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_inv_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_kafka_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_notif_pre", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=60.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=115)).isoformat(), observed_time=(t_base + timedelta(seconds=116)).isoformat(), source="cloudwatch", service="OrderService", metric="InternalExecutionFailure", value=45.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=78)).isoformat(), observed_time=(t_base + timedelta(seconds=79)).isoformat(), source="cloudwatch", service="CatalogService", metric="ServiceRequestHang", value=50.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=140)).isoformat(), observed_time=(t_base + timedelta(seconds=141)).isoformat(), source="cloudwatch", service="CatalogService", metric="EndpointFailureRate", value=40.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_07", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_08", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_09", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_10", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_11", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_12", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_13", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_14", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_15", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_16", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # =========================================================================
    # 3. IDLE HEALTHY x4 (2 on 6-node, 2 on 12-node)
    # Neutral metrics only. Wide onset spread: 25s, 55s, 85s, 115s.
    # Downstream symptom has CRITICAL severity in indep_011 and 013 so highest-severity baseline picks symptom.
    # =========================================================================
    case_specs.append({
        "id": "indep_011_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "true_roots": ["EC2"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "Real failure on EC2; non-root API suffers symptom; DynamoDB and Auth remain idle and healthy with heartbeats inside window.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=95.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=90.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=88)).isoformat(), observed_time=(t_base + timedelta(seconds=89)).isoformat(), source="cloudwatch", service="API", metric="NetworkSocketTimeout", value=85.0, severity="HIGH"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="cloudwatch", service="API", metric="ServiceRequestHang", value=75.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=38)).isoformat(), observed_time=(t_base + timedelta(seconds=39)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=42)).isoformat(), observed_time=(t_base + timedelta(seconds=43)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_012_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "true_roots": ["DynamoDB"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "Real failure on DynamoDB; Worker reports process stall; EC2 and Auth remain idle and healthy with heartbeats inside window.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=89)).isoformat(), observed_time=(t_base + timedelta(seconds=90)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=88.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=107)).isoformat(), observed_time=(t_base + timedelta(seconds=108)).isoformat(), source="cloudwatch", service="Worker", metric="TaskExecutionStall", value=45.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="Worker", metric="ProcessThreadSaturation", value=80.0, severity="HIGH"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=140)).isoformat(), observed_time=(t_base + timedelta(seconds=141)).isoformat(), source="cloudwatch", service="Worker", metric="ConnectionPoolExhaustion", value=75.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=74)).isoformat(), observed_time=(t_base + timedelta(seconds=75)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_013_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "true_roots": ["PostgresPrimary"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "Real failure on PostgresPrimary; OrderService shows internal execution failure; CatalogService and RedisCache remain idle with heartbeats.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=85)).isoformat(), observed_time=(t_base + timedelta(seconds=86)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=98.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=118)).isoformat(), observed_time=(t_base + timedelta(seconds=119)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=92.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=139)).isoformat(), observed_time=(t_base + timedelta(seconds=140)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=95.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=97)).isoformat(), observed_time=(t_base + timedelta(seconds=98)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=103)).isoformat(), observed_time=(t_base + timedelta(seconds=104)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_014_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "true_roots": ["RedisCache"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["RedisCache"], "max_level": "CONFIRMED"}],
        "rationale": "Real failure on RedisCache; CatalogService shows lookup stalls; PostgresPrimary and PaymentService remain idle with heartbeats.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_bg_mid", event_time=(t_base + timedelta(seconds=58)).isoformat(), observed_time=(t_base + timedelta(seconds=59)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=115)).isoformat(), observed_time=(t_base + timedelta(seconds=116)).isoformat(), source="cloudwatch", service="RedisCache", metric="KeyLookupStall", value=96.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=144)).isoformat(), observed_time=(t_base + timedelta(seconds=145)).isoformat(), source="cloudwatch", service="RedisCache", metric="EvictionSpikeRate", value=91.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=168)).isoformat(), observed_time=(t_base + timedelta(seconds=169)).isoformat(), source="cloudwatch", service="CatalogService", metric="ServiceRequestHang", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=126)).isoformat(), observed_time=(t_base + timedelta(seconds=127)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=134)).isoformat(), observed_time=(t_base + timedelta(seconds=135)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 4. TWO INDEPENDENT ROOTS x4 (2 on 6-node, 2 on 12-node)
    # Roots onset spread: 20s, 50s, 80s, 110s.
    # =========================================================================
    case_specs.append({
        "id": "indep_015_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "true_roots": ["EC2", "DynamoDB"],
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["EC2", "DynamoDB"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Concurrent independent failures on EC2 and DynamoDB with API observing transitive errors; both roots have 2 anomalous events. Full root set is the only fully correct outcome.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=52)).isoformat(), observed_time=(t_base + timedelta(seconds=53)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=88.0, severity="CRITICAL"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=50.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_016_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "true_roots": ["Auth", "DynamoDB"],
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["Auth", "DynamoDB"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Concurrent independent failures on Auth and DynamoDB; API experiences authorization and persistence failures. Full root set is the only fully correct outcome.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="application", service="Auth", metric="TokenSignatureReject", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="application", service="Auth", metric="IdentityValidationStall", value=85.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=94.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=82)).isoformat(), observed_time=(t_base + timedelta(seconds=83)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=98)).isoformat(), observed_time=(t_base + timedelta(seconds=99)).isoformat(), source="cloudwatch", service="API", metric="ServiceRequestHang", value=50.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_017_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "true_roots": ["PostgresPrimary", "RedisCache"],
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["PostgresPrimary", "RedisCache"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Concurrent independent storage and cache tier failures; both PostgresPrimary and RedisCache have 2 anomalous events. Full root set is the only fully correct outcome.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=82)).isoformat(), observed_time=(t_base + timedelta(seconds=83)).isoformat(), source="cloudwatch", service="RedisCache", metric="KeyLookupStall", value=96.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=112)).isoformat(), observed_time=(t_base + timedelta(seconds=113)).isoformat(), source="cloudwatch", service="RedisCache", metric="EvictionSpikeRate", value=89.0, severity="CRITICAL"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=130)).isoformat(), observed_time=(t_base + timedelta(seconds=131)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_018_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "true_roots": ["KafkaCluster", "DynamoTable"],
        "outcomes": [
            {"verdict": "DIAGNOSE", "roots": ["KafkaCluster", "DynamoTable"], "max_level": "CONFIRMED"}
        ],
        "rationale": "Concurrent independent failures on queue streaming tier KafkaCluster and ledger table DynamoTable; NotificationService and PaymentService report symptoms. Full root set is the only fully correct outcome.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="PartitionLagSurge", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=138)).isoformat(), observed_time=(t_base + timedelta(seconds=139)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="MessageDropRate", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=112)).isoformat(), observed_time=(t_base + timedelta(seconds=113)).isoformat(), source="cloudwatch", service="DynamoTable", metric="StorageWriteThrottle", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=145)).isoformat(), observed_time=(t_base + timedelta(seconds=146)).isoformat(), source="cloudwatch", service="DynamoTable", metric="StorageLockContention", value=88.0, severity="CRITICAL"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=165)).isoformat(), observed_time=(t_base + timedelta(seconds=166)).isoformat(), source="cloudwatch", service="PaymentService", metric="DownstreamCallTimeout", value=50.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 5. EDGE ROOT ENTRYPOINT x3 (1 on 6-node, 2 on 12-node)
    # Callees have 2 CRITICAL events (decoy), edge roots have anomalous events,
    # callers experience dependent symptoms with wide-range symptom delay variance.
    # Highest severity baseline picks callee (0/3).
    # Symptom delay spread: 35s, 53s, 70s.
    # =========================================================================
    case_specs.append({
        "id": "indep_019_edge_root_entrypoint_6node",
        "topo": topo_6,
        "category": "edge_root",
        "true_roots": ["API"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["API"], "max_level": "CONFIRMED"}],
        "rationale": "API edge entrypoint gateway failure; callee EC2 has 2 CRITICAL events (recovering at t=35s). Caller Application suffers dependent symptom at t=80s (symptom delay = 35s).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Worker", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=24)).isoformat(), observed_time=(t_base + timedelta(seconds=25)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=95.0, severity="CRITICAL"),
            EventData(event_id="rec_ec2_01", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=0.0, severity="LOW"),
            EventData(event_id="hb_ec2_01", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="API", metric="ServiceRequestHang", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=65)).isoformat(), observed_time=(t_base + timedelta(seconds=66)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=90.0, severity="HIGH"),
            EventData(event_id="rec_ec2_02", event_time=(t_base + timedelta(seconds=70)).isoformat(), observed_time=(t_base + timedelta(seconds=71)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=0.0, severity="LOW"),
            EventData(event_id="hb_ec2_02", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=80)).isoformat(), observed_time=(t_base + timedelta(seconds=81)).isoformat(), source="application", service="Application", metric="NetworkSocketTimeout", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_020_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "true_roots": ["OrderService"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["OrderService"], "max_level": "CONFIRMED"}],
        "rationale": "OrderService entrypoint failure; callee PaymentService has 2 CRITICAL events (recovering at t=38s, 48s). Caller APIGateway suffers dependent symptom at t=108s (symptom delay = 53s).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_ddb_00", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="application", service="PaymentService", metric="DownstreamCallTimeout", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="application", service="PaymentService", metric="ExecutionLatencySpike", value=95.0, severity="CRITICAL"),
            EventData(event_id="rec_pay_01", event_time=(t_base + timedelta(seconds=38)).isoformat(), observed_time=(t_base + timedelta(seconds=39)).isoformat(), source="application", service="PaymentService", metric="DownstreamCallTimeout", value=0.0, severity="LOW"),
            EventData(event_id="rec_pay_02", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="application", service="PaymentService", metric="ExecutionLatencySpike", value=0.0, severity="LOW"),
            EventData(event_id="hb_pay_01", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="OrderService", metric="ServiceRequestHang", value=88.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=78)).isoformat(), observed_time=(t_base + timedelta(seconds=79)).isoformat(), source="cloudwatch", service="OrderService", metric="InternalExecutionFailure", value=75.0, severity="MEDIUM"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=108)).isoformat(), observed_time=(t_base + timedelta(seconds=109)).isoformat(), source="cloudwatch", service="APIGateway", metric="NetworkSocketTimeout", value=95.0, severity="HIGH"),
            EventData(event_id="evt_06", event_time=(t_base + timedelta(seconds=125)).isoformat(), observed_time=(t_base + timedelta(seconds=126)).isoformat(), source="cloudwatch", service="APIGateway", metric="EndpointFailureRate", value=90.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_021_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "true_roots": ["CatalogService"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["CatalogService"], "max_level": "CONFIRMED"}],
        "rationale": "CatalogService edge failure; callee RedisCache has 2 CRITICAL events (recovering at t=45s, 55s). Caller APIGateway suffers dependent symptom at t=145s (symptom delay = 70s).",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="application", service="RedisCache", metric="KeyLookupStall", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=34)).isoformat(), observed_time=(t_base + timedelta(seconds=35)).isoformat(), source="application", service="RedisCache", metric="EvictionSpikeRate", value=96.0, severity="CRITICAL"),
            EventData(event_id="rec_redis_01", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="application", service="RedisCache", metric="KeyLookupStall", value=0.0, severity="LOW"),
            EventData(event_id="rec_redis_02", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="application", service="RedisCache", metric="EvictionSpikeRate", value=0.0, severity="LOW"),
            EventData(event_id="hb_redis_01", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="cloudwatch", service="CatalogService", metric="ServiceRequestHang", value=96.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=95)).isoformat(), observed_time=(t_base + timedelta(seconds=96)).isoformat(), source="cloudwatch", service="CatalogService", metric="EndpointFailureRate", value=92.0, severity="CRITICAL"),
            EventData(event_id="hb_cat_01", event_time=(t_base + timedelta(seconds=115)).isoformat(), observed_time=(t_base + timedelta(seconds=116)).isoformat(), source="cloudwatch", service="OrderService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=145)).isoformat(), observed_time=(t_base + timedelta(seconds=146)).isoformat(), source="cloudwatch", service="APIGateway", metric="NetworkSocketTimeout", value=50.0, severity="HIGH"),
            EventData(event_id="evt_06", event_time=(t_base + timedelta(seconds=160)).isoformat(), observed_time=(t_base + timedelta(seconds=161)).isoformat(), source="cloudwatch", service="APIGateway", metric="EndpointFailureRate", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 6. SOURCE DOWN x3 (2 on 6-node, 1 on 12-node) -> ABSTAIN REQUIRED
    # Telemetry source is DOWN. Engine must ABSTAIN.
    # Root onsets spread over wide seeded range: 18s, 42s, 75s (non-zero variance).
    # Symptom delays spread over wide range: 37s, 43s, 55s.
    # =========================================================================
    sh_cw_down = [
        SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="DOWN"),
        SourceHealthRecord(source="cloudtrail", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="syslog", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="application", timestamp=t_base.isoformat(), status="HEALTHY")
    ]
    case_specs.append({
        "id": "indep_022_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "true_roots": ["DynamoDB"],
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "source_health": sh_cw_down,
        "rationale": "Primary telemetry source cloudwatch is DOWN; engine cannot confirm unobserved dependencies; engine must ABSTAIN. Root DynamoDB starts at t=18s; dependent API symptoms appear at t=55s.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="application", service="DynamoDB", metric="StorageLockContention", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=36)).isoformat(), observed_time=(t_base + timedelta(seconds=37)).isoformat(), source="application", service="DynamoDB", metric="StorageWriteThrottle", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="application", service="API", metric="NetworkSocketTimeout", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="application", service="API", metric="EndpointFailureRate", value=85.0, severity="HIGH"),
        ]
    })

    sh_otel_down = [
        SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="cloudtrail", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="DOWN"),
        SourceHealthRecord(source="syslog", timestamp=t_base.isoformat(), status="HEALTHY"),
        SourceHealthRecord(source="application", timestamp=t_base.isoformat(), status="HEALTHY")
    ]
    case_specs.append({
        "id": "indep_023_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "true_roots": ["EC2"],
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "source_health": sh_otel_down,
        "rationale": "Tracing source otel is DOWN; cannot verify causal propagation delay; engine must ABSTAIN. Root EC2 starts at t=42s; dependent API symptoms appear at t=85s.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=42)).isoformat(), observed_time=(t_base + timedelta(seconds=43)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=65)).isoformat(), observed_time=(t_base + timedelta(seconds=66)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=85)).isoformat(), observed_time=(t_base + timedelta(seconds=86)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=45.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="cloudwatch", service="API", metric="NetworkSocketTimeout", value=40.0, severity="HIGH"),
        ]
    })

    case_specs.append({
        "id": "indep_024_source_down_abstain_12node",
        "topo": topo_12,
        "category": "source_down",
        "true_roots": ["PostgresPrimary"],
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "source_health": sh_cw_down,
        "rationale": "Primary metric stream cloudwatch is DOWN on 12-node enterprise cluster; engine must ABSTAIN. Root PostgresPrimary starts at t=75s; dependent OrderService symptoms appear at t=130s.",
        "events": [
            EventData(event_id="hb_bg_init", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="application", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_bg_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="application", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="application", service="PostgresPrimary", metric="StorageLockContention", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=100)).isoformat(), observed_time=(t_base + timedelta(seconds=101)).isoformat(), source="application", service="PostgresPrimary", metric="TransactionAbortRate", value=90.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=130)).isoformat(), observed_time=(t_base + timedelta(seconds=131)).isoformat(), source="application", service="OrderService", metric="InternalExecutionFailure", value=95.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=150)).isoformat(), observed_time=(t_base + timedelta(seconds=151)).isoformat(), source="application", service="OrderService", metric="ServiceRequestHang", value=80.0, severity="HIGH"),
        ]
    })

    # =========================================================================
    # 7. CONTRADICTORY (blaming caller vs healthy dependency) x2 (1 on 6-node, 1 on 12-node)
    # true_root_causes is EMPTY []. Blamed service is healthy. Engine must ABSTAIN or PROBABLE.
    # CONFIRMED on any root is confident-wrong.
    # =========================================================================
    case_specs.append({
        "id": "indep_025_contradiction_blamed_healthy_6node",
        "topo": topo_6,
        "category": "contradictory",
        "true_roots": [],
        "max_level_by_root": {"API": "PROBABLE", "DynamoDB": "ABSTAIN"},
        "outcomes": [
            {"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"},
            {"verdict": "DIAGNOSE", "roots": ["API"], "max_level": "PROBABLE"}
        ],
        "rationale": "Contradiction: API reports 2 critical timeouts explicitly blaming DynamoDB (target='DynamoDB'), but DynamoDB emits verified healthy metric telemetry (zero write throttle, zero lock contention) plus heartbeats. Engine must ABSTAIN or diagnose API at PROBABLE. CONFIRMED on any root is confident-wrong.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="application", service="API", metric="NetworkSocketTimeout", value=100.0, severity="CRITICAL", target="DynamoDB"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="application", service="API", metric="EndpointFailureRate", value=85.0, severity="HIGH", target="DynamoDB"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=0.0, severity="LOW"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=0.0, severity="LOW"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_026_contradiction_blamed_healthy_12node",
        "topo": topo_12,
        "category": "contradictory",
        "true_roots": [],
        "max_level_by_root": {"OrderService": "PROBABLE", "PostgresPrimary": "ABSTAIN"},
        "outcomes": [
            {"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"},
            {"verdict": "DIAGNOSE", "roots": ["OrderService"], "max_level": "PROBABLE"}
        ],
        "rationale": "Contradiction: OrderService reports 2 critical errors explicitly blaming database (target='PostgresPrimary'), but PostgresPrimary emits verified healthy metric telemetry (zero lock contention, zero transaction aborts) plus heartbeats. Engine must ABSTAIN or diagnose OrderService at PROBABLE. CONFIRMED on any root is confident-wrong.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="OrderService", metric="ConnectionPoolExhaustion", value=100.0, severity="CRITICAL", target="PostgresPrimary"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=65)).isoformat(), observed_time=(t_base + timedelta(seconds=66)).isoformat(), source="application", service="OrderService", metric="InternalExecutionFailure", value=80.0, severity="HIGH", target="PostgresPrimary"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=32)).isoformat(), observed_time=(t_base + timedelta(seconds=33)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=0.0, severity="LOW"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=0.0, severity="LOW"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 8. BENIGN HEARTBEATS x2 (1 on 6-node, 1 on 12-node)
    # Clear failure on true root; periodic heartbeats on healthy nodes.
    # Onsets: 18s, 72s.
    # =========================================================================
    case_specs.append({
        "id": "indep_027_benign_heartbeats_6node",
        "topo": topo_6,
        "category": "benign_heartbeats",
        "true_roots": ["DynamoDB"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["DynamoDB"], "max_level": "CONFIRMED"}],
        "rationale": "Clear failure on DynamoDB with concurrent periodic benign heartbeats on non-roots.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=36)).isoformat(), observed_time=(t_base + timedelta(seconds=37)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=92.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=54)).isoformat(), observed_time=(t_base + timedelta(seconds=55)).isoformat(), source="cloudwatch", service="API", metric="EndpointFailureRate", value=50.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=72)).isoformat(), observed_time=(t_base + timedelta(seconds=73)).isoformat(), source="cloudwatch", service="API", metric="NetworkSocketTimeout", value=40.0, severity="HIGH"),
            EventData(event_id="evt_05", event_time=(t_base + timedelta(seconds=90)).isoformat(), observed_time=(t_base + timedelta(seconds=91)).isoformat(), source="cloudwatch", service="API", metric="ServiceRequestHang", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_028_benign_heartbeats_12node",
        "topo": topo_12,
        "category": "benign_heartbeats",
        "true_roots": ["PostgresPrimary"],
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PostgresPrimary"], "max_level": "CONFIRMED"}],
        "rationale": "Clear failure on PostgresPrimary with concurrent periodic benign heartbeats on non-roots.",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=72)).isoformat(), observed_time=(t_base + timedelta(seconds=73)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=98)).isoformat(), observed_time=(t_base + timedelta(seconds=99)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=91.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=122)).isoformat(), observed_time=(t_base + timedelta(seconds=123)).isoformat(), source="cloudwatch", service="OrderService", metric="ConnectionPoolExhaustion", value=45.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 9. NO ANOMALY x2 (1 on 6-node, 1 on 12-node) -> ABSTAIN REQUIRED
    # Zero anomalies. Entire cluster healthy.
    # =========================================================================
    case_specs.append({
        "id": "indep_029_no_anomaly_6node",
        "topo": topo_6,
        "category": "no_anomaly",
        "true_roots": [],
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Entire 6-node cluster is completely healthy; zero anomalies observed; engine must ABSTAIN.",
        "events": [
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=26)).isoformat(), observed_time=(t_base + timedelta(seconds=27)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=34)).isoformat(), observed_time=(t_base + timedelta(seconds=35)).isoformat(), source="cloudwatch", service="Worker", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=42)).isoformat(), observed_time=(t_base + timedelta(seconds=43)).isoformat(), source="cloudwatch", service="API", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="Application", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    case_specs.append({
        "id": "indep_030_no_anomaly_12node",
        "topo": topo_12,
        "category": "no_anomaly",
        "true_roots": [],
        "outcomes": [{"verdict": "ABSTAIN", "roots": [], "max_level": "ABSTAIN"}],
        "rationale": "Entire 12-node enterprise cluster is completely healthy; zero anomalies observed; engine must ABSTAIN.",
        "events": [
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="cloudwatch", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=36)).isoformat(), observed_time=(t_base + timedelta(seconds=37)).isoformat(), source="cloudwatch", service="AuthService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_05", event_time=(t_base + timedelta(seconds=44)).isoformat(), observed_time=(t_base + timedelta(seconds=45)).isoformat(), source="cloudwatch", service="PaymentService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_06", event_time=(t_base + timedelta(seconds=52)).isoformat(), observed_time=(t_base + timedelta(seconds=53)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_07", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_08", event_time=(t_base + timedelta(seconds=68)).isoformat(), observed_time=(t_base + timedelta(seconds=69)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_09", event_time=(t_base + timedelta(seconds=76)).isoformat(), observed_time=(t_base + timedelta(seconds=77)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_10", event_time=(t_base + timedelta(seconds=84)).isoformat(), observed_time=(t_base + timedelta(seconds=85)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_11", event_time=(t_base + timedelta(seconds=92)).isoformat(), observed_time=(t_base + timedelta(seconds=93)).isoformat(), source="cloudwatch", service="K8sCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_12", event_time=(t_base + timedelta(seconds=100)).isoformat(), observed_time=(t_base + timedelta(seconds=101)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 10. MISLEADING BLAME WITH 3RD-PARTY ROOT x3 (New Requirement 3)
    # The blamed service is healthy, and a DIFFERENT service is the true root
    # with its own anomalous events. Proves "always guess blaming entrypoint at PROBABLE" fails!
    # Explicit blaming signal (target="...") grounds the blame claim.
    # Causal order: Root onset precedes dependent symptom onset (delay > 0).
    # Balanced severities:
    #   indep_031: Root CRITICAL, Blamer HIGH (Root HIGHER)
    #   indep_032: Root CRITICAL, Blamer CRITICAL (Root EQUAL)
    #   indep_033: Root HIGH, Blamer CRITICAL (Root LOWER)
    # =========================================================================
    # indep_031 (6-node): API blames DynamoDB, but EC2 is true root!
    case_specs.append({
        "id": "indep_031_misleading_blame_third_party_root_6node",
        "topo": topo_6,
        "category": "misleading_blame",
        "true_roots": ["EC2"],
        "max_level_by_root": {"EC2": "CONFIRMED", "API": "ABSTAIN", "DynamoDB": "ABSTAIN"},
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["EC2"], "max_level": "CONFIRMED"}],
        "rationale": "Misleading blame: API logs timeouts explicitly claiming DynamoDB is failing (target='DynamoDB'), but DynamoDB emits verified healthy metric telemetry (zero write throttle, zero lock contention) and heartbeats. The true root is EC2 starting at t=15s. API symptoms occur at t=55s (>t_root=15s, delay=40s). EC2 has 1 CRITICAL + 1 HIGH, while API has 2 HIGH (root higher severity).",
        "events": [
            # Blamed service DynamoDB emits verified healthy metric telemetry (value=0.0) starting at t=0s
            EventData(event_id="evt_05", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageWriteThrottle", value=0.0, severity="LOW"),
            EventData(event_id="evt_06", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StorageLockContention", value=0.0, severity="LOW"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            # True root EC2 starts at t=15s
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=98.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=88.0, severity="HIGH"),
            # Blaming dependent API reports errors with explicit blaming signal target="DynamoDB" AFTER root onset (t=55s)
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="application", service="API", metric="NetworkSocketTimeout", value=85.0, severity="HIGH", target="DynamoDB"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=75)).isoformat(), observed_time=(t_base + timedelta(seconds=76)).isoformat(), source="application", service="API", metric="EndpointFailureRate", value=80.0, severity="HIGH", target="DynamoDB"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=85)).isoformat(), observed_time=(t_base + timedelta(seconds=86)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_032 (12-node): OrderService blames PostgresPrimary, but PaymentService is true root!
    case_specs.append({
        "id": "indep_032_misleading_blame_third_party_root_12node",
        "topo": topo_12,
        "category": "misleading_blame",
        "true_roots": ["PaymentService"],
        "max_level_by_root": {"PaymentService": "CONFIRMED", "OrderService": "ABSTAIN", "PostgresPrimary": "ABSTAIN"},
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["PaymentService"], "max_level": "CONFIRMED"}],
        "rationale": "Misleading blame: OrderService reports errors explicitly claiming database failure (target='PostgresPrimary'), but PostgresPrimary emits verified healthy metric telemetry and heartbeats. The true root is PaymentService starting at t=35s. OrderService symptoms occur at t=80s (>t_root=35s, delay=45s). Both PaymentService and OrderService have 2 CRITICAL events (equal severity).",
        "events": [
            # Blamed service PostgresPrimary emits verified healthy metrics starting at t=0s
            EventData(event_id="evt_05", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="StorageLockContention", value=0.0, severity="LOW"),
            EventData(event_id="evt_06", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="TransactionAbortRate", value=0.0, severity="LOW"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            # True root PaymentService starts at t=35s
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="PaymentService", metric="DownstreamCallTimeout", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="PaymentService", metric="ExecutionLatencySpike", value=95.0, severity="CRITICAL"),
            # Blaming dependent OrderService reports errors with explicit blaming signal target="PostgresPrimary" AFTER root onset (t=80s)
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=80)).isoformat(), observed_time=(t_base + timedelta(seconds=81)).isoformat(), source="application", service="OrderService", metric="ConnectionPoolExhaustion", value=100.0, severity="CRITICAL", target="PostgresPrimary"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=105)).isoformat(), observed_time=(t_base + timedelta(seconds=106)).isoformat(), source="application", service="OrderService", metric="InternalExecutionFailure", value=92.0, severity="CRITICAL", target="PostgresPrimary"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=95)).isoformat(), observed_time=(t_base + timedelta(seconds=96)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
        ]
    })

    # indep_033 (6-node): API blames EC2, but Auth is true root!
    case_specs.append({
        "id": "indep_033_misleading_blame_third_party_root_6node",
        "topo": topo_6,
        "category": "misleading_blame",
        "true_roots": ["Auth"],
        "max_level_by_root": {"Auth": "CONFIRMED", "API": "ABSTAIN", "EC2": "ABSTAIN"},
        "outcomes": [{"verdict": "DIAGNOSE", "roots": ["Auth"], "max_level": "CONFIRMED"}],
        "rationale": "Misleading blame: API reports critical errors claiming EC2 host failure (target='EC2'), but EC2 emits verified healthy metric telemetry and heartbeats. The true root is Auth service starting at t=60s. API symptoms occur at t=115s (>t_root=60s, delay=55s). Auth has 1 HIGH + 1 MEDIUM, while API has 2 CRITICAL (root lower severity).",
        "events": [
            # Blamed service EC2 emits verified healthy metrics starting at t=0s
            EventData(event_id="evt_05", event_time=(t_base).isoformat(), observed_time=(t_base + timedelta(seconds=1)).isoformat(), source="cloudwatch", service="EC2", metric="HostResourceSaturation", value=5.0, severity="LOW"),
            EventData(event_id="evt_06", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="EC2", metric="ThreadContextSwitchStorm", value=2.0, severity="LOW"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            # True root Auth starts at t=60s with HIGH / MEDIUM severity
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=60)).isoformat(), observed_time=(t_base + timedelta(seconds=61)).isoformat(), source="application", service="Auth", metric="IdentityValidationStall", value=92.0, severity="HIGH"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=85)).isoformat(), observed_time=(t_base + timedelta(seconds=86)).isoformat(), source="application", service="Auth", metric="TokenSignatureReject", value=86.0, severity="MEDIUM"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=110)).isoformat(), observed_time=(t_base + timedelta(seconds=111)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            # Blaming dependent API reports 2 CRITICAL errors blaming EC2 (target="EC2") AFTER root onset (t=115s)
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=115)).isoformat(), observed_time=(t_base + timedelta(seconds=116)).isoformat(), source="application", service="API", metric="NetworkSocketTimeout", value=100.0, severity="CRITICAL", target="EC2"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=135)).isoformat(), observed_time=(t_base + timedelta(seconds=136)).isoformat(), source="application", service="API", metric="EndpointFailureRate", value=95.0, severity="CRITICAL", target="EC2"),
        ]
    })

    # Seeded RNG for heartbeats independent of failure template
    rng_hb = random.Random(20261016)
    all_heartbeat_offsets = []

    # Validate and write cases to disk (WITHOUT legacy fields expected_verdict or acceptable_verdict_level)
    written_count = 0
    for idx, spec in enumerate(case_specs, 1):
        topo = spec["topo"]
        cat = spec["category"]
        true_roots = set(spec["true_roots"])
        raw_events = spec["events"]

        t_min = min(datetime.fromisoformat(e.event_time) for e in raw_events)
        t_max = max(datetime.fromisoformat(e.event_time) for e in raw_events)
        total_span = max((t_max - t_min).total_seconds(), 60.0)

        # Identify services with events
        svcs_with_events = {e.service for e in raw_events}

        final_events = list(raw_events)

        # Ensure all background non-root services have healthy telemetry
        for node in topo.nodes:
            if cat == "silent_root" and node.id in true_roots:
                # Silent root must remain completely silent (0 events)
                continue
            if node.id not in svcs_with_events:
                o1 = round(rng_hb.uniform(10.0, min(35.0, total_span * 0.45)), 1)
                o2 = round(rng_hb.uniform(max(o1 + 10.0, total_span * 0.55), total_span), 1)
                t1 = t_min + timedelta(seconds=o1)
                t2 = t_min + timedelta(seconds=o2)
                final_events.append(
                    EventData(event_id=f"hb_bg_{node.id}_1", event_time=t1.isoformat(), observed_time=(t1 + timedelta(seconds=1)).isoformat(), source="cloudwatch", service=node.id, metric="Heartbeat", value=1.0, severity="LOW")
                )
                final_events.append(
                    EventData(event_id=f"hb_bg_{node.id}_2", event_time=t2.isoformat(), observed_time=(t2 + timedelta(seconds=1)).isoformat(), source="cloudwatch", service=node.id, metric="Heartbeat", value=1.0, severity="LOW")
                )

        # Collect heartbeat offsets for statistical reporting
        for e in final_events:
            if str(e.metric).lower() == "heartbeat":
                t_e = datetime.fromisoformat(e.event_time)
                all_heartbeat_offsets.append((t_e - t_min).total_seconds())

        # Sort chronologically by event_time
        final_events.sort(key=lambda ev: datetime.fromisoformat(ev.event_time))

        acceptable_objs = [
            AcceptableOutcome(verdict=o["verdict"], roots=o["roots"], max_level=o.get("max_level", "CONFIRMED"))
            for o in spec["outcomes"]
        ]
        gt = GroundTruth(
            true_root_causes=spec["true_roots"],
            acceptable_outcomes=acceptable_objs,
            max_level_by_root=spec.get("max_level_by_root", {}),
            identifiability_note=spec["rationale"]
        )
        case = EvaluationCase(
            case_id=spec["id"],
            family_id="independent_eval_v1",
            generator_version="independent_handcrafted_v1.0",
            seed=1000 + idx,
            topology=spec["topo"],
            events=final_events,
            source_health=spec.get("source_health", default_sources()),
            ground_truth=gt,
            perturbations=[spec["category"]],
            tags=["independent", spec["category"], f"{len(spec['topo'].nodes)}node"],
            identifiability_note=spec["rationale"],
            acceptable_outcomes=acceptable_objs,
            max_level_by_root=spec.get("max_level_by_root", {})
        )

        out_path = indep_dir / f"{spec['id']}.json"
        out_dict = case.model_dump()
        # Strictly ensure legacy fields are NOT serialized anywhere in the JSON
        out_dict["ground_truth"].pop("expected_verdict", None)
        out_dict["ground_truth"].pop("acceptable_verdict_level", None)
        out_dict.pop("expected_verdict", None)
        out_dict.pop("acceptable_verdict_level", None)

        out_path.write_text(json.dumps(out_dict, indent=2), encoding="utf-8")
        written_count += 1

    print(f"Successfully generated {written_count} independent evaluation cases into {indep_dir}")

    # Compute and print timing spread statistics per category
    print("\n" + "=" * 90)
    print("TIMING OFFSETS SPREAD PER CATEGORY (Seeded Wide-Range Randomization)")
    print("=" * 90)
    category_stats = defaultdict(lambda: {"root_onsets": [], "decoy_onsets": [], "symptom_delays": []})
    for spec in case_specs:
        cat = spec["category"]
        evs = spec["events"]
        roots = spec["true_roots"]
        anoms = [e for e in evs if str(e.severity).upper() in ("CRITICAL", "HIGH") and str(e.metric).lower() != "heartbeat"]
        t0 = t_base

        root_evs = [e for e in anoms if e.service in roots]
        if root_evs:
            t_root = min(datetime.fromisoformat(e.event_time) for e in root_evs)
            delta_root = (t_root - t0).total_seconds()
            category_stats[cat]["root_onsets"].append(delta_root)

            # Symptom delay = delay from root onset to last anomalous symptom
            non_root_anoms = [e for e in anoms if e.service not in roots]
            if non_root_anoms:
                t_last_sym = max(datetime.fromisoformat(e.event_time) for e in non_root_anoms)
                symptom_delay = (t_last_sym - t_root).total_seconds()
                category_stats[cat]["symptom_delays"].append(symptom_delay)

        decoy_evs = [e for e in anoms if e.service not in roots]
        if decoy_evs and cat in ("root_not_earliest", "misleading_blame"):
            t_decoy = min(datetime.fromisoformat(e.event_time) for e in decoy_evs)
            delta_decoy = (t_decoy - t0).total_seconds()
            category_stats[cat]["decoy_onsets"].append(delta_decoy)

    for cat, stats in sorted(category_stats.items()):
        print(f"Category: {cat:<24}")
        if stats["root_onsets"]:
            ro = stats["root_onsets"]
            std_ro = math.sqrt(sum((x - sum(ro)/len(ro))**2 for x in ro)/len(ro)) if len(ro) > 1 else 0.0
            print(f"  • Root Onset:     min={min(ro):5.1f}s, max={max(ro):5.1f}s, mean={sum(ro)/len(ro):5.1f}s, std={std_ro:4.1f}s")
        if stats["decoy_onsets"]:
            do = stats["decoy_onsets"]
            std_do = math.sqrt(sum((x - sum(do)/len(do))**2 for x in do)/len(do)) if len(do) > 1 else 0.0
            print(f"  • Decoy Onset:    min={min(do):5.1f}s, max={max(do):5.1f}s, mean={sum(do)/len(do):5.1f}s, std={std_do:4.1f}s")
        if stats["symptom_delays"]:
            sd = stats["symptom_delays"]
            std_sd = math.sqrt(sum((x - sum(sd)/len(sd))**2 for x in sd)/len(sd)) if len(sd) > 1 else 0.0
            print(f"  • Symptom Delay:  min={min(sd):5.1f}s, max={max(sd):5.1f}s, mean={sum(sd)/len(sd):5.1f}s, std={std_sd:4.1f}s")
    print("=" * 90)

    if all_heartbeat_offsets:
        hb_min = min(all_heartbeat_offsets)
        hb_max = max(all_heartbeat_offsets)
        hb_mean = sum(all_heartbeat_offsets) / len(all_heartbeat_offsets)
        hb_std = math.sqrt(sum((x - hb_mean)**2 for x in all_heartbeat_offsets) / len(all_heartbeat_offsets))
        print("RANDOMIZED HEARTBEAT TIMINGS SPREAD (Independent RNG):")
        print(f"  Count: {len(all_heartbeat_offsets)}, min={hb_min:5.1f}s, max={hb_max:5.1f}s, mean={hb_mean:5.1f}s, std={hb_std:4.1f}s")
        print("=" * 90 + "\n")


if __name__ == "__main__":
    build_independent_cases()
