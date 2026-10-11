"""Add partial index on unassigned ingested_at and server_default

Revision ID: 0003_add_ingested_at_index
Revises: 0002_add_columns_and_indexes
Create Date: 2026-10-11 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_add_ingested_at_index'
down_revision: Union[str, None] = '0002_add_columns_and_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Ensure server_default now() on ingested_at via separate ALTER COLUMN
    ev_cols = {c['name']: c for c in insp.get_columns('cloud_events')}
    if 'ingested_at' in ev_cols:
        op.alter_column('cloud_events', 'ingested_at', server_default=sa.func.now())

    # 2. Add concurrent partial index on (ingested_at) WHERE incident_id IS NULL
    ev_indexes = {i['name'] for i in insp.get_indexes('cloud_events')}
    with op.get_context().autocommit_block():
        if 'ix_cloud_events_ingested_at_unassigned' not in ev_indexes:
            op.create_index(
                'ix_cloud_events_ingested_at_unassigned',
                'cloud_events',
                ['ingested_at'],
                unique=False,
                postgresql_concurrently=True,
                postgresql_where=sa.text("incident_id IS NULL")
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    ev_indexes = {i['name'] for i in insp.get_indexes('cloud_events')}

    with op.get_context().autocommit_block():
        if 'ix_cloud_events_ingested_at_unassigned' in ev_indexes:
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_cloud_events_ingested_at_unassigned")
