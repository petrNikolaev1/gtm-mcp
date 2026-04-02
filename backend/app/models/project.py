"""Simplified Project + Company models for MCP system."""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class Company(Base):
    """Tenant company — simplified for MCP."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    domain = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    projects = relationship("Project", back_populates="company")


class Project(Base):
    """Sales project with ICP definition."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    website = Column(String(512), nullable=True)
    target_segments = Column(Text, nullable=True)
    target_industries = Column(Text, nullable=True)

    # Sender identity for outreach
    sender_name = Column(String(255), nullable=True)
    sender_company = Column(String(255), nullable=True)
    sender_position = Column(String(255), nullable=True)

    # Offer alignment — user must approve before gathering
    offer_summary = Column(JSONB, nullable=True)  # {product, value_props, target_audience, raw_website_text}
    offer_approved = Column(Boolean, server_default="false")

    # Campaign management
    campaign_filters = Column(JSONB, server_default="[]")

    is_active = Column(Boolean, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company = relationship("Company", back_populates="projects")

    __table_args__ = (
        Index("ix_project_user", "user_id", "is_active"),
    )
