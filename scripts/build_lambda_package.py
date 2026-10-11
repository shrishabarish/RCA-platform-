#!/usr/bin/env python3
"""
AWS Lambda Build & Packaging Script
Packages the CloudLens event normalization lambda and its runtime dependencies
into a deployable zip artifact. Keeps build directories out of git.
"""

import os
import sys
import shutil
import zipfile
import tempfile
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = PROJECT_ROOT / "aws_lambda"
OUTPUT_ZIP = PROJECT_ROOT / "dist" / "lambda_deployment.zip"


def build_lambda_package():
    print("=" * 80)
    print("BUILDING AWS LAMBDA DEPLOYMENT PACKAGE")
    print("=" * 80)

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_build:
        tmp_path = Path(tmp_build)
        print(f"Working in isolated temporary build directory: {tmp_path}")

        req_file = LAMBDA_DIR / "requirements.txt"
        if req_file.exists():
            print(f"Installing dependencies from {req_file}...")
            cmd = [
                sys.executable, "-m", "pip", "install",
                "-r", str(req_file),
                "-t", str(tmp_path),
                "--no-cache-dir",
                "--quiet"
            ]
            subprocess.check_call(cmd)

        # Copy lambda code
        print("Copying Lambda handler and event processing modules...")
        for py_file in LAMBDA_DIR.glob("*.py"):
            shutil.copy2(py_file, tmp_path / py_file.name)

        # Create zip artifact
        if OUTPUT_ZIP.exists():
            OUTPUT_ZIP.unlink()

        print(f"Compressing into {OUTPUT_ZIP}...")
        with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in tmp_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(tmp_path)
                    zf.write(file_path, arcname)

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"[SUCCESS] Lambda package built: {OUTPUT_ZIP} ({size_mb:.2f} MB)")
    return OUTPUT_ZIP


if __name__ == "__main__":
    build_lambda_package()
