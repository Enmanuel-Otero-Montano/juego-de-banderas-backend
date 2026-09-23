"""retire the unused single-row career aggregate

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # El ranking y las pantallas de carrera agregan StageBest por temporada y
    # dificultad. Esta tabla de una fila por usuario no tenía lectores y podía
    # representar sólo la última dificultad recalculada.
    if "career_user_stats" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("career_user_stats")


def downgrade() -> None:
    if "career_user_stats" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "career_user_stats",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_hints_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_time_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_mistakes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_career_ranking",
        "career_user_stats",
        ["stages_completed", "total_score", "total_hints_used", "total_time_seconds", "last_activity_at"],
    )
