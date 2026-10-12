import sys
import json
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.generator_v3.generator import GeneratorV3


def generate_all_splits():
    gen = GeneratorV3(uninformative_severity=True)
    topos = gen.generate_topologies()

    out_base = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3"
    out_base.mkdir(parents=True, exist_ok=True)

    manifest_v3 = {
        "generator_version": "3.0.0",
        "description": "Generator 3.0 benchmark splits with uninformative severity, periodic baseline telemetry, and balanced multi-root/controls.",
        "splits": {}
    }

    # Split specifications
    # dev3: 100 cases (20 single metric, 10 single silent, 35 multi-root, 35 idle-silent controls)
    # val3: 100 cases (new seeds, new families, 20 single metric, 10 single silent, 35 multi-root, 35 idle-silent controls)
    # test3: 80 cases (strictly blinded, hashed and locked)
    split_configs = [
        ("dev3", 3001, "fam_v3_dev3", [
            ("single_root_metric", 20),
            ("single_root_silent", 10),
            ("multi_root_independent", 35),  # >= 30 multi-root
            ("idle_silent_control", 35),      # >= 30 idle-silent controls
        ]),
        ("val3", 5001, "fam_v3_val3", [
            ("single_root_metric", 20),
            ("single_root_silent", 10),
            ("multi_root_independent", 35),  # >= 30 multi-root
            ("idle_silent_control", 35),      # >= 30 idle-silent controls
        ]),
        ("test3", 7001, "fam_v3_test3", [
            ("single_root_metric", 15),
            ("single_root_silent", 10),
            ("multi_root_independent", 30),
            ("idle_silent_control", 25),
        ]),
    ]

    for split_name, base_seed, fam_prefix, case_plan in split_configs:
        split_dir = out_base / split_name
        split_dir.mkdir(parents=True, exist_ok=True)
        print(f"Generating split: {split_name}...")

        case_hashes = {}
        idx = 0
        current_seed = base_seed

        for case_type, count in case_plan:
            for c_i in range(count):
                idx += 1
                current_seed += 1
                case_id = f"case_v3_{split_name}_{idx:03d}_{case_type}"
                fam_id = f"{fam_prefix}_{(idx % 10) + 1:02d}"
                topo = topos[idx % len(topos)]

                case = gen.generate_case(
                    case_id=case_id,
                    family_id=fam_id,
                    case_type=case_type,
                    topology=topo,
                    seed=current_seed
                )

                content_bytes = json.dumps(case.model_dump(), indent=2).encode("utf-8")
                case_file = split_dir / f"{case_id}.json"
                case_file.write_bytes(content_bytes)

                sha256 = hashlib.sha256(content_bytes).hexdigest()
                case_hashes[case_id] = sha256

        manifest_v3["splits"][split_name] = {
            "count": idx,
            "case_hashes": case_hashes
        }
        print(f"  {split_name} generated: {idx} cases.")

    manifest_path = PROJECT_ROOT / "evaluation" / "cases" / "manifest_v3.json"
    manifest_path.write_text(json.dumps(manifest_v3, indent=2), encoding="utf-8")
    print(f"Manifest saved to: {manifest_path}")
    print("Locked test3: hashed, never opened, never executed.")


if __name__ == "__main__":
    generate_all_splits()
