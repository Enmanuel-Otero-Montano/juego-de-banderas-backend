"""store career difficulty and mistake tie breakers

Revision ID: a1c2d3e4f5a6
Revises: 77842a3b385a
Create Date: 2026-09-12 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1c2d3e4f5a6'
down_revision = '77842a3b385a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    # Estas tablas tampoco tenían una migración histórica. Se describen de
    # forma explícita para que esta revisión no cambie junto con los modelos.
    if 'daily_attempts' not in existing_tables:
        op.create_table(
            'daily_attempts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('challenge_id', sa.Integer(), sa.ForeignKey('daily_challenges.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('anonymous_id', sa.String(), nullable=True),
            sa.Column('attempts_used', sa.Integer(), nullable=True),
            sa.Column('solved', sa.Boolean(), nullable=True),
            sa.Column('failed', sa.Boolean(), nullable=True),
            sa.Column('solved_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_daily_attempts_anonymous_id', 'daily_attempts', ['anonymous_id'])
    if 'daily_guesses' not in existing_tables:
        op.create_table(
            'daily_guesses',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('attempt_id', sa.Integer(), sa.ForeignKey('daily_attempts.id'), nullable=False),
            sa.Column('guess_text', sa.String(), nullable=False),
            sa.Column('is_correct', sa.Boolean(), nullable=False),
            sa.Column('attempt_number', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )

    def add_column_if_missing(table: str, column: sa.Column) -> None:
        columns = {item['name'] for item in sa.inspect(bind).get_columns(table)}
        if column.name not in columns:
            op.add_column(table, column)

    def create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
        indexes = {item['name'] for item in sa.inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns)

    add_column_if_missing('users', sa.Column('ranking_alias', sa.String(), nullable=True))
    add_column_if_missing('users', sa.Column('ranking_region', sa.String(), nullable=True))
    create_index_if_missing('ix_users_ranking_region', 'users', ['ranking_region'])

    user_unique_columns = {
        tuple(item['column_names'])
        for item in sa.inspect(bind).get_unique_constraints('users')
    }
    if ('email',) not in user_unique_columns:
        op.create_unique_constraint('uq_users_email', 'users', ['email'])
    if ('username',) not in user_unique_columns:
        op.create_unique_constraint('uq_users_username', 'users', ['username'])

    daily_indexes = {item['name']: item for item in sa.inspect(bind).get_indexes('daily_challenges')}
    daily_unique_columns = {
        tuple(item['column_names'])
        for item in sa.inspect(bind).get_unique_constraints('daily_challenges')
    }
    date_index = daily_indexes.get('ix_daily_challenges_date')
    if ('date',) in daily_unique_columns and date_index and not date_index.get('unique'):
        op.drop_index('ix_daily_challenges_date', table_name='daily_challenges')
    add_column_if_missing('career_user_stats', sa.Column('total_mistakes', sa.Integer(), nullable=False, server_default='0'))
    add_column_if_missing('stage_runs', sa.Column('mistakes', sa.Integer(), nullable=False, server_default='0'))
    add_column_if_missing('stage_runs', sa.Column('difficulty', sa.String(), nullable=False, server_default='normal'))
    add_column_if_missing('stage_best', sa.Column('mistakes', sa.Integer(), nullable=False, server_default='0'))
    add_column_if_missing('stage_best', sa.Column('difficulty', sa.String(), nullable=False, server_default='normal'))

    constraints = {item['name'] for item in sa.inspect(bind).get_unique_constraints('stage_best')}
    if 'uix_user_stage_best' in constraints:
        op.drop_constraint('uix_user_stage_best', 'stage_best', type_='unique')
    if 'uix_user_stage_best_difficulty' not in constraints:
        op.create_unique_constraint('uix_user_stage_best_difficulty', 'stage_best', ['user_id', 'stage_id', 'difficulty'])
    create_index_if_missing('ix_stage_best_difficulty', 'stage_best', ['difficulty'])
    create_index_if_missing('ix_stage_runs_difficulty', 'stage_runs', ['difficulty'])

    indexes = {item['name'] for item in sa.inspect(bind).get_indexes('career_user_stats')}
    if 'ix_career_ranking' in indexes:
        op.drop_index('ix_career_ranking', table_name='career_user_stats')
    op.create_index(
        'ix_career_ranking',
        'career_user_stats',
        [
            sa.text('stages_completed DESC'),
            sa.text('total_score DESC'),
            sa.text('total_hints_used ASC'),
            sa.text('total_time_seconds ASC'),
            sa.text('last_activity_at ASC'),
        ],
    )


def downgrade() -> None:
    op.drop_index('ix_career_ranking', table_name='career_user_stats')
    op.create_index('ix_career_ranking', 'career_user_stats', ['stages_completed', 'total_score', 'total_hints_used', 'total_time_seconds', 'last_activity_at'])
    op.drop_index('ix_stage_runs_difficulty', table_name='stage_runs')
    op.drop_index('ix_stage_best_difficulty', table_name='stage_best')
    op.drop_constraint('uix_user_stage_best_difficulty', 'stage_best', type_='unique')
    op.create_unique_constraint('uix_user_stage_best', 'stage_best', ['user_id', 'stage_id'])
    op.drop_column('stage_best', 'difficulty')
    op.drop_column('stage_best', 'mistakes')
    op.drop_column('stage_runs', 'difficulty')
    op.drop_column('stage_runs', 'mistakes')
    op.drop_column('career_user_stats', 'total_mistakes')
    op.drop_index('ix_users_ranking_region', table_name='users')
    op.drop_constraint('uq_users_username', 'users', type_='unique')
    op.drop_constraint('uq_users_email', 'users', type_='unique')
    op.drop_column('users', 'ranking_region')
    op.drop_column('users', 'ranking_alias')
