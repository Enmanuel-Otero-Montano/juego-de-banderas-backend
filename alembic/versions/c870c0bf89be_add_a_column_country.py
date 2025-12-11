"""Add a column country

Revision ID: c870c0bf89be
Revises: 5c89a85b3248
Create Date: 2024-09-10 21:40:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c870c0bf89be"
down_revision: Union[str, None] = "5c89a85b3248"
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
    # Agregar 'country' sólo si existe la tabla y falta la columna
    if _table_exists("users") and not _column_exists("users", "country"):
        op.add_column(
            "users",
            sa.Column("country", sa.String(), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    # Quitar 'country' sólo si existe
    if _column_exists("users", "country"):
        op.drop_column("users", "country", schema="public")
