import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import EvaluationCase
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
            "split": args.split,
            "final_flag": args.final,
            "pid": os.getpid()
        }
        f.write(json.dumps(log_entry) + "\n")


def run_evaluation(cases: List[EvaluationCase], baselines: list, conf_thresh: float) -> Dict[str, Any]:
    results = {}
    
    # 1. Global metrics per baseline
    for b in baselines:
        preds = []
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

    # 2. Correlator merge/split metrics (model-independent)
    merge_rate, split_rate = compute_correlation_merge_split_rates(cases)
    
    return {
        "baselines": results,
        "correlation": {
            "false_merge_rate": merge_rate,
            "false_split_rate": split_rate
        }
    }


def main():
    parser = argparse.ArgumentParser(description="CloudLens RCA Evaluation Harness")
    parser.add_argument("--split", choices=["dev", "test", "handwritten"], default="dev", help="Dataset split to evaluate")
    parser.add_argument("--final", action="store_true", help="Authorize execution on immutable TEST split")
    parser.add_argument("--out-dir", type=str, default="evaluation/reports", help="Directory to save report outputs")
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

    # Print summary table
    print("\n" + "=" * 115)
    print(f"RCA EVALUATION HARNESS RESULTS — SPLIT: {args.split.upper()} (N = {len(cases)})")
    print(f"Confident-Wrong Threshold: score >= {conf_thresh}% | CI: Empirical Bootstrap 95%")
    print("=" * 115)
    print(f"{'Baseline':<26} | {'Top-1 Acc (95% CI)':<22} | {'Top-3 Acc (95% CI)':<22} | {'MultiRoot F1':<15} | {'Conf-Wrong':<14} | {'Abstention':<12}")
    print("-" * 115)

    for b in baselines:
        res = b_results[b.name]
        t1 = res["top1"]
        t3 = res["top3"]
        mf1 = res["multi_f1"]
        cw = res["confident_wrong"]
        abs_p = res["abstention_precision"]

        t1_str = f"{t1.value:.1f}% [{t1.ci_lower:.1f}-{t1.ci_upper:.1f}]" if not t1.is_na else "N/A"
        t3_str = f"{t3.value:.1f}% [{t3.ci_lower:.1f}-{t3.ci_upper:.1f}]" if not t3.is_na else "N/A"
        mf1_str = f"{mf1.value:.1f}%" if not mf1.is_na else "N/A"
        cw_str = f"{cw.value:.1f}% [{cw.ci_lower:.1f}-{cw.ci_upper:.1f}]" if not cw.is_na else "N/A"
        abs_str = "N/A (Never)" if abs_p.is_na else f"P={abs_p.value:.1f}%"

        print(f"{b.name:<26} | {t1_str:<22} | {t3_str:<22} | {mf1_str:<15} | {cw_str:<14} | {abs_str:<12}")

    print("-" * 115)
    corr = eval_output["correlation"]
    print(f"In-Memory Correlation: False Merge Rate = {corr['false_merge_rate'].value:.1f}% (N={corr['false_merge_rate'].sample_size}) | False Split Rate = {corr['false_split_rate'].value:.1f}% (N={corr['false_split_rate'].sample_size})")
    print(f"Time to Converge: NOT APPLICABLE (Deferred to later stage)")
    print("=" * 115)

    # Breakdown by Topology Size
    print("\n" + "=" * 90)
    print("BREAKDOWN BY TOPOLOGY SIZE (Top-1 Accuracy)")
    print("=" * 90)
    topo_sizes = sorted(list(set(len(c.topology.nodes) for c in cases)))
    header = f"{'Topology Size':<15} | " + " | ".join(f"{b.name[:15]:<15}" for b in baselines)
    print(header)
    print("-" * 90)
    for t_size in topo_sizes:
        sub_cases = [c for c in cases if len(c.topology.nodes) == t_size]
        n_sub = len(sub_cases)
        flag = "* [N<10]" if n_sub < 10 else f" (N={n_sub})"
        row = f"{f'{t_size}-node' + flag:<15} | "
        cols = []
        for b in baselines:
            sub_preds = [(c, p) for c, p in b_results[b.name]["predictions"] if len(c.topology.nodes) == t_size]
            t1_sub = compute_top_k_accuracy(sub_preds, k=1)
            cols.append(f"{t1_sub.value:.1f}%" if not t1_sub.is_na else "N/A")
        print(row + " | ".join(f"{col:<15}" for col in cols))

    # Breakdown by Perturbation Type
    print("\n" + "=" * 90)
    print("BREAKDOWN BY PERTURBATION TYPE (Top-1 Accuracy)")
    print("=" * 90)
    all_perts = sorted(list(set(c.perturbations[0] if c.perturbations else "none" for c in cases)))
    header_pert = f"{'Perturbation':<30} | " + " | ".join(f"{b.name[:12]:<12}" for b in baselines)
    print(header_pert)
    print("-" * 90)
    for pert in all_perts:
        sub_cases = [c for c in cases if (c.perturbations[0] if c.perturbations else "none") == pert]
        n_pert = len(sub_cases)
        flag = "* [N<10]" if n_pert < 10 else f" (N={n_pert})"
        row_title = f"{pert} {flag}"
        row = f"{row_title[:30]:<30} | "
        cols = []
        for b in baselines:
            sub_preds = [(c, p) for c, p in b_results[b.name]["predictions"] if (c.perturbations[0] if c.perturbations else "none") == pert]
            t1_sub = compute_top_k_accuracy(sub_preds, k=1)
            cols.append(f"{t1_sub.value:.1f}%" if not t1_sub.is_na else "N/A")
        print(row + " | ".join(f"{col:<12}" for col in cols))
    print("=" * 90)
    print("(* Note: cells marked with * [N<10] have fewer than 10 sample cases.)\n")


if __name__ == "__main__":
    main()
