"""ranked attempts, immutable seasons and auditable runs

Revision ID: b2c3d4e5f6a7
Revises: a1c2d3e4f5a6
Create Date: 2026-09-17 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'career_season_profiles' not in tables:
        op.create_table(
            'career_season_profiles',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('season_id', sa.String(), primary_key=True),
            sa.Column('country', sa.String(), nullable=False),
            sa.Column('region', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )

    if 'career_attempts' not in tables:
        op.create_table(
            'career_attempts',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('season_id', sa.String(), nullable=False),
            sa.Column('ruleset_version', sa.Integer(), nullable=False),
            sa.Column('content_version', sa.Integer(), nullable=False),
            sa.Column('stage_id', sa.String(), nullable=False),
            sa.Column('route_position', sa.Integer(), nullable=False),
            sa.Column('difficulty', sa.String(), nullable=False),
            sa.Column('country_codes', sa.JSON(), nullable=False),
            sa.Column('app_version', sa.String(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('raw_submission', sa.JSON(), nullable=True),
        )
        op.create_index('ix_career_attempts_user_id', 'career_attempts', ['user_id'])
        op.create_index('ix_career_attempts_season_id', 'career_attempts', ['season_id'])
        op.create_index('ix_career_attempts_expires_at', 'career_attempts', ['expires_at'])
        op.create_index('ix_career_attempt_user_started', 'career_attempts', ['user_id', 'started_at'])

    def add_column(table: str, column: sa.Column) -> None:
        columns = {item['name'] for item in sa.inspect(bind).get_columns(table)}
        if column.name not in columns:
            op.add_column(table, column)

    add_column('stage_best', sa.Column('route_position', sa.Integer(), nullable=True))
    add_column('stage_best', sa.Column('season_id', sa.String(), nullable=False, server_default='legacy'))
    add_column('stage_best', sa.Column('ruleset_version', sa.Integer(), nullable=False, server_default='0'))
    add_column('stage_best', sa.Column('content_version', sa.Integer(), nullable=False, server_default='0'))

    add_column('stage_runs', sa.Column('route_position', sa.Integer(), nullable=True))
    add_column('stage_runs', sa.Column('season_id', sa.String(), nullable=False, server_default='legacy'))
    add_column('stage_runs', sa.Column('ruleset_version', sa.Integer(), nullable=False, server_default='0'))
    add_column('stage_runs', sa.Column('content_version', sa.Integer(), nullable=False, server_default='0'))
    add_column('stage_runs', sa.Column('attempt_id', sa.String(), nullable=True))
    add_column('stage_runs', sa.Column('passed', sa.Boolean(), nullable=False, server_default=sa.true()))
    add_column('stage_runs', sa.Column('correct_answers', sa.Integer(), nullable=False, server_default='0'))
    add_column('stage_runs', sa.Column('answers', sa.JSON(), nullable=True))

    # Las marcas anteriores quedan deliberadamente en "legacy"; nunca se
    # mezclan ni se reinterpretan dentro de la Temporada 1.
    op.alter_column('stage_best', 'season_id', server_default='season-1')
    op.alter_column('stage_best', 'ruleset_version', server_default='1')
    op.alter_column('stage_best', 'content_version', server_default='1')
    op.alter_column('stage_runs', 'season_id', server_default='season-1')
    op.alter_column('stage_runs', 'ruleset_version', server_default='1')
    op.alter_column('stage_runs', 'content_version', server_default='1')

    constraints = {item['name'] for item in sa.inspect(bind).get_unique_constraints('stage_best')}
    if 'uix_user_stage_best_difficulty' in constraints:
        op.drop_constraint('uix_user_stage_best_difficulty', 'stage_best', type_='unique')
    if 'uix_user_stage_best_season' not in constraints:
        op.create_unique_constraint(
            'uix_user_stage_best_season',
            'stage_best',
            ['user_id', 'stage_id', 'difficulty', 'season_id'],
        )

    op.create_index('ix_stage_best_season_id', 'stage_best', ['season_id'])
    op.create_index('ix_stage_runs_season_id', 'stage_runs', ['season_id'])
    op.create_unique_constraint('uq_stage_runs_attempt_id', 'stage_runs', ['attempt_id'])
    op.create_foreign_key(
        'fk_stage_runs_attempt_id',
        'stage_runs',
        'career_attempts',
        ['attempt_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_stage_runs_attempt_id', 'stage_runs', type_='foreignkey')
    op.drop_constraint('uq_stage_runs_attempt_id', 'stage_runs', type_='unique')
    op.drop_index('ix_stage_runs_season_id', table_name='stage_runs')
    op.drop_index('ix_stage_best_season_id', table_name='stage_best')
    op.drop_constraint('uix_user_stage_best_season', 'stage_best', type_='unique')
    op.create_unique_constraint(
        'uix_user_stage_best_difficulty', 'stage_best', ['user_id', 'stage_id', 'difficulty']
    )
    for column in ('answers', 'correct_answers', 'passed', 'attempt_id', 'content_version', 'ruleset_version', 'season_id', 'route_position'):
        op.drop_column('stage_runs', column)
    for column in ('content_version', 'ruleset_version', 'season_id', 'route_position'):
        op.drop_column('stage_best', column)
    op.drop_table('career_attempts')
    op.drop_table('career_season_profiles')
