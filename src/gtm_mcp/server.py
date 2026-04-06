"""GTM-MCP Server — 43 thin tools, zero LLM calls. stdio transport via FastMCP."""
from pathlib import Path

from fastmcp import FastMCP

from gtm_mcp.config import ConfigManager
from gtm_mcp.workspace import WorkspaceManager

mcp = FastMCP(
    name="gtm-mcp",
    instructions=(
        "GTM-MCP: B2B lead generation tools. "
        "Use /leadgen for full pipeline, /qualify for batch classification, "
        "/outreach for sequence generation, /replies for reply triage. "
        "All AI reasoning is done by YOU (the calling agent) using the skills in .claude/skills/. "
        "These tools only handle data access — API calls, storage, blacklist."
    ),
)

_config = ConfigManager()
_workspace = WorkspaceManager(_config.dir)


# ─── Config Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def get_config() -> dict:
    """Get current configuration. API keys show true/false (not values). Non-secret fields returned as values."""
    cfg = _config.all()
    # Non-secret fields — safe to return actual values
    safe_values = {"user_email", "google_shared_drive_id"}
    return {
        "success": True,
        "configured": {k: bool(v) for k, v in cfg.items()},
        "values": {k: v for k, v in cfg.items() if k in safe_values and v},
        "workspace": str(_config.dir),
    }


@mcp.tool()
async def set_config(key: str, value: str) -> dict:
    """Set a configuration value (e.g. apollo_api_key)."""
    _config.set(key, value)
    return {"success": True, "key": key}


# ─── Project Tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def create_project(name: str, data: dict | None = None) -> dict:
    """Create a new project in workspace."""
    project_data = data or {}
    project_data["name"] = name
    path = _workspace.save(name, "project.yaml", project_data)
    return {"success": True, "project": name, "path": str(path)}


@mcp.tool()
async def list_projects() -> dict:
    """List all projects in workspace."""
    return {"success": True, "projects": _workspace.list_projects()}


@mcp.tool()
async def save_data(project: str, name: str, data: dict | list, mode: str = "write") -> dict:
    """Save data to project workspace. Modes: write, merge, append, versioned."""
    path = _workspace.save(project, name, data, mode=mode)
    return {"success": True, "path": str(path)}


@mcp.tool()
async def load_data(project: str, name: str) -> dict:
    """Load data from project workspace."""
    data = _workspace.load(project, name)
    if data is None:
        return {"success": False, "error": f"File {name} not found in project {project}"}
    return {"success": True, "data": data}


@mcp.tool()
async def find_campaign(campaign_ref: str) -> dict:
    """Find a campaign by SmartLead ID or slug across all projects.

    Use when the user provides a campaign= parameter and you need to
    resolve which project it belongs to.
    """
    result = _workspace.find_campaign(campaign_ref)
    if result is None:
        return {"success": False, "error": f"Campaign '{campaign_ref}' not found in any project"}
    return {"success": True, "data": result}


@mcp.tool()
async def get_project_costs(project: str) -> dict:
    """Get cost breakdown for a project — totals, per-campaign, and per-run.

    Scans all run files and aggregates: credits (search + people), USD,
    companies gathered, contacts extracted. Grouped by campaign.
    """
    return {"success": True, "data": _workspace.get_project_costs(project)}


@mcp.tool()
async def normalize_company_name(name: str) -> dict:
    """Normalize company name — strips Inc/LLC/Ltd/Corp/GmbH and trailing punctuation.

    Use before storing in run file or pushing to SmartLead.
    smartlead_add_leads already auto-normalizes, but use this for run file entities.
    """
    return {"success": True, "original": name,
            "normalized": _workspace.normalize_company_name(name)}


# ─── Google Sheets Tools ─────────────────────────────────────────────────────

@mcp.tool()
async def sheets_create(title: str, share_with: str = "") -> dict:
    """Create a Google Sheet on Shared Drive with standard contact headers.

    Returns sheet_id and sheet_url. Optionally shares with an email (editor).
    Requires GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHARED_DRIVE_ID in .env.
    """
    from gtm_mcp.tools.sheets import sheets_create as _impl
    return await _impl(title, share_with, config=_config)


@mcp.tool()
async def sheets_export_contacts(
    project: str, campaign_slug: str = "", sheet_id: str = "",
) -> dict:
    """Export project contacts to a Google Sheet.

    If sheet_id provided → appends to existing sheet.
    If not → creates new sheet, returns URL.
    If campaign_slug → filters contacts by that campaign's segment.
    """
    from gtm_mcp.tools.sheets import sheets_export_contacts as _impl
    return await _impl(project, campaign_slug, sheet_id,
                       config=_config, workspace=_workspace)


@mcp.tool()
async def sheets_read(sheet_id: str, tab: str = "Sheet1") -> dict:
    """Read all data from a Google Sheet tab as list of dicts.

    Use for: importing blacklist domains from a sheet, reading company lists,
    or any structured data the user has in Google Sheets.
    """
    from gtm_mcp.tools.sheets import sheets_read as _impl
    return await _impl(sheet_id, tab, config=_config)


# ─── Blacklist Tools ──────────────────────────────────────────────────────────

@mcp.tool()
async def blacklist_check(domain: str, max_age_days: int | None = None) -> dict:
    """Check if a domain is blacklisted.

    If max_age_days is set, only considers entries contacted within that window.
    Example: max_age_days=90 means "only blacklisted if contacted in last 3 months".
    """
    return {"success": True, "domain": domain,
            "blacklisted": _workspace.blacklist_check(domain, max_age_days)}


@mcp.tool()
async def blacklist_add(
    domains: list[str], source: str = "", campaign_name: str = "",
    last_contact_date: str = "",
) -> dict:
    """Add domains to the global blacklist with temporal metadata.

    source: where these came from (e.g. "smartlead_campaign", "manual")
    campaign_name: which campaign contacted them (e.g. "ES Global Q1")
    last_contact_date: ISO date of last contact (for time-windowed filtering)
    """
    _workspace.blacklist_add(domains, source=source, campaign_name=campaign_name,
                             last_contact_date=last_contact_date)
    return {"success": True, "added": len(domains)}


@mcp.tool()
async def blacklist_import(file_path: str, source: str = "") -> dict:
    """Import domains from a file into the blacklist."""
    count = _workspace.blacklist_import(file_path, source=source)
    return {"success": True, "imported": count}


# ─── Apollo Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def apollo_search_companies(filters: dict, page: int = 1, per_page: int = 100) -> dict:
    """Search Apollo for companies. 1 credit/page. Rate limited (300ms + 429 retry).

    CRITICAL: Pass EITHER q_organization_keyword_tags OR organization_industry_tag_ids.
    NEVER both — Apollo ANDs them, kills results. Use separate parallel calls.
    Locations, employee_ranges, funding_stages CAN be combined with either.
    """
    api_key = _config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "Apollo API key not configured. Run set_config."}
    from gtm_mcp.tools.apollo import apollo_search_companies as _impl
    return await _impl(api_key, filters, page, per_page)


@mcp.tool()
async def apollo_search_people(
    domain: str,
    person_seniorities: list[str] | None = None,
    per_page: int = 25,
    enrich: bool = False,
    max_enrich: int = 3,
) -> dict:
    """Search Apollo for people at a company. FREE — no credits (search only).

    Returns candidates with person IDs for enrichment. Filters has_email=true.
    Default seniorities: owner, founder, c_suite, vp, head, director.

    If enrich=True: auto-enriches top max_enrich candidates in one call.
    Saves a round trip vs calling apollo_enrich_people separately.
    Credits: 1 per verified email (only charged when enrich=True).
    """
    api_key = _config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "Apollo API key not configured."}
    from gtm_mcp.tools.apollo import apollo_search_people as _impl
    return await _impl(api_key, domain, person_seniorities, per_page, enrich, max_enrich)


@mcp.tool()
async def apollo_search_people_batch(
    domains: list[str],
    person_seniorities: list[str] | None = None,
    per_page: int = 10,
    enrich: bool = False,
    max_enrich: int = 3,
) -> dict:
    """Search people across many domains in parallel. FREE — no credits for search.

    20 concurrent. Pass all target domains at once — one tool call instead of N.
    If enrich=True, also enriches top candidates per domain (1 credit each).
    """
    api_key = _config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "Apollo API key not configured."}
    from gtm_mcp.tools.apollo import apollo_search_people_batch as _impl
    return await _impl(api_key, domains, person_seniorities, per_page, enrich, max_enrich)


@mcp.tool()
async def apollo_enrich_people(person_ids: list[str]) -> dict:
    """Enrich people by Apollo person IDs. 1 credit per verified email.

    Returns ONLY verified emails. Also returns org data (industry_tag_id)
    which auto-extends the industry taxonomy.
    """
    api_key = _config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "Apollo API key not configured."}
    from gtm_mcp.tools.apollo import apollo_enrich_people as _impl
    return await _impl(api_key, person_ids)


@mcp.tool()
async def apollo_enrich_companies(domains: list[str]) -> dict:
    """Bulk enrich companies by domain. Max 10 per call, 1 credit per company.

    Returns full company data including industry_tag_id.
    Auto-extends industry taxonomy with discovered tag_ids.
    """
    api_key = _config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "Apollo API key not configured."}
    from gtm_mcp.tools.apollo import apollo_enrich_companies as _impl
    return await _impl(api_key, domains)


@mcp.tool()
async def apollo_get_taxonomy() -> dict:
    """Get Apollo industry taxonomy with hex tag_ids + employee ranges.

    Returns 84 industry name → tag_id mappings from production data.
    For organization_industry_tag_ids filter, use the hex tag_ids, NOT name strings.
    Keywords are free-text — generate any with LLM, no validation needed.
    """
    from gtm_mcp.tools.apollo import apollo_get_taxonomy as _impl
    return _impl()


@mcp.tool()
async def apollo_estimate_cost(
    target_count: int = 100,
    contacts_per_company: int = 3,
    target_rate: float = 0.35,
) -> dict:
    """Estimate Apollo credits needed for a pipeline run. No API call."""
    from gtm_mcp.tools.apollo import apollo_estimate_cost as _impl
    return _impl(target_count, contacts_per_company, target_rate)


# ─── Scraping Tool ────────────────────────────────────────────────────────────

@mcp.tool()
async def scrape_website(url: str) -> dict:
    """Scrape a website and return cleaned text. No credits.

    3-layer fallback: Apify proxy → direct fetch → HTTP fallback.
    Retries 429/5xx with exponential backoff.
    """
    proxy = _config.get("apify_proxy_password")
    from gtm_mcp.tools.scraping import scrape_website as _impl
    return await _impl(url, apify_proxy_password=proxy)


@mcp.tool()
async def scrape_batch(urls: list[str], max_concurrent: int = 50) -> dict:
    """Scrape many URLs in parallel with concurrency pool. No credits.

    50 concurrent by default (Apify residential proxy).
    MUCH faster than calling scrape_website one by one.
    Use this for batch scraping in the pipeline — pass all domains at once.
    """
    proxy = _config.get("apify_proxy_password")
    from gtm_mcp.tools.scraping import scrape_batch as _impl
    return await _impl(urls, apify_proxy_password=proxy, max_concurrent=max_concurrent)


# ─── Pipeline Tools ──────────────────────────────────────────────────────────

@mcp.tool()
async def campaign_push(
    project: str,
    campaign_name: str,
    sending_account_ids: list[int],
    country: str,
    segment: str,
    sequence_steps: list[dict],
    leads_file: str,
    test_email: str = "",
    run_id: str = "",
) -> dict:
    """Atomic SmartLead campaign setup — ONE tool call does everything.

    Creates campaign (DRAFT) → sets sequence → uploads ALL leads from file → sends test email.
    Also updates campaign.yaml (run_ids, total_leads_pushed) and run file (campaign data).
    100% deterministic. Zero LLM needed.

    leads_file: path to JSON file with leads array (relative to project or absolute).
    run_id: links campaign to run file (updates run.campaign + campaign.run_ids).
    """
    from gtm_mcp.tools.campaign_push import campaign_push as _impl
    return await _impl(
        project, campaign_name, sending_account_ids, country, segment,
        sequence_steps, leads_file, test_email, run_id=run_id,
        config=_config, workspace=_workspace,
    )



@mcp.tool()
async def pipeline_compute_leaderboard(project: str, run_id: str) -> dict:
    """Compute keyword + industry leaderboard from run data. Zero LLM.

    For each keyword/industry: unique companies, targets, target rate, quality_score.
    Saves to run file's keyword_leaderboard. Used by Mode 3 for seeding.
    """
    from gtm_mcp.tools.pipeline import pipeline_compute_leaderboard as _impl
    return await _impl(project, run_id, workspace=_workspace)


@mcp.tool()
async def pipeline_save_contacts(
    project: str, run_id: str, contacts: list[dict],
    search_credits: int = 0, people_credits: int = 0,
) -> dict:
    """Deterministic save: contacts to BOTH contacts.json AND run file.

    Updates run totals (credits, kpi_met). One call, no LLM needed.
    Fixes bug where contacts were in contacts.json but missing from run file.
    """
    from gtm_mcp.tools.pipeline import pipeline_save_contacts as _impl
    return await _impl(project, run_id, contacts, search_credits, people_credits,
                       workspace=_workspace)


@mcp.tool()
async def pipeline_gather_and_scrape(
    project: str,
    run_id: str,
    keywords: list[str],
    industry_tag_ids: list[str],
    locations: list[str],
    employee_ranges: list[str],
    funding_stages: list[str] | None = None,
    max_companies: int = 400,
    scrape_concurrent: int = 100,
    max_pages_per_stream: int = 5,
) -> dict:
    """Atomic gather + scrape pipeline — ONE tool call, full streaming inside.

    project + run_id REQUIRED — auto-saves companies + requests + round data to run file.
    Fires all Apollo searches in parallel (1 keyword/industry per request).
    As domains arrive from Apollo, immediately queues them for scraping (100 concurrent Apify).
    Returns scraped_texts dict for classification agent prompts.

    Typical: 300-400 companies gathered + scraped in 30-90 seconds.
    """
    from gtm_mcp.tools.pipeline import pipeline_gather_and_scrape as _impl
    return await _impl(
        keywords, industry_tag_ids, locations, employee_ranges,
        funding_stages=funding_stages,
        project=project, run_id=run_id,  # REQUIRED — auto-saves to run file
        max_companies=max_companies,
        scrape_concurrent=scrape_concurrent,
        max_pages_per_stream=max_pages_per_stream,
        config=_config, workspace=_workspace,
    )


# ─── SmartLead Tools ──────────────────────────────────────────────────────────

@mcp.tool()
async def smartlead_list_campaigns() -> dict:
    """List all SmartLead campaigns."""
    from gtm_mcp.tools.smartlead import smartlead_list_campaigns as _impl
    return await _impl(config=_config)


@mcp.tool()
async def smartlead_create_campaign(
    project: str, name: str, sending_account_ids: list[int],
    country: str = "US", segment: str = "",
) -> dict:
    """Create a SmartLead campaign with schedule, settings, and email accounts.

    Chains 5 API calls: create → schedule (timezone from country, 09-18 Mon-Fri) →
    settings (plain text, no tracking, stop on reply, 40% follow-up, AI ESP matching) →
    assign accounts → save locally.
    Campaign is always DRAFT — use smartlead_activate_campaign to start sending.
    """
    from gtm_mcp.tools.smartlead import smartlead_create_campaign as _impl
    return await _impl(project, name, sending_account_ids, country,
                       segment=segment, config=_config, workspace=_workspace)


@mcp.tool()
async def smartlead_set_sequence(
    project: str, campaign_slug: str, campaign_id: int, steps: list[dict],
) -> dict:
    """Set email sequence steps for a SmartLead campaign.

    Saves sequence.yaml locally first, then pushes to SmartLead.
    Each step: {step, day, subject, body, subject_b?}
    """
    from gtm_mcp.tools.smartlead import smartlead_set_sequence as _impl
    return await _impl(project, campaign_slug, campaign_id, steps,
                       config=_config, workspace=_workspace)


@mcp.tool()
async def smartlead_add_leads(campaign_id: int, leads: list[dict]) -> dict:
    """Add leads to a SmartLead campaign.

    Each lead: {email, first_name, last_name, company_name, custom_fields?}
    custom_fields is a dict: {"segment": "PAYMENTS", "city": "Miami"}
    """
    from gtm_mcp.tools.smartlead import smartlead_add_leads as _impl
    return await _impl(campaign_id, leads, config=_config)


@mcp.tool()
async def smartlead_list_accounts() -> dict:
    """Load ALL SmartLead email accounts, cache locally, return SUMMARY.

    With 2000+ accounts, the full list is too large for tool results.
    This caches all accounts to ~/.gtm-mcp/email_accounts.json and returns:
    total count, unique domains, top 20 domains by account count.
    Use smartlead_search_accounts(query) to filter by name/email/domain.
    """
    from gtm_mcp.tools.smartlead import smartlead_list_accounts as _impl
    return await _impl(config=_config, workspace=_workspace)


@mcp.tool()
async def smartlead_search_accounts(query: str) -> dict:
    """Search cached email accounts by name, email, or domain.

    Call smartlead_list_accounts() first to populate the cache.
    Example queries: "sally", "danila", "renat@", "getsally.io"
    Returns matching accounts with IDs ready for campaign creation.
    """
    from gtm_mcp.tools.smartlead import smartlead_search_accounts as _impl
    return await _impl(query, config=_config, workspace=_workspace)


@mcp.tool()
async def smartlead_sync_replies(
    project: str, campaign_slug: str, campaign_id: int,
) -> dict:
    """Sync replied leads from a SmartLead campaign. Saves replies.json to workspace."""
    from gtm_mcp.tools.smartlead import smartlead_sync_replies as _impl
    return await _impl(project, campaign_slug, campaign_id,
                       config=_config, workspace=_workspace)


@mcp.tool()
async def smartlead_get_lead_messages(campaign_id: int, lead_id: int) -> dict:
    """Fetch full message thread for a lead — sent emails + received replies.

    Used by Tier 2 reply classification. Returns messages in chronological order
    plus extracted latest_reply text for regex/LLM classification.
    """
    from gtm_mcp.tools.smartlead import smartlead_get_lead_messages as _impl
    return await _impl(campaign_id, lead_id, config=_config)


@mcp.tool()
async def smartlead_send_reply(campaign_id: int, lead_id: int, body: str) -> dict:
    """Send a reply to a lead in SmartLead."""
    from gtm_mcp.tools.smartlead import smartlead_send_reply as _impl
    return await _impl(campaign_id, lead_id, body, config=_config)


@mcp.tool()
async def smartlead_activate_campaign(campaign_id: int, confirm: str) -> dict:
    """Activate a SmartLead campaign. confirm must be exactly 'I confirm'.

    This starts REAL email sending — use with care.
    """
    from gtm_mcp.tools.smartlead import smartlead_activate_campaign as _impl
    return await _impl(campaign_id, confirm, config=_config)


@mcp.tool()
async def smartlead_pause_campaign(campaign_id: int, confirm: str) -> dict:
    """Pause an active SmartLead campaign. confirm must be exactly 'I confirm'.

    Pauses all email sending. Use smartlead_activate_campaign to resume.
    """
    from gtm_mcp.tools.smartlead import smartlead_pause_campaign as _impl
    return await _impl(campaign_id, confirm, config=_config)


@mcp.tool()
async def smartlead_send_test_email(
    campaign_id: int, test_email: str, sequence_number: int = 1,
) -> dict:
    """Send a test email from a campaign to verify the sequence.

    Requires at least one lead and one email account on the campaign.
    Auto-resolves the sending account and lead for variable substitution.
    """
    from gtm_mcp.tools.smartlead import smartlead_send_test_email as _impl
    return await _impl(campaign_id, test_email, sequence_number, config=_config)


@mcp.tool()
async def smartlead_get_campaign(campaign_id: int) -> dict:
    """Get campaign details by ID — name, status, assigned accounts, sequences.

    Use to validate an existing campaign before appending new leads.
    """
    from gtm_mcp.tools.smartlead import smartlead_get_campaign as _impl
    return await _impl(campaign_id, config=_config)


@mcp.tool()
async def smartlead_export_leads(campaign_id: int) -> dict:
    """Export all leads from a SmartLead campaign.

    Returns every lead with email, name, company, domain.
    Use for dedup when appending new contacts to an existing campaign.
    """
    from gtm_mcp.tools.smartlead import smartlead_export_leads as _impl
    return await _impl(campaign_id, config=_config)


# ─── GetSales Tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def getsales_list_profiles() -> dict:
    """List GetSales LinkedIn profiles."""
    api_key = _config.get("getsales_api_key")
    team_id = _config.get("getsales_team_id")
    if not api_key or not team_id:
        return {"success": False, "error": "GetSales API key or team_id not configured."}
    from gtm_mcp.tools.getsales import getsales_list_profiles as _impl
    return await _impl(api_key, team_id)


@mcp.tool()
async def getsales_build_flow(
    name: str, connection_note: str, messages: list[str],
    flow_type: str = "standard",
) -> dict:
    """Build and create a GetSales LinkedIn flow from messages — no manual node construction.

    Builds the full "God Level" node tree automatically:
      Trigger → connection_request → accept/reject branches → messages → withdraw

    flow_type: standard, networking, product, volume, event (controls timing).
    connection_note: text for the LinkedIn connection request.
    messages: list of message texts (2-3 messages recommended).
    """
    api_key = _config.get("getsales_api_key")
    team_id = _config.get("getsales_team_id")
    if not api_key or not team_id:
        return {"success": False, "error": "GetSales not configured."}
    from gtm_mcp.tools.getsales import build_node_tree, FLOW_TYPE_TIMING, getsales_create_flow as _impl
    timing = FLOW_TYPE_TIMING.get(flow_type, FLOW_TYPE_TIMING["standard"])
    nodes = build_node_tree(connection_note, messages, timing)
    result = await _impl(api_key, team_id, name, nodes)
    if result.get("success"):
        result["flow_type"] = flow_type
        result["node_count"] = len(nodes)
        result["messages_count"] = len(messages)
    return result


@mcp.tool()
async def getsales_create_flow(name: str, nodes: list[dict]) -> dict:
    """Create a GetSales LinkedIn outreach flow from raw nodes. Prefer getsales_build_flow instead."""
    api_key = _config.get("getsales_api_key")
    team_id = _config.get("getsales_team_id")
    if not api_key or not team_id:
        return {"success": False, "error": "GetSales not configured."}
    from gtm_mcp.tools.getsales import getsales_create_flow as _impl
    return await _impl(api_key, team_id, name, nodes)


@mcp.tool()
async def getsales_add_leads(flow_id: int, leads: list[dict]) -> dict:
    """Add leads to a GetSales flow."""
    api_key = _config.get("getsales_api_key")
    team_id = _config.get("getsales_team_id")
    if not api_key or not team_id:
        return {"success": False, "error": "GetSales not configured."}
    from gtm_mcp.tools.getsales import getsales_add_leads as _impl
    return await _impl(api_key, team_id, flow_id, leads)


@mcp.tool()
async def getsales_activate_flow(flow_id: int, confirm: str) -> dict:
    """Activate a GetSales flow. Must pass confirm='I confirm'."""
    api_key = _config.get("getsales_api_key")
    team_id = _config.get("getsales_team_id")
    if not api_key or not team_id:
        return {"success": False, "error": "GetSales not configured."}
    from gtm_mcp.tools.getsales import getsales_activate_flow as _impl
    return await _impl(api_key, team_id, flow_id, confirm)


# ─── MCP Prompts ─────────────────────────────────────────────────────────────

from gtm_mcp.prompts import (
    lead_generation, classify_companies, classify_replies,
    generate_email_sequence, analyze_offer,
)

mcp.prompt()(lead_generation)
mcp.prompt()(classify_companies)
mcp.prompt()(classify_replies)
mcp.prompt()(generate_email_sequence)
mcp.prompt()(analyze_offer)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
