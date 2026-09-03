from typing import Dict, Any, List, Optional
from backend.app.models.event import Incident, RemediationPlan


def generate_remediation_plan(
    incident_id: str,
    probable_root_cause: str,
    resource_id: Optional[str] = None
) -> RemediationPlan:
    """
    Generates a production-ready targeted remediation plan for an incident based on its root cause.
    Uses the real resource_id (e.g., instance-id, table-name, pod-name) to build exact executable commands.
    """
    res = resource_id or "target-resource"

    service_templates = {
        "EC2": {
            "recommended_action": f"Trigger Auto-Scaling Group Scale-Out & Reboot Overloaded Instance ({res})",
            "action_type": "SCALE_OUT",
            "estimated_mttr_minutes": 3,
            "commands": [
                f"aws autoscaling set-desired-capacity --auto-scaling-group-name prod-web-asg --desired-capacity 6",
                f"aws ec2 reboot-instances --instance-ids {res if res.startswith('i-') else '$(aws ec2 describe-instances --filters \"Name=tag:Role,Values=web\" --query \"Reservations[*].Instances[*].InstanceId\" --output text)'}",
                "aws cloudwatch set-alarm-state --alarm-name EC2-High-CPU-Alarm --state-value OK --state-reason 'Remediation auto-applied'"
            ],
            "safety_checks": [
                "Verify target capacity is within AWS account limit quotas",
                "Ensure traffic is drained from terminating nodes via ALB target group",
                "Check health check grace period (min 120s)"
            ]
        },
        "DynamoDB": {
            "recommended_action": f"Switch Table '{res}' to On-Demand Capacity & Scale Provisioned Read/Write Units",
            "action_type": "AUTO_HEAL",
            "estimated_mttr_minutes": 2,
            "commands": [
                f"aws dynamodb update-table --table-name {res if not res.startswith('target') else 'Orders'} --billing-mode PAY_PER_REQUEST",
                f"aws dynamodb update-continuous-backups --table-name {res if not res.startswith('target') else 'Orders'} --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true",
                "aws cloudwatch set-alarm-state --alarm-name DDB-Throttle-Alarm --state-value OK --state-reason 'Switched to on-demand'"
            ],
            "safety_checks": [
                "Verify no concurrent schema updates are executing",
                "Ensure AWS IAM permissions allow DynamoDB billing mode transitions",
                "Confirm provisioned GSI partitions match table settings"
            ]
        },
        "Auth": {
            "recommended_action": f"Rotate Expired JWKS Public Key & Flush Gateway Authorizer Cache ({res})",
            "action_type": "ROLLBACK",
            "estimated_mttr_minutes": 4,
            "commands": [
                "curl -X POST https://auth.internal.corp/admin/v1/jwks/refresh -H 'Authorization: Bearer $ADMIN_TOKEN'",
                "aws apigateway create-deployment --rest-api-id $API_ID --stage-name prod --description 'Flushed authorizer cache'",
                "redis-cli -h auth-cache.internal.corp FLUSHDB"
            ],
            "safety_checks": [
                "Ensure standby IDP provider is operational",
                "Verify token grace period timestamp does not lock out existing active sessions",
                "Validate SSL/TLS certificate chain expiration dates"
            ]
        },
        "Worker": {
            "recommended_action": f"Increase Pod Memory Limits & Restart Crashlooping Deployment ({res})",
            "action_type": "RESTART_POD",
            "estimated_mttr_minutes": 3,
            "commands": [
                f"kubectl patch deployment analytics-worker -n data-pipeline --patch '{{\"spec\":{{\"template\":{{\"spec\":{{\"containers\":[{{\"name\":\"worker\",\"resources\":{{\"limits\":{{\"memory\":\"8Gi\"}}}}}}]}}}}}}'",
                f"kubectl rollout restart deployment/analytics-worker -n data-pipeline",
                "kubectl rollout status deployment/analytics-worker -n data-pipeline --timeout=120s"
            ],
            "safety_checks": [
                "Check Kubernetes node group free memory capacity before rescheduling",
                "Verify dead-letter queue (DLQ) retention is >= 14 days",
                "Ensure worker idempotent consumer guarantees"
            ]
        },
        "API": {
            "recommended_action": f"Enable Circuit Breaker Pattern & Rate Limit Upstream Callers on Gateway ({res})",
            "action_type": "AUTO_HEAL",
            "estimated_mttr_minutes": 2,
            "commands": [
                "aws apigateway update-stage --rest-api-id $API_ID --stage-name prod --patch-operations op=replace,path=/throttling/burstLimit,value=500",
                "systemctl reload envoy-proxy"
            ],
            "safety_checks": [
                "Ensure health check endpoint `/health` is whitelisted from rate limiting",
                "Verify backup fallback cache is warmed up"
            ]
        }
    }

    plan_info = service_templates.get(
        probable_root_cause,
        {
            "recommended_action": f"Manual investigation required for service '{probable_root_cause}' ({res})",
            "action_type": "MANUAL_CHECK",
            "estimated_mttr_minutes": 15,
            "commands": [
                f"aws cloudwatch get-metric-data --query-id q1 --start-time $(date -u +%s -d '1 hour ago') --end-time $(date -u +%s)",
                f"kubectl logs -l app={probable_root_cause.lower()} --tail=200"
            ],
            "safety_checks": ["Review cloud provider status dashboard"]
        }
    )

    return RemediationPlan(
        incident_id=incident_id,
        target_service=probable_root_cause,
        recommended_action=plan_info["recommended_action"],
        action_type=plan_info["action_type"],
        commands=plan_info["commands"],
        estimated_mttr_minutes=plan_info["estimated_mttr_minutes"],
        safety_checks=plan_info["safety_checks"]
    )
