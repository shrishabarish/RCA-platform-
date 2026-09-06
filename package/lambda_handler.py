import json
import logging
import sys
import os

# Ensure the root of the project is in PYTHONPATH so absolute imports work in Lambda
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.app.services.incident_service import incident_service
from backend.app.db.database import Base, engine

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Ensure database tables exist (Useful for RDS setup, but typically managed by migrations)
Base.metadata.create_all(bind=engine)

def lambda_handler(event, context):
    """
    AWS Lambda entry point for processing EventBridge / CloudWatch events.
    Expected event payload is an EventBridge event triggered by a CloudWatch Alarm.
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        # 1. Parse EventBridge Payload
        # EventBridge standard wrapper:
        # { "source": "aws.cloudwatch", "detail-type": "CloudWatch Alarm State Change", "detail": { ... } }
        
        detail = event.get('detail', {})
        
        # If it's a CloudWatch Alarm
        if event.get('source') == 'aws.cloudwatch':
            alarm_name = detail.get('alarmName', 'Unknown Alarm')
            reason = detail.get('state', {}).get('reason', '')
            
            # Construct a normalized raw event for our event processor
            raw_event = {
                "event": alarm_name,
                "service": "AWS CloudWatch", # The normalizer will map this based on the alarm name
                "level": "CRITICAL" if detail.get('state', {}).get('value') == 'ALARM' else "INFO",
                "details": {
                    "reason": reason,
                    "alarm_data": detail
                }
            }
        else:
            # Fallback for custom events sent directly to EventBridge
            raw_event = event

        # 2. Ingest into RCA Engine (This normalizes, correlates, and saves to DB)
        logger.info(f"Ingesting raw event: {raw_event}")
        cloud_event = incident_service.ingest_event(raw_event)
        
        # 3. Check for new incidents
        active_incidents = incident_service.get_all_incidents()
        open_incidents = [inc for inc in active_incidents if inc.status.value == "OPEN"]
        
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
