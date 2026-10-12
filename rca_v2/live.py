import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union

from backend.app.models.event import CloudEvent, Severity, Incident
from evaluation.schema import EventData, SourceHealthRecord
from rca_v2.models import DiagnosisResult, Verdict
from rca_v2.diagnose import diagnose
from rca_v2.config import RCAv2Config
from rca_v2.telemetry_policy import TelemetryPolicy
from rca_engine.causal_graph import causal_graph_engine, CausalGraphEngine
from rca_engine.analyzer import analyze_incident

logger = logging.getLogger("cloudlens.rca_v2.live")


class LiveEventAdapter:
    """
    Unified adapter converting live CloudEvent objects and case EventData objects into
    identical normalized CloudEvent representations for rca_v2.
    Guarantee L1: Both paths produce identical input for the same events.
    """

    VALID_CLOUDEVENT_SOURCES = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}

    @classmethod
    def adapt_cloud_event(cls, event: CloudEvent) -> CloudEvent:
        """Normalizes an existing CloudEvent to ensure timezone awareness and standardized source."""
        ts = event.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        src = event.source.lower() if event.source and event.source.lower() in cls.VALID_CLOUDEVENT_SOURCES else 'application'
        return CloudEvent(
            event_id=event.event_id,
            timestamp=ts,
            source=src,
            event_type=event.event_type or f"{event.metric.lower()}_anomaly",
            service=event.service,
            resource_id=event.resource_id or f"res_{event.service.lower()}",
            metric=event.metric,
            value=float(event.value),
            severity=event.severity
        )

    @classmethod
    def adapt_case_event(cls, event: EventData, fallback_id: Optional[str] = None) -> CloudEvent:
        """Converts an evaluation case EventData model into a CloudEvent."""
        dt = datetime.fromisoformat(event.event_time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        raw_src = event.source.lower() if event.source else 'application'
        clean_src = raw_src if raw_src in cls.VALID_CLOUDEVENT_SOURCES else 'application'
        try:
            sev = Severity(event.severity.upper())
        except Exception:
            sev = Severity.HIGH

        return CloudEvent(
            event_id=event.event_id or fallback_id or "evt_case",
            timestamp=dt,
            source=clean_src,
            event_type=f"{event.metric.lower()}_anomaly",
            service=event.service,
            resource_id=f"res_{event.service.lower()}",
            metric=event.metric,
            value=float(event.value),
            severity=sev
        )

    @classmethod
    def adapt_events(cls, events: List[Any]) -> List[CloudEvent]:
        """Converts an arbitrary collection of CloudEvents, EventDatas, or dicts uniformly."""
        normalized: List[CloudEvent] = []
        for idx, item in enumerate(events):
            if isinstance(item, CloudEvent):
                normalized.append(cls.adapt_cloud_event(item))
            elif isinstance(item, EventData):
                normalized.append(cls.adapt_case_event(item, fallback_id=f"evt_{idx}"))
            elif isinstance(item, dict):
                # Dict input
                dt_str = item.get("event_time") or item.get("timestamp")
                dt = datetime.fromisoformat(dt_str) if dt_str else datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                src = item.get("source", "application").lower()
                src = src if src in cls.VALID_SOURCES else "application"
                sev_str = item.get("severity", "HIGH").upper()
                try:
                    sev = Severity(sev_str)
                except Exception:
                    sev = Severity.HIGH
                svc = item.get("service", "")
                metric = item.get("metric", "Anomaly")
                val = float(item.get("value", 1.0))
                normalized.append(CloudEvent(
                    event_id=item.get("event_id", f"evt_{idx}"),
                    timestamp=dt,
                    source=src,
                    event_type=item.get("event_type", f"{metric.lower()}_anomaly"),
                    service=svc,
                    resource_id=item.get("resource_id", f"res_{svc.lower()}"),
                    metric=metric,
                    value=val,
                    severity=sev
                ))
        return normalized


def derive_live_source_health(
    events: List[CloudEvent],
    reference_time: Optional[datetime] = None,
    expected_sources: Optional[List[str]] = None,
    source_intervals: Optional[Dict[str, int]] = None,
    policy: Optional[TelemetryPolicy] = None
) -> List[SourceHealthRecord]:
    """
    Derives live source health status from the event stream.
    Derivation Rules (L2):
    - Each source has a normal reporting interval I_s (default 60s).
    - Silence duration Delta_t = reference_time - last_seen_time.
    - If Delta_t <= 2 * I_s -> HEALTHY (source active within normal window).
    - If 2 * I_s < Delta_t <= 4 * I_s -> DEGRADED (source is delayed / lagging).
    - If Delta_t > 4 * I_s:
        * Periodic sources -> DOWN (collector outage).
        * Event-driven sources (e.g. CloudWatch alarms, CloudTrail) -> UNKNOWN (never DOWN by silence alone).
    - If a source was never observed in the stream and cannot be derived -> UNKNOWN.
    """
    target_sources = expected_sources or ["cloudwatch", "cloudtrail", "otel", "datadog", "syslog", "application"]
    intervals = source_intervals or {
        "cloudwatch": 60,
        "cloudtrail": 30,
        "otel": 15,
        "datadog": 60,
        "syslog": 60,
        "application": 15
    }

    active_policy = policy or TelemetryPolicy.load_live_policy()

    if not events:
        # Cannot derive anything from empty stream
        now_iso = (reference_time or datetime.now(timezone.utc)).isoformat()
        return [SourceHealthRecord(source=s, timestamp=now_iso, status="UNKNOWN") for s in target_sources]

    def _extract_dt(e: Any) -> datetime:
        raw = getattr(e, "timestamp", None) or getattr(e, "event_time", None)
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw)
        else:
            dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    # Reference time defaults to latest event in stream or provided time
    if reference_time is None:
        ref_dt = max(_extract_dt(e) for e in events)
    else:
        ref_dt = reference_time if reference_time.tzinfo is not None else reference_time.replace(tzinfo=timezone.utc)

    # Index latest timestamp per source
    last_seen: Dict[str, datetime] = {}
    for e in events:
        src = getattr(e, "source", "application").lower()
        e_ts = _extract_dt(e)
        if src not in last_seen or e_ts > last_seen[src]:
            last_seen[src] = e_ts

    records: List[SourceHealthRecord] = []
    for s in target_sources:
        if s not in last_seen:
            # Source never observed in event stream
            records.append(SourceHealthRecord(source=s, timestamp=ref_dt.isoformat(), status="UNKNOWN"))
            continue

        delta_sec = (ref_dt - last_seen[s]).total_seconds()
        interval = intervals.get(s, 60)
        is_ev_driven = active_policy.is_event_driven(s) if active_policy else (s.lower() in ("cloudtrail", "cloudwatch_alarms"))

        if delta_sec < 0:
            status = "HEALTHY"
        elif delta_sec <= 2 * interval:
            status = "HEALTHY"
        elif delta_sec <= 4 * interval:
            status = "DEGRADED"
        else:
            if is_ev_driven:
                status = "UNKNOWN"
            else:
                status = "DOWN"

        records.append(SourceHealthRecord(
            source=s,
            timestamp=ref_dt.isoformat(),
            status=status
        ))

    return records


class LiveIncidentBridge:
    """
    Live bridge connecting live Incidents to rca_v2 and Current Scorer.
    Supports:
    - Shadow Mode (L4): "off", "shadow", "v2"
    - Safe Fallback (L5): timeout and error resilience
    """

    def __init__(
        self,
        config: Optional[RCAv2Config] = None,
        telemetry_policy: Optional[TelemetryPolicy] = None,
        timeout_seconds: float = 2.0
    ):
        self.config = config or RCAv2Config()
        self.policy = telemetry_policy or TelemetryPolicy.load_live_policy()
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        incident: Incident,
        mode: str = "shadow",
        custom_graph_engine: Optional[CausalGraphEngine] = None,
        source_health: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """
        Analyzes an incident under live execution rules:
        - mode="off": runs Current Scorer only.
        - mode="shadow": runs Current Scorer (primary) and rca_v2 (shadow logged next to it).
        - mode="v2": runs rca_v2 (primary) with Current Scorer as safe fallback.
        """
        # Always run Current Scorer as baseline / fallback
        cs_result = analyze_incident(incident, graph_engine=custom_graph_engine)

        if mode == "off":
            return cs_result

        # Adapt events using unified LiveEventAdapter
        cloud_events = LiveEventAdapter.adapt_events(incident.events)
        cg = custom_graph_engine or causal_graph_engine
        
        # Build topology representation for rca_v2
        topo_nodes = [
            {"id": n, "type": cg.graph.nodes[n].get("type", "service")}
            for n in cg.graph.nodes
        ]
        topo_edges = list(cg.graph.edges)
        topology_obj = type("LiveTopology", (), {"nodes": topo_nodes, "edges": topo_edges})()

        # Derive live source health from event stream if not explicitly provided
        effective_source_health = source_health if source_health is not None else derive_live_source_health(cloud_events, policy=self.policy)

        # Execute rca_v2 with timeout and exception guard (L5)
        v2_result: Optional[DiagnosisResult] = None
        error_msg: Optional[str] = None
        t0 = time.perf_counter()

        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    diagnose,
                    events=cloud_events,
                    topology=topology_obj,
                    source_health=effective_source_health,
                    telemetry_policy=self.policy,
                    config=self.config
                )
                v2_result = future.result(timeout=self.timeout_seconds)
            elapsed = time.perf_counter() - t0
        except concurrent.futures.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.warning(f"rca_v2 exceeded timeout limit ({elapsed:.3f}s > {self.timeout_seconds}s); falling back to Current Scorer")
            v2_result = None
            error_msg = f"TimeoutExceeded: {elapsed:.3f}s"
        except Exception as ex:
            logger.error(f"rca_v2 execution failed with exception: {ex}; falling back to Current Scorer", exc_info=True)
            v2_result = None
            error_msg = str(ex)

        # Merge results based on mode
        cs_top = cs_result.get("probable_root_cause")
        cs_ranking = [c["service"] for c in cs_result.get("all_candidate_scores", [])]

        if mode == "shadow":
            # Current Scorer drives the primary behavior; rca_v2 results are logged/stored next to it
            output = dict(cs_result)
            if v2_result is not None:
                v2_top = v2_result.top_candidate.service if v2_result.top_candidate else None
                v2_ranking = v2_result.predicted_root_causes
                agreement = (v2_top == cs_top) if v2_top and cs_top else False

                output["rca_shadow"] = {
                    "mode": "shadow",
                    "agreement": agreement,
                    "rca_v2_verdict": v2_result.verdict.value,
                    "rca_v2_top1": v2_top,
                    "rca_v2_ranking": v2_ranking,
                    "current_scorer_ranking": cs_ranking,
                    "coverage": v2_result.coverage,
                    "margin": v2_result.margin,
                    "contradictions": [c.model_dump() for c in v2_result.contradictions],
                    "explanation": v2_result.explanation.model_dump()
                }
            else:
                output["rca_shadow"] = {
                    "mode": "shadow",
                    "fallback_triggered": True,
                    "error": error_msg,
                    "current_scorer_ranking": cs_ranking
                }
            return output

        elif mode == "v2":
            if v2_result is not None:
                v2_top = v2_result.top_candidate.service if v2_result.top_candidate else None
                output = dict(cs_result)
                output["probable_root_cause"] = v2_top
                output["confidence"] = v2_result.top_candidate.ranking_score if v2_result.top_candidate else 0.0
                output["rca_v2_verdict"] = v2_result.verdict.value
                output["rca_v2_mode"] = "v2"
                output["explanation"] = v2_result.explanation.model_dump()
                output["current_scorer_fallback"] = False
                return output
            else:
                # Fallback to Current Scorer
                output = dict(cs_result)
                output["rca_v2_mode"] = "v2_fallback"
                output["rca_v2_error"] = error_msg
                output["current_scorer_fallback"] = True
                return output

        return cs_result
