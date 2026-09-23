"""persist server-timestamped ranked attempt events

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'career_attempt_events' not in set(inspector.get_table_names()):
        op.create_table(
            'career_attempt_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('attempt_id', sa.String(), sa.ForeignKey('career_attempts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('event_id', sa.String(), nullable=False),
            sa.Column('sequence', sa.Integer(), nullable=False),
            sa.Column('country_code', sa.String(), nullable=False),
            sa.Column('selected_code', sa.String(), nullable=False),
            sa.Column('accepted_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('attempt_id', 'event_id', name='uq_career_attempt_event_id'),
            sa.UniqueConstraint('attempt_id', 'sequence', name='uq_career_attempt_event_sequence'),
        )
        op.create_index('ix_career_attempt_events_attempt_id', 'career_attempt_events', ['attempt_id'])
        op.create_index('ix_career_attempt_event_attempt_sequence', 'career_attempt_events', ['attempt_id', 'sequence'])


def downgrade() -> None:
    op.drop_index('ix_career_attempt_event_attempt_sequence', table_name='career_attempt_events')
    op.drop_index('ix_career_attempt_events_attempt_id', table_name='career_attempt_events')
    op.drop_table('career_attempt_events')
