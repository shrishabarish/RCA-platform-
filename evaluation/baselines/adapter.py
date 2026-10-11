from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from evaluation.schema import EvaluationCase


class BaselineResult(BaseModel):
    baseline_name: str
    case_id: str
    verdict: str = "DIAGNOSE"  # Baselines never abstain unless explicitly configured
    predicted_root_causes: List[str]  # Ordered ranked list from highest to lowest
    candidate_scores: Dict[str, float]  # Service -> numerical score
    confidence: float  # Top predicted root cause confidence [0, 100]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RCABaselineAdapter(ABC):
    """Abstract interface for all RCA candidate ranking engines and baselines."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def predict(self, case: EvaluationCase) -> BaselineResult:
        """
        Pure function: given an EvaluationCase (in-memory topology, events, health),
        outputs ranked candidate root causes and confidence without database or network calls.
        """
        pass
