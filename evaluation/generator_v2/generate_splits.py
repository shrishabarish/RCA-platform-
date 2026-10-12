"""
Generates dev2, val2, and test2 dataset splits using Generator 2.0.
Calculates SHA-256 manifest and locks test2.
"""
import sys
import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path("c:/Users/shris/Desktop/Cloud_Project")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.generator_v2.generator import GeneratorV2


def generate_all_splits():
    gen = GeneratorV2(seed=20261015)
    topos = gen.get_topologies()

    base_dir = PROJECT_ROOT / "evaluation/cases/generated_v2"
    dev2_dir = base_dir / "dev2"
    val2_dir = base_dir / "val2"
    test2_dir = base_dir / "test2"

    for d in (dev2_dir, val2_dir, test2_dir):
        d.mkdir(parents=True, exist_ok=True)

    manifest = {"generator_version": "2.0.0", "splits": {}}

    case_types_dev2 = [
        ("single_root_metric", 25),
        ("single_root_silent", 10),
        ("multi_root_independent", 10),
        ("source_down_abstain", 5),
        ("quiet_timeline_abstain", 5),
        ("root_healthy_blamed_abstain", 5),
    ]

    case_types_val2 = [
        ("single_root_metric", 15),
        ("single_root_silent", 5),
        ("multi_root_independent", 10),
        ("source_down_abstain", 5),
        ("quiet_timeline_abstain", 5),
        ("root_healthy_blamed_abstain", 5),
        ("idle_silent_control", 35),  # At least 30 idle-silent controls required
    ]

    case_types_test2 = [
        ("single_root_metric", 20),
        ("single_root_silent", 10),
        ("multi_root_independent", 10),
        ("source_down_abstain", 5),
        ("quiet_timeline_abstain", 5),
        ("root_healthy_blamed_abstain", 5),
        ("idle_silent_control", 10),
    ]

    for split_name, split_dir, type_counts, seed_base in [
        ("dev2", dev2_dir, case_types_dev2, 1000),
        ("val2", val2_dir, case_types_val2, 2000),
        ("test2", test2_dir, case_types_test2, 3000)
    ]:
        print(f"Generating split: {split_name}...")
        case_hashes = {}
        idx = 0
        for ctype, count in type_counts:
            for i in range(count):
                idx += 1
                case_id = f"case_v2_{split_name}_{idx:03d}_{ctype}"
                family_id = f"fam_v2_{split_name}_{(idx % 12):02d}"
                topo = topos[idx % len(topos)]
                seed = seed_base + idx

                case = gen.generate_case(
                    case_id=case_id,
                    family_id=family_id,
                    case_type=ctype,
                    topology=topo,
                    seed=seed
                )

                content = case.model_dump_json(indent=2)
                file_path = split_dir / f"{case_id}.json"
                file_path.write_text(content, encoding="utf-8")

                sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                case_hashes[case_id] = sha

        manifest["splits"][split_name] = {
            "total_cases": len(case_hashes),
            "hashes": case_hashes
        }
        print(f"  {split_name} generated: {len(case_hashes)} cases.")

    # Save manifest_v2.json
    manifest_path = PROJECT_ROOT / "evaluation/cases/manifest_v2.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest saved to: {manifest_path}")
    print("Locked test2: hashed, never opened, never executed.")


if __name__ == "__main__":
    generate_all_splits()
