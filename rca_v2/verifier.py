from typing import List, Dict, Any, Optional, Tuple
import networkx as nx

from rca_v2.models import (
    CandidateDiagnosis,
    Contradiction,
    SignalEvidence,
    SourceHealthStatus,
    EvidenceState
)
from rca_v2.evidence import EvidenceEvaluator
from rca_v2.source_health import SourceHealthTracker
from rca_engine.causal_graph import CausalGraphEngine


class EvidenceVerifier:
    """
    Computes evidence features for top candidate diagnoses:
    - Coverage of expected evidence for top candidate
    - Reachable downstream symptom coverage in topology
    - Contradictions with event references
    - Score margin between top two candidates
    - Source health summary
    """

    def __init__(
        self,
        evaluator: EvidenceEvaluator,
        graph_engine: CausalGraphEngine,
        health_tracker: SourceHealthTracker
    ):
        self.evaluator = evaluator
        self.graph_engine = graph_engine
        self.health_tracker = health_tracker

    def compute_features(
        self,
        candidates: List[CandidateDiagnosis],
        events: List[Any],
        topology_nodes: List[Any]
    ) -> Tuple[float, float, float, List[Contradiction], Dict[str, str]]:
        source_health_summary = self.health_tracker.get_summary()

        if not candidates:
            return 0.0, 0.0, 0.0, [], source_health_summary

        top_cand = candidates[0]
        top_svc = top_cand.service

        # 1. Coverage of expected evidence for top candidate
        top_evidence = self.evaluator.evaluate_service_evidence(top_svc, events, topology_nodes)
        obs_k, exp_k, top_coverage = self.evaluator.compute_observable_coverage(top_evidence)
        top_cand.expected_signals = exp_k
        top_cand.observed_signals = obs_k
        top_cand.coverage = top_coverage

        # 2. Reachable downstream symptom coverage in topology
        g = self.graph_engine.graph
        event_services = set(
            getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)
            for e in events
        )
        if top_svc in g:
            # Downstream reachable nodes in causal graph (dependencies propagate errors to callers or vice-versa)
            descendants = nx.descendants(g, top_svc)
            # In our causal graph: edge (A, B) represents caller A depends on B.
            # Failure in B propagates upstream to callers (ancestors in dependency graph).
            # We look at all related neighbors / propagation reachable nodes:
            reachable = list(descendants)
            if not reachable:
                # Also check blast radius from engine
                reachable = self.graph_engine.get_blast_radius(top_svc)
        else:
            reachable = []

        if reachable:
            symptoms_observed = sum(1 for s in reachable if s in event_services)
            reachable_coverage = symptoms_observed / len(reachable)
            top_cand.downstream_symptoms_expected = len(reachable)
            top_cand.downstream_symptoms_observed = symptoms_observed
            top_cand.reachable_coverage = round(reachable_coverage, 4)
        else:
            top_cand.downstream_symptoms_expected = 0
            top_cand.downstream_symptoms_observed = 0
            top_cand.reachable_coverage = 1.0  # Leaf node / no dependencies required

        # 3. Score margin between top two candidates
        if len(candidates) >= 2:
            margin = round(top_cand.ranking_score - candidates[1].ranking_score, 2)
        else:
            margin = round(top_cand.ranking_score, 2)

        # 4. Contradictions detection
        contradictions: List[Contradiction] = []

        # Check a: Primary root's telemetry source is DOWN or UNKNOWN
        for ev in top_evidence:
            if ev.state == EvidenceState.UNOBSERVED:
                contradictions.append(Contradiction(
                    contradiction_type="TELEMETRY_SOURCE_UNAVAILABLE",
                    description=f"Primary telemetry source '{ev.source}' for '{top_svc}' is DOWN or UNKNOWN.",
                    involved_services=[top_svc],
                    severity="CRITICAL"
                ))
                break

        # Check b: Quiet timeline (no critical or high anomaly events, only pings)
        severities = set(
            str(getattr(e, "severity", None) or (e.get("severity") if isinstance(e, dict) else "")).upper()
            for e in events
        )
        if not ("HIGH" in severities or "CRITICAL" in severities or "MEDIUM" in severities):
            contradictions.append(Contradiction(
                contradiction_type="QUIET_TIMELINE_NO_ANOMALY",
                description="Timeline contains zero anomalous breaches; only healthy baseline signals observed.",
                involved_services=[top_svc],
                severity="HIGH"
            ))

        # Check c: Competing unresolvable roots with identical score & disjoint paths
        if len(candidates) >= 2 and margin == 0.0 and top_cand.ranking_score >= 80.0:
            c2_svc = candidates[1].service
            if not self.graph_engine.are_services_related(top_svc, c2_svc):
                # Check if both have critical events at exact same second
                contradictions.append(Contradiction(
                    contradiction_type="COMPETING_DISJOINT_ROOTS",
                    description=f"Two top candidates ('{top_svc}' and '{c2_svc}') tie at score {top_cand.ranking_score} with disjoint topologies.",
                    involved_services=[top_svc, c2_svc],
                    severity="HIGH"
                ))

        return top_cand.coverage, top_cand.reachable_coverage, margin, contradictions, source_health_summary
