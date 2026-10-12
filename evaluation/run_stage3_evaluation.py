import csv
import json
import math
import sys
import copy
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional

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
from evaluation.baselines.controls import ScoreThresholdGateBaseline
from evaluation.baselines.rca_v2_adapter import RCAv2Baseline
from evaluation.baselines.rca_v3_adapter import RCAv3Baseline
from rca_v2 import RCAv2Config, Verdict as VerdictV2
from rca_v3 import RCAv3Config, Verdict as VerdictV3
from evaluation.metrics.engine import (
    bootstrap_ci_family_clustered,
    format_metric,
    format_delta
)


def extract_predicted_root_set(result: BaselineResult, alpha: float = 0.85, max_k: int = 3, oracle_len: int = 1) -> List[str]:
    """Extracts set of predicted root causes."""
    if result.baseline_name == "rca_v3" or result.baseline_name.startswith("rca_v3_"):
        # For rca_v3, the predicted_root_causes list already contains the root set hypotheses
        return result.predicted_root_causes if result.predicted_root_causes else ["NONE"]

    # For legacy engines, use greedy thresholding
    if not result.candidate_scores:
        return result.predicted_root_causes[:max_k] if result.predicted_root_causes else ["NONE"]
    scores = sorted(result.candidate_scores.items(), key=lambda x: -x[1])
    top_score = scores[0][1]
    if top_score <= 0.0:
        return [scores[0][0]]
    threshold = top_score * alpha
    cand_set = [svc for svc, sc in scores if sc >= threshold]
    return cand_set[:max_k]


def compute_set_f1(pred_set: List[str], true_roots: List[str]) -> Tuple[float, float, float]:
    p_set = set(pred_set)
    t_set = set(true_roots)
    if not p_set or not t_set:
        return 0.0, 0.0, 0.0
    prec = len(p_set & t_set) / len(p_set)
    rec = len(p_set & t_set) / len(t_set)
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return prec, rec, f1


def evaluate_stage3():
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = PROJECT_ROOT / "evaluation" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    val_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev_validation"
    hw_dir = PROJECT_ROOT / "evaluation" / "cases" / "handwritten"

    dev_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev_dir.glob("*.json"))]
    val_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val_dir.glob("*.json"))]
    hw_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(hw_dir.glob("*.json"))]

    print(f"Loaded {len(dev_cases)} DEV cases, {len(val_cases)} DEV-VALIDATION cases, {len(hw_cases)} HANDWRITTEN cases.")

    # Configurations
    cfg_v2 = RCAv2Config.load_from_file(PROJECT_ROOT / "rca_v2" / "config.json")
    cfg_v3 = RCAv3Config()

    # Models to evaluate
    models: List[RCABaselineAdapter] = [
        CurrentScorerCaseTopologyBaseline(),
        EarliestAnomalyBaseline(),
        ScoreThresholdGateBaseline(threshold=73.0),
        RCAv2Baseline(cfg_v2),
        RCAv3Baseline(cfg_v3),
    ]

    # Ablations of rca_v3 (one per component a through e)
    ablations: List[RCABaselineAdapter] = [
        RCAv3Baseline(RCAv3Config(enable_graph_candidates=False), name_suffix="no_graph_candidates"),
        RCAv3Baseline(RCAv3Config(enable_symptom_scoring=False), name_suffix="no_symptom_scoring"),
        RCAv3Baseline(RCAv3Config(enable_set_root_selection=False), name_suffix="no_set_selection"),
        RCAv3Baseline(RCAv3Config(enable_directed_relatedness=False), name_suffix="no_directed_relatedness"),
        RCAv3Baseline(RCAv3Config(enable_principled_verdicts=False), name_suffix="no_principled_verdicts"),
    ]

    all_evaluators = models + ablations

    def run_split(cases: List[EvaluationCase], split_name: str, csv_path: Path):
        print(f"\nRunning predictions on {split_name} split ({len(cases)} cases)...")
        preds: Dict[str, List[Tuple[EvaluationCase, BaselineResult]]] = {}
        for m in all_evaluators:
            preds[m.name] = [(c, m.predict(c)) for c in cases]

        # Build CSV rows
        fieldnames = [
            "case_id", "family_id", "base_case_id", "split", "topology_size", "perturbation",
            "is_hard", "expected_verdict", "true_roots", "num_true_roots"
        ]
        for m in all_evaluators:
            fieldnames.extend([
                f"{m.name}_verdict",
                f"{m.name}_pred_top1",
                f"{m.name}_pred_set",
                f"{m.name}_confidence",
                f"{m.name}_correct",
                f"{m.name}_f1"
            ])

        rows = []
        for idx, c in enumerate(cases):
            tr = c.ground_truth.true_root_causes
            pert = c.perturbations[0] if c.perturbations else "base"
            row = {
                "case_id": c.case_id,
                "family_id": c.family_id,
                "base_case_id": c.base_case_id or "",
                "split": split_name,
                "topology_size": len(c.topology.nodes),
                "perturbation": pert,
                "is_hard": c.is_hard,
                "expected_verdict": c.ground_truth.expected_verdict,
                "true_roots": ";".join(tr),
                "num_true_roots": len(tr)
            }
            for m in all_evaluators:
                _, p = preds[m.name][idx]
                top1 = p.predicted_root_causes[0] if p.predicted_root_causes else "NONE"
                pred_set = extract_predicted_root_set(p, alpha=0.85, max_k=3, oracle_len=len(tr))
                _, _, f1_val = compute_set_f1(pred_set, tr)

                if c.ground_truth.expected_verdict == "ABSTAIN":
                    corr = (p.verdict == "ABSTAIN")
                else:
                    corr = (top1 in tr) if len(tr) == 1 else False

                row[f"{m.name}_verdict"] = p.verdict
                row[f"{m.name}_pred_top1"] = top1
                row[f"{m.name}_pred_set"] = ";".join(pred_set)
                row[f"{m.name}_confidence"] = round(p.confidence, 1)
                row[f"{m.name}_correct"] = corr
                row[f"{m.name}_f1"] = round(f1_val, 4)
            rows.append(row)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Saved {split_name} results to {csv_path}")
        return preds, rows

    dev_csv_path = results_dir / f"stage3_dev_{timestamp_str}.csv"
    val_csv_path = results_dir / f"stage3_dev_val_{timestamp_str}.csv"
    hw_csv_path = results_dir / f"stage3_handwritten_{timestamp_str}.csv"

    dev_preds, dev_rows = run_split(dev_cases, "dev", dev_csv_path)
    val_preds, val_rows = run_split(val_cases, "dev_validation", val_csv_path)
    hw_preds, hw_rows = run_split(hw_cases, "handwritten", hw_csv_path)

    # =========================================================================
    # SCRIPT-GENERATED REPORT TABLES
    # =========================================================================
    print("\n" + "=" * 90)
    print("STAGE 3 FORMAL BENCHMARK EVALUATION RESULTS")
    print(f"Timestamp: {timestamp_str}")
    print(f"DEV CSV: {dev_csv_path}")
    print(f"DEV-VALIDATION CSV: {val_csv_path}")
    print("=" * 90)

    # -------------------------------------------------------------------------
    # TABLE 1: Overall Results (DEV and DEV-VALIDATION)
    # -------------------------------------------------------------------------
    print("\n--- TABLE 1: OVERALL RESULTS ---")
    for split_label, p_dict, c_list in [("DEV (N=125)", dev_preds, dev_cases), ("DEV-VALIDATION (N=106)", val_preds, val_cases)]:
        print(f"\nSplit: {split_label}")
        # Single-root diagnosable cases
        diag_single = [c for c in c_list if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
        n_diag = len(diag_single)
        print(f"{'Model':<35} | {'Top-1 Accuracy':<22} | {'Confident-Wrong':<18} | {'Abstention Acc':<16}")
        print("-" * 95)
        for m in models:
            # Top-1
            k_top1 = sum(1 for c, p in p_dict[m.name] if c in diag_single and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
            top1_str = format_metric(k_top1, n_diag, (k_top1 / n_diag * 100) if n_diag else 0.0)

            # Confident-wrong: wrong & confidence >= 75.0 over all N cases
            cw_k = sum(1 for c, p in p_dict[m.name] if (
                (c.ground_truth.expected_verdict == "ABSTAIN" and p.verdict != "ABSTAIN") or
                (c.ground_truth.expected_verdict == "DIAGNOSE" and (not p.predicted_root_causes or p.predicted_root_causes[0] not in c.ground_truth.true_root_causes))
            ) and p.confidence >= 75.0)
            cw_str = format_metric(cw_k, len(c_list), (cw_k / len(c_list) * 100))

            # Abstention accuracy on abstain-required cases
            abst_cases = [c for c in c_list if c.ground_truth.expected_verdict == "ABSTAIN"]
            k_abst = sum(1 for c, p in p_dict[m.name] if c in abst_cases and p.verdict == "ABSTAIN")
            abst_str = format_metric(k_abst, len(abst_cases), (k_abst / len(abst_cases) * 100) if abst_cases else 0.0)

            print(f"{m.name:<35} | {top1_str:<22} | {cw_str:<18} | {abst_str:<16}")

    # -------------------------------------------------------------------------
    # TABLE 2: Perturbation Breakdown on Diagnosable DEV Cases
    # -------------------------------------------------------------------------
    print("\n--- TABLE 2: PERTURBATION BREAKDOWN ON DEV DIAGNOSABLE CASES ---")
    dev_diag = [c for c in dev_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
    perts = sorted(list(set(c.perturbations[0] if c.perturbations else "base" for c in dev_diag)))
    print(f"{'Perturbation':<32} | {'N':<4} | {'Current Scorer':<16} | {'RCA v2':<16} | {'RCA v3':<16}")
    print("-" * 92)
    for pert in perts:
        pert_cases = [c for c in dev_diag if (c.perturbations[0] if c.perturbations else "base") == pert]
        n_p = len(pert_cases)
        k_curr = sum(1 for c, p in dev_preds["CurrentScorer_CaseTopology"] if c in pert_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        k_v2 = sum(1 for c, p in dev_preds["rca_v2"] if c in pert_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        k_v3 = sum(1 for c, p in dev_preds["rca_v3"] if c in pert_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        print(f"{pert:<32} | {n_p:<4} | {format_metric(k_curr, n_p, k_curr/n_p*100):<16} | {format_metric(k_v2, n_p, k_v2/n_p*100):<16} | {format_metric(k_v3, n_p, k_v3/n_p*100):<16}")

    # -------------------------------------------------------------------------
    # TABLE 3: Hard Subset Top-1 Performance
    # -------------------------------------------------------------------------
    print("\n--- TABLE 3: HARD SUBSET TOP-1 PERFORMANCE ---")
    for split_label, p_dict, c_list in [("DEV", dev_preds, dev_cases), ("DEV-VALIDATION", val_preds, val_cases)]:
        hard_cases = [c for c in c_list if c.is_hard and c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_h = len(hard_cases)
        print(f"\nSplit: {split_label} (N={n_h} hard cases)")
        for m in [CurrentScorerCaseTopologyBaseline(), RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            k_h = sum(1 for c, p in p_dict[m.name] if c in hard_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
            print(f"  {m.name:<32}: {format_metric(k_h, n_h, (k_h/n_h*100) if n_h else 0.0)}")

    # -------------------------------------------------------------------------
    # TABLE 4: Edge-Root Families Top-1 Performance
    # -------------------------------------------------------------------------
    print("\n--- TABLE 4: EDGE-ROOT FAMILIES TOP-1 PERFORMANCE ---")
    # Dev edge-root families: 006, 013, 024, 026, 028, 034
    dev_edge_fams = {"fam_gen_006", "fam_gen_013", "fam_gen_024", "fam_gen_026", "fam_gen_028", "fam_gen_034"}
    dev_edge_cases = [c for c in dev_cases if c.family_id in dev_edge_fams and c.ground_truth.expected_verdict == "DIAGNOSE"]
    val_edge_fams = {f"fam_dev_val_{i:03d}" for i in range(90, 96)}
    val_edge_cases = [c for c in val_cases if c.family_id in val_edge_fams and c.ground_truth.expected_verdict == "DIAGNOSE"]

    for split_label, p_dict, edge_cases in [("DEV (6 families)", dev_preds, dev_edge_cases), ("DEV-VALIDATION (6 families)", val_preds, val_edge_cases)]:
        n_e = len(edge_cases)
        print(f"\nSplit: {split_label} (N={n_e} edge-root cases)")
        for m in [CurrentScorerCaseTopologyBaseline(), RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            k_e = sum(1 for c, p in p_dict[m.name] if c in edge_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
            print(f"  {m.name:<32}: {format_metric(k_e, n_e, (k_e/n_e*100) if n_e else 0.0)}")

    # -------------------------------------------------------------------------
    # TABLE 5: Topology Size Breakdown
    # -------------------------------------------------------------------------
    print("\n--- TABLE 5: TOPOLOGY SIZE BREAKDOWN (DEV-VALIDATION) ---")
    val_diag = [c for c in val_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
    for sz in [6, 12, 20]:
        sz_cases = [c for c in val_diag if len(c.topology.nodes) == sz]
        n_sz = len(sz_cases)
        print(f"Topology Size {sz} Nodes (N={n_sz}):")
        for m in [CurrentScorerCaseTopologyBaseline(), RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            k_sz = sum(1 for c, p in val_preds[m.name] if c in sz_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
            print(f"  {m.name:<32}: {format_metric(k_sz, n_sz, (k_sz/n_sz*100) if n_sz else 0.0)}")

    # -------------------------------------------------------------------------
    # TABLE 6: Multi-Root Separation (Set F1 reported as a mean across multi-root cases)
    # -------------------------------------------------------------------------
    print("\n--- TABLE 6: MULTI-ROOT PERFORMANCE (SET F1 REPORTED AS MEAN) ---")
    for split_label, p_dict, c_list in [("DEV", dev_preds, dev_cases), ("DEV-VALIDATION", val_preds, val_cases)]:
        multi_cases = [c for c in c_list if len(c.ground_truth.true_root_causes) > 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_m = len(multi_cases)
        print(f"\nSplit: {split_label} (N={n_m} multi-root cases)")
        for m in [CurrentScorerCaseTopologyBaseline(), RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            f1s = []
            for c in multi_cases:
                _, p = next((x for x in p_dict[m.name] if x[0].case_id == c.case_id))
                pred_set = extract_predicted_root_set(p, alpha=0.85, max_k=3, oracle_len=len(c.ground_truth.true_root_causes))
                _, _, f1_val = compute_set_f1(pred_set, c.ground_truth.true_root_causes)
                f1s.append(f1_val)
            mean_f1 = sum(f1s) / len(f1s) if f1s else 0.0
            print(f"  {m.name:<32}: Mean Set F1 = {mean_f1*100:.2f}% (range {min(f1s)*100:.1f}% - {max(f1s)*100:.1f}%)")

    # -------------------------------------------------------------------------
    # TABLE 7: Abstention Performance (k/n, Precision, Recall, Diagnosable Base Checks)
    # -------------------------------------------------------------------------
    print("\n--- TABLE 7: ABSTENTION PERFORMANCE ---")
    for split_label, p_dict, c_list in [("DEV (N=125)", dev_preds, dev_cases), ("DEV-VALIDATION (N=106)", val_preds, val_cases)]:
        print(f"\nSplit: {split_label}")
        abst_cases = [c for c in c_list if c.ground_truth.expected_verdict == "ABSTAIN"]
        diag_cases = [c for c in c_list if c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_abst = len(abst_cases)
        n_diag = len(diag_cases)

        print(f"{'Model':<32} | {'Abst Recall':<16} | {'Abst Prec':<16} | {'False Abst on Diag':<20}")
        print("-" * 90)
        for m in [CurrentScorerCaseTopologyBaseline(), ScoreThresholdGateBaseline(73.0), RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            tp = sum(1 for c, p in p_dict[m.name] if c in abst_cases and p.verdict == "ABSTAIN")
            fp = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.verdict == "ABSTAIN")
            fn = n_abst - tp
            rec = tp / n_abst if n_abst else 0.0
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec_str = format_metric(tp, n_abst, rec * 100)
            prec_str = format_metric(tp, tp + fp, prec * 100)
            fp_str = format_metric(fp, n_diag, fp / n_diag * 100)
            print(f"{m.name:<32} | {rec_str:<16} | {prec_str:<16} | {fp_str:<20}")

    # -------------------------------------------------------------------------
    # TABLE 8: CONFIRMED Precision (k/n)
    # -------------------------------------------------------------------------
    print("\n--- TABLE 8: CONFIRMED VERDICT PRECISION ---")
    for split_label, p_dict, c_list in [("DEV", dev_preds, dev_cases), ("DEV-VALIDATION", val_preds, val_cases)]:
        diag_single = [c for c in c_list if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
        print(f"\nSplit: {split_label}:")
        for m in [RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
            confirmed_cases = []
            for c, p in p_dict[m.name]:
                # Check underlying verdict
                v = p.metadata.get(f"{m.name}_verdict") or p.metadata.get("rca_v2_verdict") or p.metadata.get("rca_v3_verdict")
                if v == "CONFIRMED":
                    confirmed_cases.append((c, p))
            n_conf = len(confirmed_cases)
            k_correct = sum(1 for c, p in confirmed_cases if (c in diag_single and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
            prec = (k_correct / n_conf * 100) if n_conf else 0.0
            print(f"  {m.name:<32}: Precision = {format_metric(k_correct, n_conf, prec)} (Total CONFIRMED n={n_conf})")

    # -------------------------------------------------------------------------
    # TABLE 9: Verdict-Based Risk-Coverage Curve
    # -------------------------------------------------------------------------
    print("\n--- TABLE 9: VERDICT-BASED RISK-COVERAGE CURVE (DEV-VALIDATION DIAGNOSABLE) ---")
    val_diag_single = [c for c in val_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
    n_vds = len(val_diag_single)
    print(f"Total Diagnosable Single-Root Cases = {n_vds}")
    for m in [RCAv2Baseline(cfg_v2), RCAv3Baseline(cfg_v3)]:
        print(f"\nModel: {m.name}")
        for tier in ["CONFIRMED only", "CONFIRMED + PROBABLE", "All Diagnosed"]:
            if tier == "CONFIRMED only":
                sub = [(c, p) for c, p in val_preds[m.name] if c in val_diag_single and (p.metadata.get(f"{m.name}_verdict") or p.metadata.get("rca_v2_verdict") or p.metadata.get("rca_v3_verdict")) == "CONFIRMED"]
            elif tier == "CONFIRMED + PROBABLE":
                sub = [(c, p) for c, p in val_preds[m.name] if c in val_diag_single and (p.metadata.get(f"{m.name}_verdict") or p.metadata.get("rca_v2_verdict") or p.metadata.get("rca_v3_verdict")) in ("CONFIRMED", "PROBABLE")]
            else:
                sub = [(c, p) for c, p in val_preds[m.name] if c in val_diag_single and p.verdict == "DIAGNOSE"]
            n_sub = len(sub)
            k_corr = sum(1 for c, p in sub if p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            cov_pct = (n_sub / n_vds * 100) if n_vds else 0.0
            acc_pct = (k_corr / n_sub * 100) if n_sub else 0.0
            print(f"  {tier:<24}: Coverage = {format_metric(n_sub, n_vds, cov_pct)} | Top-1 Accuracy = {format_metric(k_corr, n_sub, acc_pct)}")

    # -------------------------------------------------------------------------
    # TABLE 10: Paired Family-Clustered Deltas against Comparators
    # -------------------------------------------------------------------------
    print("\n--- TABLE 10: PAIRED FAMILY-CLUSTERED DELTAS ON DEV-VALIDATION ---")
    val_diag_all = [c for c in val_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
    val_hard = [c for c in val_diag_all if c.is_hard or c.family_id in val_edge_fams]

    for subset_name, sub_cases in [("Hard + Edge-Root Subset (H4)", val_hard), ("All Diagnosable Cases (H9)", val_diag_all)]:
        print(f"\nSubset: {subset_name} (N={len(sub_cases)} cases):")
        # Build family-clustered deltas
        fam_deltas_curr = defaultdict(list)
        fam_deltas_v2 = defaultdict(list)
        fam_deltas_gate = defaultdict(list)

        for c in sub_cases:
            _, p_v3 = next(x for x in val_preds["rca_v3"] if x[0].case_id == c.case_id)
            _, p_curr = next(x for x in val_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
            _, p_v2 = next(x for x in val_preds["rca_v2"] if x[0].case_id == c.case_id)
            _, p_gate = next(x for x in val_preds["Control_ScoreThresholdGate_t73"] if x[0].case_id == c.case_id)

            corr_v3 = 1.0 if (p_v3.predicted_root_causes and p_v3.predicted_root_causes[0] in c.ground_truth.true_root_causes) else 0.0
            corr_curr = 1.0 if (p_curr.predicted_root_causes and p_curr.predicted_root_causes[0] in c.ground_truth.true_root_causes) else 0.0
            corr_v2 = 1.0 if (p_v2.predicted_root_causes and p_v2.predicted_root_causes[0] in c.ground_truth.true_root_causes) else 0.0
            corr_gate = 1.0 if (p_gate.predicted_root_causes and p_gate.predicted_root_causes[0] in c.ground_truth.true_root_causes) else 0.0

            fam_deltas_curr[c.family_id].append(corr_v3 - corr_curr)
            fam_deltas_v2[c.family_id].append(corr_v3 - corr_v2)
            fam_deltas_gate[c.family_id].append(corr_v3 - corr_gate)

        d_curr_mean = (sum(v for vals in fam_deltas_curr.values() for v in vals) / len(sub_cases)) * 100
        ci_curr_lo, ci_curr_hi = bootstrap_ci_family_clustered(fam_deltas_curr)
        ci_curr_lo *= 100
        ci_curr_hi *= 100

        d_v2_mean = (sum(v for vals in fam_deltas_v2.values() for v in vals) / len(sub_cases)) * 100
        ci_v2_lo, ci_v2_hi = bootstrap_ci_family_clustered(fam_deltas_v2)
        ci_v2_lo *= 100
        ci_v2_hi *= 100

        d_gate_mean = (sum(v for vals in fam_deltas_gate.values() for v in vals) / len(sub_cases)) * 100
        ci_gate_lo, ci_gate_hi = bootstrap_ci_family_clustered(fam_deltas_gate)
        ci_gate_lo *= 100
        ci_gate_hi *= 100

        print(f"  vs Current Scorer : {format_delta(d_curr_mean, ci_curr_lo, ci_curr_hi, len(sub_cases))}")
        print(f"  vs RCA v2         : {format_delta(d_v2_mean, ci_v2_lo, ci_v2_hi, len(sub_cases))}")
        print(f"  vs Score Gate (73): {format_delta(d_gate_mean, ci_gate_lo, ci_gate_hi, len(sub_cases))}")

    # -------------------------------------------------------------------------
    # TABLE 11: Ablation Analysis (Components a through e)
    # -------------------------------------------------------------------------
    print("\n--- TABLE 11: ABLATION ANALYSIS (COMPONENTS A THROUGH E) ---")
    print(f"{'Model / Ablation':<38} | {'Dev Top-1':<12} | {'Val Top-1':<12} | {'Val Multi F1':<14} | {'Val Abst Recall':<16} | {'Status'}")
    print("-" * 110)

    full_v3_val_top1 = sum(1 for c, p in val_preds["rca_v3"] if c in val_diag_all and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    
    for m in [RCAv3Baseline(cfg_v3)] + ablations:
        k_dev = sum(1 for c, p in dev_preds[m.name] if c in dev_diag and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        k_val = sum(1 for c, p in val_preds[m.name] if c in val_diag_all and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        
        # Multi F1
        val_multi = [c for c in val_cases if len(c.ground_truth.true_root_causes) > 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
        multi_f1s = []
        for c in val_multi:
            _, p = next(x for x in val_preds[m.name] if x[0].case_id == c.case_id)
            pred_set = extract_predicted_root_set(p, alpha=0.85, max_k=3, oracle_len=len(c.ground_truth.true_root_causes))
            _, _, f1_val = compute_set_f1(pred_set, c.ground_truth.true_root_causes)
            multi_f1s.append(f1_val)
        mean_multi_f1 = (sum(multi_f1s) / len(multi_f1s) * 100) if multi_f1s else 0.0

        # Abst recall
        val_abst = [c for c in val_cases if c.ground_truth.expected_verdict == "ABSTAIN"]
        k_abst = sum(1 for c, p in val_preds[m.name] if c in val_abst and p.verdict == "ABSTAIN")
        abst_rec = (k_abst / len(val_abst) * 100) if val_abst else 0.0

        # Check if ablation changes any result compared to full rca_v3
        diff_count = 0
        for idx in range(len(val_cases)):
            _, p_full = val_preds["rca_v3"][idx]
            _, p_abl = val_preds[m.name][idx]
            if p_full.predicted_root_causes != p_abl.predicted_root_causes or p_full.verdict != p_abl.verdict:
                diff_count += 1

        status = "FULL MODEL" if m.name == "rca_v3" else (f"ACTIVE ({diff_count} diffs)" if diff_count > 0 else "INERT")
        print(f"{m.name:<38} | {k_dev}/{len(dev_diag):<8} | {k_val}/{len(val_diag_all):<8} | {mean_multi_f1:<13.1f}% | {abst_rec:<15.1f}% | {status}")

    # -------------------------------------------------------------------------
    # TABLE 12: Failure Analysis on DEV-VALIDATION
    # -------------------------------------------------------------------------
    print("\n--- TABLE 12: FAILURE ANALYSIS ON DEV-VALIDATION ---")
    wrong_cases = []
    for c, p in val_preds["rca_v3"]:
        if c.ground_truth.expected_verdict == "DIAGNOSE":
            tr = c.ground_truth.true_root_causes
            pred = p.predicted_root_causes[0] if p.predicted_root_causes else "NONE"
            if len(tr) == 1 and pred not in tr:
                wrong_cases.append((c, p, f"Predicted '{pred}' instead of true root '{tr[0]}'"))
        elif c.ground_truth.expected_verdict == "ABSTAIN":
            if p.verdict != "ABSTAIN":
                wrong_cases.append((c, p, f"Diagnosed '{p.predicted_root_causes[0] if p.predicted_root_causes else 'NONE'}' instead of ABSTAIN"))

    print(f"Total wrong cases on DEV-VALIDATION: {len(wrong_cases)}/{len(val_cases)}")
    for c, p, err_detail in wrong_cases:
        pert = c.perturbations[0] if c.perturbations else "base"
        print(f"  Case: {c.case_id:<45} | Fam: {c.family_id} | Pert: {pert:<25} | Error: {err_detail}")

    # -------------------------------------------------------------------------
    # TABLE 13: Literal Evaluation of Pre-registered Hypotheses
    # -------------------------------------------------------------------------
    print("\n--- TABLE 13: PRE-REGISTERED HYPOTHESIS VERDICTS ---")
    
    # H4: Top-1 vs Current Scorer on Hard + Edge-Root
    h4_delta = d_curr_mean
    h4_ci_lo = ci_curr_lo
    h4_met = (h4_delta > 0.0 and h4_ci_lo > 0.0)
    print(f"H4 (Top-1 on Hard/Edge vs Current Scorer):")
    print(f"   Delta: {format_delta(h4_delta, h4_ci_lo, ci_curr_hi, len(val_hard))}")
    print(f"   Status: {'MET' if h4_met else 'NOT MET'}")

    # H5: Multi-Root Set F1 vs Current Scorer
    val_multi = [c for c in val_cases if len(c.ground_truth.true_root_causes) > 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
    f1_v3 = sum(compute_set_f1(extract_predicted_root_set(next(x for x in val_preds['rca_v3'] if x[0].case_id == c.case_id)[1], alpha=0.85, max_k=3, oracle_len=len(c.ground_truth.true_root_causes)), c.ground_truth.true_root_causes)[2] for c in val_multi) / len(val_multi)
    f1_curr = sum(compute_set_f1(extract_predicted_root_set(next(x for x in val_preds['CurrentScorer_CaseTopology'] if x[0].case_id == c.case_id)[1], alpha=0.85, max_k=3, oracle_len=len(c.ground_truth.true_root_causes)), c.ground_truth.true_root_causes)[2] for c in val_multi) / len(val_multi)
    h5_met = (f1_v3 > f1_curr)
    print(f"H5 (Multi-Root Set F1 vs Current Scorer):")
    print(f"   rca_v3: {f1_v3*100:.2f}% vs Current Scorer: {f1_curr*100:.2f}% (Delta: +{(f1_v3 - f1_curr)*100:.2f} pp)")
    print(f"   Status: {'MET' if h5_met else 'NOT MET'}")

    # H6: Hallucinated Unobserved Roots on Control & Missing Root Recovery
    control_cases = [c for c in val_cases if "idle_healthy_control" in c.tags and c.ground_truth.expected_verdict == "DIAGNOSE"]
    # Check hallucinated unobserved root
    v3_hallucinated = 0
    curr_hallucinated = 0
    for c in control_cases:
        p_v3 = next(x for x in val_preds['rca_v3'] if x[0].case_id == c.case_id)[1]
        p_curr = next(x for x in val_preds['CurrentScorer_CaseTopology'] if x[0].case_id == c.case_id)[1]
        if p_v3.predicted_root_causes and p_v3.predicted_root_causes[0] not in c.ground_truth.true_root_causes:
            # check if predicted was unobserved in telemetry
            v3_hallucinated += 1
        if p_curr.predicted_root_causes and p_curr.predicted_root_causes[0] not in c.ground_truth.true_root_causes:
            curr_hallucinated += 1
    
    missing_cases = [c for c in val_cases if (c.perturbations and "missing_root_telemetry" in c.perturbations[0]) or ("hard_missing_root" in c.case_id)]
    k_miss_v3 = sum(1 for c in missing_cases if (next(x for x in val_preds['rca_v3'] if x[0].case_id == c.case_id)[1].predicted_root_causes and next(x for x in val_preds['rca_v3'] if x[0].case_id == c.case_id)[1].predicted_root_causes[0] in c.ground_truth.true_root_causes))
    k_miss_curr = sum(1 for c in missing_cases if (next(x for x in val_preds['CurrentScorer_CaseTopology'] if x[0].case_id == c.case_id)[1].predicted_root_causes and next(x for x in val_preds['CurrentScorer_CaseTopology'] if x[0].case_id == c.case_id)[1].predicted_root_causes[0] in c.ground_truth.true_root_causes))
    
    h6_met = (v3_hallucinated <= curr_hallucinated and k_miss_v3 >= k_miss_curr)
    print(f"H6 (Control Hallucination Rate & Missing Root Recovery):")
    print(f"   Control Hallucinations: rca_v3={v3_hallucinated}/{len(control_cases)} vs Current Scorer={curr_hallucinated}/{len(control_cases)}")
    print(f"   Missing Root Recovery: rca_v3={k_miss_v3}/{len(missing_cases)} vs Current Scorer={k_miss_curr}/{len(missing_cases)}")
    print(f"   Status: {'MET' if h6_met else 'NOT MET'}")

    # H7: CONFIRMED Precision >= 90%
    val_confirmed = [(c, p) for c, p in val_preds["rca_v3"] if p.metadata.get("rca_v3_verdict") == "CONFIRMED"]
    n_conf = len(val_confirmed)
    k_conf_corr = sum(1 for c, p in val_confirmed if (c in val_diag_single and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
    h7_prec = (k_conf_corr / n_conf * 100) if n_conf else 0.0
    h7_met = (h7_prec >= 90.0)
    print(f"H7 (CONFIRMED Verdict Precision >= 90.0%):")
    print(f"   CONFIRMED Precision: {format_metric(k_conf_corr, n_conf, h7_prec)} (Total CONFIRMED n={n_conf})")
    print(f"   Status: {'MET' if h7_met else 'NOT MET'}")

    # H8: Abstention Precision >= 85% and Recall > rca_v2
    val_abst_cases = [c for c in val_cases if c.ground_truth.expected_verdict == "ABSTAIN"]
    val_diag_cases = [c for c in val_cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    tp_v3 = sum(1 for c, p in val_preds["rca_v3"] if c in val_abst_cases and p.verdict == "ABSTAIN")
    fp_v3 = sum(1 for c, p in val_preds["rca_v3"] if c in val_diag_cases and p.verdict == "ABSTAIN")
    tp_v2 = sum(1 for c, p in val_preds["rca_v2"] if c in val_abst_cases and p.verdict == "ABSTAIN")
    h8_prec = (tp_v3 / (tp_v3 + fp_v3) * 100) if (tp_v3 + fp_v3) else 0.0
    h8_rec_v3 = (tp_v3 / len(val_abst_cases) * 100) if val_abst_cases else 0.0
    h8_rec_v2 = (tp_v2 / len(val_abst_cases) * 100) if val_abst_cases else 0.0
    h8_met = (h8_prec >= 85.0 and h8_rec_v3 > h8_rec_v2)
    print(f"H8 (Abstention Precision >= 85% and Recall > rca_v2):")
    print(f"   Precision: {format_metric(tp_v3, tp_v3 + fp_v3, h8_prec)}")
    print(f"   Recall: rca_v3 = {format_metric(tp_v3, len(val_abst_cases), h8_rec_v3)} vs rca_v2 = {format_metric(tp_v2, len(val_abst_cases), h8_rec_v2)}")
    print(f"   Status: {'MET' if h8_met else 'NOT MET'}")

    # H9: Non-Inferiority on Overall Diagnosable Top-1 (margin 3 pp)
    k_v3_all = sum(1 for c, p in val_preds["rca_v3"] if c in val_diag_all and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    k_v2_all = sum(1 for c, p in val_preds["rca_v2"] if c in val_diag_all and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    h9_delta = ((k_v3_all - k_v2_all) / len(val_diag_all) * 100) if val_diag_all else 0.0
    h9_met = (h9_delta >= -3.0)
    print(f"H9 (Non-Inferiority on Overall Diagnosable Top-1 Margin 3 pp):")
    print(f"   rca_v3 = {format_metric(k_v3_all, len(val_diag_all), k_v3_all/len(val_diag_all)*100)} vs rca_v2 = {format_metric(k_v2_all, len(val_diag_all), k_v2_all/len(val_diag_all)*100)}")
    print(f"   Delta: {h9_delta:+.2f} pp (Threshold >= -3.0 pp)")
    print(f"   Status: {'MET' if h9_met else 'NOT MET'}")


if __name__ == "__main__":
    evaluate_stage3()
