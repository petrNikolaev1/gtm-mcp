"""Add missing indexes for performance

Revision ID: 005_indexes
Revises: 004_campaign_fields
"""
from alembic import op

revision = '005_indexes'
down_revision = '004_campaign_fields'


def upgrade() -> None:
    # P3: company_id on gathering_runs (already has index=True in model, but ensure)
    op.create_index('ix_gathering_runs_company_id', 'gathering_runs', ['company_id'], if_not_exists=True)
    # Source links by run
    op.create_index('ix_company_source_links_run', 'company_source_links', ['gathering_run_id'], if_not_exists=True)
    # Extracted contacts by project
    op.create_index('ix_extracted_contacts_project', 'extracted_contacts', ['project_id'], if_not_exists=True)
    # MCP replies by project
    op.create_index('ix_mcp_replies_project', 'mcp_replies', ['project_id'], if_not_exists=True)
    # Conversation logs by user
    op.create_index('ix_mcp_conversation_logs_user', 'mcp_conversation_logs', ['user_id'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_mcp_conversation_logs_user', 'mcp_conversation_logs')
    op.drop_index('ix_mcp_replies_project', 'mcp_replies')
    op.drop_index('ix_extracted_contacts_project', 'extracted_contacts')
    op.drop_index('ix_company_source_links_run', 'company_source_links')
    op.drop_index('ix_gathering_runs_company_id', 'gathering_runs')
