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

GENERATOR_VERSION = "1.2.0"
VAL_SEED = 99999
TEST_SUPP_SEED = 88888

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

MULTI_TYPES = [
    "separated_time",
    "disjoint_topological",
]


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_v1_2_splits():
    dev_val_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev_validation"
    test_supp_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "test_supp"
    audit_log = PROJECT_ROOT / "evaluation" / "test_split_audit.log"
    manifest_path = PROJECT_ROOT / "evaluation" / "manifest_v1_2.json"

    # Clean only supplement directories
    for d in [dev_val_dir, test_supp_dir]:
        if d.exists():
            for f in d.glob("*.json"):
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Dict[str, Any]] = {}

    def generate_family_bundle(
        fam_idx: int,
        family_type: str,
        split_name: str,
        target_dir: Path,
        rng_seed: int
    ) -> List[Any]:
        fam_id = f"fam_{split_name}_{fam_idx:03d}"
        fam_rng = random.Random(rng_seed)

        # Topology sizing: 6, 12, or 20 nodes
        if fam_idx % 3 == 1:
            topo_size = 6
        elif fam_idx % 3 == 2:
            topo_size = 12
        else:
            topo_size = 20

        topo = get_topology_by_size(topo_size)
        base_case_id = f"case_{fam_id}_base"

        # Check if family is multi-root
        is_multi = (family_type == "multi_root")
        base_case = simulate_case(
            topology=topo,
            family_id=fam_id,
            case_id=base_case_id,
            seed=rng_seed,
            is_multi_root=is_multi
        )
        base_case.generator_version = GENERATOR_VERSION
        cases: List[Any] = [base_case]

        if family_type == "hard":
            # 2 hard variants -> root is NOT earliest
            h1 = HARD_TYPES[(fam_idx * 2) % len(HARD_TYPES)]
            h2 = HARD_TYPES[(fam_idx * 2 + 1) % len(HARD_TYPES)]
            c1 = create_hard_variant(base_case, h1, f"case_{fam_id}_hard_{h1}", rng_seed + 201)
            c2 = create_hard_variant(base_case, h2, f"case_{fam_id}_hard_{h2}", rng_seed + 202)
            c1.generator_version = GENERATOR_VERSION
            c2.generator_version = GENERATOR_VERSION
            cases.extend([c1, c2])

        elif family_type == "edge_root":
            # Edge-root: force root to be a leaf dependency with deep blast radius
            # Create hard variant + delay variant
            c1 = create_hard_variant(base_case, "missing_root_telemetry", f"case_{fam_id}_hard_missing_root", rng_seed + 301)
            c2 = apply_delay_and_reorder(base_case, random.Random(rng_seed + 302))
            c2.case_id = f"case_{fam_id}_delay_and_reorder"
            c1.generator_version = GENERATOR_VERSION
            c2.generator_version = GENERATOR_VERSION
            cases.extend([c1, c2])

        elif family_type == "abstain":
            # 2 abstain variants paired with diagnosable base counterpart
            a1 = ABSTAIN_TYPES[(fam_idx * 2) % len(ABSTAIN_TYPES)]
            a2 = ABSTAIN_TYPES[(fam_idx * 2 + 1) % len(ABSTAIN_TYPES)]
            c1 = create_abstain_variant(base_case, a1, f"case_{fam_id}_abstain_{a1}", rng_seed + 401)
            c2 = create_abstain_variant(base_case, a2, f"case_{fam_id}_abstain_{a2}", rng_seed + 402)
            c1.generator_version = GENERATOR_VERSION
            c2.generator_version = GENERATOR_VERSION
            cases.extend([c1, c2])

        elif family_type == "multi_root":
            m_type = MULTI_TYPES[fam_idx % len(MULTI_TYPES)]
            c1 = create_multi_incident_variant(base_case, m_type, f"case_{fam_id}_multi_{m_type}", rng_seed + 501)
            c1.generator_version = GENERATOR_VERSION
            cases.append(c1)

        elif family_type == "idle_healthy_control":
            # Diagnosable case with idle-but-healthy non-root services in topology
            # Add tags and metadata
            base_case.tags.append("idle_healthy_control")
            c1 = apply_drop_one_signal(base_case, random.Random(rng_seed + 601))
            c1.case_id = f"case_{fam_id}_drop_one_signal"
            c1.tags.append("idle_healthy_control")
            c1.generator_version = GENERATOR_VERSION
            cases.append(c1)

        # Write cases
        for c in cases:
            out_file = target_dir / f"{c.case_id}.json"
            out_file.write_text(json.dumps(c.model_dump(), indent=2), encoding="utf-8")
            sha = sha256_file(out_file)
            rel_path = str(out_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
            manifest[rel_path] = {
                "case_id": c.case_id,
                "family_id": c.family_id,
                "base_case_id": c.base_case_id,
                "split": split_name,
                "topology_size": topo_size,
                "perturbation": c.perturbations[0] if c.perturbations else "none",
                "expected_verdict": c.ground_truth.expected_verdict,
                "is_hard": c.is_hard,
                "is_multi_root": len(c.ground_truth.true_root_causes) > 1,
                "sha256": sha
            }

        return cases

    # 1. Generate DEV-VALIDATION split (40 families)
    # Family index range: 76 to 115
    print("Generating DEV-VALIDATION split...")
    val_families = []
    # 14 Hard families (76-89) -> 14 * 2 = 28 hard cases
    for idx in range(76, 90):
        val_families.append((idx, "hard"))
    # 6 Edge-root families (90-95) -> 6 hard cases + 6 delay
    for idx in range(90, 96):
        val_families.append((idx, "edge_root"))
    # 10 Multi-root families (96-105) -> 20 multi cases
    for idx in range(96, 106):
        val_families.append((idx, "multi_root"))
    # 6 Abstain families (106-111) -> 12 abstain cases + 6 diagnosable counterparts
    for idx in range(106, 112):
        val_families.append((idx, "abstain"))
    # 4 Idle-healthy controls (112-115) -> 8 diagnosable cases
    for idx in range(112, 116):
        val_families.append((idx, "idle_healthy_control"))

    for fam_idx, fam_type in val_families:
        seed = VAL_SEED + fam_idx * 43
        generate_family_bundle(fam_idx, fam_type, "dev_val", dev_val_dir, seed)

    # 2. Generate TEST SUPPLEMENT split (25 families, strictly locked and blinded!)
    print("Generating TEST SUPPLEMENT split (blinded)...")
    test_families = []
    for idx in range(116, 141):
        if idx < 125:
            test_families.append((idx, "hard"))
        elif idx < 130:
            test_families.append((idx, "edge_root"))
        elif idx < 135:
            test_families.append((idx, "multi_root"))
        elif idx < 139:
            test_families.append((idx, "abstain"))
        else:
            test_families.append((idx, "idle_healthy_control"))

    for fam_idx, fam_type in test_families:
        seed = TEST_SUPP_SEED + fam_idx * 57
        generate_family_bundle(fam_idx, fam_type, "test_supp", test_supp_dir, seed)

    # Save manifest_v1_2.json
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved manifest to {manifest_path} with {len(manifest)} cases.")

    # Write audit log for test supplement locking
    with open(audit_log, "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "GENERATED_AND_LOCKED_TEST_SUPPLEMENT",
            "generator_version": GENERATOR_VERSION,
            "seed": TEST_SUPP_SEED,
            "test_supp_families": len(test_families),
            "manifest_hash": sha256_file(manifest_path),
            "details": "Test supplement generated with seed 88888, hashed in manifest_v1_2.json, and strictly blinded."
        }
        f.write(json.dumps(log_entry) + "\n")
    print(f"[AUDIT LOGGED] Recorded test supplement locking in {audit_log}")


if __name__ == "__main__":
    generate_v1_2_splits()
