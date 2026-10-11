import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from evaluation.baselines.current_scorer import CurrentScorerBaseline
from evaluation.baselines.earliest_anomaly import EarliestAnomalyBaseline
from evaluation.baselines.pagerank_only import PageRankBaseline
from evaluation.baselines.highest_severity import HighestSeverityBaseline
from evaluation.metrics.engine import (
    compute_top_k_accuracy,
    compute_multiroot_f1,
    compute_abstention_metrics,
    compute_confident_wrong_rate,
    compute_correlation_merge_split_rates,
)

AUDIT_LOG_FILE = PROJECT_ROOT / "evaluation" / "test_split_audit.log"
CONFIG_FILE = PROJECT_ROOT / "evaluation" / "config.json"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"


def load_harness_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {"confident_wrong_threshold": 75.0}


def load_cases(split: str) -> List[EvaluationCase]:
    cases: List[EvaluationCase] = []
    cases_root = PROJECT_ROOT / "evaluation" / "cases"
    
    if split == "dev":
        target_dir = cases_root / "generated" / "dev"
        files = sorted(list(target_dir.glob("*.json")))
    elif split == "test":
        target_dir = cases_root / "generated" / "test"
        files = sorted(list(target_dir.glob("*.json")))
    elif split == "handwritten":
        target_dir = cases_root / "handwritten"
        files = sorted(list(target_dir.glob("*.json")))
    else:
        raise ValueError(f"Unknown split: {split}")

    for f in files:
        cases.append(EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")))
    return cases


def log_test_split_run(args):
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "FINAL_EVALUATION_RUN",
            "split": "test",
            "flags": sys.argv[1:],
            "authorized": True
        }
        f.write(json.dumps(log_entry) + "\n")


def run_evaluation(
    cases: List[EvaluationCase],
    baselines: List[RCABaselineAdapter],
    conf_thresh: float = 75.0
) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Any]] = {}

    for b in baselines:
        preds: List[Tuple[EvaluationCase, BaselineResult]] = []
        for c in cases:
            pred = b.predict(c)
            preds.append((c, pred))

        top1 = compute_top_k_accuracy(preds, k=1)
        top3 = compute_top_k_accuracy(preds, k=3)
        multi_f1 = compute_multiroot_f1(preds)
        abs_p, abs_r = compute_abstention_metrics(preds)
        conf_wrong = compute_confident_wrong_rate(preds, confidence_threshold=conf_thresh)
        
        results[b.name] = {
            "top1": top1,
            "top3": top3,
            "multi_f1": multi_f1,
            "abstention_precision": abs_p,
            "abstention_recall": abs_r,
            "confident_wrong": conf_wrong,
            "predictions": preds
        }

    merge_rate, split_rate = compute_correlation_merge_split_rates(cases)
    
    return {
        "baselines": results,
        "correlation": {
            "false_merge_rate": merge_rate,
            "false_split_rate": split_rate
        }
    }


def save_per_case_results_file(
    cases: List[EvaluationCase],
    baselines: List[RCABaselineAdapter],
    b_results: Dict[str, Dict[str, Any]],
    split: str,
    conf_thresh: float
) -> Tuple[str, str]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ts_csv = RESULTS_DIR / f"eval_{split}_{ts}.csv"
    latest_csv = RESULTS_DIR / f"latest_{split}.csv"

    fieldnames = [
        "case_id", "family_id", "base_case_id", "split", "topology_size", "perturbation",
        "is_hard", "expected_verdict", "true_roots", "num_true_roots"
    ]
    for b in baselines:
        fieldnames.extend([
            f"{b.name}_verdict",
            f"{b.name}_pred_top1",
            f"{b.name}_pred_top3",
            f"{b.name}_confidence",
            f"{b.name}_top1_correct",
            f"{b.name}_top3_correct",
            f"{b.name}_confident_wrong"
        ])

    rows = []
    case_count = len(cases)
    for idx in range(case_count):
        c = cases[idx]
        true_roots = c.ground_truth.true_root_causes
        pert = c.perturbations[0] if c.perturbations else "none"
        row = {
            "case_id": c.case_id,
            "family_id": c.family_id,
            "base_case_id": c.base_case_id or "",
            "split": split,
            "topology_size": len(c.topology.nodes),
            "perturbation": pert,
            "is_hard": c.is_hard,
            "expected_verdict": c.ground_truth.expected_verdict,
            "true_roots": ";".join(true_roots),
            "num_true_roots": len(true_roots)
        }
        for b in baselines:
            _, pred = b_results[b.name]["predictions"][idx]
            preds = pred.predicted_root_causes
            top1 = preds[0] if preds else "NONE"
            top3 = ";".join(preds[:3])
            
            # Correctness flags
            if c.ground_truth.expected_verdict == "ABSTAIN":
                t1_corr = (pred.verdict == "ABSTAIN")
                t3_corr = (pred.verdict == "ABSTAIN")
                is_wrong = (pred.verdict != "ABSTAIN")
            else:
                t1_corr = (top1 in true_roots) if len(true_roots) == 1 else False
                t3_corr = any(r in preds[:3] for r in true_roots) if len(true_roots) == 1 else False
                is_wrong = (top1 not in true_roots)

            is_cw = is_wrong and (pred.confidence >= conf_thresh)

            row[f"{b.name}_verdict"] = pred.verdict
            row[f"{b.name}_pred_top1"] = top1
            row[f"{b.name}_pred_top3"] = top3
            row[f"{b.name}_confidence"] = round(pred.confidence, 1)
            row[f"{b.name}_top1_correct"] = t1_corr
            row[f"{b.name}_top3_correct"] = t3_corr
            row[f"{b.name}_confident_wrong"] = is_cw
        rows.append(row)

    for p in [ts_csv, latest_csv]:
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return str(ts_csv), str(latest_csv)


def format_cell_kn(k: int, n: int) -> str:
    if n == 0:
        return "N/A"
    flag = " (!)" if n < 10 else ""
    if n < 5:
        return f"{k}/{n}{flag}"
    pct = (k / n) * 100.0
    return f"{k}/{n} ({pct:.1f}%){flag}"


def main():
    parser = argparse.ArgumentParser(description="CloudLens RCA Evaluation Harness")
    parser.add_argument("--split", choices=["dev", "test", "handwritten"], default="dev", help="Dataset split to evaluate")
    parser.add_argument("--final", action="store_true", help="Authorize execution on immutable TEST split")
    args = parser.parse_args()

    # ENFORCE HARD GUARD AGAINST RUNNING TEST SPLIT WITHOUT --final
    if args.split == "test" and not args.final:
        print("=" * 80)
        print("REFUSAL: Attempted execution on TEST split without explicit --final flag!")
        print("To prevent data snooping, iterations must run against the DEVELOPMENT split.")
        print("Pass --final to authorize a final recorded run.")
        print("=" * 80)
        sys.exit(2)

    if args.split == "test":
        log_test_split_run(args)
        print(f"[AUDIT LOGGED] Running authorized final test split evaluation at {datetime.now(timezone.utc).isoformat()}")

    config = load_harness_config()
    conf_thresh = config.get("confident_wrong_threshold", 75.0)

    cases = load_cases(args.split)
    print(f"Loaded {len(cases)} cases from split '{args.split}'.")

    baselines = [
        CurrentScorerBaseline(),
        EarliestAnomalyBaseline(),
        PageRankBaseline(),
        HighestSeverityBaseline()
    ]

    eval_output = run_evaluation(cases, baselines, conf_thresh)
    b_results = eval_output["baselines"]

    # Save per-case results CSV on every run
    ts_csv, latest_csv = save_per_case_results_file(cases, baselines, b_results, args.split, conf_thresh)
    print(f"Per-case results saved to: {ts_csv}")
    print(f"Latest results updated at: {latest_csv}")

    # Summary table
    print("\n" + "=" * 120)
    print(f"RCA EVALUATION HARNESS RESULTS — SPLIT: {args.split.upper()} (N = {len(cases)})")
    print(f"Confident-Wrong Threshold: score >= {conf_thresh}% | CI: Empirical Bootstrap 95%")
    print("=" * 120)
    print(f"{'Baseline':<26} | {'Top-1 Acc (95% CI)':<22} | {'Top-3 Acc (95% CI)':<22} | {'MultiRoot F1':<15} | {'Conf-Wrong':<16} | {'Abstention P / R':<16}")
    print("-" * 120)

    for b in baselines:
        res = b_results[b.name]
        t1 = res["top1"]
        t3 = res["top3"]
        mf1 = res["multi_f1"]
        cw = res["confident_wrong"]
        abs_p = res["abstention_precision"]
        abs_r = res["abstention_recall"]

        t1_str = f"{t1.value:.1f}% [{t1.ci_lower:.1f}-{t1.ci_upper:.1f}]" if not t1.is_na else "N/A"
        t3_str = f"{t3.value:.1f}% [{t3.ci_lower:.1f}-{t3.ci_upper:.1f}]" if not t3.is_na else "N/A"
        mf1_str = f"{mf1.value:.1f}%" if not mf1.is_na else "N/A"
        cw_str = f"{cw.value:.1f}% [{cw.ci_lower:.1f}-{cw.ci_upper:.1f}]" if not cw.is_na else "N/A"
        abs_str = "N/A (Never)" if abs_p.is_na else f"P={abs_p.value:.1f}% R={abs_r.value:.1f}%"

        print(f"{b.name:<26} | {t1_str:<22} | {t3_str:<22} | {mf1_str:<15} | {cw_str:<16} | {abs_str:<16}")

    print("-" * 120)
    corr = eval_output["correlation"]
    fm_val = f"{corr['false_merge_rate'].value:.1f}%" if not corr['false_merge_rate'].is_na else "N/A"
    fs_val = f"{corr['false_split_rate'].value:.1f}%" if not corr['false_split_rate'].is_na else "N/A"
    print(f"In-Memory Correlation: False Merge Rate = {fm_val} (N={corr['false_merge_rate'].sample_size}) | False Split Rate = {fs_val} (N={corr['false_split_rate'].sample_size})")
    print(f"Time to Converge: NOT APPLICABLE (Deferred to later stage)")
    print("=" * 120)

    # Hard-Subset Analysis
    hard_cases = [c for c in cases if c.is_hard and c.ground_truth.expected_verdict == "DIAGNOSE"]
    if hard_cases:
        print("\n" + "=" * 90)
        print(f"HARD SUBSET ANALYSIS — ROOT NOT EARLIEST (N = {len(hard_cases)})")
        print("=" * 90)
        print(f"{'Baseline':<26} | {'Top-1 Correct (k/n)':<25} | {'Status vs <60% Target':<25}")
        print("-" * 90)
        for b in baselines:
            preds = [p for c, p in b_results[b.name]["predictions"] if c.is_hard and c.ground_truth.expected_verdict == "DIAGNOSE"]
            k = sum(1 for p in preds if p.predicted_root_causes and p.predicted_root_causes[0] in [c.ground_truth.true_root_causes[0] for c in hard_cases if c.case_id == p.case_id])
            n = len(hard_cases)
            pct = (k / n) * 100.0 if n > 0 else 0.0
            status = "PASSED (< 60%)" if pct < 60.0 else "EXCEEDS TARGET"
            print(f"{b.name:<26} | {k}/{n} ({pct:.1f}%)" + f"{'':<10}" + f" | {status}")
        print("=" * 90)

    # Paired Perturbation Deltas Table
    # A family contains a base case plus perturbations of the exact same scenario
    families = sorted(list(set(c.family_id for c in cases)))
    base_case_map = {c.family_id: c for c in cases if c.base_case_id is None}
    
    print("\n" + "=" * 110)
    print("PAIRED PERTURBATION DELTAS (Base Accuracy - Perturbed Accuracy)")
    print("Positive delta = perturbation degraded performance by X percentage points")
    print("=" * 110)
    all_perts = sorted(list(set(c.perturbations[0] for c in cases if c.perturbations)))
    print(f"{'Perturbation':<32} | {'Count (n)':<10} | " + " | ".join(f"{b.name[:14]:<14}" for b in baselines))
    print("-" * 110)

    for pert in all_perts:
        pert_cases = [c for c in cases if c.perturbations and c.perturbations[0] == pert and c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_p = len(pert_cases)
        flag = " (!)" if n_p < 10 else ""
        row = f"{pert:<32} | {n_p}{flag:<6} | "
        cols = []
        for b in baselines:
            # For each paired case, check if base was correct and if pert was correct
            base_hits = 0
            pert_hits = 0
            valid_pairs = 0
            for pc in pert_cases:
                bc = base_case_map.get(pc.family_id)
                if not bc or bc.ground_truth.expected_verdict != "DIAGNOSE":
                    continue
                # Retrieve predictions
                bp = [p for c, p in b_results[b.name]["predictions"] if c.case_id == bc.case_id][0]
                pp = [p for c, p in b_results[b.name]["predictions"] if c.case_id == pc.case_id][0]
                
                b_corr = (bp.predicted_root_causes[0] in bc.ground_truth.true_root_causes) if bp.predicted_root_causes else False
                p_corr = (pp.predicted_root_causes[0] in pc.ground_truth.true_root_causes) if pp.predicted_root_causes else False
                if b_corr: base_hits += 1
                if p_corr: pert_hits += 1
                valid_pairs += 1

            if valid_pairs >= 5:
                delta = ((base_hits - pert_hits) / valid_pairs) * 100.0
                sign = "+" if delta > 0 else ""
                cols.append(f"{sign}{delta:.1f}%")
            elif valid_pairs > 0:
                delta_cnt = base_hits - pert_hits
                cols.append(f"{delta_cnt:+d}/{valid_pairs}")
            else:
                cols.append("N/A")
        print(row + " | ".join(f"{col:<14}" for col in cols))
    print("=" * 110)

    # Breakdown by Topology Size
    print("\n" + "=" * 90)
    print("BREAKDOWN BY TOPOLOGY SIZE (Top-1 Accuracy in k/n)")
    print("=" * 90)
    topo_sizes = sorted(list(set(len(c.topology.nodes) for c in cases)))
    header = f"{'Topology Size':<15} | " + " | ".join(f"{b.name[:15]:<15}" for b in baselines)
    print(header)
    print("-" * 90)
    for t_size in topo_sizes:
        row = f"{f'{t_size}-node':<15} | "
        cols = []
        for b in baselines:
            sub_preds = [(c, p) for c, p in b_results[b.name]["predictions"] if len(c.topology.nodes) == t_size and len(c.ground_truth.true_root_causes) == 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
            n = len(sub_preds)
            k = sum(1 for c, p in sub_preds if p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            cols.append(format_cell_kn(k, n))
        print(row + " | ".join(f"{col:<15}" for col in cols))
    print("=" * 90)

    # Breakdown by Perturbation Type
    print("\n" + "=" * 100)
    print("BREAKDOWN BY PERTURBATION TYPE (Top-1 Accuracy in k/n)")
    print("=" * 100)
    print(f"{'Perturbation':<32} | " + " | ".join(f"{b.name[:14]:<14}" for b in baselines))
    print("-" * 100)
    for pert in ["none"] + all_perts:
        sub_preds = [(c, p) for c, p in b_results[b.name]["predictions"] if (c.perturbations[0] if c.perturbations else "none") == pert and len(c.ground_truth.true_root_causes) == 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
        n = len(sub_preds)
        row = f"{pert:<32} | "
        cols = []
        for b in baselines:
            b_sub = [(c, p) for c, p in b_results[b.name]["predictions"] if (c.perturbations[0] if c.perturbations else "none") == pert and len(c.ground_truth.true_root_causes) == 1 and c.ground_truth.expected_verdict == "DIAGNOSE"]
            k = sum(1 for c, p in b_sub if p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes)
            cols.append(format_cell_kn(k, n))
        print(row + " | ".join(f"{col:<14}" for col in cols))
    print("=" * 100)
    print("(! Note: cells marked with (!) have fewer than 10 cases; percentages omitted for n < 5.)\n")


if __name__ == "__main__":
    main()
