"""Add email_account_ids to campaigns, campaign_id FK to gathering_runs.

Revision ID: 013_pipeline_campaign_accounts
Revises: 012_apollo_taxonomy_db
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "013_pipeline_campaign_accounts"
down_revision = "012_apollo_taxonomy_db"


def upgrade():
    op.add_column("campaigns", sa.Column("email_account_ids", JSONB, nullable=True))
    op.add_column("gathering_runs", sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_gr_campaign", "gathering_runs", ["campaign_id"])


def downgrade():
    op.drop_index("ix_gr_campaign", table_name="gathering_runs")
    op.drop_column("gathering_runs", "campaign_id")
    op.drop_column("campaigns", "email_account_ids")
