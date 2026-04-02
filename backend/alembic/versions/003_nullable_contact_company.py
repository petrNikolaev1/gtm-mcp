"""Make extracted_contacts.discovered_company_id nullable

Revision ID: 003_nullable_contact
Revises: 002_active_project
"""
from alembic import op
import sqlalchemy as sa

revision = '003_nullable_contact'
down_revision = '002_active_project'


def upgrade() -> None:
    op.alter_column('extracted_contacts', 'discovered_company_id', nullable=True)


def downgrade() -> None:
    op.alter_column('extracted_contacts', 'discovered_company_id', nullable=False)
