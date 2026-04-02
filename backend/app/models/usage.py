"""Usage logging for MCP actions + full conversation tracking."""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class MCPUsageLog(Base):
    __tablename__ = "mcp_usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(100), nullable=False)
    tool_name = Column(String(100), nullable=True)
    extra_data = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MCPConversationLog(Base):
    """Stores every MCP protocol message — user prompts, tool calls, tool results, assistant responses.

    This is the full conversation log: what the user typed to Claude,
    what tools Claude called, what results came back, what Claude replied.
    """
    __tablename__ = "mcp_conversation_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("mcp_users.id", ondelete="CASCADE"), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)

    # message = full JSON-RPC message, direction = client→server or server→client
    direction = Column(String(20), nullable=False)  # "client_to_server" or "server_to_client"
    method = Column(String(100), nullable=True)  # JSON-RPC method: tools/call, tools/list, etc.
    message_type = Column(String(50), nullable=True)  # "request", "response", "notification"

    # Content — store the full raw message + extracted readable parts
    raw_json = Column(JSONB, nullable=True)  # Full JSON-RPC message
    content_summary = Column(Text, nullable=True)  # Human-readable summary (tool name, args preview, result preview)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_mcl_user_session", "user_id", "session_id"),
        Index("ix_mcl_created", "created_at"),
    )
