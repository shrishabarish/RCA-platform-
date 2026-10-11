from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class TopologyNode(BaseModel):
    id: str
    type: str = "service"
    label: Optional[str] = None
    criticality: float = 0.7


class TopologyData(BaseModel):
    nodes: List[TopologyNode]
    edges: List[List[str]]  # [caller, dependency]
    topology_version: str = "1.0"


class EventData(BaseModel):
    event_id: Optional[str] = None
    event_time: str
    observed_time: str
    source: str
    service: str
    metric: str
    value: float
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL


class SourceHealthRecord(BaseModel):
    source: str
    timestamp: str
    status: str  # HEALTHY, DEGRADED, DOWN, UNKNOWN


class GroundTruth(BaseModel):
    true_root_causes: List[str]
    onset_times: Dict[str, str] = Field(default_factory=dict)
    symptom_to_cause: Dict[str, str] = Field(default_factory=dict)
    expected_verdict: str = "DIAGNOSE"  # DIAGNOSE or ABSTAIN
    incident_groups: List[List[str]] = Field(default_factory=list)  # Groups of event IDs or services representing distinct true incidents


class EvaluationCase(BaseModel):
    case_id: str
    family_id: str
    base_case_id: Optional[str] = None  # If perturbed, points to the unperturbed base case in the family
    generator_version: str = "1.1.0"
    seed: int
    topology: TopologyData
    events: List[EventData]
    source_health: List[SourceHealthRecord] = Field(default_factory=list)
    ground_truth: GroundTruth
    perturbations: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    is_hard: bool = False

