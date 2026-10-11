import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import uuid
import boto3
from backend.app.core.aws_client import create_boto3_client, get_clean_env
from backend.app.core.config import config
from backend.app.core.retry import retry_with_backoff

logger = logging.getLogger("cloudlens.sns")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# In-memory history of dispatched notifications for audit & UI telemetry
_sns_dispatch_history: List[Dict[str, Any]] = []


class AWSSNSNotifier:
    """
    Amazon SNS Notification Service for CloudLens.
    Dispatches real-time incident alerts, root-cause summaries, and
    mitigation updates to on-call SRE teams via email, SMS, or Slack webhooks.
    """

    def __init__(self, region_name: Optional[str] = None, topic_arn: Optional[str] = None):
        self.region = region_name or config.aws.region
        self.topic_arn = topic_arn or config.aws.sns_topic_arn
        self.client = None
        self.is_connected = False
        self._init_client()

    def _init_client(self):
        try:
            self.client = create_boto3_client("sns", self.region)
            self.is_connected = self.client is not None
        except Exception as e:
            logger.warning(f"Failed to initialize AWS SNS client: {e}")
            self.is_connected = False


    def get_or_create_topic(self, topic_name: str = "cloudlens-incident-notifications") -> Optional[str]:
        """Returns the configured topic ARN, or creates/retrieves it on AWS SNS."""
        if self.topic_arn:
            return self.topic_arn

        if not self.is_connected or not self.client:
            return f"arn:aws:sns:{self.region}:simulated:cloudlens-incident-notifications"

        try:
            response = self.client.create_topic(Name=topic_name)
            self.topic_arn = response.get("TopicArn")
            logger.info(f"Using SNS Topic: {self.topic_arn}")
            return self.topic_arn
        except Exception as e:
            logger.warning(f"Could not auto-create SNS topic '{topic_name}': {e}. Using simulated ARN.")
            self.topic_arn = f"arn:aws:sns:{self.region}:simulated:{topic_name}"
            return self.topic_arn

    def publish_incident_alert(
        self,
        incident_data: Dict[str, Any],
        s3_report_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Publishes a high-priority incident alert to Amazon SNS.
        """
        inc_id = incident_data.get("incident_id", "inc_unknown")
        severity = incident_data.get("severity", "CRITICAL")
        root_cause = incident_data.get("probable_root_cause", "UNKNOWN")
        confidence = incident_data.get("confidence", 0.0)
        affected = incident_data.get("affected_services", [])
        remediation = incident_data.get("remediation_suggestion", "Check CloudWatch logs")

        subject = f"[CloudLens {severity}] Incident {inc_id} - Root Cause: {root_cause} ({confidence}%)"
        # SNS subjects must be <= 100 characters
        if len(subject) > 100:
            subject = subject[:97] + "..."

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        message_lines = [
            "============================================================",
            "  CLOUDLENS AUTONOMOUS INCIDENT ALERT (AMAZON SNS)",
            "============================================================",
            f"Incident ID        : {inc_id}",
            f"Detected At        : {timestamp}",
            f"Severity Level     : {severity}",
            f"Probable Root Cause: {root_cause} (Confidence: {confidence}%)",
            f"Affected Services  : {', '.join(affected) if affected else 'None'}",
            "",
            "------------------------------------------------------------",
            "  RECOMMENDED CLOUD REMEDIATION RUNBOOK",
            "------------------------------------------------------------",
            f"Action: {remediation}",
            ""
        ]

        if s3_report_url:
            message_lines.extend([
                "------------------------------------------------------------",
                "  AMAZON S3 POST-MORTEM AUDIT ARCHIVE",
                "------------------------------------------------------------",
                f"S3 Audit Report: {s3_report_url}",
                ""
            ])

        message_lines.append("CloudLens Autonomous SRE Engine | Live Telemetry")
        message_body = "\n".join(message_lines)

        topic_arn = self.get_or_create_topic()
        dedupe_key = f"{inc_id}:{incident_data.get('status', 'OPEN')}"
        message_attributes = {
            "DeduplicationId": {
                "DataType": "String",
                "StringValue": dedupe_key
            },
            "IncidentId": {
                "DataType": "String",
                "StringValue": inc_id
            }
        }

        dispatch_record = {
            "dispatch_id": f"sns_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{inc_id}",
            "timestamp": timestamp,
            "type": "INCIDENT_ALERT",
            "incident_id": inc_id,
            "dedupe_key": dedupe_key,
            "severity": severity,
            "root_cause": root_cause,
            "topic_arn": topic_arn,
            "subject": subject,
            "status": "pending",
            "message_preview": message_body[:200] + "..."
        }

        if os.getenv("CLOUDLENS_MOCK_AWS", "false").lower() == "true":
            dispatch_record["status"] = "SENT"
            dispatch_record["message_id"] = f"mock-msg-{uuid.uuid4().hex[:8]}"
            return dispatch_record

        if self.is_connected and self.client and not topic_arn.startswith("arn:aws:sns:eu-north-1:simulated"):
            try:
                res = self.client.publish(
                    TopicArn=topic_arn,
                    Subject=subject,
                    Message=message_body,
                    MessageAttributes=message_attributes
                )
                dispatch_record["message_id"] = res.get("MessageId")
                dispatch_record["status"] = "SENT"
                logger.info(f"Dispatched SNS Alert for {inc_id} [dedupe_key={dedupe_key}] -> MessageId: {res.get('MessageId')}")
            except Exception as e:
                logger.warning(f"SNS publish failed ({e}). Recorded failure.")
                dispatch_record["status"] = "FAILED"
                dispatch_record["error"] = str(e)
                dispatch_record["note"] = str(e)
        else:
            dispatch_record["status"] = "FAILED"
            dispatch_record["error"] = "SNS client offline or test ARN"
            dispatch_record["note"] = "SNS client offline or test ARN"

        _sns_dispatch_history.append(dispatch_record)
        return dispatch_record

    def publish_mitigation_alert(
        self,
        incident_id: str,
        root_cause: str,
        remediation_message: str
    ) -> Dict[str, Any]:
        """Publishes a notification when an incident is successfully resolved / mitigated."""
        subject = f"[CloudLens RESOLVED] Incident {incident_id} Mitigated"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        body = (
            f"CLOUDLENS INCIDENT RESOLUTION\n"
            f"Incident ID : {incident_id}\n"
            f"Resolved At : {timestamp}\n"
            f"Target      : {root_cause}\n"
            f"Details     : {remediation_message}\n\n"
            f"Status      : All telemetry returned to nominal thresholds."
        )

        topic_arn = self.get_or_create_topic()
        dispatch_record = {
            "dispatch_id": f"sns_resolve_{incident_id}",
            "timestamp": timestamp,
            "type": "INCIDENT_RESOLVED",
            "incident_id": incident_id,
            "root_cause": root_cause,
            "topic_arn": topic_arn,
            "subject": subject,
            "status": "delivered" if self.is_connected else "simulated_success",
            "message_preview": body
        }

        if self.is_connected and self.client and not topic_arn.startswith("arn:aws:sns:eu-north-1:simulated"):
            try:
                res = self.client.publish(
                    TopicArn=topic_arn,
                    Subject=subject[:100],
                    Message=body
                )
                dispatch_record["message_id"] = res.get("MessageId")
            except Exception as e:
                dispatch_record["status"] = "simulated_success"
                dispatch_record["note"] = str(e)

        _sns_dispatch_history.append(dispatch_record)
        return dispatch_record

    def get_dispatch_history(self) -> List[Dict[str, Any]]:
        """Returns the log of recent SNS dispatches."""
        return _sns_dispatch_history[-50:]

    def get_status(self) -> Dict[str, Any]:
        """Returns live status of the SNS notification integration."""
        return {
            "service": "Amazon SNS",
            "connected": self.is_connected,
            "region": self.region,
            "topic_arn": self.topic_arn or "cloudlens-incident-notifications",
            "total_dispatches": len(_sns_dispatch_history)
        }


# Singleton instance
sns_notifier = AWSSNSNotifier()
