"""SmartLead accounts cache + email account lists.

Revision ID: 015_smartlead_accounts_cache
Revises: 014_streaming_pipeline_columns
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "015_smartlead_accounts_cache"
down_revision = "014_streaming_pipeline_columns"


def upgrade():
    op.create_table(
        "smartlead_accounts_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("from_email", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "account_id", name="uq_sl_cache_user_account"),
    )
    op.create_index("ix_sl_cache_user", "smartlead_accounts_cache", ["user_id"])

    op.create_table(
        "email_account_lists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("filter_pattern", sa.String(255), nullable=True),
        sa.Column("account_ids", JSONB, nullable=False),
        sa.Column("account_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("email_account_lists")
    op.drop_index("ix_sl_cache_user", table_name="smartlead_accounts_cache")
    op.drop_table("smartlead_accounts_cache")
