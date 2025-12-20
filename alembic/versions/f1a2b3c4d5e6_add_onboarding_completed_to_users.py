"""Add onboarding_completed to users

Revision ID: f1a2b3c4d5e6
Revises: c870c0bf89be
Create Date: 2025-12-20 11:22:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "c870c0bf89be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str, schema: str = "public") -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table_name in insp.get_table_names(schema=schema)


def _column_exists(table_name: str, column_name: str, schema: str = "public") -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names(schema=schema):
        return False
    cols = {c["name"] for c in insp.get_columns(table_name, schema=schema)}
    return column_name in cols


def upgrade() -> None:
    # Add 'onboarding_completed' only if the table exists and the column doesn't
    if _table_exists("users") and not _column_exists("users", "onboarding_completed"):
        op.add_column(
            "users",
            sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
            schema="public",
        )


def downgrade() -> None:
    # Remove 'onboarding_completed' only if it exists
    if _column_exists("users", "onboarding_completed"):
        op.drop_column("users", "onboarding_completed", schema="public")
