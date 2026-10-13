import pytest
from evaluation.schema import EvaluationCase, GroundTruth, AcceptableOutcome, TopologyData
from evaluation.strip_case import strip_case_for_engine


def test_strip_case_for_engine():
    # Construct a dummy case with full hints and metadata
    gt = GroundTruth(
        true_root_causes=["DynamoDB"],
        identifiability_note="Root is DynamoDB because of storage contention",
        acceptable_outcomes=[
            AcceptableOutcome(verdict="DIAGNOSE", roots=["DynamoDB"], max_level="CONFIRMED")
        ],
        max_level_by_root={"DynamoDB": "CONFIRMED"}
    )
    topo = TopologyData(nodes=[], edges=[])
    case = EvaluationCase(
        case_id="indep_test_001",
        family_id="indep_family",
        seed=42,
        topology=topo,
        events=[],
        source_health=[],
        ground_truth=gt,
        perturbations=["root_not_earliest", "slow_burn"],
        tags=["independent", "decoy", "6node"],
        identifiability_note="Identifiability note at case level",
        acceptable_outcomes=[
            AcceptableOutcome(verdict="DIAGNOSE", roots=["DynamoDB"], max_level="CONFIRMED")
        ],
        max_level_by_root={"DynamoDB": "CONFIRMED"}
    )

    stripped = strip_case_for_engine(case)

    # Prove that engine receives ZERO category, hint, or outcome fields
    assert stripped.tags == [], "Tags must be empty"
    assert stripped.perturbations == [], "Perturbations must be empty"
    assert stripped.identifiability_note is None, "Case identifiability_note must be None"
    assert stripped.acceptable_outcomes == [], "Case acceptable_outcomes must be empty"
    assert stripped.max_level_by_root == {}, "Case max_level_by_root must be empty"

    assert stripped.ground_truth.identifiability_note is None, "GT identifiability_note must be None"
    assert stripped.ground_truth.acceptable_outcomes == [], "GT acceptable_outcomes must be empty"
    assert stripped.ground_truth.max_level_by_root == {}, "GT max_level_by_root must be empty"

    # Core topology, case id, and events are preserved
    assert stripped.case_id == "indep_test_001"
    assert stripped.topology == topo
