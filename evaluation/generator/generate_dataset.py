import sys
import os
import json
import hashlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.generator.topology_builder import get_topology_by_size
from evaluation.generator.simulator import (
    simulate_case,
    create_hard_variant,
    create_abstain_variant,
    create_multi_incident_variant,
)
from evaluation.generator.perturbations import (
    apply_drop_one_signal,
    apply_drop_all_signals_one_source,
    apply_delay_and_reorder,
    apply_duplicate,
    apply_clock_skew,
    apply_second_independent_failure,
    apply_severe_decoy,
    apply_change_topology_mid_incident,
)

GENERATOR_VERSION = "1.1.0"
BASE_SEED = 42

PERTURBATION_OPERATORS = [
    ("drop_one_signal", apply_drop_one_signal),
    ("drop_all_signals_one_source", apply_drop_all_signals_one_source),
    ("delay_and_reorder", apply_delay_and_reorder),
    ("duplicate", apply_duplicate),
    ("clock_skew", apply_clock_skew),
    ("second_independent_failure", apply_second_independent_failure),
    ("severe_decoy", apply_severe_decoy),
    ("change_topology_mid_incident", apply_change_topology_mid_incident),
]

HARD_TYPES = [
    "slow_burn",
    "pre_incident_alert",
    "flapping_decoy",
    "root_clock_skew",
    "missing_root_telemetry",
]

ABSTAIN_TYPES = [
    "source_down",
    "pipeline_outage",
    "contradictory",
    "quiet",
]

MULTI_INCIDENT_TYPES = [
    "separated_time",
    "disjoint_topological",
]


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_all():
    rng = random.Random(BASE_SEED)
    
    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    test_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "test"
    hw_dir = PROJECT_ROOT / "evaluation" / "cases" / "handwritten"
    audit_log = PROJECT_ROOT / "evaluation" / "test_split_audit.log"
    
    # Clean previous generated files
    for d in [dev_dir, test_dir]:
        if d.exists():
            for f in d.glob("*.json"):
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)
        
    total_families = 75
    # 45 Dev families, 30 Test families (60/40)
    dev_family_indices = list(range(1, 46))
    test_family_indices = list(range(46, 76))

    manifest_files: Dict[str, Dict[str, Any]] = {}

    def process_family(fam_idx: int, split_name: str, target_dir: Path):
        fam_id = f"fam_gen_{fam_idx:03d}"
        seed = BASE_SEED + fam_idx * 31
        fam_rng = random.Random(seed)
        
        # Stratified topology sizes
        if fam_idx % 3 == 1:
            topo_size = 6
        elif fam_idx % 3 == 2:
            topo_size = 12
        else:
            topo_size = 20
            
        topo = get_topology_by_size(topo_size)
        base_case_id = f"case_{fam_id}_base"
        
        # Generate clean unperturbed base case
        base_case = simulate_case(
            topology=topo,
            family_id=fam_id,
            case_id=base_case_id,
            seed=seed,
            is_multi_root=False
        )
        base_case.generator_version = GENERATOR_VERSION
        
        cases_in_family = [base_case]
        
        # Family specialization:
        # Dev (45 families):
        # 1-15: Standard Perturbations (paired with base)
        # 16-25: Hard Cases where root is NOT earliest (paired with base)
        # 26-35: Abstention Cases with Diagnosable counterpart (paired with base)
        # 36-45: Multi-Incident Cases for False Merge (paired with base)
        
        # Test (30 families):
        # 46-57: Standard Perturbations
        # 58-63: Hard Cases
        # 64-69: Abstention Cases
        # 70-75: Multi-Incident Cases

        if (1 <= fam_idx <= 15) or (46 <= fam_idx <= 57):
            # Standard perturbation family: paired with 2 perturbation operators
            op_idx1 = (fam_idx * 2) % len(PERTURBATION_OPERATORS)
            op_idx2 = (fam_idx * 2 + 1) % len(PERTURBATION_OPERATORS)
            p_name1, p_fn1 = PERTURBATION_OPERATORS[op_idx1]
            p_name2, p_fn2 = PERTURBATION_OPERATORS[op_idx2]
            
            c1 = p_fn1(base_case, random.Random(seed + 101))
            c1.case_id = f"case_{fam_id}_{p_name1}"
            c1.base_case_id = base_case_id
            c1.generator_version = GENERATOR_VERSION
            cases_in_family.append(c1)
            
            c2 = p_fn2(base_case, random.Random(seed + 102))
            c2.case_id = f"case_{fam_id}_{p_name2}"
            c2.base_case_id = base_case_id
            c2.generator_version = GENERATOR_VERSION
            cases_in_family.append(c2)

        elif (16 <= fam_idx <= 25) or (58 <= fam_idx <= 63):
            # Hard cases family: root is NOT earliest
            h_type1 = HARD_TYPES[(fam_idx * 2) % len(HARD_TYPES)]
            h_type2 = HARD_TYPES[(fam_idx * 2 + 1) % len(HARD_TYPES)]
            
            c1 = create_hard_variant(base_case, h_type1, f"case_{fam_id}_hard_{h_type1}", seed + 201)
            c2 = create_hard_variant(base_case, h_type2, f"case_{fam_id}_hard_{h_type2}", seed + 202)
            cases_in_family.append(c1)
            cases_in_family.append(c2)

        elif (26 <= fam_idx <= 35) or (64 <= fam_idx <= 69):
            # Abstention cases family: paired with 2 abstain variants
            # Dev gets 10 families * 2 = 20 abstain-required cases (exact >= 20 constraint)
            # Test gets 6 families * 2 = 12 abstain-required cases (exact >= 12 constraint)
            a_type1 = ABSTAIN_TYPES[(fam_idx * 2) % len(ABSTAIN_TYPES)]
            a_type2 = ABSTAIN_TYPES[(fam_idx * 2 + 1) % len(ABSTAIN_TYPES)]
            
            c1 = create_abstain_variant(base_case, a_type1, f"case_{fam_id}_abstain_{a_type1}", seed + 301)
            c2 = create_abstain_variant(base_case, a_type2, f"case_{fam_id}_abstain_{a_type2}", seed + 302)
            cases_in_family.append(c1)
            cases_in_family.append(c2)

        else:
            # Multi-incident cases family: tests False Merge Rate
            m_type = MULTI_INCIDENT_TYPES[fam_idx % len(MULTI_INCIDENT_TYPES)]
            c1 = create_multi_incident_variant(base_case, m_type, f"case_{fam_id}_multi_{m_type}", seed + 401)
            cases_in_family.append(c1)

        # Write each case in the family to disk
        for c in cases_in_family:
            out_path = target_dir / f"{c.case_id}.json"
            out_path.write_text(json.dumps(c.model_dump(), indent=2), encoding="utf-8")
            
            sha = sha256_file(out_path)
            rel_path = str(out_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            pert_str = c.perturbations[0] if c.perturbations else "none"
            manifest_files[rel_path] = {
                "case_id": c.case_id,
                "family_id": c.family_id,
                "base_case_id": c.base_case_id,
                "split": split_name,
                "topology_size": topo_size,
                "perturbation": pert_str,
                "expected_verdict": c.ground_truth.expected_verdict,
                "is_hard": c.is_hard,
                "is_multi_incident": (len(c.ground_truth.incident_groups) > 1),
                "num_events": len(c.events),
                "sha256": sha
            }

    # Generate Dev Split
    print(f"Generating DEV split ({len(dev_family_indices)} families)...")
    for f_idx in dev_family_indices:
        process_family(f_idx, "dev", dev_dir)

    # Generate Test Split
    print(f"Generating TEST split ({len(test_family_indices)} families)...")
    for f_idx in test_family_indices:
        process_family(f_idx, "test", test_dir)

    # Log to test_split_audit.log that TEST split was replaced and never executed
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_log, "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "REPLACED_TEST_SPLIT",
            "generator_version": GENERATOR_VERSION,
            "test_families": len(test_family_indices),
            "details": "Test split regenerated as v1.1.0 and never executed."
        }
        f.write(json.dumps(log_entry) + "\n")
    print(f"[AUDIT LOGGED] Recorded test split regeneration in {audit_log}")

    # Process Handwritten Cases
    hw_cases_count = 0
    for hw_file in sorted(hw_dir.glob("*.json")):
        sha = sha256_file(hw_file)
        rel_path = str(hw_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        try:
            hw_data = json.loads(hw_file.read_text(encoding="utf-8"))
            cid = hw_data.get("case_id", hw_file.stem)
            fid = hw_data.get("family_id", "handwritten")
            ev = hw_data.get("ground_truth", {}).get("expected_verdict", "DIAGNOSE")
            nev = len(hw_data.get("events", []))
            pert = hw_data.get("perturbations", ["none"])
            pert_str = pert[0] if pert else "none"
            is_h = "hard" in hw_data.get("tags", [])
        except Exception:
            cid = hw_file.stem
            fid = "handwritten"
            ev = "DIAGNOSE"
            nev = 0
            pert_str = "none"
            is_h = False

        manifest_files[rel_path] = {
            "case_id": cid,
            "family_id": fid,
            "base_case_id": None,
            "split": "handwritten",
            "topology_size": 6,
            "perturbation": pert_str,
            "expected_verdict": ev,
            "is_hard": is_h,
            "is_multi_incident": False,
            "num_events": nev,
            "sha256": sha
        }
        hw_cases_count += 1

    # Manifest Breakdown Counts
    dev_cases = [f for f in manifest_files.values() if f["split"] == "dev"]
    test_cases = [f for f in manifest_files.values() if f["split"] == "test"]
    
    dev_abstain = sum(1 for c in dev_cases if c["expected_verdict"] == "ABSTAIN")
    test_abstain = sum(1 for c in test_cases if c["expected_verdict"] == "ABSTAIN")
    dev_hard = sum(1 for c in dev_cases if c["is_hard"])
    test_hard = sum(1 for c in test_cases if c["is_hard"])

    manifest = {
        "manifest_version": "1.1.0",
        "generator_version": GENERATOR_VERSION,
        "base_seed": BASE_SEED,
        "total_families": total_families,
        "dev_families_count": len(dev_family_indices),
        "test_families_count": len(test_family_indices),
        "total_cases": len(manifest_files),
        "split_counts": {
            "dev": len(dev_cases),
            "test": len(test_cases),
            "handwritten": hw_cases_count
        },
        "dev_stats": {
            "total_cases": len(dev_cases),
            "abstain_cases": dev_abstain,
            "hard_cases": dev_hard,
            "multi_incident_cases": sum(1 for c in dev_cases if c["is_multi_incident"])
        },
        "test_stats": {
            "total_cases": len(test_cases),
            "abstain_cases": test_abstain,
            "hard_cases": test_hard,
            "multi_incident_cases": sum(1 for c in test_cases if c["is_multi_incident"])
        },
        "files": manifest_files
    }

    manifest_path = PROJECT_ROOT / "evaluation" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest written to {manifest_path} with {len(manifest_files)} cases.")
    print(f"DEV: {len(dev_cases)} cases (Abstain={dev_abstain}, Hard={dev_hard})")
    print(f"TEST: {len(test_cases)} cases (Abstain={test_abstain}, Hard={test_hard})")
    return manifest


if __name__ == "__main__":
    generate_all()
