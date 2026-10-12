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
    GroundTruth
)


def build_independent_cases():
    indep_dir = PROJECT_ROOT / "evaluation" / "cases" / "independent"
    indep_dir.mkdir(parents=True, exist_ok=True)

    # 1. Real 6-node topology (Family 001)
    topo_6_nodes = [
        TopologyNode(id="EC2", type="compute", label="EC2 Compute Fleet", criticality=0.9),
        TopologyNode(id="DynamoDB", type="database", label="DynamoDB Primary Table", criticality=0.95),
        TopologyNode(id="Auth", type="auth", label="Auth Microservice", criticality=0.9),
        TopologyNode(id="Worker", type="worker", label="Worker Fleet", criticality=0.8),
        TopologyNode(id="API", type="service", label="Core API Gateway", criticality=0.85),
        TopologyNode(id="Application", type="service", label="Web/Client Ingress", criticality=0.7)
    ]
    # Service call edges: [caller, callee]
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
        TopologyNode(id="APIGateway", type="api_gateway", label="Edge Ingress Gateway", criticality=0.95),
        TopologyNode(id="OrderService", type="service", label="Order API", criticality=0.9),
        TopologyNode(id="CatalogService", type="service", label="Catalog API", criticality=0.8),
        TopologyNode(id="AuthService", type="auth", label="Auth Service", criticality=0.9),
        TopologyNode(id="PaymentService", type="service", label="Payment Gateway", criticality=0.95),
        TopologyNode(id="InventoryService", type="service", label="Inventory Management", criticality=0.85),
        TopologyNode(id="NotificationService", type="service", label="Notification Worker", criticality=0.7),
        TopologyNode(id="PostgresPrimary", type="database", label="Postgres DB", criticality=0.95),
        TopologyNode(id="DynamoTable", type="database", label="Payment Ledger", criticality=0.95),
        TopologyNode(id="RedisCache", type="cache", label="Product Cache", criticality=0.8),
        TopologyNode(id="K8sCluster", type="compute", label="App Node Pool", criticality=0.85),
        TopologyNode(id="KafkaCluster", type="queue", label="Event Streaming Bus", criticality=0.9)
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

    # Clean old JSON files in the independent directory first
    for f in indep_dir.glob("*.json"):
        f.unlink()

    case_specs = []

    # =========================================================================
    # 1. ROOT NOT EARLIEST (slow burn, early decoy) x6 (3 on 6-node, 3 on 12-node)
    # =========================================================================
    # indep_001 (6-node)
    case_specs.append({
        "id": "indep_001_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "rationale": "Decoy transient latency surge on API at t=10s, true root DynamoDB builds latent lock contention starting at t=0s and breaching threshold at t=40s",
        "identifiability_note": "DynamoDB emits early weak WriteThrottle telemetry at t=5s (value 12.0) that precedes the API decoy burst, while API's latency spike at t=10s returns to normal before DynamoDB suffers hard saturation at t=40s.",
        "roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=12.0, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="API", metric="Latency", value=220.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=450.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=22.0, severity="CRITICAL")
        ]
    })
    # indep_002 (6-node)
    case_specs.append({
        "id": "indep_002_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "rationale": "Caller Worker emits transient retry alert at t=15s, slow-burn memory leak root EC2 reaches exhaustion at t=45s",
        "identifiability_note": "EC2 exhibits progressive thread allocation warning at t=8s; Worker errors specifically report EC2 compute node unreachable once EC2 memory breaches 99% at t=45s, while DynamoDB and Auth emit healthy heartbeats.",
        "roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="cloudwatch", service="EC2", metric="MemoryUtilization", value=82.0, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Worker", metric="JobRetrySpike", value=18.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="EC2", metric="MemoryUtilization", value=99.2, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="Worker", metric="ErrorRate", value=48.0, severity="CRITICAL")
        ]
    })
    # indep_003 (6-node)
    case_specs.append({
        "id": "indep_003_slow_burn_decoy_6node",
        "topo": topo_6,
        "category": "root_not_earliest",
        "rationale": "Application reports 504 gateway timeout at t=12s as an early client-side decoy before backend EC2 resource leak causes hard crash at t=50s",
        "identifiability_note": "EC2 logs CPU load average creep starting at t=6s (load=7.8); Auth and DynamoDB emit verified healthy heartbeats confirming that EC2 is the singular failing backend explaining Application timeouts.",
        "roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=6)).isoformat(), observed_time=(t_base + timedelta(seconds=7)).isoformat(), source="cloudwatch", service="EC2", metric="CPULoad", value=7.8, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="application", service="Application", metric="GatewayTimeout", value=14.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="EC2", metric="CPULoad", value=28.5, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=30.0, severity="CRITICAL"),
            EventData(event_id="hb_dynamo", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_004 (12-node)
    case_specs.append({
        "id": "indep_004_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "rationale": "OrderService emits transient timeout at t=10s, PostgresPrimary connection leak culminates in total deadlock at t=45s",
        "identifiability_note": "PostgresPrimary connection pool usage telemetry climbs steadily starting at t=5s (85% pool used); OrderService errors cite SQL connection acquisition timeout, while sibling cache RedisCache emits healthy heartbeats.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="ConnectionPoolUtilization", value=85.0, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="otel", service="OrderService", metric="ResponseTime", value=450.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="DeadlockDetected", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="OrderService", metric="5xxErrorRate", value=42.0, severity="CRITICAL")
        ]
    })
    # indep_005 (12-node)
    case_specs.append({
        "id": "indep_005_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "rationale": "CatalogService reports cache-miss spike at t=12s as early decoy, slow-burn memory fragmentation in RedisCache triggers crash at t=48s",
        "identifiability_note": "RedisCache records early memory fragmentation index warning at t=7s (ratio=1.8); downstream CatalogService latency degrades only when RedisCache eviction halts at t=48s.",
        "roots": ["RedisCache"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=7)).isoformat(), observed_time=(t_base + timedelta(seconds=8)).isoformat(), source="otel", service="RedisCache", metric="FragmentationRatio", value=1.85, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="application", service="CatalogService", metric="CacheMissRate", value=65.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=48)).isoformat(), observed_time=(t_base + timedelta(seconds=49)).isoformat(), source="cloudwatch", service="RedisCache", metric="OOMKillCount", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=52)).isoformat(), observed_time=(t_base + timedelta(seconds=53)).isoformat(), source="otel", service="CatalogService", metric="5xxErrorRate", value=28.0, severity="CRITICAL")
        ]
    })
    # indep_006 (12-node)
    case_specs.append({
        "id": "indep_006_slow_burn_decoy_12node",
        "topo": topo_12,
        "category": "root_not_earliest",
        "rationale": "NotificationService emits transient consumer lag warning at t=15s, KafkaCluster broker storage exhaustion surfaces at t=50s",
        "identifiability_note": "KafkaCluster emits disk space threshold warning at t=8s (disk=88%); consumer queue lag at NotificationService is a direct consequence of broker throttling at t=50s.",
        "roots": ["KafkaCluster"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="DiskUtilization", value=88.0, severity="MEDIUM"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="application", service="NotificationService", metric="ConsumerLag", value=1200.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="BrokerUnderReplicatedPartitions", value=14.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=55)).isoformat(), observed_time=(t_base + timedelta(seconds=56)).isoformat(), source="application", service="NotificationService", metric="MessageLossCount", value=35.0, severity="CRITICAL")
        ]
    })

    # =========================================================================
    # 2. SILENT ROOT WITH SEVERAL DEPENDENTS x4 (2 on 6-node, 2 on 12-node)
    # =========================================================================
    # indep_007 (6-node)
    case_specs.append({
        "id": "indep_007_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "rationale": "EC2 crashed completely emitting zero telemetry; API and Worker experience cascading connection refused errors",
        "identifiability_note": "API and Worker report connection refused specifically pointing to EC2 socket. Alternative dependencies DynamoDB and Auth emit continuous healthy heartbeats, isolating EC2 as the only possible cause despite its complete silence.",
        "roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "PROBABLE only",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="ConnectionRefused_EC2", value=45.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="Worker", metric="ComputeNodeUnreachable", value=30.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="application", service="Application", metric="502BadGateway", value=25.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_008 (6-node)
    case_specs.append({
        "id": "indep_008_silent_root_6node",
        "topo": topo_6,
        "category": "silent_root",
        "rationale": "DynamoDB network partition halts telemetry (0 events); API and Worker experience severe database timeout cascades",
        "identifiability_note": "Both API and Worker record critical timeouts addressed to DynamoDB endpoints. Competing dependencies EC2 and Auth emit verified healthy heartbeats, uniquely singling out DynamoDB.",
        "roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "PROBABLE only",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="DatabaseTimeout_Dynamo", value=60.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="Worker", metric="DatabaseWriteTimeout", value=40.0, severity="CRITICAL"),
            EventData(event_id="hb_ec2", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_009 (12-node)
    case_specs.append({
        "id": "indep_009_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "rationale": "PostgresPrimary kernel panic leaves 0 events; OrderService and InventoryService report broken DB connections",
        "identifiability_note": "OrderService and InventoryService both report SQL driver connection drop. RedisCache, PaymentService, and DynamoTable emit healthy baseline heartbeats, proving PostgresPrimary is the silent cause.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "PROBABLE only",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="DBConnectionFailed", value=80.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="InventoryService", metric="DBQueryTimeout", value=50.0, severity="CRITICAL"),
            EventData(event_id="hb_redis", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="otel", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_dynamo", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoTable", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_010 (12-node)
    case_specs.append({
        "id": "indep_010_silent_root_12node",
        "topo": topo_12,
        "category": "silent_root",
        "rationale": "AuthService power failure halts all metrics; APIGateway and OrderService fail client authentication",
        "identifiability_note": "APIGateway and OrderService explicitly fail with token validation timeout addressed to AuthService. Backend database nodes PostgresPrimary and DynamoTable emit normal healthy telemetry.",
        "roots": ["AuthService"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "PROBABLE only",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="otel", service="APIGateway", metric="AuthServiceTimeout", value=75.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="OrderService", metric="AuthTokenVerificationError", value=60.0, severity="CRITICAL"),
            EventData(event_id="hb_db", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 3. IDLE-HEALTHY NON-ROOTS IN DIAGNOSABLE FAILURE x4 (2 on 6-node, 2 on 12-node)
    # =========================================================================
    # indep_011 (6-node)
    case_specs.append({
        "id": "indep_011_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "rationale": "DynamoDB suffers critical write throttle failure; API cascades while non-roots EC2, Auth, and Worker remain idle and healthy",
        "identifiability_note": "Direct critical throttle events on DynamoDB at t=10s explain API errors; EC2, Auth, and Worker emit normal healthy heartbeats with zero errors.",
        "roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=320.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=25.0, severity="HIGH"),
            EventData(event_id="hb_ec2", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_worker", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Worker", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_012 (6-node)
    case_specs.append({
        "id": "indep_012_idle_healthy_6node",
        "topo": topo_6,
        "category": "idle_healthy",
        "rationale": "EC2 compute failure impacts Worker and Application; non-roots DynamoDB and Auth remain completely idle and healthy",
        "identifiability_note": "EC2 reports 99% CPU utilization causing Worker job queue stall; DynamoDB and Auth emit verified healthy telemetry.",
        "roots": ["EC2"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="Worker", metric="QueueBacklog", value=450.0, severity="HIGH"),
            EventData(event_id="hb_dynamo", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_auth", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_013 (12-node)
    case_specs.append({
        "id": "indep_013_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "rationale": "PostgresPrimary connection pool exhaustion breaks OrderService; 8 peripheral services remain idle and healthy",
        "identifiability_note": "PostgresPrimary emits critical connection deadlock; CatalogService, RedisCache, KafkaCluster, and NotificationService are unaffected and emit routine heartbeats.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="ConnectionPoolExhausted", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="OrderService", metric="5xxErrorRate", value=38.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="otel", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="application", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_014 (12-node)
    case_specs.append({
        "id": "indep_014_idle_healthy_12node",
        "topo": topo_12,
        "category": "idle_healthy",
        "rationale": "PaymentService outage triggers APIGateway alerts; Inventory, Catalog, and Kafka services remain completely idle and healthy",
        "identifiability_note": "PaymentService fails third-party webhook verification; unassociated microservices emit continuous healthy telemetry.",
        "roots": ["PaymentService"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="application", service="PaymentService", metric="GatewayExceptionRate", value=65.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=14)).isoformat(), observed_time=(t_base + timedelta(seconds=15)).isoformat(), source="otel", service="APIGateway", metric="5xxErrorRate", value=20.0, severity="HIGH"),
            EventData(event_id="hb_inv", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="InventoryService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_cat", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="otel", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 4. TWO INDEPENDENT ROOTS x4 (2 on 6-node, 2 on 12-node)
    # =========================================================================
    # indep_015 (6-node)
    case_specs.append({
        "id": "indep_015_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "rationale": "Concurrent independent hardware failure on EC2 and storage throttling on DynamoDB",
        "identifiability_note": "EC2 CPU hardware faults and DynamoDB write throttles occur simultaneously on disjoint services; engine must return both in root set.",
        "roots": ["EC2", "DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="EC2", metric="HardwareFault", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=250.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=45.0, severity="CRITICAL")
        ]
    })
    # indep_016 (6-node)
    case_specs.append({
        "id": "indep_016_two_independent_roots_6node",
        "topo": topo_6,
        "category": "two_independent_roots",
        "rationale": "Auth token expiration bug occurs simultaneously with independent DynamoDB storage partition",
        "identifiability_note": "Auth reports certificate signing error while DynamoDB reports network drop; both are independent causal origins.",
        "roots": ["Auth", "DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="application", service="Auth", metric="TokenVerificationFailure", value=80.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=16)).isoformat(), observed_time=(t_base + timedelta(seconds=17)).isoformat(), source="cloudwatch", service="DynamoDB", metric="StoragePartitionUnreachable", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=50.0, severity="CRITICAL")
        ]
    })
    # indep_017 (12-node)
    case_specs.append({
        "id": "indep_017_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "rationale": "Disjoint concurrent failures on PostgresPrimary (deadlock) and RedisCache (OOM eviction failure)",
        "identifiability_note": "PostgresPrimary deadlocks order checkout while RedisCache OOM crashes product browsing; disjoint root causes.",
        "roots": ["PostgresPrimary", "RedisCache"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="DeadlockCount", value=5.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=12)).isoformat(), observed_time=(t_base + timedelta(seconds=13)).isoformat(), source="cloudwatch", service="RedisCache", metric="OOMKillCount", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="otel", service="OrderService", metric="5xxErrorRate", value=30.0, severity="HIGH"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="otel", service="CatalogService", metric="5xxErrorRate", value=28.0, severity="HIGH")
        ]
    })
    # indep_018 (12-node)
    case_specs.append({
        "id": "indep_018_two_independent_roots_12node",
        "topo": topo_12,
        "category": "two_independent_roots",
        "rationale": "PaymentService third-party gateway drop coincides with independent KafkaCluster disk full crash",
        "identifiability_note": "PaymentService and KafkaCluster fail on independent subgraphs; engine must extract both as the minimal root set.",
        "roots": ["PaymentService", "KafkaCluster"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="application", service="PaymentService", metric="GatewayExceptionRate", value=75.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=14)).isoformat(), observed_time=(t_base + timedelta(seconds=15)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="DiskFullError", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="application", service="NotificationService", metric="QueueStall", value=100.0, severity="HIGH")
        ]
    })

    # =========================================================================
    # 5. EDGE-ROOT (Root is ENTRY-POINT service whose callees are noisy) x3 (1 on 6-node, 2 on 12-node)
    # =========================================================================
    # indep_019 (6-node)
    case_specs.append({
        "id": "indep_019_edge_root_entrypoint_6node",
        "topo": topo_6,
        "category": "edge_root",
        "rationale": "Top-level ingress Application misconfigures retry flood, causing downstream API and Worker to report high error rates",
        "identifiability_note": "Application ingress client bug generates excessive flood traffic (10,000 req/sec) causing downstream API rate-limit errors and Worker saturation. The root is the entrypoint itself, not the downstream callees.",
        "roots": ["Application"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="application", service="Application", metric="RequestFloodVolume", value=10000.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="API", metric="RateLimitThrottling", value=250.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="Worker", metric="QueueSaturated", value=95.0, severity="HIGH")
        ]
    })
    # indep_020 (12-node)
    case_specs.append({
        "id": "indep_020_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "rationale": "Edge ingress APIGateway deploys faulty routing table, flooding OrderService and CatalogService with invalid requests",
        "identifiability_note": "APIGateway logs routing configuration corruption at t=10s; downstream OrderService and CatalogService report 400 Bad Request surges due to gateway payload mangling.",
        "roots": ["APIGateway"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="otel", service="APIGateway", metric="RoutingTableCorruption", value=1.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="otel", service="OrderService", metric="InvalidPayloadRate", value=85.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="otel", service="CatalogService", metric="InvalidPayloadRate", value=60.0, severity="HIGH")
        ]
    })
    # indep_021 (12-node)
    case_specs.append({
        "id": "indep_021_edge_root_entrypoint_12node",
        "topo": topo_12,
        "category": "edge_root",
        "rationale": "Edge ingress APIGateway TLS termination failure drops client connections, causing downstream services to report connection resets",
        "identifiability_note": "APIGateway TLS termination certificate failure initiates all downstream communication drops.",
        "roots": ["APIGateway"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="otel", service="APIGateway", metric="TLSTerminationFailure", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="OrderService", metric="ClientConnectionReset", value=70.0, severity="HIGH"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="AuthService", metric="ClientConnectionReset", value=50.0, severity="HIGH")
        ]
    })

    # =========================================================================
    # 6. SOURCE DOWN COVERING CANDIDATE x3 (2 on 6-node, 1 on 12-node)
    # =========================================================================
    # indep_022 (6-node)
    case_specs.append({
        "id": "indep_022_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "rationale": "CloudWatch telemetry source is DOWN covering DynamoDB; engine must abstain with INSUFFICIENT_EVIDENCE",
        "identifiability_note": "Source health record explicitly marks cloudwatch DOWN; telemetry covering DynamoDB is unobservable.",
        "roots": ["DynamoDB"],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "source_health": [
            SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="DOWN"),
            SourceHealthRecord(source="application", timestamp=t_base.isoformat(), status="HEALTHY")
        ],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="API", metric="ErrorRate", value=40.0, severity="HIGH")
        ]
    })
    # indep_023 (6-node)
    case_specs.append({
        "id": "indep_023_source_down_abstain_6node",
        "topo": topo_6,
        "category": "source_down",
        "rationale": "Metrics source otel is DOWN covering EC2 compute nodes; engine must abstain rather than hallucinate",
        "identifiability_note": "Source health record marks otel DOWN covering EC2; unobservable root telemetry forces abstention.",
        "roots": ["EC2"],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "source_health": [
            SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="DOWN"),
            SourceHealthRecord(source="application", timestamp=t_base.isoformat(), status="HEALTHY")
        ],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="Worker", metric="ErrorRate", value=45.0, severity="HIGH")
        ]
    })
    # indep_024 (12-node)
    case_specs.append({
        "id": "indep_024_source_down_abstain_12node",
        "topo": topo_12,
        "category": "source_down",
        "rationale": "Cloudwatch agent is DOWN covering PostgresPrimary database; engine must abstain with INSUFFICIENT_EVIDENCE",
        "identifiability_note": "Primary metrics collector DOWN prevents evidence confirmation.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "source_health": [
            SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="DOWN"),
            SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="HEALTHY")
        ],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="otel", service="OrderService", metric="DatabaseTimeout", value=50.0, severity="HIGH")
        ]
    })

    # =========================================================================
    # 7. CONTRADICTORY (root healthy while dependents blame it) x2 (1 on 6-node, 1 on 12-node)
    # =========================================================================
    # indep_025 (6-node)
    case_specs.append({
        "id": "indep_025_contradiction_blamed_healthy_6node",
        "topo": topo_6,
        "category": "contradictory",
        "rationale": "Downstream API blames DynamoDB for HTTP 500s, but DynamoDB verified health telemetry proves healthy latency and 0 errors",
        "identifiability_note": "Contradiction detector fires ROOT_HEALTHY_WHILE_DEPENDENTS_BLAME; engine must abstain because direct positive health signals contradict caller blame.",
        "roots": ["DynamoDB"],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="application", service="API", metric="DynamoDBFailureBlame", value=100.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_026 (12-node)
    case_specs.append({
        "id": "indep_026_contradiction_blamed_healthy_12node",
        "topo": topo_12,
        "category": "contradictory",
        "rationale": "OrderService reports critical errors claiming PostgresPrimary is down, yet database health metrics prove 100% healthy transactions",
        "identifiability_note": "Direct health verification on PostgresPrimary invalidates caller blame, triggering contradiction abstention.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="otel", service="OrderService", metric="DatabaseDeadBlame", value=100.0, severity="CRITICAL"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 8. BENIGN HEARTBEATS ADDED TO CLEAR CASE x2 (1 on 6-node, 1 on 12-node)
    # =========================================================================
    # indep_027 (6-node)
    case_specs.append({
        "id": "indep_027_benign_heartbeats_6node",
        "topo": topo_6,
        "category": "benign_heartbeats",
        "rationale": "Definite DynamoDB throttle failure with 10 high-frequency benign heartbeats interleaved on all idle services",
        "identifiability_note": "High-frequency benign heartbeats across all nodes do not alter the true root diagnosis of DynamoDB.",
        "roots": ["DynamoDB"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=300.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=22.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=2)).isoformat(), observed_time=(t_base + timedelta(seconds=3)).isoformat(), source="cloudwatch", service="EC2", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=4)).isoformat(), observed_time=(t_base + timedelta(seconds=5)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=6)).isoformat(), observed_time=(t_base + timedelta(seconds=7)).isoformat(), source="cloudwatch", service="Worker", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_04", event_time=(t_base + timedelta(seconds=8)).isoformat(), observed_time=(t_base + timedelta(seconds=9)).isoformat(), source="application", service="Application", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })
    # indep_028 (12-node)
    case_specs.append({
        "id": "indep_028_benign_heartbeats_12node",
        "topo": topo_12,
        "category": "benign_heartbeats",
        "rationale": "Clear PostgresPrimary failover with dense periodic heartbeats across all 11 unaffected microservices",
        "identifiability_note": "Dense benign heartbeats across 11 services are filtered cleanly and preserve PostgresPrimary ranking.",
        "roots": ["PostgresPrimary"],
        "expected_verdict": "DIAGNOSE",
        "acceptable_verdict_level": "CONFIRMED allowed",
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="ConnectionRefused", value=100.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=18)).isoformat(), observed_time=(t_base + timedelta(seconds=19)).isoformat(), source="cloudwatch", service="OrderService", metric="5xxErrorRate", value=40.0, severity="HIGH"),
            EventData(event_id="hb_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="otel", service="APIGateway", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_02", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="otel", service="CatalogService", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="hb_03", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="cloudwatch", service="RedisCache", metric="Heartbeat", value=1.0, severity="LOW")
        ]
    })

    # =========================================================================
    # 9. NO-ANOMALY (All-quiet control, entire system healthy) x2 (1 on 6-node, 1 on 12-node)
    # =========================================================================
    # indep_029 (6-node)
    case_specs.append({
        "id": "indep_029_no_anomaly_6node",
        "topo": topo_6,
        "category": "no_anomaly",
        "rationale": "Entire 6-node cluster operating normally with only periodic benign heartbeats; zero anomalies",
        "identifiability_note": "Quiet timeline contradiction QUIET_TIMELINE_NO_ANOMALY fires; engine must abstain from hallucinating any root cause.",
        "roots": [],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "events": [
            EventData(event_id=f"hb_6_{s}", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service=s, metric="Heartbeat", value=1.0, severity="LOW")
            for s in ["EC2", "DynamoDB", "Auth", "Worker", "API", "Application"]
        ]
    })
    # indep_030 (12-node)
    case_specs.append({
        "id": "indep_030_no_anomaly_12node",
        "topo": topo_12,
        "category": "no_anomaly",
        "rationale": "Entire 12-node enterprise microservice cluster operating normally with periodic synthetic health pings; zero anomalies",
        "identifiability_note": "Complete quiet baseline across 12 services forces abstention with zero hallucinated root causes.",
        "roots": [],
        "expected_verdict": "ABSTAIN",
        "acceptable_verdict_level": "ABSTAIN",
        "events": [
            EventData(event_id=f"hb_12_{n.id}", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service=n.id, metric="Heartbeat", value=1.0, severity="LOW")
            for n in topo_12_nodes
        ]
    })

    assert len(case_specs) == 30, f"Expected 30 case specs, got {len(case_specs)}"

    six_count = sum(1 for c in case_specs if len(c["topo"].nodes) == 6)
    twelve_count = sum(1 for c in case_specs if len(c["topo"].nodes) == 12)
    assert six_count == 15, f"Expected 15 six-node cases, got {six_count}"
    assert twelve_count == 15, f"Expected 15 twelve-node cases, got {twelve_count}"

    for spec in case_specs:
        cid = spec["id"]
        gt = GroundTruth(
            true_root_causes=spec["roots"],
            expected_verdict=spec["expected_verdict"],
            acceptable_verdict_level=spec["acceptable_verdict_level"],
            identifiability_note=spec.get("identifiability_note")
        )
        case_obj = EvaluationCase(
            case_id=cid,
            family_id="indep_eval",
            seed=20261016,
            generator_version="independent_handwritten_1.0",
            topology=spec["topo"],
            events=spec["events"],
            source_health=spec.get("source_health", default_sources()),
            ground_truth=gt,
            perturbations=[spec["category"]],
            tags=[spec["category"], "independent_test"],
            identifiability_note=spec.get("identifiability_note"),
            acceptable_verdict_level=spec["acceptable_verdict_level"]
        )
        out_path = indep_dir / f"{cid}.json"
        out_path.write_text(json.dumps(case_obj.model_dump(), indent=2), encoding="utf-8")

    print(f"Successfully generated {len(case_specs)} independent cases in {indep_dir}:")
    print(f"  6-node topology: {six_count} cases")
    print(f"  12-node topology: {twelve_count} cases")


if __name__ == "__main__":
    build_independent_cases()
