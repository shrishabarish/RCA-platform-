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


def _get_clean_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Helper to cleanly extract environment variables without quotes or whitespace."""
    val = os.getenv(key, default)
    if val is None:
        return None
    val = str(val).strip().strip("'\"")
    return val if val else None


class AWSCloudCollector:
    """
    Live AWS Telemetry Collector using the official AWS SDK (boto3).
    Reads credentials securely from .env or AWS CLI config without exposing secrets.
    """

    def __init__(self, region_name: Optional[str] = None):
        self.region = region_name or _get_clean_env("AWS_DEFAULT_REGION", _get_clean_env("AWS_REGION", "us-east-1"))
        self._init_clients()

    def _get_boto_kwargs(self) -> Dict[str, Any]:
        """Constructs Boto3 client arguments cleanly."""
        kwargs: Dict[str, Any] = {"region_name": self.region}
        ak = _get_clean_env("AWS_ACCESS_KEY_ID")
        sk = _get_clean_env("AWS_SECRET_ACCESS_KEY")
        token = _get_clean_env("AWS_SESSION_TOKEN")
        if ak and sk:
            kwargs["aws_access_key_id"] = ak
            kwargs["aws_secret_access_key"] = sk
            if token:
                kwargs["aws_session_token"] = token
        return kwargs

    def _init_clients(self):
        """Initializes Boto3 AWS service clients using environment credentials."""
        try:
            kwargs = self._get_boto_kwargs()
            self.cloudwatch = boto3.client("cloudwatch", **kwargs)
            self.cloudtrail = boto3.client("cloudtrail", **kwargs)
            self.ec2 = boto3.client("ec2", **kwargs)
            self.is_configured = True
        except Exception as e:
            self.is_configured = False
            self.init_error = str(e)

    def check_aws_connectivity(self) -> Dict[str, Any]:
        """Checks if AWS credentials and region are accessible."""
        try:
            kwargs = self._get_boto_kwargs()
            sts = boto3.client("sts", **kwargs)
            identity = sts.get_caller_identity()
            # Mask the Account ID and ARN for security
            acc = identity.get("Account", "unknown")
            masked_acc = f"***{acc[-4:]}" if len(acc) >= 4 else "***"
            return {
                "connected": True,
                "account_id": masked_acc,
                "arn": identity.get("Arn"),
                "region": self.region,
                "message": "Successfully authenticated with AWS"
            }
        except Exception as e:
            return {
                "connected": False,
                "region": self.region,
                "error": str(e),
                "message": "AWS authentication failed. Please verify your Access Key, Secret Key, and Region in .env."
            }
        except Exception as e:
            return {
                "connected": False,
                "region": self.region,
                "error": str(e),
                "message": "AWS credentials not configured. Add them to .env or run 'aws configure'."
            }

    def fetch_active_cloudwatch_alarms(self) -> List[CloudEvent]:
        """Fetches all CloudWatch alarms in 'ALARM' state from your live AWS account."""
        events: List[CloudEvent] = []
        try:
            paginator = self.cloudwatch.get_paginator("describe_alarms")
            for page in paginator.paginate(StateValue="ALARM"):
                for alarm in page.get("MetricAlarms", []):
                    namespace = alarm.get("Namespace", "AWS/Unknown")
                    service = namespace.replace("AWS/", "").replace("System/", "")
                    if not service:
                        service = "CloudWatchService"

                    dimensions = alarm.get("Dimensions", [])
                    resource_id = dimensions[0]["Value"] if dimensions else alarm.get("AlarmName", "unknown-res")

                    alarm_name = alarm.get("AlarmName", "").upper()
                    if "CRITICAL" in alarm_name or "FATAL" in alarm_name:
                        severity = Severity.CRITICAL
                    elif "HIGH" in alarm_name or "ERROR" in alarm_name:
                        severity = Severity.HIGH
                    elif "WARN" in alarm_name:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.HIGH

                    event = CloudEvent(
                        event_id=f"cw_{alarm.get('AlarmArn', alarm.get('AlarmName'))}",
                        timestamp=alarm.get("StateUpdatedTimestamp", datetime.now(timezone.utc)),
                        source=EventSource.CLOUDWATCH,
                        event_type=alarm.get("AlarmName", "MetricAlarm"),
                        service=service,
                        resource_id=resource_id,
                        metric=alarm.get("MetricName"),
                        value=alarm.get("Threshold"),
                        severity=severity,
                        metadata={
                            "aws_region": self.region,
                            "alarm_arn": alarm.get("AlarmArn"),
                            "state_reason": alarm.get("StateReason"),
                            "comparison_operator": alarm.get("ComparisonOperator")
                        }
                    )
                    events.append(event)
        except Exception as e:
            print(f"AWS CloudWatch collection notice: {e}")

        return events

    def fetch_recent_cloudtrail_errors(self, lookup_minutes: int = 15) -> List[CloudEvent]:
        """Fetches recent AWS API error events from CloudTrail."""
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
            print(f"AWS CloudTrail collection notice: {e}")

        return events

    def collect_all_aws_events(self) -> List[CloudEvent]:
        """Gathers all active alarms and anomalies across all configured AWS services."""
        cw_events = self.fetch_active_cloudwatch_alarms()
        ct_events = self.fetch_recent_cloudtrail_errors()
        return cw_events + ct_events


# Global Collector Instance
aws_collector = AWSCloudCollector()
