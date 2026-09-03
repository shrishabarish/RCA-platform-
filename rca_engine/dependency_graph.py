import networkx as nx
from typing import List, Dict, Any, Optional
from rca_engine.causal_graph import causal_graph_engine, CausalGraphEngine


def build_dependency_graph() -> nx.DiGraph:
    """Returns the NetworkX DiGraph instance."""
    return causal_graph_engine.graph


def get_downstream_affected(g: nx.DiGraph, resource: str) -> List[str]:
    """Returns direct dependents of a service."""
    return causal_graph_engine.get_direct_dependents(resource)


def get_all_downstream_recursive(g: nx.DiGraph, resource: str) -> List[str]:
    """Returns all transitive dependents (blast radius)."""
    return causal_graph_engine.get_blast_radius(resource)


def export_topology(root_cause: Optional[str] = None, affected_services: Optional[List[str]] = None) -> Dict[str, Any]:
    """Exports topology formatted for frontend & REST API."""
    return causal_graph_engine.export_graph_topology(root_cause, affected_services)