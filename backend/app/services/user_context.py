"""Per-user service context — injects user's API keys into service instances."""
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.integration import MCPIntegrationSetting
from app.services.encryption import decrypt_value

logger = logging.getLogger(__name__)


class UserServiceContext:
    """Creates service instances with user's own API keys."""

    def __init__(self, user_id: int, session: AsyncSession):
        self.user_id = user_id
        self.session = session
        self._keys: dict[str, str] = {}
        self._loaded = False

    async def _load_keys(self):
        if self._loaded:
            return
        result = await self.session.execute(
            select(MCPIntegrationSetting).where(
                MCPIntegrationSetting.user_id == self.user_id,
                MCPIntegrationSetting.is_connected == True,
            )
        )
        for setting in result.scalars().all():
            try:
                self._keys[setting.integration_name] = decrypt_value(setting.api_key_encrypted)
            except Exception as e:
                logger.error(f"Failed to decrypt {setting.integration_name} key for user {self.user_id}: {e}")
        self._loaded = True

    async def get_key(self, integration_name: str) -> Optional[str]:
        await self._load_keys()
        return self._keys.get(integration_name)

    async def get_apollo_service(self):
        from app.services.apollo_service import ApolloService
        key = await self.get_key("apollo")
        return ApolloService(api_key=key)

    async def get_smartlead_service(self):
        from app.services.smartlead_service import SmartLeadService
        key = await self.get_key("smartlead")
        return SmartLeadService(api_key=key)

    async def get_scraper_service(self):
        from app.services.scraper_service import ScraperService
        apify_key = await self.get_key("apify")
        return ScraperService(apify_proxy_password=apify_key)
