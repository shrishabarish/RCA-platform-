import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
from backend.app.core.aws_client import create_boto3_client, get_clean_env
from backend.app.core.config import config
from backend.app.core.retry import retry_with_backoff

logger = logging.getLogger("cloudlens.s3")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Local memory cache of archived reports (for instant UI retrieval and offline resilience)
_s3_archived_cache: Dict[str, Dict[str, Any]] = {}


class AWSS3Archiver:
    """
    Amazon S3 Incident Post-Mortem and Historical Log Archiver for CloudLens.
    Permanently stores structured post-mortem reports, graph reasoning chains,
    and raw telemetry event dumps in an S3 bucket for compliance and auditability.
    """

    def __init__(self, bucket_name: Optional[str] = None, region_name: Optional[str] = None):
        self.region = region_name or config.aws.region
        self.bucket_name = bucket_name or config.aws.s3_bucket_name
        self.client = None
        self.is_connected = False
        self._bucket_verified = False
        self._init_client()

    def _init_client(self):
        try:
            self.client = create_boto3_client("s3", self.region)
            self.is_connected = self.client is not None
        except Exception as e:
            logger.warning(f"Failed to initialize AWS S3 client: {e}")
            self.is_connected = False


    def ensure_bucket(self) -> bool:
        """Verifies or creates the S3 archive bucket."""
        if self._bucket_verified:
            return True
        if not self.is_connected or not self.client:
            return False

        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            self._bucket_verified = True
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    create_kwargs: Dict[str, Any] = {"Bucket": self.bucket_name}
                    if self.region != "us-east-1":
                        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
                    self.client.create_bucket(**create_kwargs)
                    logger.info(f"Created Amazon S3 bucket: {self.bucket_name}")
                    self._bucket_verified = True
                    return True
                except Exception as create_err:
                    logger.warning(f"Could not auto-create S3 bucket {self.bucket_name}: {create_err}")
                    return False
            else:
                logger.warning(f"S3 head_bucket error: {e}")
                return False
        except Exception as e:
            logger.warning(f"S3 verify error: {e}")
            return False

    def archive_incident(self, incident_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Builds a comprehensive SRE post-mortem document and archives it to Amazon S3.
        """
        inc_id = incident_dict.get("incident_id", "inc_unknown")
        now_utc = datetime.now(timezone.utc)
        timestamp_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        post_mortem = {
            "metadata": {
                "platform": "CloudLens Autonomous RCA (Enterprise)",
                "schema_version": "2.0.0",
                "incident_id": inc_id,
                "archived_at": timestamp_str,
                "aws_region": self.region,
                "s3_bucket": self.bucket_name
            },
            "incident_summary": {
                "incident_id": inc_id,
                "severity": incident_dict.get("severity", "CRITICAL"),
                "status": incident_dict.get("status", "RESOLVED"),
                "created_at": str(incident_dict.get("created_at", timestamp_str)),
                "updated_at": str(incident_dict.get("updated_at", timestamp_str)),
                "probable_root_cause": incident_dict.get("probable_root_cause", "UNKNOWN"),
                "confidence_score": incident_dict.get("confidence", 0.0),
                "affected_services": incident_dict.get("affected_services", []),
                "is_compound_incident": incident_dict.get("is_compound_incident", False)
            },
            "causal_graph_analysis": {
                "propagation_paths": incident_dict.get("causal_paths", []),
                "score_breakdown": incident_dict.get("score_breakdown", {}),
                "mathematical_reasoning_chain": incident_dict.get("reasoning_chain", [])
            },
            "remediation_runbook": {
                "action": incident_dict.get("remediation_suggestion", "N/A"),
                "plan_details": incident_dict.get("remediation_plan", {})
            },
            "telemetry_events": incident_dict.get("events", [])
        }

        s3_key = f"incidents/{inc_id}/post_mortem.json"
        s3_uri = f"s3://{self.bucket_name}/{s3_key}"

        archive_result = {
            "incident_id": inc_id,
            "archived_at": timestamp_str,
            "s3_uri": s3_uri,
            "s3_bucket": self.bucket_name,
            "s3_key": s3_key,
            "status": "archived_locally",
            "report_summary": {
                "root_cause": post_mortem["incident_summary"]["probable_root_cause"],
                "confidence": post_mortem["incident_summary"]["confidence_score"],
                "severity": post_mortem["incident_summary"]["severity"]
            }
        }

        # Cache in memory
        _s3_archived_cache[inc_id] = post_mortem

        # Upload to AWS S3 if connected or simulated in mock mode
        if os.getenv("CLOUDLENS_MOCK_AWS", "false").lower() == "true":
            archive_result["status"] = "SENT"
            return archive_result

        if self.is_connected and self.client and self.ensure_bucket():
            try:
                body = json.dumps(post_mortem, indent=2, default=str).encode("utf-8")
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=body,
                    ContentType="application/json",
                    Metadata={
                        "incident_id": inc_id,
                        "root_cause": str(post_mortem["incident_summary"]["probable_root_cause"]),
                        "confidence": str(post_mortem["incident_summary"]["confidence_score"])
                    }
                )
                archive_result["status"] = "SENT"
                logger.info(f"Successfully archived incident {inc_id} to {s3_uri}")
            except Exception as e:
                logger.warning(f"S3 upload failed ({e}). Retaining in local cache.")
                archive_result["status"] = "FAILED"
                archive_result["error"] = str(e)
                archive_result["note"] = str(e)
        else:
            archive_result["status"] = "FAILED"
            archive_result["error"] = "S3 bucket offline or unreachable"
            archive_result["note"] = "S3 bucket offline or unreachable"

        return archive_result

    def archive_telemetry_batch(self, events: List[Dict[str, Any]], batch_id: Optional[str] = None) -> Dict[str, Any]:
        """Archives a batch of raw telemetry events to Amazon S3."""
        bid = batch_id or f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        s3_key = f"telemetry/{bid}.json"
        s3_uri = f"s3://{self.bucket_name}/{s3_key}"

        payload = {
            "batch_id": bid,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "event_count": len(events),
            "events": events
        }

        if self.is_connected and self.client and self.ensure_bucket():
            try:
                body = json.dumps(payload, indent=2, default=str).encode("utf-8")
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=body,
                    ContentType="application/json"
                )
                logger.info(f"Archived telemetry batch {bid} to {s3_uri}")
                return {"status": "uploaded_to_s3", "s3_uri": s3_uri, "event_count": len(events)}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {"status": "cached_locally", "s3_uri": s3_uri, "event_count": len(events)}

    def get_incident_report(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves an archived post-mortem report from S3 or the memory cache."""
        if incident_id in _s3_archived_cache:
            return _s3_archived_cache[incident_id]

        if self.is_connected and self.client:
            s3_key = f"incidents/{incident_id}/post_mortem.json"
            try:
                res = self.client.get_object(Bucket=self.bucket_name, Key=s3_key)
                content = json.loads(res["Body"].read().decode("utf-8"))
                _s3_archived_cache[incident_id] = content
                return content
            except Exception as e:
                logger.warning(f"Could not fetch {s3_key} from S3: {e}")
                return None
        return None

    def list_archived_reports(self) -> List[Dict[str, Any]]:
        """Lists all available archived incident reports."""
        summaries = []
        for inc_id, report in _s3_archived_cache.items():
            meta = report.get("metadata", {})
            summ = report.get("incident_summary", {})
            summaries.append({
                "incident_id": inc_id,
                "archived_at": meta.get("archived_at"),
                "s3_uri": f"s3://{self.bucket_name}/incidents/{inc_id}/post_mortem.json",
                "probable_root_cause": summ.get("probable_root_cause"),
                "ranking_score": summ.get("ranking_score") or summ.get("confidence_score"),
                "severity": summ.get("severity")
            })
        return summaries

    def get_status(self) -> Dict[str, Any]:
        """Returns live status of the S3 Archival integration."""
        return {
            "service": "Amazon S3",
            "connected": self.is_connected,
            "region": self.region,
            "bucket_name": self.bucket_name,
            "total_archived_reports": len(_s3_archived_cache)
        }


# Singleton instance
s3_archiver = AWSS3Archiver()
