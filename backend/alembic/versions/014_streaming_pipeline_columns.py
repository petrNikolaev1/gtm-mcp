"""Add status, scraped_text, scraped_at to discovered_companies for streaming pipeline.

Revision ID: 014_streaming_pipeline_columns
Revises: 013_pipeline_campaign_accounts
"""
from alembic import op
import sqlalchemy as sa

revision = "014_streaming_pipeline_columns"
down_revision = "013_pipeline_campaign_accounts"


def upgrade():
    op.add_column("discovered_companies", sa.Column("status", sa.String(30), nullable=True))
    op.add_column("discovered_companies", sa.Column("scraped_text", sa.Text(), nullable=True))
    op.add_column("discovered_companies", sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_dc_status", "discovered_companies", ["project_id", "status"])


def downgrade():
    op.drop_index("ix_dc_status", table_name="discovered_companies")
    op.drop_column("discovered_companies", "scraped_at")
    op.drop_column("discovered_companies", "scraped_text")
    op.drop_column("discovered_companies", "status")
