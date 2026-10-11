from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from rca_v2.models import EvidenceState, SignalEvidence, SourceHealthStatus
from rca_v2.telemetry_policy import TelemetryPolicy, ExpectedSignalPolicy
from rca_v2.source_health import SourceHealthTracker


class EvidenceEvaluator:
    """
    Evaluates service evidence across expected signals according to TelemetryPolicy and SourceHealthTracker.
    Strict Rule: When a source is DOWN or UNKNOWN, missing telemetry is NEVER counted
    as evidence against or for a service.
    """

    def __init__(self, policy: Optional[TelemetryPolicy] = None, health_tracker: Optional[SourceHealthTracker] = None):
        self.policy = policy or TelemetryPolicy()
        self.health_tracker = health_tracker or SourceHealthTracker()

    def evaluate_service_evidence(
        self,
        service: str,
        events: List[Any],
        topology_nodes: List[Any],
        incident_window_end: Optional[datetime] = None
    ) -> List[SignalEvidence]:
        # Determine node type from topology
        node_type = "service"
        for n in topology_nodes:
            n_id = getattr(n, "id", None) or (n.get("id") if isinstance(n, dict) else str(n))
            if n_id == service:
                node_type = getattr(n, "type", "service") or (n.get("type", "service") if isinstance(n, dict) else "service")
                break

        expected_signals = self.policy.get_expected_signals_for_node(service, node_type)
        service_events = [
            e for e in events
            if (getattr(e, "service", None) or (e.get("service") if isinstance(e, dict) else None)) == service
        ]

        # Index service events by metric
        event_by_metric: Dict[str, List[Any]] = defaultdict(list)
        for e in service_events:
            metric = getattr(e, "metric", None) or (e.get("metric") if isinstance(e, dict) else None)
            if metric:
                event_by_metric[metric].append(e)

        results: List[SignalEvidence] = []
        for exp in expected_signals:
            matched_events = event_by_metric.get(exp.metric, [])
            source_status = self.health_tracker.get_source_status(exp.source, as_of=incident_window_end)

            if matched_events:
                # Evidence is physically present
                latest_ev = matched_events[-1]
                ev_id = getattr(latest_ev, "event_id", None) or (latest_ev.get("event_id") if isinstance(latest_ev, dict) else None)
                ts_str = getattr(latest_ev, "event_time", None) or (latest_ev.get("event_time") if isinstance(latest_ev, dict) else None)
                val = getattr(latest_ev, "value", None) or (latest_ev.get("value") if isinstance(latest_ev, dict) else None)
                src = getattr(latest_ev, "source", None) or (latest_ev.get("source") if isinstance(latest_ev, dict) else exp.source)
                
                # Check for staleness if incident_window_end is defined and event is older than 10m
                state = EvidenceState.PRESENT
                if ts_str and incident_window_end:
                    try:
                        dt = datetime.fromisoformat(ts_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if (incident_window_end - dt) > timedelta(minutes=15):
                            state = EvidenceState.STALE
                    except Exception:
                        pass

                results.append(SignalEvidence(
                    service=service,
                    signal_name=exp.metric,
                    source=src,
                    state=state,
                    event_ref=ev_id,
                    timestamp=ts_str,
                    value=float(val) if val is not None else None,
                    details=f"Signal {exp.metric} confirmed via {src}"
                ))
            else:
                # Signal absent in events
                if source_status in (SourceHealthStatus.DOWN, SourceHealthStatus.UNKNOWN):
                    # RULE 2b: Source is DOWN/UNKNOWN. Missing telemetry is NEVER counted against or for service.
                    results.append(SignalEvidence(
                        service=service,
                        signal_name=exp.metric,
                        source=exp.source,
                        state=EvidenceState.UNOBSERVED,
                        details=f"Telemetry source {exp.source} is {source_status.value}; signal cannot be observed"
                    ))
                else:
                    # Source is healthy
                    if exp.is_anomaly_only:
                        # Silence is expected under healthy conditions
                        results.append(SignalEvidence(
                            service=service,
                            signal_name=exp.metric,
                            source=exp.source,
                            state=EvidenceState.QUIET,
                            details=f"Signal quiet as expected on healthy source {exp.source}"
                        ))
                    else:
                        # Periodic metric missing despite healthy pipeline
                        results.append(SignalEvidence(
                            service=service,
                            signal_name=exp.metric,
                            source=exp.source,
                            state=EvidenceState.MISSING,
                            details=f"Expected telemetry missing from healthy source {exp.source}"
                        ))

        return results

    def compute_observable_coverage(self, evidence_list: List[SignalEvidence]) -> Tuple[int, int, float]:
        """
        Returns (observed_count, observable_count, coverage_ratio).
        Signals marked UNOBSERVED (due to DOWN/UNKNOWN sources) are excluded from both numerator and denominator.
        """
        observable = [e for e in evidence_list if e.state != EvidenceState.UNOBSERVED]
        if not observable:
            return 0, 0, 0.0

        observed = [e for e in observable if e.state in (EvidenceState.PRESENT, EvidenceState.STALE)]
        ratio = len(observed) / len(observable)
        return len(observed), len(observable), round(ratio, 4)
