from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class ExpectedSignalPolicy(BaseModel):
    metric: str
    source: str
    is_anomaly_only: bool = True  # True if silence (absence) is expected when healthy (QUIET)


class TelemetryPolicy(BaseModel):
    """
    Declarative policy mapping node types and services to expected telemetry signals.
    Enables rca_v2 to evaluate evidence coverage without editing case files.
    """
    type_policies: Dict[str, List[ExpectedSignalPolicy]] = Field(default_factory=lambda: {
        "compute": [
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="MemoryUtilization", source="datadog", is_anomaly_only=True),
        ],
        "database": [
            ExpectedSignalPolicy(metric="WriteThrottleEvents", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="IOPS", source="cloudwatch", is_anomaly_only=True),
        ],
        "service": [
            ExpectedSignalPolicy(metric="Latency", source="otel", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="ErrorRate", source="cloudwatch", is_anomaly_only=True),
        ],
        "api_gateway": [
            ExpectedSignalPolicy(metric="5xxErrorRate", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="Latency", source="otel", is_anomaly_only=True),
        ],
        "worker": [
            ExpectedSignalPolicy(metric="QueueDepth", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="ErrorRate", source="syslog", is_anomaly_only=True),
        ],
        "auth": [
            ExpectedSignalPolicy(metric="AuthFailureRate", source="cloudtrail", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="Latency", source="otel", is_anomaly_only=True),
        ],
        "cache": [
            ExpectedSignalPolicy(metric="CacheHitRatio", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", is_anomaly_only=True),
        ],
        "queue": [
            ExpectedSignalPolicy(metric="ApproximateNumberOfMessagesVisible", source="cloudwatch", is_anomaly_only=True),
            ExpectedSignalPolicy(metric="DeadLetterQueueCount", source="cloudwatch", is_anomaly_only=True),
        ]
    })

    def get_expected_signals_for_node(self, node_id: str, node_type: str = "service") -> List[ExpectedSignalPolicy]:
        node_id_lower = node_id.lower()
        if "dynamo" in node_id_lower or "postgres" in node_id_lower or "db" in node_id_lower:
            return self.type_policies.get("database", [])
        if "auth" in node_id_lower:
            return self.type_policies.get("auth", [])
        if "gateway" in node_id_lower or "api" in node_id_lower:
            return self.type_policies.get("api_gateway", [])
        if "worker" in node_id_lower:
            return self.type_policies.get("worker", [])
        if "cache" in node_id_lower or "redis" in node_id_lower:
            return self.type_policies.get("cache", [])
        if "compute" in node_id_lower or "ec2" in node_id_lower or "eks" in node_id_lower:
            return self.type_policies.get("compute", [])
        
        return self.type_policies.get(node_type, self.type_policies.get("service", []))
