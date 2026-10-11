import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root if present
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

env_file = root_dir / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)

import boto3
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from backend.app.models.event import CloudEvent, EventSource, Severity
from backend.app.core.aws_client import create_boto3_client, get_boto3_kwargs, get_clean_env
from backend.app.core.config import config
from backend.app.core.retry import retry_with_backoff


class AWSCloudCollector:
    """
    Live AWS Telemetry Collector using the official AWS SDK (boto3).
    Reads credentials securely from .env or AWS CLI config without exposing secrets.
    """

    def __init__(self, region_name: Optional[str] = None):
        self.region = region_name or config.aws.region
        self._cached_connectivity: Optional[Dict[str, Any]] = None
        self._cached_connectivity_time: Optional[datetime] = None
        self._cloudtrail_supported: bool = True
        self._init_clients()

    def _init_clients(self):
        """Initializes Boto3 AWS service clients using environment credentials."""
        try:
            self.cloudwatch = create_boto3_client("cloudwatch", self.region)
            self.cloudtrail = create_boto3_client("cloudtrail", self.region)
            self.ec2 = create_boto3_client("ec2", self.region)
            self.is_configured = self.cloudwatch is not None
        except Exception as e:
            self.is_configured = False
            self.init_error = str(e)


    def check_aws_connectivity(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Checks if AWS credentials and region are accessible (with fast 60s caching)."""
        now = datetime.now(timezone.utc)
        if not force_refresh and self._cached_connectivity and self._cached_connectivity_time:
            if (now - self._cached_connectivity_time).total_seconds() < 60:
                return self._cached_connectivity

        try:
            kwargs = self._get_boto_kwargs()
            sts = boto3.client("sts", **kwargs)
            identity = sts.get_caller_identity()
            acc = identity.get("Account", "unknown")
            masked_acc = f"***{acc[-4:]}" if len(acc) >= 4 else "***"
            result = {
                "connected": True,
                "account_id": masked_acc,
                "arn": identity.get("Arn"),
                "region": self.region,
                "message": "Successfully authenticated with AWS"
            }
            self._cached_connectivity = result
            self._cached_connectivity_time = now
            return result
        except Exception as e:
            result = {
                "connected": False,
                "region": self.region,
                "error": str(e),
                "message": "AWS authentication failed. Please verify your Access Key, Secret Key, and Region in .env."
            }
            self._cached_connectivity = result
            self._cached_connectivity_time = now
            return result

    # Maps alarm names / AWS namespaces to RCA engine topology services.
    # The RCA dependency graph uses: EC2, DynamoDB, Auth, Worker, API, Application.
    ALARM_SERVICE_MAP = {
        # Alarm-name keyword -> RCA service
        "cpu":        "EC2",
        "ec2":        "EC2",
        "database":   "DynamoDB",
        "dynamo":     "DynamoDB",
        "rds":        "DynamoDB",
        "db":         "DynamoDB",
        "auth":       "Auth",
        "identity":   "Auth",
        "cognito":    "Auth",
        "worker":     "Worker",
        "timeout":    "Worker",
        "sqs":        "Worker",
        "queue":      "Worker",
        "api":        "API",
        "gateway":    "API",
        "5xx":        "API",
        "500":        "API",
        "application":"Application",
        "frontend":   "Application",
    }

    # Fallback: AWS namespace -> RCA service
    NAMESPACE_SERVICE_MAP = {
        "EC2":        "EC2",
        "RDS":        "DynamoDB",
        "DynamoDB":   "DynamoDB",
        "Lambda":     "API",
        "ApiGateway": "API",
        "ECS":        "Worker",
        "SQS":        "Worker",
        "Cognito":    "Auth",
        "ElasticLoadBalancingV2": "API",
    }

    def _map_alarm_to_service(self, alarm_name: str, namespace: str) -> str:
        """
        Intelligently maps a CloudWatch alarm to the correct RCA topology service
        by checking alarm name keywords first, then falling back to namespace mapping.
        """
        name_lower = alarm_name.lower()
        for keyword, svc in self.ALARM_SERVICE_MAP.items():
            if keyword in name_lower:
                return svc
        # Fallback: namespace-based mapping
        ns_clean = namespace.replace("AWS/", "").replace("System/", "")
        return self.NAMESPACE_SERVICE_MAP.get(ns_clean, ns_clean or "CloudWatchService")

    def fetch_active_cloudwatch_alarms(self) -> List[CloudEvent]:
        """Fetches all CloudWatch alarms in 'ALARM' state from your live AWS account."""
        events: List[CloudEvent] = []
        try:
            response = self.cloudwatch.describe_alarms(StateValue="ALARM", MaxRecords=100)
            for alarm in response.get("MetricAlarms", []):
                namespace = alarm.get("Namespace", "AWS/Unknown")
                alarm_name = alarm.get("AlarmName", "MetricAlarm")

                # Map to RCA topology service name
                service = self._map_alarm_to_service(alarm_name, namespace)

                dimensions = alarm.get("Dimensions", [])
                resource_id = dimensions[0]["Value"] if dimensions else alarm_name

                alarm_name_upper = alarm_name.upper()
                if "CRITICAL" in alarm_name_upper or "FATAL" in alarm_name_upper:
                    severity = Severity.CRITICAL
                elif "HIGH" in alarm_name_upper or "ERROR" in alarm_name_upper:
                    severity = Severity.HIGH
                elif "WARN" in alarm_name_upper:
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.HIGH

                event = CloudEvent(
                    event_id=f"cw_{alarm.get('AlarmArn', alarm_name)}",
                    timestamp=alarm.get("StateUpdatedTimestamp", datetime.now(timezone.utc)),
                    source=EventSource.CLOUDWATCH,
                    event_type=alarm_name,
                    service=service,
                    resource_id=resource_id,
                    metric=alarm.get("MetricName"),
                    value=alarm.get("Threshold"),
                    severity=severity,
                    metadata={
                        "aws_region": self.region,
                        "alarm_arn": alarm.get("AlarmArn"),
                        "state_reason": alarm.get("StateReason"),
                        "comparison_operator": alarm.get("ComparisonOperator"),
                        "aws_namespace": namespace,
                        "original_alarm_name": alarm_name
                    }
                )
                events.append(event)
        except Exception as e:
            print(f"AWS CloudWatch collection notice: {e}")

        return events

    def fetch_recent_cloudtrail_errors(self, lookup_minutes: int = 15) -> List[CloudEvent]:
        """Fetches recent AWS API error events from CloudTrail."""
        if not self._cloudtrail_supported:
            return []

        events: List[CloudEvent] = []
        try:
            start_time = datetime.now(timezone.utc) - timedelta(minutes=lookup_minutes)
            response = self.cloudtrail.lookup_events(
                LookupAttributes=[{
                    "AttributeKey": "ReadOnly",
                    "AttributeValue": "false"
                }],
                StartTime=start_time,
                MaxResults=20
            )

            for item in response.get("Events", []):
                error_code = item.get("ErrorCode")
                if error_code:
                    event = CloudEvent(
                        event_id=f"ct_{item.get('EventId')}",
                        timestamp=item.get("EventTime", datetime.now(timezone.utc)),
                        source=EventSource.CLOUDTRAIL,
                        event_type=f"api_error_{error_code.lower()}",
                        service=item.get("EventSource", "").replace(".amazonaws.com", "").upper(),
                        resource_id=item.get("Username", "aws-caller"),
                        metric="ErrorCount",
                        value=1.0,
                        severity=Severity.HIGH,
                        metadata={
                            "aws_region": self.region,
                            "event_name": item.get("EventName"),
                            "error_code": error_code,
                            "error_message": item.get("ErrorMessage")
                        }
                    )
                    events.append(event)
        except Exception as e:
            err_str = str(e)
            if "AccessDenied" in err_str or "not authorized" in err_str:
                self._cloudtrail_supported = False
                print("CloudTrail LookupEvents not permitted for this IAM user; disabling CloudTrail polling to keep sync fast.")
            else:
                print(f"AWS CloudTrail collection notice: {e}")

        return events

    def collect_all_aws_events(self) -> List[CloudEvent]:
        """Gathers all active alarms and anomalies across all configured AWS services."""
        cw_events = self.fetch_active_cloudwatch_alarms()
        ct_events = self.fetch_recent_cloudtrail_errors() if self._cloudtrail_supported else []
        return cw_events + ct_events


# Global Collector Instance
aws_collector = AWSCloudCollector()
