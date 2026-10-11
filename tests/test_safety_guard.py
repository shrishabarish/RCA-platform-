import os
import pytest
import subprocess
import sys
from pathlib import Path

from backend.app.core.safety import (
    check_database_safety,
    mask_database_url,
    print_database_host,
    ProductionDatabaseAccessError,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RDS_HOST_URL = "postgresql://myuser:mock_password@rca-db1.cjuiwiwa8mr6.eu-north-1.rds.amazonaws.com:5432/postgres"
LOCAL_URL = "postgresql://postgres:postgres@localhost:5432/cloudlens_test"


def test_mask_database_url():
    masked = mask_database_url(RDS_HOST_URL)
    assert "mock_password" not in masked
    assert "cjuiwiwa8mr6" not in masked
    assert "rca-db1" in masked
    assert "rds.amazonaws.com" in masked
    assert "***" in masked

    masked_local = mask_database_url(LOCAL_URL)
    assert "postgres:postgres" not in masked_local
    assert "localhost:5432/cloudlens_test" in masked_local


def test_safety_check_blocks_rds_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_PRODUCTION", raising=False)
    with pytest.raises(ProductionDatabaseAccessError) as exc_info:
        check_database_safety(RDS_HOST_URL, entry_point="unit_test")
    assert "FATAL SAFETY GUARD" in str(exc_info.value)
    assert "unit_test" in str(exc_info.value)
    assert "mypassword" not in str(exc_info.value)


def test_safety_check_blocks_rds_when_allow_production_is_not_1(monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCTION", "0")
    with pytest.raises(ProductionDatabaseAccessError):
        check_database_safety(RDS_HOST_URL, entry_point="unit_test")


def test_safety_check_allows_rds_when_allow_production_is_1(monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCTION", "1")
    # Should not raise
    check_database_safety(RDS_HOST_URL, entry_point="unit_test")


def test_safety_check_allows_local_db(monkeypatch):
    monkeypatch.delenv("ALLOW_PRODUCTION", raising=False)
    # Should not raise
    check_database_safety(LOCAL_URL, entry_point="unit_test")


def test_app_database_module_refuses_rds_in_subprocess():
    """Verify that importing backend.app.db.database refuses RDS URL when ALLOW_PRODUCTION!=1."""
    cmd = [
        sys.executable,
        "-c",
        "import os; os.environ['DATABASE_URL'] = 'postgresql://user:pass@test.rds.amazonaws.com/db'; "
        "os.environ.pop('ALLOW_PRODUCTION', None); "
        "import backend.app.db.database",
    ]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    assert res.returncode != 0
    assert "ProductionDatabaseAccessError" in res.stderr
    assert "FATAL SAFETY GUARD" in res.stderr


def test_scripts_refuse_rds_in_subprocess():
    """Verify that scripts refuse RDS URL when ALLOW_PRODUCTION!=1."""
    scripts_to_test = [
        ("scripts/backfill_legacy_events.py", ["--db-url", RDS_HOST_URL]),
        ("scripts/migrate_step_a_schema.py", ["--db-url", RDS_HOST_URL]),
        ("scripts/migrate_step_b_resolve_incidents.py", ["--db-url", RDS_HOST_URL]),
    ]
    for script, args in scripts_to_test:
        env = os.environ.copy()
        env.pop("ALLOW_PRODUCTION", None)
        res = subprocess.run(
            [sys.executable, script] + args,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode != 0, f"Script {script} unexpectedly succeeded!"
        assert "ProductionDatabaseAccessError" in res.stderr, f"Script {script} did not trigger safety error: {res.stderr}"


def test_alembic_refuses_rds_in_subprocess():
    """Verify that Alembic CLI refuses RDS URL when ALLOW_PRODUCTION!=1."""
    env = os.environ.copy()
    env["DATABASE_URL"] = RDS_HOST_URL
    env.pop("ALLOW_PRODUCTION", None)
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode != 0
    assert "ProductionDatabaseAccessError" in res.stderr
    assert "FATAL SAFETY GUARD" in res.stderr

