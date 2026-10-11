"""
Test Secret Scanning: Verifies that no tracked files contain live credentials.
"""

from scripts.secret_scanner import scan_tracked_files


def test_no_secrets_in_tracked_files():
    exit_code = scan_tracked_files()
    assert exit_code == 0, "Secret scanner identified unmasked credentials in tracked files"
