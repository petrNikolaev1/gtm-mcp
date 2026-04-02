"""Self-refinement engine models — THE KEY DIFFERENTIATOR."""
from sqlalchemy import Column, Integer, String, DateTime, Text, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class RefinementRun(Base):
    """Tracks a self-refinement cycle across multiple iterations."""
    __tablename__ = "refinement_runs"

    id = Column(Integer, primary_key=True, index=True)
    gathering_run_id = Column(Integer, ForeignKey("gathering_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(30), nullable=False, server_default="running")  # running, converged, stopped, failed
    target_accuracy = Column(Float, nullable=False, server_default="0.9")
    max_iterations = Column(Integer, nullable=False, server_default="8")
    current_iteration = Column(Integer, nullable=False, server_default="0")
    final_accuracy = Column(Float, nullable=True)

    # Cost tracking
    total_cost_usd = Column(Float, nullable=True, server_default="0")
    total_tokens = Column(Integer, nullable=True, server_default="0")

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_rr_gathering", "gathering_run_id"),
        Index("ix_rr_status", "status"),
    )


class RefinementIteration(Base):
    """Per-iteration results within a refinement run."""
    __tablename__ = "refinement_iterations"

    id = Column(Integer, primary_key=True, index=True)
    refinement_run_id = Column(Integer, ForeignKey("refinement_runs.id", ondelete="CASCADE"), nullable=False, index=True)

    iteration_number = Column(Integer, nullable=False)
    accuracy = Column(Float, nullable=False)

    # Verification results
    true_positives = Column(Integer, nullable=True)
    true_negatives = Column(Integer, nullable=True)
    false_positives = Column(Integer, nullable=True)
    false_negatives = Column(Integer, nullable=True)
    sample_size = Column(Integer, nullable=True)

    # Error patterns discovered
    false_positive_patterns = Column(JSONB, nullable=True)  # list of pattern strings
    false_negative_patterns = Column(JSONB, nullable=True)

    # Prompt changes
    prompt_id = Column(Integer, ForeignKey("gathering_prompts.id", ondelete="SET NULL"), nullable=True)
    prompt_adjustments = Column(Text, nullable=True)  # diff summary

    # Cost for this iteration
    cost_usd = Column(Float, nullable=True)
    tokens_used = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_ri_run_iter", "refinement_run_id", "iteration_number", unique=True),
    )
