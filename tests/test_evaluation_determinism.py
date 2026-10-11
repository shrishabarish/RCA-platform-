import json
import hashlib
import pytest
from pathlib import Path

from evaluation.schema import EvaluationCase
from evaluation.baselines.current_scorer import CurrentScorerBaseline
from evaluation.baselines.earliest_anomaly import EarliestAnomalyBaseline
from evaluation.baselines.pagerank_only import PageRankBaseline
from evaluation.baselines.highest_severity import HighestSeverityBaseline

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_case_files_sha256_integrity():
    """Verifies that all 91 evaluation case files match their manifest SHA-256 hashes exactly."""
    manifest_path = PROJECT_ROOT / "evaluation" / "manifest.json"
    assert manifest_path.exists(), "Manifest file not found"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    assert len(files) == manifest["total_cases"], f"Expected {manifest['total_cases']} manifest files, found {len(files)}"
    assert len(files) == 225, f"Expected 225 manifest files for v1.1.0, found {len(files)}"

    for rel_path, meta in files.items():
        file_path = PROJECT_ROOT / rel_path
        assert file_path.exists(), f"Evaluation case file missing: {rel_path}"

        # Compute SHA-256
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        actual_sha = h.hexdigest()

        assert actual_sha == meta["sha256"], (
            f"Case file {rel_path} has been tampered with or modified! "
            f"Manifest SHA: {meta['sha256']}, actual SHA: {actual_sha}"
        )


def test_baseline_determinism_byte_identical():
    """Verifies that running baselines twice on the same cases yields byte-identical outputs."""
    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    case_files = sorted(list(dev_dir.glob("*.json")))[:5]
    assert len(case_files) == 5

    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in case_files]
    baselines = [
        CurrentScorerBaseline(),
        EarliestAnomalyBaseline(),
        PageRankBaseline(),
        HighestSeverityBaseline()
    ]

    for b in baselines:
        run1 = [b.predict(c).model_dump_json() for c in cases]
        run2 = [b.predict(c).model_dump_json() for c in cases]
        assert run1 == run2, f"Baseline {b.name} produced non-deterministic results between runs!"
