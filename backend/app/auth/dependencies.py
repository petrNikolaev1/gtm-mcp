"""FastAPI dependencies for auth."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import MCPUser
from app.auth.middleware import verify_token


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> MCPUser:
    """Extract and verify API token from request headers."""
    # Check X-MCP-Token header first, then Authorization: Bearer
    token = request.headers.get("X-MCP-Token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Missing API token")

    user = await verify_token(session, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired API token")

    return user


async def get_optional_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> MCPUser | None:
    """Like get_current_user but returns None instead of 401."""
    token = request.headers.get("X-MCP-Token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    return await verify_token(session, token)
