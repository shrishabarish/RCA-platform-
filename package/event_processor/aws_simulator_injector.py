import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import boto3

# Load credentials from .env
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

env_file = root_dir / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)

from event_processor.aws_collector import _get_clean_env

region = _get_clean_env("AWS_DEFAULT_REGION", _get_clean_env("AWS_REGION", "us-east-1"))

def _get_cw_client():
    kwargs = {"region_name": region}
    ak = _get_clean_env("AWS_ACCESS_KEY_ID")
    sk = _get_clean_env("AWS_SECRET_ACCESS_KEY")
    token = _get_clean_env("AWS_SESSION_TOKEN")
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        if token:
            kwargs["aws_session_token"] = token
    return boto3.client("cloudwatch", **kwargs)

cw = _get_cw_client()


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
    try:
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
            print(f"      [OK] Triggered: {a['name']} -> State: ALARM")

        print(f"\n=======================================================")
        print(f"  [*] LIVE INCIDENT IS NOW ACTIVE IN YOUR AWS ACCOUNT!")
        print(f"  Now click 'Check live AWS' on your dashboard or run:")
        print(f"  curl -X POST http://localhost:8000/api/aws/poll")
        print(f"=======================================================\n")
    except Exception as e:
        print(f"\n[!] AWS Permission Notice: {e}")
        print("    Your IAM user is authenticated with AWS, but needs 'cloudwatch:PutMetricAlarm'")
        print("    and 'cloudwatch:SetAlarmState' permissions in IAM to create test alarms via script.")
        print("    (You can also use 'python test_failure_cycle.py' for local simulated test runs)")


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
