#!/usr/bin/env python3
"""
Lightweight Secret Scanner & Pre-Commit Hook
Scans tracked git files for AWS keys, secrets, credentials, and private keys.
Fails with code 1 if any unmasked secret is detected in tracked files.
"""

import re
import sys
import subprocess
from pathlib import Path

SECRET_PATTERNS = [
    ("AWS Access Key ID", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("AWS Secret Key Assignment", re.compile(r"(?i)(?:aws_secret_access_key|aws_secret_key)\s*[:=]\s*['\"]?([a-zA-Z0-9/+=]{40})['\"]?")),
    ("RSA / SSH Private Key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Database Connection Password", re.compile(r"(?i)postgres(?:ql)?://[a-zA-Z0-9_\-]+:([a-zA-Z0-9_\-!@#$%^&*]{6,})@")),
    ("GitHub Personal Access Token", re.compile(r"\b(ghp_[0-9a-zA-Z]{36})\b")),
]

# Allowlist for safe templates, test mocks, documentation, or gitignore
ALLOWED_FILES = {
    ".env.example",
    "scripts/secret_scanner.py",
    "tests/test_secret_scan.py",
    "tests/test_safety_guard.py",
}


def scan_tracked_files() -> int:
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=True
        )
        files = res.stdout.strip().splitlines()
    except Exception as e:
        print(f"Error running git ls-files: {e}")
        return 1

    findings = []
    for f in files:
        if (
            f in ALLOWED_FILES
            or f.startswith((".git", "package/", "package\\", "test_linux_pkg/", "test_linux_pkg\\"))
            or f.endswith((".png", ".jpg", ".zip", ".ico"))
        ):
            continue
        path = Path(f)
        if not path.exists() or not path.is_file():
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for label, pat in SECRET_PATTERNS:
            for match in pat.finditer(content):
                surrounding = content[match.start():match.end() + 30].lower()
                # Check if it's a known placeholder, mock, or local test connection
                if any(placeholder in surrounding for placeholder in ["example", "placeholder", "xxx", "your_", "mock", "os.getenv", ":password@", ":pass@", ":postgres@", "localhost", "127.0.0.1"]):
                    continue
                findings.append((f, label))

    if findings:
        print("=" * 80)
        print("SECRET SCAN FAILED: Potential unmasked credentials found in tracked files!")
        print("=" * 80)
        for fname, label in findings:
            print(f"- [FAILED] {fname} ({label})")
        print("\nPlease remove the credentials, use environment variables, or update .gitignore.")
        return 1

    print("[SUCCESS] Secret scan passed: 0 credentials found in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(scan_tracked_files())
