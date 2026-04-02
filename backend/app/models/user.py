"""MCP User + API Token models."""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class MCPUser(Base):
    __tablename__ = "mcp_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)  # bcrypt hash, nullable for legacy accounts
    is_active = Column(Boolean, server_default="true")
    active_project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL", use_alter=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tokens = relationship("MCPApiToken", back_populates="user", cascade="all, delete-orphan")
    integrations = relationship("MCPIntegrationSetting", back_populates="user", cascade="all, delete-orphan")


class MCPApiToken(Base):
    __tablename__ = "mcp_api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_prefix = Column(String(12), nullable=False)  # first 8 chars for display
    token_hash = Column(String(255), nullable=False)    # bcrypt hash
    name = Column(String(255), server_default="default")
    is_active = Column(Boolean, server_default="true")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("MCPUser", back_populates="tokens")

    __table_args__ = (
        Index("ix_token_prefix", "token_prefix"),
        Index("ix_token_active", "is_active", "token_prefix"),
    )
