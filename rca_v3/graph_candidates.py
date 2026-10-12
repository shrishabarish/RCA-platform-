from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone

from rca_v2.models import SourceHealthStatus
from rca_v2.source_health import SourceHealthTracker
from rca_v2.telemetry_policy import TelemetryPolicy
from rca_v3.models import CandidateDiagnosis
from rca_v3.causal_graph import CausalGraphV3


class GraphCandidateGenerator:
    """
    Step 2a:
    Generates unobserved candidates from topology when:
    - Service has NO observed events.
    - Expected signals are MISSING.
    - Telemetry sources for those signals are HEALTHY.
    - Downstream dependents show observed symptoms.
    """

    def __init__(
        self,
        graph_v3: CausalGraphV3,
        policy: TelemetryPolicy,
        health_tracker: SourceHealthTracker
    ):
        self.graph_v3 = graph_v3
        self.policy = policy
        self.health_tracker = health_tracker

    def find_unobserved_candidates(
        self,
        topology_nodes: List[Any],
        event_services: Set[str],
        service_earliest_times: Dict[str, str]
    ) -> List[CandidateDiagnosis]:
        unobserved: List[CandidateDiagnosis] = []

        for node in topology_nodes:
            node_id = getattr(node, "id", None) or (node.get("id") if isinstance(node, dict) else str(node))
            node_type = getattr(node, "type", None) or (node.get("type") if isinstance(node, dict) else "service")

            # Must have NO events
            if node_id in event_services:
                continue

            # Must have expected signals defined in policy
            expected_signals = self.policy.get_expected_signals_for_node(node_id, node_type)
            if not expected_signals:
                continue

            # Telemetry source must be HEALTHY (not DOWN)
            source_healthy = True
            for sig in expected_signals:
                src_status = self.health_tracker.get_source_status(sig.source)
                if src_status == SourceHealthStatus.DOWN:
                    source_healthy = False
                    break

            if not source_healthy:
                continue

            # Dependents must show symptoms
            dependents = self.graph_v3.get_all_downstream_dependents(node_id)
            symptom_dependents = [d for d in dependents if d in event_services]
            if not symptom_dependents:
                continue

            # Impute earliest onset time as the earliest dependent symptom time
            dependent_times = [
                service_earliest_times[d] for d in symptom_dependents if d in service_earliest_times
            ]
            earliest_t = min(dependent_times) if dependent_times else datetime.now(timezone.utc).isoformat()

            unobserved.append(CandidateDiagnosis(
                service=node_id,
                ranking_score=0.0,
                symptom_coverage=0.0,
                marginal_symptom_coverage=0.0,
                combined_score=0.0,
                earliest_event_time=earliest_t,
                rank=999,
                is_unobserved=True,
                expected_signals=len(expected_signals),
                observed_signals=0,
                coverage=0.0,
                downstream_symptoms_observed=len(symptom_dependents),
                downstream_symptoms_expected=len(dependents),
                reachable_coverage=len(symptom_dependents) / len(dependents) if dependents else 0.0
            ))

        return unobserved
