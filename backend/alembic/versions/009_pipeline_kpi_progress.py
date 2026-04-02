"""Add KPI, progress, and pause columns to gathering_runs

Revision ID: 009_kpi_progress
Revises: 008_iterations
"""
from alembic import op
import sqlalchemy as sa

revision = '009_kpi_progress'
down_revision = '008_iterations'


def upgrade() -> None:
    # KPI configuration (user-settable via MCP prompts)
    op.add_column('gathering_runs', sa.Column('target_count', sa.Integer(), nullable=True))
    op.add_column('gathering_runs', sa.Column('min_targets', sa.Integer(), nullable=True))
    op.add_column('gathering_runs', sa.Column('contacts_per_company', sa.Integer(), nullable=True))

    # Auto-pipeline progress (written by orchestrator each iteration)
    op.add_column('gathering_runs', sa.Column('total_targets_found', sa.Integer(), server_default='0'))
    op.add_column('gathering_runs', sa.Column('total_people_found', sa.Integer(), server_default='0'))
    op.add_column('gathering_runs', sa.Column('pages_fetched', sa.Integer(), server_default='0'))
    op.add_column('gathering_runs', sa.Column('current_iteration', sa.Integer(), server_default='0'))

    # Pause control
    op.add_column('gathering_runs', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('gathering_runs', sa.Column('resumed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('gathering_runs', 'resumed_at')
    op.drop_column('gathering_runs', 'paused_at')
    op.drop_column('gathering_runs', 'current_iteration')
    op.drop_column('gathering_runs', 'pages_fetched')
    op.drop_column('gathering_runs', 'total_people_found')
    op.drop_column('gathering_runs', 'total_targets_found')
    op.drop_column('gathering_runs', 'contacts_per_company')
    op.drop_column('gathering_runs', 'min_targets')
    op.drop_column('gathering_runs', 'target_count')
