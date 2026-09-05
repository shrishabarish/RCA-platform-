import sys
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Safe UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.app.models.event import CloudEvent, EventSource, Severity, Incident
from rca_engine.analyzer import analyze_incident
from rca_engine.remediation import generate_remediation_plan
from rca_engine.causal_graph import causal_graph_engine
from event_processor.aws_collector import aws_collector


# The 5 Standard AI Fault Scenarios
SCENARIOS = {
    "1": "database_outage",
    "2": "latency_spike",
    "3": "api_error",
    "4": "service_degradation",
    "5": "cloudwatch_breach",
    "database_outage": "database_outage",
    "latency_spike": "latency_spike",
    "api_error": "api_error",
    "service_degradation": "service_degradation",
    "cloudwatch_breach": "cloudwatch_breach",
    "live_aws": "live_aws"
}

def get_fault_payload(scenario: str) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    if scenario == "database_outage":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "DATABASE_DISCONNECT",
            "target_service": "DynamoDB",
            "trigger_source": "manual_simulation",
            "timestamp": now_iso,
            "params": {"dbDisconnect": True, "port": 5432}
        }
    elif scenario == "latency_spike":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "DATABASE_LATENCY_SPIKE",
            "target_service": "DynamoDB",
            "trigger_source": "manual_simulation",
            "timestamp": now_iso,
            "params": {"dbLatencyMs": 4000, "threshold_ms": 2500}
        }
    elif scenario == "api_error":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "ENDPOINT_INTERNAL_ERROR",
            "target_service": "API",
            "trigger_source": "manual_simulation",
            "timestamp": now_iso,
            "params": {"statusCode": 500, "endpoint": "/api/submissions"}
        }
    elif scenario == "service_degradation":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "SERVICE_DEGRADATION",
            "target_service": "EC2",
            "trigger_source": "manual_simulation",
            "timestamp": now_iso,
            "params": {"serviceUnhealthy": True, "health_endpoint": "/health", "returned_code": 503}
        }
    elif scenario == "cloudwatch_breach":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "CLOUDWATCH_METRIC_BREACH",
            "target_service": "EC2",
            "trigger_source": "aws_cloudwatch_alarm",
            "timestamp": now_iso,
            "params": {"metric": "CPUUtilization", "threshold": 95.0, "current_value": 98.5}
        }
    elif scenario == "live_aws":
        return {
            "action": "INJECT_FAULT",
            "fault_type": "LIVE_AWS_ALARM_INJECTION",
            "target_service": "AWS_ACCOUNT",
            "trigger_source": "boto3_cloudwatch_set_alarm_state",
            "timestamp": now_iso,
            "params": {"region": aws_collector.region, "alarms_triggered": 3}
        }
    return {}

def get_telemetry_events(scenario: str, corr_id: str) -> list:
    now = datetime.now(timezone.utc)
    if scenario == "database_outage":
        return [
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="DatabaseConnectionRefused",
                service="DynamoDB",
                resource_id="postgres-primary:5432",
                metric="ConnectionRefusalCount",
                value=1.0,
                severity=Severity.CRITICAL,
                metadata={
                    "correlation_id": corr_id,
                    "endpoint": "/api/submissions",
                    "error_message": "connect ECONNREFUSED 127.0.0.1:5432",
                    "http_status": 500,
                    "active_users_affected": 12,
                    "user_facing_error_rate": "100%"
                }
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="db_connection_pool_timeout",
                service="API",
                resource_id="api-gateway-01",
                metric="ConnectionPoolWaitTimeMs",
                value=5000.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id, "endpoint": "/api/submissions"}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="http_500_internal_error",
                service="Application",
                resource_id="web-ui-frontend",
                metric="5xxErrorRate",
                value=100.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id, "path": "/submissions"}
            )
        ]
    elif scenario == "latency_spike":
        return [
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.CLOUDWATCH,
                event_type="database_query_latency_high",
                service="DynamoDB",
                resource_id="OrdersTable",
                metric="QueryLatencyMs",
                value=4250.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id, "p99_sla_ms": 500}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="upstream_timeout_504",
                service="API",
                resource_id="api-gateway-01",
                metric="Latency",
                value=4500.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id, "endpoint": "/api/orders"}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="ui_render_timeout",
                service="Application",
                resource_id="frontend-web",
                metric="ResponseTimeMs",
                value=5100.0,
                severity=Severity.MEDIUM,
                metadata={"correlation_id": corr_id}
            )
        ]
    elif scenario == "api_error":
        return [
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="http_500_internal_error",
                service="API",
                resource_id="api-gateway-submissions",
                metric="5xxErrorCount",
                value=85.0,
                severity=Severity.CRITICAL,
                metadata={"correlation_id": corr_id, "endpoint": "/api/submissions", "http_status": 500}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="submission_form_failure",
                service="Application",
                resource_id="frontend-web",
                metric="FormSubmissionDropRate",
                value=92.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id}
            )
        ]
    elif scenario == "service_degradation":
        return [
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="health_check_failed",
                service="EC2",
                resource_id="i-0987654321fedcba0",
                metric="HealthCheckStatus",
                value=503.0,
                severity=Severity.CRITICAL,
                metadata={"correlation_id": corr_id, "endpoint": "/health", "http_status": 503}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.CLOUDWATCH,
                event_type="alb_target_unhealthy_hosts",
                service="API",
                resource_id="app-load-balancer",
                metric="UnHealthyHostCount",
                value=4.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="gateway_503_unavailable",
                service="Application",
                resource_id="frontend-web",
                metric="503ErrorRate",
                value=60.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id}
            )
        ]
    elif scenario == "cloudwatch_breach":
        return [
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.CLOUDWATCH,
                event_type="cpu_utilization_high",
                service="EC2",
                resource_id="i-0abcdef1234567890",
                metric="CPUUtilization",
                value=98.5,
                severity=Severity.CRITICAL,
                metadata={"correlation_id": corr_id, "threshold": 95.0, "region": "eu-north-1"}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="api_latency_high",
                service="API",
                resource_id="api-gateway-01",
                metric="Latency",
                value=2800.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id}
            ),
            CloudEvent(
                event_id=f"evt_{uuid.uuid4().hex[:6]}",
                timestamp=now,
                source=EventSource.APPLICATION,
                event_type="http_500_errors",
                service="Application",
                resource_id="web-app-01",
                metric="5xxErrorRate",
                value=15.0,
                severity=Severity.HIGH,
                metadata={"correlation_id": corr_id}
            )
        ]
    return []


def run_test_failure_cycle(scenario_key: str = "database_outage"):
    scenario = SCENARIOS.get(scenario_key, "database_outage")
    corr_id = f"corr-{uuid.uuid4().hex[:6]}"
    inc_id = f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    print("\n" + "="*72)
    print(f"  [*] 3-STEP AI FAILURE & ROOT CAUSE ANALYSIS TEST RUN")
    print(f"  Scenario: {scenario.upper().replace('_', ' ')}")
    print("="*72)

    # -------------------------------------------------------------
    # STEP 1: FAULT INJECTION (The Trigger / Chaos)
    # -------------------------------------------------------------
    print("\n[STEP 1] FAULT INJECTION (The Trigger)")
    print("  Injecting intentional operational disturbance into architecture...")
    fault_payload = get_fault_payload(scenario)
    print(json.dumps(fault_payload, indent=2))

    # -------------------------------------------------------------
    # STEP 2: TELEMETRY & SYMPTOMS (What the System Emits)
    # -------------------------------------------------------------
    print("\n[STEP 2] TELEMETRY & SYMPTOMS (Structured JSON Log Ingestion)")
    print(f"  Emitting correlated structured telemetry across microservices [Correlation ID: {corr_id}]...")
    
    if scenario == "live_aws":
        from event_processor.aws_simulator_injector import create_and_trigger_real_aws_incident
        create_and_trigger_real_aws_incident()
        print("  Querying live AWS account via Boto3 SDK...")
        events = aws_collector.collect_all_aws_events()
        if not events:
            print("  Fallback: generating synthetic AWS alarm events.")
            events = get_telemetry_events("cloudwatch_breach", corr_id)
    else:
        events = get_telemetry_events(scenario, corr_id)

    # Display raw telemetry log of primary symptom
    primary_event = events[0]
    sev_str = primary_event.severity.value if hasattr(primary_event.severity, "value") else str(primary_event.severity)
    telemetry_log = {
        "timestamp": primary_event.timestamp.isoformat(),
        "level": sev_str,
        "service": primary_event.service,
        "request_id": f"req-{uuid.uuid4().hex[:6]}",
        "correlation_id": corr_id,
        "symptom": {
            "metric": primary_event.metric,
            "value": primary_event.value,
            "event_type": primary_event.event_type,
            "metadata": primary_event.metadata
        },
        "total_correlated_events": len(events)
    }
    print(json.dumps(telemetry_log, indent=2))

    # -------------------------------------------------------------
    # STEP 3: ROOT CAUSE ANALYSIS & REMEDIATION (AI Diagnosis)
    # -------------------------------------------------------------
    print("\n[STEP 3] AI ROOT CAUSE ANALYSIS & REMEDIATION (Graph Inference & Causal Reasoning)")
    incident = Incident(
        incident_id=inc_id,
        events=events,
        severity=sev_str,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )

    rca_result = analyze_incident(incident)
    remediation = generate_remediation_plan(inc_id, rca_result["probable_root_cause"], rca_result.get("resource_id"))

    # Construct the AI RCA Diagnostic JSON matching standard enterprise specification
    ai_rca_output = {
        "incident_id": inc_id,
        "confidence_score": round(rca_result["confidence"] / 100.0, 2),
        "root_cause_analysis": {
            "primary_root_cause": rca_result["probable_root_cause"],
            "failure_category": f"INFRASTRUCTURE_{rca_result['probable_root_cause'].upper()}_FAILURE",
            "propagation_path": " -> ".join(rca_result["causal_paths"][0].split(" -> ")) if rca_result["causal_paths"] else f"{rca_result['probable_root_cause']} -> Affected Services",
            "downstream_casualties": rca_result["affected_services"],
            "score_breakdown": rca_result["score_breakdown"]
        },
        "reasoning_chain": rca_result["reasoning_chain"],
        "recommended_mitigation": [
            f"Step 1: {remediation.recommended_action}",
            f"Step 2: Execute command: `{remediation.commands[0] if remediation.commands else 'N/A'}`",
            f"Step 3: Verification - Estimated MTTR: {remediation.estimated_mttr_minutes} min | Safety check: {remediation.safety_checks[0] if remediation.safety_checks else 'N/A'}"
        ]
    }

    print(json.dumps(ai_rca_output, indent=2))

    # Predictive Blast Radius
    what_if = causal_graph_engine.simulate_what_if_failure(rca_result["probable_root_cause"])
    print("\n" + "-"*72)
    print(f"  [+] PREDICTIVE ARCHITECTURE BLAST RADIUS FOR '{rca_result['probable_root_cause']}'")
    print(f"  * Total Blast Radius Count : {what_if.get('total_blast_radius_count', 0)} service(s)")
    print(f"  * Impacted Downstream Nodes: {', '.join(what_if.get('all_impacted_services', []))}")
    print(f"  * Architecture Risk Score  : {what_if.get('estimated_architecture_risk_score', 0)} / 100")
    print("="*72 + "\n")


if __name__ == "__main__":
    scenario_arg = sys.argv[1] if len(sys.argv) > 1 else "database_outage"
    run_test_failure_cycle(scenario_arg)
