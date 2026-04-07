"""SmartLead API tools — campaign CRUD, sequences, leads, replies, activation.

Auth: api_key as QUERY PARAMETER ?api_key=xxx (not header).
Base URL: https://server.smartlead.ai/api/v1
Known bug: create sometimes returns "Plan expired!" — retry once with 2s delay.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone as tz
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://server.smartlead.ai/api/v1"

COUNTRY_TIMEZONES: dict[str, str] = {
    "US": "America/New_York", "UK": "Europe/London", "DE": "Europe/Berlin",
    "FR": "Europe/Paris", "AU": "Australia/Sydney", "IN": "Asia/Kolkata",
    "SG": "Asia/Singapore", "JP": "Asia/Tokyo", "BR": "America/Sao_Paulo",
    "CA": "America/Toronto", "NL": "Europe/Amsterdam", "SE": "Europe/Stockholm",
    "IL": "Asia/Jerusalem", "AE": "Asia/Dubai", "HK": "Asia/Hong_Kong",
    "KR": "Asia/Seoul", "MX": "America/Mexico_City", "AR": "America/Buenos_Aires",
    "ZA": "Africa/Johannesburg", "PL": "Europe/Warsaw", "IT": "Europe/Rome",
    "ES": "Europe/Madrid", "CH": "Europe/Zurich",
    # Extended from magnum-opus reference
    "AT": "Europe/Vienna", "BE": "Europe/Brussels", "NG": "Africa/Lagos",
    "PH": "Asia/Manila", "RU": "Europe/Moscow", "TR": "Europe/Istanbul",
    "SA": "Asia/Riyadh", "QA": "Asia/Qatar", "KW": "Asia/Kuwait",
    "CZ": "Europe/Prague", "RO": "Europe/Bucharest", "UA": "Europe/Kyiv",
}


def _get_log_path() -> Path:
    """Get SmartLead API log file path — persists across sessions."""
    log_dir = Path.home() / ".gtm-mcp"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "smartlead_api.log"


def _log_to_file(direction: str, method: str, endpoint: str, detail: str = ""):
    """Append SmartLead API call to persistent log file."""
    from datetime import datetime, timezone as tz
    ts = datetime.now(tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {direction} {method} {endpoint}{detail}\n"
    try:
        with open(_get_log_path(), "a") as f:
            f.write(line)
    except Exception:
        pass


async def _api_call(method: str, endpoint: str, api_key: str, *,
                    json_data: dict | None = None, params: dict | None = None) -> Any:
    p = dict(params or {})
    p["api_key"] = api_key
    url = f"{BASE_URL}{endpoint}"

    # Log request (truncate large payloads for readability)
    payload_summary = ""
    if json_data:
        ids = json_data.get("email_account_ids")
        if ids and len(ids) > 5:
            payload_summary = f" payload={{email_account_ids: [{len(ids)} ids, first={ids[:3]}...]}}"
        else:
            payload_summary = f" payload={str(json_data)[:200]}"
    logger.info("SmartLead → %s %s%s", method, endpoint, payload_summary)
    _log_to_file("→", method, endpoint, payload_summary)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "POST":
                resp = await client.post(url, json=json_data, params=p)
            elif method == "PATCH":
                resp = await client.patch(url, json=json_data, params=p)
            elif method == "DELETE":
                resp = await client.request("DELETE", url, json=json_data, params=p)
            else:
                resp = await client.get(url, params=p)

            # Log response
            body = resp.text[:300] if resp.text else ""
            if resp.status_code >= 400:
                logger.error("SmartLead ← %s %s → HTTP %s: %s", method, endpoint, resp.status_code, body)
                _log_to_file("←", method, endpoint, f" HTTP {resp.status_code}: {body[:200]}")
            else:
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        logger.info("SmartLead ← %s %s → %s (%d items)", method, endpoint, resp.status_code, len(data))
                        _log_to_file("←", method, endpoint, f" {resp.status_code} ({len(data)} items)")
                    else:
                        logger.info("SmartLead ← %s %s → %s", method, endpoint, resp.status_code)
                        _log_to_file("←", method, endpoint, f" {resp.status_code}")
                except Exception:
                    logger.info("SmartLead ← %s %s → %s", method, endpoint, resp.status_code)
                    _log_to_file("←", method, endpoint, f" {resp.status_code}")

            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("SmartLead %s %s → %s: %s", method, endpoint,
                      exc.response.status_code, exc.response.text[:300])
        _log_to_file("ERROR", method, endpoint, f" HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return None
    except Exception as exc:
        logger.error("SmartLead %s %s failed: %s", method, endpoint, exc)
        _log_to_file("ERROR", method, endpoint, f" {str(exc)[:200]}")
        return None


# ---------------------------------------------------------------------------
# List campaigns
# ---------------------------------------------------------------------------

async def smartlead_list_campaigns(*, config=None) -> dict:
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    data = await _api_call("GET", "/campaigns", api_key)
    if data is None:
        return {"success": False, "error": "SmartLead API request failed"}
    campaigns = data if isinstance(data, list) else []
    return {"success": True, "data": campaigns}


# ---------------------------------------------------------------------------
# Get campaign by ID — validate existing campaign for reuse
# ---------------------------------------------------------------------------

async def smartlead_get_campaign(campaign_id: int, *, config=None) -> dict:
    """Get campaign details by ID. Used to validate campaign exists for append mode."""
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    data = await _api_call("GET", f"/campaigns/{campaign_id}", api_key)
    if data is None:
        return {"success": False, "error": f"Campaign {campaign_id} not found or API failed"}

    # Also fetch assigned email accounts
    accounts = await _api_call("GET", f"/campaigns/{campaign_id}/email-accounts", api_key)
    account_ids = []
    if accounts and isinstance(accounts, list):
        account_ids = [a.get("id") for a in accounts if a.get("id")]

    # Fetch sequences
    sequences = await _api_call("GET", f"/campaigns/{campaign_id}/sequences", api_key)

    campaign = {
        "campaign_id": campaign_id,
        "name": data.get("name", ""),
        "status": data.get("status", ""),
        "created_at": data.get("created_at", ""),
        "sending_account_ids": account_ids,
        "sequences": sequences if isinstance(sequences, list) else [],
    }
    return {"success": True, "data": campaign}


# ---------------------------------------------------------------------------
# Export leads from campaign — for dedup on append
# ---------------------------------------------------------------------------

async def smartlead_export_leads(campaign_id: int, *, config=None) -> dict:
    """Export all leads from a campaign as structured data.

    Returns email + domain for every lead. Used to dedup when
    appending new contacts to an existing campaign.
    """
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{BASE_URL}/campaigns/{campaign_id}/leads-export",
                params={"api_key": api_key},
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Export failed: HTTP {resp.status_code}"}

            import csv
            import io
            text = resp.text
            if not text.strip():
                return {"success": True, "data": {"campaign_id": campaign_id,
                        "leads": [], "count": 0, "domains": []}}

            reader = csv.DictReader(io.StringIO(text))
            leads = []
            domains = set()
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
                if domain:
                    domains.add(domain)

            return {"success": True, "data": {
                "campaign_id": campaign_id,
                "leads": leads,
                "count": len(leads),
                "domains": sorted(domains),
            }}
    except Exception as exc:
        logger.error("SmartLead export %s failed: %s", campaign_id, exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# List email accounts — caches locally, returns summary only
# ---------------------------------------------------------------------------

_ACCOUNTS_CACHE_FILE = "email_accounts.json"


async def smartlead_list_accounts(*, config=None, workspace=None) -> dict:
    """Load ALL SmartLead email accounts, cache locally, return SUMMARY only.

    With 2000+ accounts, the full list overflows MCP tool result limits.
    This caches to ~/.gtm-mcp/email_accounts.json and returns:
    - total count
    - unique domains with account counts
    - cache timestamp
    Use smartlead_search_accounts(query) to filter by name/email/domain.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    all_accounts: list[dict] = []
    offset = 0
    while True:
        data = await _api_call("GET", "/email-accounts", api_key,
                               params={"offset": offset, "limit": 100})
        if data is None:
            break
        entries = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        if not entries:
            break
        all_accounts.extend(entries)
        if len(entries) < 100:
            break  # last page
        offset += 100

    accounts = [{
        "id": a.get("id"),
        "from_email": a.get("from_email", ""),
        "from_name": a.get("from_name", ""),
        "warmup_status": a.get("warmup_status", ""),
    } for a in all_accounts]

    # Cache full list locally
    import json
    cache_path = workspace.base / _ACCOUNTS_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "cached_at": datetime.now(tz.utc).isoformat(),
        "accounts": accounts,
    }, indent=2, ensure_ascii=False))

    # Return summary only (not 2000+ accounts)
    domains: dict[str, int] = {}
    for a in accounts:
        email = a.get("from_email", "")
        domain = email.split("@")[1] if "@" in email else "unknown"
        domains[domain] = domains.get(domain, 0) + 1

    return {"success": True, "data": {
        "total": len(accounts),
        "unique_domains": len(domains),
        "top_domains": dict(sorted(domains.items(), key=lambda x: -x[1])[:20]),
        "cached_at": datetime.now(tz.utc).isoformat(),
        "cache_path": str(cache_path),
    }}


# ---------------------------------------------------------------------------
# Search email accounts — filters from local cache
# ---------------------------------------------------------------------------

async def smartlead_search_accounts(query: str, project: str = "", *, config=None, workspace=None) -> dict:
    """Search cached email accounts by name, email, or domain substring.

    Call smartlead_list_accounts() first to populate the cache.
    If project provided, saves selected accounts to project dir (not global).
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    import json
    cache_path = workspace.base / _ACCOUNTS_CACHE_FILE
    if not cache_path.exists():
        return {"success": False, "error": "Account cache not found. Call smartlead_list_accounts() first."}

    cache = json.loads(cache_path.read_text())
    accounts = cache.get("accounts", [])

    # Filter by query (case-insensitive substring match on email + name)
    q = query.lower().strip()
    # Remove common filler words
    stop_words = {"all", "with", "in", "the", "my", "use", "accounts", "from", "of"}
    terms = [t for t in q.split() if t not in stop_words]

    matched = []
    for a in accounts:
        combined = f"{a.get('from_email', '')} {a.get('from_name', '')}".lower()
        if all(t in combined for t in terms):
            matched.append(a)

    # Group by domain for readability
    by_domain: dict[str, list] = {}
    for a in matched:
        email = a.get("from_email", "")
        domain = email.split("@")[1] if "@" in email else "unknown"
        by_domain.setdefault(domain, []).append(a)

    # Save matched accounts to project dir (or global if no project)
    if project:
        selected_path = workspace.base / "projects" / project / "selected_accounts.json"
        selected_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        selected_path = workspace.base / "selected_accounts.json"
    import json as _json
    selected_path.write_text(_json.dumps({
        "query": query, "count": len(matched),
        "by_domain": {d: len(accs) for d, accs in by_domain.items()},
        "accounts": [{"id": a["id"], "email": a.get("from_email",""), "name": a.get("from_name","")} for a in matched],
    }, indent=2))

    return {"success": True, "data": {
        "query": query,
        "matched": len(matched),
        "accounts": matched,
        "by_domain": {d: len(accs) for d, accs in by_domain.items()},
        "account_ids": [a["id"] for a in matched],
        "saved_to": str(selected_path),
    }, "message": f"{len(matched)} accounts saved → {selected_path}"}


# ---------------------------------------------------------------------------
# Create campaign (5-step chain)
# ---------------------------------------------------------------------------

async def smartlead_create_campaign(
    project: str, name: str, sending_account_ids: list[int],
    country: str = "US", segment: str = "", *, config=None, workspace=None,
) -> dict:
    """Create campaign with schedule, settings, and email accounts.

    5 API calls: create → schedule → settings → email accounts → save locally.
    segment: target segment label (e.g. "PAYMENTS") — stored in campaign.yaml for tracking.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    if not sending_account_ids:
        return {"success": False, "error": "sending_account_ids must not be empty"}

    timezone = COUNTRY_TIMEZONES.get(country.upper(), "UTC")

    # Step 1: create (retry once for "Plan expired!" bug)
    create_data = await _api_call("POST", "/campaigns/create", api_key,
                                   json_data={"name": name})
    if create_data is None or (isinstance(create_data, dict) and "Plan expired" in str(create_data)):
        await asyncio.sleep(2)
        create_data = await _api_call("POST", "/campaigns/create", api_key,
                                       json_data={"name": name})
    if create_data is None:
        return {"success": False, "error": "Failed to create campaign"}

    campaign_id = create_data.get("id") if isinstance(create_data, dict) else None
    if not campaign_id:
        return {"success": False, "error": f"No campaign ID: {create_data}"}

    # Step 2: schedule (Mon-Fri 9-18 target timezone)
    step2 = await _api_call("POST", f"/campaigns/{campaign_id}/schedule", api_key,
                    json_data={"timezone": timezone, "days_of_the_week": [1, 2, 3, 4, 5],
                               "start_hour": "09:00", "end_hour": "18:00",
                               "min_time_btw_emails": 3, "max_new_leads_per_day": 1500})
    if step2 is None:
        logger.error("Campaign %s: schedule setup failed", campaign_id)

    # Step 3: settings (plain text, no tracking, stop on reply, AI ESP matching)
    step3 = await _api_call("POST", f"/campaigns/{campaign_id}/settings", api_key,
                    json_data={"track_settings": [],
                               "stop_lead_settings": "REPLY_TO_AN_EMAIL",
                               "send_as_plain_text": True, "follow_up_percentage": 40,
                               "enable_ai_esp_matching": True})
    if step3 is None:
        logger.error("Campaign %s: settings setup failed", campaign_id)

    # Step 4: assign email accounts
    # SmartLead may auto-assign ALL connected accounts on campaign creation.
    # Pattern from magnum-opus: POST adds, DELETE /email-accounts/{id} removes.
    # Flow: POST desired → GET current → DELETE unwanted → verify.
    desired_ids = set(sending_account_ids)
    accounts_ok = False

    # 4a: POST desired accounts (same as magnum-opus/mcp set_campaign_email_accounts)
    step4 = await _api_call("POST", f"/campaigns/{campaign_id}/email-accounts", api_key,
                    json_data={"email_account_ids": sending_account_ids})
    if step4 is None:
        logger.error("Campaign %s: email account POST failed", campaign_id)

    # 4b: Verify — GET what's actually on the campaign
    current_accounts = await _api_call("GET", f"/campaigns/{campaign_id}/email-accounts", api_key)
    current_ids = set()
    if isinstance(current_accounts, list):
        current_ids = {a.get("id") for a in current_accounts if a.get("id")}

    # 4c: Remove unwanted accounts (if SmartLead auto-assigned extras)
    to_remove = current_ids - desired_ids
    if to_remove:
        logger.info("Campaign %s: found %d unwanted auto-assigned accounts, removing...",
                     campaign_id, len(to_remove))
        # Delete per-account (pattern from magnum-opus fix_campaign_sequence.py)
        sem = asyncio.Semaphore(10)  # 10 concurrent deletes
        async def _delete_one(acc_id: int):
            async with sem:
                return await _api_call("DELETE",
                    f"/campaigns/{campaign_id}/email-accounts/{acc_id}", api_key)
        results = await asyncio.gather(
            *[_delete_one(aid) for aid in to_remove], return_exceptions=True)
        removed = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        logger.info("Campaign %s: removed %d/%d unwanted accounts",
                     campaign_id, removed, len(to_remove))

    # 4d: Final verify
    if to_remove:  # only re-check if we had to clean up
        final_accounts = await _api_call("GET", f"/campaigns/{campaign_id}/email-accounts", api_key)
        if isinstance(final_accounts, list):
            final_ids = {a.get("id") for a in final_accounts if a.get("id")}
            extra = final_ids - desired_ids
            accounts_ok = len(extra) == 0
            if not accounts_ok:
                logger.error("Campaign %s: still %d extra accounts after cleanup", campaign_id, len(extra))
            else:
                logger.info("Campaign %s: verified %d accounts (cleaned up %d)", campaign_id, len(final_ids), len(to_remove))
        else:
            accounts_ok = False
    else:
        accounts_ok = desired_ids.issubset(current_ids)
        if accounts_ok:
            logger.info("Campaign %s: %d accounts assigned, no cleanup needed", campaign_id, len(current_ids))

    setup_warnings = []
    if step2 is None:
        setup_warnings.append("schedule")
    if step3 is None:
        setup_warnings.append("settings")
    if not accounts_ok:
        setup_warnings.append("email_accounts")

    # Step 5: save locally
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    campaign_data = {
        "campaign_id": campaign_id, "name": name, "slug": slug,
        "project": project, "segment": segment,
        "country": country.upper(), "timezone": timezone,
        "sending_account_ids": sending_account_ids, "status": "DRAFT",
        "run_ids": [], "total_leads_pushed": 0,
        "created_at": datetime.now(tz.utc).isoformat(),
    }
    workspace.save(project, f"campaigns/{slug}/campaign.yaml", campaign_data)

    result = {"success": True, "data": campaign_data}
    if setup_warnings:
        result["warnings"] = f"These setup steps failed (campaign created but incomplete): {', '.join(setup_warnings)}"
    return result


# ---------------------------------------------------------------------------
# Set sequence
# ---------------------------------------------------------------------------

async def smartlead_set_sequence(
    project: str, campaign_slug: str, campaign_id: int, steps: list[dict],
    *, config=None, workspace=None,
) -> dict:
    """Save sequence locally then push to SmartLead.

    Each step: {step, day, subject, body, subject_b?}
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    if not steps:
        return {"success": False, "error": "steps must not be empty"}

    # Save locally
    workspace.save(project, f"campaigns/{campaign_slug}/sequence.yaml",
                        {"campaign_id": campaign_id, "steps": steps})

    # Format for SmartLead API
    sl_sequences = []
    for i, step in enumerate(steps):
        body = step.get("body", "")
        # Auto-convert \n to <br> for SmartLead HTML rendering
        if body and "<br" not in body and "\n" in body:
            body = body.replace("\n", "<br>")
        seq = {
            "seq_number": step.get("step", i + 1),
            "seq_delay_details": {"delay_in_days": step.get("day", i * 3)},
            "subject": step.get("subject", ""),
            "email_body": body,
        }
        sl_sequences.append(seq)

    result = await _api_call("POST", f"/campaigns/{campaign_id}/sequences", api_key,
                             json_data={"sequences": sl_sequences})

    # A/B variants via separate API call (SmartLead doesn't accept inline variants)
    for i, step in enumerate(steps):
        if step.get("subject_b") or step.get("body_b"):
            # Fetch sequence IDs to find the right one
            seqs_data = await _api_call("GET", f"/campaigns/{campaign_id}/sequences", api_key)
            if seqs_data and isinstance(seqs_data, list):
                seq_id = None
                for s in seqs_data:
                    if s.get("seq_number") == step.get("step", i + 1):
                        seq_id = s.get("id")
                        break
                if seq_id:
                    variant_body = step.get("body_b", step.get("body", ""))
                    if variant_body and "<br" not in variant_body and "\n" in variant_body:
                        variant_body = variant_body.replace("\n", "<br>")
                    await _api_call("POST",
                                    f"/campaigns/{campaign_id}/sequences/{seq_id}/variants",
                                    api_key,
                                    json_data={
                                        "variant_label": "B",
                                        "subject": step.get("subject_b", step.get("subject", "")),
                                        "email_body": variant_body,
                                    })

    return {"success": True, "data": {"campaign_id": campaign_id, "steps_count": len(steps)}}


# ---------------------------------------------------------------------------
# Add leads
# ---------------------------------------------------------------------------

async def smartlead_add_leads(campaign_id: int, leads: list[dict], *, config=None) -> dict:
    """Add leads to a campaign. Each lead: {email, first_name, last_name, company_name, custom_fields?}

    Company names are auto-normalized (strips Inc/LLC/Ltd/Corp/GmbH).
    """
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    from gtm_mcp.workspace import WorkspaceManager

    # Format leads for SmartLead with normalized company names
    lead_list = []
    for lead in leads:
        entry = {
            "email": lead["email"],
            "first_name": lead.get("first_name", ""),
            "last_name": lead.get("last_name", ""),
            "company_name": WorkspaceManager.normalize_company_name(lead.get("company_name", "")),
        }
        if lead.get("custom_fields"):
            entry["custom_fields"] = lead["custom_fields"]
        lead_list.append(entry)

    data = await _api_call("POST", f"/campaigns/{campaign_id}/leads", api_key,
                           json_data={"lead_list": lead_list})
    if data is None:
        return {"success": False, "error": f"Failed to add {len(lead_list)} leads to campaign {campaign_id}"}

    # Parse actual accepted count from SmartLead response
    uploaded = len(lead_list)  # fallback
    if isinstance(data, dict):
        # SmartLead may return upload_count, total_leads, or similar
        uploaded = data.get("upload_count", data.get("total_leads",
                   data.get("added", len(lead_list))))
    rejected = len(lead_list) - uploaded if uploaded < len(lead_list) else 0

    result = {"campaign_id": campaign_id, "leads_sent": len(lead_list), "leads_accepted": uploaded}
    if rejected > 0:
        result["leads_rejected"] = rejected
        logger.warning("SmartLead rejected %d of %d leads for campaign %d", rejected, len(lead_list), campaign_id)
    return {"success": True, "data": result}


# ---------------------------------------------------------------------------
# Sync replies
# ---------------------------------------------------------------------------

async def smartlead_sync_replies(
    project: str, campaign_slug: str, campaign_id: int,
    *, config=None, workspace=None,
) -> dict:
    """Sync replied leads from campaign. Saves replies.json to workspace."""
    config = config or _default_config()
    workspace = workspace or _default_workspace()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    data = await _api_call("GET", f"/campaigns/{campaign_id}/statistics", api_key)
    replied = [l for l in (data if isinstance(data, list) else [])
               if l.get("lead_status") == "REPLIED"]

    # Save locally
    workspace.save(project, f"campaigns/{campaign_slug}/replies.json",
                        replied, mode="write")

    return {"success": True, "data": {"campaign_id": campaign_id,
            "replied_count": len(replied), "leads": replied}}


# ---------------------------------------------------------------------------
# Get lead message history — full thread for Tier 2 reply classification
# ---------------------------------------------------------------------------

async def smartlead_get_lead_messages(
    campaign_id: int, lead_id: int, *, config=None,
) -> dict:
    """Fetch full message thread for a lead — sent emails + received replies.

    Used by Tier 2 reply classification to extract the LATEST reply text
    and re-check regex patterns on the full conversation thread.
    Returns messages in chronological order.
    """
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    data = await _api_call("GET", f"/campaigns/{campaign_id}/leads/{lead_id}/message-history",
                           api_key)
    if data is None:
        return {"success": False, "error": f"Failed to fetch messages for lead {lead_id}"}

    messages = []
    history = data if isinstance(data, list) else data.get("data", data.get("history", []))
    if isinstance(history, list):
        for msg in history:
            messages.append({
                "type": msg.get("type", ""),        # "SENT" or "RECEIVED"
                "subject": msg.get("subject", ""),
                "body": msg.get("body", msg.get("email_body", "")),
                "time": msg.get("time", msg.get("created_at", "")),
            })

    # Extract latest reply (last RECEIVED message)
    latest_reply = None
    for msg in reversed(messages):
        if msg.get("type", "").upper() in ("RECEIVED", "REPLY"):
            latest_reply = msg.get("body", "")
            break

    return {
        "success": True,
        "data": {
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "messages": messages,
            "message_count": len(messages),
            "latest_reply": latest_reply,
        },
    }


# ---------------------------------------------------------------------------
# Send reply
# ---------------------------------------------------------------------------

async def smartlead_send_reply(campaign_id: int, lead_id: int, body: str, *, config=None) -> dict:
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    await _api_call("POST", f"/campaigns/{campaign_id}/leads/{lead_id}/reply", api_key,
                    json_data={"body": body})
    return {"success": True}


# ---------------------------------------------------------------------------
# Activate campaign
# ---------------------------------------------------------------------------

async def smartlead_activate_campaign(campaign_id: int, confirm: str, *, config=None) -> dict:
    """Activate campaign. confirm must be exactly 'I confirm'."""
    if confirm != "I confirm":
        return {"success": False, "error": "Must pass confirm='I confirm' to activate"}
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    await _api_call("POST", f"/campaigns/{campaign_id}/status", api_key,
                    json_data={"status": "START"})
    return {"success": True, "data": {"campaign_id": campaign_id, "status": "ACTIVE"}}


# ---------------------------------------------------------------------------
# Pause campaign
# ---------------------------------------------------------------------------

async def smartlead_pause_campaign(campaign_id: int, confirm: str, *, config=None) -> dict:
    """Pause an active campaign. confirm must be exactly 'I confirm'."""
    if confirm != "I confirm":
        return {"success": False, "error": "Must pass confirm='I confirm' to pause"}
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}
    await _api_call("POST", f"/campaigns/{campaign_id}/status", api_key,
                    json_data={"status": "PAUSE"})
    return {"success": True, "data": {"campaign_id": campaign_id, "status": "PAUSED"}}


# ---------------------------------------------------------------------------
# Send test email
# ---------------------------------------------------------------------------

async def smartlead_send_test_email(
    campaign_id: int, test_email: str, sequence_number: int = 1,
    *, config=None,
) -> dict:
    """Send a test email from a campaign to verify sequence before activation.

    Requires at least one lead and one email account on the campaign.
    Auto-resolves account and lead if not provided.
    """
    config = config or _default_config()
    api_key = config.get("smartlead_api_key")
    if not api_key:
        return {"success": False, "error": "smartlead_api_key not configured"}

    # Auto-resolve email account (first assigned)
    accounts = await _api_call("GET", f"/campaigns/{campaign_id}/email-accounts", api_key)
    if not accounts or not isinstance(accounts, list):
        return {"success": False, "error": "No email accounts on this campaign"}
    email_account_id = accounts[0].get("id")

    # Auto-resolve lead (first in campaign, for variable substitution)
    leads = await _api_call("GET", f"/campaigns/{campaign_id}/leads", api_key,
                            params={"limit": 1, "offset": 0})
    lead_id = None
    if isinstance(leads, list) and leads:
        lead_obj = leads[0].get("lead", leads[0])
        lead_id = lead_obj.get("id")
    elif isinstance(leads, dict):
        entries = leads.get("data", leads.get("leads", []))
        if entries:
            lead_obj = entries[0].get("lead", entries[0])
            lead_id = lead_obj.get("id")
    if not lead_id:
        return {"success": False, "error": "No leads in campaign for variable substitution"}

    result = await _api_call("POST", f"/campaigns/{campaign_id}/send-test-email", api_key,
                             json_data={
                                 "leadId": lead_id,
                                 "sequenceNumber": sequence_number,
                                 "selectedEmailAccountId": email_account_id,
                                 "customEmailAddress": test_email,
                             })
    if result and isinstance(result, dict) and result.get("status") == "success":
        return {"success": True, "data": {
            "test_email": test_email, "from_account_id": email_account_id,
            "lead_id": lead_id, "sequence_number": sequence_number,
            "message_id": result.get("messageId"),
        }}
    return {"success": False, "error": str(result), "test_email": test_email}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()

def _default_workspace():
    from gtm_mcp.workspace import WorkspaceManager
    return WorkspaceManager(_default_config().dir)
