import csv
import subprocess
from pathlib import Path

def get_file_commit(path: Path) -> str:
    """Gets the short commit hash of when the file was last committed or created."""
    try:
        res = subprocess.run(
            ["git", "log", "-n", "1", "--format=%h", "--", str(path)],
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip() or "uncommitted"
    except Exception:
        return "unknown"

results_dir = Path("evaluation/results")
csv_paths = sorted(results_dir.glob("*.csv"))

print(f"{'CSV Filename':<40} {'Rows':<6} {'Top-1 Acc':<12} {'Conf-Wrong':<12} {'Commit':<10}")
print("=" * 85)

audit_data = []

for cp in csv_paths:
    with open(cp, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        total = len(rows)
        fieldnames = reader.fieldnames or []
        
        # Determine engine & accuracy
        # Try different column conventions:
        acc_str = "N/A"
        cw_str = "N/A"
        
        # Check rca_v2 columns
        if "rca_v2_correct" in fieldnames:
            correct = sum(1 for r in rows if r.get("rca_v2_correct", "").lower() in ("true", "1"))
            acc_str = f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "0%"
            if "rca_v2_confident_wrong" in fieldnames:
                cw = sum(1 for r in rows if r.get("rca_v2_confident_wrong", "").lower() in ("true", "1"))
                cw_str = f"{cw}/{total} ({cw/total*100:.1f}%)" if total else "0%"
        elif "is_top1" in fieldnames:
            correct = sum(1 for r in rows if r.get("is_top1", "").lower() in ("true", "1"))
            acc_str = f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "0%"
        elif "match_ground_truth" in fieldnames:
            correct = sum(1 for r in rows if r.get("match_ground_truth", "").lower() in ("true", "1"))
            acc_str = f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "0%"
        elif "CurrentScorer_CaseTopology_correct" in fieldnames:
            correct = sum(1 for r in rows if r.get("CurrentScorer_CaseTopology_correct", "").lower() in ("true", "1"))
            acc_str = f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "0%"
            if "CurrentScorer_CaseTopology_confident_wrong" in fieldnames:
                cw = sum(1 for r in rows if r.get("CurrentScorer_CaseTopology_confident_wrong", "").lower() in ("true", "1"))
                cw_str = f"{cw}/{total} ({cw/total*100:.1f}%)" if total else "0%"
        elif "Current_5Factor_Ensemble_top1_correct" in fieldnames:
            correct = sum(1 for r in rows if r.get("Current_5Factor_Ensemble_top1_correct", "").lower() in ("true", "1"))
            acc_str = f"{correct}/{total} ({correct/total*100:.1f}%)" if total else "0%"
            if "Current_5Factor_Ensemble_confident_wrong" in fieldnames:
                cw = sum(1 for r in rows if r.get("Current_5Factor_Ensemble_confident_wrong", "").lower() in ("true", "1"))
                cw_str = f"{cw}/{total} ({cw/total*100:.1f}%)" if total else "0%"

        commit_hash = get_file_commit(cp)
        print(f"{cp.name:<40} {total:<6} {acc_str:<12} {cw_str:<12} {commit_hash:<10}")
        audit_data.append({
            "filename": cp.name,
            "path": str(cp),
            "total_cases": total,
            "accuracy": acc_str,
            "confident_wrong": cw_str,
            "commit": commit_hash
        })
