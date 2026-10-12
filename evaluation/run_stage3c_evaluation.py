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
from evaluation.run_stage3b_evaluation import compute_set_f1, extract_predicted_root_set


class EarliestAnomalyNoBaselineBaseline(RCABaselineAdapter):
    """Earliest anomaly baseline with periodic baseline events (LOW) excluded."""
    @property
    def name(self) -> str:
        return "Earliest_Anomaly_NoBaselines"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        anom_events = [e for e in case.events if e.severity.upper() in {"CRITICAL", "HIGH", "MEDIUM"}]
        if not anom_events:
            return BaselineResult(
                baseline_name=self.name,
                case_id=case.case_id,
                verdict="ABSTAIN",
                predicted_root_causes=[],
                candidate_scores={},
                confidence=0.0
            )
        sorted_evs = sorted(anom_events, key=lambda e: datetime.fromisoformat(e.event_time))
        earliest_svc = sorted_evs[0].service
        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict="DIAGNOSE",
            predicted_root_causes=[earliest_svc],
            candidate_scores={earliest_svc: 100.0},
            confidence=100.0
        )


class SeverityThresholdGateBaseline(RCABaselineAdapter):
    """Abstains if no CRITICAL event was observed in the incident."""
    def __init__(self, inner: Optional[RCABaselineAdapter] = None):
        self.inner = inner or HighestSeverityBaseline()

    @property
    def name(self) -> str:
        return "Control_SeverityGate"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        has_critical = any(e.severity.upper() == "CRITICAL" for e in case.events)
        res = self.inner.predict(case)
        if not has_critical:
            return BaselineResult(
                baseline_name=self.name,
                case_id=case.case_id,
                verdict="ABSTAIN",
                predicted_root_causes=[],
                candidate_scores=res.candidate_scores,
                confidence=0.0
            )
        return res


def calibrate_score_gate_threshold(cases: List[EvaluationCase], target_coverage: float) -> float:
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
    return scores[k - 1]


def run_stage3c():
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = PROJECT_ROOT / "evaluation" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    dev3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "dev3"
    val3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"

    dev3_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev3_dir.glob("*.json"))]
    val3_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val3_dir.glob("*.json"))]

    print(f"Loaded {len(dev3_cases)} DEV3 cases and {len(val3_cases)} VAL3 cases.")

    cfg_v2 = RCAv2Config.load_from_file(PROJECT_ROOT / "rca_v2" / "config.json")
    cfg_v3 = RCAv3Config()

    rca_v3_engine = RCAv3Baseline(cfg_v3)

    # Calibrate score gate on dev3 to match rca_v3 coverage
    dev3_diag = [c for c in dev3_cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    dev3_v3_diag_count = sum(1 for c in dev3_diag if rca_v3_engine.predict(c).verdict != "ABSTAIN")
    dev3_target_cov = dev3_v3_diag_count / len(dev3_diag) if dev3_diag else 1.0
    calibrated_gate_threshold = calibrate_score_gate_threshold(dev3_cases, dev3_target_cov)
    print(f"DEV3 Calibration: rca_v3 coverage = {dev3_target_cov*100:.1f}% -> ScoreGate threshold={calibrated_gate_threshold:.1f}")

    evaluators: List[RCABaselineAdapter] = [
        CurrentScorerCaseTopologyBaseline(),
        EarliestAnomalyBaseline(),
        EarliestAnomalyNoBaselineBaseline(),
        HighestSeverityBaseline(),
        ScoreThresholdGateBaseline(threshold=calibrated_gate_threshold),
        SeverityThresholdGateBaseline(),
        RCAv2Baseline(cfg_v2),
        rca_v3_engine
    ]

    def evaluate_split(cases: List[EvaluationCase], split_name: str, csv_path: Path):
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

    dev3_csv = results_dir / f"stage3c_dev3_{timestamp_str}.csv"
    val3_csv = results_dir / f"stage3c_val3_{timestamp_str}.csv"

    dev3_preds, dev3_rows = evaluate_split(dev3_cases, "dev3", dev3_csv)
    val3_preds, val3_rows = evaluate_split(val3_cases, "val3", val3_csv)

    # Unified Reporting Table on VAL3
    for split_label, p_dict, c_list in [("DEV3 (N=100)", dev3_preds, dev3_cases), ("VAL3 (N=100)", val3_preds, val3_cases)]:
        print("\n" + "=" * 115)
        print(f"TABLE: UNIFIED BENCHMARK REPORT - {split_label}")
        print("=" * 115)
        diag_cases = [c for c in c_list if c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_diag = len(diag_cases)
        abst_cases = [c for c in c_list if c.ground_truth.expected_verdict == "ABSTAIN"]
        n_abst = len(abst_cases)

        print(f"{'Model':<35} | {'Ranking Top-1':<16} | {'Coverage':<12} | {'Diagnosed Acc':<16} | {'Abstained Acc':<14}")
        print("-" * 105)

        for m in evaluators:
            corr_top1 = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            top1_str = format_metric(corr_top1, n_diag, (corr_top1 / n_diag * 100) if n_diag else 0.0)

            n_diagnosed = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.verdict != "ABSTAIN")
            cov_pct = (n_diagnosed / n_diag * 100) if n_diag else 0.0
            cov_str = f"{n_diagnosed}/{n_diag} ({cov_pct:.1f}%)"

            diag_corr = sum(1 for c, p in p_dict[m.name] if c in diag_cases and p.verdict != "ABSTAIN" and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            diag_acc_str = format_metric(diag_corr, n_diagnosed, (diag_corr / n_diagnosed * 100) if n_diagnosed else 0.0)

            corr_abst = sum(1 for c, p in p_dict[m.name] if c in abst_cases and p.verdict == "ABSTAIN")
            abst_str = format_metric(corr_abst, n_abst, (corr_abst / n_abst * 100) if n_abst else 0.0)

            print(f"{m.name:<35} | {top1_str:<16} | {cov_str:<12} | {diag_acc_str:<16} | {abst_str:<14}")

    # =========================================================================
    # FORMAL HYPOTHESIS TESTING: V7 TO V12 ON VAL3
    # =========================================================================
    print("\n" + "=" * 95)
    print("FORMAL PREREGISTERED HYPOTHESES VERIFICATION (VAL3, UNINFORMATIVE SEVERITY)")
    print("=" * 95)

    val3_diag = [c for c in val3_cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    n_diag = len(val3_diag)

    # (V7) rca_v3 vs CurrentScorer Top-1 Delta with family-clustered CI
    v7_fam_delta = defaultdict(list)
    v7_fam_v3 = defaultdict(list)
    v7_fam_curr = defaultdict(list)
    for c in val3_diag:
        _, p_v3 = next(x for x in val3_preds["rca_v3"] if x[0].case_id == c.case_id)
        _, p_curr = next(x for x in val3_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
        tr = set(c.ground_truth.true_root_causes)
        v3_c = 1.0 if p_v3.predicted_root_causes and p_v3.predicted_root_causes[0] in tr else 0.0
        curr_c = 1.0 if p_curr.predicted_root_causes and p_curr.predicted_root_causes[0] in tr else 0.0
        v7_fam_v3[c.family_id].append(v3_c)
        v7_fam_curr[c.family_id].append(curr_c)
        v7_fam_delta[c.family_id].append(v3_c - curr_c)

    ci_v7_v3 = bootstrap_ci_family_clustered(v7_fam_v3, n_bootstrap=1000)
    ci_v7_curr = bootstrap_ci_family_clustered(v7_fam_curr, n_bootstrap=1000)
    ci_v7_delta = bootstrap_ci_family_clustered(v7_fam_delta, n_bootstrap=1000)
    v3_top1_cnt = sum(1 for c, p in val3_preds["rca_v3"] if c in val3_diag and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    curr_top1_cnt = sum(1 for c, p in val3_preds["CurrentScorer_CaseTopology"] if c in val3_diag and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    v7_delta_pp = (v3_top1_cnt - curr_top1_cnt) / n_diag * 100
    v7_met = (ci_v7_delta[0] > 0.0)

    print(f"\n(V7) rca_v3 Top-1 vs CurrentScorer (N={n_diag} Diagnosable Cases):")
    print(f"  CurrentScorer Top-1: {curr_top1_cnt}/{n_diag} ({curr_top1_cnt/n_diag*100:.1f}%) [{ci_v7_curr[0]:.1f}, {ci_v7_curr[1]:.1f}]")
    print(f"  rca_v3 Top-1:        {v3_top1_cnt}/{n_diag} ({v3_top1_cnt/n_diag*100:.1f}%) [{ci_v7_v3[0]:.1f}, {ci_v7_v3[1]:.1f}]")
    print(f"  Paired Delta:        {v7_delta_pp:+.1f} pp [{ci_v7_delta[0]*100:.1f}, {ci_v7_delta[1]*100:.1f}]")
    print(f"  Result: {'LITERALLY MET' if v7_met else 'NOT MET (CI includes 0)'}")

    # (V8) rca_v3 vs Score Gate at 80% Coverage
    k_80 = int(round(0.80 * n_diag))
    v8_cases_v3 = sorted(val3_diag, key=lambda c: -next(x for x in val3_preds["rca_v3"] if x[0].case_id == c.case_id)[1].confidence)[:k_80]
    v8_cases_gate = sorted(val3_diag, key=lambda c: -next(x for x in val3_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)[1].confidence)[:k_80]
    v8_corr_v3 = sum(1 for c in v8_cases_v3 if next(x for x in val3_preds["rca_v3"] if x[0].case_id == c.case_id)[1].predicted_root_causes[0] in c.ground_truth.true_root_causes)
    v8_corr_gate = sum(1 for c in v8_cases_gate if next(x for x in val3_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)[1].predicted_root_causes[0] in c.ground_truth.true_root_causes)
    v8_acc_v3 = v8_corr_v3 / k_80 * 100
    v8_acc_gate = v8_corr_gate / k_80 * 100
    v8_met = (v8_acc_v3 >= v8_acc_gate)
    print(f"\n(V8) rca_v3 vs Score Gate at 80% Coverage (N={k_80}/{n_diag}):")
    print(f"  rca_v3 at 80% cov:     {v8_corr_v3}/{k_80} ({v8_acc_v3:.1f}%)")
    print(f"  Score Gate at 80% cov: {v8_corr_gate}/{k_80} ({v8_acc_gate:.1f}%)")
    print(f"  Delta:                 {v8_acc_v3 - v8_acc_gate:+.1f} pp")
    print(f"  Result: {'LITERALLY MET' if v8_met else 'NOT MET'}")

    # (V9) rca_v3 vs Severity Gate
    sev_cnt = sum(1 for c, p in val3_preds["Highest_Severity"] if c in val3_diag and p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
    v9_delta_pp = (v3_top1_cnt - sev_cnt) / n_diag * 100
    v9_met = (v3_top1_cnt > sev_cnt)
    print(f"\n(V9) rca_v3 vs Highest Severity on Uninformative Severity (N={n_diag}):")
    print(f"  Highest_Severity Top-1: {sev_cnt}/{n_diag} ({sev_cnt/n_diag*100:.1f}%)")
    print(f"  rca_v3 Top-1:           {v3_top1_cnt}/{n_diag} ({v3_top1_cnt/n_diag*100:.1f}%)")
    print(f"  Delta:                  {v9_delta_pp:+.1f} pp")
    print(f"  Result: {'LITERALLY MET' if v9_met else 'NOT MET'}")

    # (V10) CONFIRMED Precision > PROBABLE Precision (N >= 20)
    from rca_v3.diagnose import diagnose
    v_conf_corr = 0
    v_conf_total = 0
    v_prob_corr = 0
    v_prob_total = 0
    for c in val3_cases:
        res = diagnose(c.events, c.topology, c.source_health, c.telemetry_policy, cfg_v3)
        top = res.top_candidate.service if res.top_candidate else "NONE"
        tr = set(c.ground_truth.true_root_causes)
        is_c = (top in tr)
        if res.verdict.value == "CONFIRMED":
            v_conf_total += 1
            if is_c:
                v_conf_corr += 1
        elif res.verdict.value == "PROBABLE":
            v_prob_total += 1
            if is_c:
                v_prob_corr += 1

    p_conf = (v_conf_corr / v_conf_total * 100) if v_conf_total else 0.0
    p_prob = (v_prob_corr / v_prob_total * 100) if v_prob_total else 0.0
    v10_met = (v_conf_total >= 20 and p_conf > p_prob)
    print(f"\n(V10) CONFIRMED Precision vs PROBABLE Precision:")
    print(f"  CONFIRMED Precision: {v_conf_corr}/{v_conf_total} ({p_conf:.1f}%) [N={v_conf_total}]")
    print(f"  PROBABLE Precision:  {v_prob_corr}/{v_prob_total} ({p_prob:.1f}%) [N={v_prob_total}]")
    print(f"  Delta:               {p_conf - p_prob:+.1f} pp")
    print(f"  Result: {'LITERALLY MET' if v10_met else 'NOT MET'}")

    # (V11) Multi-Root Set F1 Delta (N >= 30)
    multi_cases = [c for c in val3_cases if len(c.ground_truth.true_root_causes) > 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
    n_multi = len(multi_cases)
    f1_v3_list = []
    f1_curr_list = []
    v11_fam_delta = defaultdict(list)
    for c in multi_cases:
        _, p_v3 = next(x for x in val3_preds["rca_v3"] if x[0].case_id == c.case_id)
        _, p_curr = next(x for x in val3_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
        _, _, f1_v3 = compute_set_f1(extract_predicted_root_set(p_v3, alpha=0.85), c.ground_truth.true_root_causes)
        _, _, f1_curr = compute_set_f1(extract_predicted_root_set(p_curr, alpha=0.85), c.ground_truth.true_root_causes)
        f1_v3_list.append(f1_v3)
        f1_curr_list.append(f1_curr)
        v11_fam_delta[c.family_id].append(f1_v3 - f1_curr)

    ci_v11_delta = bootstrap_ci_family_clustered(v11_fam_delta, n_bootstrap=1000)
    mean_f1_v3 = sum(f1_v3_list) / n_multi * 100 if n_multi else 0.0
    mean_f1_curr = sum(f1_curr_list) / n_multi * 100 if n_multi else 0.0
    v11_met = (mean_f1_v3 > mean_f1_curr)
    print(f"\n(V11) Multi-Root Set F1 Delta (N={n_multi} >= 30, alpha=0.85 frozen on dev3):")
    print(f"  CurrentScorer Mean F1: {mean_f1_curr:.1f}%")
    print(f"  rca_v3 Mean F1:        {mean_f1_v3:.1f}%")
    print(f"  Paired Delta:          {mean_f1_v3 - mean_f1_curr:+.1f} pp [{ci_v11_delta[0]*100:.1f}, {ci_v11_delta[1]*100:.1f}]")
    print(f"  Result: {'LITERALLY MET' if v11_met else 'NOT MET'}")

    # (V12) Hallucination Rate & False Abstention Rate
    ctrl_cases = [c for c in val3_cases if "idle_silent_control" in c.case_id or (c.perturbations and "idle_silent_control" in c.perturbations)]
    n_ctrl = len(ctrl_cases)
    v12_v3_halluc = 0
    v12_curr_halluc = 0
    for c in ctrl_cases:
        _, p_v3 = next(x for x in val3_preds["rca_v3"] if x[0].case_id == c.case_id)
        _, p_curr = next(x for x in val3_preds["CurrentScorer_CaseTopology"] if x[0].case_id == c.case_id)
        obs = set(e.service for e in c.events)
        tr = set(c.ground_truth.true_root_causes)
        v3_p = [x.strip() for x in p_v3.predicted_root_causes if x.strip() and x.strip() != "NONE"]
        curr_p = [x.strip() for x in p_curr.predicted_root_causes if x.strip() and x.strip() != "NONE"]
        if any(p not in obs and p not in tr for p in v3_p):
            v12_v3_halluc += 1
        if any(p not in obs and p not in tr for p in curr_p):
            v12_curr_halluc += 1

    v12_false_abst = sum(1 for c, p in val3_preds["rca_v3"] if c in val3_diag and p.verdict == "ABSTAIN")
    false_abst_rate = v12_false_abst / n_diag * 100 if n_diag else 0.0
    v12_met = (v12_v3_halluc <= v12_curr_halluc and false_abst_rate <= 15.0)
    print(f"\n(V12) Hallucination Rate (Controls N={n_ctrl} >= 30) & False Abstention:")
    print(f"  CurrentScorer Hallucinations: {v12_curr_halluc}/{n_ctrl} ({v12_curr_halluc/n_ctrl*100:.1f}%)")
    print(f"  rca_v3 Hallucinations:        {v12_v3_halluc}/{n_ctrl} ({v12_v3_halluc/n_ctrl*100:.1f}%)")
    print(f"  False Abstention Rate:        {v12_false_abst}/{n_diag} ({false_abst_rate:.1f}%, target <= 15%)")
    print(f"  Result: {'LITERALLY MET' if v12_met else 'NOT MET'}")


if __name__ == "__main__":
    run_stage3c()
