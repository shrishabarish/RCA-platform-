"""Baseline production schema

Revision ID: 0001_baseline_prod_schema
Revises: 
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_baseline_prod_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline mirrors existing tables in production RDS
    op.create_table(
        'incidents',
        sa.Column('incident_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('probable_root_cause', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('affected_services', sa.JSON(), nullable=True),
        sa.Column('score_breakdown', sa.JSON(), nullable=True),
        sa.Column('remediation_suggestion', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('incident_id')
    )
    op.create_index('ix_incidents_incident_id', 'incidents', ['incident_id'], unique=False)
    op.create_index('ix_incidents_severity', 'incidents', ['severity'], unique=False)
    op.create_index('ix_incidents_status', 'incidents', ['status'], unique=False)

    op.create_table(
        'cloud_events',
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('event_type', sa.String(), nullable=True),
        sa.Column('service', sa.String(), nullable=True),
        sa.Column('resource_id', sa.String(), nullable=True),
        sa.Column('metric', sa.String(), nullable=True),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('incident_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.incident_id']),
        sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index('ix_cloud_events_event_id', 'cloud_events', ['event_id'], unique=False)
    op.create_index('ix_cloud_events_service', 'cloud_events', ['service'], unique=False)
    op.create_index('ix_cloud_events_timestamp', 'cloud_events', ['timestamp'], unique=False)

    op.create_table(
        'outbox_events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('incident_id', sa.String(), nullable=True),
        sa.Column('topic_arn', sa.String(), nullable=True),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=True),
        sa.Column('max_attempts', sa.Integer(), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.incident_id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outbox_events_id', 'outbox_events', ['id'], unique=False)
    op.create_index('ix_outbox_events_incident_id', 'outbox_events', ['incident_id'], unique=False)
    op.create_index('ix_outbox_events_status', 'outbox_events', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('outbox_events')
    op.drop_table('cloud_events')
    op.drop_table('incidents')
