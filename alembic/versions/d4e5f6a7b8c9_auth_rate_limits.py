"""add shared authentication rate limits

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('auth_rate_limits', sa.Column('key', sa.String(length=64), primary_key=True), sa.Column('window_started_at', sa.DateTime(), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False))

def downgrade():
    op.drop_table('auth_rate_limits')
