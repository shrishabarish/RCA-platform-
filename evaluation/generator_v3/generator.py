import json
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Set, Tuple

from evaluation.schema import (
    EvaluationCase,
    TopologyData,
    TopologyNode,
    EventData,
    SourceHealthRecord,
    GroundTruth
)
from rca_v2.telemetry_policy import TelemetryPolicy, ExpectedSignalPolicy


class GeneratorV3:
    """
    Generator 3.0:
    - Fixes severity leakage: root and downstream symptom events draw severity from the EXACT SAME distribution.
    - Periodic baseline telemetry with Gaussian noise at policy intervals.
    - Silent failures drop telemetry.
    - Collector outages drop telemetry across entire source.
    - Idle-silent non-root controls.
    - Independent multi-root failures with distinct true root causes.
    """

    def __init__(self, policy: Optional[TelemetryPolicy] = None, uninformative_severity: bool = True):
        self.policy = policy or TelemetryPolicy()
        self.uninformative_severity = uninformative_severity

    def generate_topologies(self) -> List[TopologyData]:
        # Topo A: 8 nodes
        topo_a_nodes = [
            TopologyNode(id="ComputeCluster", type="compute", label="K8s Compute Nodes", criticality=0.9),
            TopologyNode(id="PostgresPrimary", type="database", label="PostgreSQL Primary DB", criticality=0.95),
            TopologyNode(id="RedisCache", type="cache", label="Redis Cache", criticality=0.8),
            TopologyNode(id="AuthService", type="auth", label="Auth Service", criticality=0.9),
            TopologyNode(id="OrderService", type="service", label="Order Management Service", criticality=0.85),
            TopologyNode(id="CatalogService", type="service", label="Catalog Service", criticality=0.75),
            TopologyNode(id="APIGateway", type="api_gateway", label="API Gateway", criticality=0.9),
            TopologyNode(id="WebFrontend", type="service", label="Web Frontend", criticality=0.6)
        ]
        topo_a_edges = [
            ["APIGateway", "OrderService"],
            ["APIGateway", "CatalogService"],
            ["APIGateway", "AuthService"],
            ["OrderService", "PostgresPrimary"],
            ["OrderService", "RedisCache"],
            ["OrderService", "ComputeCluster"],
            ["CatalogService", "ComputeCluster"],
            ["WebFrontend", "APIGateway"]
        ]
        topo_a = TopologyData(nodes=topo_a_nodes, edges=topo_a_edges, topology_version="v3-topo-8node")

        # Topo B: 10 nodes
        topo_b_nodes = [
            TopologyNode(id="IngestionService", type="service", label="Data Ingestion API", criticality=0.85),
            TopologyNode(id="MessageQueue", type="queue", label="SQS Message Queue", criticality=0.9),
            TopologyNode(id="WorkerHost", type="compute", label="EC2 Worker Fleet", criticality=0.8),
            TopologyNode(id="BatchWorker", type="worker", label="Batch Processing Worker", criticality=0.85),
            TopologyNode(id="AnalyticsDB", type="database", label="Analytics DynamoDB Table", criticality=0.9),
            TopologyNode(id="ReportingService", type="service", label="Reporting Service", criticality=0.7),
            TopologyNode(id="AuthService", type="auth", label="Auth Service", criticality=0.9),
            TopologyNode(id="EdgeGateway", type="api_gateway", label="Edge Ingress Gateway", criticality=0.95),
            TopologyNode(id="CacheCluster", type="cache", label="Memcached Cluster", criticality=0.75),
            TopologyNode(id="NotificationService", type="service", label="Notification Dispatcher", criticality=0.65)
        ]
        topo_b_edges = [
            ["EdgeGateway", "IngestionService"],
            ["EdgeGateway", "ReportingService"],
            ["EdgeGateway", "AuthService"],
            ["IngestionService", "MessageQueue"],
            ["BatchWorker", "MessageQueue"],
            ["BatchWorker", "WorkerHost"],
            ["BatchWorker", "AnalyticsDB"],
            ["ReportingService", "AnalyticsDB"],
            ["ReportingService", "CacheCluster"],
            ["BatchWorker", "NotificationService"]
        ]
        topo_b = TopologyData(nodes=topo_b_nodes, edges=topo_b_edges, topology_version="v3-topo-10node")

        # Topo C: 7 nodes
        topo_c_nodes = [
            TopologyNode(id="IngressAPI", type="api_gateway", label="Ingress API", criticality=0.9),
            TopologyNode(id="ProcessorA", type="service", label="Processor A", criticality=0.8),
            TopologyNode(id="ProcessorB", type="service", label="Processor B", criticality=0.8),
            TopologyNode(id="StorageDB", type="database", label="Storage Aurora DB", criticality=0.95),
            TopologyNode(id="SharedCache", type="cache", label="Shared Redis", criticality=0.85),
            TopologyNode(id="EC2Host", type="compute", label="EC2 Compute Cluster", criticality=0.85),
            TopologyNode(id="MetricsDashboard", type="service", label="Metrics Dashboard", criticality=0.6)
        ]
        topo_c_edges = [
            ["IngressAPI", "ProcessorA"],
            ["IngressAPI", "ProcessorB"],
            ["ProcessorA", "StorageDB"],
            ["ProcessorA", "SharedCache"],
            ["ProcessorB", "StorageDB"],
            ["ProcessorB", "EC2Host"],
            ["MetricsDashboard", "IngressAPI"],
            ["MetricsDashboard", "ProcessorA"]
        ]
        topo_c = TopologyData(nodes=topo_c_nodes, edges=topo_c_edges, topology_version="v3-topo-7node")

        return [topo_a, topo_b, topo_c]

    def generate_case(
        self,
        case_id: str,
        family_id: str,
        case_type: str,
        topology: TopologyData,
        seed: int,
        t0: Optional[datetime] = None
    ) -> EvaluationCase:
        rng = random.Random(seed)
        t_base = t0 or datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
        duration_sec = 300

        node_map = {n.id: n for n in topology.nodes}
        callees: Dict[str, List[str]] = {n.id: [] for n in topology.nodes}
        callers: Dict[str, List[str]] = {n.id: [] for n in topology.nodes}
        for u, v in topology.edges:
            callees[u].append(v)
            callers[v].append(u)

        def get_signals_for(node_id: str) -> List[ExpectedSignalPolicy]:
            ntype = node_map[node_id].type
            return self.policy.get_expected_signals_for_node(node_id, ntype)

        events: List[EventData] = []
        source_health: List[SourceHealthRecord] = []
        evt_idx = 0

        all_sources = {"cloudwatch", "otel", "datadog", "syslog", "application", "cloudtrail"}
        for s in sorted(all_sources):
            source_health.append(SourceHealthRecord(
                source=s,
                timestamp=t_base.isoformat(),
                status="HEALTHY"
            ))

        leaf_dependencies = [n.id for n in topology.nodes if not callees[n.id]]
        intermediate_nodes = [n.id for n in topology.nodes if callees[n.id] and callers[n.id]]
        root_candidates = leaf_dependencies if leaf_dependencies else list(node_map.keys())

        true_roots: List[str] = []
        failed_nodes: Set[str] = set()
        silent_nodes: Set[str] = set()
        down_sources: Set[str] = set()
        healthy_blamed_roots: Set[str] = set()
        expected_verdict = "DIAGNOSE"
        tags: List[str] = ["generator_3.0"]
        perturbations: List[str] = []

        if case_type == "single_root_metric":
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            expected_verdict = "DIAGNOSE"
            perturbations.append("single_root_failure_metric_spike")

        elif case_type == "single_root_silent":
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            silent_nodes.add(root)
            expected_verdict = "DIAGNOSE"
            tags.append("silent_failure")
            perturbations.append("silent_root_stops_heartbeat")

        elif case_type == "multi_root_independent":
            if len(root_candidates) >= 2:
                r1, r2 = rng.sample(root_candidates, 2)
            else:
                r1 = root_candidates[0]
                r2 = intermediate_nodes[0] if intermediate_nodes else r1
            true_roots = sorted(list(set([r1, r2])))
            failed_nodes.update(true_roots)
            expected_verdict = "DIAGNOSE"
            tags.append("multi_root")
            perturbations.append("two_simultaneous_independent_failures")

        elif case_type == "source_down_abstain":
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            root_sigs = get_signals_for(root)
            primary_src = root_sigs[0].source if root_sigs else "cloudwatch"
            down_sources.add(primary_src)
            expected_verdict = "ABSTAIN"
            perturbations.append("collector_outage_drops_heartbeats")
            tags.append("abstain_required")

        elif case_type == "quiet_timeline_abstain":
            true_roots = []
            expected_verdict = "ABSTAIN"
            tags.append("abstain_required")
            tags.append("quiet_timeline")

        elif case_type == "root_healthy_blamed_abstain":
            root = rng.choice(root_candidates)
            healthy_blamed_roots.add(root)
            true_roots = [root]
            expected_verdict = "ABSTAIN"
            perturbations.append("contradiction_root_healthy_while_dependents_blame")
            tags.append("abstain_required")
            tags.append("contradiction")

        elif case_type == "idle_silent_control":
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            expected_verdict = "DIAGNOSE"
            other_candidates = [n.id for n in topology.nodes if n.id != root]
            idle_control = rng.choice(other_candidates)
            tags.append("idle_healthy_control")
            tags.append(f"control_svc_{idle_control}")

        if down_sources:
            for sh in source_health:
                if sh.source in down_sources:
                    sh.status = "DOWN"

        onset_time_sec = 60 if failed_nodes or healthy_blamed_roots else 999999
        onset_dt = t_base + timedelta(seconds=onset_time_sec)

        # Telemetry generation
        for node_id, node in node_map.items():
            sigs = get_signals_for(node_id)
            for sig in sigs:
                interval = sig.interval_seconds
                t_curr = 0

                while t_curr < duration_sec:
                    cur_dt = t_base + timedelta(seconds=t_curr)
                    t_curr += interval

                    if sig.source in down_sources:
                        continue

                    if node_id in silent_nodes and t_curr >= onset_time_sec:
                        continue

                    is_failed = (node_id in failed_nodes and t_curr >= onset_time_sec)
                    is_dependent_of_failed = False
                    is_dependent_of_blamed = False

                    for fn in failed_nodes:
                        if node_id in callers[fn] and t_curr >= (onset_time_sec + 10):
                            is_dependent_of_failed = True
                            break

                    for hn in healthy_blamed_roots:
                        if node_id in callers[hn] and t_curr >= (onset_time_sec + 10):
                            is_dependent_of_blamed = True
                            break

                    if is_failed:
                        # Anomaly at root
                        if self.uninformative_severity:
                            # CRITICAL RULE: Root and non-root events draw severity from the EXACT SAME distribution!
                            sev = rng.choice(["CRITICAL", "HIGH", "MEDIUM"])
                        else:
                            sev = "CRITICAL" if t_curr >= onset_time_sec + 20 else "HIGH"
                        val = rng.uniform(85.0, 99.5) if "Utilization" in sig.metric else rng.uniform(500.0, 1200.0)

                    elif is_dependent_of_failed or is_dependent_of_blamed:
                        # Propagated symptom
                        if self.uninformative_severity:
                            # Exact same distribution as root failures!
                            sev = rng.choice(["CRITICAL", "HIGH", "MEDIUM"])
                        else:
                            sev = "HIGH" if rng.random() > 0.3 else "MEDIUM"
                        val = rng.uniform(60.0, 85.0) if "Utilization" in sig.metric else rng.uniform(250.0, 600.0)

                    elif node_id in healthy_blamed_roots:
                        sev = "LOW"
                        val = rng.uniform(8.0, 18.0) if "Utilization" in sig.metric else rng.uniform(10.0, 25.0)
                    else:
                        sev = "LOW"
                        val = rng.uniform(10.0, 25.0) if "Utilization" in sig.metric else rng.uniform(12.0, 30.0)

                    evt_idx += 1
                    events.append(EventData(
                        event_id=f"evt_{case_id}_{evt_idx:04d}",
                        event_time=cur_dt.isoformat(),
                        observed_time=(cur_dt + timedelta(milliseconds=rng.randint(5, 50))).isoformat(),
                        source=sig.source,
                        service=node_id,
                        metric=sig.metric,
                        value=round(val, 2),
                        severity=sev
                    ))

        onset_times_dict = {r: onset_dt.isoformat() for r in true_roots}
        symptom_map = {}
        for r in true_roots:
            for caller in callers[r]:
                symptom_map[caller] = r

        gt = GroundTruth(
            true_root_causes=true_roots,
            onset_times=onset_times_dict,
            symptom_to_cause=symptom_map,
            expected_verdict=expected_verdict,
            incident_groups=[[r] for r in true_roots]
        )

        return EvaluationCase(
            case_id=case_id,
            family_id=family_id,
            base_case_id=None,
            generator_version="3.0.0",
            seed=seed,
            topology=topology,
            events=events,
            source_health=source_health,
            ground_truth=gt,
            perturbations=perturbations,
            tags=tags,
            is_hard=("silent_failure" in tags or "contradiction" in tags or "multi_root" in tags),
            telemetry_policy=self.policy.model_dump()
        )
