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
    ranking_score: float
    earliest_event_time: str
    rank: int
    expected_signals: int = 0
    observed_signals: int = 0
    coverage: float = 0.0
    downstream_symptoms_observed: int = 0
    downstream_symptoms_expected: int = 0
    reachable_coverage: float = 0.0


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
    ranking_scores: Dict[str, float] = Field(default_factory=dict)
    margin: float = 0.0
    coverage: float = 0.0
    reachable_symptom_coverage: float = 0.0
    contradictions: List[Contradiction] = Field(default_factory=list)
    source_health_summary: Dict[str, str] = Field(default_factory=dict)
    explanation: StructuredExplanation
    metadata: Dict[str, Any] = Field(default_factory=dict)
