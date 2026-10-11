import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class ExpectedSignalPolicy(BaseModel):
    metric: str
    source: str
    interval_seconds: int = 60
    is_anomaly_only: bool = True  # True if silence (absence) is expected when healthy (QUIET)


class TelemetryPolicy(BaseModel):
    """
    Declarative policy mapping node types and services to expected telemetry signals.
    Enables rca_v2 to evaluate evidence coverage.
    Requirement L3: Missing policy means signals are NOT marked QUIET.
    """
    service_policies: Dict[str, List[ExpectedSignalPolicy]] = Field(default_factory=dict)
    type_policies: Dict[str, List[ExpectedSignalPolicy]] = Field(default_factory=lambda: {
        "compute": [
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="MemoryUtilization", source="datadog", interval_seconds=60, is_anomaly_only=True),
        ],
        "database": [
            ExpectedSignalPolicy(metric="WriteThrottleEvents", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="IOPS", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
        ],
        "service": [
            ExpectedSignalPolicy(metric="Latency", source="otel", interval_seconds=15, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="ErrorRate", source="cloudwatch", interval_seconds=15, is_anomaly_only=True),
        ],
        "api_gateway": [
            ExpectedSignalPolicy(metric="5xxErrorRate", source="cloudwatch", interval_seconds=15, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="Latency", source="otel", interval_seconds=15, is_anomaly_only=True),
        ],
        "worker": [
            ExpectedSignalPolicy(metric="QueueDepth", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="ErrorRate", source="syslog", interval_seconds=60, is_anomaly_only=True),
        ],
        "auth": [
            ExpectedSignalPolicy(metric="AuthFailureRate", source="cloudtrail", interval_seconds=30, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="Latency", source="otel", interval_seconds=30, is_anomaly_only=True),
        ],
        "cache": [
            ExpectedSignalPolicy(metric="CacheHitRatio", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="CPUUtilization", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
        ],
        "queue": [
            ExpectedSignalPolicy(metric="ApproximateNumberOfMessagesVisible", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
            ExpectedSignalPolicy(metric="DeadLetterQueueCount", source="cloudwatch", interval_seconds=60, is_anomaly_only=True),
        ]
    })

    @classmethod
    def load_live_policy(cls, path: Optional[Path] = None) -> "TelemetryPolicy":
        policy_file = path or (Path(__file__).resolve().parent / "live_telemetry_policy.json")
        if policy_file.exists():
            data = json.loads(policy_file.read_text(encoding="utf-8"))
            services = data.get("services", {})
            svc_map = {}
            for s_name, s_info in services.items():
                sigs = [ExpectedSignalPolicy(**sig) for sig in s_info.get("expected_signals", [])]
                svc_map[s_name] = sigs
            return cls(service_policies=svc_map)
        return cls()

    def get_expected_signals_for_node(self, node_id: str, node_type: str = "service") -> List[ExpectedSignalPolicy]:
        # Exact service match takes precedence
        if node_id in self.service_policies:
            return self.service_policies[node_id]

        # Case-insensitive match
        for s_name, sigs in self.service_policies.items():
            if s_name.lower() == node_id.lower():
                return sigs

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

        # Fallback if no specific policy matches:
        fallback = self.type_policies.get(node_type, self.type_policies.get("service", []))
        if not fallback:
            return []
        # Clone with is_anomaly_only=False so missing policy signals are NEVER marked QUIET
        return [
            ExpectedSignalPolicy(
                metric=f.metric,
                source=f.source,
                interval_seconds=f.interval_seconds,
                is_anomaly_only=False
            )
            for f in fallback
        ]
