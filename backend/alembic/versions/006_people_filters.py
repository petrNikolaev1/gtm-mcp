"""Add people_filters to gathering_runs

Revision ID: 006_people_filters
Revises: 005_indexes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '006_people_filters'
down_revision = '005_indexes'


def upgrade() -> None:
    op.add_column('gathering_runs', sa.Column('people_filters', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('gathering_runs', 'people_filters')
