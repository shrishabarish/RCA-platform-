import pytest
from evaluation.schema import EvaluationCase, GroundTruth, TopologyData
from evaluation.baselines.adapter import BaselineResult
from evaluation.metrics.engine import (
    compute_top_k_accuracy,
    compute_multiroot_f1,
    compute_abstention_metrics,
    compute_confident_wrong_rate,
)

DUMMY_TOPO = TopologyData(nodes=[], edges=[])


def make_dummy_case(case_id: str, true_roots: list, expected_verdict: str = "DIAGNOSE") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        family_id="fam_dummy",
        seed=1,
        topology=DUMMY_TOPO,
        events=[],
        ground_truth=GroundTruth(
            true_root_causes=true_roots,
            expected_verdict=expected_verdict
        )
    )


def test_top_1_and_top_3_hand_computed():
    """
    Hand-computed test:
    Case 1: True: ['A'], Pred: ['A', 'B', 'C'] -> Top-1 HIT, Top-3 HIT
    Case 2: True: ['B'], Pred: ['A', 'B', 'C'] -> Top-1 MISS, Top-3 HIT
    Case 3: True: ['D'], Pred: ['A', 'B', 'C'] -> Top-1 MISS, Top-3 MISS
    Expected:
    Top-1 Accuracy = 1/3 = 33.33%
    Top-3 Accuracy = 2/3 = 66.67%
    """
    c1 = make_dummy_case("c1", ["A"])
    p1 = BaselineResult(baseline_name="test", case_id="c1", predicted_root_causes=["A", "B", "C"], candidate_scores={}, confidence=90.0)

    c2 = make_dummy_case("c2", ["B"])
    p2 = BaselineResult(baseline_name="test", case_id="c2", predicted_root_causes=["A", "B", "C"], candidate_scores={}, confidence=80.0)

    c3 = make_dummy_case("c3", ["D"])
    p3 = BaselineResult(baseline_name="test", case_id="c3", predicted_root_causes=["A", "B", "C"], candidate_scores={}, confidence=70.0)

    pairs = [(c1, p1), (c2, p2), (c3, p3)]

    top1 = compute_top_k_accuracy(pairs, k=1)
    assert top1.value == 33.33
    assert top1.sample_size == 3
    assert top1.flag_low_sample is True

    top3 = compute_top_k_accuracy(pairs, k=3)
    assert top3.value == 66.67
    assert top3.sample_size == 3


def test_multiroot_f1_hand_computed():
    """
    Hand-computed test:
    Case 1: True: ['A', 'B'], Pred: ['A', 'B', 'C'] -> Pred top 2: {'A', 'B'}. Overlap=2 -> F1 = 1.0 (100%)
    Case 2: True: ['A', 'B'], Pred: ['A', 'C', 'D'] -> Pred top 2: {'A', 'C'}. Overlap=1 -> P=0.5, R=0.5, F1 = 0.5 (50%)
    Expected Mean F1 = (1.0 + 0.5) / 2 = 75.0%
    """
    c1 = make_dummy_case("c1", ["A", "B"])
    p1 = BaselineResult(baseline_name="test", case_id="c1", predicted_root_causes=["A", "B", "C"], candidate_scores={}, confidence=85.0)

    c2 = make_dummy_case("c2", ["A", "B"])
    p2 = BaselineResult(baseline_name="test", case_id="c2", predicted_root_causes=["A", "C", "D"], candidate_scores={}, confidence=75.0)

    pairs = [(c1, p1), (c2, p2)]
    res = compute_multiroot_f1(pairs)
    assert res.value == 75.0
    assert res.sample_size == 2


def test_abstention_precision_recall_hand_computed():
    """
    Hand-computed test:
    Case 1: True: ABSTAIN, Pred: ABSTAIN -> Correct abstention
    Case 2: True: DIAGNOSE, Pred: ABSTAIN -> False alarm abstention
    Case 3: True: ABSTAIN, Pred: DIAGNOSE -> Failed abstention
    Expected:
    All abstentions = 2. Correct = 1. Precision = 1/2 = 50.0%
    Cases requiring abstention = 2. Correct = 1. Recall = 1/2 = 50.0%
    """
    c1 = make_dummy_case("c1", [], expected_verdict="ABSTAIN")
    p1 = BaselineResult(baseline_name="test", case_id="c1", verdict="ABSTAIN", predicted_root_causes=[], candidate_scores={}, confidence=0.0)

    c2 = make_dummy_case("c2", ["A"], expected_verdict="DIAGNOSE")
    p2 = BaselineResult(baseline_name="test", case_id="c2", verdict="ABSTAIN", predicted_root_causes=[], candidate_scores={}, confidence=0.0)

    c3 = make_dummy_case("c3", [], expected_verdict="ABSTAIN")
    p3 = BaselineResult(baseline_name="test", case_id="c3", verdict="DIAGNOSE", predicted_root_causes=["A"], candidate_scores={}, confidence=60.0)

    pairs = [(c1, p1), (c2, p2), (c3, p3)]
    prec, rec = compute_abstention_metrics(pairs)
    assert prec.value == 50.0
    assert rec.value == 50.0


def test_confident_wrong_rate_hand_computed():
    """
    Hand-computed test with threshold = 80.0:
    Case 1: Wrong root, confidence = 90.0 (>= 80) -> Confident Wrong (1)
    Case 2: Wrong root, confidence = 65.0 (< 80)  -> Low-confidence Wrong (0)
    Case 3: Correct root, confidence = 95.0 (>= 80) -> Correct (0)
    Case 4: Correct root, confidence = 50.0 (< 80)  -> Correct (0)
    Expected Confident-Wrong Rate = 1/4 = 25.0%
    """
    c1 = make_dummy_case("c1", ["A"])
    p1 = BaselineResult(baseline_name="test", case_id="c1", predicted_root_causes=["B"], candidate_scores={}, confidence=90.0)

    c2 = make_dummy_case("c2", ["A"])
    p2 = BaselineResult(baseline_name="test", case_id="c2", predicted_root_causes=["C"], candidate_scores={}, confidence=65.0)

    c3 = make_dummy_case("c3", ["A"])
    p3 = BaselineResult(baseline_name="test", case_id="c3", predicted_root_causes=["A"], candidate_scores={}, confidence=95.0)

    c4 = make_dummy_case("c4", ["A"])
    p4 = BaselineResult(baseline_name="test", case_id="c4", predicted_root_causes=["A"], candidate_scores={}, confidence=50.0)

    pairs = [(c1, p1), (c2, p2), (c3, p3), (c4, p4)]
    res = compute_confident_wrong_rate(pairs, confidence_threshold=80.0)
    assert res.value == 25.0
    assert res.sample_size == 4
