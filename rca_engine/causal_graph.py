import networkx as nx
from typing import List, Dict, Any, Optional, Set, Tuple
from backend.app.core.config import config

SERVICE_ALIASES = config.service_aliases

TYPE_ICONS = {
    "compute": "🖥️",
    "database": "🗄️",
    "auth": "🔐",
    "worker": "⚙️",
    "service": "🌐",
    "frontend": "💻",
    "queue": "📬",
    "storage": "📦",
    "cache": "⚡",
    "lambda": "λ"
}


class CausalGraphEngine:
    """
    Advanced Dynamic Graph-Theoretic & Causal Inference Engine for Cloud Topologies.
    Supports on-the-fly microservice registration, dynamic dependency creation,
    live AWS infrastructure discovery, PageRank diffusion, blast radius calculations,
    and automatic collision-free topological coordinate layout.
    """

    def __init__(self):
        self.graph = self._build_cloud_topology()
        self._blast_radius_cache: Dict[str, List[str]] = {}
        self._direct_dependents_cache: Dict[str, List[str]] = {}
        self._causal_paths_cache: Dict[Tuple[str, str], List[List[str]]] = {}
        self._pagerank_cache: Optional[Dict[str, float]] = None
        self._reversed_graph_cache: Optional[nx.DiGraph] = None

    def invalidate_cache(self):
        """Clears cached topology computations when graph structure changes."""
        self._blast_radius_cache.clear()
        self._direct_dependents_cache.clear()
        self._causal_paths_cache.clear()
        self._pagerank_cache = None
        self._reversed_graph_cache = None

    def _get_reversed_graph(self) -> nx.DiGraph:
        if self._reversed_graph_cache is None:
            self._reversed_graph_cache = self.graph.reverse()
        return self._reversed_graph_cache

    def resolve_service_name(self, service: str) -> str:
        """Resolves alias service names to canonical topology node IDs."""
        if service in self.graph:
            return service
        clean = service.lower().strip()
        aliases = config.service_aliases
        if clean in aliases:
            canonical = aliases[clean]
            if canonical in self.graph:
                return canonical
        return service

    def are_services_related(self, service1: str, service2: str) -> bool:
        """Determines if two services are identical or connected in the dependency topology graph."""
        c1 = self.resolve_service_name(service1)
        c2 = self.resolve_service_name(service2)
        if c1 == c2:
            return True
        ug = self.graph.to_undirected()
        if c1 in ug and c2 in ug:
            return nx.has_path(ug, c1, c2)
        return False


    def _build_cloud_topology(self) -> nx.DiGraph:
        """Constructs the base cloud microservice dependency graph."""
        g = nx.DiGraph()

        nodes = [
            ("EC2", {"type": "compute", "label": "EC2 Instances / Host", "criticality": 0.9}),
            ("DynamoDB", {"type": "database", "label": "Database / PostgreSQL / DynamoDB", "criticality": 0.95}),
            ("Auth", {"type": "auth", "label": "Authentication & OAuth2 IdP", "criticality": 0.85}),
            ("Worker", {"type": "worker", "label": "Background Worker Pods", "criticality": 0.7}),
            ("API", {"type": "service", "label": "API Gateway / Backend Service", "criticality": 0.85}),
            ("Application", {"type": "frontend", "label": "Customer Web App & Frontend UI", "criticality": 0.6}),
        ]

        for node_id, attrs in nodes:
            g.add_node(node_id, **attrs)

        edges = [
            ("API", "EC2"),           # API Backend runs on EC2
            ("API", "DynamoDB"),      # API operations query Database
            ("API", "Auth"),          # API routes validate tokens via Auth
            ("Worker", "DynamoDB"),   # Worker reads/writes batch jobs in Database
            ("Worker", "EC2"),        # Worker runs on compute host
            ("Application", "API"),   # Web Frontend communicates with Backend API
            ("Application", "Worker"),# Web UI relies on async background reporting
        ]

        for src, dst in edges:
            g.add_edge(src, dst)

        return g

    # =========================================================================
    # DYNAMIC REGISTRATION & DISCOVERY APIS
    # =========================================================================

    def register_service(
        self,
        service_name: str,
        service_type: str = "service",
        label: Optional[str] = None,
        criticality: float = 0.7,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Dynamically registers a new microservice node in the topology graph.
        """
        name = service_name.strip()
        attrs = {
            "type": service_type,
            "label": label or name,
            "criticality": criticality,
            "metadata": metadata or {},
            "dynamic": True
        }
        self.graph.add_node(name, **attrs)
        self.invalidate_cache()
        return {"service": name, "attributes": attrs, "total_nodes": self.graph.number_of_nodes()}

    def register_dependency(self, source_service: str, target_service: str) -> Dict[str, Any]:
        """
        Dynamically registers a dependency edge: `source_service` depends on `target_service`.
        (A failure in `target_service` propagates upstream to `source_service`).
        """
        src = self.resolve_service_name(source_service)
        tgt = self.resolve_service_name(target_service)

        if src not in self.graph:
            self.register_service(src, service_type="service")
        if tgt not in self.graph:
            self.register_service(tgt, service_type="service")

        self.graph.add_edge(src, tgt)
        self.invalidate_cache()
        return {"source": src, "target": tgt, "relationship": "depends_on"}


    def auto_register_from_event(self, event) -> str:
        """
        Inspects an incoming telemetry event, automatically discovers any unknown service,
        infers its role/type, and links it into the causal graph.
        """
        service = getattr(event, "service", None)
        if not service:
            return "UNKNOWN"

        canonical = self.resolve_service_name(service)
        if canonical in self.graph:
            return canonical

        # Infer service type from name or metadata
        s_lower = service.lower()
        if any(w in s_lower for w in ("db", "database", "sql", "postgres", "dynamo", "redis", "mongo")):
            stype = "database"
            crit = 0.9
        elif any(w in s_lower for w in ("auth", "idp", "jwt", "oauth", "sso", "security")):
            stype = "auth"
            crit = 0.85
        elif any(w in s_lower for w in ("worker", "job", "queue", "consumer", "processor")):
            stype = "worker"
            crit = 0.75
        elif any(w in s_lower for w in ("ui", "front", "web", "app", "client", "portal")):
            stype = "frontend"
            crit = 0.6
        elif any(w in s_lower for w in ("sqs", "sns", "kafka", "mq", "stream")):
            stype = "queue"
            crit = 0.8
        elif any(w in s_lower for w in ("s3", "bucket", "storage", "blob")):
            stype = "storage"
            crit = 0.85
        else:
            stype = "service"
            crit = 0.8

        self.register_service(service, service_type=stype, criticality=crit)

        # Check for explicit dependency hints in event metadata
        meta = getattr(event, "metadata", {}) or {}
        dep = meta.get("dependency") or meta.get("target") or meta.get("callee")
        if dep:
            self.register_dependency(service, str(dep))
        elif stype == "service":
            # Link default backend edge to Database & Compute
            self.register_dependency(service, "DynamoDB")
            self.register_dependency(service, "EC2")
            self.register_dependency("Application", service)
        elif stype == "worker":
            self.register_dependency(service, "DynamoDB")
            self.register_dependency(service, "EC2")

        return service

    # =========================================================================
    # DYNAMIC AUTO-LAYOUT ENGINE
    # =========================================================================

    def auto_layout_nodes(self, width: int = 860, height: int = 460) -> Dict[str, Dict[str, Any]]:
        """
        Calculates collision-free, tiered (x, y) coordinates dynamically for any number of services.
        Tier 0: Frontend / Client (right)
        Tier 1: Core API & Gateways (center-right)
        Tier 2: Microservices, Workers & Compute (center-left)
        Tier 3: Databases, Storage & Queues (left)
        """
        tiers: Dict[int, List[str]] = {0: [], 1: [], 2: [], 3: []}

        for node, data in self.graph.nodes(data=True):
            ntype = data.get("type", "service")
            if ntype in ("frontend", "client"):
                tiers[0].append(node)
            elif ntype in ("service", "gateway", "api"):
                tiers[1].append(node)
            elif ntype in ("compute", "worker", "auth", "lambda"):
                tiers[2].append(node)
            else:
                tiers[3].append(node)

        # Invert tier X coordinates so client is on right and base infrastructure on left:
        # X: Tier 3 (120) -> Tier 2 (340) -> Tier 1 (560) -> Tier 0 (740)
        tier_x = {3: 110, 2: 320, 1: 530, 0: 730}
        positions: Dict[str, Dict[str, Any]] = {}

        for tier_idx, node_list in tiers.items():
            count = len(node_list)
            if count == 0:
                continue
            x_pos = tier_x.get(tier_idx, 300)
            y_start = 80
            y_end = height - 60
            y_step = (y_end - y_start) / max(count, 1)

            for i, n in enumerate(node_list):
                y_pos = int(y_start + (i + 0.5) * y_step) if count > 1 else (height // 2)
                node_data = self.graph.nodes[n]
                ntype = node_data.get("type", "service")
                icon = TYPE_ICONS.get(ntype, "🌐")

                positions[n] = {
                    "x": x_pos,
                    "y": y_pos,
                    "icon": icon,
                    "title": node_data.get("label", n),
                    "desc": f"{ntype.title()} Tier"
                }

        return positions

    # =========================================================================
    # GRAPH METRICS & CAUSAL REASONING
    # =========================================================================

    def get_blast_radius(self, service: str) -> List[str]:
        svc = self.resolve_service_name(service)
        if svc not in self.graph:
            return []
        if svc in self._blast_radius_cache:
            return list(self._blast_radius_cache[svc])
        reversed_g = self._get_reversed_graph()
        descendants = list(nx.descendants(reversed_g, svc))
        self._blast_radius_cache[svc] = descendants
        return list(descendants)

    def get_direct_dependents(self, service: str) -> List[str]:
        svc = self.resolve_service_name(service)
        if svc not in self.graph:
            return []
        if svc in self._direct_dependents_cache:
            return list(self._direct_dependents_cache[svc])
        direct = list(self.graph.predecessors(svc))
        self._direct_dependents_cache[svc] = direct
        return list(direct)

    def find_causal_paths(self, root_candidate: str, affected_service: str) -> List[List[str]]:
        root = self.resolve_service_name(root_candidate)
        affected = self.resolve_service_name(affected_service)
        if root not in self.graph or affected not in self.graph:
            return []
        if root == affected:
            return [[root]]

        cache_key = (root, affected)
        if cache_key in self._causal_paths_cache:
            return [list(p) for p in self._causal_paths_cache[cache_key]]

        reversed_g = self._get_reversed_graph()
        cutoff = config.topology.max_path_search_depth
        try:
            paths = list(nx.all_simple_paths(reversed_g, source=root, target=affected, cutoff=cutoff))
            self._causal_paths_cache[cache_key] = paths
            return [list(p) for p in paths]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def compute_pagerank_centrality(self, personalization: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        if personalization is None and self._pagerank_cache is not None:
            return dict(self._pagerank_cache)
        try:
            reversed_g = self._get_reversed_graph()
            if personalization:
                total = sum(personalization.values())
                norm_pers = {k: v / total for k, v in personalization.items() if k in reversed_g} if total > 0 else None
            else:
                norm_pers = None
            pr = nx.pagerank(reversed_g, alpha=0.85, personalization=norm_pers)
            if personalization is None:
                self._pagerank_cache = pr
            return pr
        except Exception:
            nodes = list(self.graph.nodes())
            return {n: 1.0 / len(nodes) for n in nodes} if nodes else {}


    def compute_node_betweenness(self) -> Dict[str, float]:
        return nx.betweenness_centrality(self.graph)

    def simulate_what_if_failure(self, failed_service: str) -> Dict[str, Any]:
        svc = self.resolve_service_name(failed_service)
        if svc not in self.graph:
            return {"error": f"Service {failed_service} does not exist in topology graph"}

        direct = self.get_direct_dependents(svc)
        transitive = self.get_blast_radius(svc)
        all_affected = list(set(direct + transitive))

        node_attrs = self.graph.nodes[svc]
        criticality = node_attrs.get("criticality", 0.5)
        risk_score = round(min(100.0, (len(all_affected) * 20.0 + criticality * 40.0)), 1)

        return {
            "target_service": svc,
            "criticality_level": "HIGH" if criticality >= 0.85 else "MEDIUM",
            "direct_downstream_count": len(direct),
            "direct_downstream_services": direct,
            "total_blast_radius_count": len(all_affected),
            "all_impacted_services": all_affected,
            "estimated_architecture_risk_score": risk_score,
            "remediation_recommendation": f"Ensure active-active redundancy and circuit breakers are active on {svc}."
        }

    def export_graph_topology(self, root_cause: Optional[str] = None, affected_services: Optional[List[str]] = None) -> Dict[str, Any]:
        affected_services = affected_services or []
        positions = self.auto_layout_nodes()
        nodes_data = []

        for node, data in self.graph.nodes(data=True):
            status = "HEALTHY"
            if root_cause and node == root_cause:
                status = "ROOT_CAUSE"
            elif node in affected_services:
                status = "DEGRADED"

            pos = positions.get(node, {"x": 300, "y": 200, "icon": "🌐", "title": node, "desc": "Service"})
            nodes_data.append({
                "id": node,
                "label": data.get("label", node),
                "type": data.get("type", "service"),
                "status": status,
                "criticality": data.get("criticality", 0.5),
                "x": pos["x"],
                "y": pos["y"],
                "icon": pos["icon"],
                "desc": pos.get("desc", "Service Tier")
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
            "edges": edges_data,
            "total_nodes": len(nodes_data),
            "total_edges": len(edges_data)
        }


# Global instance
causal_graph_engine = CausalGraphEngine()
