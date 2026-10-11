"""Additive migration for missing columns and indexes

Revision ID: 0002_add_columns_and_indexes
Revises: 0001_baseline_prod_schema
Create Date: 2026-10-11 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_add_columns_and_indexes'
down_revision: Union[str, None] = '0001_baseline_prod_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Additive columns for incidents
    inc_cols = {c['name'] for c in insp.get_columns('incidents')}
    if 'archive_status' not in inc_cols:
        op.add_column('incidents', sa.Column('archive_status', sa.String(length=32), server_default='PENDING', nullable=True))
    if 'notification_status' not in inc_cols:
        op.add_column('incidents', sa.Column('notification_status', sa.String(length=32), server_default='PENDING', nullable=True))
    if 'delivery_error' not in inc_cols:
        op.add_column('incidents', sa.Column('delivery_error', sa.Text(), nullable=True))
    if 'analysis_status' not in inc_cols:
        op.add_column('incidents', sa.Column('analysis_status', sa.String(length=32), server_default='PENDING', nullable=True))
    if 'analyzed_at' not in inc_cols:
        op.add_column('incidents', sa.Column('analyzed_at', sa.DateTime(), nullable=True))

    # 2. Additive columns for cloud_events
    ev_cols = {c['name'] for c in insp.get_columns('cloud_events')}
    if 'ingested_at' not in ev_cols:
        op.add_column('cloud_events', sa.Column('ingested_at', sa.DateTime(), server_default=sa.func.now(), nullable=True))
        op.execute("UPDATE cloud_events SET ingested_at = timestamp WHERE ingested_at IS NULL")

    # 3. Additive columns for outbox_events
    outbox_cols = {c['name'] for c in insp.get_columns('outbox_events')}
    if 'event_type' not in outbox_cols:
        op.add_column('outbox_events', sa.Column('event_type', sa.String(length=32), server_default='SNS_NOTIFICATION', nullable=True))
    if 'lease_until' not in outbox_cols:
        op.add_column('outbox_events', sa.Column('lease_until', sa.DateTime(), nullable=True))
    if 'next_retry_at' not in outbox_cols:
        op.add_column('outbox_events', sa.Column('next_retry_at', sa.DateTime(), nullable=True))
    if 'last_error' not in outbox_cols:
        op.add_column('outbox_events', sa.Column('last_error', sa.Text(), nullable=True))
    if 'updated_at' not in outbox_cols:
        op.add_column('outbox_events', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # 4. System settings table for deploy configuration and safety cutoffs
    tables = insp.get_table_names()
    if 'system_settings' not in tables:
        op.create_table(
            'system_settings',
            sa.Column('key', sa.String(), primary_key=True),
            sa.Column('value', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True)
        )

    # 5. Additive concurrent indexes (non-blocking for production load)
    inc_indexes = {i['name'] for i in insp.get_indexes('incidents')}
    ev_indexes = {i['name'] for i in insp.get_indexes('cloud_events')}
    outbox_indexes = {i['name'] for i in insp.get_indexes('outbox_events')}
    sys_indexes = {i['name'] for i in insp.get_indexes('system_settings')} if 'system_settings' in tables else set()

    with op.get_context().autocommit_block():
        if 'ix_incidents_status_created_at' not in inc_indexes:
            op.create_index('ix_incidents_status_created_at', 'incidents', ['status', 'created_at'], unique=False, postgresql_concurrently=True)
        if 'ix_incidents_open_created_at' not in inc_indexes:
            op.create_index('ix_incidents_open_created_at', 'incidents', ['created_at'], unique=False, postgresql_concurrently=True, postgresql_where=sa.text("status = 'OPEN'"))
        if 'ix_incidents_archive_status' not in inc_indexes:
            op.create_index('ix_incidents_archive_status', 'incidents', ['archive_status'], unique=False, postgresql_concurrently=True)
        if 'ix_incidents_notification_status' not in inc_indexes:
            op.create_index('ix_incidents_notification_status', 'incidents', ['notification_status'], unique=False, postgresql_concurrently=True)
        if 'ix_incidents_analysis_status' not in inc_indexes:
            op.create_index('ix_incidents_analysis_status', 'incidents', ['analysis_status'], unique=False, postgresql_concurrently=True)

        if 'ix_cloud_events_service_timestamp' not in ev_indexes:
            op.create_index('ix_cloud_events_service_timestamp', 'cloud_events', ['service', 'timestamp'], unique=False, postgresql_concurrently=True)
        if 'ix_cloud_events_unassigned' not in ev_indexes:
            op.create_index('ix_cloud_events_unassigned', 'cloud_events', ['timestamp'], unique=False, postgresql_concurrently=True, postgresql_where=sa.text("incident_id IS NULL"))
        if 'ix_cloud_events_ingested_at' not in ev_indexes:
            op.create_index('ix_cloud_events_ingested_at', 'cloud_events', ['ingested_at'], unique=False, postgresql_concurrently=True)

        if 'ix_outbox_events_incident_id' not in outbox_indexes:
            op.create_index('ix_outbox_events_incident_id', 'outbox_events', ['incident_id'], unique=False, postgresql_concurrently=True)
        if 'ix_outbox_events_lease_until' not in outbox_indexes:
            op.create_index('ix_outbox_events_lease_until', 'outbox_events', ['lease_until'], unique=False, postgresql_concurrently=True)
        if 'ix_outbox_events_next_retry_at' not in outbox_indexes:
            op.create_index('ix_outbox_events_next_retry_at', 'outbox_events', ['next_retry_at'], unique=False, postgresql_concurrently=True)

        if 'ix_system_settings_key' not in sys_indexes:
            try:
                op.create_index('ix_system_settings_key', 'system_settings', ['key'], unique=False, postgresql_concurrently=True)
            except Exception:
                pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    with op.get_context().autocommit_block():
        indexes_to_drop = [
            'ix_system_settings_key',
            'ix_outbox_events_next_retry_at',
            'ix_outbox_events_lease_until',
            'ix_outbox_events_incident_id',
            'ix_cloud_events_ingested_at',
            'ix_cloud_events_unassigned',
            'ix_cloud_events_service_timestamp',
            'ix_incidents_analysis_status',
            'ix_incidents_notification_status',
            'ix_incidents_archive_status',
            'ix_incidents_open_created_at',
            'ix_incidents_status_created_at',
        ]
        for idx in indexes_to_drop:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")

    if 'system_settings' in tables:
        op.drop_table('system_settings')

    if 'outbox_events' in tables:
        cols = {c['name'] for c in insp.get_columns('outbox_events')}
        for col in ['updated_at', 'last_error', 'next_retry_at', 'lease_until', 'event_type']:
            if col in cols:
                op.drop_column('outbox_events', col)

    if 'cloud_events' in tables:
        cols = {c['name'] for c in insp.get_columns('cloud_events')}
        if 'ingested_at' in cols:
            op.drop_column('cloud_events', 'ingested_at')

    if 'incidents' in tables:
        cols = {c['name'] for c in insp.get_columns('incidents')}
        for col in ['analyzed_at', 'analysis_status', 'delivery_error', 'notification_status', 'archive_status']:
            if col in cols:
                op.drop_column('incidents', col)
