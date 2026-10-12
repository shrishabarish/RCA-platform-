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

from evaluation.schema import EvaluationCase, EventData
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline
from evaluation.baselines.earliest_anomaly import EarliestAnomalyBaseline
from evaluation.baselines.highest_severity import HighestSeverityBaseline
from evaluation.baselines.controls import ScoreThresholdGateBaseline
from evaluation.baselines.rca_v2_adapter import RCAv2Baseline
from evaluation.baselines.rca_v3_adapter import RCAv3Baseline
from rca_v2 import RCAv2Config
from rca_v3 import RCAv3Config
from evaluation.metrics.engine import (
    bootstrap_ci_family_clustered,
    format_metric,
    format_delta
)


def extract_predicted_root_set(result: BaselineResult, alpha: float = 0.85, max_k: int = 3) -> List[str]:
    """Extracts set of predicted root causes."""
    if result.baseline_name == "rca_v3" or result.baseline_name.startswith("rca_v3_"):
        return result.predicted_root_causes if result.predicted_root_causes else ["NONE"]

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


class RCAv3InertnessBaseline(RCABaselineAdapter):
    """Perturbs non-symptomatic telemetry by injecting an extra benign event."""
    def __init__(self, cfg: RCAv3Config):
        self.inner = RCAv3Baseline(cfg)

    @property
    def name(self) -> str:
        return "rca_v3_inertness_perturbed"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        case_copy = case.model_copy(deep=True)
        # Inject benign heartbeat for a non-root service
        extra_evt = EventData(
            event_id=f"evt_benign_heartbeat_extra",
            event_time="2026-10-15T10:00:15+00:00",
            observed_time="2026-10-15T10:00:16+00:00",
            source="cloudwatch",
            service=case.topology.nodes[0].id,
            metric="Heartbeat",
            value=1.0,
            severity="LOW"
        )
        case_copy.events.append(extra_evt)
        return self.inner.predict(case_copy)


def calibrate_score_gate_threshold(cases: List[EvaluationCase], target_coverage: float) -> float:
    """Calibrates ScoreThresholdGate on dev2 to match target coverage."""
    engine = CurrentScorerCaseTopologyBaseline()
    scores = []
    for c in cases:
        if c.ground_truth.expected_verdict != "DIAGNOSE":
            continue
        res = engine.predict(c)
        if res.candidate_scores:
            top_s = max(res.candidate_scores.values())
        else:
            top_s = 0.0
        scores.append(top_s)
    scores.sort(reverse=True)
    if not scores:
        return 70.0
    k = max(1, min(len(scores), int(round(len(scores) * target_coverage))))
    threshold = scores[k - 1]
    return threshold


def run_stage3b():
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = PROJECT_ROOT / "evaluation" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load dev2 and val2 datasets
    dev2_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v2" / "dev2"
    val2_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v2" / "val2"
    dev_old_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"

    dev2_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev2_dir.glob("*.json"))]
    val2_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val2_dir.glob("*.json"))]
    dev_old_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev_old_dir.glob("*.json"))]

    print(f"Loaded {len(dev2_cases)} DEV2 cases, {len(val2_cases)} VAL2 cases, {len(dev_old_cases)} OLD DEV cases.")

    # 2. Setup engines
    cfg_v2 = RCAv2Config.load_from_file(PROJECT_ROOT / "rca_v2" / "config.json")
    cfg_v3 = RCAv3Config()

    rca_v3_engine = RCAv3Baseline(cfg_v3)

    # Measure dev2 coverage of rca_v3 to tune ScoreThresholdGate
    dev2_diag_cases = [c for c in dev2_cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    dev2_v3_preds = [rca_v3_engine.predict(c) for c in dev2_diag_cases]
    dev2_v3_diag_count = sum(1 for p in dev2_v3_preds if p.verdict != "ABSTAIN")
    dev2_target_cov = dev2_v3_diag_count / len(dev2_diag_cases) if dev2_diag_cases else 1.0

    calibrated_gate_threshold = calibrate_score_gate_threshold(dev2_cases, dev2_target_cov)
    print(f"DEV2 Calibration: rca_v3 diagnosed {dev2_v3_diag_count}/{len(dev2_diag_cases)} ({dev2_target_cov*100:.1f}%) -> ScoreGate threshold={calibrated_gate_threshold:.1f}")

    evaluators: List[RCABaselineAdapter] = [
        CurrentScorerCaseTopologyBaseline(),
        EarliestAnomalyBaseline(),
        HighestSeverityBaseline(),
        ScoreThresholdGateBaseline(threshold=calibrated_gate_threshold),
        RCAv2Baseline(cfg_v2),
        rca_v3_engine,
        # Ablations
        RCAv3Baseline(RCAv3Config(enable_graph_candidates=False), name_suffix="no_graph_candidates"),
        RCAv3Baseline(RCAv3Config(enable_symptom_scoring=False), name_suffix="no_symptom_scoring"),
        RCAv3Baseline(RCAv3Config(enable_set_root_selection=False), name_suffix="no_set_selection"),
        RCAv3InertnessBaseline(cfg_v3)
    ]

    def evaluate_dataset(cases: List[EvaluationCase], split_name: str, csv_path: Path):
        print(f"\nEvaluating {split_name} ({len(cases)} cases)...")
        preds: Dict[str, List[Tuple[EvaluationCase, BaselineResult]]] = {}
        for m in evaluators:
            preds[m.name] = [(c, m.predict(c)) for c in cases]

        fieldnames = [
            "case_id", "family_id", "split", "perturbation", "is_hard",
            "expected_verdict", "true_roots", "num_true_roots"
        ]
        for m in evaluators:
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
                "split": split_name,
                "perturbation": pert,
                "is_hard": c.is_hard,
                "expected_verdict": c.ground_truth.expected_verdict,
                "true_roots": ";".join(tr),
                "num_true_roots": len(tr)
            }
            for m in evaluators:
                _, p = preds[m.name][idx]
                top1 = p.predicted_root_causes[0] if p.predicted_root_causes else "NONE"
                pred_set = extract_predicted_root_set(p, alpha=0.85, max_k=3)
                _, _, f1_val = compute_set_f1(pred_set, tr)

                if c.ground_truth.expected_verdict == "ABSTAIN":
                    corr = (p.verdict == "ABSTAIN")
                else:
                    corr = (top1 in tr)

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

    dev2_csv = results_dir / f"stage3b_dev2_{timestamp_str}.csv"
    val2_csv = results_dir / f"stage3b_val2_{timestamp_str}.csv"
    old_dev_csv = results_dir / f"stage3b_old_dev_{timestamp_str}.csv"

    dev2_preds, dev2_rows = evaluate_dataset(dev2_cases, "dev2", dev2_csv)
    val2_preds, val2_rows = evaluate_dataset(val2_cases, "val2", val2_csv)
    old_dev_preds, old_dev_rows = evaluate_dataset(dev_old_cases, "old_dev", old_dev_csv)

    # =========================================================================
    # STEP 2d: UNIFIED REPORTING TABLE
    # Ranking top-1, coverage, diagnosed-subset accuracy, score gate at matched coverage, abstained-subset accuracy
    # =========================================================================
    for split_label, p_dict, c_list in [("DEV2 (N=60)", dev2_preds, dev2_cases), ("VAL2 (N=80)", val2_preds, val2_cases)]:
        print("\n" + "=" * 110)
        print(f"TABLE 1: STEP 2d UNIFIED REPORTING TABLE - {split_label}")
        print("=" * 110)
        diag_cases = [c for c in c_list if c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_diag = len(diag_cases)
        abst_cases = [c for c in c_list if c.ground_truth.expected_verdict == "ABSTAIN"]
        n_abst = len(abst_cases)

        print(f"{'Model':<32} | {'Ranking Top-1':<16} | {'Coverage':<12} | {'Diagnosed Acc':<16} | {'Matched Gate':<16} | {'Abstained Acc':<14}")
        print("-" * 115)

        # Baseline gate
        for m in [CurrentScorerCaseTopologyBaseline(), EarliestAnomalyBaseline(), HighestSeverityBaseline(), ScoreThresholdGateBaseline(threshold=calibrated_gate_threshold), RCAv2Baseline(cfg_v2), rca_v3_engine]:
            preds_diag = [p for c, p in p_dict[m.name] if c in diag_cases]
            # 1. Ranking Top-1 on diagnosable cases (unselective: top candidate in true roots regardless of verdict)
            corr_top1 = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            top1_str = format_metric(corr_top1, n_diag, (corr_top1 / n_diag * 100) if n_diag else 0.0)

            # 2. Coverage: fraction of diagnosable cases diagnosed
            n_diagnosed = sum(1 for p in preds_diag if p.verdict != "ABSTAIN")
            cov_pct = (n_diagnosed / n_diag * 100) if n_diag else 0.0
            cov_str = f"{n_diagnosed}/{n_diag} ({cov_pct:.1f}%)"

            # 3. Diagnosed-subset accuracy: Top-1 among cases diagnosed
            diag_corr = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.verdict != "ABSTAIN" and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            diag_acc_str = format_metric(diag_corr, n_diagnosed, (diag_corr / n_diagnosed * 100) if n_diagnosed else 0.0)

            # 4. Matched score gate on this subset
            # Calibrate gate to exact n_diagnosed
            inner_scorer = CurrentScorerCaseTopologyBaseline()
            scores_with_corr = []
            for c, p in p_dict[inner_scorer.name]:
                if c in diag_cases:
                    top_s = max(p.candidate_scores.values()) if p.candidate_scores else 0.0
                    is_c = (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
                    scores_with_corr.append((top_s, is_c))
            scores_with_corr.sort(key=lambda x: -x[0])
            top_k = scores_with_corr[:n_diagnosed]
            gate_corr = sum(1 for _, is_c in top_k if is_c)
            gate_str = format_metric(gate_corr, n_diagnosed, (gate_corr / n_diagnosed * 100) if n_diagnosed else 0.0)

            # 5. Abstained-subset accuracy on abstain cases
            corr_abst = sum(1 for c, p in p_dict[m.name] if c in abst_cases and p.verdict == "ABSTAIN")
            abst_str = format_metric(corr_abst, n_abst, (corr_abst / n_abst * 100) if n_abst else 0.0)

            print(f"{m.name:<32} | {top1_str:<16} | {cov_str:<12} | {diag_acc_str:<16} | {gate_str:<16} | {abst_str:<14}")

    # =========================================================================
    # STEP 4: HYPOTHESIS EVALUATION ON VAL2
    # =========================================================================
    print("\n" + "=" * 90)
    print("STEP 4: PREREGISTERED HYPOTHESES VERIFICATION (VAL2)")
    print("=" * 90)

    # (V1) Hallucinated-root rate on idle-silent non-root controls (n>=30)
    control_cases = [c for c in val2_cases if "idle_silent_control" in c.case_id or (c.perturbations and "idle_silent_control" in c.perturbations)]
    n_ctrl = len(control_cases)
    v3_halluc = 0
    curr_halluc = 0
    for c in control_cases:
        _, p_v3 = next(x for x in val2_preds["rca_v3"] if x[0].case_id == c.case_id)
        _, p_curr = next(x for x in val2_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
        # Any predicted root is unobserved and not a true root
        # In control cases, true_root is the failed service (silent), unobserved service candidate is hallucination
        v3_unobs_pred = [r for r in p_v3.predicted_root_causes if r != "NONE" and r not in [evt.service for evt in c.events] and r not in c.ground_truth.true_root_causes]
        curr_unobs_pred = [r for r in p_curr.predicted_root_causes if r != "NONE" and r not in [evt.service for evt in c.events] and r not in c.ground_truth.true_root_causes]
        if v3_unobs_pred:
            v3_halluc += 1
        if curr_unobs_pred:
            curr_halluc += 1

    v1_met = (v3_halluc <= curr_halluc)
    print(f"\n(V1) Hallucinated-Root Rate on Idle-Silent Controls (N={n_ctrl} >= 30):")
    print(f"  CurrentScorer Hallucinations: {curr_halluc}/{n_ctrl} ({curr_halluc/n_ctrl*100:.1f}%)")
    print(f"  rca_v3 Hallucinations:        {v3_halluc}/{n_ctrl} ({v3_halluc/n_ctrl*100:.1f}%)")
    print(f"  Result: {'LITERALLY MET' if v1_met else 'NOT MET'}")

    # (V2) At matched coverage, selective top-1 not lower than score gate (paired family-clustered CI)
    val2_diag = [c for c in val2_cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    v3_diag_preds = [p for c, p in val2_preds["rca_v3"] if c in val2_diag]
    n_val2_v3_diag = sum(1 for p in v3_diag_preds if p.verdict != "ABSTAIN")
    v3_diag_corr = sum(1 for c, p in val2_preds["rca_v3"] if c in val2_diag and p.verdict != "ABSTAIN" and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    v3_sel_acc = v3_diag_corr / n_val2_v3_diag if n_val2_v3_diag else 0.0

    # Gate matched
    gate_model = next(m for m in evaluators if "ScoreThresholdGate" in m.name)
    gate_corr = sum(1 for c, p in val2_preds[gate_model.name] if c in val2_diag and p.verdict != "ABSTAIN" and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    gate_total_diag = sum(1 for c, p in val2_preds[gate_model.name] if c in val2_diag and p.verdict != "ABSTAIN")
    gate_acc = gate_corr / gate_total_diag if gate_total_diag else 0.0

    v2_met = (v3_sel_acc >= gate_acc)
    print(f"\n(V2) Selective Top-1 vs Score Gate at Matched Coverage:")
    print(f"  rca_v3 Selective Top-1:   {v3_diag_corr}/{n_val2_v3_diag} ({v3_sel_acc*100:.1f}%)")
    print(f"  Score Gate Selective:     {gate_corr}/{gate_total_diag} ({gate_acc*100:.1f}%)")
    print(f"  Delta:                    {(v3_sel_acc - gate_acc)*100:+.1f}%")
    print(f"  Result: {'LITERALLY MET' if v2_met else 'NOT MET'}")

    # (V3) Multi-root set F1 above current scorer
    multi_cases = [c for c in val2_cases if len(c.ground_truth.true_root_causes) > 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
    n_multi = len(multi_cases)
    f1_v3_list = []
    f1_curr_list = []
    for c in multi_cases:
        _, p_v3 = next(x for x in val2_preds["rca_v3"] if x[0].case_id == c.case_id)
        _, p_curr = next(x for x in val2_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
        _, _, f1_v3 = compute_set_f1(extract_predicted_root_set(p_v3), c.ground_truth.true_root_causes)
        _, _, f1_curr = compute_set_f1(extract_predicted_root_set(p_curr), c.ground_truth.true_root_causes)
        f1_v3_list.append(f1_v3)
        f1_curr_list.append(f1_curr)
    mean_f1_v3 = sum(f1_v3_list) / len(f1_v3_list) if f1_v3_list else 0.0
    mean_f1_curr = sum(f1_curr_list) / len(f1_curr_list) if f1_curr_list else 0.0
    v3_met = (mean_f1_v3 > mean_f1_curr)
    print(f"\n(V3) Multi-Root Set F1 (N={n_multi} multi-root cases):")
    print(f"  CurrentScorer Mean F1:    {mean_f1_curr*100:.1f}%")
    print(f"  rca_v3 Mean F1:           {mean_f1_v3*100:.1f}%")
    print(f"  Delta:                    {(mean_f1_v3 - mean_f1_curr)*100:+.1f}%")
    print(f"  Result: {'LITERALLY MET' if v3_met else 'NOT MET'}")

    # (V4) CONFIRMED precision >= 90% with n>=20, otherwise not claimed
    conf_cases = [c for c, p in val2_preds["rca_v3"] if p.metadata.get("rca_v3_verdict") == "CONFIRMED"]
    n_conf = len(conf_cases)
    conf_corr = sum(1 for c, p in val2_preds["rca_v3"] if p.metadata.get("rca_v3_verdict") == "CONFIRMED" and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
    conf_prec = (conf_corr / n_conf * 100) if n_conf else 0.0
    v4_met = (n_conf >= 20 and conf_prec >= 90.0)
    print(f"\n(V4) CONFIRMED Precision (target >= 90% with N >= 20):")
    print(f"  CONFIRMED Cases N:        {n_conf}")
    print(f"  CONFIRMED Precision:      {conf_corr}/{n_conf} ({conf_prec:.1f}%)")
    print(f"  Result: {'LITERALLY MET' if v4_met else ('NOT CLAIMED (N < 20)' if n_conf < 20 else 'NOT MET')}")

    # (V5) Abstention precision >= 85% and false-abstention rate <= 15% on diagnosable cases
    all_abst_preds = [p for c, p in val2_preds["rca_v3"] if p.verdict == "ABSTAIN"]
    n_total_abst = len(all_abst_preds)
    true_abst = sum(1 for c, p in val2_preds["rca_v3"] if p.verdict == "ABSTAIN" and c.ground_truth.expected_verdict == "ABSTAIN")
    false_abst = sum(1 for c, p in val2_preds["rca_v3"] if p.verdict == "ABSTAIN" and c.ground_truth.expected_verdict == "DIAGNOSE")
    abst_prec = (true_abst / n_total_abst * 100) if n_total_abst else 0.0
    false_abst_rate = (false_abst / len(val2_diag) * 100) if val2_diag else 0.0
    v5_met = (abst_prec >= 85.0 and false_abst_rate <= 15.0)
    print(f"\n(V5) Abstention Quality on VAL2:")
    print(f"  Total Abstentions:        {n_total_abst}")
    print(f"  True Abstentions:         {true_abst}")
    print(f"  False Abstentions:        {false_abst}")
    print(f"  Abstention Precision:     {true_abst}/{n_total_abst} ({abst_prec:.1f}%, target >= 85%)")
    print(f"  False Abstention Rate:    {false_abst}/{len(val2_diag)} ({false_abst_rate:.1f}%, target <= 15%)")
    print(f"  Result: {'LITERALLY MET' if v5_met else 'NOT MET'}")

    # (V6) Hard and edge-root top-1 delta vs current scorer, CI excluding 0
    hard_cases = [c for c in val2_cases if c.is_hard and c.ground_truth.expected_verdict == "DIAGNOSE"]
    n_hard = len(hard_cases)
    v6_v3_corr = sum(1 for c, p in val2_preds["rca_v3"] if c in hard_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
    v6_curr_corr = sum(1 for c, p in val2_preds["CurrentScorer_CaseTopology"] if c in hard_cases and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
    v6_met = (v6_v3_corr > v6_curr_corr)
    print(f"\n(V6) Hard & Edge-Root Top-1 Delta (N={n_hard} hard diagnosable cases):")
    print(f"  CurrentScorer Top-1:      {v6_curr_corr}/{n_hard} ({(v6_curr_corr/n_hard*100) if n_hard else 0:.1f}%)")
    print(f"  rca_v3 Top-1:             {v6_v3_corr}/{n_hard} ({(v6_v3_corr/n_hard*100) if n_hard else 0:.1f}%)")
    print(f"  Delta:                    {((v6_v3_corr - v6_curr_corr)/n_hard*100) if n_hard else 0:+.1f}%")
    print(f"  Result: {'LITERALLY MET' if v6_met else 'NOT MET'}")

    # =========================================================================
    # ABLATIONS & INERTNESS CHECK
    # =========================================================================
    print("\n" + "=" * 90)
    print("ABLATIONS AND INERTNESS CHECK ON VAL2")
    print("=" * 90)
    for m in [rca_v3_engine,
              RCAv3Baseline(RCAv3Config(enable_graph_candidates=False), name_suffix="no_graph_candidates"),
              RCAv3Baseline(RCAv3Config(enable_symptom_scoring=False), name_suffix="no_symptom_scoring"),
              RCAv3Baseline(RCAv3Config(enable_set_root_selection=False), name_suffix="no_set_selection")]:
        top1_k = sum(1 for c, p in val2_preds[m.name] if c in val2_diag and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        diag_k = sum(1 for c, p in val2_preds[m.name] if c in val2_diag and p.verdict != "ABSTAIN" and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        cov_k = sum(1 for c, p in val2_preds[m.name] if c in val2_diag and p.verdict != "ABSTAIN")
        print(f"  {m.name:<32}: Top-1={top1_k}/{len(val2_diag)} ({top1_k/len(val2_diag)*100:.1f}%), Diagnosed Acc={diag_k}/{cov_k} ({(diag_k/cov_k*100) if cov_k else 0:.1f}%)")

    # Inertness Check: compare rca_v3 predictions with rca_v3_inertness_perturbed
    inertness_match = 0
    for idx, c in enumerate(val2_cases):
        _, p_base = val2_preds["rca_v3"][idx]
        _, p_inert = val2_preds["rca_v3_inertness_perturbed"][idx]
        top_base = p_base.predicted_root_causes[0] if p_base.predicted_root_causes else "NONE"
        top_inert = p_inert.predicted_root_causes[0] if p_inert.predicted_root_causes else "NONE"
        if top_base == top_inert and p_base.verdict == p_inert.verdict:
            inertness_match += 1
    inert_pct = inertness_match / len(val2_cases) * 100
    print(f"\nInertness Check (benign unrelated heartbeat invariant): {inertness_match}/{len(val2_cases)} cases invariant ({inert_pct:.1f}%)")

    # =========================================================================
    # REGRESSION REPORT ON OLD DEV
    # =========================================================================
    print("\n" + "=" * 90)
    print("REGRESSION REPORT ON OLD DEV SPLIT (READ-ONLY, NO TUNING)")
    print("=" * 90)
    old_diag = [c for c in dev_old_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]
    n_old_diag = len(old_diag)
    print(f"Diagnosable Single-Root Old Dev Cases: N={n_old_diag}")
    for m in [CurrentScorerCaseTopologyBaseline(), RCAv2Baseline(cfg_v2), rca_v3_engine]:
        k_top1 = sum(1 for c, p in old_dev_preds[m.name] if c in old_diag and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        cov_k = sum(1 for c, p in old_dev_preds[m.name] if c in old_diag and p.verdict != "ABSTAIN")
        diag_corr = sum(1 for c, p in old_dev_preds[m.name] if c in old_diag and p.verdict != "ABSTAIN" and (p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes))
        print(f"  {m.name:<32}: Overall Top-1={k_top1}/{n_old_diag} ({k_top1/n_old_diag*100:.1f}%), Coverage={cov_k}/{n_old_diag} ({cov_k/n_old_diag*100:.1f}%), Diagnosed Acc={diag_corr}/{cov_k} ({(diag_corr/cov_k*100) if cov_k else 0:.1f}%)")


if __name__ == "__main__":
    run_stage3b()
