from typing import Any
from copy import deepcopy
from evaluation.schema import EvaluationCase


def strip_case_for_engine(case: EvaluationCase) -> EvaluationCase:
    """
    Step 1f:
    Ensures that engines receive NO evaluation category, notes, or ground-truth hints:
    - Strips tags -> []
    - Strips perturbations -> []
    - Strips identifiability_note -> None
    - Strips ground_truth.identifiability_note -> None
    - Strips acceptable_outcomes -> []
    - Strips ground_truth.acceptable_outcomes -> []
    - Strips max_level_by_root -> {}
    - Strips ground_truth.max_level_by_root -> {}

    Preserves topology, events, and source_health intact.
    """
    case_copy = case.model_copy(deep=True)
    case_copy.tags = []
    case_copy.perturbations = []
    case_copy.identifiability_note = None
    case_copy.acceptable_outcomes = []
    case_copy.max_level_by_root = {}

    if hasattr(case_copy, "ground_truth") and case_copy.ground_truth is not None:
        case_copy.ground_truth.identifiability_note = None
        case_copy.ground_truth.acceptable_outcomes = []
        case_copy.ground_truth.max_level_by_root = {}
        case_copy.ground_truth.true_root_causes = []

    return case_copy
