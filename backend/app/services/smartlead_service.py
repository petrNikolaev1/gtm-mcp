"""SmartLead Service — full campaign lifecycle for MCP."""
import logging
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

# Country → IANA timezone mapping (business hours 9-18)
COUNTRY_TIMEZONES = {
    "united states": "America/New_York", "us": "America/New_York",
    "united kingdom": "Europe/London", "uk": "Europe/London",
    "germany": "Europe/Berlin", "austria": "Europe/Vienna", "switzerland": "Europe/Zurich",
    "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam", "belgium": "Europe/Brussels",
    "india": "Asia/Kolkata", "australia": "Australia/Sydney",
    "united arab emirates": "Asia/Dubai", "uae": "Asia/Dubai",
    "south africa": "Africa/Johannesburg", "nigeria": "Africa/Lagos",
    "brazil": "America/Sao_Paulo", "mexico": "America/Mexico_City",
    "canada": "America/Toronto", "japan": "Asia/Tokyo",
    "singapore": "Asia/Singapore", "philippines": "Asia/Manila",
    "russia": "Europe/Moscow", "israel": "Asia/Jerusalem",
    "turkey": "Europe/Istanbul", "saudi arabia": "Asia/Riyadh",
    "qatar": "Asia/Qatar", "kuwait": "Asia/Kuwait",
    "poland": "Europe/Warsaw", "czech republic": "Europe/Prague",
    "romania": "Europe/Bucharest", "ukraine": "Europe/Kyiv",
}


def get_timezone_for_country(country: str) -> str:
    """Get IANA timezone for a country. Defaults to UTC."""
    if not country:
        return "UTC"
    return COUNTRY_TIMEZONES.get(country.lower().strip(), "UTC")


class SmartLeadService:
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self.base_url = "https://server.smartlead.ai/api/v1"

    @property
    def api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        from app.config import settings
        return settings.SMARTLEAD_API_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _api_call(self, method: str, endpoint: str, json_data: dict = None, params: dict = None) -> Optional[dict]:
        try:
            p = params or {}
            p["api_key"] = self.api_key
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "POST":
                    resp = await client.post(f"{self.base_url}{endpoint}", json=json_data, params=p)
                elif method == "PATCH":
                    resp = await client.patch(f"{self.base_url}{endpoint}", json=json_data, params=p)
                else:
                    resp = await client.get(f"{self.base_url}{endpoint}", params=p)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"SmartLead {method} {endpoint}: {e}")
            return None

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        data = await self._api_call("GET", "/campaigns")
        return data is not None

    async def get_campaigns(self) -> List[Dict[str, Any]]:
        data = await self._api_call("GET", "/campaigns")
        return data if isinstance(data, list) else []

    # ── Campaign Creation ──

    async def create_campaign(self, name: str) -> Optional[Dict[str, Any]]:
        """Create a DRAFT campaign. Retries once on 'Plan expired' intermittent error."""
        import asyncio
        result = await self._api_call("POST", "/campaigns/create", {"name": name})
        if result is None:
            # Retry once — SmartLead sometimes returns "Plan expired!" intermittently
            await asyncio.sleep(2)
            result = await self._api_call("POST", "/campaigns/create", {"name": name})
        return result

    async def set_campaign_sequences(self, campaign_id: int, sequences: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Set email sequence steps with A/B variant support.

        Each step can have:
        - subject/body: variant A (default)
        - subject_b/body_b: variant B (A/B test)

        SmartLead splits traffic 50/50 between variants automatically.
        """
        formatted = []
        for i, step in enumerate(sequences):
            entry = {
                "seq_number": step.get("step", i + 1),
                "seq_delay_details": {"delay_in_days": step.get("day", i * 3)},
                "subject": step.get("subject", ""),
                "email_body": step.get("body", ""),
            }

            formatted.append(entry)

        # Push sequences first (without variants — SmartLead doesn't accept inline variants)
        result = await self._api_call("POST", f"/campaigns/{campaign_id}/sequences", {"sequences": formatted})

        # Then add A/B variants via separate API call for steps that have subject_b
        for i, step in enumerate(sequences):
            if step.get("subject_b") or step.get("body_b"):
                seq_id = None
                # Fetch sequence IDs to add variant
                seqs_data = await self.get_campaign_sequences(campaign_id)
                if seqs_data:
                    for s in seqs_data:
                        if s.get("seq_number") == step.get("step", i + 1):
                            seq_id = s.get("id")
                            break
                if seq_id:
                    await self._api_call("POST", f"/campaigns/{campaign_id}/sequences/{seq_id}/variants", {
                        "variant_label": "B",
                        "subject": step.get("subject_b", step.get("subject", "")),
                        "email_body": step.get("body_b", step.get("body", "")),
                    })

        return result

    async def set_campaign_schedule(self, campaign_id: int, timezone: str, start_hour: str = "09:00", end_hour: str = "18:00") -> Optional[Dict[str, Any]]:
        """Set campaign sending schedule — 9-6 business hours in target timezone."""
        return await self._api_call("POST", f"/campaigns/{campaign_id}/schedule", {
            "timezone": timezone,
            "days_of_the_week": [1, 2, 3, 4, 5],  # Mon-Fri only
            "start_hour": start_hour,
            "end_hour": end_hour,
            "min_time_btw_emails": 3,
            "max_new_leads_per_day": 1500,
        })

    async def set_campaign_settings(self, campaign_id: int,
                                     track_open: bool = False, stop_on_reply: bool = True,
                                     plain_text: bool = True, follow_up_pct: int = 40) -> Optional[Dict[str, Any]]:
        """Set campaign delivery settings. Defaults from reference campaign 3070919.
        All settings configurable from document extraction or user override."""
        return await self._api_call("POST", f"/campaigns/{campaign_id}/settings", {
            "track_settings": [] if not track_open else ["TRACK_EMAIL_OPEN", "TRACK_LINK_CLICK"],
            "stop_lead_settings": "REPLY_TO_AN_EMAIL" if stop_on_reply else "DONT_REPLY_TO_AN_EMAIL",
            "send_as_plain_text": plain_text,
            "follow_up_percentage": follow_up_pct,
            "enable_ai_esp_matching": True,
        })

    async def set_campaign_email_accounts(self, campaign_id: int, account_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Assign email sending accounts to campaign."""
        return await self._api_call("POST", f"/campaigns/{campaign_id}/email-accounts", {
            "email_account_ids": account_ids,
        })

    async def get_email_accounts(self) -> List[Dict[str, Any]]:
        """Get all email accounts (paginated — SmartLead returns max 100 per call)."""
        all_accounts = []
        offset = 0
        while True:
            data = await self._api_call("GET", "/email-accounts", params={"offset": offset, "limit": 100})
            if not isinstance(data, list) or not data:
                break
            all_accounts.extend(data)
            if len(data) < 100:
                break
            offset += 100
        return all_accounts

    async def get_campaign_email_accounts(self, campaign_id: int) -> List[Dict[str, Any]]:
        """Get email accounts assigned to a specific campaign."""
        data = await self._api_call("GET", f"/campaigns/{campaign_id}/email-accounts")
        return data if isinstance(data, list) else []

    async def get_lead_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get lead data by email (global search). Returns numeric lead ID."""
        data = await self._api_call("GET", "/leads/", params={"email": email})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data.get("id"):
            return data
        return None

    async def get_campaign_statistics(self, campaign_id: int) -> List[Dict[str, Any]]:
        """Get campaign statistics — includes reply_time for each lead."""
        data = await self._api_call("GET", f"/campaigns/{campaign_id}/statistics")
        if isinstance(data, dict):
            return data.get("data", [])
        return data if isinstance(data, list) else []

    # ── Leads ──

    async def export_campaign_leads(self, campaign_id: int) -> List[Dict[str, Any]]:
        """Export ALL leads from a campaign as CSV."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    f"{self.base_url}/campaigns/{campaign_id}/leads-export",
                    params={"api_key": self.api_key},
                )
                if resp.status_code != 200:
                    return []
                import csv, io
                text = resp.text
                if not text.strip():
                    return []
                reader = csv.DictReader(io.StringIO(text))
                leads = []
                for row in reader:
                    email = row.get("email", "").strip()
                    if not email:
                        continue
                    domain = email.split("@")[1] if "@" in email else ""
                    leads.append({
                        "email": email,
                        "first_name": row.get("first_name", ""),
                        "last_name": row.get("last_name", ""),
                        "company_name": row.get("company_name", ""),
                        "domain": domain,
                    })
                return leads
        except Exception as e:
            logger.error(f"SmartLead export {campaign_id} failed: {e}")
            return []

    async def get_campaign_sequences(self, campaign_id: int) -> Optional[List[Dict[str, Any]]]:
        data = await self._api_call("GET", f"/campaigns/{campaign_id}/sequences")
        return data if isinstance(data, list) else None

    async def get_campaign_leads_with_status(self, campaign_id: int, status: str = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get leads from a campaign, optionally filtered by status."""
        params = {"limit": limit, "offset": offset}
        if status:
            params["lead_status"] = status
        data = await self._api_call("GET", f"/campaigns/{campaign_id}/leads", params=params)
        if not data:
            return []
        return data if isinstance(data, list) else data.get("data", data.get("leads", []))

    async def get_lead_message_history(self, campaign_id: int, lead_id_or_email: str) -> Any:
        """Get full message thread for one lead. Pass numeric lead ID or email."""
        data = await self._api_call("GET", f"/campaigns/{campaign_id}/leads/{lead_id_or_email}/message-history")
        # Returns {"history": [...]} or list directly
        if isinstance(data, dict):
            return data  # caller handles .get("history", [])
        return data if isinstance(data, list) else []

    async def add_leads_to_campaign(self, campaign_id: int, leads: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Add leads to a SmartLead campaign.

        Each lead dict: {email, first_name, last_name, company_name, custom_fields?}
        """
        formatted = []
        for lead in leads:
            entry = {
                "email": lead["email"],
                "first_name": lead.get("first_name", "Test"),
                "last_name": lead.get("last_name", "Lead"),
                "company_name": lead.get("company_name", "TEST - DELETE"),
            }
            if lead.get("custom_fields"):
                entry["custom_fields"] = lead["custom_fields"]
            formatted.append(entry)

        return await self._api_call("POST", f"/campaigns/{campaign_id}/leads", {"lead_list": formatted})

    async def update_campaign_status(self, campaign_id: int, status: str = "START") -> Optional[Dict[str, Any]]:
        """Start/pause/stop a campaign. Status: START, PAUSE, STOP.

        SAFETY: Campaign activation (START) requires explicit user approval.
        MCP creates campaigns as DRAFT only. This method exists for when
        the user explicitly asks to activate after reviewing the sequence.
        """
        logger.info(f"Campaign {campaign_id} status change: {status} (requires user approval for START)")
        return await self._api_call("POST", f"/campaigns/{campaign_id}/status", {"status": status})

    async def send_test_email(
        self,
        campaign_id: int,
        test_email: str = "test@example.com",
        sequence_number: int = 1,
        email_account_id: Optional[int] = None,
        lead_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send a test email via SmartLead's native API.

        Required params: leadId (for variable substitution), sequenceNumber,
        selectedEmailAccountId (which sending account), customEmailAddress (recipient).

        If email_account_id not provided, auto-picks the first account on the campaign.
        If lead_id not provided, auto-picks the first lead in the campaign.
        """
        # Auto-resolve email account if not provided
        if not email_account_id:
            accounts = await self.get_campaign_email_accounts(campaign_id)
            if accounts:
                email_account_id = accounts[0].get("id")
            if not email_account_id:
                return {"ok": False, "error": "No email accounts on this campaign"}

        # Auto-resolve lead if not provided
        if not lead_id:
            leads = await self.get_campaign_leads_with_status(campaign_id, limit=1)
            if leads:
                lead_obj = leads[0].get("lead", leads[0])
                lead_id = lead_obj.get("id")
            if not lead_id:
                return {"ok": False, "error": "No leads in this campaign for variable substitution"}

        result = await self._api_call("POST", f"/campaigns/{campaign_id}/send-test-email", {
            "leadId": lead_id,
            "sequenceNumber": sequence_number,
            "selectedEmailAccountId": email_account_id,
            "customEmailAddress": test_email,
        })

        if result and result.get("status") == "success":
            return {"ok": True, "message_id": result.get("messageId"), "test_email": test_email,
                    "from_account_id": email_account_id, "lead_id": lead_id}
        return {"ok": False, "error": str(result), "test_email": test_email}
