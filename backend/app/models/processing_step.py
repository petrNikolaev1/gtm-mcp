"""ProcessingStep — flexible pipeline steps (AI, regex, scrape) with iteration tracking.

Architecture:
- Each step = a column in the pipeline results
- Steps can be: ai (GPT classification), regex (pattern matching), scrape (website page)
- Essential steps (segment, is_target) are permanent — cannot be removed
- Custom steps are user-managed via MCP chat
- Each add/remove creates a new PipelineIteration
- All iterations are historically selectable in UI
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class PipelineIteration(Base):
    """A snapshot of pipeline processing rules at a point in time.

    Each change to processing steps (add/remove/modify) creates a new iteration.
    The iteration captures the FULL step config at that moment, so historical
    iterations can be viewed with their original columns.
    """
    __tablename__ = "pipeline_iterations"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    gathering_run_id = Column(Integer, ForeignKey("gathering_runs.id", ondelete="SET NULL"), nullable=True, index=True)

    iteration_number = Column(Integer, nullable=False)  # 1, 2, 3, ...
    label = Column(String(255), nullable=True)  # Human-readable: "Added size_segment column"
    trigger = Column(String(100), nullable=False)  # "add_step", "remove_step", "modify_step", "initial", "re_analyze"

    # Snapshot of ALL active steps at this iteration (for historical viewing)
    steps_snapshot = Column(JSONB, nullable=False)  # [{name, type, config, output_column, step_number}, ...]

    # What changed
    change_detail = Column(JSONB, nullable=True)  # {action: "add", step_name: "...", ...}

    # Snapshots for historical comparison
    filters_snapshot = Column(JSONB, nullable=True)   # Apollo filters used in this iteration
    prompt_snapshot = Column(Text, nullable=True)      # Classification prompt text used
    target_count = Column(Integer, nullable=True)      # Targets found in this iteration
    target_rate = Column(Float, nullable=True)         # Target rate (targets/total)

    status = Column(String(30), server_default="pending")  # pending, running, completed, failed
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Results summary
    companies_processed = Column(Integer, server_default="0")
    columns_count = Column(Integer, server_default="0")  # Number of custom columns in this iteration

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    steps = relationship("ProcessingStep", back_populates="iteration", cascade="all, delete-orphan")

    __table_args__ = (
        Index("uq_pi_project_number", "project_id", "iteration_number", unique=True),
    )


class ProcessingStep(Base):
    """A single processing step in the pipeline.

    Types:
    - ai: GPT classification with prompt. Output = text classification result.
    - regex: Pattern matching on company data. Output = matched value or boolean.
    - scrape: Website scraping for additional pages. Output = scraped text.
    - filter: Remove companies matching condition. No output column.

    Each step produces a named output_column stored in
    DiscoveredCompany.source_data.custom_columns[output_column].
    """
    __tablename__ = "processing_steps"

    id = Column(Integer, primary_key=True, index=True)
    iteration_id = Column(Integer, ForeignKey("pipeline_iterations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    step_number = Column(Integer, nullable=False)  # Execution order
    name = Column(String(255), nullable=False)  # Human-readable name
    output_column = Column(String(100), nullable=True)  # Column name in results (null for filter steps)

    step_type = Column(String(30), nullable=False)  # ai, regex, scrape, filter
    is_essential = Column(Boolean, server_default="false")  # Essential steps cannot be removed

    # Config depends on step_type:
    # ai: {prompt: "...", model: "gpt-4o-mini"}
    # regex: {pattern: "...", input_field: "domain|name|industry|...", output_format: "match|boolean|group"}
    # scrape: {page_paths: ["/about", "/team"], max_pages: 3}
    # filter: {condition: "column_name != 'OTHER'", source_column: "..."}
    config = Column(JSONB, nullable=False)

    is_active = Column(Boolean, server_default="true")  # Soft delete — keeps history
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    iteration = relationship("PipelineIteration", back_populates="steps")

    __table_args__ = (
        Index("ix_ps_iteration_order", "iteration_id", "step_number"),
        Index("ix_ps_project_active", "project_id", "is_active"),
    )
