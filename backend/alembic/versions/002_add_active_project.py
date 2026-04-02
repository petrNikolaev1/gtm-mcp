"""Add active_project_id to mcp_users

Revision ID: 002_active_project
Revises: 001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = '002_active_project'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('mcp_users', sa.Column('active_project_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_user_active_project', 'mcp_users', 'projects', ['active_project_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_user_active_project', 'mcp_users', type_='foreignkey')
    op.drop_column('mcp_users', 'active_project_id')
