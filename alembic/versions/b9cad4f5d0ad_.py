"""empty message

Revision ID: b9cad4f5d0ad
Revises: b235f38265c7
Create Date: 2024-08-23 16:53:16.570548
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b9cad4f5d0ad"
down_revision: Union[str, None] = "b235f38265c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------- helpers ----------
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

def _safe_drop_index(index_names, table_name: str, schema: str = "public"):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name in insp.get_table_names(schema=schema):
        existing = {ix["name"] for ix in insp.get_indexes(table_name, schema=schema)}
        for name in index_names:
            if name in existing:
                op.drop_index(name, table_name=table_name, schema=schema)
# -----------------------------

FK_NAME = op.f("fk_overall_score_table_user_id_users")

def upgrade() -> None:
    # Añadir user_id solo si no existe
    if not _column_exists("overall_score_table", "user_id"):
        op.add_column(
            "overall_score_table",
            sa.Column("user_id", sa.Integer(), nullable=True),
            schema="public",
        )

    # Dropear índices solo si existen
    _safe_drop_index(
        {"ix_overall_score_table_full_name", op.f("ix_overall_score_table_full_name")},
        "overall_score_table",
    )
    _safe_drop_index(
        {"ix_overall_score_table_username", op.f("ix_overall_score_table_username")},
        "overall_score_table",
    )

    # Crear la FK solo si existen ambas tablas y la columna local
    if (
        _table_exists("overall_score_table")
        and _table_exists("users")
        and _column_exists("overall_score_table", "user_id")
    ):
        op.create_foreign_key(
            FK_NAME,
            "overall_score_table",
            "users",
            ["user_id"],
            ["id"],
            source_schema="public",
            referent_schema="public",
        )

    # Dropear columnas antiguas solo si existen
    if _column_exists("overall_score_table", "username"):
        op.drop_column("overall_score_table", "username", schema="public")
    if _column_exists("overall_score_table", "full_name"):
        op.drop_column("overall_score_table", "full_name", schema="public")
    if _column_exists("overall_score_table", "profile_image"):
        op.drop_column("overall_score_table", "profile_image", schema="public")


def downgrade() -> None:
    # Restaurar columnas antiguas
    op.add_column(
        "overall_score_table",
        sa.Column("profile_image", postgresql.BYTEA(), nullable=True),
        schema="public",
    )
    op.add_column(
        "overall_score_table",
        sa.Column("full_name", sa.VARCHAR(), nullable=True),
        schema="public",
    )
    op.add_column(
        "overall_score_table",
        sa.Column("username", sa.VARCHAR(), nullable=True),
        schema="public",
    )

    # Dropear FK con nombre estable
    op.drop_constraint(FK_NAME, "overall_score_table", type_="foreignkey", schema="public")

    # Recrear índices
    op.create_index(
        "ix_overall_score_table_username",
        "overall_score_table",
        ["username"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_overall_score_table_full_name",
        "overall_score_table",
        ["full_name"],
        unique=False,
        schema="public",
    )

    # Quitar user_id
    if _column_exists("overall_score_table", "user_id"):
        op.drop_column("overall_score_table", "user_id", schema="public")
