"""Pipeline models — DiscoveredCompany + ExtractedContact."""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class DiscoveredCompany(Base):
    """Company discovered during gathering — deduplicated by domain per project."""
    __tablename__ = "discovered_companies"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    domain = Column(String(255), nullable=False)
    name = Column(String(500), nullable=True)
    industry = Column(String(255), nullable=True)
    employee_count = Column(Integer, nullable=True)
    employee_range = Column(String(50), nullable=True)
    country = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    linkedin_url = Column(String(500), nullable=True)
    website_url = Column(String(500), nullable=True)

    # Streaming pipeline state
    status = Column(String(30), nullable=True)  # new, scraped, scrape_failed, target, rejected, classify_failed
    scraped_text = Column(Text, nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=True)

    # Pipeline state
    is_blacklisted = Column(Boolean, server_default="false")
    blacklist_reason = Column(String(255), nullable=True)
    is_pre_filtered = Column(Boolean, server_default="false")
    pre_filter_reason = Column(String(255), nullable=True)

    # Analysis state (latest)
    is_target = Column(Boolean, nullable=True)
    analysis_confidence = Column(Float, nullable=True)
    analysis_segment = Column(String(100), nullable=True)
    analysis_reasoning = Column(Text, nullable=True)

    # Enrichment state
    is_enriched = Column(Boolean, server_default="false")
    enrichment_source = Column(String(50), nullable=True)

    # Raw data from source
    source_data = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_dc_project_domain", "project_id", "domain", unique=True),
        Index("ix_dc_target", "project_id", "is_target"),
        Index("ix_dc_blacklist", "project_id", "is_blacklisted"),
    )


class ExtractedContact(Base):
    """Person extracted from a discovered company."""
    __tablename__ = "extracted_contacts"

    id = Column(Integer, primary_key=True, index=True)
    discovered_company_id = Column(Integer, ForeignKey("discovered_companies.id", ondelete="CASCADE"), nullable=True, index=True)  # NULL for imported contacts
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    job_title = Column(String(255), nullable=True)
    linkedin_url = Column(String(500), nullable=True)
    phone = Column(String(50), nullable=True)

    email_verified = Column(Boolean, nullable=True)
    email_source = Column(String(50), nullable=True)  # source of email verification

    source_data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_ec_company", "discovered_company_id"),
        Index("ix_ec_email", "email"),
    )
