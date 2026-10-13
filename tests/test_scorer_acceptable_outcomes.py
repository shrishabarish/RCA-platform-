import pytest
from evaluation.schema import EvaluationCase, GroundTruth, AcceptableOutcome, TopologyData, TopologyNode
from evaluation.baselines.adapter import BaselineResult
from evaluation.metrics.engine import is_outcome_acceptable, compute_confident_wrong_rate


def test_schema_excludes_legacy_fields():
    """Verify that expected_verdict and acceptable_verdict_level are not in the schema."""
    assert "expected_verdict" not in GroundTruth.model_fields
    assert "acceptable_verdict_level" not in GroundTruth.model_fields
    assert "expected_verdict" not in EvaluationCase.model_fields
    assert "acceptable_verdict_level" not in EvaluationCase.model_fields


def test_scorer_uses_acceptable_outcomes_and_max_level_by_root():
    """
    Tests that the evaluation scorer:
    1. Uses acceptable_outcomes and max_level_by_root exclusively.
    2. Treats diagnosing a PROBABLE-capped root at CONFIRMED (score >= 75) as confident-wrong.
    3. Treats ABSTAIN as acceptable when ABSTAIN is in acceptable_outcomes.
    4. Treats diagnosing an ABSTAIN-only root as wrong and confident-wrong at >= 75.
    """
    topo = TopologyData(
        nodes=[TopologyNode(id="API", type="service"), TopologyNode(id="DynamoDB", type="database")],
        edges=[["API", "DynamoDB"]]
    )

    gt = GroundTruth(
        true_root_causes=[],
        acceptable_outcomes=[
            AcceptableOutcome(verdict="ABSTAIN", roots=[], max_level="ABSTAIN"),
            AcceptableOutcome(verdict="DIAGNOSE", roots=["API"], max_level="PROBABLE")
        ],
        max_level_by_root={"API": "PROBABLE", "DynamoDB": "ABSTAIN"},
        identifiability_note="Contradiction test case"
    )

    case = EvaluationCase(
        case_id="test_indep_025",
        family_id="test_family",
        generator_version="test_v1",
        seed=123,
        topology=topo,
        events=[],
        ground_truth=gt,
        acceptable_outcomes=gt.acceptable_outcomes,
        max_level_by_root=gt.max_level_by_root
    )

    # 1. Prediction: ABSTAIN -> Acceptable, not confident-wrong
    p_abstain = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="ABSTAIN",
        predicted_root_causes=[],
        candidate_scores={},
        confidence=0.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_abstain)
    assert is_acc is True, "ABSTAIN prediction must be acceptable"
    assert is_cw is False, "ABSTAIN prediction cannot be confident-wrong"

    # 2. Prediction: DIAGNOSE API at PROBABLE level (score=60.0) -> Acceptable, not confident-wrong
    p_api_probable = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["API"],
        candidate_scores={"API": 60.0},
        confidence=60.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_api_probable)
    assert is_acc is True, "DIAGNOSE API at PROBABLE level must be acceptable"
    assert is_cw is False, "DIAGNOSE API at PROBABLE level must not be confident-wrong"

    # 3. Prediction: DIAGNOSE API at CONFIRMED level (score=90.0 >= 75.0) -> Not acceptable, CONFIDENT-WRONG
    p_api_confirmed = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["API"],
        candidate_scores={"API": 90.0},
        confidence=90.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_api_confirmed)
    assert is_acc is False, "DIAGNOSE API at CONFIRMED level must NOT be acceptable"
    assert is_cw is True, "DIAGNOSE API at CONFIRMED level must be flagged CONFIDENT-WRONG"

    # 4. Prediction: DIAGNOSE DynamoDB (blamed healthy service, max_level='ABSTAIN') at 85.0 -> CONFIDENT-WRONG
    p_ddb_confirmed = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["DynamoDB"],
        candidate_scores={"DynamoDB": 85.0},
        confidence=85.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_ddb_confirmed)
    assert is_acc is False, "DIAGNOSE DynamoDB must NOT be acceptable"
    assert is_cw is True, "DIAGNOSE DynamoDB at 85.0 must be flagged CONFIDENT-WRONG"

    # 5. Test aggregate confident wrong rate metric computation
    cw_summary = compute_confident_wrong_rate([(case, p_api_confirmed), (case, p_abstain)])
    assert cw_summary.count_k == 1, "Exactly 1 out of 2 predictions must be confident-wrong"
    assert cw_summary.value == 50.0, "Confident-wrong rate must be 50.0%"


def test_multiroot_incomplete_scoring():
    """
    Tests that for multi-root cases:
    1. Full root set is the only fully correct outcome (is_acceptable=True).
    2. Single-root answers are scored as incomplete (is_acceptable=False, is_cw=False).
    3. Wrong answers at high confidence are confident-wrong (is_acceptable=False, is_cw=True).
    """
    topo = TopologyData(
        nodes=[TopologyNode(id="EC2", type="compute"), TopologyNode(id="DynamoDB", type="database"), TopologyNode(id="API", type="service")],
        edges=[["API", "EC2"], ["API", "DynamoDB"]]
    )

    gt = GroundTruth(
        true_root_causes=["EC2", "DynamoDB"],
        acceptable_outcomes=[
            AcceptableOutcome(verdict="DIAGNOSE", roots=["EC2", "DynamoDB"], max_level="CONFIRMED")
        ],
        max_level_by_root={"EC2": "CONFIRMED", "DynamoDB": "CONFIRMED"}
    )

    case = EvaluationCase(
        case_id="test_indep_015_multiroot",
        family_id="test_family",
        generator_version="test_v1",
        seed=15,
        topology=topo,
        events=[],
        ground_truth=gt,
        acceptable_outcomes=gt.acceptable_outcomes,
        max_level_by_root=gt.max_level_by_root
    )

    # 1. Full root set predicted: Acceptable
    p_full = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["EC2", "DynamoDB"],
        candidate_scores={"EC2": 90.0, "DynamoDB": 85.0},
        confidence=90.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_full)
    assert is_acc is True, "Full root set must be acceptable"
    assert is_cw is False

    # 2. Single root predicted: Incomplete (NOT acceptable, but NOT confident-wrong)
    p_single = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["EC2"],
        candidate_scores={"EC2": 90.0},
        confidence=90.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_single)
    assert is_acc is False, "Single root answer on two-root case must be scored incomplete (not acceptable)"
    assert is_cw is False, "Single root answer on two-root case must not be flagged confident-wrong"

    # 3. Non-root predicted at high score: Confident-wrong
    p_wrong = BaselineResult(
        baseline_name="test",
        case_id=case.case_id,
        verdict="DIAGNOSE",
        predicted_root_causes=["API"],
        candidate_scores={"API": 88.0},
        confidence=88.0
    )
    is_acc, is_cw = is_outcome_acceptable(case, p_wrong)
    assert is_acc is False
    assert is_cw is True, "Wrong candidate must be flagged confident-wrong"
