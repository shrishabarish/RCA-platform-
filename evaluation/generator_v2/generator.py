"""
Generator 2.0: Realistic synthetic cloud incident benchmark with periodic baseline telemetry,
heartbeats, source health, and parsimonious ground truth.
"""
import random
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Any, Optional, Set
from pathlib import Path

from evaluation.schema import (
    TopologyData,
    TopologyNode,
    EventData,
    SourceHealthRecord,
    GroundTruth,
    EvaluationCase
)
from rca_v2.telemetry_policy import TelemetryPolicy, ExpectedSignalPolicy


class GeneratorV2:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.policy = TelemetryPolicy()

    def get_topologies(self) -> List[TopologyData]:
        # Topology A: 6-node Standard Microservices
        topo_a_nodes = [
            TopologyNode(id="EC2", type="compute", label="EC2 Compute Host", criticality=0.9),
            TopologyNode(id="DynamoDB", type="database", label="DynamoDB Database", criticality=0.95),
            TopologyNode(id="Auth", type="auth", label="Auth & IdP Service", criticality=0.85),
            TopologyNode(id="Worker", type="worker", label="Background Worker", criticality=0.7),
            TopologyNode(id="API", type="api_gateway", label="Backend API Gateway", criticality=0.85),
            TopologyNode(id="Application", type="service", label="Web Application UI", criticality=0.6),
        ]
        topo_a_edges = [
            ["API", "EC2"],
            ["API", "DynamoDB"],
            ["API", "Auth"],
            ["Worker", "DynamoDB"],
            ["Worker", "EC2"],
            ["Application", "API"],
            ["Application", "Worker"]
        ]
        topo_a = TopologyData(nodes=topo_a_nodes, edges=topo_a_edges, topology_version="v2-topo-6node")

        # Topology B: 8-node E-commerce Platform
        topo_b_nodes = [
            TopologyNode(id="ComputeCluster", type="compute", label="K8s Compute Nodes", criticality=0.9),
            TopologyNode(id="PostgresPrimary", type="database", label="PostgreSQL Primary DB", criticality=0.95),
            TopologyNode(id="RedisCache", type="cache", label="Redis Cache", criticality=0.8),
            TopologyNode(id="AuthService", type="auth", label="Auth Service", criticality=0.9),
            TopologyNode(id="OrderService", type="service", label="Order Management Service", criticality=0.85),
            TopologyNode(id="CatalogService", type="service", label="Catalog Service", criticality=0.75),
            TopologyNode(id="APIGateway", type="api_gateway", label="API Gateway", criticality=0.9),
            TopologyNode(id="WebFrontend", type="service", label="Web Frontend", criticality=0.6),
        ]
        topo_b_edges = [
            ["APIGateway", "AuthService"],
            ["APIGateway", "OrderService"],
            ["APIGateway", "CatalogService"],
            ["OrderService", "PostgresPrimary"],
            ["OrderService", "ComputeCluster"],
            ["CatalogService", "RedisCache"],
            ["CatalogService", "PostgresPrimary"],
            ["CatalogService", "ComputeCluster"],
            ["WebFrontend", "APIGateway"]
        ]
        topo_b = TopologyData(nodes=topo_b_nodes, edges=topo_b_edges, topology_version="v2-topo-8node")

        # Topology C: 7-node Async Streaming Pipeline
        topo_c_nodes = [
            TopologyNode(id="WorkerHost", type="compute", label="Worker Host EC2", criticality=0.85),
            TopologyNode(id="EventStore", type="database", label="Event Store DB", criticality=0.95),
            TopologyNode(id="MessageQueue", type="queue", label="SQS Event Queue", criticality=0.85),
            TopologyNode(id="ProcessorA", type="worker", label="Stream Processor A", criticality=0.8),
            TopologyNode(id="ProcessorB", type="worker", label="Stream Processor B", criticality=0.75),
            TopologyNode(id="IngressAPI", type="api_gateway", label="Event Ingress Gateway", criticality=0.9),
            TopologyNode(id="MetricsDashboard", type="service", label="Monitoring UI", criticality=0.5),
        ]
        topo_c_edges = [
            ["IngressAPI", "MessageQueue"],
            ["ProcessorA", "MessageQueue"],
            ["ProcessorA", "EventStore"],
            ["ProcessorA", "WorkerHost"],
            ["ProcessorB", "MessageQueue"],
            ["ProcessorB", "EventStore"],
            ["ProcessorB", "WorkerHost"],
            ["MetricsDashboard", "IngressAPI"],
            ["MetricsDashboard", "ProcessorA"]
        ]
        topo_c = TopologyData(nodes=topo_c_nodes, edges=topo_c_edges, topology_version="v2-topo-7node")

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
        t_base = t0 or datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
        duration_sec = 300  # 5 min window

        # Adjacency and dependencies
        node_map = {n.id: n for n in topology.nodes}
        # In topology.edges: [caller, callee]
        callees: Dict[str, List[str]] = {n.id: [] for n in topology.nodes}
        callers: Dict[str, List[str]] = {n.id: [] for n in topology.nodes}
        for u, v in topology.edges:
            callees[u].append(v)
            callers[v].append(u)

        # Baseline periodic telemetry policy lookup
        def get_signals_for(node_id: str) -> List[ExpectedSignalPolicy]:
            ntype = node_map[node_id].type
            return self.policy.get_expected_signals_for_node(node_id, ntype)

        events: List[EventData] = []
        source_health: List[SourceHealthRecord] = []
        evt_idx = 0

        # All sources default to HEALTHY
        all_sources = {"cloudwatch", "otel", "datadog", "syslog", "application", "cloudtrail"}
        for s in sorted(all_sources):
            source_health.append(SourceHealthRecord(
                source=s,
                timestamp=t_base.isoformat(),
                status="HEALTHY"
            ))

        # Root candidate selection
        leaf_dependencies = [n.id for n in topology.nodes if not callees[n.id]]
        intermediate_nodes = [n.id for n in topology.nodes if callees[n.id] and callers[n.id]]
        root_candidates = leaf_dependencies if leaf_dependencies else list(node_map.keys())

        # Determine scenario parameters based on case_type
        # Types:
        # - "single_root_metric"
        # - "single_root_silent"
        # - "multi_root_independent"
        # - "source_down_abstain"
        # - "quiet_timeline_abstain"
        # - "root_healthy_blamed_abstain"
        # - "idle_silent_control"

        true_roots = []
        expected_verdict = "DIAGNOSE"
        perturbations = []
        tags = [case_type]
        is_hard = False

        failed_nodes: Set[str] = set()
        silent_nodes: Set[str] = set()
        down_sources: Set[str] = set()
        healthy_blamed_roots: Set[str] = set()

        if case_type == "single_root_metric":
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            expected_verdict = "DIAGNOSE"

        elif case_type == "single_root_silent":
            root = rng.choice(root_candidates)
            true_roots = [root]
            silent_nodes.add(root)
            failed_nodes.add(root)
            expected_verdict = "DIAGNOSE"
            perturbations.append("silent_failure_stops_heartbeat")

        elif case_type == "multi_root_independent":
            # Pick 2 disjoint roots
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
            # Find root's primary source and take it down
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
            # Real contradiction: root emits continuous healthy baseline heartbeats,
            # but callers report critical errors blaming root
            root = rng.choice(root_candidates)
            healthy_blamed_roots.add(root)
            true_roots = [root]
            expected_verdict = "ABSTAIN"
            perturbations.append("contradiction_root_healthy_while_dependents_blame")
            tags.append("abstain_required")
            tags.append("contradiction")

        elif case_type == "idle_silent_control":
            # True root is one service; another unrelated/upstream service is idle-silent
            root = rng.choice(root_candidates)
            true_roots = [root]
            failed_nodes.add(root)
            expected_verdict = "DIAGNOSE"
            # Choose an idle healthy non-root control
            other_candidates = [n.id for n in topology.nodes if n.id != root]
            idle_control = rng.choice(other_candidates)
            tags.append("idle_healthy_control")
            tags.append(f"control_svc_{idle_control}")

        # Update source health records if any down
        if down_sources:
            for sh in source_health:
                if sh.source in down_sources:
                    sh.status = "DOWN"

        # Generate Periodic Telemetry Timeline (0 to 300s)
        onset_time_sec = 60 if failed_nodes or healthy_blamed_roots else 999999
        onset_dt = t_base + timedelta(seconds=onset_time_sec)

        for node_id, node in node_map.items():
            sigs = get_signals_for(node_id)
            for sig in sigs:
                # Interval determines periodic heartbeats
                interval = sig.interval_seconds
                t_curr = 0

                while t_curr < duration_sec:
                    cur_dt = t_base + timedelta(seconds=t_curr)
                    t_curr += interval

                    # If collector outage for this source, drop event!
                    if sig.source in down_sources:
                        continue

                    # If silent failure on this node, stop emitting after onset!
                    if node_id in silent_nodes and t_curr >= onset_time_sec:
                        continue

                    # Determine value and severity
                    is_failed = (node_id in failed_nodes and t_curr >= onset_time_sec)
                    is_dependent_of_failed = False
                    is_dependent_of_blamed = False

                    # Check downstream propagation
                    for fn in failed_nodes:
                        if node_id in callers[fn] and t_curr >= (onset_time_sec + 10):
                            is_dependent_of_failed = True
                            break

                    for hn in healthy_blamed_roots:
                        if node_id in callers[hn] and t_curr >= (onset_time_sec + 10):
                            is_dependent_of_blamed = True
                            break

                    if is_failed:
                        # Root anomaly spike
                        sev = "CRITICAL" if t_curr >= onset_time_sec + 20 else "HIGH"
                        val = rng.uniform(85.0, 99.5) if "Utilization" in sig.metric else rng.uniform(500.0, 1200.0)
                    elif is_dependent_of_failed or is_dependent_of_blamed:
                        # Propagated symptom
                        sev = "HIGH" if rng.random() > 0.3 else "MEDIUM"
                        val = rng.uniform(60.0, 85.0) if "Utilization" in sig.metric else rng.uniform(250.0, 600.0)
                    elif node_id in healthy_blamed_roots:
                        # Emits strictly healthy baseline heartbeats (never anomalous)
                        sev = "LOW"
                        val = rng.uniform(8.0, 18.0) if "Utilization" in sig.metric else rng.uniform(10.0, 25.0)
                    else:
                        # Normal periodic baseline heartbeat with minor noise
                        sev = "LOW"
                        val = rng.uniform(10.0, 25.0) if "Utilization" in sig.metric else rng.uniform(12.0, 30.0)

                    # Only emit baseline heartbeats or actual anomalies
                    # (Periodic baseline emits normal values)
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

        # Sort events chronologically
        events.sort(key=lambda e: e.event_time)

        # Ground truth mapping
        gt_onset = {r: onset_dt.isoformat() for r in true_roots}
        gt_s2c = {}
        for fn in failed_nodes:
            for dep in callers[fn]:
                gt_s2c[dep] = fn

        ground_truth = GroundTruth(
            true_root_causes=true_roots,
            onset_times=gt_onset,
            symptom_to_cause=gt_s2c,
            expected_verdict=expected_verdict,
            incident_groups=[true_roots] if true_roots else []
        )

        return EvaluationCase(
            case_id=case_id,
            family_id=family_id,
            base_case_id=None,
            generator_version="2.0.0",
            seed=seed,
            topology=topology,
            events=events,
            source_health=source_health,
            ground_truth=ground_truth,
            perturbations=perturbations,
            tags=tags,
            is_hard=len(true_roots) > 1 or case_type == "single_root_silent",
            telemetry_policy=self.policy.model_dump()
        )
