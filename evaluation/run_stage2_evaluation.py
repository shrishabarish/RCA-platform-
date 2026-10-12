import csv
import json
import math
import sys
import copy
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from evaluation.baselines.current_scorer import (
    CurrentScorerCaseTopologyBaseline,
    CurrentScorerStaticGraphBaseline
)
from evaluation.baselines.earliest_anomaly import EarliestAnomalyBaseline
from evaluation.baselines.highest_severity import HighestSeverityBaseline
from evaluation.baselines.pagerank_only import PageRankBaseline
from evaluation.baselines.controls import (
    AlwaysAbstainBaseline,
    NeverAbstainBaseline,
    ScoreThresholdGateBaseline
)
from evaluation.baselines.rca_v2_adapter import RCAv2Baseline
from rca_v2 import RCAv2Config, Verdict
from evaluation.metrics.engine import (
    compute_top_k_accuracy,
    compute_multiroot_f1,
    compute_abstention_metrics,
    compute_confident_wrong_rate,
    compute_paired_family_delta,
    bootstrap_ci_family_clustered,
    format_metric,
    format_delta
)


def run_stage2_evaluation():
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    hw_dir = PROJECT_ROOT / "evaluation" / "cases" / "handwritten"
    results_dir = PROJECT_ROOT / "evaluation" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    dev_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev_dir.glob("*.json"))]
    hw_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(hw_dir.glob("*.json"))]

    print(f"Loaded {len(dev_cases)} DEV cases, {len(hw_cases)} Hand-Written cases.")

    # Load frozen optimal config
    cfg_path = PROJECT_ROOT / "rca_v2" / "config.json"
    opt_cfg = RCAv2Config.load_from_file(cfg_path)

    # Baselines
    all_baselines = [
        CurrentScorerCaseTopologyBaseline(),
        CurrentScorerStaticGraphBaseline(),
        EarliestAnomalyBaseline(),
        HighestSeverityBaseline(),
        PageRankBaseline(),
        AlwaysAbstainBaseline(),
        NeverAbstainBaseline(),
        ScoreThresholdGateBaseline(threshold=73.0),
        RCAv2Baseline(opt_cfg),
    ]

    # Ablations
    ablations = [
        RCAv2Baseline(RCAv2Config(enable_source_health=False, min_score_diagnose=55.0, min_score_confirmed=75.0, min_coverage_confirmed=0.4, min_margin_confirmed=3.0), "no_source_health"),
        RCAv2Baseline(RCAv2Config(enable_coverage=False, min_score_diagnose=55.0, min_score_confirmed=75.0, min_coverage_confirmed=0.4, min_margin_confirmed=3.0), "no_coverage"),
        RCAv2Baseline(RCAv2Config(enable_contradictions=False, min_score_diagnose=55.0, min_score_confirmed=75.0, min_coverage_confirmed=0.4, min_margin_confirmed=3.0), "no_contradictions"),
        RCAv2Baseline(RCAv2Config(enable_margin=False, min_score_diagnose=55.0, min_score_confirmed=75.0, min_coverage_confirmed=0.4, min_margin_confirmed=3.0), "no_margin"),
    ]

    # 1. Run predictions on dev cases
    dev_preds: Dict[str, List[Tuple[EvaluationCase, BaselineResult]]] = {}
    for b in all_baselines + ablations:
        preds = []
        for c in dev_cases:
            p = b.predict(c)
            preds.append((c, p))
        dev_preds[b.name] = preds

    # 2. Save fresh DEV CSV
    dev_csv_path = results_dir / f"stage2b_{timestamp_str}.csv"
    canonical_dev_csv_path = results_dir / "stage2_dev.csv"

    fieldnames = [
        "case_id", "family_id", "base_case_id", "split", "topology_size", "perturbation",
        "is_hard", "expected_verdict", "true_roots", "num_true_roots"
    ]
    for b in all_baselines + ablations:
        fieldnames.extend([
            f"{b.name}_verdict",
            f"{b.name}_pred_top1",
            f"{b.name}_pred_set",
            f"{b.name}_confidence",
            f"{b.name}_correct",
            f"{b.name}_confident_wrong"
        ])

    from evaluation.metrics.engine import extract_predicted_root_set
    rows = []
    for idx, c in enumerate(dev_cases):
        tr = c.ground_truth.true_root_causes
        pert = c.perturbations[0] if c.perturbations else "base"
        row = {
            "case_id": c.case_id,
            "family_id": c.family_id,
            "base_case_id": c.base_case_id or "",
            "split": "dev",
            "topology_size": len(c.topology.nodes),
            "perturbation": pert,
            "is_hard": c.is_hard,
            "expected_verdict": c.ground_truth.expected_verdict,
            "true_roots": ";".join(tr),
            "num_true_roots": len(tr)
        }
        for b in all_baselines + ablations:
            _, p = dev_preds[b.name][idx]
            top1 = p.predicted_root_causes[0] if p.predicted_root_causes else "NONE"
            pred_set = extract_predicted_root_set(p, alpha=0.85, max_k=3, oracle_len=len(tr))
            if c.ground_truth.expected_verdict == "ABSTAIN":
                corr = (p.verdict == "ABSTAIN")
                is_wrong = (p.verdict != "ABSTAIN")
            else:
                corr = (top1 in tr) if len(tr) == 1 else False
                is_wrong = (top1 not in tr)
            is_cw = is_wrong and (p.confidence >= 75.0)

            row[f"{b.name}_verdict"] = p.verdict
            row[f"{b.name}_pred_top1"] = top1
            row[f"{b.name}_pred_set"] = ";".join(pred_set)
            row[f"{b.name}_confidence"] = round(p.confidence, 1)
            row[f"{b.name}_correct"] = corr
            row[f"{b.name}_confident_wrong"] = is_cw
        rows.append(row)

    for target_path in [dev_csv_path, canonical_dev_csv_path]:
        with open(target_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    print(f"Saved fresh Stage 2b DEV results to {dev_csv_path}")

    # 3. Run predictions on handwritten cases
    hw_preds: Dict[str, List[Tuple[EvaluationCase, BaselineResult]]] = {}
    for b in all_baselines:
        preds = []
        for c in hw_cases:
            p = b.predict(c)
            preds.append((c, p))
        hw_preds[b.name] = preds

    hw_csv_path = results_dir / f"stage2b_handwritten_{timestamp_str}.csv"
    canonical_hw_csv_path = results_dir / "stage2_handwritten.csv"

    hw_fieldnames = [
        "case_id", "family_id", "expected_verdict", "true_roots",
        "rca_v2_verdict", "rca_v2_top1", "rca_v2_conf",
        "CS_top1", "CS_conf", "match_ground_truth"
    ]
    hw_rows = []
    for idx, c in enumerate(hw_cases):
        tr = c.ground_truth.true_root_causes
        _, p_v2 = hw_preds["rca_v2"][idx]
        _, p_cs = hw_preds["CurrentScorer_CaseTopology"][idx]
        v2_top = p_v2.predicted_root_causes[0] if p_v2.predicted_root_causes else "NONE"
        cs_top = p_cs.predicted_root_causes[0] if p_cs.predicted_root_causes else "NONE"

        if c.ground_truth.expected_verdict == "ABSTAIN":
            match = (p_v2.verdict == "ABSTAIN")
        else:
            match = (v2_top in tr)

        hw_rows.append({
            "case_id": c.case_id,
            "family_id": c.family_id,
            "expected_verdict": c.ground_truth.expected_verdict,
            "true_roots": ";".join(tr),
            "rca_v2_verdict": p_v2.verdict,
            "rca_v2_top1": v2_top,
            "rca_v2_conf": round(p_v2.confidence, 1),
            "CS_top1": cs_top,
            "CS_conf": round(p_cs.confidence, 1),
            "match_ground_truth": match
        })

    for target_path in [hw_csv_path, canonical_hw_csv_path]:
        with open(target_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=hw_fieldnames)
            writer.writeheader()
            writer.writerows(hw_rows)
    print(f"Saved fresh Stage 2b Hand-Written results to {hw_csv_path}")

    return timestamp_str, dev_csv_path, hw_csv_path


if __name__ == "__main__":
    run_stage2_evaluation()
