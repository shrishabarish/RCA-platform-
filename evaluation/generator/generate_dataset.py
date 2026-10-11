import sys
import os
import json
import hashlib
import random
from pathlib import Path
from typing import Dict, List, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.generator.topology_builder import get_topology_by_size
from evaluation.generator.simulator import simulate_case
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

GENERATOR_VERSION = "1.0"
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
    
    dev_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # 75 families: 25 of 6-node, 25 of 12-node, 25 of 20-node
    total_families = 75
    family_configs = []
    
    for i in range(total_families):
        fam_idx = i + 1
        fam_seed = BASE_SEED + fam_idx * 17
        fam_rng = random.Random(fam_seed)
        
        # Topology size distribution
        if fam_idx <= 25:
            topo_size = 6
        elif fam_idx <= 50:
            topo_size = 12
        else:
            topo_size = 20
            
        # ~30% multi-root
        is_multi_root = (fam_rng.random() < 0.3)
        
        # Pick 1 perturbation operator per family (cycle through 8 operators + clean control)
        # 1 out of 9 is unperturbed clean baseline; 8 out of 9 get 1 perturbation
        op_idx = i % (len(PERTURBATION_OPERATORS) + 1)
        if op_idx < len(PERTURBATION_OPERATORS):
            pert_name, pert_fn = PERTURBATION_OPERATORS[op_idx]
        else:
            pert_name, pert_fn = "none", None
            
        family_configs.append({
            "family_id": f"fam_gen_{fam_idx:03d}",
            "seed": fam_seed,
            "topo_size": topo_size,
            "is_multi_root": is_multi_root,
            "pert_name": pert_name,
            "pert_fn": pert_fn
        })
        
    # Split families: 60% Dev (45 families), 40% Test (30 families)
    # Stratified by topology size and perturbation to ensure balanced splits
    dev_families = []
    test_families = []
    
    for i, cfg in enumerate(family_configs):
        if (i % 5) in [0, 1, 2]: # 3 out of 5 = 60%
            dev_families.append(cfg)
        else: # 2 out of 5 = 40%
            test_families.append(cfg)
            
    print(f"Assigning {len(dev_families)} families to DEV, {len(test_families)} families to TEST.")
    
    manifest_files: Dict[str, Dict[str, Any]] = {}
    
    def process_split(split_name: str, families: List[Dict[str, Any]], target_dir: Path):
        counts = {"cases": 0, "by_topo": {}, "by_pert": {}}
        for cfg in families:
            topo = get_topology_by_size(cfg["topo_size"])
            case_id = f"case_{cfg['family_id']}"
            base_case = simulate_case(
                topology=topo,
                family_id=cfg["family_id"],
                case_id=case_id,
                seed=cfg["seed"],
                is_multi_root=cfg["is_multi_root"]
            )
            
            # Apply family's specific perturbation operator if assigned
            if cfg["pert_fn"] is not None:
                final_case = cfg["pert_fn"](base_case, random.Random(cfg["seed"] + 999))
            else:
                final_case = base_case
                
            out_path = target_dir / f"{final_case.case_id}.json"
            out_path.write_text(json.dumps(final_case.model_dump(), indent=2), encoding="utf-8")
            
            # File metadata for manifest
            sha = sha256_file(out_path)
            rel_path = str(out_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            manifest_files[rel_path] = {
                "case_id": final_case.case_id,
                "family_id": final_case.family_id,
                "split": split_name,
                "topology_size": cfg["topo_size"],
                "perturbation": cfg["pert_name"],
                "expected_verdict": final_case.ground_truth.expected_verdict,
                "num_events": len(final_case.events),
                "sha256": sha
            }
            
            counts["cases"] += 1
            counts["by_topo"][cfg["topo_size"]] = counts["by_topo"].get(cfg["topo_size"], 0) + 1
            counts["by_pert"][cfg["pert_name"]] = counts["by_pert"].get(cfg["pert_name"], 0) + 1
            
        return counts

    dev_counts = process_split("dev", dev_families, dev_dir)
    test_counts = process_split("test", test_families, test_dir)
    
    # Also record handwritten cases in manifest
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
        except Exception:
            cid = hw_file.stem
            fid = "handwritten"
            ev = "DIAGNOSE"
            nev = 0
            pert_str = "none"
            
        manifest_files[rel_path] = {
            "case_id": cid,
            "family_id": fid,
            "split": "handwritten",
            "topology_size": 6,
            "perturbation": pert_str,
            "expected_verdict": ev,
            "num_events": nev,
            "sha256": sha
        }
        hw_cases_count += 1
        
    # Assemble Manifest
    manifest = {
        "manifest_version": "1.0",
        "generator_version": GENERATOR_VERSION,
        "base_seed": BASE_SEED,
        "total_families": total_families,
        "dev_families_count": len(dev_families),
        "test_families_count": len(test_families),
        "total_cases": len(manifest_files),
        "split_counts": {
            "dev": dev_counts["cases"],
            "test": test_counts["cases"],
            "handwritten": hw_cases_count
        },
        "dev_breakdown": dev_counts,
        "test_breakdown": test_counts,
        "files": manifest_files
    }
    
    manifest_path = PROJECT_ROOT / "evaluation" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest written to {manifest_path} with {len(manifest_files)} cases.")
    return manifest


if __name__ == "__main__":
    generate_all()
