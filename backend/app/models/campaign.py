"""Campaign + GOD_SEQUENCE models."""
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Float, Boolean,
    ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class Campaign(Base):
    """Campaign record — represents a SmartLead campaign."""
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(500), nullable=False)
    external_id = Column(String(255), nullable=True)  # SmartLead campaign ID
    platform = Column(String(50), server_default="smartlead")
    status = Column(String(50), server_default="draft")  # draft, active, paused, completed
    created_by = Column(String(20), server_default="user")  # "mcp" or "user"
    monitoring_enabled = Column(Boolean, server_default="false")
    sequence_id = Column(Integer, ForeignKey("generated_sequences.id", ondelete="SET NULL"), nullable=True)

    leads_count = Column(Integer, server_default="0")
    email_account_ids = Column(JSONB, nullable=True)  # [{id, email, name}, ...] — SmartLead accounts
    config = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CampaignSnapshot(Base):
    __tablename__ = "campaign_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    leads_count = Column(Integer, nullable=False, server_default="0")
    total_replies = Column(Integer, nullable=False, server_default="0")
    warm_replies = Column(Integer, nullable=False, server_default="0")
    meetings_count = Column(Integer, nullable=False, server_default="0")

    warm_reply_rate = Column(Float, nullable=True)
    meeting_rate = Column(Float, nullable=True)
    quality_score = Column(Float, nullable=True)

    campaign_name = Column(String(500), nullable=False)
    platform = Column(String(50), nullable=True)
    market = Column(String(50), nullable=True)

    sequence_steps = Column(JSONB, nullable=True)
    sequence_step_count = Column(Integer, nullable=True)

    is_latest = Column(Boolean, nullable=False, server_default="true")
    snapshotted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_cs_campaign_latest", "campaign_id", "is_latest"),
        Index("ix_cs_project_score", "project_id", "quality_score"),
    )


class CampaignPattern(Base):
    __tablename__ = "campaign_patterns"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    scope_level = Column(String(20), nullable=False, server_default="universal")
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    business_key = Column(String(255), nullable=True)

    pattern_type = Column(String(50), nullable=False)
    pattern_key = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)

    market = Column(String(50), nullable=True)
    channel = Column(String(50), nullable=True)
    segment = Column(String(100), nullable=True)

    confidence = Column(Float, nullable=True)
    evidence_campaign_ids = Column(JSONB, nullable=True)
    sample_size = Column(Integer, nullable=True)

    version = Column(Integer, nullable=False, server_default="1")
    is_active = Column(Boolean, nullable=False, server_default="true")

    extraction_run_id = Column(Integer, ForeignKey("campaign_intelligence_runs.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_cp_type_market", "pattern_type", "market", "is_active"),
        Index("ix_cp_company", "company_id", "is_active"),
    )


class CampaignIntelligenceRun(Base):
    __tablename__ = "campaign_intelligence_runs"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    trigger = Column(String(50), nullable=False)
    campaigns_analyzed = Column(Integer, nullable=True)
    patterns_created = Column(Integer, nullable=True, server_default="0")
    patterns_updated = Column(Integer, nullable=True, server_default="0")

    model_used = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)

    status = Column(String(30), nullable=False, server_default="processing")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GeneratedSequence(Base):
    __tablename__ = "generated_sequences"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    generation_prompt = Column(Text, nullable=True)
    patterns_used = Column(JSONB, nullable=True)

    campaign_name = Column(String(500), nullable=True)
    sequence_steps = Column(JSONB, nullable=False)
    sequence_step_count = Column(Integer, nullable=True)
    rationale = Column(Text, nullable=True)

    status = Column(String(30), nullable=False, server_default="draft")
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    pushed_campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    pushed_at = Column(DateTime(timezone=True), nullable=True)

    model_used = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_gs_project_status", "project_id", "status"),
    )
