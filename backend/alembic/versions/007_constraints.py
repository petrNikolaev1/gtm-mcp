"""Add constraints: UNIQUE email, CHECK cost >= 0

Revision ID: 007_constraints
Revises: 006_people_filters
"""
from alembic import op
import sqlalchemy as sa

revision = '007_constraints'
down_revision = '006_people_filters'


def upgrade() -> None:
    # M24: Prevent duplicate signup emails
    op.create_unique_constraint('uq_mcp_users_email', 'mcp_users', ['email'])

    # M20: Cost columns must be non-negative
    op.create_check_constraint('ck_gathering_runs_cost_positive', 'gathering_runs',
                               'total_cost_usd >= 0')
    op.create_check_constraint('ck_gathering_runs_credits_positive', 'gathering_runs',
                               'credits_used >= 0')


def downgrade() -> None:
    op.drop_constraint('ck_gathering_runs_credits_positive', 'gathering_runs')
    op.drop_constraint('ck_gathering_runs_cost_positive', 'gathering_runs')
    op.drop_constraint('uq_mcp_users_email', 'mcp_users')
