import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import boto3

# Load credentials from .env
root_dir = Path(__file__).resolve().parent.parent
env_file = root_dir / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)

region = os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", "us-east-1"))

cw = boto3.client(
    "cloudwatch",
    region_name=region,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.getenv("AWS_SESSION_TOKEN")
)


def create_and_trigger_real_aws_incident():
    """
    Creates real lightweight CloudWatch Alarms in your AWS Account ($0 cost)
    and sets them to 'ALARM' state so you can demonstrate live AWS triage.
    """
    print(f"\n=======================================================")
    print(f"  ⚡ CREATING & TRIGGERING REAL AWS CLOUDWATCH ALARMS")
    print(f"  Target AWS Region: {region}")
    print(f"=======================================================")

    alarms = [
        {
            "name": "CloudLens-EC2-CPU-Saturation",
            "metric": "CPUUtilization",
            "namespace": "AWS/EC2",
            "resource": "i-0987654321fedcba0",
            "threshold": 95.0,
            "reason": "CPU exceeded 95% threshold on production web server."
        },
        {
            "name": "CloudLens-API-Gateway-Latency",
            "metric": "Latency",
            "namespace": "AWS/ApiGateway",
            "resource": "api-orders-v1",
            "threshold": 2500.0,
            "reason": "Upstream timeout - latency breached 2500ms SLA."
        },
        {
            "name": "CloudLens-DynamoDB-Read-Throttle",
            "metric": "ThrottledRequests",
            "namespace": "AWS/DynamoDB",
            "resource": "OrdersTable",
            "threshold": 120.0,
            "reason": "DynamoDB provisioned capacity exceeded."
        }
    ]

    print("\n[1/2] Creating CloudWatch Alarms in your AWS account...")
    for a in alarms:
        cw.put_metric_alarm(
            AlarmName=a["name"],
            AlarmDescription="Test incident created by CloudLens RCA Platform",
            MetricName=a["metric"],
            Namespace=a["namespace"],
            Statistic="Average",
            Period=60,
            EvaluationPeriods=1,
            Threshold=a["threshold"],
            ComparisonOperator="GreaterThanThreshold",
            Dimensions=[{"Name": "ResourceName", "Value": a["resource"]}]
        )
        print(f"      + Created alarm: {a['name']}")

    print("\n[2/2] Setting CloudWatch Alarms to 'ALARM' state in real AWS...")
    for a in alarms:
        cw.set_alarm_state(
            AlarmName=a["name"],
            StateValue="ALARM",
            StateReason=a["reason"]
        )
        print(f"      ✓ Triggered: {a['name']} -> State: ALARM")

    print(f"\n=======================================================")
    print(f"  🎉 LIVE INCIDENT IS NOW ACTIVE IN YOUR AWS ACCOUNT!")
    print(f"  Now click 'Check live AWS' on your dashboard or run:")
    print(f"  curl -X POST http://localhost:8000/api/aws/poll")
    print(f"=======================================================\n")


def cleanup_real_aws_alarms():
    """Deletes the test CloudWatch alarms from your AWS account."""
    alarm_names = [
        "CloudLens-EC2-CPU-Saturation",
        "CloudLens-API-Gateway-Latency",
        "CloudLens-DynamoDB-Read-Throttle"
    ]
    print("\nCleaning up test CloudWatch alarms from AWS...")
    cw.delete_alarms(AlarmNames=alarm_names)
    print("✓ All test alarms deleted from your AWS account.\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_real_aws_alarms()
    else:
        create_and_trigger_real_aws_incident()
