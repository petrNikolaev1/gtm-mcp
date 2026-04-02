"""Gathering pipeline models — mirrored from main backend."""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean, Float, Numeric, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class GatheringRun(Base):
    __tablename__ = "gathering_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    source_type = Column(String(100), nullable=False)
    source_label = Column(String(255), nullable=True)
    filters = Column(JSONB, nullable=False)
    people_filters = Column(JSONB, nullable=True)  # Apollo people filters (title, seniority, etc.)
    filter_hash = Column(String(64), nullable=False)

    status = Column(String(30), nullable=False, server_default="pending")
    current_phase = Column(String(30), nullable=False, server_default="gather")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    raw_results_count = Column(Integer, server_default="0")
    new_companies_count = Column(Integer, server_default="0")
    duplicate_count = Column(Integer, server_default="0")
    rejected_count = Column(Integer, server_default="0")
    error_count = Column(Integer, server_default="0")

    credits_used = Column(Integer, server_default="0")
    total_cost_usd = Column(Numeric(10, 4), server_default="0")

    target_rate = Column(Float, nullable=True)
    avg_analysis_confidence = Column(Float, nullable=True)
    cost_per_target_usd = Column(Numeric(10, 4), nullable=True)

    destination = Column(String(20), nullable=True)  # "smartlead", "getsales", "both"
    triggered_by = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    raw_output_sample = Column(JSONB, nullable=True)

    # KPI configuration (user-settable via MCP prompts)
    target_people = Column(Integer, nullable=True)            # total contacts to gather (default 100)
    target_companies = Column(Integer, nullable=True)         # target companies needed (derived: ceil(target_people/max_people_per_company))
    max_people_per_company = Column(Integer, nullable=True)   # max contacts per company (default 3)

    # Auto-pipeline progress (written by orchestrator each iteration)
    total_targets_found = Column(Integer, server_default="0")
    total_people_found = Column(Integer, server_default="0")
    pages_fetched = Column(Integer, server_default="0")
    current_iteration = Column(Integer, server_default="0")

    # Campaign link — draft campaign created before pipeline starts
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)

    # Pause control
    paused_at = Column(DateTime(timezone=True), nullable=True)
    resumed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    source_links = relationship("CompanySourceLink", back_populates="gathering_run", cascade="all, delete-orphan")
    approval_gates = relationship("ApprovalGate", back_populates="gathering_run")

    __table_args__ = (
        Index("ix_gr_project_source", "project_id", "source_type", "status"),
        Index("ix_gr_filter_hash", "project_id", "filter_hash"),
    )


class CompanySourceLink(Base):
    __tablename__ = "company_source_links"

    id = Column(Integer, primary_key=True, index=True)
    discovered_company_id = Column(Integer, ForeignKey("discovered_companies.id", ondelete="CASCADE"), nullable=False, index=True)
    gathering_run_id = Column(Integer, ForeignKey("gathering_runs.id", ondelete="CASCADE"), nullable=False, index=True)

    source_rank = Column(Integer, nullable=True)
    source_data = Column(JSONB, nullable=True)
    source_confidence = Column(Float, nullable=True)
    found_at = Column(DateTime(timezone=True), server_default=func.now())

    gathering_run = relationship("GatheringRun", back_populates="source_links")

    __table_args__ = (
        Index("uq_csl_company_run", "discovered_company_id", "gathering_run_id", unique=True),
    )


class CompanyScrape(Base):
    __tablename__ = "company_scrapes"

    id = Column(Integer, primary_key=True, index=True)
    discovered_company_id = Column(Integer, ForeignKey("discovered_companies.id", ondelete="CASCADE"), nullable=False, index=True)

    url = Column(Text, nullable=False)
    page_path = Column(String(255), server_default="/")
    raw_html = Column(Text, nullable=True)
    clean_text = Column(Text, nullable=True)
    page_metadata = Column(JSONB, nullable=True)

    scraped_at = Column(DateTime(timezone=True), server_default=func.now())
    is_current = Column(Boolean, server_default="true")
    version = Column(Integer, server_default="1")

    scrape_method = Column(String(50), server_default="httpx")
    scrape_status = Column(String(30), server_default="success")
    error_message = Column(Text, nullable=True)
    http_status_code = Column(Integer, nullable=True)
    text_size_bytes = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_cs_company_current", "discovered_company_id", "is_current"),
    )


class GatheringPrompt(Base):
    __tablename__ = "gathering_prompts"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)

    name = Column(String(255), nullable=False)
    prompt_text = Column(Text, nullable=False)
    prompt_hash = Column(String(64), nullable=False, unique=True)

    category = Column(String(50), server_default="icp_analysis")
    model_default = Column(String(100), server_default="gpt-4o-mini")
    version = Column(Integer, server_default="1")
    parent_prompt_id = Column(Integer, ForeignKey("gathering_prompts.id", ondelete="SET NULL"), nullable=True)

    usage_count = Column(Integer, server_default="0")
    avg_target_rate = Column(Float, nullable=True)
    avg_confidence = Column(Float, nullable=True)
    total_companies_analyzed = Column(Integer, server_default="0")

    created_by = Column(String(100), nullable=True)
    is_active = Column(Boolean, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    analysis_runs = relationship("AnalysisRun", back_populates="prompt", foreign_keys="AnalysisRun.prompt_id")

    __table_args__ = (
        Index("ix_gp_company_project", "company_id", "project_id"),
    )


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    prompt_id = Column(Integer, ForeignKey("gathering_prompts.id", ondelete="SET NULL"), nullable=True, index=True)
    model = Column(String(100), nullable=False)
    prompt_hash = Column(String(64), nullable=False)
    prompt_text = Column(Text, nullable=True)

    status = Column(String(30), server_default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    total_analyzed = Column(Integer, server_default="0")
    targets_found = Column(Integer, server_default="0")
    rejected_count = Column(Integer, server_default="0")
    avg_confidence = Column(Float, nullable=True)

    total_cost_usd = Column(Numeric(10, 4), server_default="0")
    total_tokens = Column(Integer, server_default="0")

    triggered_by = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    prompt = relationship("GatheringPrompt", back_populates="analysis_runs", foreign_keys=[prompt_id])
    results = relationship("AnalysisResult", back_populates="analysis_run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_ar_project_status", "project_id", "status"),
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)
    analysis_run_id = Column(Integer, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_company_id = Column(Integer, ForeignKey("discovered_companies.id", ondelete="CASCADE"), nullable=False, index=True)

    is_target = Column(Boolean, server_default="false")
    confidence = Column(Float, nullable=True)
    segment = Column(String(100), nullable=True)
    reasoning = Column(Text, nullable=True)
    scores = Column(JSONB, nullable=True)
    raw_output = Column(Text, nullable=True)

    override_verdict = Column(Boolean, nullable=True)
    override_reason = Column(Text, nullable=True)

    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    analysis_run = relationship("AnalysisRun", back_populates="results")

    __table_args__ = (
        Index("uq_ar_run_company", "analysis_run_id", "discovered_company_id", unique=True),
        Index("ix_ar_run_target", "analysis_run_id", "is_target"),
    )


class ApprovalGate(Base):
    __tablename__ = "approval_gates"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    gathering_run_id = Column(Integer, ForeignKey("gathering_runs.id", ondelete="SET NULL"), nullable=True)

    gate_type = Column(String(50), nullable=False)
    gate_label = Column(String(255), nullable=False)
    scope = Column(JSONB, nullable=False)

    status = Column(String(30), server_default="pending")
    decided_by = Column(String(100), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_note = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    gathering_run = relationship("GatheringRun", back_populates="approval_gates")

    __table_args__ = (
        Index("ix_ag_project_status", "project_id", "status"),
        Index("ix_ag_pending", "status", postgresql_where="status = 'pending'"),
    )
