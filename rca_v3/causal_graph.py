from typing import List, Dict, Any, Optional, Set, Tuple
import networkx as nx

from rca_engine.causal_graph import CausalGraphEngine


class CausalGraphV3:
    """
    Enhanced causal graph wrapper for rca_v3:
    - Directed failure-propagation paths (failure propagates from callee/dependency to caller/dependent).
    - Directed relatedness: two services are related iff one lies on a directed failure propagation path of the other.
    """

    def __init__(self, engine: CausalGraphEngine):
        self.engine = engine
        self.graph = engine.graph
        self.reversed_graph = engine._get_reversed_graph()

    @classmethod
    def from_topology(cls, topology_data: Any) -> "CausalGraphV3":
        engine = CausalGraphEngine.from_topology(topology_data)
        return cls(engine)

    def are_services_related(self, service1: str, service2: str, enable_directed: bool = True) -> bool:
        """
        Step 2d:
        If enable_directed is True:
            Two services are related iff one lies on a directed failure-propagation path of the other.
            Sharing a common caller or common dependency is NOT enough.
        If enable_directed is False (ablation / legacy):
            Falls back to undirected connectivity.
        """
        s1 = self.engine.resolve_service_name(service1)
        s2 = self.engine.resolve_service_name(service2)
        if s1 == s2:
            return True

        if not enable_directed:
            return self.engine.are_services_related(s1, s2)

        rev_g = self.reversed_graph
        if s1 not in rev_g or s2 not in rev_g:
            return False

        # Check if s1 propagates to s2 OR s2 propagates to s1
        return nx.has_path(rev_g, s1, s2) or nx.has_path(rev_g, s2, s1)

    def is_ancestor_cause(self, candidate_root: str, downstream_service: str) -> bool:
        """Returns True if failure at candidate_root can propagate directed to downstream_service."""
        c = self.engine.resolve_service_name(candidate_root)
        d = self.engine.resolve_service_name(downstream_service)
        if c == d:
            return True
        rev_g = self.reversed_graph
        if c not in rev_g or d not in rev_g:
            return False
        return nx.has_path(rev_g, c, d)

    def get_propagation_distance(self, from_service: str, to_service: str) -> Optional[int]:
        """Returns shortest path length from cause to symptom in failure propagation graph."""
        c = self.engine.resolve_service_name(from_service)
        d = self.engine.resolve_service_name(to_service)
        if c == d:
            return 0
        rev_g = self.reversed_graph
        if c not in rev_g or d not in rev_g:
            return None
        try:
            return nx.shortest_path_length(rev_g, source=c, target=d)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_all_downstream_dependents(self, service: str) -> List[str]:
        """All services that depend on `service` (reachable in reversed graph)."""
        svc = self.engine.resolve_service_name(service)
        rev_g = self.reversed_graph
        if svc not in rev_g:
            return []
        return list(nx.descendants(rev_g, svc))

    def get_all_upstream_dependencies(self, service: str) -> List[str]:
        """All services that `service` depends on (predecessors in reversed graph = dependencies in base graph)."""
        svc = self.engine.resolve_service_name(service)
        rev_g = self.reversed_graph
        if svc not in rev_g:
            return []
        return list(nx.ancestors(rev_g, svc))
