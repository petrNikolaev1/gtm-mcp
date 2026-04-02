"""Integration setup API — connect SmartLead, Apollo, OpenAI, Apify, GetSales."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_session
from app.models.user import MCPUser
from app.models.integration import MCPIntegrationSetting
from app.auth.dependencies import get_current_user
from app.services.encryption import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

SUPPORTED_INTEGRATIONS = {"smartlead", "apollo", "openai", "apify", "getsales", "telegram"}


class IntegrationRequest(BaseModel):
    integration_name: str
    api_key: str


class IntegrationResponse(BaseModel):
    connected: bool
    message: str


@router.post("/integrations", response_model=IntegrationResponse)
async def configure_integration(
    req: IntegrationRequest,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if req.integration_name not in SUPPORTED_INTEGRATIONS:
        raise HTTPException(400, f"Unsupported integration: {req.integration_name}")
    # Always trim whitespace from user input
    req.api_key = req.api_key.strip()
    if not req.api_key or len(req.api_key) < 8:
        raise HTTPException(400, "API key too short (minimum 8 characters)")
    if len(req.api_key) > 4000:
        raise HTTPException(400, "API key too long (maximum 4000 characters)")

    # Test connection
    connected = False
    message = ""

    if req.integration_name == "smartlead":
        from app.services.smartlead_service import SmartLeadService
        svc = SmartLeadService(api_key=req.api_key)
        connected = await svc.test_connection()
        if connected:
            campaigns = await svc.get_campaigns()
            message = f"{len(campaigns)} campaigns found"
        else:
            message = "Connection failed — check API key"

    elif req.integration_name == "apollo":
        from app.services.apollo_service import ApolloService
        svc = ApolloService(api_key=req.api_key)
        connected = await svc.test_connection()
        message = "Connected" if connected else "Connection failed — check API key"

    elif req.integration_name == "apify":
        # Store + set as env var so scraper picks it up immediately
        import os
        os.environ["APIFY_PROXY_PASSWORD"] = req.api_key
        connected = True
        message = "Apify proxy password saved — website scraping will use residential proxy"

    elif req.integration_name == "getsales":
        # Just store — GetSales uses JWT token, no simple test
        connected = True
        message = "GetSales key saved"

    elif req.integration_name == "openai":
        # Just store — no easy test endpoint
        connected = True
        message = "OpenAI key saved"

    elif req.integration_name == "telegram":
        # Store telegram chat_id for reply notifications
        # api_key here is the chat_id (sent from Telegram bot on /start)
        connected = True
        message = f"Telegram connected (chat {req.api_key})"

    # Upsert integration setting
    existing = await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == user.id,
            MCPIntegrationSetting.integration_name == req.integration_name,
        )
    )
    setting = existing.scalar_one_or_none()

    encrypted = encrypt_value(req.api_key)

    if setting:
        setting.api_key_encrypted = encrypted
        setting.is_connected = connected
        setting.connection_info = message
    else:
        setting = MCPIntegrationSetting(
            user_id=user.id,
            integration_name=req.integration_name,
            api_key_encrypted=encrypted,
            is_connected=connected,
            connection_info=message,
        )
        session.add(setting)

    await session.commit()
    return IntegrationResponse(connected=connected, message=message)


class TelegramConnectRequest(BaseModel):
    mcp_token: str
    chat_id: str
    username: str = ""


@router.post("/telegram-connect")
async def telegram_connect(
    req: TelegramConnectRequest,
    session: AsyncSession = Depends(get_session),
):
    """Called by the Telegram bot when user sends /start with their MCP token.
    Stores chat_id so reply_monitor can send notifications."""
    from app.auth.middleware import verify_token
    user = await verify_token(session, req.mcp_token)
    if not user:
        raise HTTPException(401, "Invalid MCP token")

    # Upsert telegram integration
    existing = await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == user.id,
            MCPIntegrationSetting.integration_name == "telegram",
        )
    )
    setting = existing.scalar_one_or_none()

    # Store chat_id as the "api_key" (encrypted) — reply_monitor reads it
    encrypted = encrypt_value(req.chat_id)
    info = f"Connected (@{req.username})" if req.username else f"Connected (chat {req.chat_id})"

    if setting:
        setting.api_key_encrypted = encrypted
        setting.is_connected = True
        setting.connection_info = info
    else:
        setting = MCPIntegrationSetting(
            user_id=user.id,
            integration_name="telegram",
            api_key_encrypted=encrypted,
            is_connected=True,
            connection_info=info,
        )
        session.add(setting)

    await session.commit()
    return {"connected": True, "message": f"Telegram notifications enabled for {user.name}"}


@router.get("/integrations")
async def list_integrations(
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == user.id
        )
    )
    return [
        {
            "name": i.integration_name,
            "connected": i.is_connected,
            "info": i.connection_info,
        }
        for i in result.scalars().all()
    ]
