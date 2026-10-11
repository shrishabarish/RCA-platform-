import os
import sys
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import boto3
from backend.app.core.aws_client import create_boto3_client, get_clean_env
from backend.app.core.config import config
from backend.app.core.retry import retry_with_backoff

logger = logging.getLogger("cloudlens.sqs")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# In-memory record of SQS processed messages
_sqs_message_log: List[Dict[str, Any]] = []


class AWSSQSConsumer:
    """
    Amazon SQS Telemetry Queue Consumer for CloudLens.
    Polls the SQS event buffer for incoming Amazon EventBridge / CloudWatch alarm payloads,
    prevents dropped packets during high-volume alert storms, and passes them to the RCA engine.
    """

    def __init__(self, queue_url: Optional[str] = None, region_name: Optional[str] = None):
        self.region = region_name or config.aws.region
        self.queue_url = queue_url or config.aws.sqs_queue_url
        self.client = None
        self.is_connected = False
        self._init_client()

    def _init_client(self):
        try:
            self.client = create_boto3_client("sqs", self.region)
            self.is_connected = self.client is not None
        except Exception as e:
            logger.warning(f"Failed to initialize AWS SQS client: {e}")
            self.is_connected = False


    def get_or_create_queue(self, queue_name: str = "cloudlens-rca-ingestion-dlq") -> Optional[str]:
        """Returns or creates the SQS queue URL."""
        if self.queue_url:
            return self.queue_url
        if not self.is_connected or not self.client:
            return f"https://sqs.{self.region}.amazonaws.com/simulated/{queue_name}"

        try:
            res = self.client.get_queue_url(QueueName=queue_name)
            self.queue_url = res.get("QueueUrl")
            return self.queue_url
        except Exception:
            try:
                res = self.client.create_queue(QueueName=queue_name)
                self.queue_url = res.get("QueueUrl")
                logger.info(f"Created SQS Queue: {self.queue_url}")
                return self.queue_url
            except Exception as e:
                logger.warning(f"SQS queue create failed: {e}")
                self.queue_url = f"https://sqs.{self.region}.amazonaws.com/simulated/{queue_name}"
                return self.queue_url

    def poll_and_consume_messages(self, max_messages: int = 10) -> List[Dict[str, Any]]:
        """
        Polls messages from the SQS queue, parses CloudWatch / EventBridge payloads,
        and returns them for correlation.
        """
        if not self.is_connected or not self.client:
            return []

        q_url = self.get_or_create_queue()
        if not q_url or "simulated" in q_url:
            return []

        try:
            response = self.client.receive_message(
                QueueUrl=q_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=2,
                VisibilityTimeout=30
            )
            messages = response.get("Messages", [])
            processed = []

            for msg in messages:
                receipt_handle = msg.get("ReceiptHandle")
                body_raw = msg.get("Body", "{}")
                try:
                    body = json.loads(body_raw)
                except Exception:
                    body = {"raw": body_raw}

                processed.append({
                    "message_id": msg.get("MessageId"),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "payload": body
                })

                # Delete message after successful retrieval
                try:
                    self.client.delete_message(QueueUrl=q_url, ReceiptHandle=receipt_handle)
                except Exception as del_err:
                    logger.warning(f"Failed to delete SQS message {msg.get('MessageId')}: {del_err}")

            _sqs_message_log.extend(processed)
            return processed
        except Exception as e:
            logger.warning(f"Error polling SQS queue: {e}")
            return []

    def send_event_to_queue(self, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Pushes an event to the SQS queue for testing or asynchronous buffering."""
        q_url = self.get_or_create_queue()
        record = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "payload": event_dict
        }

        if self.is_connected and self.client and "simulated" not in q_url:
            try:
                res = self.client.send_message(
                    QueueUrl=q_url,
                    MessageBody=json.dumps(event_dict, default=str)
                )
                record["message_id"] = res.get("MessageId")
                record["status"] = "queued"
            except Exception as e:
                logger.warning(f"Could not send to live SQS ({e}), buffering to local simulated buffer")
                record["message_id"] = f"sqs_mock_{int(datetime.now(timezone.utc).timestamp())}"
                record["status"] = "buffered_simulated"
                _sqs_message_log.append(record)
                return record

        record["message_id"] = f"sqs_mock_{int(datetime.now(timezone.utc).timestamp())}"
        record["status"] = "buffered_simulated"
        _sqs_message_log.append(record)
        return record


    def get_status(self) -> Dict[str, Any]:
        """Returns live status of the Amazon SQS buffer."""
        q_url = self.get_or_create_queue()
        approx_msgs = 0
        if self.is_connected and self.client and "simulated" not in q_url:
            try:
                attrs = self.client.get_queue_attributes(
                    QueueUrl=q_url,
                    AttributeNames=['ApproximateNumberOfMessages']
                )
                approx_msgs = int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0))
            except Exception:
                pass

        return {
            "service": "Amazon SQS",
            "connected": self.is_connected,
            "region": self.region,
            "queue_url": q_url,
            "approximate_messages": approx_msgs,
            "total_messages_processed": len(_sqs_message_log)
        }


# Singleton instance
sqs_consumer = AWSSQSConsumer()
