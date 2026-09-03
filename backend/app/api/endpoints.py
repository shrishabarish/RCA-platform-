from fastapi import APIRouter, HTTPException, Query, status
from typing import List, Dict, Any, Optional
from backend.app.models.event import (
    CloudEvent,
    Incident,
    RemediationPlan,
    TopologyGraph
)
from backend.app.services.incident_service import incident_service
from rca_engine.causal_graph import causal_graph_engine
from event_processor.aws_collector import aws_collector

router = APIRouter()


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
# 2. SCENARIO SIMULATION & TOPOLOGY
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
