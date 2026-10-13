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
    target: Optional[str] = None
    dimensions: Dict[str, Any] = Field(default_factory=dict)


class SourceHealthRecord(BaseModel):
    source: str
    timestamp: str
    status: str  # HEALTHY, DEGRADED, DOWN, UNKNOWN


class AcceptableOutcome(BaseModel):
    verdict: str  # DIAGNOSE or ABSTAIN
    roots: List[str] = Field(default_factory=list)
    max_level: Optional[str] = "CONFIRMED"  # CONFIRMED or PROBABLE or ABSTAIN
    allow_partial: bool = False  # False means incomplete root sets are not acceptable


from pydantic import BaseModel, Field, model_validator


class GroundTruth(BaseModel):
    true_root_causes: List[str]
    onset_times: Dict[str, str] = Field(default_factory=dict)
    symptom_to_cause: Dict[str, str] = Field(default_factory=dict)
    acceptable_outcomes: List[AcceptableOutcome] = Field(default_factory=list)
    max_level_by_root: Dict[str, str] = Field(default_factory=dict)
    identifiability_note: Optional[str] = None
    incident_groups: List[List[str]] = Field(default_factory=list)  # Groups of event IDs or services representing distinct true incidents

    @model_validator(mode="before")
    @classmethod
    def populate_acceptable_outcomes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("acceptable_outcomes"):
                exp = data.get("expected_verdict")
                if exp == "ABSTAIN":
                    data["acceptable_outcomes"] = [AcceptableOutcome(verdict="ABSTAIN", roots=[])]
                elif exp == "DIAGNOSE":
                    roots = data.get("true_root_causes", [])
                    data["acceptable_outcomes"] = [AcceptableOutcome(verdict="DIAGNOSE", roots=roots)]
        return data


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
    telemetry_policy: Optional[Dict[str, Any]] = None
    identifiability_note: Optional[str] = None
    acceptable_outcomes: List[AcceptableOutcome] = Field(default_factory=list)
    max_level_by_root: Dict[str, str] = Field(default_factory=dict)

