import networkx as nx
from typing import List, Dict, Any, Optional, Set, Tuple


SERVICE_ALIASES = {
    "codereview-backend": "API",
    "backend": "API",
    "backend-service": "API",
    "postgresql": "DynamoDB",
    "postgres": "DynamoDB",
    "database": "DynamoDB",
    "db-primary": "DynamoDB",
    "rds": "DynamoDB",
    "frontend": "Application",
    "frontend-service": "Application",
    "ui": "Application",
    "client": "Application",
    "ec2-instance": "EC2",
    "compute": "EC2"
}


class CausalGraphEngine:
    """
    Advanced Graph-Theoretic & Causal Inference Engine for Cloud Topologies.
    Uses NetworkX DiGraph to model microservice dependencies, compute PageRank
    anomaly diffusion, trace causal failure paths, and calculate blast radius.
    """

    def __init__(self):
        self.graph = self._build_cloud_topology()

    def resolve_service_name(self, service: str) -> str:
        """Resolves alias service names to canonical topology node IDs."""
        if service in self.graph:
            return service
        clean = service.lower().strip()
        if clean in SERVICE_ALIASES:
            canonical = SERVICE_ALIASES[clean]
            if canonical in self.graph:
                return canonical
        return service

    def _build_cloud_topology(self) -> nx.DiGraph:
        """
        Constructs the cloud microservice dependency graph.
        Edge A -> B signifies: "A depends on B" (Failure of B propagates to A).
        """
        g = nx.DiGraph()

        # Service Nodes with rich cloud metadata
        nodes = [
            ("EC2", {"type": "compute", "label": "EC2 Instances / Host", "criticality": 0.9}),
            ("DynamoDB", {"type": "database", "label": "Database / PostgreSQL / DynamoDB", "criticality": 0.95}),
            ("Auth", {"type": "auth", "label": "Authentication & OAuth2 IdP", "criticality": 0.85}),
            ("Worker", {"type": "compute", "label": "Background Worker Pods", "criticality": 0.7}),
            ("API", {"type": "service", "label": "API Gateway / Backend Service", "criticality": 0.85}),
            ("Application", {"type": "frontend", "label": "Customer Web App & Frontend UI", "criticality": 0.6}),
        ]

        for node_id, attrs in nodes:
            g.add_node(node_id, **attrs)

        # Microservice Dependency Edges: [Source] depends on [Target]
        edges = [
            ("API", "EC2"),           # API Backend runs on EC2
            ("API", "DynamoDB"),      # API operations query Database (PostgreSQL / DynamoDB)
            ("API", "Auth"),          # API routes validate tokens via Auth
            ("Worker", "DynamoDB"),   # Worker reads/writes batch jobs in Database
            ("Worker", "EC2"),        # Worker runs on compute host
            ("Application", "API"),   # Web Frontend communicates with Backend API
            ("Application", "Worker"),# Web UI relies on async background reporting
        ]

        for src, dst in edges:
            g.add_edge(src, dst)

        return g

    def get_blast_radius(self, service: str) -> List[str]:
        """
        Returns all direct and indirect downstream services that depend on `service`.
        Uses reversed graph reachability (since edges point dependent -> dependency).
        """
        svc = self.resolve_service_name(service)
        if svc not in self.graph:
            return []
        reversed_g = self.graph.reverse()
        descendants = nx.descendants(reversed_g, svc)
        return list(descendants)

    def get_direct_dependents(self, service: str) -> List[str]:
        """
        Returns services that directly depend on `service`.
        In NetworkX, predecessors of `service` are nodes that point TO `service`.
        """
        svc = self.resolve_service_name(service)
        if svc not in self.graph:
            return []
        return list(self.graph.predecessors(svc))

    def find_causal_paths(self, root_candidate: str, affected_service: str) -> List[List[str]]:
        """
        Traces all directed causal failure propagation paths from root_candidate to affected_service.
        Path format: [RootCandidate, IntermediateService, ..., AffectedService]
        """
        if root_candidate not in self.graph or affected_service not in self.graph:
            return []

        if root_candidate == affected_service:
            return [[root_candidate]]

        reversed_g = self.graph.reverse()
        try:
            paths = list(nx.all_simple_paths(reversed_g, source=root_candidate, target=affected_service))
            return paths
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def compute_pagerank_centrality(self, personalization: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        Computes Personalized PageRank / Random Walk with Restart (RWR) across the dependency graph.
        Higher scores indicate greater structural centrality and potential for cascading failure.
        """
        try:
            # We compute PageRank on the reverse graph so influence flows from dependency to dependents
            reversed_g = self.graph.reverse()
            if personalization:
                # Normalize personalization vector
                total = sum(personalization.values())
                if total > 0:
                    norm_pers = {k: v / total for k, v in personalization.items() if k in reversed_g}
                else:
                    norm_pers = None
            else:
                norm_pers = None

            return nx.pagerank(reversed_g, alpha=0.85, personalization=norm_pers)
        except Exception:
            # Fallback to uniform distribution
            nodes = list(self.graph.nodes())
            return {n: 1.0 / len(nodes) for n in nodes} if nodes else {}

    def compute_node_betweenness(self) -> Dict[str, float]:
        """
        Computes Betweenness Centrality for each service in the graph.
        Identifies critical bottleneck services whose failure fragments the architecture.
        """
        return nx.betweenness_centrality(self.graph)

    def simulate_what_if_failure(self, failed_service: str) -> Dict[str, Any]:
        """
        Predictive What-If Simulation:
        Evaluates the potential blast radius, degraded services, and critical path risk
        if `failed_service` were to experience total outage.
        """
        if failed_service not in self.graph:
            return {"error": f"Service {failed_service} does not exist in topology graph"}

        direct = self.get_direct_dependents(failed_service)
        transitive = self.get_blast_radius(failed_service)
        all_affected = list(set(direct + transitive))

        # Criticality assessment
        node_attrs = self.graph.nodes[failed_service]
        criticality = node_attrs.get("criticality", 0.5)

        risk_score = round(min(100.0, (len(all_affected) * 20.0 + criticality * 40.0)), 1)

        return {
            "target_service": failed_service,
            "criticality_level": "HIGH" if criticality >= 0.85 else "MEDIUM",
            "direct_downstream_count": len(direct),
            "direct_downstream_services": direct,
            "total_blast_radius_count": len(all_affected),
            "all_impacted_services": all_affected,
            "estimated_architecture_risk_score": risk_score,
            "remediation_recommendation": f"Ensure active-active redundancy and circuit breakers are active on {failed_service}."
        }

    def export_graph_topology(self, root_cause: Optional[str] = None, affected_services: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Exports graph nodes, edges, and real-time status for the UI visualization.
        """
        affected_services = affected_services or []
        nodes_data = []

        for node, data in self.graph.nodes(data=True):
            status = "HEALTHY"
            if root_cause and node == root_cause:
                status = "ROOT_CAUSE"
            elif node in affected_services:
                status = "DEGRADED"

            nodes_data.append({
                "id": node,
                "label": data.get("label", node),
                "type": data.get("type", "service"),
                "status": status,
                "criticality": data.get("criticality", 0.5)
            })

        edges_data = []
        for u, v in self.graph.edges():
            edges_data.append({
                "source": u,
                "target": v,
                "relationship": "depends_on"
            })

        return {
            "nodes": nodes_data,
            "edges": edges_data
        }


# Global instance
causal_graph_engine = CausalGraphEngine()
