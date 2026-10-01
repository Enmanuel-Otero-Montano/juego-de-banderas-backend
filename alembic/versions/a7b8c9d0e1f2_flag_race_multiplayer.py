"""add private flag race multiplayer tables

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "flag_race_rooms",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("join_code_hash", sa.String(length=64), nullable=False),
        sa.Column("join_code_encrypted", sa.String(), nullable=False),
        sa.Column("join_token_hash", sa.String(length=64), nullable=False),
        sa.Column("join_token_encrypted", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_host_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("is_persistent", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_flag_race_rooms_join_code_hash", "flag_race_rooms", ["join_code_hash"], unique=True)
    op.create_index("ix_flag_race_rooms_join_token_hash", "flag_race_rooms", ["join_token_hash"], unique=True)
    op.create_index("ix_flag_race_rooms_status", "flag_race_rooms", ["status"])
    op.create_index("ix_flag_race_rooms_expires_at", "flag_race_rooms", ["expires_at"])

    op.create_table(
        "flag_race_room_members",
        sa.Column("room_id", sa.String(length=36), sa.ForeignKey("flag_race_rooms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_ready", sa.Boolean(), nullable=False),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("intermission_state", sa.String(length=24), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("room_id", "seat", name="uq_flag_race_room_seat"),
    )
    op.create_index("ix_flag_race_member_user_active", "flag_race_room_members", ["user_id", "left_at"])

    op.create_table(
        "flag_race_rounds",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("room_id", sa.String(length=36), sa.ForeignKey("flag_race_rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ruleset_version", sa.Integer(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("plan", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("winner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("finish_reason", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("room_id", "round_number", name="uq_flag_race_room_round_number"),
    )
    op.create_index("ix_flag_race_rounds_room_id", "flag_race_rounds", ["room_id"])
    op.create_index("ix_flag_race_rounds_status", "flag_race_rounds", ["status"])

    op.create_table(
        "flag_race_participants",
        sa.Column("round_id", sa.String(length=36), sa.ForeignKey("flag_race_rounds.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("mistakes", sa.Integer(), nullable=False),
        sa.Column("expected_sequence", sa.Integer(), nullable=False),
        sa.Column("discarded_codes", json_type, nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("progress_reached_at", sa.DateTime(), nullable=False),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("game_state", sa.String(length=16), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("round_id", "seat", name="uq_flag_race_participant_seat"),
    )

    op.create_table(
        "flag_race_answer_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("round_id", sa.String(length=36), sa.ForeignKey("flag_race_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("selected_code", sa.String(length=2), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("round_id", "user_id", "sequence", name="uq_flag_race_answer_sequence"),
    )
    op.create_index("ix_flag_race_answer_events_round_id", "flag_race_answer_events", ["round_id"])
    op.create_index("ix_flag_race_answer_round_user", "flag_race_answer_events", ["round_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_flag_race_answer_round_user", table_name="flag_race_answer_events")
    op.drop_index("ix_flag_race_answer_events_round_id", table_name="flag_race_answer_events")
    op.drop_table("flag_race_answer_events")
    op.drop_table("flag_race_participants")
    op.drop_index("ix_flag_race_rounds_status", table_name="flag_race_rounds")
    op.drop_index("ix_flag_race_rounds_room_id", table_name="flag_race_rounds")
    op.drop_table("flag_race_rounds")
    op.drop_index("ix_flag_race_member_user_active", table_name="flag_race_room_members")
    op.drop_table("flag_race_room_members")
    op.drop_index("ix_flag_race_rooms_expires_at", table_name="flag_race_rooms")
    op.drop_index("ix_flag_race_rooms_status", table_name="flag_race_rooms")
    op.drop_index("ix_flag_race_rooms_join_token_hash", table_name="flag_race_rooms")
    op.drop_index("ix_flag_race_rooms_join_code_hash", table_name="flag_race_rooms")
    op.drop_table("flag_race_rooms")
