"""Initial MCP schema

Revision ID: 001_initial
Revises:
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MCPUsers
    op.create_table('mcp_users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(255), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_mcp_users_email', 'mcp_users', ['email'])

    # MCPApiTokens
    op.create_table('mcp_api_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('mcp_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_prefix', sa.String(12), nullable=False),
        sa.Column('token_hash', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), server_default='default'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_token_prefix', 'mcp_api_tokens', ['token_prefix'])

    # MCPIntegrationSettings
    op.create_table('mcp_integration_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('mcp_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('integration_name', sa.String(50), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('is_connected', sa.Boolean(), server_default='false'),
        sa.Column('connection_info', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('uq_user_integration', 'mcp_integration_settings', ['user_id', 'integration_name'], unique=True)

    # Companies
    op.create_table('companies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('domain', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Projects
    op.create_table('projects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('mcp_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('target_segments', sa.Text(), nullable=True),
        sa.Column('target_industries', sa.Text(), nullable=True),
        sa.Column('sender_name', sa.String(255), nullable=True),
        sa.Column('sender_company', sa.String(255), nullable=True),
        sa.Column('sender_position', sa.String(255), nullable=True),
        sa.Column('campaign_filters', postgresql.JSONB(), server_default='[]'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_project_user', 'projects', ['user_id', 'is_active'])

    # Domains
    op.create_table('domains',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('domain', sa.String(255), unique=True, nullable=False),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('source', sa.String(30), nullable=True),
        sa.Column('times_seen', sa.Integer(), server_default='1'),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Campaigns
    op.create_table('campaigns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(500), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('platform', sa.String(50), server_default='smartlead'),
        sa.Column('status', sa.String(50), server_default='draft'),
        sa.Column('leads_count', sa.Integer(), server_default='0'),
        sa.Column('config', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # DiscoveredCompanies
    op.create_table('discovered_companies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('name', sa.String(500), nullable=True),
        sa.Column('industry', sa.String(255), nullable=True),
        sa.Column('employee_count', sa.Integer(), nullable=True),
        sa.Column('employee_range', sa.String(50), nullable=True),
        sa.Column('country', sa.String(100), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('linkedin_url', sa.String(500), nullable=True),
        sa.Column('website_url', sa.String(500), nullable=True),
        sa.Column('is_blacklisted', sa.Boolean(), server_default='false'),
        sa.Column('blacklist_reason', sa.String(255), nullable=True),
        sa.Column('is_pre_filtered', sa.Boolean(), server_default='false'),
        sa.Column('pre_filter_reason', sa.String(255), nullable=True),
        sa.Column('is_target', sa.Boolean(), nullable=True),
        sa.Column('analysis_confidence', sa.Float(), nullable=True),
        sa.Column('analysis_segment', sa.String(100), nullable=True),
        sa.Column('analysis_reasoning', sa.Text(), nullable=True),
        sa.Column('is_enriched', sa.Boolean(), server_default='false'),
        sa.Column('enrichment_source', sa.String(50), nullable=True),
        sa.Column('source_data', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('uq_dc_project_domain', 'discovered_companies', ['project_id', 'domain'], unique=True)

    # ExtractedContacts
    op.create_table('extracted_contacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discovered_company_id', sa.Integer(), sa.ForeignKey('discovered_companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('first_name', sa.String(255), nullable=True),
        sa.Column('last_name', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('job_title', sa.String(255), nullable=True),
        sa.Column('linkedin_url', sa.String(500), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('email_verified', sa.Boolean(), nullable=True),
        sa.Column('email_source', sa.String(50), nullable=True),
        sa.Column('source_data', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # GatheringRuns
    op.create_table('gathering_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_type', sa.String(100), nullable=False),
        sa.Column('source_label', sa.String(255), nullable=True),
        sa.Column('filters', postgresql.JSONB(), nullable=False),
        sa.Column('filter_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(30), server_default='pending'),
        sa.Column('current_phase', sa.String(30), server_default='gather'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('raw_results_count', sa.Integer(), server_default='0'),
        sa.Column('new_companies_count', sa.Integer(), server_default='0'),
        sa.Column('duplicate_count', sa.Integer(), server_default='0'),
        sa.Column('rejected_count', sa.Integer(), server_default='0'),
        sa.Column('error_count', sa.Integer(), server_default='0'),
        sa.Column('credits_used', sa.Integer(), server_default='0'),
        sa.Column('total_cost_usd', sa.Numeric(10, 4), server_default='0'),
        sa.Column('target_rate', sa.Float(), nullable=True),
        sa.Column('avg_analysis_confidence', sa.Float(), nullable=True),
        sa.Column('cost_per_target_usd', sa.Numeric(10, 4), nullable=True),
        sa.Column('triggered_by', sa.String(100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('raw_output_sample', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_gr_project_source', 'gathering_runs', ['project_id', 'source_type', 'status'])

    # CompanySourceLinks
    op.create_table('company_source_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discovered_company_id', sa.Integer(), sa.ForeignKey('discovered_companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gathering_run_id', sa.Integer(), sa.ForeignKey('gathering_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_rank', sa.Integer(), nullable=True),
        sa.Column('source_data', postgresql.JSONB(), nullable=True),
        sa.Column('source_confidence', sa.Float(), nullable=True),
        sa.Column('found_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('uq_csl_company_run', 'company_source_links', ['discovered_company_id', 'gathering_run_id'], unique=True)

    # CompanyScrapes
    op.create_table('company_scrapes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discovered_company_id', sa.Integer(), sa.ForeignKey('discovered_companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('page_path', sa.String(255), server_default='/'),
        sa.Column('raw_html', sa.Text(), nullable=True),
        sa.Column('clean_text', sa.Text(), nullable=True),
        sa.Column('page_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('is_current', sa.Boolean(), server_default='true'),
        sa.Column('version', sa.Integer(), server_default='1'),
        sa.Column('scrape_method', sa.String(50), server_default='httpx'),
        sa.Column('scrape_status', sa.String(30), server_default='success'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('http_status_code', sa.Integer(), nullable=True),
        sa.Column('text_size_bytes', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # GatheringPrompts
    op.create_table('gathering_prompts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('prompt_text', sa.Text(), nullable=False),
        sa.Column('prompt_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('category', sa.String(50), server_default='icp_analysis'),
        sa.Column('model_default', sa.String(100), server_default='gpt-4o-mini'),
        sa.Column('version', sa.Integer(), server_default='1'),
        sa.Column('parent_prompt_id', sa.Integer(), sa.ForeignKey('gathering_prompts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('usage_count', sa.Integer(), server_default='0'),
        sa.Column('avg_target_rate', sa.Float(), nullable=True),
        sa.Column('avg_confidence', sa.Float(), nullable=True),
        sa.Column('total_companies_analyzed', sa.Integer(), server_default='0'),
        sa.Column('created_by', sa.String(100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # AnalysisRuns
    op.create_table('analysis_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('prompt_id', sa.Integer(), sa.ForeignKey('gathering_prompts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('prompt_hash', sa.String(64), nullable=False),
        sa.Column('prompt_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(30), server_default='pending'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_analyzed', sa.Integer(), server_default='0'),
        sa.Column('targets_found', sa.Integer(), server_default='0'),
        sa.Column('rejected_count', sa.Integer(), server_default='0'),
        sa.Column('avg_confidence', sa.Float(), nullable=True),
        sa.Column('total_cost_usd', sa.Numeric(10, 4), server_default='0'),
        sa.Column('total_tokens', sa.Integer(), server_default='0'),
        sa.Column('triggered_by', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # AnalysisResults
    op.create_table('analysis_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('analysis_run_id', sa.Integer(), sa.ForeignKey('analysis_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('discovered_company_id', sa.Integer(), sa.ForeignKey('discovered_companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_target', sa.Boolean(), server_default='false'),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('segment', sa.String(100), nullable=True),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('scores', postgresql.JSONB(), nullable=True),
        sa.Column('raw_output', sa.Text(), nullable=True),
        sa.Column('override_verdict', sa.Boolean(), nullable=True),
        sa.Column('override_reason', sa.Text(), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('uq_ar_run_company', 'analysis_results', ['analysis_run_id', 'discovered_company_id'], unique=True)

    # ApprovalGates
    op.create_table('approval_gates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gathering_run_id', sa.Integer(), sa.ForeignKey('gathering_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('gate_type', sa.String(50), nullable=False),
        sa.Column('gate_label', sa.String(255), nullable=False),
        sa.Column('scope', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(30), server_default='pending'),
        sa.Column('decided_by', sa.String(100), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # CampaignSnapshots
    op.create_table('campaign_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('leads_count', sa.Integer(), server_default='0'),
        sa.Column('total_replies', sa.Integer(), server_default='0'),
        sa.Column('warm_replies', sa.Integer(), server_default='0'),
        sa.Column('meetings_count', sa.Integer(), server_default='0'),
        sa.Column('warm_reply_rate', sa.Float(), nullable=True),
        sa.Column('meeting_rate', sa.Float(), nullable=True),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('campaign_name', sa.String(500), nullable=False),
        sa.Column('platform', sa.String(50), nullable=True),
        sa.Column('market', sa.String(50), nullable=True),
        sa.Column('sequence_steps', postgresql.JSONB(), nullable=True),
        sa.Column('sequence_step_count', sa.Integer(), nullable=True),
        sa.Column('is_latest', sa.Boolean(), server_default='true'),
        sa.Column('snapshotted_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # CampaignIntelligenceRuns
    op.create_table('campaign_intelligence_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trigger', sa.String(50), nullable=False),
        sa.Column('campaigns_analyzed', sa.Integer(), nullable=True),
        sa.Column('patterns_created', sa.Integer(), server_default='0'),
        sa.Column('patterns_updated', sa.Integer(), server_default='0'),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('status', sa.String(30), server_default='processing'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # CampaignPatterns
    op.create_table('campaign_patterns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scope_level', sa.String(20), server_default='universal'),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('business_key', sa.String(255), nullable=True),
        sa.Column('pattern_type', sa.String(50), nullable=False),
        sa.Column('pattern_key', sa.String(100), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('market', sa.String(50), nullable=True),
        sa.Column('channel', sa.String(50), nullable=True),
        sa.Column('segment', sa.String(100), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('evidence_campaign_ids', postgresql.JSONB(), nullable=True),
        sa.Column('sample_size', sa.Integer(), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('extraction_run_id', sa.Integer(), sa.ForeignKey('campaign_intelligence_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # GeneratedSequences
    op.create_table('generated_sequences',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('generation_prompt', sa.Text(), nullable=True),
        sa.Column('patterns_used', postgresql.JSONB(), nullable=True),
        sa.Column('campaign_name', sa.String(500), nullable=True),
        sa.Column('sequence_steps', postgresql.JSONB(), nullable=False),
        sa.Column('sequence_step_count', sa.Integer(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('status', sa.String(30), server_default='draft'),
        sa.Column('reviewed_by', sa.String(100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pushed_campaign_id', sa.Integer(), sa.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # RefinementRuns
    op.create_table('refinement_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('gathering_run_id', sa.Integer(), sa.ForeignKey('gathering_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(30), server_default='running'),
        sa.Column('target_accuracy', sa.Float(), server_default='0.9'),
        sa.Column('max_iterations', sa.Integer(), server_default='8'),
        sa.Column('current_iteration', sa.Integer(), server_default='0'),
        sa.Column('final_accuracy', sa.Float(), nullable=True),
        sa.Column('total_cost_usd', sa.Float(), server_default='0'),
        sa.Column('total_tokens', sa.Integer(), server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # RefinementIterations
    op.create_table('refinement_iterations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('refinement_run_id', sa.Integer(), sa.ForeignKey('refinement_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('iteration_number', sa.Integer(), nullable=False),
        sa.Column('accuracy', sa.Float(), nullable=False),
        sa.Column('true_positives', sa.Integer(), nullable=True),
        sa.Column('true_negatives', sa.Integer(), nullable=True),
        sa.Column('false_positives', sa.Integer(), nullable=True),
        sa.Column('false_negatives', sa.Integer(), nullable=True),
        sa.Column('sample_size', sa.Integer(), nullable=True),
        sa.Column('false_positive_patterns', postgresql.JSONB(), nullable=True),
        sa.Column('false_negative_patterns', postgresql.JSONB(), nullable=True),
        sa.Column('prompt_id', sa.Integer(), sa.ForeignKey('gathering_prompts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('prompt_adjustments', sa.Text(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('uq_ri_run_iter', 'refinement_iterations', ['refinement_run_id', 'iteration_number'], unique=True)

    # MCPUsageLogs
    op.create_table('mcp_usage_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('mcp_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('tool_name', sa.String(100), nullable=True),
        sa.Column('metadata', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('mcp_usage_logs')
    op.drop_table('refinement_iterations')
    op.drop_table('refinement_runs')
    op.drop_table('generated_sequences')
    op.drop_table('campaign_patterns')
    op.drop_table('campaign_intelligence_runs')
    op.drop_table('campaign_snapshots')
    op.drop_table('approval_gates')
    op.drop_table('analysis_results')
    op.drop_table('analysis_runs')
    op.drop_table('gathering_prompts')
    op.drop_table('company_scrapes')
    op.drop_table('company_source_links')
    op.drop_table('gathering_runs')
    op.drop_table('extracted_contacts')
    op.drop_table('discovered_companies')
    op.drop_table('campaigns')
    op.drop_table('domains')
    op.drop_table('projects')
    op.drop_table('companies')
    op.drop_table('mcp_integration_settings')
    op.drop_table('mcp_api_tokens')
    op.drop_table('mcp_users')
