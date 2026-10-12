from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SourceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class EvidenceState(str, Enum):
    PRESENT = "PRESENT"
    STALE = "STALE"
    MISSING = "MISSING"
    QUIET = "QUIET"
    UNOBSERVED = "UNOBSERVED"  # Source is DOWN or UNKNOWN; cannot evaluate


class Verdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class SignalEvidence(BaseModel):
    service: str
    signal_name: str
    source: str
    state: EvidenceState
    event_ref: Optional[str] = None
    timestamp: Optional[str] = None
    value: Optional[float] = None
    details: str = ""


class Contradiction(BaseModel):
    contradiction_type: str
    description: str
    event_refs: List[str] = Field(default_factory=list)
    involved_services: List[str] = Field(default_factory=list)
    severity: str = "HIGH"


class CandidateDiagnosis(BaseModel):
    service: str
    ranking_score: float                  # 5-factor base score
    symptom_coverage: float = 0.0         # Feature: time-consistent directed coverage of symptoms
    marginal_symptom_coverage: float = 0.0
    combined_score: float = 0.0           # Tie-break / ranking score
    earliest_event_time: str
    earliest_anomalous_time: Optional[str] = None # Step 3a: Earliest anomalous event time invariant to benign heartbeats
    rank: int
    is_unobserved: bool = False           # Graph-generated candidate with no events
    expected_signals: int = 0
    observed_signals: int = 0
    coverage: float = 0.0                 # Evidence coverage of expected signals
    downstream_symptoms_observed: int = 0
    downstream_symptoms_expected: int = 0
    reachable_coverage: float = 0.0


class RootHypothesis(BaseModel):
    root: str
    ranking_score: float
    symptom_coverage: float
    linked_symptoms: List[str] = Field(default_factory=list)
    is_unobserved: bool = False


class StructuredExplanation(BaseModel):
    verdict_reason: str
    evidence_table: List[Dict[str, Any]]
    contradictions: List[Dict[str, Any]]
    summary_text: str


class DiagnosisResult(BaseModel):
    verdict: Verdict
    predicted_root_causes: List[str]
    candidate_diagnoses: List[CandidateDiagnosis]
    top_candidate: Optional[CandidateDiagnosis] = None
    root_hypotheses: List[RootHypothesis] = Field(default_factory=list)
    residual_symptoms: List[str] = Field(default_factory=list)
    ranking_scores: Dict[str, float] = Field(default_factory=dict)
    margin: float = 0.0
    coverage: float = 0.0
    reachable_symptom_coverage: float = 0.0
    contradictions: List[Contradiction] = Field(default_factory=list)
    source_health_summary: Dict[str, str] = Field(default_factory=dict)
    explanation: StructuredExplanation
    metadata: Dict[str, Any] = Field(default_factory=dict)


def get_event_field(e: Any, field: str, default: Any = None) -> Any:
    """Safely extracts field from event whether it is a dict or model object."""
    if isinstance(e, dict):
        val = e.get(field)
        return val if val is not None else default
    val = getattr(e, field, None)
    return val if val is not None else default
