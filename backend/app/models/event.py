from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventSource(str, Enum):
    CLOUDWATCH = "cloudwatch"
    CLOUDTRAIL = "cloudtrail"
    CONFIG = "config"
    APPLICATION = "application"
    SYNTHETIC = "synthetic"
    KUBERNETES = "kubernetes"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"


class CloudEvent(BaseModel):
    """
    Common internal representation for any incoming event,
    regardless of its original AWS or application source.
    """
    model_config = ConfigDict(use_enum_values=True, from_attributes=True)

    event_id: str
    timestamp: datetime
    source: EventSource
    event_type: str            # e.g. "metric_alarm", "latency", "http_error", "oom_killed"
    service: str               # e.g. "EC2", "API", "DynamoDB", "Auth", "Payment"
    resource_id: str           # e.g. "server-01", "api-gateway-01"
    metric: Optional[str] = None
    value: Optional[float] = None
    severity: Severity
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ingested_at: Optional[datetime] = None


class Incident(BaseModel):
    """
    A correlated group of events forming an operational incident.
    """
    model_config = ConfigDict(use_enum_values=True, from_attributes=True)

    incident_id: str
    events: List[CloudEvent]
    status: IncidentStatus = IncidentStatus.OPEN
    severity: Severity
    created_at: datetime
    updated_at: datetime
    probable_root_cause: Optional[str] = None
    ranking_score: Optional[float] = None
    affected_services: List[str] = Field(default_factory=list)
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    remediation_suggestion: Optional[str] = None
    archive_status: str = "PENDING"
    notification_status: str = "PENDING"
    delivery_error: Optional[str] = None
    analysis_status: str = "PENDING"
    analyzed_at: Optional[datetime] = None

    @property
    def confidence(self) -> Optional[float]:
        return self.ranking_score

    @confidence.setter
    def confidence(self, val: Optional[float]) -> None:
        self.ranking_score = val


class RemediationPlan(BaseModel):
    """
    Actionable automated or manual remediation runbook.
    """
    model_config = ConfigDict(use_enum_values=True)

    incident_id: str
    target_service: str
    recommended_action: str
    action_type: str           # e.g. "AUTO_HEAL", "SCALE_OUT", "RESTART_POD", "ROLLBACK"
    commands: List[str] = Field(default_factory=list)
    estimated_mttr_minutes: int = 5
    safety_checks: List[str] = Field(default_factory=list)


class TopologyNode(BaseModel):
    id: str
    label: str
    type: str                  # e.g. "compute", "service", "database", "auth", "cache"
    status: str = "HEALTHY"    # "HEALTHY", "DEGRADED", "FAILED", "ROOT_CAUSE"


class TopologyEdge(BaseModel):
    source: str
    target: str
    relationship: str = "depends_on"


class TopologyGraph(BaseModel):
    nodes: List[TopologyNode]
    edges: List[TopologyEdge]