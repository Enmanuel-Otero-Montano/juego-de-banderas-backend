"""Add max_score field to OverallScoreTable model

Revision ID: 71c48ccff8cf
Revises: 8b7b4bbf8505
Create Date: 2024-08-22 21:59:05.420957
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "71c48ccff8cf"
down_revision: Union[str, None] = "8b7b4bbf8505"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------- helpers de idempotencia ----------
def _safe_drop_index(index_names, table_name: str, schema: str = "public"):
    """Drop index(es) solo si existen en la tabla dada."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name in insp.get_table_names(schema=schema):
        existing = {ix["name"] for ix in insp.get_indexes(table_name, schema=schema)}
        for name in index_names:
            if name in existing:
                op.drop_index(name, table_name=table_name, schema=schema)


def _column_exists(table_name: str, column_name: str, schema: str = "public") -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names(schema=schema):
        return False
    cols = {c["name"] for c in insp.get_columns(table_name, schema=schema)}
    return column_name in cols


def _table_exists(table_name: str, schema: str = "public") -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table_name in insp.get_table_names(schema=schema)


def _ensure_table_overall_score_table(schema: str = "public"):
    """Crea overall_score_table si falta (id, username, full_name, profile_image) e índices."""
    if _table_exists("overall_score_table", schema=schema):
        return

    # Crear la tabla base
    op.create_table(
        "overall_score_table",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("profile_image", postgresql.BYTEA(), nullable=True),
        schema=schema,
    )

    # Crear índices si faltan
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set()
    try:
        existing = {ix["name"] for ix in insp.get_indexes("overall_score_table", schema=schema)}
    except Exception:
        pass

    if "ix_overall_score_table_username" not in existing and "overall_score_table_username_key" not in existing:
        op.create_index("ix_overall_score_table_username", "overall_score_table", ["username"], unique=True, schema=schema)
    if "ix_overall_score_table_full_name" not in existing:
        op.create_index("ix_overall_score_table_full_name", "overall_score_table", ["full_name"], unique=False, schema=schema)
# ---------------------------------------------


def upgrade() -> None:
    # -- Idempotencia sobre índices de users (por migraciones antiguas)
    _safe_drop_index({"ix_users_email", op.f("ix_users_email")}, "users")
    _safe_drop_index({"ix_users_full_name", op.f("ix_users_full_name")}, "users")
    _safe_drop_index({"ix_users_username", op.f("ix_users_username")}, "users")

    # -- Idempotencia sobre índices en overall_score_table (si ya existiera)
    _safe_drop_index(
        {"ix_overall_score_table_full_name", op.f("ix_overall_score_table_full_name")},
        "overall_score_table",
    )
    _safe_drop_index(
        {"ix_overall_score_table_username", op.f("ix_overall_score_table_username")},
        "overall_score_table",
    )

    # -- Asegurar que la tabla overall_score_table exista en DB fresh
    _ensure_table_overall_score_table(schema="public")

    # -- Agregar la columna max_score si no existe
    if not _column_exists("overall_score_table", "max_score"):
        op.add_column(
            "overall_score_table",
            sa.Column("max_score", sa.Integer(), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    # Downgrade autogenerado original (restaura esquema previo a esta revisión)
    op.create_table(
        "overall_score_table",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("username", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("full_name", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("profile_image", postgresql.BYTEA(), autoincrement=False, nullable=True),
        sa.PrimaryKeyConstraint("id", name="overall_score_table_pkey"),
    )
    op.create_index(
        "ix_overall_score_table_username", "overall_score_table", ["username"], unique=True
    )
    op.create_index(
        "ix_overall_score_table_full_name", "overall_score_table", ["full_name"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("email", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("username", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("full_name", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("hashed_password", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("is_active", sa.BOOLEAN(), autoincrement=False, nullable=True),
        sa.Column("is_verified", sa.BOOLEAN(), autoincrement=False, nullable=True),
        sa.Column("profile_image", postgresql.BYTEA(), autoincrement=False, nullable=True),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_full_name", "users", ["full_name"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
