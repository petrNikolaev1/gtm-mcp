"""API token authentication — bcrypt hash lookup."""
import secrets
import bcrypt
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.user import MCPUser, MCPApiToken

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "mcp_"


def generate_api_token() -> tuple[str, str, str]:
    """Generate a new API token. Returns (raw_token, token_prefix, token_hash)."""
    raw = TOKEN_PREFIX + secrets.token_hex(32)
    prefix = raw[:12]  # "mcp_" + first 8 hex chars
    hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()
    return raw, prefix, hashed


async def verify_token(session: AsyncSession, raw_token: str) -> Optional[MCPUser]:
    """Verify an API token and return the associated user."""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None

    prefix = raw_token[:12]

    # Find candidate tokens by prefix (fast lookup)
    result = await session.execute(
        select(MCPApiToken).where(
            MCPApiToken.token_prefix == prefix,
            MCPApiToken.is_active == True,
        )
    )
    candidates = result.scalars().all()

    for token_record in candidates:
        if bcrypt.checkpw(raw_token.encode(), token_record.token_hash.encode()):
            # Single query: load user + update token atomically
            user_result = await session.execute(
                select(MCPUser).where(
                    MCPUser.id == token_record.user_id,
                    MCPUser.is_active == True,
                )
            )
            user = user_result.scalar_one_or_none()
            if user:
                # Update last_used_at only if user is valid
                await session.execute(
                    update(MCPApiToken)
                    .where(MCPApiToken.id == token_record.id)
                    .values(last_used_at=datetime.utcnow())
                )
            return user

    return None
