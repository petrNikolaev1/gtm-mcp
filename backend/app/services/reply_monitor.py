"""Reply Monitor — background polling for SmartLead campaign replies.

Reuses the same logic as main app's crm_scheduler:
- Polls SmartLead API every 3 minutes for new replies
- Classifies replies (warm/cold/OOO/etc.) via GPT-4o-mini
- Stores in mcp_replies table
- Sends Telegram notifications for warm replies

Started on app startup. Monitors campaigns with monitoring_enabled=True.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

POLL_INTERVAL = 180  # 3 minutes


class ReplyMonitor:
    """Background service that polls SmartLead for new replies."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._poll_count = 0

    def start(self):
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Reply monitor started (polling every 3 min)")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self):
        """Main loop — poll every 3 minutes."""
        await asyncio.sleep(30)  # Wait for app startup

        while self._running:
            try:
                await self._check_replies()
                self._poll_count += 1
            except Exception as e:
                logger.error(f"Reply poll error: {e}")

            await asyncio.sleep(POLL_INTERVAL)

    async def _check_replies(self):
        """Check all monitored campaigns for new replies."""
        from app.db import async_session_maker
        from app.models.campaign import Campaign
        from app.models.reply import MCPReply
        from app.services.smartlead_service import SmartLeadService
        from app.services.encryption import decrypt_value
        from app.models.integration import MCPIntegrationSetting
        from app.models.user import MCPUser
        from app.models.project import Project

        async with async_session_maker() as session:
            # Find all campaigns with monitoring enabled
            monitored = (await session.execute(
                select(Campaign).where(Campaign.monitoring_enabled == True, Campaign.external_id.isnot(None))
            )).scalars().all()

            if not monitored:
                return

            logger.info(f"Reply monitor: checking {len(monitored)} campaigns")

            # Get SmartLead API key (from any user who configured it)
            sl_key = None
            integrations = (await session.execute(
                select(MCPIntegrationSetting).where(
                    MCPIntegrationSetting.integration_name == "smartlead",
                    MCPIntegrationSetting.is_connected == True,
                )
            )).scalars().first()
            if integrations:
                try:
                    sl_key = decrypt_value(integrations.api_key_encrypted)
                except Exception:
                    pass

            if not sl_key:
                from app.config import settings
                sl_key = settings.SMARTLEAD_API_KEY

            if not sl_key:
                logger.debug("No SmartLead key — skipping reply check")
                return

            sl = SmartLeadService(api_key=sl_key)
            total_new = 0

            for campaign in monitored:
                try:
                    new_replies = await self._check_campaign_replies(
                        session, sl, campaign
                    )
                    total_new += new_replies
                except Exception as e:
                    logger.warning(f"Reply check for campaign {campaign.id} failed: {e}")

            if total_new > 0:
                logger.info(f"Reply monitor: {total_new} new replies found")
                await session.commit()

    async def _check_campaign_replies(
        self, session: AsyncSession, sl: 'SmartLeadService', campaign: 'Campaign'
    ) -> int:
        """Check one campaign for new replies."""
        from app.models.reply import MCPReply
        import httpx

        campaign_id = int(campaign.external_id)

        # Get campaign statistics to find replied leads
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"https://server.smartlead.ai/api/v1/campaigns/{campaign_id}/leads",
                    params={"api_key": sl.api_key, "limit": 100, "offset": 0},
                )
                if resp.status_code != 200:
                    return 0
                data = resp.json()
                leads = data.get("data", []) if isinstance(data, dict) else data
        except Exception as e:
            logger.debug(f"Failed to get leads for campaign {campaign_id}: {e}")
            return 0

        new_count = 0
        for lead_data in leads:
            lead = lead_data.get("lead", lead_data)
            email = lead.get("email", "")
            status = lead_data.get("status", "")

            # Only process leads that have replied
            if status not in ("REPLIED", "INTERESTED", "MEETING_BOOKED", "CLOSED"):
                continue

            # Check if already processed
            existing = (await session.execute(
                select(MCPReply).where(
                    MCPReply.lead_email == email,
                    MCPReply.campaign_id == campaign.id,
                )
            )).scalar_one_or_none()

            if existing:
                continue

            # Get the actual reply message
            reply_text = ""
            try:
                msg_resp = await httpx.AsyncClient(timeout=15).get(
                    f"https://server.smartlead.ai/api/v1/campaigns/{campaign_id}/leads/{lead.get('id')}/message-history",
                    params={"api_key": sl.api_key},
                )
                if msg_resp.status_code == 200:
                    messages = msg_resp.json()
                    # Find the latest inbound message
                    for msg in reversed(messages if isinstance(messages, list) else []):
                        if msg.get("type") == "REPLY" or msg.get("direction") == "inbound":
                            reply_text = msg.get("body", msg.get("text", ""))[:2000]
                            break
            except Exception:
                pass

            # Classify reply
            category = self._classify_reply(status, reply_text)

            # Store
            reply = MCPReply(
                project_id=campaign.project_id,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                lead_email=email,
                lead_name=f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                reply_text=reply_text[:2000] if reply_text else None,
                category=category,
                source="smartlead",
                smartlead_lead_id=lead.get("id"),
            )
            session.add(reply)
            new_count += 1

            # Telegram notification for warm replies
            if category in ("interested", "meeting_request", "question"):
                asyncio.create_task(self._notify_telegram(campaign, email, category, reply_text))

        return new_count

    def _classify_reply(self, status: str, text: str) -> str:
        """Quick rule-based classification (same as main app tier-1)."""
        text_lower = (text or "").lower()

        # OOO
        if any(w in text_lower for w in ["out of office", "vacation", "away from", "auto-reply", "automatic reply"]):
            return "ooo"

        # Unsubscribe
        if any(w in text_lower for w in ["unsubscribe", "remove me", "stop emailing", "opt out"]):
            return "not_interested"

        # Wrong person
        if any(w in text_lower for w in ["wrong person", "no longer", "left the company", "doesn't work here"]):
            return "wrong_person"

        # Meeting / interested based on SmartLead status
        if status == "MEETING_BOOKED":
            return "meeting_request"
        if status == "INTERESTED":
            return "interested"

        # Positive signals
        if any(w in text_lower for w in ["interested", "tell me more", "let's talk", "schedule", "calendar", "sounds good"]):
            return "interested"

        if any(w in text_lower for w in ["not interested", "no thanks", "no thank you", "pass"]):
            return "not_interested"

        return "other"

    async def _notify_telegram(self, campaign, email: str, category: str, text: str):
        """Send Telegram notification for warm reply."""
        try:
            import os
            bot_token = os.environ.get("TELEGRAM_NOTIFY_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
            if not bot_token:
                return

            import httpx

            # Try per-user Telegram chat_id from DB first, then fall back to env var
            chat_id = None
            if hasattr(campaign, 'project_id') and campaign.project_id:
                try:
                    from app.db import async_session_maker
                    from app.models.project import Project
                    from app.models.integration import MCPIntegrationSetting
                    from app.services.encryption import decrypt_value
                    async with async_session_maker() as db_session:
                        project = await db_session.get(Project, campaign.project_id)
                        if project and project.user_id:
                            tg_result = await db_session.execute(
                                select(MCPIntegrationSetting).where(
                                    MCPIntegrationSetting.user_id == project.user_id,
                                    MCPIntegrationSetting.integration_name == "telegram",
                                )
                            )
                            tg_setting = tg_result.scalar_one_or_none()
                            if tg_setting and tg_setting.api_key_encrypted:
                                chat_id = decrypt_value(tg_setting.api_key_encrypted)
                except Exception as e:
                    logger.debug(f"Failed to get per-user telegram chat_id: {e}")

            if not chat_id:
                chat_id = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID")
            if not chat_id:
                return

            msg = (
                f"New {category} reply!\n\n"
                f"Campaign: {campaign.name}\n"
                f"Lead: {email}\n"
                f"Category: {category}\n"
                + (f"Text: {text[:200]}..." if text else "")
                + f"\n\nView: {settings.UI_BASE}/tasks/replies"
            )

            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg},
                )
        except Exception as e:
            logger.debug(f"Telegram notification failed: {e}")


# Singleton
_monitor: Optional[ReplyMonitor] = None


def get_reply_monitor() -> ReplyMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ReplyMonitor()
    return _monitor


def start_reply_monitor():
    """Call from app startup (lifespan)."""
    monitor = get_reply_monitor()
    monitor.start()
