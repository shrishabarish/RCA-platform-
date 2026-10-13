import re
import csv
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


def test_evaluation_tab_renders_dynamically_from_api_eval_summary():
    """
    Track B requirement 3:
    Evaluation tab renders from /api/eval/summary, not static HTML.
    Remove the typed numbers. State which engine each row is and the denominator.
    """
    html_path = PROJECT_ROOT / "frontend" / "index.html"
    assert html_path.exists(), "frontend/index.html must exist"
    html_content = html_path.read_text(encoding="utf-8")

    # Locate the evaluation table in tab-evaluation
    assert 'id="tab-evaluation"' in html_content, "Evaluation tab missing in HTML"
    assert '<tbody id="evalSummaryTableBody">' in html_content, "Dynamic tbody id missing in Evaluation tab"

    # Verify typed numbers were removed from static HTML tbody
    tab_eval_match = re.search(r'<tbody id="evalSummaryTableBody">(.*?)</tbody>', html_content, re.DOTALL)
    assert tab_eval_match is not None
    static_tbody_content = tab_eval_match.group(1).strip()
    assert "64.0%" not in static_tbody_content, "Typed static numbers must be removed from HTML tbody"
    assert "94.0%" not in static_tbody_content, "Typed static numbers must be removed from HTML tbody"


def test_api_eval_summary_numbers_match_csvs():
    """
    Verifies that /api/eval/summary accurately computes all denominators,
    engines, accuracy rates, and commit hashes directly from the whole CSVs.
    """
    resp = client.get("/api/eval/summary")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 4, f"Expected at least 4 evaluation rows, got {len(rows)}"

    for row in rows:
        split_name = row["split"]
        csv_rel = row["csv_path"]
        engine = row["engine"]
        cases_count = row["denominator"]
        acc_pct = row["top1_accuracy_pct"]
        cw_pct = row.get("confident_wrong_pct")
        commit_hash = row["commit"]

        csv_path = PROJECT_ROOT / csv_rel
        assert csv_path.exists(), f"CSV file {csv_path} does not exist"

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data_rows = list(reader)
            actual_count = len(data_rows)
            fieldnames = reader.fieldnames or []

            assert cases_count == actual_count, (
                f"Row '{split_name}': Displayed denominator ({cases_count}) != actual CSV row count ({actual_count})"
            )

            cols_used = row.get("columns_used", [])
            top1_col = cols_used[0] if cols_used else None
            assert top1_col and top1_col in fieldnames, f"Top1 column {top1_col} not in CSV {csv_rel}"
            corr = sum(1 for r in data_rows if str(r.get(top1_col, "")).strip().lower() in ("true", "1"))
            actual_acc = round(corr / actual_count * 100.0, 1)

            assert abs(acc_pct - actual_acc) <= 0.1, (
                f"Row '{split_name}' engine '{engine}': API accuracy ({acc_pct}%) != recomputed CSV accuracy ({actual_acc}%)"
            )

            if cw_pct is not None and len(cols_used) > 1:
                cw_col = cols_used[1]
                if cw_col in fieldnames:
                    cw_count = sum(1 for r in data_rows if str(r.get(cw_col, "")).strip().lower() in ("true", "1"))
                    actual_cw = round(cw_count / actual_count * 100.0, 1)
                    assert abs(cw_pct - actual_cw) <= 0.1
