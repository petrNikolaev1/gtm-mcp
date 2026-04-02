"""MCP System configuration — simplified from main backend."""
from pydantic_settings import BaseSettings
from typing import Optional
import os


class MCPSettings(BaseSettings):
    # App
    APP_NAME: str = "MCP LeadGen"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    MCP_MODE: bool = True

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://mcp:changeme@mcp-postgres:5432/mcp_leadgen"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    # Redis
    REDIS_URL: str = "redis://mcp-redis:6379/0"

    # AI — shared from .env (read-only, fallback for users without own keys)
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    # Apollo defaults
    APOLLO_API_KEY: Optional[str] = None
    APOLLO_API_URL: str = "https://api.apollo.io/api/v1"

    # SmartLead defaults
    SMARTLEAD_API_KEY: Optional[str] = None

    # GetSales defaults
    GETSALES_API_KEY: Optional[str] = None
    GETSALES_TEAM_ID: Optional[str] = None

    # Apify proxy (shared)
    APIFY_PROXY_HOST: str = "proxy.apify.com"
    APIFY_PROXY_PORT: int = 8000
    APIFY_PROXY_PASSWORD: Optional[str] = None

    # Encryption key for stored API keys (auto-generated if not set)
    ENCRYPTION_KEY: Optional[str] = None

    # UI base URL (used for links in tool responses)
    UI_BASE: str = "https://gtm-mcp.com"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    class Config:
        env_file = ".env"
        extra = "allow"


settings = MCPSettings()
