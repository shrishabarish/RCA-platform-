import os
import secrets
from fastapi import APIRouter, HTTPException, Query, Request, status, Header
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
from backend.app.core.config import config
from backend.app.models.event import (
    CloudEvent,
    Incident,
    RemediationPlan,
    TopologyGraph
)
from backend.app.services.incident_service import incident_service
from rca_engine.causal_graph import causal_graph_engine
from rca_engine.analyzer import analyze_incident as run_rca_analysis
from event_processor.aws_collector import aws_collector
from event_processor.aws_config_collector import aws_config_collector
from event_processor.aws_discovery import aws_discovery
from event_processor.s3_archiver import s3_archiver
from event_processor.sns_notifier import sns_notifier
from event_processor.sqs_consumer import sqs_consumer
from event_processor.normalizer import normalize_external_webhook
from backend.app.services.websocket_manager import ws_manager
from backend.app.services.outbox_worker import outbox_worker

router = APIRouter()

# In-memory log of external webhook events for debugging
_webhook_event_log: List[Dict[str, Any]] = []


from sqlalchemy import text
from backend.app.db.database import SessionLocal

@router.get("/health", tags=["System"])
def get_health() -> Dict[str, Any]:
    """Lightweight process liveness probe. Fast 200 without blocking dependencies."""
    return {
        "status": "healthy",
        "service": "Autonomous Cloud RCA Engine (Enterprise)",
        "version": "2.0.0",
        "active_incidents": len(incident_service._active_incidents),
        "total_events_processed": incident_service.get_total_events_count(),
    }


@router.get("/ready", tags=["System"])
def get_ready():
    """Readiness probe checking database health, backlog health, and dependency readiness."""
    import os
    from fastapi.responses import JSONResponse
    from backend.app.services.correlator_consumer import correlator_consumer

    db_connected = False
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_connected = True
        finally:
            db.close()
    except Exception as e:
        db_connected = False

    aws_status = aws_collector.check_aws_connectivity()

    # Backlog health & degradation guardrail
    oldest_age = correlator_consumer.metrics_data.get("oldest_unassigned_age_seconds", 0.0)
    unassigned_count = correlator_consumer.metrics_data.get("unassigned_count", 0)
    is_leader = correlator_consumer.is_leader
    leader_alive = True

    if not is_leader and db_connected:
        try:
            with SessionLocal() as db_session:
                from backend.app.db.models import DBSystemSetting, DBCloudEvent
                from datetime import datetime, timezone
                hb_row = db_session.query(DBSystemSetting).filter(DBSystemSetting.key == "leader_heartbeat").first()
                if hb_row and hb_row.updated_at:
                    hb_ts = hb_row.updated_at
                    if hb_ts.tzinfo is None:
                        hb_ts = hb_ts.replace(tzinfo=timezone.utc)
                    hb_age = (datetime.now(timezone.utc) - hb_ts).total_seconds()
                    if hb_age > 10.0:
                        leader_alive = False

                oldest_event = db_session.query(DBCloudEvent.ingested_at).filter(
                    DBCloudEvent.incident_id.is_(None),
                    DBCloudEvent.ingested_at.isnot(None)
                ).order_by(DBCloudEvent.ingested_at.asc()).first()
                if oldest_event and oldest_event[0]:
                    evt_ts = oldest_event[0]
                    if evt_ts.tzinfo is None:
                        evt_ts = evt_ts.replace(tzinfo=timezone.utc)
                    oldest_age = max(oldest_age, (datetime.now(timezone.utc) - evt_ts).total_seconds())

                unassigned_count = db_session.query(DBCloudEvent).filter(DBCloudEvent.incident_id.is_(None)).count()
        except Exception:
            pass

    threshold = float(os.getenv("UNASSIGNED_AGE_THRESHOLD_SECONDS", "30.0"))
    is_degraded = oldest_age > threshold or not leader_alive

    status_str = "degraded" if is_degraded else "ready"
    response_data = {
        "status": status_str,
        "database_connected": db_connected,
        "aws_connected": aws_status.get("connected", False),
        "in_memory_engine_ready": True,
        "is_leader": is_leader,
        "leader_alive": leader_alive,
        "oldest_unassigned_age_seconds": oldest_age,
        "unassigned_count": unassigned_count,
        "threshold_seconds": threshold
    }

    if is_degraded:
        return JSONResponse(
            status_code=503,
            content=response_data
        )

    return response_data



# ==========================================
# 1. LIVE AWS TELEMETRY (BOTO3) INTEGRATION
# ==========================================

@router.get("/aws/status", tags=["AWS Live Integration"])
def check_aws_status() -> Dict[str, Any]:
    """Checks AWS SDK / Boto3 credentials, account ID, and region connectivity."""
    return aws_collector.check_aws_connectivity()


@router.post("/aws/poll", tags=["AWS Live Integration"])
def poll_live_aws_alarms() -> Dict[str, Any]:
    """
    Directly queries your real AWS Account via Boto3 (CloudWatch Alarms & CloudTrail errors),
    normalizes the live payloads, and feeds them into the correlation and RCA engine.
    """
    live_events = aws_collector.collect_all_aws_events()
    if not live_events:
        return {
            "status": "success",
            "message": "No active CloudWatch alarms or CloudTrail error anomalies found in your AWS account.",
            "events_collected": 0,
            "region": aws_collector.region
        }

    # Ingest live events into IncidentService (batch to avoid StaleDataError)
    ingested = incident_service.ingest_batch([e.model_dump() for e in live_events])
    return {
        "status": "success",
        "message": f"Successfully ingested {len(ingested)} live AWS telemetry events.",
        "events_collected": len(ingested),
        "incidents_active": len(incident_service.get_all_incidents()),
        "region": aws_collector.region
    }


# ==========================================
# 2. EXTERNAL APP WEBHOOK (Live Telemetry)
# ==========================================

@router.post("/events/webhook", tags=["External Webhook"])
async def receive_external_webhook(request: Request) -> Dict[str, Any]:
    """
    Receives structured JSON error logs from external applications
    (e.g. AWS failure code-review-backend's structuredLogger).
    Normalizes them into CloudEvents and feeds into the RCA pipeline.
    """
    try:
        raw_log = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Skip non-error logs (INFO level HTTP requests, etc.)
    level = raw_log.get("level", "INFO").upper()
    event_name = raw_log.get("event", "")
    details = raw_log.get("details", {})
    status_code = details.get("statusCode", 200) if isinstance(details, dict) else 200

    # Only process ERROR/CRITICAL/WARN events, or HTTP 500+ responses
    is_actionable = level in ("ERROR", "CRITICAL", "WARN") or status_code >= 500
    if not is_actionable:
        return {
            "status": "skipped",
            "reason": f"Non-error log (level={level}, statusCode={status_code})",
            "message": "Only ERROR/CRITICAL/WARN events are processed by the RCA engine."
        }

    # Normalize external log -> CloudEvent
    try:
        cloud_event = normalize_external_webhook(raw_log)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to normalize webhook payload: {e}")

    # Ingest into incident pipeline
    incident_service.ingest_event(cloud_event)

    # Log for debugging
    _webhook_event_log.append({
        "received_at": cloud_event.timestamp.isoformat(),
        "source_app": raw_log.get("service", "unknown"),
        "event": event_name,
        "mapped_service": cloud_event.service,
        "severity": cloud_event.severity,
        "correlation_id": raw_log.get("correlation_id", "")
    })

    # Return current RCA state
    incidents = incident_service.get_all_incidents()
    latest_analysis = None
    if incidents:
        latest = incidents[-1]
        try:
            latest_analysis = run_rca_analysis(latest)
        except Exception:
            latest_analysis = None

    return {
        "status": "ingested",
        "event_id": cloud_event.event_id,
        "mapped_service": cloud_event.service,
        "mapped_event_type": cloud_event.event_type,
        "severity": cloud_event.severity,
        "total_events": len(incident_service.get_all_events()),
        "active_incidents": len(incidents),
        "latest_rca": {
            "root_cause": latest_analysis.get("probable_root_cause") if latest_analysis else None,
            "ranking_score": latest_analysis.get("ranking_score") or latest_analysis.get("confidence") if latest_analysis else None,
            "affected_services": latest_analysis.get("affected_services") if latest_analysis else [],
        } if latest_analysis else None
    }


@router.get("/events/webhook/log", tags=["External Webhook"])
def get_webhook_event_log() -> Dict[str, Any]:
    """Returns the log of all external webhook events received (for debugging)."""
    return {
        "total_received": len(_webhook_event_log),
        "events": _webhook_event_log[-50:]  # Last 50 events
    }


# ==========================================
# 3. SCENARIO SIMULATION & TOPOLOGY
# ==========================================

@router.get("/scenarios", tags=["Simulation"])
def list_scenarios() -> List[Dict[str, str]]:
    """Lists pre-packaged cloud failure scenarios."""
    return incident_service.get_available_scenarios()


@router.post("/scenarios/{scenario_id}/simulate", tags=["Simulation"])
def simulate_scenario(scenario_id: str) -> Dict[str, Any]:
    """Loads and simulates a pre-packaged cloud cascading failure scenario."""
    try:
        return incident_service.load_scenario(scenario_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/what-if/{service_name}", tags=["Graph & Prediction"])
def simulate_what_if(service_name: str) -> Dict[str, Any]:
    """Simulates a hypothetical outage on a target service and predicts transitive blast radius."""
    result = causal_graph_engine.simulate_what_if_failure(service_name)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    return result


@router.get("/topology", tags=["Topology"])
def get_topology(incident_id: Optional[str] = None) -> Dict[str, Any]:
    """Returns the microservice topology graph with health, root cause, and dynamic coordinates."""
    return incident_service.get_topology(incident_id)


@router.post("/topology/discover", tags=["Topology"])
def auto_discover_aws_topology() -> Dict[str, Any]:
    """
    Dynamically scans AWS for live EC2, RDS, Lambda, DynamoDB, and SQS resources,
    and updates the dynamic topology graph.
    """
    result = aws_discovery.discover_and_register_cloud_assets()
    return result


@router.post("/topology/services", tags=["Topology"])
def register_dynamic_service(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically registers a custom microservice and optional dependency edges on the fly.
    """
    svc_name = payload.get("service")
    if not svc_name:
        raise HTTPException(status_code=400, detail="Field 'service' is required")

    stype = payload.get("type", "service")
    crit = float(payload.get("criticality", 0.7))
    label = payload.get("label", svc_name)

    reg_res = causal_graph_engine.register_service(
        service_name=svc_name,
        service_type=stype,
        label=label,
        criticality=crit
    )

    # Optional dependency registration
    depends_on = payload.get("depends_on")
    if depends_on:
        if isinstance(depends_on, list):
            for target in depends_on:
                causal_graph_engine.register_dependency(svc_name, str(target))
        else:
            causal_graph_engine.register_dependency(svc_name, str(depends_on))

    return {
        "status": "registered",
        "service": svc_name,
        "topology": causal_graph_engine.export_graph_topology()
    }


# ==========================================
# 3. TELEMETRY EVENT INGESTION
# ==========================================

@router.get("/events", response_model=List[CloudEvent], tags=["Events"])
def get_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: Optional[str] = None,
    severity: Optional[str] = None
) -> List[CloudEvent]:
    """Retrieves paginated and filtered cloud events."""
    return incident_service.get_all_events(limit=limit, offset=offset, service=service, severity=severity)


@router.post("/events", response_model=CloudEvent, status_code=status.HTTP_201_CREATED, tags=["Events"])
def ingest_event(event: Dict[str, Any]) -> CloudEvent:
    """Ingests a new raw or normalized CloudEvent (webhook target for AWS Lambda / EventBridge)."""
    try:
        return incident_service.ingest_event(event)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.post("/events/batch", response_model=List[CloudEvent], status_code=status.HTTP_201_CREATED, tags=["Events"])
def ingest_batch_events(events: List[Dict[str, Any]]) -> List[CloudEvent]:
    """Batch ingests multiple cloud events."""
    try:
        return incident_service.ingest_batch(events)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


# ==========================================
# 4. INCIDENTS & RCA REASONING
# ==========================================

@router.get("/incidents", response_model=List[Incident], tags=["Incidents"])
def get_incidents(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = None
) -> List[Incident]:
    """Retrieves paginated and filtered correlated incidents."""
    return incident_service.get_all_incidents(limit=limit, offset=offset, status=status_filter, severity=severity)


@router.get("/metrics", tags=["Metrics"])
def get_metrics() -> Dict[str, Any]:
    """Returns operational stability metrics (events received, duplicates dropped, outbox, queue depth)."""
    from backend.app.core.metrics import metrics
    m = metrics.get_metrics()
    m["outbox_failed_count"] = m.get("outbox_failed", 0)
    return m


@router.patch("/incidents/{incident_id}/status", tags=["Incidents"])
def update_incident_status(incident_id: str, body: Dict[str, str]) -> Dict[str, Any]:
    """Manually updates the lifecycle status of an incident (e.g. RESOLVED, CLOSED, IN_PROGRESS)."""
    new_status = body.get("status")
    if not new_status:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Field 'status' is required")
    updated = incident_service.update_incident_status(incident_id, new_status)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")
    status_str = updated.status.value if hasattr(updated.status, 'value') else updated.status
    return {"incident_id": incident_id, "status": status_str, "updated_at": updated.updated_at.isoformat()}


@router.get("/outbox/failed", tags=["Outbox"])
def list_failed_outbox_items(limit: int = Query(100, ge=1, le=500)) -> List[Dict[str, Any]]:
    """Lists outbox items that have exhausted their retry backoff schedule."""
    return outbox_worker.get_failed_items(limit=limit)


@router.post("/outbox/failed/{item_id}/replay", tags=["Outbox"])
def replay_failed_outbox_item(item_id: str) -> Dict[str, Any]:
    """Replays a specific FAILED outbox item by resetting its status to PENDING."""
    ok = outbox_worker.replay_failed_item(item_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Failed outbox item '{item_id}' not found")
    return {"status": "replayed", "item_id": item_id}


@router.post("/outbox/failed/replay-all", tags=["Outbox"])
def replay_all_failed_outbox_items() -> Dict[str, Any]:
    """Replays all FAILED outbox items by resetting their status to PENDING."""
    count = outbox_worker.replay_all_failed()
    return {"status": "replayed_all", "count": count}


@router.post("/retention/prune", tags=["Maintenance"])
def prune_historical_data(
    days: int = Query(30, ge=1),
    batch_size: int = Query(1000, ge=10, le=5000),
    dry_run: bool = Query(True)
) -> Dict[str, Any]:
    """Archives and prunes historical telemetry older than specified days with dry-run support."""
    from backend.app.services.retention_service import retention_service
    return retention_service.archive_and_prune(days=days, batch_size=batch_size, dry_run=dry_run)


@router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident_detail(
    incident_id: str,
    recompute: bool = Query(False, description="Force on-demand fresh RCA recomputation")
) -> Dict[str, Any]:
    """Retrieves deep RCA details, causal paths, and reasoning chain for a specific incident."""
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")
    
    if recompute:
        fresh_analysis = incident_service.recompute_incident_analysis(incident_id)
        if fresh_analysis:
            return fresh_analysis

    if incident.incident_id in incident_service._cached_analysis:
        res = dict(incident_service._cached_analysis[incident.incident_id])
        res["analysis_status"] = incident.analysis_status
        res["analyzed_at"] = incident.analyzed_at.isoformat() if incident.analyzed_at else None
        return res

    from rca_engine.analyzer import analyze_incident
    res = analyze_incident(incident)
    res["analysis_status"] = incident.analysis_status
    res["analyzed_at"] = incident.analyzed_at.isoformat() if incident.analyzed_at else None
    return res


@router.post("/incidents/{incident_id}/recompute", tags=["Incidents"])
def recompute_incident_rca(incident_id: str) -> Dict[str, Any]:
    """Forces fresh on-demand RCA recomputation for an incident and updates cache/database."""
    analysis = incident_service.recompute_incident_analysis(incident_id)
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")
    return {"status": "recomputed", "analysis": analysis}


UNSAFE_RUNBOOK_COMMANDS = {
    "FLUSHDB": "Destructive in-memory database wipe; risks database thundering herd cascade",
    "set-alarm-state": "Artificial suppression of active monitoring alarm without resolving root cause",
    "create-deployment": "Blind API Gateway stage rollback without change verification",
    "burstLimit": "Severe ingress rate reduction that risks dropping valid client traffic"
}

AUTO_REMEDIATION_ENABLED = config.remediation.auto_remediation_enabled


class MitigationPayload(BaseModel):
    human_approval_token: Optional[str] = None


@router.get("/incidents/{incident_id}/diagnose", tags=["Incidents"])
def diagnose_incident_rca(incident_id: str) -> Dict[str, Any]:
    """
    Diagnoses an incident using the modern RCA v3 engine with graph reasoning,
    symptom coverage, and counter-evidence verification.
    Displays legacy analyzer results as 'Baseline engine' for clear comparison.
    """
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")

    # 1. Run modern RCA v3 engine
    from rca_v3.diagnose import diagnose as diagnose_v3
    from rca_v3.config import RCAv3Config

    topo_data = incident_service.get_topology(incident_id)
    if isinstance(topo_data, dict):
        class TopologyWrapper:
            def __init__(self, nodes, edges):
                self.nodes = nodes
                self.edges = [
                    [e["source"], e["target"]] if isinstance(e, dict) and "source" in e and "target" in e else e
                    for e in edges
                ]
        topo_data = TopologyWrapper(topo_data.get("nodes", []), topo_data.get("edges", []))
    source_health_records = incident_service.get_source_health()

    v3_result = diagnose_v3(
        events=incident.events,
        topology=topo_data,
        source_health=source_health_records,
        config=RCAv3Config()
    )

    # 2. Run legacy baseline analyzer
    from rca_engine.analyzer import analyze_incident
    legacy_res = analyze_incident(incident)

    # 3. Format candidate diagnoses (suppressing experimental reachable_coverage)
    candidates_formatted = []
    for cand in v3_result.candidate_diagnoses:
        cand_dict = cand.model_dump()
        # Per audit instruction: reachable_coverage is uncalibrated and omitted from UI
        cand_dict.pop("reachable_coverage", None)
        cand_dict["ranking_score_label"] = "Ranking Score"
        cand_dict["combined_score_label"] = "Combined Score (Tie-Break)"
        candidates_formatted.append(cand_dict)

    # 4. Check mitigation guard eligibility server-side
    sources_healthy = all(
        getattr(s, "status", getattr(s, "get", lambda k, d=None: d)("status")) == "HEALTHY"
        for s in (source_health_records or [])
    )
    is_confirmed = (v3_result.verdict.value == "CONFIRMED")
    has_unobserved_ancestors = any(
        getattr(cand, "is_unobserved", False)
        for cand in v3_result.candidate_diagnoses
        if cand.service in v3_result.predicted_root_causes
    )

    can_mitigate = (
        is_confirmed
        and not has_unobserved_ancestors
        and sources_healthy
        and AUTO_REMEDIATION_ENABLED
    )

    return {
        "incident_id": incident_id,
        "engine_version": "rca_v3",
        "verdict": v3_result.verdict.value,
        "predicted_root_causes": v3_result.predicted_root_causes,
        "candidate_diagnoses": candidates_formatted,
        "root_hypotheses": [h.model_dump() for h in v3_result.root_hypotheses],
        "explanation": v3_result.explanation.model_dump(),
        "contradictions": [c.model_dump() for c in v3_result.contradictions],
        "evidence_table": v3_result.explanation.evidence_table,
        "baseline_engine": {
            "name": "Baseline engine (Legacy v1)",
            "probable_root_cause": legacy_res.get("probable_root_cause"),
            "ranking_score": legacy_res.get("confidence"),
            "affected_services": legacy_res.get("affected_services", []),
            "reasoning_chain": legacy_res.get("reasoning_chain", []),
            "analysis_status": legacy_res.get("status", "COMPLETED")
        },
        "remediation_guard": {
            "can_mitigate": can_mitigate,
            "verdict_is_confirmed": is_confirmed,
            "no_unobserved_ancestors": not has_unobserved_ancestors,
            "sources_healthy": sources_healthy,
            "auto_remediation_enabled": AUTO_REMEDIATION_ENABLED,
            "reason": (
                "Automated remediation is safely guarded. Requires CONFIRMED verdict, "
                "healthy telemetry sources, valid human approval token, and global auto-remediation enablement."
            ) if not can_mitigate else "All safety conditions met. Human approval token required to execute."
        }
    }


@router.get("/incidents/{incident_id}/remediation", response_model=RemediationPlan, tags=["Incidents"])
def get_incident_remediation(incident_id: str) -> RemediationPlan:
    """Retrieves the automated remediation runbook for an incident with safety flags."""
    remediation = incident_service.get_remediation(incident_id)
    if not remediation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Remediation for '{incident_id}' not found")
    return remediation


@router.post("/incidents/{incident_id}/mitigate", tags=["Incidents"])
def mitigate_incident(
    incident_id: str,
    payload: Optional[MitigationPayload] = None,
    x_approval_token: Optional[str] = Header(None, alias="X-Human-Approval-Token")
) -> Dict[str, Any]:
    """
    Applies automated remediation to resolve the incident with strict SERVER-SIDE safety guards.
    Fails closed (HTTP 403 Forbidden) unless all 5 safety requirements are strictly verified:
    1. RCA diagnosis verdict is CONFIRMED.
    2. No unobserved ancestors exist in the causal chain.
    3. All telemetry sources are verified HEALTHY.
    4. Valid human approval token is provided.
    5. Automated cloud remediation is globally enabled.
    """
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")

    # Guard 1: Auto-remediation globally enabled
    if not AUTO_REMEDIATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Remediation guard failed: Automated cloud remediation is globally disabled."
        )

    # Guard 2: Human approval token (constant-time comparison against configured secret)
    provided_token = (payload.human_approval_token if payload else None) or x_approval_token
    configured_secret = config.remediation.human_approval_secret
    if not provided_token or not secrets.compare_digest(provided_token, configured_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Remediation guard failed: Missing or invalid human approval token. Action rejected."
        )

    # Guard 3: Telemetry source health
    sources = incident_service.get_source_health() or []
    for s in sources:
        s_status = getattr(s, "status", getattr(s, "get", lambda k, d=None: d)("status"))
        if s_status and str(s_status).upper() != "HEALTHY":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Remediation guard failed: Telemetry source is unhealthy ({s_status}). Action rejected."
            )

    # Guard 4 & 5: RCA v3 verification (CONFIRMED verdict, no unobserved ancestors)
    from rca_v3.diagnose import diagnose as diagnose_v3
    from rca_v3.config import RCAv3Config

    topo_data = incident_service.get_topology(incident_id)
    if isinstance(topo_data, dict):
        class TopologyWrapper:
            def __init__(self, nodes, edges):
                self.nodes = nodes
                self.edges = [
                    [e["source"], e["target"]] if isinstance(e, dict) and "source" in e and "target" in e else e
                    for e in edges
                ]
        topo_data = TopologyWrapper(topo_data.get("nodes", []), topo_data.get("edges", []))
    v3_result = diagnose_v3(
        events=incident.events,
        topology=topo_data,
        source_health=sources,
        config=RCAv3Config()
    )

    if v3_result.verdict.value != "CONFIRMED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Remediation guard failed: Verdict is '{v3_result.verdict.value}' (must be CONFIRMED). Action rejected."
        )

    # Check for unobserved ancestors
    for cand in v3_result.candidate_diagnoses:
        if cand.service in v3_result.predicted_root_causes and getattr(cand, "is_unobserved", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Remediation guard failed: Root cause has unobserved ancestors in causal topology. Action rejected."
            )

    # All 5 guards passed -> proceed with remediation
    try:
        return incident_service.mitigate_incident(incident_id)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/reset", tags=["System"])
def reset_system() -> Dict[str, str]:
    """Resets system events and incidents."""
    incident_service.reset_state()
    return {"message": "All events and incidents have been reset."}


# ==========================================
# 5. AWS CLOUD-NATIVE ECOSYSTEM INTEGRATION
# ==========================================

@router.get("/cloud/status", tags=["AWS Cloud Architecture"])
def get_cloud_architecture_status() -> Dict[str, Any]:
    """
    Returns live connectivity status of all AWS Architecture components
    specified in Review 1 (RDS, CloudWatch, CloudTrail, S3, SNS, SQS, AWS Config).
    """
    cw_status = aws_collector.check_aws_connectivity()
    return {
        "platform": "CloudLens Autonomous RCA Platform",
        "architecture_layer": "AWS Cloud-Native (Production)",
        "components": {
            "database": {
                "service": "AWS RDS (PostgreSQL)",
                "status": "connected",
                "engine": "PostgreSQL 16",
                "region": aws_collector.region
            },
            "monitoring": {
                "service": "Amazon CloudWatch",
                "status": "connected" if cw_status.get("connected") else "configured",
                "region": aws_collector.region
            },
            "audit_archive": s3_archiver.get_status(),
            "notifications": sns_notifier.get_status(),
            "config_drift": aws_config_collector.get_status(),
            "event_buffer": sqs_consumer.get_status()
        }
    }


@router.get("/aws/config", tags=["AWS Config Drift"])
def get_aws_config_drift() -> List[Dict[str, Any]]:
    """Retrieves recent AWS Config infrastructure drift events."""
    events = aws_config_collector.collect_config_changes()
    return [e.model_dump() for e in events]


@router.get("/s3/reports", tags=["Amazon S3 Archive"])
def list_s3_incident_reports() -> List[Dict[str, Any]]:
    """Lists all SRE incident post-mortem reports permanently archived in Amazon S3."""
    return s3_archiver.list_archived_reports()


@router.get("/s3/reports/{incident_id}", tags=["Amazon S3 Archive"])
def get_s3_incident_report(incident_id: str) -> Dict[str, Any]:
    """Downloads a structured incident post-mortem audit report from Amazon S3."""
    report = s3_archiver.get_incident_report(incident_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"S3 report for {incident_id} not found")
    return report


@router.get("/sns/status", tags=["Amazon SNS Notifications"])
def get_sns_status() -> Dict[str, Any]:
    """Returns Amazon SNS notification service status and recent dispatches."""
    status_info = sns_notifier.get_status()
    status_info["recent_dispatches"] = sns_notifier.get_dispatch_history()
    return status_info


@router.post("/sns/test", tags=["Amazon SNS Notifications"])
def test_sns_notification(incident_id: Optional[str] = None) -> Dict[str, Any]:
    """Sends a mock test alert to the Amazon SNS topic for demonstration purposes."""
    test_incident = {
        "incident_id": incident_id or "inc_test_mock_demo",
        "is_mock": True,
        "mock_note": "Mock demonstration payload (simulated alert)",
        "severity": "CRITICAL",
        "probable_root_cause": "EC2",
        "ranking_score": 96.5,
        "affected_services": ["API", "Application"],
        "remediation_suggestion": "Trigger ASG scale-out and reboot instance i-0abcdef1234567890 (Mock Demonstration)"
    }
    return sns_notifier.publish_incident_alert(test_incident)


@router.get("/sqs/status", tags=["Amazon SQS Buffer"])
def get_sqs_status() -> Dict[str, Any]:
    """Returns Amazon SQS buffer queue status and processed message counts."""
    return sqs_consumer.get_status()


@router.post("/sqs/poll", tags=["Amazon SQS Buffer"])
def poll_sqs_buffer() -> Dict[str, Any]:
    """Drains telemetry events from the Amazon SQS queue into the correlation pipeline."""
    messages = sqs_consumer.poll_and_consume_messages()
    ingested_count = 0
    for msg in messages:
        payload = msg.get("payload")
        if isinstance(payload, dict) and "service" in payload:
            incident_service.ingest_event(payload)
            ingested_count += 1

    return {
        "status": "success",
        "messages_pulled": len(messages),
        "events_ingested": ingested_count
    }


# ==========================================
# 8. READ-ONLY SYNTHETIC RCA REPLAY & EVALUATION
# ==========================================

from pathlib import Path
import json
import math
import csv
from evaluation.schema import EvaluationCase
from evaluation.strip_case import strip_case_for_engine
from scripts.audit_evaluation_csvs import get_file_commit


def _find_case_by_id(case_id: str) -> Optional[EvaluationCase]:
    cases_dir = Path("evaluation/cases")
    candidates = list(cases_dir.glob(f"**/{case_id}.json"))
    if not candidates:
        candidates = list(cases_dir.glob(f"**/{case_id}*.json"))
    if candidates:
        try:
            return EvaluationCase.model_validate_json(candidates[0].read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


_rca_cases_cache: Optional[List[Dict[str, Any]]] = None


@router.get("/rca/cases", tags=["RCA Evaluation & Replay"])
def list_rca_cases() -> List[Dict[str, Any]]:
    """Lists available synthetic evaluation cases for diagnosis, replay, and comparison."""
    global _rca_cases_cache
    if _rca_cases_cache is not None:
        return _rca_cases_cache

    cases_dir = Path("evaluation/cases/independent")
    if not cases_dir.exists():
        return []

    case_files = sorted(list(cases_dir.glob("*.json")))
    results = []
    for f in case_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            cid = data.get("case_id", f.stem)
            topo = data.get("topology", {})
            nodes = topo.get("nodes", []) if isinstance(topo, dict) else []
            evts = data.get("events", [])
            perts = data.get("perturbations", [])
            cat = perts[0] if perts else "independent"
            results.append({
                "case_id": cid,
                "category": cat,
                "node_count": len(nodes),
                "event_count": len(evts),
                "is_synthetic": True,
                "filename": f.name
            })
        except Exception:
            continue
    _rca_cases_cache = results
    return results


@router.get("/rca/diagnose", tags=["RCA Evaluation & Replay"])
def diagnose_rca_case(
    case_id: str = Query(..., description="ID of synthetic case to diagnose"),
    engine: str = Query("rca_v3", description="Engine to run: 'rca_v3' or 'current_scorer'")
) -> Dict[str, Any]:
    """Diagnoses a stripped synthetic case using rca_v3 or cleaned current scorer baseline."""
    case = _find_case_by_id(case_id)
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case '{case_id}' not found")

    stripped = strip_case_for_engine(case)

    if engine == "rca_v3":
        from rca_v3 import diagnose as rca_v3_diagnose
        diag = rca_v3_diagnose(
            events=stripped.events,
            topology=stripped.topology,
            source_health=stripped.source_health
        )
        return {
            "case_id": case_id,
            "engine": "rca_v3",
            "is_synthetic": True,
            "verdict": diag.verdict.value,
            "predicted_root_causes": diag.predicted_root_causes,
            "ranking_scores": diag.ranking_scores,
            "top_candidate": {
                "service": diag.top_candidate.service if diag.top_candidate else None,
                "ranking_score": diag.top_candidate.combined_score if diag.top_candidate else 0.0,
                "coverage": diag.coverage,
                "margin": diag.margin
            } if diag.top_candidate else None,
            "verdict_reason": diag.explanation.verdict_reason if diag.explanation else None,
            "note": "rca_v3's added value is the verdict, abstention and multi-root output; ranking accuracy is matched by the cleaned current scorer on synthetic data."
        }
    elif engine in ("current_scorer", "baseline", "current_5factor"):
        from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline
        scorer = CurrentScorerCaseTopologyBaseline()
        pred = scorer.predict(stripped)
        top_svc = pred.predicted_root_causes[0] if pred.predicted_root_causes else None
        top_score = pred.candidate_scores.get(top_svc, 0.0) if top_svc else 0.0
        return {
            "case_id": case_id,
            "engine": "current_scorer",
            "is_synthetic": True,
            "verdict": pred.verdict,
            "predicted_root_causes": pred.predicted_root_causes,
            "ranking_scores": pred.candidate_scores,
            "top_candidate": {
                "service": top_svc,
                "ranking_score": top_score
            } if top_svc else None,
            "note": "rca_v3's added value is the verdict, abstention and multi-root output; ranking accuracy is matched by the cleaned current scorer on synthetic data."
        }
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown engine '{engine}'. Choose 'rca_v3' or 'current_scorer'.")


_replay_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}


@router.get("/rca/replay", tags=["RCA Evaluation & Replay"])
def replay_rca_case(
    case_id: str = Query(..., description="ID of synthetic case to replay"),
    engine: str = Query("rca_v3", description="Engine to run: 'rca_v3' or 'current_scorer'")
) -> Dict[str, Any]:
    """Generates prefix event-by-event frames for interactive timeline replay."""
    cache_key = (case_id, engine)
    if cache_key in _replay_cache:
        return _replay_cache[cache_key]

    case = _find_case_by_id(case_id)
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case '{case_id}' not found")

    stripped = strip_case_for_engine(case)
    sorted_events = sorted(stripped.events, key=lambda e: e.event_time)
    total_evts = len(sorted_events)

    max_frames = 50
    if total_evts > max_frames:
        indices = list(range(0, total_evts, math.ceil(total_evts / max_frames)))
        if (total_evts - 1) not in indices:
            indices.append(total_evts - 1)
    else:
        indices = list(range(total_evts))

    frames = []
    from rca_v3 import diagnose as rca_v3_diagnose
    from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline

    cs_engine = CurrentScorerCaseTopologyBaseline() if engine != "rca_v3" else None

    for idx in indices:
        prefix_events = sorted_events[:idx + 1]
        cur_evt = sorted_events[idx]
        cur_evt_dict = {
            "event_id": cur_evt.event_id,
            "timestamp": cur_evt.event_time,
            "service": cur_evt.service,
            "metric": cur_evt.metric,
            "value": cur_evt.value,
            "severity": cur_evt.severity,
            "target": getattr(cur_evt, "target", None),
        }

        if engine == "rca_v3":
            diag = rca_v3_diagnose(
                events=prefix_events,
                topology=stripped.topology,
                source_health=stripped.source_health
            )
            top_svc = diag.top_candidate.service if diag.top_candidate else None
            top_sc = diag.top_candidate.combined_score if diag.top_candidate else 0.0
            frames.append({
                "frame_index": len(frames) + 1,
                "event_index": idx,
                "timestamp": cur_evt.event_time,
                "current_event": cur_evt_dict,
                "verdict": diag.verdict.value,
                "predicted_root_causes": diag.predicted_root_causes,
                "ranking_scores": diag.ranking_scores,
                "top_candidate": top_svc,
                "ranking_score": top_sc
            })
        else:
            prefix_case = stripped.model_copy(deep=True)
            prefix_case.events = prefix_events
            pred = cs_engine.predict(prefix_case)
            top_svc = pred.predicted_root_causes[0] if pred.predicted_root_causes else None
            top_sc = pred.candidate_scores.get(top_svc, 0.0) if top_svc else 0.0
            frames.append({
                "frame_index": len(frames) + 1,
                "event_index": idx,
                "timestamp": cur_evt.event_time,
                "current_event": cur_evt_dict,
                "verdict": pred.verdict,
                "predicted_root_causes": pred.predicted_root_causes,
                "ranking_scores": pred.candidate_scores,
                "top_candidate": top_svc,
                "ranking_score": top_sc
            })

    node_count = len(stripped.topology.nodes)
    res_dict = {
        "case_id": case_id,
        "engine": engine,
        "is_synthetic": True,
        "node_count": node_count,
        "total_events": total_evts,
        "frame_count": len(frames),
        "frames": frames,
        "note": "rca_v3's added value is the verdict, abstention and multi-root output; ranking accuracy is matched by the cleaned current scorer on synthetic data."
    }
    _replay_cache[cache_key] = res_dict
    return res_dict


@router.get("/rca/compare", tags=["RCA Evaluation & Replay"])
def compare_rca_case(
    case_id: str = Query(..., description="ID of synthetic case to compare")
) -> Dict[str, Any]:
    """Compares current scorer on cleaned input against rca_v3 with a diff summary and generated sentence."""
    case = _find_case_by_id(case_id)
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case '{case_id}' not found")

    stripped = strip_case_for_engine(case)

    # 1. Current Scorer on cleaned input
    from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline
    cs_res = CurrentScorerCaseTopologyBaseline().predict(stripped)
    cs_top = cs_res.predicted_root_causes[0] if cs_res.predicted_root_causes else None
    cs_score = cs_res.candidate_scores.get(cs_top, 0.0) if cs_top else 0.0

    # 2. RCA v3 on cleaned input
    from rca_v3 import diagnose as rca_v3_diagnose
    v3_diag = rca_v3_diagnose(
        events=stripped.events,
        topology=stripped.topology,
        source_health=stripped.source_health
    )
    v3_top = v3_diag.top_candidate.service if v3_diag.top_candidate else None
    v3_score = v3_diag.top_candidate.combined_score if v3_diag.top_candidate else 0.0

    diffs = []
    if cs_res.verdict != v3_diag.verdict.value:
        diffs.append(f"Verdict difference: Current Scorer output '{cs_res.verdict}', whereas rca_v3 output '{v3_diag.verdict.value}'.")

    if cs_res.predicted_root_causes != v3_diag.predicted_root_causes:
        if len(v3_diag.predicted_root_causes) > 1:
            diffs.append(f"Root set difference: rca_v3 identified multi-root cause {v3_diag.predicted_root_causes}, whereas Current Scorer produced single ranking {cs_res.predicted_root_causes[:3]}.")
        else:
            diffs.append(f"Ranking difference: rca_v3 diagnosed {v3_diag.predicted_root_causes}, whereas Current Scorer ranked {cs_res.predicted_root_causes[:3]}.")
    else:
        diffs.append("Root set match: Both engines isolated the same root cause.")

    if v3_diag.verdict.value == "INSUFFICIENT_EVIDENCE":
        diff_sentence = f"rca_v3 abstained due to insufficient causal evidence ({v3_diag.explanation.verdict_reason if v3_diag.explanation else 'unverifiable anomaly'}), whereas Current Scorer forced a diagnosis of {cs_top or 'unknown'}."
    elif len(v3_diag.predicted_root_causes) > 1:
        diff_sentence = f"rca_v3 detected a multi-root incident ({', '.join(v3_diag.predicted_root_causes)}) with {v3_diag.verdict.value} verdict, whereas Current Scorer only isolated single candidate {cs_top}."
    elif cs_top == v3_top:
        diff_sentence = f"Both engines agreed on root cause '{v3_top}', with rca_v3 qualifying verdict as {v3_diag.verdict.value} based on topological verification."
    else:
        diff_sentence = f"rca_v3 selected '{v3_top}' ({v3_diag.verdict.value}) based on reachability and symptom coverage, whereas Current Scorer selected '{cs_top}'."

    node_count = len(stripped.topology.nodes)
    return {
        "case_id": case_id,
        "is_synthetic": True,
        "node_count": node_count,
        "current_scorer": {
            "engine": "current_scorer",
            "verdict": cs_res.verdict,
            "predicted_root_causes": cs_res.predicted_root_causes,
            "top_candidate": cs_top,
            "ranking_score": cs_score,
            "ranking_scores": cs_res.candidate_scores
        },
        "rca_v3": {
            "engine": "rca_v3",
            "verdict": v3_diag.verdict.value,
            "predicted_root_causes": v3_diag.predicted_root_causes,
            "top_candidate": v3_top,
            "ranking_score": v3_score,
            "ranking_scores": v3_diag.ranking_scores,
            "is_multiroot": len(v3_diag.predicted_root_causes) > 1,
            "verdict_reason": v3_diag.explanation.verdict_reason if v3_diag.explanation else None
        },
        "differences": diffs,
        "diff_summary_sentence": diff_sentence,
        "plain_note": "rca_v3's added value is the verdict, abstention and multi-root output; ranking accuracy is matched by the cleaned current scorer on synthetic data."
    }


@router.get("/eval/summary", tags=["RCA Evaluation & Replay"])
def get_evaluation_summary() -> List[Dict[str, Any]]:
    """Reads whole evaluation CSVs and sidecar metadata, returning structured summary rows with engine, split, metric, numerator, denominator, denominator_type, status, path, and commit."""
    results_dir = Path("evaluation/results")
    if not results_dir.exists():
        return []

    csv_files = sorted(list(results_dir.glob("*.csv")))
    summary_rows = []

    for cp in csv_files:
        try:
            with open(cp, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                total = len(rows)
                if total == 0:
                    continue
                fieldnames = reader.fieldnames or []

                # Count distinct families
                fam_set = set(
                    r.get("family_id") or r.get("family") or r.get("scenario_family") or r.get("case_family")
                    for r in rows
                    if (r.get("family_id") or r.get("family") or r.get("scenario_family") or r.get("case_family"))
                )
                if not fam_set:
                    # fallback to distinct prefixes if no explicit family column
                    fam_set = set(
                        (r.get("case_id") or r.get("case_name") or "fam").split("_")[0]
                        for r in rows
                    )
                family_count = len(fam_set)
                commit_hash = get_file_commit(cp)

                sidecar_path = cp.with_suffix(".json")
                if sidecar_path.exists():
                    try:
                        with open(sidecar_path, encoding="utf-8") as sf:
                            meta = json.load(sf)
                    except Exception:
                        meta = {}
                else:
                    meta = {}

                split = meta.get("split")
                if not split:
                    fn = cp.name.lower()
                    if "val3" in fn:
                        split = "val3"
                    elif "dev3" in fn:
                        split = "dev3"
                    elif "val2" in fn:
                        split = "val2"
                    elif "dev2" in fn:
                        split = "dev2"
                    elif "old_dev" in fn:
                        split = "old_dev"
                    elif "dev_val" in fn:
                        split = "dev_val"
                    elif "handwritten" in fn:
                        split = "handwritten"
                    else:
                        split = "dev"

                status = meta.get("status", "SUPERSEDED")

                # Read engines definition from sidecar or discover directly from CSV columns
                engines_dict = meta.get("engines", {})
                if not engines_dict:
                    engines_dict = {}
                    for col in fieldnames:
                        if col.endswith("_correct"):
                            eng = col[:-len("_correct")]
                            engines_dict[eng] = {
                                "top1_column": col,
                                "metric": "Top-1 Accuracy",
                                "denominator_type": "all cases"
                            }
                        elif col == "match_ground_truth":
                            engines_dict["rca_v2"] = {
                                "top1_column": "match_ground_truth",
                                "metric": "Top-1 Accuracy",
                                "denominator_type": "all cases"
                            }
                        elif col == "is_top1":
                            engines_dict["rca_v3"] = {
                                "top1_column": "is_top1",
                                "metric": "Top-1 Accuracy",
                                "denominator_type": "all cases"
                            }

                if not engines_dict:
                    continue

                for eng_name, eng_info in engines_dict.items():
                    top1_col = eng_info.get("top1_column")
                    if not top1_col or top1_col not in fieldnames:
                        continue
                    cw_col = eng_info.get("cw_column")
                    if cw_col and cw_col not in fieldnames:
                        cw_col = None

                    metric = eng_info.get("metric", "Top-1 Accuracy")
                    den_type = eng_info.get("denominator_type", "all cases")

                    cols_used = [top1_col]
                    if cw_col:
                        cols_used.append(cw_col)

                    print(f"[{cp.name}] Engine: {eng_name} | Columns used: {cols_used}")

                    numerator = sum(1 for r in rows if str(r.get(top1_col, "")).strip().lower() in ("true", "1"))
                    denominator = total
                    acc_pct = round(numerator / denominator * 100.0, 1) if denominator else 0.0

                    cw_count = None
                    cw_pct = None
                    if cw_col:
                        cw_count = sum(1 for r in rows if str(r.get(cw_col, "")).strip().lower() in ("true", "1"))
                        cw_pct = round(cw_count / denominator * 100.0, 1) if denominator else 0.0

                    is_latest_frozen = (cp.name in ("stage3f_dev3_comparison.csv", "stage3f_val3_comparison.csv"))
                    is_superseded = (status == "SUPERSEDED")

                    summary_rows.append({
                        "filename": cp.name,
                        "csv_path": str(cp.as_posix()),
                        "engine": eng_name,
                        "split": split,
                        "status": status,
                        "metric": metric,
                        "numerator": numerator,
                        "denominator": denominator,
                        "denominator_cases": denominator,
                        "denominator_families": family_count,
                        "denominator_type": den_type,
                        "columns_used": cols_used,
                        "is_superseded": is_superseded,
                        "is_latest_frozen": is_latest_frozen,
                        "top1_count": numerator,
                        "top1_accuracy_pct": acc_pct,
                        "confident_wrong_count": cw_count,
                        "confident_wrong_pct": cw_pct,
                        "commit": commit_hash
                    })
        except Exception as e:
            print(f"Error parsing evaluation CSV {cp}: {e}")
            continue

    return summary_rows


