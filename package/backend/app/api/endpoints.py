from fastapi import APIRouter, HTTPException, Query, Request, status
from typing import List, Dict, Any, Optional
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
from event_processor.normalizer import normalize_external_webhook

router = APIRouter()

# In-memory log of external webhook events for debugging
_webhook_event_log: List[Dict[str, Any]] = []


@router.get("/health", tags=["System"])
def get_health() -> Dict[str, Any]:
    """System health check and live telemetry summary."""
    return {
        "status": "healthy",
        "service": "Autonomous Cloud RCA Engine (Enterprise)",
        "version": "2.0.0",
        "active_incidents": len(incident_service.get_all_incidents()),
        "total_events_processed": len(incident_service.get_all_events()),
    }


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

    # Ingest live events into IncidentService
    ingested = [incident_service.ingest_event(e) for e in live_events]
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
            "confidence": latest_analysis.get("confidence") if latest_analysis else None,
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
    """Returns the microservice topology graph with health and root cause status."""
    return incident_service.get_topology(incident_id)


# ==========================================
# 3. TELEMETRY EVENT INGESTION
# ==========================================

@router.get("/events", response_model=List[CloudEvent], tags=["Events"])
def get_events() -> List[CloudEvent]:
    """Retrieves all ingested cloud events."""
    return incident_service.get_all_events()


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
def get_incidents() -> List[Incident]:
    """Retrieves all correlated incidents and root cause analyses."""
    return incident_service.get_all_incidents()


@router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident_detail(incident_id: str) -> Dict[str, Any]:
    """Retrieves deep RCA details, causal paths, and reasoning chain for a specific incident."""
    incident = incident_service.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found")
    
    from rca_engine.analyzer import analyze_incident
    return analyze_incident(incident)


@router.get("/incidents/{incident_id}/remediation", response_model=RemediationPlan, tags=["Incidents"])
def get_incident_remediation(incident_id: str) -> RemediationPlan:
    """Retrieves the automated remediation runbook for an incident."""
    remediation = incident_service.get_remediation(incident_id)
    if not remediation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Remediation for '{incident_id}' not found")
    return remediation


@router.post("/incidents/{incident_id}/mitigate", tags=["Incidents"])
def mitigate_incident(incident_id: str) -> Dict[str, Any]:
    """Applies automated remediation to resolve the incident."""
    try:
        return incident_service.mitigate_incident(incident_id)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/reset", tags=["System"])
def reset_system() -> Dict[str, str]:
    """Resets system events and incidents."""
    incident_service.reset_state()
    return {"message": "All events and incidents have been reset."}
