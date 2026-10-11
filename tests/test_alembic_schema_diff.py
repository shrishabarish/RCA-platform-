import os
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata

from backend.app.db.database import Base
import backend.app.db.models  # ensure models registered

POSTGRES_TEST_URL = os.getenv(
    "POSTGRES_TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/cloudlens_test"
)


def test_alembic_upgrade_head_zero_diff_against_models():
    """
    Step 2b Proof:
    Stamps the baseline production schema on a baseline-shaped database/schema,
    runs alembic upgrade head, and verifies that the resulting database produces
    EXACTLY ZERO diff against the SQLAlchemy models.
    """
    engine = sa.create_engine(POSTGRES_TEST_URL)
    schema_name = "test_alembic_zero_diff"

    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        conn.execute(sa.text(f"CREATE SCHEMA {schema_name};"))

    try:
        # Connect to engine with search_path set to the isolated test schema
        with engine.connect() as conn:
            conn.execute(sa.text(f"SET search_path TO {schema_name};"))
            conn.commit()

            cfg = Config("alembic.ini")
            cfg.attributes["connection"] = conn

            # 1. Run baseline migration 0001
            command.upgrade(cfg, "0001_baseline_prod_schema")

            # 2. Upgrade to head (0002_add_columns_and_indexes)
            command.upgrade(cfg, "head")

            # 3. Compare resulting database schema against SQLAlchemy Base.metadata
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)

            critical_diffs = []
            for d in diff:
                critical_diffs.append(d)

            assert len(critical_diffs) == 0, f"Expected 0 schema diffs, got {len(critical_diffs)}: {critical_diffs}"

    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))
        engine.dispose()
