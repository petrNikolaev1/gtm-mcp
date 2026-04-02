"""MCP Reply models — fully independent from main backend.

Stores all reply data in MCP's own database.
No proxy to main backend needed.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class MCPReply(Base):
    """Processed reply — classified and optionally drafted."""
    __tablename__ = "mcp_replies"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)

    # Lead info
    lead_email = Column(String(255), nullable=True, index=True)
    lead_name = Column(String(255), nullable=True)
    lead_company = Column(String(255), nullable=True)

    # Campaign info
    campaign_name = Column(String(500), nullable=True, index=True)
    campaign_external_id = Column(String(100), nullable=True)
    source = Column(String(50), server_default="smartlead")  # smartlead, getsales
    channel = Column(String(50), server_default="email")  # email, linkedin

    # Reply content
    email_subject = Column(String(500), nullable=True)
    reply_text = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)

    # Classification (GPT-4o-mini)
    category = Column(String(50), nullable=True, index=True)  # interested, meeting_request, not_interested, out_of_office, wrong_person, unsubscribe, question, other
    category_confidence = Column(String(20), nullable=True)  # high, medium, low
    classification_reasoning = Column(Text, nullable=True)

    # Draft reply (Gemini 2.5 Pro)
    draft_reply = Column(Text, nullable=True)
    draft_subject = Column(String(500), nullable=True)
    draft_generated_at = Column(DateTime(timezone=True), nullable=True)

    # Operator actions
    approval_status = Column(String(50), nullable=True)  # pending, approved, dismissed
    needs_reply = Column(Boolean, server_default="true")

    # Tracking
    tracking_enabled = Column(Boolean, server_default="true")
    smartlead_lead_id = Column(String(100), nullable=True)
    message_hash = Column(String(32), nullable=True, index=True)  # MD5 for dedup

    # Telegram notification
    telegram_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Raw data
    raw_webhook_data = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_mcp_reply_project_category", "project_id", "category"),
        Index("ix_mcp_reply_campaign", "campaign_name"),
        Index("ix_mcp_reply_needs_reply", "project_id", "needs_reply"),
        Index("uq_mcp_reply_dedup", "lead_email", "campaign_external_id", "message_hash", unique=True),
    )
