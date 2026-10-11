import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class RCAv2Config(BaseModel):
    # Component feature flags for ablation studies
    enable_source_health: bool = True
    enable_coverage: bool = True
    enable_contradictions: bool = True
    enable_margin: bool = True

    # Decision thresholds
    min_score_diagnose: float = 55.0       # Candidate ranking_score floor for diagnosis
    min_score_confirmed: float = 78.0      # Candidate score needed for CONFIRMED
    min_coverage_confirmed: float = 0.50   # Expected evidence coverage required for CONFIRMED
    min_margin_confirmed: float = 4.0      # Score margin needed for CONFIRMED
    max_down_sources_allowed: int = 0      # Max down sources allowed before abstaining on impacted service
    block_on_contradiction: bool = True    # Critical contradictions force INSUFFICIENT_EVIDENCE

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(), indent=indent)

    def sha256_hash(self) -> str:
        content = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def load_from_file(cls, path: Path) -> "RCAv2Config":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        return cls()
