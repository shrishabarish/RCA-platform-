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

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def build_independent_cases():
    indep_dir = PROJECT_ROOT / "evaluation" / "cases" / "independent"
    indep_dir.mkdir(parents=True, exist_ok=True)

    # 1. Real 6-node topology
    topo_6_nodes = [
        TopologyNode(id="EC2", type="compute", label="EC2 Compute Cluster", criticality=0.9),
        TopologyNode(id="DynamoDB", type="database", label="DynamoDB Primary Table", criticality=0.95),
        TopologyNode(id="Auth", type="auth", label="Auth Service", criticality=0.9),
        TopologyNode(id="Worker", type="worker", label="Worker Fleet", criticality=0.8),
        TopologyNode(id="API", type="service", label="Core API Gateway", criticality=0.85),
        TopologyNode(id="Application", type="service", label="Web/Mobile Client", criticality=0.6)
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
        TopologyNode(id="APIGateway", type="api_gateway", label="Edge Ingress", criticality=0.95),
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

    # Health records helper
    def default_sources():
        return [
            SourceHealthRecord(source=s, timestamp=t_base.isoformat(), status="HEALTHY")
            for s in ["cloudwatch", "cloudtrail", "otel", "syslog", "application"]
        ]

    case_specs = []

    # -------------------------------------------------------------------------
    # 1. Root not earliest (slow burn, early decoy) x6 (3 on 6-node, 3 on 12-node)
    # -------------------------------------------------------------------------
    for i in range(1, 4):
        case_specs.append({
            "id": f"indep_00{i}_slow_burn_decoy_6node",
            "topo": topo_6,
            "rationale": f"Decoy transient spike at t=10s on caller, slow-burn memory leak root at t=40s on EC2 (6-node #{i})",
            "roots": ["EC2"],
            "verdict": "DIAGNOSE",
            "tags": ["root_not_earliest", "slow_burn", "decoy"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="API", metric="Latency", value=220.0, severity="HIGH"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="EC2", metric="MemoryUtilization", value=98.5, severity="CRITICAL"),
                EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=50)).isoformat(), observed_time=(t_base + timedelta(seconds=51)).isoformat(), source="cloudwatch", service="API", metric="5xxErrorRate", value=15.0, severity="CRITICAL")
            ]
        })
    for i in range(4, 7):
        case_specs.append({
            "id": f"indep_00{i}_slow_burn_decoy_12node",
            "topo": topo_12,
            "rationale": f"Decoy alert on APIGateway at t=5s, true slow-burn disk saturation on PostgresPrimary at t=35s (12-node #{i-3})",
            "roots": ["PostgresPrimary"],
            "verdict": "DIAGNOSE",
            "tags": ["root_not_earliest", "slow_burn", "decoy"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=5)).isoformat(), observed_time=(t_base + timedelta(seconds=6)).isoformat(), source="otel", service="APIGateway", metric="Latency", value=310.0, severity="HIGH"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="WriteThrottleEvents", value=450.0, severity="CRITICAL"),
                EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="OrderService", metric="5xxErrorRate", value=42.0, severity="CRITICAL")
            ]
        })

    # -------------------------------------------------------------------------
    # 2. Silent root with several dependents x4 (2 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    for i in range(7, 9):
        case_specs.append({
            "id": f"indep_00{i}_silent_root_6node",
            "topo": topo_6,
            "rationale": f"DynamoDB crashed silently emitting zero events; API and Worker experience cascading connection errors (6-node #{i-6})",
            "roots": ["DynamoDB"],
            "verdict": "DIAGNOSE",
            "tags": ["silent_root", "unobserved_root"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="ErrorRate", value=35.0, severity="HIGH"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=35)).isoformat(), observed_time=(t_base + timedelta(seconds=36)).isoformat(), source="cloudwatch", service="Worker", metric="ErrorRate", value=50.0, severity="HIGH")
            ]
        })
    for i in range(9, 11):
        case_specs.append({
            "id": f"indep_{i:03d}_silent_root_12node",
            "topo": topo_12,
            "rationale": f"KafkaCluster dropped silently; NotificationService and OrderService queue lags surge (12-node #{i-8})",
            "roots": ["KafkaCluster"],
            "verdict": "DIAGNOSE",
            "tags": ["silent_root", "unobserved_root"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=40)).isoformat(), observed_time=(t_base + timedelta(seconds=41)).isoformat(), source="cloudwatch", service="NotificationService", metric="QueueDepth", value=15000.0, severity="CRITICAL"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=45)).isoformat(), observed_time=(t_base + timedelta(seconds=46)).isoformat(), source="cloudwatch", service="OrderService", metric="ErrorRate", value=12.0, severity="HIGH")
            ]
        })

    # -------------------------------------------------------------------------
    # 3. Idle-but-healthy non-root x4 (2 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    for i in range(11, 13):
        case_specs.append({
            "id": f"indep_{i:03d}_idle_healthy_control_6node",
            "topo": topo_6,
            "rationale": f"EC2 fails while Worker and DynamoDB are idle and healthy; verify idle services are not falsely blamed (6-node #{i-10})",
            "roots": ["EC2"],
            "verdict": "DIAGNOSE",
            "tags": ["idle_control"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=99.0, severity="CRITICAL"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="Latency", value=450.0, severity="HIGH")
            ]
        })
    for i in range(13, 15):
        case_specs.append({
            "id": f"indep_{i:03d}_idle_healthy_control_12node",
            "topo": topo_12,
            "rationale": f"RedisCache failure; InventoryService and KafkaCluster idle healthy (12-node #{i-12})",
            "roots": ["RedisCache"],
            "verdict": "DIAGNOSE",
            "tags": ["idle_control"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="RedisCache", metric="CacheHitRatio", value=2.0, severity="CRITICAL"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="CatalogService", metric="Latency", value=380.0, severity="HIGH")
            ]
        })

    # -------------------------------------------------------------------------
    # 4. Two independent roots x4 (2 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    case_specs.append({
        "id": "indep_015_two_independent_roots_6node",
        "topo": topo_6,
        "rationale": "Simultaneous independent failures on Auth and EC2 (6-node #1)",
        "roots": ["Auth", "EC2"],
        "verdict": "DIAGNOSE",
        "tags": ["multi_root", "independent_roots"],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudtrail", service="Auth", metric="AuthFailureRate", value=85.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="API", metric="ErrorRate", value=45.0, severity="HIGH")
        ]
    })
    case_specs.append({
        "id": "indep_016_two_independent_roots_6node",
        "topo": topo_6,
        "rationale": "Simultaneous independent failures on DynamoDB and EC2 (6-node #2)",
        "roots": ["DynamoDB", "EC2"],
        "verdict": "DIAGNOSE",
        "tags": ["multi_root", "independent_roots"],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=310.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="EC2", metric="MemoryUtilization", value=96.0, severity="CRITICAL")
        ]
    })
    for i in range(17, 19):
        case_specs.append({
            "id": f"indep_{i:03d}_two_independent_roots_12node",
            "topo": topo_12,
            "rationale": f"Simultaneous failures on RedisCache and DynamoTable (12-node #{i-16})",
            "roots": ["DynamoTable", "RedisCache"],
            "verdict": "DIAGNOSE",
            "tags": ["multi_root", "independent_roots"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="RedisCache", metric="CacheHitRatio", value=1.5, severity="CRITICAL"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=22)).isoformat(), observed_time=(t_base + timedelta(seconds=23)).isoformat(), source="cloudwatch", service="DynamoTable", metric="WriteThrottleEvents", value=600.0, severity="CRITICAL"),
                EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="OrderService", metric="5xxErrorRate", value=25.0, severity="HIGH")
            ]
        })

    # -------------------------------------------------------------------------
    # 5. Edge-root x3 (1 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    case_specs.append({
        "id": "indep_019_edge_root_leaf_6node",
        "topo": topo_6,
        "rationale": "Failure located at edge leaf dependency DynamoDB (6-node)",
        "roots": ["DynamoDB"],
        "verdict": "DIAGNOSE",
        "tags": ["edge_root"],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="DynamoDB", metric="WriteThrottleEvents", value=500.0, severity="CRITICAL"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="cloudwatch", service="Worker", metric="ErrorRate", value=40.0, severity="HIGH")
        ]
    })
    for i in range(20, 22):
        case_specs.append({
            "id": f"indep_{i:03d}_edge_root_leaf_12node",
            "topo": topo_12,
            "rationale": f"Failure located at furthest edge leaf KafkaCluster (12-node #{i-19})",
            "roots": ["KafkaCluster"],
            "verdict": "DIAGNOSE",
            "tags": ["edge_root"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="DeadLetterQueueCount", value=800.0, severity="CRITICAL"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=28)).isoformat(), observed_time=(t_base + timedelta(seconds=29)).isoformat(), source="cloudwatch", service="NotificationService", metric="ErrorRate", value=30.0, severity="HIGH")
            ]
        })

    # -------------------------------------------------------------------------
    # 6. Source DOWN covering candidate x3 (1 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    case_specs.append({
        "id": "indep_022_source_down_abstain_6node",
        "topo": topo_6,
        "rationale": "CloudWatch telemetry source DOWN covering EC2 and DynamoDB; must ABSTAIN (6-node)",
        "roots": ["EC2"],
        "verdict": "ABSTAIN",
        "tags": ["source_down", "abstain_required"],
        "sources": [
            SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="DOWN"),
            SourceHealthRecord(source="cloudtrail", timestamp=t_base.isoformat(), status="HEALTHY"),
            SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="HEALTHY")
        ],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="otel", service="API", metric="Latency", value=400.0, severity="HIGH")
        ]
    })
    for i in range(23, 25):
        case_specs.append({
            "id": f"indep_{i:03d}_source_down_abstain_12node",
            "topo": topo_12,
            "rationale": f"CloudWatch source DOWN covering PostgresPrimary; must ABSTAIN (12-node #{i-22})",
            "roots": ["PostgresPrimary"],
            "verdict": "ABSTAIN",
            "tags": ["source_down", "abstain_required"],
            "sources": [
                SourceHealthRecord(source="cloudwatch", timestamp=t_base.isoformat(), status="DOWN"),
                SourceHealthRecord(source="otel", timestamp=t_base.isoformat(), status="HEALTHY")
            ],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="otel", service="OrderService", metric="Latency", value=350.0, severity="HIGH")
            ]
        })

    # -------------------------------------------------------------------------
    # 7. Contradictory (root healthy while dependents blame it) x3 (1 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    case_specs.append({
        "id": "indep_025_contradiction_blamed_healthy_6node",
        "topo": topo_6,
        "rationale": "API reports timeout blaming DynamoDB, but DynamoDB emits healthy normal baseline metrics; must ABSTAIN (6-node)",
        "roots": ["DynamoDB"],
        "verdict": "ABSTAIN",
        "tags": ["contradiction", "abstain_required"],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="DynamoDB", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="ErrorRate", value=60.0, severity="CRITICAL")
        ]
    })
    for i in range(26, 28):
        case_specs.append({
            "id": f"indep_{i:03d}_contradiction_blamed_healthy_12node",
            "topo": topo_12,
            "rationale": f"OrderService blames PostgresPrimary, but PostgresPrimary shows zero latency and healthy baseline; must ABSTAIN (12-node #{i-25})",
            "roots": ["PostgresPrimary"],
            "verdict": "ABSTAIN",
            "tags": ["contradiction", "abstain_required"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="PostgresPrimary", metric="Heartbeat", value=1.0, severity="LOW"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=30)).isoformat(), observed_time=(t_base + timedelta(seconds=31)).isoformat(), source="otel", service="OrderService", metric="ErrorRate", value=50.0, severity="CRITICAL")
            ]
        })

    # -------------------------------------------------------------------------
    # 8. Benign heartbeats added to a clear case x3 (1 on 6-node, 2 on 12-node)
    # -------------------------------------------------------------------------
    case_specs.append({
        "id": "indep_028_clear_with_heartbeats_6node",
        "topo": topo_6,
        "rationale": "Clear EC2 failure with benign periodic heartbeats added across non-root services; ranking must remain invariant (6-node)",
        "roots": ["EC2"],
        "verdict": "DIAGNOSE",
        "tags": ["heartbeat_invariance"],
        "events": [
            EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Auth", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=15)).isoformat(), observed_time=(t_base + timedelta(seconds=16)).isoformat(), source="cloudwatch", service="Worker", metric="Heartbeat", value=1.0, severity="LOW"),
            EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="EC2", metric="CPUUtilization", value=99.0, severity="CRITICAL"),
            EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="API", metric="Latency", value=400.0, severity="HIGH")
        ]
    })
    for i in range(29, 31):
        case_specs.append({
            "id": f"indep_{i:03d}_clear_with_heartbeats_12node",
            "topo": topo_12,
            "rationale": f"Clear RedisCache failure with benign heartbeats on idle services (12-node #{i-28})",
            "roots": ["RedisCache"],
            "verdict": "DIAGNOSE",
            "tags": ["heartbeat_invariance"],
            "events": [
                EventData(event_id="evt_01", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="KafkaCluster", metric="Heartbeat", value=1.0, severity="LOW"),
                EventData(event_id="evt_02", event_time=(t_base + timedelta(seconds=10)).isoformat(), observed_time=(t_base + timedelta(seconds=11)).isoformat(), source="cloudwatch", service="NotificationService", metric="Heartbeat", value=1.0, severity="LOW"),
                EventData(event_id="evt_03", event_time=(t_base + timedelta(seconds=20)).isoformat(), observed_time=(t_base + timedelta(seconds=21)).isoformat(), source="cloudwatch", service="RedisCache", metric="CacheHitRatio", value=1.0, severity="CRITICAL"),
                EventData(event_id="evt_04", event_time=(t_base + timedelta(seconds=25)).isoformat(), observed_time=(t_base + timedelta(seconds=26)).isoformat(), source="cloudwatch", service="CatalogService", metric="Latency", value=350.0, severity="HIGH")
            ]
        })

    # Write each case file
    manifest = []
    for spec in case_specs:
        cid = spec["id"]
        gt = GroundTruth(
            true_root_causes=spec["roots"],
            expected_verdict=spec["verdict"],
            onset_times={r: t_base.isoformat() for r in spec["roots"]}
        )
        case = EvaluationCase(
            case_id=cid,
            family_id="fam_independent",
            generator_version="independent_handwritten_1.0",
            seed=42,
            topology=spec["topo"],
            events=spec["events"],
            source_health=spec.get("sources", default_sources()),
            ground_truth=gt,
            tags=spec["tags"]
        )
        out_file = indep_dir / f"{cid}.json"
        out_file.write_text(json.dumps(case.model_dump(), indent=2), encoding="utf-8")
        manifest.append({
            "case_id": cid,
            "topology": "6-node" if len(spec["topo"].nodes) == 6 else "12-node",
            "expected_verdict": spec["verdict"],
            "true_roots": spec["roots"],
            "rationale": spec["rationale"]
        })

    print(f"Successfully created {len(manifest)} hand-written independent cases in {indep_dir}")
    return manifest


if __name__ == "__main__":
    build_independent_cases()
