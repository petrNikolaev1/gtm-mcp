"""Add created_by, monitoring_enabled, sequence_id to campaigns; destination to gathering_runs

Revision ID: 004_campaign_fields
Revises: 003_nullable_contact
"""
from alembic import op
import sqlalchemy as sa

revision = '004_campaign_fields'
down_revision = '003_nullable_contact'


def upgrade() -> None:
    op.add_column('campaigns', sa.Column('created_by', sa.String(20), server_default='user'))
    op.add_column('campaigns', sa.Column('monitoring_enabled', sa.Boolean(), server_default='false'))
    op.add_column('campaigns', sa.Column('sequence_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_campaigns_sequence', 'campaigns', 'generated_sequences', ['sequence_id'], ['id'], ondelete='SET NULL')

    op.add_column('gathering_runs', sa.Column('destination', sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column('gathering_runs', 'destination')
    op.drop_constraint('fk_campaigns_sequence', 'campaigns', type_='foreignkey')
    op.drop_column('campaigns', 'sequence_id')
    op.drop_column('campaigns', 'monitoring_enabled')
    op.drop_column('campaigns', 'created_by')
