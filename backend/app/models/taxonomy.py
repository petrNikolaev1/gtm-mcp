"""Apollo taxonomy — keywords + industries stored in DB with vector embeddings."""
from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.db import Base


class ApolloTaxonomy(Base):
    __tablename__ = "apollo_taxonomy"

    id = Column(Integer, primary_key=True)
    term = Column(Text, nullable=False)
    term_type = Column(String(20), nullable=False)  # 'keyword' or 'industry'
    seen_count = Column(Integer, server_default="1")
    source = Column(String(50), nullable=True)  # 'seed', 'enrichment', 'search', 'bulk_enrich'
    last_segment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    # embedding column added via raw SQL (pgvector type not in SQLAlchemy)

    __table_args__ = (
        UniqueConstraint("term", "term_type", name="uq_taxonomy_term_type"),
        Index("ix_taxonomy_type", "term_type"),
    )
