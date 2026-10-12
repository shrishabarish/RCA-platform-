import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class RCAv3Config(BaseModel):
    # Step 2 Component Feature Flags for Ablation Studies
    enable_graph_candidates: bool = True       # 2a: Graph-generated candidates for unobserved roots
    enable_symptom_scoring: bool = True        # 2b: Time-consistent directed symptom-explanation scoring
    enable_set_root_selection: bool = True     # 2c: Multi-root separation via candidate set marginal coverage
    enable_directed_relatedness: bool = True   # 2d: Directed failure propagation relatedness
    enable_principled_verdicts: bool = True    # 2e: Principled verdict decision rules
    enable_circuit_breaker: bool = True        # 2f: Live safety circuit breaker

    # Temporal & Propagation Parameters
    max_propagation_delay_sec: float = 300.0   # Maximum propagation delay between cause and symptom (5 min)
    clock_skew_tolerance_sec: float = 60.0     # Clock skew tolerance window (1 min)

    # Multi-root Set Selection Parameters
    min_marginal_symptom_coverage: float = 0.15 # Min incremental symptom coverage required to add candidate root

    # Decision Thresholds
    min_score_diagnose: float = 50.0           # Floor score for diagnosis
    min_score_confirmed: float = 75.0          # Preregistered threshold for CONFIRMED
    min_coverage_confirmed: float = 0.50       # Required evidence coverage for CONFIRMED
    min_margin_confirmed: float = 4.0          # Required score margin for CONFIRMED
    max_down_sources_allowed: int = 0          # Maximum DOWN sources allowed before abstaining
    block_on_contradiction: bool = True        # Contradictions force INSUFFICIENT_EVIDENCE

    # Live Circuit Breaker Parameters
    circuit_breaker_max_failures: int = 3      # Consecutive timeouts before breaker opens
    circuit_breaker_cooldown_sec: float = 30.0 # Duration in seconds breaker stays open

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(), indent=indent)

    def sha256_hash(self) -> str:
        content = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def load_from_file(cls, path: Path) -> "RCAv3Config":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        return cls()
