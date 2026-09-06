import json
import logging
import sys
import os

import uuid
from datetime import datetime, timezone

# Ensure the root of the project is in PYTHONPATH so absolute imports work in Lambda
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.app.services.incident_service import incident_service
from backend.app.db.database import Base, engine
from event_processor.normalizer import normalize_external_webhook

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Ensure database tables exist
Base.metadata.create_all(bind=engine)

def lambda_handler(event, context):
    """
    AWS Lambda entry point for processing EventBridge / CloudWatch events.
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        detail = event.get('detail', {})
        
        # 1. If it's a CloudWatch Alarm from EventBridge
        if event.get('source') == 'aws.cloudwatch':
            alarm_name = detail.get('alarmName', 'Unknown Alarm')
            reason = detail.get('state', {}).get('reason', '')
            state_val = detail.get('state', {}).get('value', 'ALARM')
            
            raw_event = {
                "event_id": f"cw_{uuid.uuid4().hex[:8]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "cloudwatch",
                "event_type": "cpu_utilization_high" if "cpu" in alarm_name.lower() else "cloudwatch_alarm",
                "service": "EC2" if "ec2" in alarm_name.lower() else "API",
                "resource_id": "i-0a1b2c3d4e5f67890",
                "metric": "CPUUtilization" if "cpu" in alarm_name.lower() else "AlarmStatus",
                "value": 94.2 if "cpu" in alarm_name.lower() else 1.0,
                "severity": "CRITICAL" if state_val == 'ALARM' else "LOW",
                "metadata": {
                    "alarm_name": alarm_name,
                    "reason": reason,
                    "alarm_data": detail
                }
            }
        elif "event_id" in event and "timestamp" in event:
            # Already a canonical CloudEvent format
            raw_event = event
        else:
            # Fallback for structured webhook logs
            raw_event = normalize_external_webhook(event)

        # 2. Ingest into RCA Engine (This normalizes, correlates, and saves to DB)
        logger.info(f"Ingesting raw event: {raw_event}")
        cloud_event = incident_service.ingest_event(raw_event)
        
        # 3. Check for new incidents
        active_incidents = incident_service.get_all_incidents()
        open_incidents = [
            inc for inc in active_incidents 
            if (inc.status.value if hasattr(inc.status, 'value') else str(inc.status)) == "OPEN"
        ]
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Event processed and RCA analyzed successfully.",
                "event_id": cloud_event.event_id,
                "active_incidents": len(open_incidents)
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
