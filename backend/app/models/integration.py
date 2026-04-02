"""Integration settings — encrypted API key storage per user."""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class MCPIntegrationSetting(Base):
    __tablename__ = "mcp_integration_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False)
    integration_name = Column(String(50), nullable=False)  # smartlead, apollo, openai, getsales, apify
    api_key_encrypted = Column(Text, nullable=False)        # AES-256-GCM encrypted
    is_connected = Column(Boolean, server_default="false")
    connection_info = Column(Text, nullable=True)            # e.g. "47 campaigns found"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("MCPUser", back_populates="integrations")

    __table_args__ = (
        Index("uq_user_integration", "user_id", "integration_name", unique=True),
    )
