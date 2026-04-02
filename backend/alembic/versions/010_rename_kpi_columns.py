"""Rename KPI columns for clarity

target_count → target_people
min_targets → target_companies
contacts_per_company → max_people_per_company

Revision ID: 010_rename_kpi
Revises: 009_kpi_progress
"""
from alembic import op

revision = '010_rename_kpi'
down_revision = '009_kpi_progress'


def upgrade() -> None:
    op.alter_column('gathering_runs', 'target_count', new_column_name='target_people')
    op.alter_column('gathering_runs', 'min_targets', new_column_name='target_companies')
    op.alter_column('gathering_runs', 'contacts_per_company', new_column_name='max_people_per_company')


def downgrade() -> None:
    op.alter_column('gathering_runs', 'target_people', new_column_name='target_count')
    op.alter_column('gathering_runs', 'target_companies', new_column_name='min_targets')
    op.alter_column('gathering_runs', 'max_people_per_company', new_column_name='contacts_per_company')
