"""MCP Tool Dispatcher — routes tool calls to service methods."""
import logging
from typing import Any, Optional

from starlette.requests import Request
from sqlalchemy import select

from app.db import async_session_maker
from app.auth.middleware import verify_token
from app.models.user import MCPUser
from app.models.project import Project, Company
from app.models.gathering import GatheringRun, ApprovalGate
from app.models.campaign import GeneratedSequence, Campaign
from app.models.integration import MCPIntegrationSetting
from app.models.pipeline import DiscoveredCompany, ExtractedContact
from app.models.usage import MCPUsageLog, MCPConversationLog
from app.models.reply import MCPReply
from app.services.user_context import UserServiceContext
from app.config import settings

logger = logging.getLogger(__name__)


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration like '4m 32s' or '1h 12m'."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _build_strategy_message(filters, keywords, locations, sizes, total_available,
                            target_people, max_ppc, cost_est, people_defaults):
    """Build transparent strategy message showing primary + backlog filters."""
    strategy = filters.get("filter_strategy", "keywords_only")
    industries = filters.get("industries", [])
    tag_ids = filters.get("organization_industry_tag_ids")

    if strategy == "industry_first" and tag_ids:
        # Industry is specific — primary filter, keywords are backlog
        primary_label = "INDUSTRY (specific match — high target rate)"
        primary_detail = f"  Industries: {', '.join(industries)}"
        backlog_label = f"KEYWORDS (after industry pages exhausted) — {len(keywords)} keywords"
        backlog_detail = f"  Keywords: {', '.join(keywords)}"
        reasoning = (
            f"Apollo industry \"{industries[0]}\" directly matches your query — "
            f"most companies in this category ARE your targets (45-90% target rate). "
            f"When industry pages stop returning new targets (20 pages with 0 new companies), "
            f"the pipeline regenerates fresh keywords via GPT and retries (up to 5 attempts)."
        )
    else:
        # Keywords are primary — industry is too broad
        primary_label = f"KEYWORDS ({len(keywords)} keywords — industry too broad for your niche)"
        primary_detail = f"  Keywords: {', '.join(keywords)}"
        backlog_label = "INDUSTRY (broader, lower precision — for scale)"
        backlog_detail = f"  Industries: {', '.join(industries)}" if industries else "  (no matching industry)"
        reasoning = (
            f"Apollo industries ({', '.join(industries[:2])}) are too broad for \"{keywords[0] if keywords else '?'}\" — "
            f"they include many irrelevant company types. Specific keywords give better targeting (30-50% rate). "
            f"When 20 pages yield 0 new companies, GPT regenerates fresh keywords (up to 5 attempts). "
            f"Then industry filters kick in for maximum TAM coverage."
        )

    # People filters explanation
    people_section = ""
    if people_defaults:
        people_section = (
            f"\n\nPeople search strategy:\n"
            f"{people_defaults}\n"
            f"  How it works: Apollo searches for senior roles at each target company (FREE).\n"
            f"  Results ranked by seniority + role match. Top {max_ppc} get email enrichment (1 credit each).\n"
            f"  Roles derived from your offer — these are the decision makers who buy your product."
        )

    # Show ALL keywords
    all_keywords = keywords if keywords else []
    kw_display = ", ".join(all_keywords[:20])
    if len(all_keywords) > 20:
        kw_display += f" (+{len(all_keywords) - 20} more)"

    pages = cost_est.get("pages_needed", 10)
    msg = (
        f"Company search strategy:\n\n"
        f"  WHY: {reasoning}\n\n"
        f"  ▶ PRIMARY: {primary_label}\n"
        f"  {primary_detail}\n\n"
        f"  ⏸ BACKLOG (after primary exhausted): {backlog_label}\n"
        f"  Exhaustion: 20 empty pages → regenerate keywords via GPT (up to 5 attempts)\n\n"
        f"  Keywords ({len(all_keywords)}): {kw_display}\n\n"
        f"  Location: {', '.join(locations)}\n"
        f"  Size: {', '.join(sizes)}\n"
        f"  Available: {total_available:,} companies in Apollo\n\n"
        f"  KPIs: {target_people} target people, max {max_ppc}/company"
        f"{people_section}\n\n"
        f"Estimated cost:\n"
        f"  Company search: {pages} pages × $0.01 = ${pages * 0.01:.2f} ({pages} credits)\n"
        f"  Classification: ~$0.07 (GPT-4o-mini)\n"
        f"  People search: FREE (Apollo seniority filter)\n"
        f"  Email enrichment: ~{target_people} × $0.01 = ${target_people * 0.01:.2f}\n"
        f"  Total: ~${cost_est['total_cost_usd']:.2f}\n\n"
        f"Proceed?"
    )
    return msg


async def _get_user(token: Optional[str], session) -> MCPUser:
    if not token:
        raise ValueError("Authentication required. Pass your API token.")
    user = await verify_token(session, token)
    if not user:
        raise ValueError("Invalid API token")
    return user


async def dispatch_tool(tool_name: str, args: dict, token: Optional[str], request: Request) -> Any:
    """Route a tool call to the appropriate service method. Logs every call."""
    import time as _time
    start = _time.monotonic()

    async with async_session_maker() as session:
        try:
            result = await _dispatch(tool_name, args, token, session)
            latency = int((_time.monotonic() - start) * 1000)

            # Log usage + costs + conversation
            try:
                from app.auth.middleware import verify_token
                from app.services.cost_tracker import get_tracker
                user = await verify_token(session, token) if token else None
                tracker = get_tracker()
                cost_summary = tracker.summary()
                log_extra = {"args": _safe_truncate(args), "latency_ms": latency}
                if cost_summary.get("total_cost_usd", 0) > 0:
                    log_extra["costs"] = cost_summary
                if isinstance(result, dict) and result.get("credits_spent"):
                    log_extra["credits_spent"] = result["credits_spent"]
                uid = user.id if user else None
                if not uid and isinstance(result, dict) and result.get("user_id"):
                    uid = result["user_id"]
                if uid:
                    session.add(MCPUsageLog(
                        user_id=uid, action="tool_call", tool_name=tool_name,
                        extra_data=log_extra,
                    ))
                    # Persist individual cost entries for detailed reporting
                    for entry in tracker.entries:
                        session.add(MCPUsageLog(
                            user_id=uid, action=f"cost_{entry['service']}",
                            tool_name=tool_name,
                            extra_data=entry,
                        ))
                    # Log tool call (request)
                    import json as _json
                    args_preview = _json.dumps(args, default=str)[:300]
                    session.add(MCPConversationLog(
                        user_id=uid, direction="client_to_server",
                        method="tools/call", message_type="tool_call",
                        raw_json={"tool": tool_name, "args": args},
                        content_summary=f"{tool_name}({args_preview})",
                    ))
                    # Log tool result (response)
                    result_preview = ""
                    if isinstance(result, dict):
                        result_preview = result.get("message", _json.dumps(result, default=str)[:500])
                    elif isinstance(result, list):
                        result_preview = f"[{len(result)} items]"
                    else:
                        result_preview = str(result)[:500]
                    session.add(MCPConversationLog(
                        user_id=uid, direction="server_to_client",
                        method="tools/call", message_type="tool_result",
                        raw_json={"tool": tool_name, "result": _safe_truncate(result)},
                        content_summary=f"→ {tool_name}: {result_preview[:500]}",
                    ))
            except Exception:
                pass

            await session.commit()
            return result
        except Exception as e:
            # Log error
            try:
                session.add(MCPUsageLog(
                    user_id=0, action="tool_error", tool_name=tool_name,
                    extra_data={"args": _safe_truncate(args), "error": str(e)[:500]},
                ))
                await session.commit()
            except Exception:
                await session.rollback()
            raise


def _safe_truncate(obj, max_len=5000) -> dict:
    """Truncate large values in args for logging."""
    import json
    try:
        s = json.dumps(obj, default=str)
        if len(s) > max_len:
            return {"_truncated": s[:max_len]}
        return obj
    except Exception:
        return {"_error": "could not serialize"}


async def _dispatch(tool_name: str, args: dict, token: Optional[str], session) -> Any:

    # ── Global integration gate — block ALL tools if essential keys missing ──
    # Exempt: login, get_context, configure_integration, check_integrations, list_projects, select_project
    EXEMPT_TOOLS = {"get_context", "configure_integration", "check_integrations",
                    "list_projects", "select_project", "estimate_cost"}
    if tool_name not in EXEMPT_TOOLS and token:
        try:
            from app.auth.middleware import verify_token as _vt
            _gate_user = await _vt(session, token)
            if _gate_user:
                _gate_integrations = (await session.execute(
                    select(MCPIntegrationSetting).where(
                        MCPIntegrationSetting.user_id == _gate_user.id,
                        MCPIntegrationSetting.is_connected == True,
                    )
                )).scalars().all()
                _configured = {i.integration_name for i in _gate_integrations}
                _required = {"apollo", "openai", "smartlead", "apify"}
                _missing = _required - _configured
                if _missing:
                    return {
                        "error": "missing_integrations",
                        "missing": sorted(_missing),
                        "message": (
                            f"STOP — missing API keys: {', '.join(sorted(_missing))}.\n\n"
                            f"Set up at {settings.UI_BASE}/setup first.\n"
                            f"No tools work until all keys are connected."
                        ),
                    }
        except Exception:
            pass  # Don't block on auth errors — individual tools handle that

    # ── Auth: token from SSE URL, or from tool argument, or from session store ──

    if tool_name == "get_context":
        # Accept token as argument (Claude reads it from .mcp.json URL and passes it)
        arg_token = args.get("token", "")
        if arg_token and arg_token.startswith("mcp_"):
            token = arg_token
            # Store for all future tool calls in this session
            from app.mcp.server import _session_tokens, _session_user_tokens, mcp_server
            _session_tokens["_latest"] = token
            try:
                ctx = mcp_server.request_context
                _session_user_tokens[id(ctx.session)] = token
            except Exception:
                pass

        if not token or not token.startswith("mcp_"):
            return {
                "error": "not_authenticated",
                "message": (
                    "Authentication required. Pass your token: get_context(token='mcp_...')\n"
                    f"Get your token at {settings.UI_BASE}/setup"
                ),
            }
        user = await _get_user(token, session)
        from sqlalchemy import func

        # Check integrations
        integrations_result = await session.execute(
            select(MCPIntegrationSetting).where(MCPIntegrationSetting.user_id == user.id)
        )
        configured_keys = {i.integration_name for i in integrations_result.scalars().all()}
        missing_keys = {"apollo", "openai", "smartlead", "apify"} - configured_keys

        # Projects
        projects = (await session.execute(
            select(Project).where(Project.user_id == user.id, Project.is_active == True)
        )).scalars().all()

        # Pipeline runs (GatheringRun already imported at module level)
        runs = (await session.execute(
            select(GatheringRun).where(
                GatheringRun.project_id.in_([p.id for p in projects])
            ).order_by(GatheringRun.created_at.desc()).limit(10)
        )).scalars().all() if projects else []

        # Draft campaigns (including mcp_draft = accounts selected, not yet in SmartLead)
        drafts = (await session.execute(
            select(Campaign).where(
                Campaign.project_id.in_([p.id for p in projects]),
                Campaign.status.in_(["draft", "DRAFT", "DRAFTED", "mcp_draft"]),
            )
        )).scalars().all() if projects else []

        # Reply counts
        reply_count = 0
        warm_count = 0
        if projects:
            pids = [p.id for p in projects]
            reply_count = (await session.execute(
                select(func.count(MCPReply.id)).where(MCPReply.project_id.in_(pids))
            )).scalar() or 0
            warm_count = (await session.execute(
                select(func.count(MCPReply.id)).where(
                    MCPReply.project_id.in_(pids),
                    MCPReply.category.in_(["interested", "meeting_request", "question"]),
                )
            )).scalar() or 0

        # Recent conversations
        recent_convos = (await session.execute(
            select(MCPConversationLog).where(
                MCPConversationLog.user_id == user.id
            ).order_by(MCPConversationLog.created_at.desc()).limit(5)
        )).scalars().all()

        # Auto-set active project if user has exactly 1
        if len(projects) == 1 and not user.active_project_id:
            user.active_project_id = projects[0].id

        # Pending approval gates
        pending_gates = []
        if projects:
            gate_result = await session.execute(
                select(ApprovalGate).where(
                    ApprovalGate.gathering_run_id.in_([r.id for r in runs]),
                    ApprovalGate.status == "pending",
                )
            )
            pending_gates = gate_result.scalars().all()

        # Integration status — BLOCK everything if keys missing
        keys_msg = ""
        if missing_keys:
            return {
                "user": {"name": user.name, "email": user.email},
                "integrations": {"configured": sorted(configured_keys), "missing": sorted(missing_keys)},
                "setup_required": True,
                "message": (
                    f"Connected as {user.name}.\n\n"
                    f"**STOP — API keys required before anything else.**\n"
                    f"Missing: **{', '.join(sorted(missing_keys))}**\n\n"
                    f"Go to {settings.UI_BASE}/setup and connect ALL of these:\n"
                    + "".join(f"  - {k}\n" for k in sorted(missing_keys))
                    + f"\nNo projects, no campaigns, no searches until all keys are set.\n"
                    f"Come back after setting them up."
                ),
            }

        active_project = next((p for p in projects if p.id == user.active_project_id), None) if user.active_project_id else None
        context = {
            "user": {"name": user.name, "email": user.email},
            "active_project": {
                "name": active_project.name if active_project else None,
                "link": f"{settings.UI_BASE}/projects/{active_project.id}" if active_project else None,
                "offer_approved": active_project.offer_approved if active_project else None,
            } if active_project else None,
            "integrations": {"configured": sorted(configured_keys), "missing": sorted(missing_keys)},
            "projects": [{"name": p.name, "link": f"{settings.UI_BASE}/projects/{p.id}", "offer_approved": p.offer_approved} for p in projects],
            "pipeline_runs": [{"id": r.id, "phase": r.current_phase, "status": r.status, "companies": r.new_companies_count, "people": r.total_people_found, "project_id": r.project_id, "campaign_id": r.campaign_id, "kpi": {"target_people": r.target_people, "max_per_company": r.max_people_per_company}} for r in runs],
            "draft_campaigns": [{"id": c.id, "name": c.name, "status": c.status, "accounts": len(c.email_account_ids or []), "smartlead_url": f"https://app.smartlead.ai/app/email-campaigns-v2/{c.external_id}/analytics" if c.external_id else None} for c in drafts],
            "replies": {"total": reply_count, "warm": warm_count},
            "recent_activity": [{"method": c.method, "summary": c.content_summary, "at": str(c.created_at)} for c in recent_convos],
            "message": (
                f"Welcome back, {user.name}!\n\n"
                + keys_msg
                + (("\n".join(
                    f"- {p.name}: {settings.UI_BASE}/projects/{p.id}"
                    + (f" — OFFER CONFIRMED ✓" if p.offer_approved else f" — ⚠️ OFFER NOT CONFIRMED")
                    for p in projects) + "\n") if projects else "No projects yet. Tell me your company website to create one.\n")
                + (f"{len(runs)} pipeline run{'s' if len(runs) != 1 else ''}\n" if runs else "")
                + (f"{len(drafts)} DRAFT campaign{'s' if len(drafts) != 1 else ''}\n" if drafts else "")
                + (f"{reply_count} replies ({warm_count} warm)\n" if reply_count else "")
                # CRITICAL: if any project has unapproved offer, this is THE next step
                + ("".join(
                    f"\n🚨 OFFER NOT CONFIRMED for '{p.name}' — gathering blocked until confirmed."
                    + (f"\nWebsite: {p.website}" if getattr(p, 'website', None) else "")
                    + f"\nProject page: {settings.UI_BASE}/projects/{p.id}"
                    + (f"\nCurrent offer: {(p.offer_summary or {}).get('primary_offer', p.target_segments or 'NOT SET')}" if p.offer_summary or p.target_segments else "\nNo offer extracted yet.")
                    + f"\nShow the user the offer, website, and project page link. Ask: is this correct? If user provides feedback, call confirm_offer(feedback=...). If user says yes, call confirm_offer(approved=true).\n"
                    for p in projects if not p.offer_approved))
                + ("\nReady for next step. Ask the user what they'd like to do — ONE question only, no lists." if all(p.offer_approved for p in projects) else "")
            ),
        }

        # Action items for state restoration
        if pending_gates:
            g = pending_gates[0]
            context["action_required"] = {
                "type": "checkpoint_approval",
                "gate_id": g.id,
                "run_id": g.gathering_run_id,
                "checkpoint": g.gate_type,
                "message": f"Pending checkpoint: {g.gate_type}. Approve or reject to continue.",
            }
        if drafts:
            context["action_required_campaigns"] = [{
                "id": c.id, "name": c.name, "status": c.status,
                "accounts": len(c.email_account_ids or []),
                "message": "Accounts pre-selected, pipeline will auto-push when KPI hit." if c.status == "mcp_draft"
                    else "SmartLead DRAFT — check test email and activate when ready.",
            } for c in drafts]

        return context

    if tool_name == "configure_integration":
        user = await _get_user(token, session)
        from app.services.encryption import encrypt_value
        integration_name = args["integration_name"].strip()
        api_key = args["api_key"].strip()
        connected = False
        message = ""

        if integration_name == "smartlead":
            from app.services.smartlead_service import SmartLeadService
            svc = SmartLeadService(api_key=api_key)
            connected = await svc.test_connection()
            campaigns = await svc.get_campaigns() if connected else []
            # Pre-cache ALL email accounts on key connect (instant lookups later)
            account_count = 0
            if connected:
                try:
                    all_accounts = await svc.get_email_accounts()
                    account_count = len(all_accounts)
                    # Cache in DB
                    from sqlalchemy import text as _st
                    await session.execute(_st(f"DELETE FROM smartlead_accounts_cache WHERE user_id={user.id}"))
                    for a in all_accounts:
                        await session.execute(_st(
                            "INSERT INTO smartlead_accounts_cache (user_id, account_id, from_email, from_name) "
                            "VALUES (:uid, :aid, :email, :name) ON CONFLICT (user_id, account_id) DO NOTHING"
                        ), {"uid": user.id, "aid": a.get("id"), "email": a.get("from_email", ""), "name": a.get("from_name", "")})
                    logger.info(f"Pre-cached {account_count} SmartLead accounts for user {user.id}")
                except Exception as e:
                    logger.warning(f"SmartLead account pre-cache failed: {e}")
            message = f"{len(campaigns)} campaigns, {account_count} email accounts cached" if connected else "Connection failed"
        elif integration_name == "apollo":
            from app.services.apollo_service import ApolloService
            svc = ApolloService(api_key=api_key)
            connected = await svc.test_connection()
            message = "Connected" if connected else "Connection failed"
        elif integration_name == "apify":
            # Apify proxy — just store the key, test by making a dummy request
            connected = bool(api_key and len(api_key) > 10)
            message = "Apify proxy key saved" if connected else "Invalid key"
        else:
            connected = True
            message = f"{integration_name} key saved"

        existing = await session.execute(
            select(MCPIntegrationSetting).where(
                MCPIntegrationSetting.user_id == user.id,
                MCPIntegrationSetting.integration_name == integration_name,
            )
        )
        setting = existing.scalar_one_or_none()
        encrypted = encrypt_value(api_key)
        if setting:
            setting.api_key_encrypted = encrypted
            setting.is_connected = connected
            setting.connection_info = message
        else:
            session.add(MCPIntegrationSetting(
                user_id=user.id, integration_name=integration_name,
                api_key_encrypted=encrypted, is_connected=connected, connection_info=message,
            ))
        return {"connected": connected, "message": message}

    if tool_name == "check_integrations":
        user = await _get_user(token, session)
        result = await session.execute(
            select(MCPIntegrationSetting).where(MCPIntegrationSetting.user_id == user.id)
        )
        integrations = {i.integration_name: {"connected": i.is_connected, "info": i.connection_info}
                        for i in result.scalars().all()}

        REQUIRED = ["apollo", "smartlead", "openai", "apify"]
        missing = [k for k in REQUIRED if k not in integrations or not integrations[k]["connected"]]

        return {
            "integrations": [{"name": k, **v} for k, v in integrations.items()],
            "required_for_campaigns": REQUIRED,
            "missing_required": missing,
            "ready": len(missing) == 0,
            "message": (
                "All required integrations connected! Ready to create campaigns."
                if not missing else
                f"Missing required keys: {', '.join(missing)}. Connect them with configure_integration to start creating campaigns."
            ),
        }

    # ── Project tools ──
    if tool_name == "select_project":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found or not yours")
        user.active_project_id = project.id
        # Get project details for display (use already-imported models)
        from sqlalchemy import func
        runs_count = (await session.execute(
            select(func.count(GatheringRun.id)).where(GatheringRun.project_id == project.id)
        )).scalar() or 0
        companies_count = (await session.execute(
            select(func.count(DiscoveredCompany.id)).where(DiscoveredCompany.project_id == project.id)
        )).scalar() or 0
        campaign_filters = project.campaign_filters or []
        return {
            "active_project": {
                "id": project.id,
                "name": project.name,
                "target_segments": project.target_segments,
                "target_industries": project.target_industries,
                "sender_name": project.sender_name,
                "sender_company": project.sender_company,
            },
            "stats": {
                "gathering_runs": runs_count,
                "discovered_companies": companies_count,
                "campaign_filters": campaign_filters,
            },
            "blacklist_scope": f"Blacklisting checks against {len(campaign_filters)} campaigns assigned to this project. "
                               f"Companies already in these campaigns will be rejected during CP1.",
            "message": f"Now working on '{project.name}'. All pipeline operations will use this project's campaigns for blacklisting.",
            "_links": {"project": f"{settings.UI_BASE}/projects/{project.id}"},
        }

    if tool_name == "create_project":
        user = await _get_user(token, session)
        result = await session.execute(select(Company).limit(1))
        company = result.scalar_one_or_none()
        if not company:
            company = Company(name=f"{user.name}'s Company")
            session.add(company)
            await session.flush()

        website = args.get("website")
        document_text = args.get("document_text")
        target_segments = args.get("target_segments") or ""
        offer_summary = None
        scrape_status = "not_scraped"
        extracted_sequences = []
        extracted_settings = {}

        # ── Document-based extraction: extracts EVERYTHING from strategy doc ──
        if document_text:
            try:
                from app.services.document_extractor import extract_from_document
                ctx = UserServiceContext(user.id, session)
                openai_key = await ctx.get_key("openai")
                if openai_key:
                    extraction = await extract_from_document(
                        document_text, website or "", openai_key, model="gpt-4.1-nano"
                    )
                    if not extraction.get("error"):
                        roles = extraction.get("target_roles", {})
                        segments = extraction.get("segments", [])
                        all_titles = (roles.get("primary", []) + roles.get("secondary", [])
                                      + roles.get("tertiary", []))

                        offer_summary = {
                            "primary_offer": extraction.get("offer", ""),
                            "value_proposition": extraction.get("value_prop", ""),
                            "target_audience": extraction.get("target_audience", ""),
                            "target_roles": {
                                "titles": all_titles,
                                "primary": roles.get("primary", []),
                                "secondary": roles.get("secondary", []),
                                "tertiary": roles.get("tertiary", []),
                                "seniorities": roles.get("seniorities", ["c_suite", "vp", "head", "director"]),
                            },
                            "segments": segments,
                            "apollo_filters": extraction.get("apollo_filters", {}),
                            "example_companies": extraction.get("example_companies", []),
                            "exclusion_list": extraction.get("exclusion_list", []),
                            "_source": "document",
                        }
                        target_segments = (
                            f"{extraction.get('offer', '')}. Target: {extraction.get('target_audience', '')}"
                        )
                        extracted_sequences = extraction.get("sequences", [])
                        extracted_settings = extraction.get("campaign_settings", {})
                        scrape_status = "document_extracted"
                        logger.info(f"Document extraction: {len(segments)} segments, "
                                   f"{len(all_titles)} roles, {len(extracted_sequences)} sequences")
            except Exception as e:
                logger.error(f"Document extraction failed: {e}")

        # ── 3-layer offer extraction: proxy -> direct+meta -> GPT knowledge ──
        if not offer_summary and website:
            if not website.startswith("http"):
                website = f"https://{website}"
            import httpx as _hx, re as _re, json as _jn
            ctx = UserServiceContext(user.id, session)
            wt = ""  # website_text
            # Layer 1: Apify proxy
            try:
                sc = await ctx.get_scraper_service()
                wt = (await sc.scrape_website(website)).get("text", "")[:4000]
            except Exception as e:
                logger.warning(f"Proxy scrape: {e}")
            # Layer 2: Direct + meta
            if not wt:
                try:
                    async with _hx.AsyncClient(timeout=15, follow_redirects=True) as c:
                        r = await c.get(website, headers={"User-Agent": "Mozilla/5.0"})
                        if r.status_code == 200:
                            h = r.text
                            mp = []
                            for p in [r'<meta\s+name="description"\s+content="([^"]+)"', r'<meta\s+property="og:description"\s+content="([^"]+)"', r'<title>([^<]+)</title>']:
                                m = _re.search(p, h, _re.IGNORECASE)
                                if m:
                                    mp.append(m.group(1).strip())
                            b = _re.sub(r'<script[^>]*>.*?</script>', '', h, flags=_re.DOTALL|_re.IGNORECASE)
                            b = _re.sub(r'<style[^>]*>.*?</style>', '', b, flags=_re.DOTALL|_re.IGNORECASE)
                            b = _re.sub(r'<[^>]+>', ' ', b)
                            b = _re.sub(r'\s+', ' ', b).strip()[:4000]
                            wt = b if len(b) > 100 else ("Meta: " + " | ".join(mp) if mp else "")
                except Exception:
                    pass
            # GPT analysis — ALWAYS (scraped text OR Layer 3: GPT knowledge)
            openai_key = await ctx.get_key("openai")
            if openai_key:
                dom = website.replace("https://", "").replace("http://", "").rstrip("/")
                if wt:
                    pr = f"Analyze this company website.\nWebsite: {website}\nContent: {wt[:3000]}\n\n"
                else:
                    pr = f"What does the company at {dom} do? Use your knowledge.\n\n"
                pr += ('Return JSON: {"company_name":"...","products":[{"name":"...","description":"..."}],'
                       '"primary_offer":"main product in 1 sentence","value_proposition":"problem solved",'
                       '"target_audience":"who buys","key_differentiators":["..."],'
                       '"target_roles":{"titles":["CEO","CMO","VP of X","Head of Y"],'
                       '"seniorities":["c_suite","vp","head","director"],'
                       '"reasoning":"who at target company would BUY this"}}\n'
                       'ALWAYS include CEO + relevant C-level + 2-3 specific buyer roles.\n'
                       'If unknown: {"unknown":true}\nOnly JSON.')
                try:
                    async with _hx.AsyncClient(timeout=25) as c:
                        rr = await c.post("https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                            json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": pr}], "max_tokens": 800, "temperature": 0})
                        ct = rr.json()["choices"][0]["message"]["content"].strip()
                        if ct.startswith("```"):
                            ct = ct.split("\n", 1)[1].rsplit("```", 1)[0]
                        offer_summary = _jn.loads(ct)
                        if offer_summary.get("unknown"):
                            offer_summary = None
                            scrape_status = "unknown"
                        else:
                            if wt:
                                offer_summary["_raw_website_text"] = wt[:2000]
                            offer_summary["_source"] = "website" if wt else "gpt_knowledge"
                            target_segments = f"{offer_summary.get('primary_offer', '')}. Target: {offer_summary.get('target_audience', '')}"
                            scrape_status = "analyzed"
                except Exception as e:
                    logger.warning(f"GPT offer: {e}")
                    if wt:
                        target_segments = f"Website ({website}): {wt[:2000]}"
                    scrape_status = "gpt_failed"

        project = Project(
            company_id=company.id, user_id=user.id,
            name=args.get("name") or (website.replace("https://", "").replace("http://", "").split("/")[0].split(".")[0].title() if website else "New Project"),
            website=website,
            target_segments=target_segments or None,
            target_industries=args.get("target_industries"),
            sender_name=args.get("sender_name"),
            sender_company=args.get("sender_company"),
            sender_position=args.get("sender_position"),
            offer_summary=offer_summary,
            offer_approved=False,
        )
        session.add(project)
        await session.flush()
        user.active_project_id = project.id

        # ── Store extracted sequences from document ──
        sequence_display = ""
        if extracted_sequences:
            for seq_data in extracted_sequences:
                steps = seq_data.get("steps", [])
                if not steps:
                    continue
                # Create GeneratedSequence
                seq = GeneratedSequence(
                    project_id=project.id, company_id=company.id,
                    campaign_name=seq_data.get("name", f"Sequence from document"),
                    sequence_steps={"steps": steps},
                    sequence_step_count=len(steps),
                    rationale="Extracted from strategy document",
                    status="draft",
                    model_used="document_extraction",
                )
                session.add(seq)
                await session.flush()

                # Create draft Campaign linked to sequence
                campaign = Campaign(
                    project_id=project.id, company_id=company.id,
                    name=seq_data.get("name", f"Draft — {project.name}"),
                    status="mcp_draft", created_by="mcp",
                    sequence_id=seq.id,
                    config={
                        "from_document": True,
                        "settings": extracted_settings,
                        "daily_limit_per_mailbox": extracted_settings.get("daily_limit_per_mailbox"),
                        "tracking": extracted_settings.get("tracking", False),
                        "stop_on_reply": extracted_settings.get("stop_on_reply", True),
                        "plain_text": extracted_settings.get("plain_text", True),
                    },
                )
                session.add(campaign)
                await session.flush()

                sequence_display += f"\n**Sequence: {seq.campaign_name}** ({len(steps)} emails)\n"
                for i, step in enumerate(steps):
                    sequence_display += f"  Email {i+1} (Day {step.get('day', '?')}): {step.get('subject', 'No subject')}\n"
                sequence_display += f"\n**Campaign settings:** "
                if extracted_settings:
                    sequence_display += f"{'No tracking' if not extracted_settings.get('tracking') else 'Tracking on'}, "
                    sequence_display += f"{'Stop on reply' if extracted_settings.get('stop_on_reply') else 'Continue after reply'}, "
                    sequence_display += f"{extracted_settings.get('daily_limit_per_mailbox', '?')}/mailbox/day\n"

                logger.info(f"Created sequence {seq.id} ({len(steps)} steps) + draft campaign {campaign.id}")

        # Queue for background re-analysis if inline scrape failed
        if not offer_summary and website:
            from app.services.offer_scraper import queue_offer_analysis
            queue_offer_analysis(project.id)

        # ── Build response with offer for user alignment ──
        offer_display = ""
        if offer_summary:
            target_roles = offer_summary.get("target_roles", {})
            role_titles = target_roles.get("titles", [])
            role_reasoning = target_roles.get("reasoning", "")

            source = "document" if offer_summary.get("_source") == "document" else website
            offer_display = (
                f"\n\n**Offer extracted from {source}:**\n"
                f"  Product: {offer_summary.get('primary_offer', 'N/A')}\n"
                f"  Value prop: {offer_summary.get('value_proposition', 'N/A')}\n"
                f"  Target audience: {offer_summary.get('target_audience', 'N/A')}\n"
            )
            if offer_summary.get("products"):
                offer_display += "  All products: " + ", ".join(p.get("name", "") for p in offer_summary["products"]) + "\n"
            # Show segments from document
            segments = offer_summary.get("segments", [])
            if segments:
                offer_display += f"\n**{len(segments)} market segments:**\n"
                for seg in segments:
                    kw_list = seg.get("keywords", [])
                    offer_display += f"  - {seg.get('name', '?')}: {', '.join(kw_list[:5])}\n"
            if role_titles:
                offer_display += f"\n**Target decision makers:** {', '.join(role_titles)}\n"
                if role_reasoning:
                    offer_display += f"  Reasoning: {role_reasoning}\n"
            # Show example companies from document
            example_companies = offer_summary.get("example_companies", [])
            if example_companies:
                offer_display += f"\n**Seed companies ({len(example_companies)}):**\n"
                for ex in example_companies[:5]:
                    offer_display += f"  - {ex.get('name', '?')} ({ex.get('domain', '?')})\n"
            # Show exclusion list from document
            exclusion_list = offer_summary.get("exclusion_list", [])
            if exclusion_list:
                offer_display += f"\n**Exclusion rules ({len(exclusion_list)}):**\n"
                for ex in exclusion_list[:5]:
                    offer_display += f"  - {ex.get('type', '?')}\n"
            # Show sequences from document
            if sequence_display:
                offer_display += sequence_display
            offer_display += (
                f"\n**Is this correct?** Review: offer, target audience, roles"
                + (", segments, and sequence" if segments else "")
                + f". If anything is wrong, tell me. "
                f"Once confirmed, these will be used to find leads and create the campaign."
            )
        else:
            offer_display = (
                f"\n\nCouldn't extract offer from {website} automatically (site may use JavaScript rendering)."
                f"\n\n**ACTION REQUIRED**: Open {website} yourself using WebFetch, read the page content, "
                f"then call confirm_offer with feedback describing the company's product/offer. "
                f"Example: confirm_offer(project_id={project.id}, feedback='Our company provides [service] for [target audience]')"
            )

        return {
            "project_id": project.id,
            "project_link": f"{settings.UI_BASE}/projects/{project.id}",
            "name": project.name,
            "offer_summary": offer_summary,
            "offer_approved": False,
            "scrape_status": scrape_status,
            "_instructions": "ALWAYS include the project_link in your response to the user. It must be visible and clickable.",
            "message": (
                f"Project '{project.name}' created.\n"
                f"**Project page: {settings.UI_BASE}/projects/{project.id}**\n"
                + offer_display
            ),
            "_links": {"project": f"{settings.UI_BASE}/projects/{project.id}"},
            "next_step": "confirm_offer — user must approve the offer before gathering can start",
        }

    if tool_name == "confirm_offer":
        user = await _get_user(token, session)
        project_id = args.get("project_id") or (user.active_project_id if hasattr(user, 'active_project_id') else None)
        if project_id and isinstance(project_id, str):
            project_id = int(project_id)  # Claude sometimes sends string "424" instead of int
        if not project_id:
            raise ValueError("project_id required")
        project = await session.get(Project, project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        approved = args.get("approved", False)
        feedback = args.get("feedback", "")

        if approved and not feedback:
            # User confirms — offer + target roles approved
            project.offer_approved = True
            roles = (project.offer_summary or {}).get("target_roles", {})
            return {
                "project_id": project.id,
                "offer_approved": True,
                "target_roles": roles.get("titles", []),
                "message": (
                    f"Offer confirmed for '{project.name}'.\n"
                    f"Target roles: {', '.join(roles.get('titles', ['CEO']))}\n"
                    f"Project: {settings.UI_BASE}/projects/{project.id}\n\n"
                    f"Have you launched campaigns for this segment before?\n"
                    f"If yes, share the campaign name pattern so I can exclude already-contacted leads."
                ),
                "_instructions": "Ask ONLY about previous campaigns (blacklist). ONE question. If user says no, proceed to ask about email accounts (align_email_accounts).",
                "_links": {"project": f"{settings.UI_BASE}/projects/{project.id}"},
            }
        elif feedback:
            # User provides feedback — update offer and re-analyze
            old_offer = project.offer_summary or {}
            ctx = UserServiceContext(user.id, session)
            openai_key = await ctx.get_key("openai")

            if openai_key:
                import httpx as _httpx
                update_prompt = f"""The user reviewed the extracted offer and provided feedback.

Current offer:
- Product: {old_offer.get('primary_offer', 'N/A')}
- Value prop: {old_offer.get('value_proposition', 'N/A')}
- Target: {old_offer.get('target_audience', 'N/A')}
- Products: {', '.join(p.get('name','') for p in old_offer.get('products', []))}

User feedback: "{feedback}"

Update the offer based on user feedback. Return JSON with same structure:
- "primary_offer": updated main product in 1 sentence
- "value_proposition": updated
- "target_audience": updated
- "products": updated list
- "key_differentiators": updated

Return ONLY valid JSON."""

                try:
                    async with _httpx.AsyncClient(timeout=15) as client:
                        resp = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                            json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": update_prompt}],
                                  "max_tokens": 600, "temperature": 0},
                        )
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"].strip()
                        if content.startswith("```"):
                            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                        import json as _json
                        updated = _json.loads(content)
                        # Preserve raw website text
                        updated["_raw_website_text"] = old_offer.get("_raw_website_text", "")
                        project.offer_summary = updated
                        project.target_segments = f"{updated.get('primary_offer', '')}. Target: {updated.get('target_audience', '')}"
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(project, "offer_summary")
                except Exception as e:
                    logger.warning(f"Offer update failed: {e}")
                    # Fallback: just store user's feedback as target_segments
                    project.target_segments = feedback
            else:
                project.target_segments = feedback

            project.offer_approved = False
            updated_offer = project.offer_summary or {}
            return {
                "project_id": project.id,
                "offer_approved": False,
                "offer_summary": updated_offer,
                "message": (
                    f"Offer updated based on your feedback.\n\n"
                    f"**Updated offer:**\n"
                    f"  Product: {updated_offer.get('primary_offer', project.target_segments)}\n"
                    f"  Target: {updated_offer.get('target_audience', 'N/A')}\n\n"
                    f"**Is this correct now?** Confirm to proceed, or provide more feedback."
                ),
                "_links": {"project": f"{settings.UI_BASE}/projects/{project.id}"},
            }
        else:
            # No approved, no feedback — just show current offer
            offer = project.offer_summary or {}
            return {
                "project_id": project.id,
                "offer_approved": project.offer_approved,
                "offer_summary": offer,
                "message": (
                    f"Current offer for '{project.name}':\n"
                    f"  Product: {offer.get('primary_offer', project.target_segments or 'Not set')}\n"
                    f"  Target: {offer.get('target_audience', 'N/A')}\n\n"
                    f"Confirm (approved=true) or provide feedback to adjust."
                ),
            }

    if tool_name == "list_projects":
        user = await _get_user(token, session)
        result = await session.execute(
            select(Project).where(Project.user_id == user.id, Project.is_active == True)
        )
        return [{"id": p.id, "name": p.name, "target_segments": p.target_segments,
                 "sender_name": p.sender_name} for p in result.scalars().all()]

    if tool_name == "update_project":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")
        for field in ["name", "target_segments", "target_industries", "sender_name", "sender_company"]:
            if field in args:
                setattr(project, field, args[field])
        return {"updated": True, "project_id": project.id}

    # ── Intent Parsing ──
    if tool_name == "parse_gathering_intent":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        # Get user's offer from project context (for competitor exclusion)
        user_offer = project.target_segments or ""
        if project.sender_company:
            user_offer = f"{project.sender_company}: {user_offer}"

        # Get OpenAI key
        from app.services.encryption import decrypt_value
        openai_key = None
        r = await session.execute(
            select(MCPIntegrationSetting).where(
                MCPIntegrationSetting.user_id == user.id,
                MCPIntegrationSetting.integration_name == "openai",
            )
        )
        row = r.scalar_one_or_none()
        if row and row.api_key_encrypted:
            try:
                openai_key = decrypt_value(row.api_key_encrypted)
            except Exception:
                pass

        from app.services.intent_parser import parse_gathering_intent
        result = await parse_gathering_intent(
            query=args["query"],
            user_offer=user_offer,
            openai_key=openai_key,
        )

        segments = result.get("segments", [])
        n = result.get("pipelines_needed", len(segments))

        return {
            **result,
            "message": (
                f"Parsed query into {n} segment{'s' if n > 1 else ''}: "
                + ", ".join(s.get("label", "?") for s in segments)
                + (f". Competitor exclusions: {result.get('competitor_exclusions', [])}" if result.get("competitor_exclusions") else "")
                + f"\n\n{'Call tam_gather ONCE per segment.' if n > 1 else 'Call tam_gather with this segment.'}"
            ),
        }

    # ── Pipeline tools ──
    if tool_name == "tam_gather":
        user = await _get_user(token, session)
        project_id = args.get("project_id")

        # If no project_id provided, use active project
        if not project_id and user.active_project_id:
            project_id = user.active_project_id

        # If still no project, check how many projects user has
        if not project_id:
            all_projects = (await session.execute(
                select(Project).where(Project.user_id == user.id, Project.is_active == True)
            )).scalars().all()
            if len(all_projects) == 0:
                raise ValueError("No projects found. Create a project first with create_project.")
            elif len(all_projects) == 1:
                project_id = all_projects[0].id
                user.active_project_id = project_id
            else:
                return {
                    "error": "project_selection_required",
                    "message": "You have multiple projects. Which one are you working on? Select one first.",
                    "projects": [{"id": p.id, "name": p.name, "target_segments": p.target_segments} for p in all_projects],
                    "hint": "Call select_project with the project_id you want to use.",
                }

        project = await session.get(Project, project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        # ── OFFER GATE: must be approved before gathering ──
        if not project.offer_approved:
            offer = project.offer_summary or {}
            return {
                "error": "offer_not_approved",
                "message": (
                    f"Cannot start gathering — offer not confirmed yet for '{project.name}'.\n\n"
                    + (f"Current offer: {offer.get('primary_offer', project.target_segments or 'Not set')}\n"
                       f"Target: {offer.get('target_audience', 'N/A')}\n\n" if offer else "")
                    + "Confirm the offer first with confirm_offer (approved=true), or provide feedback to adjust it."
                ),
                "project_id": project.id,
                "_links": {"project": f"{settings.UI_BASE}/projects/{project.id}"},
            }

        # Auto-set active project during pipeline work
        user.active_project_id = project.id

        # Bug 11: Offer verification — MUST know what user sells before gathering
        if "api" in args.get("source_type", "") and not project.target_segments:
            return {
                "error": "offer_unknown",
                "message": (
                    "Before searching, I need to understand what you sell so I can correctly identify targets vs competitors.\n\n"
                    "Please either:\n"
                    "1. Tell me your company website (I'll scrape it to understand your offer)\n"
                    "2. Describe what you sell and who your customers are\n\n"
                    "Use update_project with target_segments to set this, or call create_project with a website."
                ),
                "project_id": project.id,
            }

        source_type = args["source_type"]
        filters = args.get("filters", {})

        # ── REUSE filters from a previous run ──
        reuse_run_id = args.get("reuse_run_id")
        if reuse_run_id and not filters.get("q_organization_keyword_tags"):
            prev_run = await session.get(GatheringRun, reuse_run_id)
            if prev_run and prev_run.filters:
                filters = dict(prev_run.filters)  # Copy previous filters
                # Keep user's target_count override if provided

        # ── AUTO-DISCOVER filters via taxonomy-backed filter mapper ──
        if "api" in source_type and not filters.get("q_organization_keyword_tags"):
            query = args.get("query") or project.target_segments
            offer_text = project.target_segments or ""
            if query:
                try:
                    from app.config import settings as _s
                    ctx_probe = UserServiceContext(user.id, session)
                    openai_key = await ctx_probe.get_key("openai") or _s.OPENAI_API_KEY

                    # Try filter_mapper first (uses taxonomy with embeddings)
                    try:
                        from app.services.filter_mapper import map_query_to_filters
                        mapped = await map_query_to_filters(query, offer_text, openai_key)
                        if mapped and (mapped.get("q_organization_keyword_tags") or mapped.get("organization_industry_tag_ids")):
                            filters.setdefault("q_organization_keyword_tags", mapped.get("q_organization_keyword_tags"))
                            filters.setdefault("organization_locations", mapped.get("organization_locations"))
                            filters.setdefault("organization_num_employees_ranges", mapped.get("organization_num_employees_ranges"))
                            # CRITICAL: copy industry_tag_ids + strategy from filter_mapper
                            if mapped.get("organization_industry_tag_ids"):
                                filters["organization_industry_tag_ids"] = mapped["organization_industry_tag_ids"]
                            filters["filter_strategy"] = mapped.get("filter_strategy", "keywords_only")
                            filters["industries"] = mapped.get("industries", [])
                            logger.info(f"Filter mapper: {len(mapped.get('organization_industry_tag_ids') or [])} industry_tag_ids, "
                                        f"{len(mapped.get('q_organization_keyword_tags') or [])} keywords, "
                                        f"strategy={mapped.get('filter_strategy')}")
                    except Exception as e:
                        logger.warning(f"Filter mapper failed, falling back to filter_intelligence: {e}")
                        # Fallback to old filter_intelligence
                        apollo_probe = await ctx_probe.get_apollo_service()
                        anthropic_key = await ctx_probe.get_key("anthropic") or _s.ANTHROPIC_API_KEY
                        from app.services.filter_intelligence import suggest_filters
                        suggestion = await suggest_filters(
                            query, apollo_probe, openai_key, anthropic_key, None,
                            args.get("target_people", 10),
                        )
                        if suggestion.get("suggested_filters"):
                            sf = suggestion["suggested_filters"]
                            filters.setdefault("q_organization_keyword_tags", sf.get("q_organization_keyword_tags"))
                            filters.setdefault("organization_locations", sf.get("organization_locations"))
                            filters.setdefault("organization_num_employees_ranges", sf.get("organization_num_employees_ranges"))
                except Exception as e:
                    logger.warning(f"Auto-filter discovery failed: {e}")

        # ── Auto-calculate pages from target_count BEFORE validation ──
        if "api" in source_type:
            target_count = args.get("target_people") or filters.pop("target_count", None)
            if target_count and not filters.get("max_pages"):
                per_page = filters.get("per_page", 100)
                companies_needed = int(int(target_count) / 0.3)
                filters["max_pages"] = max(10, (companies_needed + per_page - 1) // per_page)  # min 10 pages

        # ── Auto-infer company size from offer (Gap 1: smart size inference) ──
        if "api" in source_type and not filters.get("organization_num_employees_ranges") and project.target_segments:
            try:
                from app.config import settings as _s
                _oai = await UserServiceContext(user.id, session).get_key("openai") or _s.OPENAI_API_KEY
                if _oai:
                    from app.services.offer_analyzer import infer_target_size
                    size_result = await infer_target_size(project.target_segments, _oai)
                    apollo_range = size_result.get("apollo_range", "11,500")
                    filters["organization_num_employees_ranges"] = [apollo_range]
                    logger.info(f"Auto-inferred size from offer: {apollo_range} ({size_result.get('reasoning', '')})")
            except Exception as e:
                logger.warning(f"Size auto-inference failed: {e}")

        # ── Essential filter validation for API sources ──
        if "api" in source_type:
            missing = []
            if not filters.get("q_organization_keyword_tags") and not filters.get("organization_locations"):
                missing.append("keywords (q_organization_keyword_tags) OR locations (organization_locations)")
            if not filters.get("organization_num_employees_ranges"):
                missing.append("company size range (e.g. ['51,200'] for 50-200 employees)")
            if "api" in source_type and not filters.get("max_pages"):
                missing.append("max_pages OR target_count (how many target companies do you want?)")

            if missing:
                return {
                    "error": "missing_essential_filters",
                    "message": f"Cannot proceed — I need more info:\n" + "\n".join(f"  - {m}" for m in missing),
                    "hint": "You can say 'I want 10 target companies' and I'll calculate the rest.",
                }

            filters.setdefault("per_page", 100)
            filters.setdefault("max_pages", 10)

        import hashlib, json as _json
        filter_hash = hashlib.sha256(_json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]

        max_pages = filters.get("max_pages", 10)
        per_page = filters.get("per_page", 100)
        est_credits = max_pages if "api" in source_type else 0
        est_companies = max_pages * per_page

        # Filter confirmation — probe Apollo with per_page=100 (same 1 credit, get 100 companies FREE)
        if "api" in source_type and not args.get("confirm_filters"):
            total_available = 0
            probe_companies = []
            try:
                ctx_probe = UserServiceContext(user.id, session)
                apollo_probe = await ctx_probe.get_apollo_service()
                if apollo_probe.is_configured():
                    probe_result = await apollo_probe.search_organizations(
                        keyword_tags=filters.get("q_organization_keyword_tags", []),
                        locations=filters.get("organization_locations"),
                        num_employees_ranges=filters.get("organization_num_employees_ranges"),
                        industry_tag_ids=filters.get("organization_industry_tag_ids"),
                        page=1, per_page=100,
                    )
                    total_available = probe_result.get("pagination", {}).get("total_entries", 0) if probe_result else 0
                    # Save probe companies — page 1 is already fetched, reuse on confirm
                    probe_companies = probe_result.get("organizations", []) if probe_result else []
                    if probe_companies:
                        # Store in session so confirm step can skip page 1
                        # We'll create the run + companies on confirm, not here (user hasn't approved yet)
                        filters["_probe_companies"] = probe_companies
                        filters["_probe_page_done"] = 1
                        logger.info(f"Probe: {len(probe_companies)} companies from page 1 (total: {total_available})")
            except Exception as e:
                logger.warning(f"Apollo probe failed: {e}")

            # A8: Cost Estimator
            from app.services.cost_estimator import estimate_cost
            target_people = args.get("target_people", 100)
            max_ppc = args.get("max_people_per_company") or filters.get("max_people_per_company", 3)
            cost_est = estimate_cost(
                target_count=target_people,
                contacts_per_company=max_ppc,
                total_available=total_available,
                per_page=per_page,
            )

            # A7: People filter defaults (if not set)
            people_defaults = ""
            try:
                from app.services.people_mapper import infer_people_filters
                openai_key_raw = await UserServiceContext(user.id, session).get_key("openai")
                if openai_key_raw:
                    pf = await infer_people_filters(project.target_segments or "", openai_key_raw)
                    people_defaults = f"\n  Roles: {', '.join(pf.get('person_titles', []))}\n  Seniority: {', '.join(pf.get('person_seniorities', []))}"
            except Exception:
                pass

            keywords = filters.get("q_organization_keyword_tags", [])
            locations = filters.get("organization_locations", ["(any)"])
            sizes = filters.get("organization_num_employees_ranges", ["(any)"])

            # Warn if too few keywords
            keyword_warning = ""
            if len(keywords) < 20:
                keyword_warning = f"\n  ⚠️ Only {len(keywords)} keywords (recommended: 20+). Pipeline may exhaust quickly."

            # ── Create pipeline in pending_approval state ──
            import hashlib as _hl, json as _jn2
            _fh = _hl.sha256(_jn2.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
            preview_run = GatheringRun(
                project_id=project.id,
                company_id=project.company_id,
                source_type=source_type,
                filters=filters,
                filter_hash=_fh,
                status="pending_approval",
                current_phase="pending",
                triggered_by=f"mcp:user:{user.id}",
                target_people=target_people,
                max_people_per_company=max_ppc,
                raw_results_count=total_available,  # Apollo total_entries from probe
            )
            session.add(preview_run)
            await session.flush()

            # Link existing campaign (from align_email_accounts) to this run
            _existing_camp = (await session.execute(
                select(Campaign).where(
                    Campaign.project_id == project.id,
                    Campaign.status == "mcp_draft",
                ).order_by(Campaign.id.desc()).limit(1)
            )).scalar_one_or_none()
            if _existing_camp:
                preview_run.campaign_id = _existing_camp.id
                await session.flush()

            pipeline_link = f"{settings.UI_BASE}/pipeline/{preview_run.id}"

            return {
                "status": "awaiting_filter_confirmation",
                "run_id": preview_run.id,
                "total_available": total_available,
                "filters_preview": {
                    "organization_industry_tag_ids": filters.get("organization_industry_tag_ids"),
                    "q_organization_keyword_tags": keywords,
                    "organization_locations": locations,
                    "organization_num_employees_ranges": sizes,
                    "filter_strategy": filters.get("filter_strategy", "keywords_only"),
                },
                "cost_estimate": cost_est,
                "next_action": {
                    "tool": "tam_gather",
                    "args": {"project_id": project.id, "source_type": source_type, "filters": filters, "confirm_filters": True},
                },
                "message": _build_strategy_message(
                    filters, keywords, locations, sizes, total_available,
                    target_people, max_ppc, cost_est, people_defaults,
                ) + keyword_warning
                + (f"\n\nEmail accounts: {len(_existing_camp.email_account_ids)} selected" if _existing_camp and _existing_camp.email_account_ids else "")
                + f"\nPipeline: {pipeline_link}",
                "_instructions": "Show this preview to user. Ask ONLY: 'Proceed?' Do NOT add any questions about KPIs or target count.",
                "project_id": project.id,
                "project_name": project.name,
                "_links": {"pipeline": pipeline_link},
            }

        # Get user's Apollo service for API sources
        apollo_svc = None
        if "apollo" in source_type:
            ctx = UserServiceContext(user.id, session)
            apollo_svc = await ctx.get_apollo_service()
            if not apollo_svc.is_configured():
                raise ValueError("Apollo not connected. Use configure_integration to add your Apollo API key first.")

        # Extract probe companies before passing filters to gathering (remove internal keys)
        probe_companies = filters.pop("_probe_companies", None)
        probe_page_done = filters.pop("_probe_page_done", 0)

        # If probe already got page 1, tell gathering to start from page 2
        if probe_page_done and probe_companies:
            filters["page_offset"] = probe_page_done + 1
            # Reduce max_pages by 1 since page 1 is already done
            if filters.get("max_pages", 10) > 1:
                filters["max_pages"] = filters.get("max_pages", 10) - 1

        # Call the real gathering service
        from app.services.gathering_service import GatheringService
        svc = GatheringService()
        run = await svc.start_gathering(
            session, project.id, project.company_id,
            source_type, filters, triggered_by=f"mcp:user:{user.id}",
            apollo_service=apollo_svc,
        )

        # Link existing campaign from align_email_accounts (created before tam_gather)
        _existing_camp = (await session.execute(
            select(Campaign).where(
                Campaign.project_id == project.id,
                Campaign.status == "mcp_draft",
            ).order_by(Campaign.id.desc()).limit(1)
        )).scalar_one_or_none()
        if _existing_camp:
            run.campaign_id = _existing_camp.id
            await session.flush()

        # Save probe companies (page 1 was already fetched during preview — don't waste it)
        if probe_companies:
            from app.models.gathering import CompanySourceLink as _CSL
            probe_saved = 0
            for company_data in probe_companies:
                domain = (company_data.get("domain") or "").lower().strip()
                if not domain:
                    continue
                # Dedup against project
                exists = (await session.execute(
                    select(DiscoveredCompany.id).where(
                        DiscoveredCompany.project_id == run.project_id,
                        DiscoveredCompany.domain == domain,
                    )
                )).scalar_one_or_none()
                if exists:
                    run.duplicate_count = (run.duplicate_count or 0) + 1
                    continue
                dc = DiscoveredCompany(
                    project_id=run.project_id,
                    company_id=run.company_id,
                    domain=domain,
                    name=company_data.get("name"),
                    industry=company_data.get("industry"),
                    employee_count=company_data.get("employee_count"),
                    country=company_data.get("country"),
                    city=company_data.get("city"),
                    source_data=company_data,
                )
                session.add(dc)
                await session.flush()
                session.add(_CSL(discovered_company_id=dc.id, gathering_run_id=run.id))
                probe_saved += 1
            run.new_companies_count = (run.new_companies_count or 0) + probe_saved
            await session.flush()
            logger.info(f"Saved {probe_saved} probe companies from preview (page 1 reused)")

        # Track total pages consumed by tam_gather (probe page + remaining pages)
        if "api" in source_type:
            run.pages_fetched = max_pages  # Total pages including probe
            await session.flush()

        # Bug 6: Credit tracking — calculate actual credits used
        credits_spent = run.credits_used or (max_pages if "api" in source_type else 0)
        credits_remaining = None
        if apollo_svc and hasattr(apollo_svc, 'get_credits'):
            try:
                credits_remaining = await apollo_svc.get_credits()
            except Exception:
                pass

        # Build filter summary for the message
        kw_summary = filters.get("q_organization_keyword_tags", [])
        ind_summary = filters.get("organization_industry_tag_ids", [])
        loc_summary = filters.get("organization_locations", [])
        size_summary = filters.get("organization_num_employees_ranges", [])
        strategy_summary = filters.get("filter_strategy", "keywords_only")

        # ── Auto-start streaming pipeline ──
        from app.services.pipeline_orchestrator import start_pipeline_background
        run.target_people = run.target_people or 100
        run.max_people_per_company = run.max_people_per_company or 3
        run.status = "running"
        from datetime import datetime as _dt2, timezone as _tz2
        run.started_at = _dt2.now(_tz2.utc)
        await session.commit()

        start_pipeline_background(run.id, filters, user.id)

        pipeline_link = f"{settings.UI_BASE}/pipeline/{run.id}"
        result = {
            "run_id": run.id,
            "status": "started",
            "new_companies": run.new_companies_count,
            "credits_spent": credits_spent,
            "message": (
                f"**{run.new_companies_count} companies gathered.** Pipeline started!\n\n"
                f"Pipeline: {pipeline_link}\n"
                f"KPIs: {run.target_people} target people, max {run.max_people_per_company}/company.\n"
                f"Credits used so far: {credits_spent}.\n\n"
                f"Pipeline is scraping → classifying → extracting people in parallel.\n"
                f"SmartLead campaign will be created automatically when KPI is hit."
            ),
            "_links": {
                "pipeline": pipeline_link,
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }
        return result

    if tool_name == "tam_blacklist_check":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        from app.services.gathering_service import GatheringService
        svc = GatheringService()
        gate = await svc.blacklist_check(session, run)
        project = await session.get(Project, run.project_id)

        # M7: Trigger background reply analysis IN PARALLEL with blacklist
        try:
            import asyncio as _asyncio
            from app.services.reply_analysis_service import start_background_analysis
            _asyncio.create_task(start_background_analysis(run.project_id, user.id))
            logger.info(f"Background reply analysis started for project {run.project_id}")
        except Exception as e:
            logger.debug(f"Background reply analysis skip: {e}")

        scope = gate.scope or {}
        return {
            "gate_id": gate.id, "type": "checkpoint_1",
            "project_name": project.name if project else "Unknown",
            "scope": scope,
            "message": f"CHECKPOINT 1: Blacklist check complete for project '{project.name if project else 'Unknown'}'. "
                       f"Checked {scope.get('companies_checked', 0)} companies, "
                       f"{scope.get('companies_passed', 0)} passed, "
                       f"{scope.get('companies_rejected', 0)} rejected. "
                       f"Show to user and ask: 'Approve to continue?'",
            "next_action": {
                "tool": "tam_approve_checkpoint",
                "args": {"gate_id": gate.id},
                "description": "User approves → call tam_approve_checkpoint, then tam_pre_filter, tam_scrape, tam_analyze",
            },
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

    if tool_name == "tam_approve_checkpoint":
        user = await _get_user(token, session)
        gate = await session.get(ApprovalGate, args["gate_id"])
        if not gate:
            raise ValueError("Gate not found")
        gate.status = "approved"
        gate.decided_by = f"mcp:user:{user.id}"
        gate.decision_note = args.get("note")
        from datetime import datetime
        gate.decided_at = datetime.utcnow()
        # Advance run phase
        if gate.gathering_run_id:
            run = await session.get(GatheringRun, gate.gathering_run_id)
            if run:
                phase_map = {
                    "awaiting_scope_ok": "pre_filter",
                    "awaiting_targets_ok": "prepare_verification",
                    "awaiting_verify_ok": "verified",
                }
                run.current_phase = phase_map.get(run.current_phase, run.current_phase)
        next_tool = "tam_pre_filter"
        if run:
            next_tool = {"awaiting_scope_ok": "tam_pre_filter", "awaiting_targets_ok": "tam_explore"}.get(run.current_phase, "pipeline_status")
        return {
            "approved": True, "gate_id": gate.id,
            "next_action": {
                "tool": next_tool,
                "args": {"run_id": gate.gathering_run_id},
                "description": f"Approved. Now call {next_tool} to continue the pipeline.",
            },
        }

    if tool_name == "tam_pre_filter":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        from app.services.gathering_service import GatheringService
        svc = GatheringService()
        result = await svc.pre_filter(session, run)
        return {
            "status": "pre_filter_complete", "run_id": run.id,
            "passed": result["passed"], "filtered": result["filtered"],
            "message": f"Pre-filter done: {result['passed']} passed, {result['filtered']} removed.",
            "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
        }

    if tool_name == "tam_scrape":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        from app.services.gathering_service import GatheringService
        from app.services.scraper_service import ScraperService
        svc = GatheringService()
        scraper = ScraperService()
        result = await svc.scrape(session, run, scraper_service=scraper)
        return {
            "status": "scrape_complete", "run_id": run.id,
            "scraped": result["scraped"], "errors": result["errors"], "total": result["total"],
            "message": f"Scraped {result['scraped']}/{result['total']} websites ({result['errors']} errors).",
            "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
        }

    if tool_name == "tam_analyze":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        # Get OpenAI key for GPT-4o-mini analysis (cheap workhorse)
        ctx = UserServiceContext(user.id, session)
        openai_key = await ctx.get_key("openai")
        if not openai_key:
            from app.config import settings
            openai_key = settings.OPENAI_API_KEY
        from app.services.gathering_service import GatheringService
        svc = GatheringService()

        # Multi-step prompt chain support
        prompt_steps = args.get("prompt_steps")
        if prompt_steps and isinstance(prompt_steps, list) and len(prompt_steps) > 0:
            gate = await svc.analyze_multi_step(
                session, run,
                prompt_steps=prompt_steps,
                openai_key=openai_key,
            )
        else:
            gate = await svc.analyze(
                session, run,
                prompt_text=args.get("prompt_text"),
                auto_refine=args.get("auto_refine", False),
                target_accuracy=args.get("target_accuracy", 0.9),
                openai_key=openai_key,
            )
        scope = gate.scope or {}
        return {
            "gate_id": gate.id, "type": "checkpoint_2",
            "targets_found": scope.get("targets_found", 0),
            "total_analyzed": scope.get("total_analyzed", 0),
            "skipped_no_text": scope.get("skipped_no_scraped_text", 0),
            "target_rate": scope.get("target_rate", "0%"),
            # confidence excluded per requirements line 79
            "segment_distribution": scope.get("segment_distribution", {}),
            "target_list": scope.get("target_list", []),
            "borderline_rejections": scope.get("borderline_rejections", []),
            # P0-5: Check if enough targets for 100 contacts (34 companies × 3 contacts)
            "targets_sufficient": scope.get("targets_found", 0) >= 34,
            "contacts_estimate": scope.get("targets_found", 0) * 3,
            # P1-9: Always suggest exploration when there are any targets
            "suggest_exploration": scope.get("targets_found", 0) >= 1,
            "message": (
                f"CHECKPOINT 2: Analyzed {scope.get('total_analyzed', 0)} companies. "
                f"TARGETS: {scope.get('targets_found', 0)} ({scope.get('target_rate', '0%')} target rate). "
                f"Segments: {scope.get('segment_distribution', {})}.\n\n"
                + (f"Enough targets for ≈{scope.get('targets_found', 0) * 3} contacts (need 100 minimum).\n"
                   if scope.get("targets_found", 0) >= 34
                   else f"Only {scope.get('targets_found', 0)} targets (≈{scope.get('targets_found', 0) * 3} contacts). Need at least 34 targets for 100 contacts. Consider exploring with broader filters.\n")
                + f"\nNext steps:\n"
                + (f"0. GATHER MORE — not enough targets. Call tam_gather with same filters + confirm_filters=true + page_offset={run.filters.get('max_pages', 2) + 1} to get next batch from Apollo.\n"
                   if scope.get("targets_found", 0) < 34 else "")
                + f"1. Approve targets → call tam_approve_checkpoint\n"
                + f"2. Run exploration to discover better filters → call tam_explore (5 credits, finds more relevant keywords)\n"
                + f"3. Re-analyze with adjusted prompt → call tam_re_analyze\n"
                + f"4. If accuracy < 90%, provide feedback → call provide_feedback"
            ),
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

    if tool_name == "tam_explore":
        # Exploration: enrich top 5 targets → discover real Apollo keywords → suggest optimized filters
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")

        project = await session.get(Project, run.project_id)
        ctx = UserServiceContext(user.id, session)
        apollo_key_raw = await ctx.get_key("apollo")
        openai_key = await ctx.get_key("openai")
        if not apollo_key_raw:
            from app.config import settings as _cfg
            apollo_key_raw = _cfg.APOLLO_API_KEY
        if not openai_key:
            from app.config import settings as _cfg
            openai_key = _cfg.OPENAI_API_KEY
        if not apollo_key_raw:
            raise ValueError("Apollo not connected — needed for enrichment")

        import os
        apify_proxy = os.environ.get("APIFY_PROXY_PASSWORD")

        from app.services.exploration_service import run_exploration
        exploration_result = await run_exploration(
            query=project.target_segments or "",
            initial_filters=run.filters or {},
            offer_text=project.target_segments or "",
            apollo_key=apollo_key_raw,
            openai_key=openai_key,
            apify_proxy_password=apify_proxy,
        )

        # Taxonomy update now happens inside exploration_service.run_exploration()
        # (uses singleton taxonomy_service, full enrichment data, rebuilds embeddings)
        new_kw = exploration_result.get("exploration_stats", {}).get("new_keywords_added", 0)
        if new_kw:
            logger.info(f"Taxonomy grew by +{new_kw} keywords from exploration enrichment")

        optimized = exploration_result.get("optimized_filters", run.filters)
        credits_used = exploration_result.get("credits_used", 0)
        initial_count = exploration_result.get("exploration_stats", {}).get("initial_companies", 0)
        targets_found = exploration_result.get("exploration_stats", {}).get("targets_identified", 0)

        return {
            "optimized_filters": optimized,
            "exploration_stats": exploration_result.get("exploration_stats", {}),
            "credits_used": credits_used,
            "message": (
                f"Exploration complete ({credits_used} credits):\n"
                f"  Initial search: {initial_count} companies\n"
                f"  Targets identified: {targets_found}\n"
                f"  Enriched top {min(5, targets_found)} → discovered real Apollo keywords\n\n"
                f"Optimized filters:\n"
                f"  Keywords: {optimized.get('q_organization_keyword_tags', [])}\n"
                f"  Location: {optimized.get('organization_locations', [])}\n"
                f"  Size: {optimized.get('organization_num_employees_ranges', [])}\n\n"
                f"Re-search with these optimized filters? Call tam_gather with these filters and confirm_filters=true."
            ),
            "run_id": run.id,
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

    if tool_name == "tam_enrich_from_examples":
        # Case: user provides example companies/file → enrich in Apollo → extract filters
        user = await _get_user(token, session)
        domains = args.get("domains", [])
        if not domains:
            raise ValueError("Provide a list of example company domains to reverse-engineer filters from")

        ctx = UserServiceContext(user.id, session)
        apollo_key_raw = await ctx.get_key("apollo")
        openai_key = await ctx.get_key("openai")
        if not apollo_key_raw:
            from app.config import settings as _cfg
            apollo_key_raw = _cfg.APOLLO_API_KEY
        if not apollo_key_raw:
            raise ValueError("Apollo not connected — needed for enrichment")

        # Enrich each domain in Apollo to get their real labels
        from app.services.exploration_service import _enrich_targets
        example_companies = [{"domain": d, "name": d.split(".")[0]} for d in domains[:10]]
        enriched = await _enrich_targets(apollo_key_raw, example_companies)

        from app.services.exploration_service import _extract_common_labels, _build_optimized_filters
        common_labels = _extract_common_labels(enriched)

        # If no segment_description, infer from enriched data
        seg_desc = args.get("segment_description", "")
        if not seg_desc:
            seg_desc = ", ".join(common_labels.get("industries", [])[:3]) or "target companies"

        # Build filters from the examples
        optimized = await _build_optimized_filters(
            {"organization_locations": args.get("locations", [])},
            common_labels,
            seg_desc,
            openai_key or "",
        )

        return {
            "filters_from_examples": optimized,
            "common_labels": common_labels,
            "enriched_count": len(enriched),
            "credits_used": len(enriched),
            "message": (
                f"Reverse-engineered filters from {len(enriched)} example companies:\n"
                f"  Keywords: {optimized.get('q_organization_keyword_tags', [])}\n"
                f"  Industries: {common_labels.get('industries', [])}\n\n"
                f"Use these filters with tam_gather to find similar companies."
            ),
        }

    # ── Define Targets (god tool — examples + description + enrichment) ──
    if tool_name == "define_targets":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        segment_desc = args.get("segment_description")
        example_domains = args.get("example_domains", [])
        locations = args.get("locations", [])
        employee_range = args.get("employee_range")
        skip_exploration = args.get("skip_exploration", bool(example_domains))

        # Determine which case
        has_examples = bool(example_domains)
        has_description = bool(segment_desc)

        if not has_examples and not has_description:
            raise ValueError(
                "Provide at least one of:\n"
                "  - segment_description: who are the targets? (e.g. 'IT consulting in Miami')\n"
                "  - example_domains: example target company domains (e.g. ['stripe.com'])\n\n"
                "Or both for best results."
            )

        # TIER 1: Preview before executing
        if not args.get("confirm"):
            case_label = (
                "CASE 4: Examples + Description (best)" if has_examples and has_description
                else "CASE 1: Examples with domains → enrich → extract filters" if has_examples
                else "CASE 3: Segment description → normal pipeline with exploration"
            )
            preview_msg = f"I will define targets for project '{project.name}':\n\n"
            preview_msg += f"  Mode: {case_label}\n"
            if has_description:
                preview_msg += f"  Segment: {segment_desc}\n"
            if has_examples:
                preview_msg += f"  Examples: {', '.join(example_domains[:5])}" + (f" (+{len(example_domains)-5} more)" if len(example_domains) > 5 else "") + "\n"
                preview_msg += f"  Will enrich {len(example_domains)} companies in Apollo ({len(example_domains)} credits)\n"
                preview_msg += f"  Exploration phase: SKIP (filters from examples)\n"
            else:
                preview_msg += f"  Exploration phase: will run (1 page + enrich top 5)\n"
            if locations:
                preview_msg += f"  Locations: {', '.join(locations)}\n"
            if employee_range:
                preview_msg += f"  Size: {employee_range}\n"
            preview_msg += "\nApprove?"

            return {
                "status": "awaiting_confirmation",
                "preview": {
                    "case": case_label,
                    "has_examples": has_examples,
                    "has_description": has_description,
                    "example_count": len(example_domains),
                    "skip_exploration": skip_exploration,
                },
                "message": preview_msg,
                "next_action": {"tool": "define_targets", "args": {**args, "confirm": True}},
            }

        # ── Execute ──
        result = {"project_id": project.id, "project_name": project.name}

        # Store segment description on project
        if has_description:
            project.target_segments = segment_desc
            result["segment_stored"] = True

        # Store locations
        if locations:
            project.target_industries = ", ".join(locations)  # reuse field for geo

        # Enrich examples if provided
        enriched_filters = None
        if has_examples:
            ctx = UserServiceContext(user.id, session)
            apollo_key = await ctx.get_key("apollo")
            openai_key = await ctx.get_key("openai")
            if not apollo_key:
                from app.config import settings as _cfg
                apollo_key = _cfg.APOLLO_API_KEY
            if not apollo_key:
                raise ValueError("Apollo not connected — needed for enrichment")

            from app.services.exploration_service import _enrich_targets, _extract_common_labels, _build_optimized_filters
            example_companies = [{"domain": d.strip(), "name": d.split(".")[0]} for d in example_domains[:10]]

            # Step 1: Scrape example websites (real signal > Apollo labels)
            scraped_texts = {}
            try:
                from app.services.scraper_service import ScraperService
                import os
                scraper = ScraperService(apify_proxy_password=os.environ.get("APIFY_PROXY_PASSWORD"))
                scrape_tasks = [scraper.scrape_website(f"https://{d.strip()}") for d in example_domains[:10]]
                import asyncio as _aio
                scrape_results = await _aio.gather(*scrape_tasks, return_exceptions=True)
                for domain, sr in zip(example_domains[:10], scrape_results):
                    if isinstance(sr, dict) and sr.get("success"):
                        scraped_texts[domain.strip()] = sr["text"][:2000]
                logger.info(f"Scraped {len(scraped_texts)}/{len(example_domains[:10])} example websites")
            except Exception as _e:
                logger.warning(f"Example scraping failed: {_e}")

            # Step 2: Enrich in Apollo (keyword_tags, industries)
            enriched = await _enrich_targets(apollo_key, example_companies)
            common_labels = _extract_common_labels(enriched)

            # Step 3: Infer segment from SCRAPED TEXT + APOLLO DATA (if no description)
            filter_query = segment_desc
            if not filter_query and openai_key:
                try:
                    import httpx as _hx
                    # Build rich summary: scraped text + apollo labels
                    company_summaries = []
                    for e in enriched[:10]:
                        domain = e.get("domain", "?")
                        apollo_info = f"industry={e.get('enriched', {}).get('industry', '?')}, keywords={e.get('enriched', {}).get('keywords', [])[:5]}"
                        website_text = scraped_texts.get(domain, "")[:500]
                        summary = f"- {domain}: {apollo_info}"
                        if website_text:
                            summary += f"\n  Website: {website_text[:300]}"
                        company_summaries.append(summary)

                    async with _hx.AsyncClient(timeout=20) as _c:
                        _resp = await _c.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": (
                                    f"These companies are examples of the user's target market:\n"
                                    + "\n".join(company_summaries) + "\n\n"
                                    f"Based on their WEBSITES and Apollo data, in ONE sentence describe:\n"
                                    f"What business segment do these companies belong to? What do they DO, "
                                    f"who are their CUSTOMERS, what specific industry/niche?"
                                )}],
                                "max_tokens": 150, "temperature": 0,
                            },
                        )
                        _data = _resp.json()
                        filter_query = _data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        logger.info(f"Inferred segment from examples (scraped+apollo): {filter_query}")
                        result["inferred_segment"] = filter_query
                except Exception as _e:
                    logger.warning(f"Segment inference failed: {_e}")
                    filter_query = ", ".join(common_labels.get("industries", [])[:3])

            result["scraped_count"] = len(scraped_texts)

            enriched_filters = await _build_optimized_filters(
                {"organization_locations": locations, "organization_num_employees_ranges": [employee_range] if employee_range else []},
                common_labels,
                filter_query or ", ".join(common_labels.get("industries", [])[:3]),
                openai_key or "",
            )

            result["enriched_count"] = len(enriched)
            result["credits_used"] = len(enriched)
            result["filters_from_examples"] = enriched_filters
            result["common_labels"] = common_labels
            result["filter_query_used"] = filter_query
            result["skip_exploration"] = True

            # Store inferred/provided segment on project
            if not project.target_segments:
                project.target_segments = filter_query or f"Companies in: {', '.join(common_labels.get('industries', [])[:5])}"

        await session.commit()

        # Build message
        msg = f"Target definition saved for '{project.name}'.\n\n"
        if has_description:
            msg += f"  Segment: {segment_desc}\n"
        if enriched_filters:
            msg += f"  Enriched {result['enriched_count']} examples ({result['credits_used']} Apollo credits)\n"
            msg += f"  Keywords: {enriched_filters.get('q_organization_keyword_tags', [])}\n"
            msg += f"  Exploration: SKIP (filters from examples)\n"
        msg += f"\nNext: call tam_gather with these filters to start pipeline."

        result["message"] = msg
        result["_links"] = {
            "project": f"{settings.UI_BASE}/projects/{project.id}",
            "pipelines": "{settings.UI_BASE}/pipeline",
        }
        if enriched_filters:
            result["next_action"] = {
                "tool": "tam_gather",
                "args": {
                    "project_id": project.id,
                    "source_type": "apollo.companies.api",
                    "filters": enriched_filters,
                },
                "description": "Start gathering with enriched filters. Will skip exploration phase.",
            }

        return result

    if tool_name == "tam_re_analyze":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)

        # Count companies that will be re-analyzed
        from app.models.gathering import CompanySourceLink as _CSL
        company_count = (await session.execute(
            select(sa_func.count(DiscoveredCompany.id))
            .join(_CSL, _CSL.discovered_company_id == DiscoveredCompany.id)
            .where(_CSL.gathering_run_id == run.id)
        )).scalar() or 0

        # TIER 1: Preview before executing
        if not args.get("confirm"):
            prompt_preview = args.get("prompt_text", "(auto-generated from feedback)")[:200]
            return {
                "status": "awaiting_confirmation",
                "preview": {
                    "action": "tam_re_analyze",
                    "run_id": run.id,
                    "project": project.name if project else "Unknown",
                    "companies": company_count,
                    "prompt_preview": prompt_preview,
                },
                "message": (
                    f"I will re-analyze ALL {company_count} companies in pipeline #{run.id} ({project.name if project else 'Unknown'}).\n"
                    f"Same companies, new classification prompt.\n"
                    f"Prompt: {prompt_preview}...\n\n"
                    f"Previous results will be preserved for comparison (new iteration in UI).\n\n"
                    f"Approve?"
                ),
                "next_action": {"tool": "tam_re_analyze", "args": {**args, "confirm": True}},
            }

        # Reset to scraped phase for re-analysis
        run.current_phase = "analyze"
        # Reject current CP2 gate
        from sqlalchemy import update
        await session.execute(
            update(ApprovalGate)
            .where(ApprovalGate.gathering_run_id == run.id, ApprovalGate.gate_type == "checkpoint_2", ApprovalGate.status == "pending")
            .values(status="rejected", decision_note="Re-analyzing with adjusted prompt")
        )

        ctx = UserServiceContext(user.id, session)
        openai_key = await ctx.get_key("openai")
        if not openai_key:
            from app.config import settings
            openai_key = settings.OPENAI_API_KEY

        # P1-8: If user provided feedback/verdicts, use prompt_tuner to improve prompt first
        agent_verdicts = args.get("agent_verdicts")  # {domain: {target: bool, reason: str}}
        if agent_verdicts and openai_key:
            try:
                from app.services.prompt_tuner import tune_classification_prompt
                # Get companies with scraped text for tuning
                from app.models.gathering import CompanySourceLink
                from app.models.pipeline import CompanyScrape
                companies_for_tuning = []
                company_results = await session.execute(
                    select(DiscoveredCompany, CompanyScrape)
                    .outerjoin(CompanyScrape, CompanyScrape.discovered_company_id == DiscoveredCompany.id)
                    .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
                    .where(CompanySourceLink.gathering_run_id == run.id, CompanyScrape.is_current == True)
                )
                for dc, scrape in company_results.all():
                    companies_for_tuning.append({
                        "domain": dc.domain,
                        "name": dc.name or dc.domain,
                        "scraped_text": scrape.clean_text[:3000] if scrape and scrape.clean_text else "",
                    })
                if companies_for_tuning:
                    project = await session.get(Project, run.project_id)
                    tuned_prompt, accuracy, iterations = await tune_classification_prompt(
                        companies=companies_for_tuning,
                        agent_verdicts=agent_verdicts,
                        offer=project.target_segments or "",
                        query=project.target_segments or "",
                        openai_key=openai_key,
                    )
                    logger.info(f"Prompt tuner: {accuracy*100:.0f}% accuracy after {iterations} iterations")
                    args["prompt_text"] = tuned_prompt
            except Exception as e:
                logger.warning(f"Prompt tuner failed, using manual prompt: {e}")

        # Re-run analysis with new/tuned prompt
        from app.services.gathering_service import GatheringService
        svc = GatheringService()
        prompt_text = args.get("prompt_text")
        prompt_steps = args.get("prompt_steps")
        if prompt_steps and isinstance(prompt_steps, list):
            gate = await svc.analyze_multi_step(session, run, prompt_steps=prompt_steps, openai_key=openai_key)
        else:
            gate = await svc.analyze(session, run, prompt_text=prompt_text, openai_key=openai_key)

        # Create PipelineIteration record for tracking
        try:
            from app.models.processing_step import PipelineIteration
            from sqlalchemy import func as sa_func
            existing_iters = (await session.execute(
                select(sa_func.count(PipelineIteration.id)).where(PipelineIteration.gathering_run_id == run.id)
            )).scalar() or 0
            iteration = PipelineIteration(
                project_id=run.project_id,
                gathering_run_id=run.id,
                iteration_number=existing_iters + 1,
                label=f"Re-analysis #{existing_iters + 1}" + (f" (tuned prompt)" if agent_verdicts else ""),
                trigger="re_analyze",
                steps_snapshot=[],
                filters_snapshot=run.filters,
                prompt_snapshot=prompt_text[:3000] if prompt_text else None,
                target_count=gate.scope.get("targets_found", 0) if gate.scope else 0,
                target_rate=float(gate.scope.get("target_rate", "0").replace("%", "")) / 100 if gate.scope else 0,
            )
            session.add(iteration)
        except Exception as e:
            logger.debug(f"PipelineIteration tracking failed: {e}")
        scope = gate.scope or {}
        return {
            "gate_id": gate.id, "type": "checkpoint_2_retry",
            "targets_found": scope.get("targets_found", 0),
            "total_analyzed": scope.get("total_analyzed", 0),
            "target_rate": scope.get("target_rate", "0%"),
            # confidence excluded per requirements line 79
            "segment_distribution": scope.get("segment_distribution", {}),
            "target_list": scope.get("target_list", []),
            "borderline_rejections": scope.get("borderline_rejections", []),
            "message": (
                f"Re-analysis complete. Classified {scope.get('total_analyzed', 0)} companies. "
                f"TARGETS: {scope.get('targets_found', 0)} ({scope.get('target_rate', '0%')}). "
                f"Segments: {scope.get('segment_distribution', {})}. "
                f"Review the updated target list."
            ),
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

    if tool_name == "tam_list_sources":
        from app.services.gathering_adapters.source_router import list_sources
        return {"sources": list_sources()}

    # ── Refinement tools ──
    if tool_name == "refinement_status":
        user = await _get_user(token, session)
        from app.models.refinement import RefinementRun, RefinementIteration
        run = await session.get(RefinementRun, args["run_id"])
        if not run:
            raise ValueError("Refinement run not found")
        result = await session.execute(
            select(RefinementIteration).where(RefinementIteration.refinement_run_id == run.id)
            .order_by(RefinementIteration.iteration_number)
        )
        iterations = result.scalars().all()
        return {
            "status": run.status, "current_iteration": run.current_iteration,
            "target_accuracy": run.target_accuracy, "final_accuracy": run.final_accuracy,
            "iterations": [{"n": i.iteration_number, "accuracy": i.accuracy,
                           "fp": i.false_positives, "fn": i.false_negatives} for i in iterations],
        }

    if tool_name == "refinement_override":
        user = await _get_user(token, session)
        from app.models.refinement import RefinementRun
        run = await session.get(RefinementRun, args["refinement_run_id"])
        if not run:
            raise ValueError("Refinement run not found")
        run.status = "stopped"
        from datetime import datetime
        run.completed_at = datetime.utcnow()
        return {"stopped": True, "final_accuracy": run.final_accuracy}

    # ── Campaign Sequence tools ──
    if tool_name == "smartlead_generate_sequence":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project:
            raise ValueError("Project not found")
        from app.services.campaign_intelligence import CampaignIntelligenceService
        ci_svc = CampaignIntelligenceService()
        seq = await ci_svc.generate_sequence(
            session, project.id,
            campaign_name=args.get("campaign_name"),
            instructions=args.get("instructions"),
        )
        # Show sequence preview
        steps_preview = []
        for s in seq.sequence_steps:
            steps_preview.append(f"Step {s['step']} (Day {s['day']}): {s['subject']}")
        return {
            "sequence_id": seq.id,
            "campaign_name": seq.campaign_name,
            "steps": seq.sequence_step_count,
            "status": "draft",
            "rationale": seq.rationale,
            "preview": steps_preview,
            "message": f"Generated 5-step sequence '{seq.campaign_name}'. Review and approve, or request changes.",
            "_links": {"sequence": f"{settings.UI_BASE}/campaigns/{seq.id}"},
        }

    if tool_name == "smartlead_approve_sequence":
        user = await _get_user(token, session)
        seq = await session.get(GeneratedSequence, args["sequence_id"])
        if not seq:
            raise ValueError("Sequence not found")
        seq.status = "approved"
        seq.reviewed_by = f"mcp:user:{user.id}"
        from datetime import datetime
        seq.reviewed_at = datetime.utcnow()
        return {"approved": True, "sequence_id": seq.id}

    if tool_name == "list_email_accounts":
        user = await _get_user(token, session)
        from sqlalchemy import text as _st

        # Use cache first (instant). Fallback to API if cache empty.
        cache_result = await session.execute(_st(
            f"SELECT COUNT(*) FROM smartlead_accounts_cache WHERE user_id={user.id}"
        ))
        cache_count = cache_result.scalar() or 0

        if cache_count == 0:
            # Cache empty — populate it now
            ctx = UserServiceContext(user.id, session)
            svc = await ctx.get_smartlead_service()
            if svc.is_configured():
                all_accounts = await svc.get_email_accounts()
                for a in all_accounts:
                    await session.execute(_st(
                        "INSERT INTO smartlead_accounts_cache (user_id, account_id, from_email, from_name) "
                        "VALUES (:uid, :aid, :email, :name) ON CONFLICT (user_id, account_id) DO NOTHING"
                    ), {"uid": user.id, "aid": a.get("id"), "email": a.get("from_email", ""), "name": a.get("from_name", "")})
                await session.flush()
                cache_count = len(all_accounts)

        # NEVER dump all accounts. Return count + link.
        return {
            "total": cache_count,
            "message": f"{cache_count} email accounts available. Tell me a name/email pattern (e.g. 'all with rinat') and I'll find matching accounts.",
            "accounts_link": f"{settings.UI_BASE}/campaigns/accounts",
        }

    if tool_name == "align_email_accounts":
        user = await _get_user(token, session)

        # Accept project_id OR run_id (run may not exist yet in new flow)
        project_id = args.get("project_id")
        run_id = args.get("run_id")
        run = None
        project = None

        if run_id:
            run = await session.get(GatheringRun, run_id)
            if run:
                project = await session.get(Project, run.project_id)
        if not project and project_id:
            project = await session.get(Project, project_id)
        if not project:
            # Fallback: active project
            if user.active_project_id:
                project = await session.get(Project, user.active_project_id)
        if not project:
            raise ValueError("project_id or run_id required")
        if project.user_id != user.id:
            raise ValueError("Project not found")

        # Load from cache (instant) or fallback to API
        from sqlalchemy import text as _st2
        cache = await session.execute(_st2(
            f"SELECT account_id, from_email, from_name FROM smartlead_accounts_cache WHERE user_id={user.id}"
        ))
        cache_rows = cache.fetchall()
        if cache_rows:
            accounts_list = [{"id": r[0], "email": r[1] or "", "name": r[2] or ""} for r in cache_rows]
        else:
            ctx = UserServiceContext(user.id, session)
            svc = await ctx.get_smartlead_service()
            if not svc.is_configured():
                raise ValueError("SmartLead not connected")
            all_accounts = await svc.get_email_accounts() or []
            accounts_list = [
                {"id": a.get("id"), "email": a.get("from_email") or a.get("email", ""), "name": a.get("from_name", "")}
                for a in all_accounts
            ]

        # Filter by user query or explicit IDs
        account_ids = args.get("account_ids")
        account_filter = args.get("account_filter", "")
        if account_ids:
            matched = [a for a in accounts_list if a["id"] in account_ids]
        elif account_filter:
            # Strip common filler words — user says "all accounts with john in name"
            STOP_WORDS = {"all", "with", "in", "the", "my", "use", "accounts", "account", "emails", "email", "name", "named", "from", "containing", "that", "have", "called"}
            terms = [t.strip().lower() for t in account_filter.lower().split() if t.strip() and t.strip().lower() not in STOP_WORDS]
            if not terms:
                terms = [account_filter.strip().lower()]  # fallback: use whole string
            def _matches(a):
                combined = (a["email"] + " " + a["name"]).lower()
                return all(t in combined for t in terms)
            matched = [a for a in accounts_list if _matches(a)]
        else:
            matched = accounts_list

        if not matched:
            return {
                "matched": [],
                "total_available": len(accounts_list),
                "message": f"No accounts match '{account_filter}'. Available: {', '.join(a['email'] for a in accounts_list[:10])}",
            }

        # Preview step
        if not args.get("confirm"):
            return {
                "matched": matched[:20],  # Show first 20 in preview
                "count": len(matched),
                "message": f"Found {len(matched)} matching accounts. Confirm to create draft campaign.",
                "_instructions": "Show account count and ask: 'Confirm these {N} accounts?' ONE question.",
            }

        # Confirm step — create mcp_draft campaign linked to PROJECT (run linked later)
        campaign_name = args.get("campaign_name") or f"{project.name} Campaign"

        campaign = Campaign(
            project_id=project.id,
            company_id=project.company_id,
            name=campaign_name,
            platform="smartlead",
            status="mcp_draft",
            created_by="mcp",
            email_account_ids=matched,
        )
        session.add(campaign)
        await session.flush()

        # Link to run if it exists
        if run:
            run.campaign_id = campaign.id

        return {
            "campaign_id": campaign.id,
            "project_id": project.id,
            "status": "mcp_draft",
            "email_accounts_count": len(matched),
            "message": (
                f"Campaign '{campaign_name}' created with {len(matched)} email accounts.\n"
                f"Next: describe your target segment (e.g. 'fashion brands in Italy up to 200 employees')."
            ),
            "_instructions": "Ask user to describe their target segment for tam_gather. ONE question.",
        }

    if tool_name == "check_destination":
        # M1: When both SmartLead and GetSales keys present, ask which platform
        user = await _get_user(token, session)
        ctx = UserServiceContext(user.id, session)
        sl_configured = (await ctx.get_smartlead_service()).is_configured()
        gs_key = await ctx.get_key("getsales")
        gs_configured = bool(gs_key)

        if sl_configured and gs_configured:
            return {
                "both_configured": True,
                "question": "destination_selection",
                "message": "You have both SmartLead (email) and GetSales (LinkedIn) connected. Which platform should we use?",
                "options": ["SmartLead (email outreach)", "GetSales (LinkedIn outreach)", "Both"],
            }
        elif sl_configured:
            return {"destination": "smartlead", "message": "SmartLead configured. Will push email campaign."}
        elif gs_configured:
            return {"destination": "getsales", "message": "GetSales configured. Will push LinkedIn flow."}
        else:
            raise ValueError("No outreach platform configured. Connect SmartLead or GetSales in Setup.")

    if tool_name == "smartlead_push_campaign":
        user = await _get_user(token, session)
        from app.models.gathering import CompanySourceLink
        seq = await session.get(GeneratedSequence, args["sequence_id"])
        if not seq:
            raise ValueError("Sequence not found")
        if seq.status != "approved":
            raise ValueError("Sequence must be approved first")

        # M5: Require email accounts — agent MUST call list_email_accounts first
        if not args.get("email_account_ids"):
            raise ValueError("email_account_ids required. Call list_email_accounts first and ask the user which accounts to use.")

        ctx = UserServiceContext(user.id, session)
        svc = await ctx.get_smartlead_service()
        if not svc.is_configured():
            raise ValueError("SmartLead not connected")

        # 1. Create campaign
        campaign_data = await svc.create_campaign(seq.campaign_name or "MCP Generated")
        if not campaign_data:
            raise ValueError("Failed to create SmartLead campaign")
        campaign_id = campaign_data.get("id")

        # 2. Set sequences
        await svc.set_campaign_sequences(campaign_id, seq.sequence_steps)

        # 3. Set production settings (M3: match reference campaign 3070919)
        # Hardcoded in SmartLeadService: no tracking, plain text, stop on reply, 40% follow-up
        await svc.set_campaign_settings(campaign_id)

        # 4. Set schedule (M2: 9-18 in target contact timezone)
        target_country = args.get("target_country", "")
        if not target_country:
            # Try to get from project's gathering filters or gathered contact geo
            project = await session.get(Project, seq.project_id)
            if project and project.target_segments:
                for country in ["United States", "Germany", "United Kingdom", "India", "Australia", "UAE", "Canada", "France", "Netherlands", "Switzerland"]:
                    if country.lower() in (project.target_segments or "").lower():
                        target_country = country
                        break
            # Also check gathered contacts' most common country
            if not target_country:
                from sqlalchemy import func as sa_func
                geo_result = await session.execute(
                    select(DiscoveredCompany.country, sa_func.count(DiscoveredCompany.id))
                    .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
                    .where(DiscoveredCompany.is_target == True, DiscoveredCompany.country.isnot(None))
                    .group_by(DiscoveredCompany.country)
                    .order_by(sa_func.count(DiscoveredCompany.id).desc())
                    .limit(1)
                )
                top_country = geo_result.first()
                if top_country:
                    target_country = top_country[0]
        from app.services.smartlead_service import get_timezone_for_country
        timezone = get_timezone_for_country(target_country or "United States")
        await svc.set_campaign_schedule(campaign_id, timezone)

        # 5. Assign email accounts — MUST be provided by user
        email_account_ids = args.get("email_account_ids", [])
        if email_account_ids:
            await svc.set_campaign_email_accounts(campaign_id, email_account_ids)
        # If no accounts provided, campaign stays without accounts — agent must ask user

        # 6. Save to DB
        from datetime import datetime
        campaign = Campaign(
            project_id=seq.project_id, company_id=seq.company_id,
            name=seq.campaign_name, external_id=str(campaign_id),
            platform="smartlead", status="draft",
        )
        session.add(campaign)
        seq.pushed_at = datetime.utcnow()
        seq.status = "pushed"
        seq.pushed_campaign_id = campaign.id
        await session.flush()

        smartlead_url = f"https://app.smartlead.ai/app/email-campaigns-v2/{campaign_id}/analytics"

        # 7. Upload target contacts to SmartLead campaign
        leads_uploaded = 0
        try:
            contacts_result = await session.execute(
                select(ExtractedContact, DiscoveredCompany)
                .outerjoin(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
                .where(
                    ExtractedContact.project_id == seq.project_id,
                    DiscoveredCompany.is_target == True,
                    ExtractedContact.email.isnot(None),
                )
            )
            contacts = contacts_result.all()
            if contacts:
                lead_list = []
                for contact, company in contacts:
                    lead = {
                        "email": contact.email,
                        "first_name": contact.first_name or "",
                        "last_name": contact.last_name or "",
                        "company_name": company.name if company else "",  # normalized name
                        "custom_fields": {
                            "segment": company.analysis_segment if company else "",
                            "source_company_name": (company.source_data or {}).get("source_company_name", "") if company else "",
                            "domain": company.domain if company else "",
                            "pipeline_run": str(seq.project_id),
                        },
                    }
                    lead_list.append(lead)
                if lead_list:
                    await svc.add_leads_to_campaign(campaign_id, lead_list)
                    leads_uploaded = len(lead_list)
                    campaign.leads_count = leads_uploaded
        except Exception as e:
            logger.warning(f"Failed to upload contacts to SmartLead: {e}")

        # 7b. Add test leads ONLY when the user is a known test account
        _TEST_ACCOUNTS = {"test@example.com"}
        if user.email in _TEST_ACCOUNTS:
            test_leads = [
                {"email": "test@example.com", "first_name": "Test", "last_name": "User", "company_name": "TEST - DELETE", "custom_fields": {"is_test_lead": "true"}},
            ]
            try:
                await svc.add_leads_to_campaign(campaign_id, test_leads)
                leads_uploaded += len(test_leads)
            except Exception as e:
                logger.debug(f"Test leads add failed: {e}")

        # 8. Auto-send test email to the user's own email
        test_email_result = None
        try:
            test_email_result = await svc.send_test_email(
                campaign_id=campaign_id,
                test_email=user.email,
                sequence_number=1,
            )
            logger.info(f"Test email sent to {user.email} for campaign {campaign_id}: {test_email_result}")
        except Exception as e:
            logger.warning(f"Auto test email failed for campaign {campaign_id}: {e}")
            test_email_result = {"ok": False, "error": str(e)}

        return {
            "pushed": True,
            "smartlead_campaign_id": campaign_id,
            "status": "DRAFT",
            "settings": {
                "timezone": timezone,
                "schedule": "Mon-Fri 09:00-18:00",
                "plain_text": True,
                "tracking": "disabled (no open/click tracking)",
                "stop_on": "reply",
                "follow_up_rate": "40%",
                "max_daily": 100,
                "email_accounts": len(email_account_ids),
            },
            "test_email": test_email_result,
            "user_email": user.email,
            "message": (
                f"Campaign '{seq.campaign_name}' created as DRAFT.\n\n"
                f"SmartLead: {smartlead_url}\n"
                f"Schedule: Mon-Fri 9:00-18:00 {timezone}\n"
                f"Email accounts: {len(email_account_ids)} assigned\n"
                f"Leads uploaded: {leads_uploaded} target contacts\n\n"
                + (f"Check your inbox at {user.email} — test email sent!\n\n" if test_email_result and test_email_result.get("ok") else
                   f"Test email could not be sent ({test_email_result.get('error', 'no email accounts') if test_email_result else 'no accounts assigned'}). Assign email accounts first.\n\n")
                + f"I'll launch after your approval. Before activating, you can:\n"
                + f"- Review the sequence in SmartLead\n"
                + f"- Edit any email step (tell me which to change)\n"
                + f"- Override target companies (tell me which to add/remove)\n"
                + f"- Provide feedback on the companies or sequence\n\n"
                + f"Once satisfied, say 'activate' to start sending."
            ),
            "_links": {
                "smartlead": smartlead_url,
                "campaigns": "{settings.UI_BASE}/campaigns",
                "crm": f"{settings.UI_BASE}/crm?campaign={seq.campaign_name or ''}",
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id if 'run' in dir() else ''}",
            },
        }

    if tool_name == "send_test_email":
        user = await _get_user(token, session)
        ctx = UserServiceContext(user.id, session)
        svc = await ctx.get_smartlead_service()
        if not svc.is_configured():
            raise ValueError("SmartLead not connected")
        test_email = args.get("test_email") or user.email
        result = await svc.send_test_email(
            campaign_id=args["campaign_id"],
            test_email=test_email,
            sequence_number=args.get("sequence_number", 1),
        )
        return {**result, "sent_to": test_email}

    if tool_name in ("smartlead_score_campaigns", "smartlead_extract_patterns"):
        user = await _get_user(token, session)
        return {"message": f"{tool_name} — coming in next iteration"}

    # ── GetSales LinkedIn Automation ──

    if tool_name == "gs_generate_flow":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project:
            raise ValueError("Project not found")

        from app.config import settings as _cfg
        from app.services.getsales_automation import GetSalesAutomationService
        gs_key = _cfg.GETSALES_API_KEY
        gs_team = _cfg.GETSALES_TEAM_ID
        if not gs_key:
            raise ValueError("GetSales not configured (GETSALES_API_KEY missing)")

        svc = GetSalesAutomationService(gs_key, gs_team or "7430")
        openai_key = getattr(_cfg, "OPENAI_API_KEY", None)

        seq = await svc.generate_flow(
            session, project.id,
            flow_name=args.get("flow_name"),
            flow_type=args.get("flow_type", "standard"),
            instructions=args.get("instructions"),
            openai_key=openai_key,
        )

        flow_data = seq.sequence_steps or {}
        messages = flow_data.get("messages", [])
        conn_note = flow_data.get("connection_note", "")

        preview = []
        if conn_note:
            preview.append(f"Connection note: {conn_note[:100]}...")
        for i, msg in enumerate(messages):
            preview.append(f"MSG{i+1}: {msg[:100]}...")

        return {
            "sequence_id": seq.id,
            "flow_name": seq.campaign_name,
            "flow_type": flow_data.get("flow_type", "standard"),
            "messages": len(messages),
            "status": "draft",
            "rationale": seq.rationale,
            "preview": preview,
            "connection_note": conn_note,
            "full_messages": messages,
            "include_inmail": flow_data.get("include_inmail", False),
            "message": (
                f"Generated LinkedIn flow '{seq.campaign_name}' ({flow_data.get('flow_type', 'standard')} type).\n\n"
                f"Connection note: {conn_note[:150] if conn_note else '(none — networking style)'}\n"
                f"Messages: {len(messages)} follow-ups\n"
                f"InMail fallback: {'Yes' if flow_data.get('include_inmail') else 'No'}\n\n"
                f"Review the messages above. You can:\n"
                f"- Edit any message (tell me which to change)\n"
                f"- Change flow type (standard/networking/product/volume/event)\n"
                f"- Say 'approve' when ready to push to GetSales"
            ),
        }

    if tool_name == "gs_approve_flow":
        user = await _get_user(token, session)
        seq = await session.get(GeneratedSequence, args["sequence_id"])
        if not seq:
            raise ValueError("Flow not found")
        from datetime import datetime
        seq.status = "approved"
        seq.reviewed_by = f"mcp:user:{user.id}"
        seq.reviewed_at = datetime.utcnow()
        return {"approved": True, "sequence_id": seq.id}

    if tool_name == "gs_list_sender_profiles":
        user = await _get_user(token, session)
        from app.config import settings as _cfg
        from app.services.getsales_automation import GetSalesAutomationService
        gs_key = _cfg.GETSALES_API_KEY
        gs_team = _cfg.GETSALES_TEAM_ID
        if not gs_key:
            raise ValueError("GetSales not configured")
        svc = GetSalesAutomationService(gs_key, gs_team or "7430")

        profiles = await svc.get_sender_profiles()
        workspaces = await svc.get_workspaces()

        return {
            "sender_profiles": [
                {
                    "uuid": p.get("uuid", ""),
                    "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                    "status": p.get("status", ""),
                    "linkedin_url": p.get("linkedin_url", ""),
                }
                for p in (profiles or [])
            ],
            "workspaces": [
                {"uuid": w.get("uuid", ""), "name": w.get("name", "")}
                for w in (workspaces or [])
            ],
            "message": "Select sender profile UUIDs and optionally a workspace UUID for the flow.",
        }

    if tool_name == "gs_push_to_getsales":
        user = await _get_user(token, session)
        seq = await session.get(GeneratedSequence, args["sequence_id"])
        if not seq:
            raise ValueError("Flow not found")
        if seq.status != "approved":
            raise ValueError("Flow must be approved first. Call gs_approve_flow.")

        from app.config import settings as _cfg
        from app.services.getsales_automation import (
            GetSalesAutomationService, get_timezone_for_country,
            TIMING_STANDARD, TIMING_NETWORKING, TIMING_VOLUME,
        )
        gs_key = _cfg.GETSALES_API_KEY
        gs_team = _cfg.GETSALES_TEAM_ID
        if not gs_key:
            raise ValueError("GetSales not configured")
        svc = GetSalesAutomationService(gs_key, gs_team or "7430")

        flow_data = seq.sequence_steps or {}
        flow_type = flow_data.get("flow_type", "standard")

        # Select timing based on flow type
        timing = {
            "standard": TIMING_STANDARD,
            "product": TIMING_STANDARD,
            "networking": TIMING_NETWORKING,
            "event": TIMING_NETWORKING,
            "volume": TIMING_VOLUME,
        }.get(flow_type, TIMING_STANDARD)

        # Resolve timezone
        target_country = args.get("target_country", "")
        if not target_country and seq.project_id:
            project = await session.get(Project, seq.project_id)
            if project and project.target_segments:
                for country in ["United States", "Germany", "United Kingdom", "India", "Australia", "UAE", "Russia"]:
                    if country.lower() in (project.target_segments or "").lower():
                        target_country = country
                        break
        timezone = get_timezone_for_country(target_country)

        # Build node tree
        node_tree = svc.build_node_tree(
            connection_note=flow_data.get("connection_note", ""),
            messages=flow_data.get("messages", []),
            timing=timing,
            include_inmail=flow_data.get("include_inmail", False),
            inmail_text=flow_data.get("inmail_text"),
        )

        # 1. Create flow
        flow_result = await svc.create_flow(
            name=seq.campaign_name or "MCP Generated",
            workspace_uuid=args.get("workspace_uuid"),
            timezone=timezone,
        )
        if not flow_result:
            raise ValueError("Failed to create GetSales flow")
        flow_uuid = flow_result.get("uuid")
        if not flow_uuid:
            raise ValueError(f"GetSales returned no UUID: {flow_result}")

        # 2. Save flow version with nodes
        sender_uuids = args.get("sender_profile_uuids", [])
        rotation = args.get("rotation_strategy", "fair")

        version_result = await svc.save_flow_version(
            flow_uuid=flow_uuid,
            nodes=node_tree,
            sender_profile_uuids=sender_uuids,
            rotation_strategy=rotation,
        )

        # 3. Upload target contacts from pipeline
        leads_uploaded = 0
        try:
            contacts_result = await session.execute(
                select(ExtractedContact, DiscoveredCompany)
                .outerjoin(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
                .where(
                    ExtractedContact.project_id == seq.project_id,
                    DiscoveredCompany.is_target == True,
                )
            )
            contacts = contacts_result.all()
            for contact, company in contacts:
                linkedin_url = contact.linkedin_url if hasattr(contact, 'linkedin_url') else None
                if not linkedin_url:
                    continue
                lead_data = {
                    "linkedin_url": linkedin_url,
                    "first_name": contact.first_name or "",
                    "last_name": contact.last_name or "",
                    "company_name": company.name if company else "",
                }
                result = await svc.add_lead_to_flow(flow_uuid, lead_data)
                if result:
                    leads_uploaded += 1
        except Exception as e:
            logger.warning(f"Failed to upload contacts to GetSales: {e}")

        # 4. Save campaign to DB
        campaign = Campaign(
            project_id=seq.project_id,
            company_id=seq.company_id,
            name=seq.campaign_name,
            external_id=flow_uuid,
            platform="getsales",
            status="draft",
            leads_count=leads_uploaded,
        )
        session.add(campaign)
        from datetime import datetime
        seq.pushed_at = datetime.utcnow()
        seq.status = "pushed"
        await session.flush()

        getsales_url = f"https://amazing.getsales.io/flow/{flow_uuid}/builder"

        return {
            "pushed": True,
            "flow_uuid": flow_uuid,
            "status": "DRAFT",
            "settings": {
                "timezone": timezone,
                "schedule": "Mon-Fri 09:00-18:00",
                "sender_profiles": len(sender_uuids),
                "rotation_strategy": rotation,
                "flow_type": flow_type,
                "messages": len(flow_data.get("messages", [])),
                "include_inmail": flow_data.get("include_inmail", False),
            },
            "leads_uploaded": leads_uploaded,
            "version_saved": version_result is not None,
            "message": (
                f"Flow '{seq.campaign_name}' created as DRAFT in GetSales.\n\n"
                f"GetSales: {getsales_url}\n"
                f"Schedule: Mon-Fri 9:00-18:00 {timezone}\n"
                f"Sender profiles: {len(sender_uuids)} assigned ({rotation} rotation)\n"
                f"Leads uploaded: {leads_uploaded} target contacts\n\n"
                f"Review the flow in GetSales Builder, then say 'activate' when ready to start."
            ),
            "_links": {
                "getsales_builder": getsales_url,
                "getsales_flow": f"https://amazing.getsales.io/flow/{flow_uuid}",
            },
        }

    if tool_name == "gs_activate_flow":
        user = await _get_user(token, session)
        if not args.get("user_confirmation"):
            raise ValueError("SAFETY: user_confirmation required. Quote the user's exact words confirming activation.")

        from app.config import settings as _cfg
        from app.services.getsales_automation import GetSalesAutomationService
        gs_key = _cfg.GETSALES_API_KEY
        gs_team = _cfg.GETSALES_TEAM_ID
        if not gs_key:
            raise ValueError("GetSales not configured")
        svc = GetSalesAutomationService(gs_key, gs_team or "7430")

        result = await svc.start_flow(args["flow_uuid"])

        from datetime import datetime
        session.add(MCPUsageLog(
            user_id=user.id,
            tool_name="gs_activate_flow",
            action="flow_activated",
            extra_data={
                "flow_uuid": args["flow_uuid"],
                "user_confirmation": args["user_confirmation"],
                "timestamp": str(datetime.utcnow()),
            },
        ))

        return {
            "activated": True,
            "flow_uuid": args["flow_uuid"],
            "status": "ACTIVE",
            "message": f"Flow {args['flow_uuid']} is now ACTIVE. LinkedIn outreach will begin.",
            "_links": {"getsales": f"https://amazing.getsales.io/flow/{args['flow_uuid']}/builder"},
        }

    # ── People Extraction ──
    if tool_name == "extract_people":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        ctx = UserServiceContext(user.id, session)
        apollo_svc = await ctx.get_apollo_service()
        if not apollo_svc.is_configured():
            raise ValueError("Apollo not connected")

        # Read target roles from project offer_summary (aligned with user during offer confirmation)
        # Falls back to GPT inference if not present (legacy projects)
        person_titles = None
        person_seniorities = ["c_suite", "vp", "director"]

        # Priority 1: from run's people_filters (user explicitly set via set_people_filters)
        if run.people_filters and run.people_filters.get("person_titles"):
            person_titles = run.people_filters["person_titles"]
            person_seniorities = run.people_filters.get("person_seniorities", person_seniorities)
        # Priority 2: from project offer_summary.target_roles (aligned during offer confirmation)
        elif project.offer_summary and isinstance(project.offer_summary, dict):
            target_roles = project.offer_summary.get("target_roles", {})
            if target_roles.get("titles"):
                person_titles = target_roles["titles"]
                person_seniorities = target_roles.get("seniorities", person_seniorities)
        # Priority 3: GPT inference (legacy fallback)
        if not person_titles:
            openai_key = await ctx.get_key("openai")
            if not openai_key:
                from app.config import settings as _cfg
                openai_key = _cfg.OPENAI_API_KEY
            if project.target_segments and openai_key:
                try:
                    from app.services.offer_analyzer import infer_people_roles
                    roles = await infer_people_roles(project.target_segments, openai_key)
                    person_titles = roles.get("person_titles")
                    person_seniorities = roles.get("person_seniorities", person_seniorities)
                except Exception:
                    pass

        # Get target companies without contacts
        from app.models.gathering import CompanySourceLink
        targets = (await session.execute(
            select(DiscoveredCompany)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(
                CompanySourceLink.gathering_run_id == run.id,
                DiscoveredCompany.is_target == True,
            )
        )).scalars().all()

        if not targets:
            raise ValueError("No target companies in this run. Run tam_analyze first.")

        people_per_company = args.get("people_per_company", 3)
        total_people = 0
        companies_with_people = 0

        # ExtractedContact already imported at top of file
        for company in targets:
            try:
                # Always search WITHOUT titles first (reliable), then prefer matching titles
                people = await apollo_svc.enrich_by_domain(
                    company.domain, limit=people_per_company * 2, titles=None,
                )
                # Prefer people matching offer roles, but take anyone with email
                if people and person_titles and len(people) > people_per_company:
                    title_lower = [t.lower() for t in (person_titles or [])]
                    preferred = [p for p in people if any(t in (p.get("title", "") or "").lower() for t in title_lower)]
                    others = [p for p in people if p not in preferred]
                    people = (preferred + others)[:people_per_company]
                else:
                    people = people[:people_per_company]
                for person in people:
                    contact = ExtractedContact(
                        project_id=run.project_id,
                        discovered_company_id=company.id,
                        email=person.get("email"),
                        first_name=person.get("first_name"),
                        last_name=person.get("last_name"),
                        job_title=person.get("title") or person.get("job_title"),
                        linkedin_url=person.get("linkedin_url"),
                        email_verified=person.get("is_verified", False),
                        email_source="apollo" if person.get("is_verified") else None,
                        source_data=person,
                    )
                    session.add(contact)
                total_people += len(people)
                if people:
                    companies_with_people += 1
            except Exception as e:
                logger.error(f"People search for {company.domain} failed: {e}", exc_info=True)

        return {
            "people_found": total_people,
            "companies_searched": len(targets),
            "companies_with_people": companies_with_people,
            "people_filters": {
                "person_titles": person_titles,
                "person_seniorities": person_seniorities,
            },
            "message": (
                f"Found {total_people} contacts from {companies_with_people}/{len(targets)} target companies.\n"
                f"Roles: {person_titles or ['C-level (default)']}\n"
                f"Seniorities: {person_seniorities}\n"
                f"FREE — no Apollo credits used.\n\n"
                f"View contacts: {settings.UI_BASE}/crm?pipeline={run.id}"
            ),
            "_links": {
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
            },
        }

    # ── Auto Pipeline (non-blocking — runs in background) ──
    if tool_name == "run_auto_pipeline":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        from app.services.pipeline_orchestrator import is_pipeline_running, start_pipeline_background
        if is_pipeline_running(run.id):
            raise ValueError(f"Pipeline {run.id} is already running. Use pipeline_status to check progress.")

        from math import ceil as _ceil
        tp = args.get("target_people", 100)
        mpc = args.get("max_people_per_company", 3)
        tc = args.get("target_companies") or _ceil(tp / mpc)

        # HARD GATE: pipeline cannot start without pre-selected email accounts
        from app.models.campaign import Campaign as _Camp
        _camp = None
        if run.campaign_id:
            _camp = await session.get(_Camp, run.campaign_id)
        if not _camp or not _camp.email_account_ids:
            raise ValueError(
                "Email accounts must be selected before starting the pipeline. "
                "Call align_email_accounts first to select which SmartLead accounts to use for the campaign."
            )
        campaign_status = f"{len(_camp.email_account_ids)} accounts pre-selected — auto-push on KPI hit"

        # TIER 1: Preview before executing
        if not args.get("confirm"):
            return {
                "status": "awaiting_confirmation",
                "preview": {
                    "action": "run_auto_pipeline",
                    "run_id": run.id,
                    "project": project.name,
                    "kpi": {"target_people": tp, "max_people_per_company": mpc, "target_companies": tc},
                    "filters": run.filters,
                    "campaign_status": campaign_status,
                },
                "message": (
                    f"Pre-pipeline checklist for #{run.id} ({project.name}):\n\n"
                    f"  1. Offer: ✅ confirmed\n"
                    f"  2. Filters: ✅ {run.filters.get('q_organization_keyword_tags', [])}\n"
                    f"  3. Accounts: ✅ {campaign_status}\n"
                    f"  4. KPI: {tp} contacts, max {mpc}/company → ~{tc} target companies\n"
                    f"  5. Estimated cost: ~{tc} Apollo credits\n\n"
                    f"Review the KPI above. Change with set_pipeline_kpi if needed.\n"
                    f"Approve to start?"
                ),
                "next_action": {"tool": "run_auto_pipeline", "args": {**args, "confirm": True}},
            }

        # Confirmed — execute
        run.target_people = tp
        run.max_people_per_company = mpc
        run.target_companies = tc
        run.status = "running"
        from datetime import datetime as _dt, timezone as _tz
        run.started_at = _dt.now(_tz.utc)
        await session.commit()

        filters = args.get("filters") or run.filters or {}
        start_pipeline_background(run.id, filters, user.id)

        return {
            "run_id": run.id,
            "status": "started",
            "kpi": {"target_people": tp, "max_people_per_company": mpc, "target_companies": tc},
            "message": (
                f"Pipeline running in background. Target: {tp} contacts "
                f"(max {mpc}/company, ~{tc} target companies).\n\n"
                f"I will poll pipeline_status every 30 seconds and notify you when:\n"
                f"  - KPI is hit\n"
                f"  - SmartLead campaign is created\n"
                f"  - Test email is sent to your inbox\n"
                f"  - Pipeline completes or encounters issues\n\n"
                f"You can also use control_pipeline to pause/resume."
            ),
            "poll_until_complete": True,
            "poll_interval_seconds": 30,
            "poll_tool": "pipeline_status",
            "poll_args": {"run_id": run.id},
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

    # ── Orchestration ──
    if tool_name == "pipeline_status":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")

        gates = await session.execute(
            select(ApprovalGate).where(ApprovalGate.gathering_run_id == run.id, ApprovalGate.status == "pending")
        )
        pending = gates.scalars().all()

        # KPI defaults
        from math import ceil as _ceil
        target_count = run.target_people or 100
        contacts_per_company = run.max_people_per_company or 3
        min_targets = run.target_companies or _ceil(target_count / contacts_per_company)
        people_found = run.total_people_found or 0
        targets_found = run.total_targets_found or 0

        # Timing
        from datetime import datetime as _dt, timezone as _tz
        elapsed_seconds = None
        eta_seconds = None
        elapsed_human = None
        eta_human = None
        if run.started_at:
            now = _dt.now(_tz.utc)
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=_tz.utc)
            elapsed_seconds = int((now - started).total_seconds())
            elapsed_human = _format_duration(elapsed_seconds)
            # ETA based on people/second rate
            if people_found > 0 and people_found < target_count and elapsed_seconds > 0:
                rate = people_found / elapsed_seconds
                remaining = target_count - people_found
                eta_seconds = int(remaining / rate)
                eta_human = f"~{_format_duration(eta_seconds)}"
            elif run.status in ("running",) and elapsed_seconds < 60:
                eta_human = "calculating..."

        # Cost estimate for remaining work
        pages_fetched = run.pages_fetched or 0
        remaining_people = max(0, target_count - people_found)
        remaining_companies = _ceil(remaining_people / contacts_per_company) if remaining_people > 0 else 0
        pages_remaining = max(0, _ceil(remaining_companies / 8.75) - pages_fetched) if remaining_companies > 0 else 0

        from app.services.pipeline_orchestrator import is_pipeline_running
        is_bg_running = is_pipeline_running(run.id)

        # Check for campaign created (auto-push result)
        campaign_info = None
        if run.campaign_id:
            camp = await session.get(Campaign, run.campaign_id)
            if camp:
                campaign_info = {
                    "id": camp.id,
                    "name": camp.name,
                    "status": camp.status,
                    "smartlead_url": f"https://app.smartlead.ai/app/email-campaigns-v2/{camp.external_id}/analytics" if camp.external_id else None,
                    "leads_count": camp.leads_count or 0,
                    "accounts": len(camp.email_account_ids or []),
                }

        is_done = run.status in ("completed", "insufficient", "failed")

        result = {
            "run_id": run.id,
            "status": run.status if not (run.status == "running" and not is_bg_running) else run.status,
            "phase": run.current_phase,
            "new_companies": run.new_companies_count,
            "duplicates": run.duplicate_count,
            "rejected": run.rejected_count,
            "credits_used": run.credits_used,
            "kpi": {
                "target_people": target_count,
                "max_people_per_company": contacts_per_company,
                "target_companies": min_targets,
            },
            "progress": {
                "targets_found": targets_found,
                "people_found": people_found,
                "pages_fetched": pages_fetched,
                "iteration": run.current_iteration or 0,
                "people_pct": round(people_found / target_count * 100, 1) if target_count > 0 else 0,
                "targets_pct": round(targets_found / min_targets * 100, 1) if min_targets > 0 else 0,
            },
            "timing": {
                "started_at": str(run.started_at) if run.started_at else None,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_human": elapsed_human,
                "eta_seconds": eta_seconds,
                "eta_human": eta_human,
            },
            "cost": {
                "credits_used": run.credits_used or 0,
                "estimated_remaining_credits": pages_remaining,
            },
            "paused_at": str(run.paused_at) if run.paused_at else None,
            "pending_gates": [{"gate_id": g.id, "type": g.gate_type, "scope": g.scope} for g in pending],
            "campaign": campaign_info,
            "is_complete": is_done,
            "_links": {
                "pipeline": f"{settings.UI_BASE}/pipeline/{run.id}",
                "crm": f"{settings.UI_BASE}/crm?pipeline={run.id}&project_id={run.project_id}",
            },
        }

        # Add completion message for the agent to relay to user
        if is_done and campaign_info:
            sl_url = campaign_info.get("smartlead_url", "")
            result["completion_message"] = (
                f"Pipeline #{run.id} complete! {people_found} contacts gathered in {elapsed_human}.\n\n"
                + (f"SmartLead campaign '{campaign_info['name']}' created as DRAFT with "
                   f"{campaign_info['leads_count']} leads and {campaign_info['accounts']} email accounts.\n"
                   f"SmartLead: {sl_url}\n"
                   f"A test email was sent to your inbox — check it before activating.\n\n"
                   f"Say 'activate' when ready to start sending."
                   if sl_url else
                   f"Campaign '{campaign_info['name']}' ready (status: {campaign_info['status']}).\n")
            )
        elif is_done:
            result["completion_message"] = (
                f"Pipeline #{run.id} finished with status '{run.status}'. "
                f"{people_found}/{target_count} contacts gathered in {elapsed_human}."
                + (f"\nIssues: {run.error_message}" if run.error_message else "")
            )

        return result

    # ── Set Pipeline KPI ──
    if tool_name == "set_pipeline_kpi":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        from math import ceil as _ceil

        # Calculate new KPIs with alignment
        user_set = set()
        new_tp = run.target_people or 100
        new_mpc = run.max_people_per_company or 3
        new_tc = run.target_companies or _ceil(new_tp / new_mpc)

        if "target_people" in args:
            new_tp = args["target_people"]
            user_set.add("target_people")
        if "max_people_per_company" in args:
            new_mpc = args["max_people_per_company"]
            user_set.add("max_people_per_company")
        if "target_companies" in args:
            new_tc = args["target_companies"]
            user_set.add("target_companies")

        # Alignment (see tests/test_kpi_alignment.md)
        if "target_companies" in user_set and "target_people" not in user_set:
            new_tp = new_tc * new_mpc
        else:
            new_tc = _ceil(new_tp / new_mpc)

        old_tp = run.target_people or 100
        old_mpc = run.max_people_per_company or 3
        people_found = run.total_people_found or 0
        remaining = max(0, new_tp - people_found)
        remaining_companies = _ceil(remaining / new_mpc) if remaining > 0 else 0
        pages_needed = max(0, _ceil(remaining_companies / 8.75))

        # TIER 1: Preview before executing
        if not args.get("confirm"):
            return {
                "status": "awaiting_confirmation",
                "preview": {
                    "action": "set_pipeline_kpi",
                    "run_id": run.id,
                    "old_kpi": {"target_people": old_tp, "max_people_per_company": old_mpc},
                    "new_kpi": {"target_people": new_tp, "max_people_per_company": new_mpc, "target_companies": new_tc},
                },
                "message": (
                    f"I will change KPIs on pipeline #{run.id} ({project.name}):\n"
                    f"  Target people: {old_tp} → {new_tp}\n"
                    f"  Max per company: {old_mpc} → {new_mpc}\n"
                    f"  Target companies: ~{new_tc}\n"
                    f"  Current progress: {people_found} contacts found\n"
                    f"  Remaining: {remaining} contacts, ~{pages_needed} credits\n\n"
                    f"Approve?"
                ),
                "next_action": {"tool": "set_pipeline_kpi", "args": {**args, "confirm": True}},
            }

        # Confirmed — execute
        run.target_people = new_tp
        run.max_people_per_company = new_mpc
        run.target_companies = new_tc
        await session.commit()

        return {
            "kpi": {"target_people": new_tp, "max_people_per_company": new_mpc, "target_companies": new_tc},
            "progress": {"people_found": people_found, "targets_found": run.total_targets_found or 0, "remaining_people": remaining},
            "cost_estimate": {"pages_needed": pages_needed, "credits": pages_needed, "usd": f"${pages_needed * 0.01:.2f}"},
            "message": (
                f"KPIs updated: {new_tp} contacts, max {new_mpc}/company, ~{new_tc} target companies.\n"
                f"Current: {people_found} found. Remaining: {remaining}. Est: {pages_needed} credits."
            ),
            "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
        }

    # ── Set People Filters (role changes) ──
    if tool_name == "set_people_filters":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        # Build new filter set
        new_pf = dict(run.people_filters or {})
        if "person_titles" in args:
            new_pf["person_titles"] = args["person_titles"]
        if "person_seniorities" in args:
            valid = {"owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry"}
            new_pf["person_seniorities"] = [s for s in args["person_seniorities"] if s in valid]
        new_mpc = args.get("max_people_per_company") or run.max_people_per_company or 3

        old_pf = run.people_filters or {}
        targets_found = run.total_targets_found or 0

        # TIER 1: Preview before executing
        if not args.get("confirm"):
            return {
                "status": "awaiting_confirmation",
                "preview": {
                    "action": "set_people_filters",
                    "run_id": run.id,
                    "old_filters": old_pf,
                    "new_filters": new_pf,
                },
                "message": (
                    f"I will change people filters on pipeline #{run.id} ({project.name}):\n"
                    f"  Titles: {', '.join(new_pf.get('person_titles', ['(default)']))}\n"
                    f"  Seniority: {', '.join(new_pf.get('person_seniorities', ['(default)']))}\n"
                    f"  Max per company: {new_mpc}\n"
                    f"  {targets_found} target companies x {new_mpc} = up to {targets_found * new_mpc} contacts\n"
                    f"  People search is FREE. Takes effect on next batch.\n\n"
                    f"Approve?"
                ),
                "next_action": {"tool": "set_people_filters", "args": {**args, "confirm": True}},
            }

        # Confirmed — execute
        run.people_filters = new_pf
        run.max_people_per_company = new_mpc
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(run, "people_filters")
        await session.commit()

        return {
            "people_filters": new_pf,
            "message": (
                f"People filters updated. Titles: {', '.join(new_pf.get('person_titles', ['(default)']))}. "
                f"Max {new_mpc}/company. FREE — takes effect on next batch."
            ),
            "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
        }

    # ── Control Pipeline (pause/resume) — TIER 1: preview + confirm ──
    if tool_name == "control_pipeline":
        user = await _get_user(token, session)
        run = await session.get(GatheringRun, args["run_id"])
        if not run:
            raise ValueError("Run not found")
        project = await session.get(Project, run.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        action = args["action"]
        from datetime import datetime as _dt, timezone as _tz
        tp = run.target_people or 100
        pf = run.total_people_found or 0
        tf = run.total_targets_found or 0
        pg = run.pages_fetched or 0

        if action == "pause":
            if run.status != "running":
                raise ValueError(f"Cannot pause: pipeline status is '{run.status}', not 'running'")

            # Preview before executing
            if not args.get("confirm"):
                return {
                    "status": "awaiting_confirmation",
                    "preview": {"action": "pause", "run_id": run.id, "project": project.name,
                                "progress": f"{pf}/{tp} contacts, {tf} targets, page {pg}"},
                    "message": (
                        f"I will pause pipeline #{run.id} ({project.name}).\n"
                        f"  Progress: {pf}/{tp} contacts, {tf} targets, {pg} pages.\n"
                        f"  All progress will be saved — resume anytime.\n\n"
                        f"Approve?"
                    ),
                    "next_action": {"tool": "control_pipeline", "args": {**args, "confirm": True}},
                }

            run.status = "paused"
            run.paused_at = _dt.now(_tz.utc)
            await session.commit()
            return {
                "run_id": run.id, "status": "paused",
                "message": f"Pipeline #{run.id} paused. Progress saved: {pf} contacts, {tf} targets, {pg} pages.",
                "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
            }

        elif action == "resume":
            if run.status not in ("paused", "insufficient"):
                raise ValueError(f"Cannot resume: pipeline status is '{run.status}'. Must be 'paused' or 'insufficient'.")

            from app.services.pipeline_orchestrator import is_pipeline_running, start_pipeline_background
            if is_pipeline_running(run.id):
                raise ValueError(f"Pipeline {run.id} is already running.")

            # Preview before executing
            if not args.get("confirm"):
                return {
                    "status": "awaiting_confirmation",
                    "preview": {"action": "resume", "run_id": run.id, "project": project.name,
                                "resume_from": f"page {pg}, {pf} contacts found", "target": tp},
                    "message": (
                        f"I will resume pipeline #{run.id} ({project.name}) from page {pg}.\n"
                        f"  Current: {pf}/{tp} contacts, {tf} targets.\n"
                        f"  Target: {tp} contacts.\n\n"
                        f"Approve?"
                    ),
                    "next_action": {"tool": "control_pipeline", "args": {**args, "confirm": True}},
                }

            run.status = "running"
            run.resumed_at = _dt.now(_tz.utc)
            await session.commit()

            filters = run.filters or {}
            start_pipeline_background(run.id, filters, user.id)

            return {
                "run_id": run.id, "status": "running",
                "message": f"Pipeline #{run.id} resumed from page {pg}. Target: {tp} contacts.",
                "_links": {"pipeline": f"{settings.UI_BASE}/pipeline/{run.id}"},
            }

        raise ValueError(f"Unknown action: {action}. Use 'pause' or 'resume'.")

    # ── Filter Intelligence (DEPRECATED — use tam_gather without confirm_filters instead) ──
    if tool_name == "suggest_apollo_filters":
        user = await _get_user(token, session)
        ctx = UserServiceContext(user.id, session)
        apollo_svc = await ctx.get_apollo_service()
        if not apollo_svc.is_configured():
            raise ValueError("Apollo not connected. Use configure_integration first.")
        # Collect all AI keys (user's first, system fallback)
        from app.config import settings as _cfg
        openai_key = await ctx.get_key("openai") or _cfg.OPENAI_API_KEY
        anthropic_key = await ctx.get_key("anthropic") or _cfg.ANTHROPIC_API_KEY

        from app.services.filter_intelligence import suggest_filters
        result = await suggest_filters(
            query=args["query"],
            apollo_service=apollo_svc,
            openai_key=openai_key,
            anthropic_key=anthropic_key,
            gemini_key=None,
            target_count=args.get("target_people", 10),
        )

        # Add total_available + cost estimate (probe Apollo)
        sf = result.get("suggested_filters", {})
        if sf.get("q_organization_keyword_tags"):
            try:
                probe = await apollo_svc.search_organizations(
                    keyword_tags=sf["q_organization_keyword_tags"],
                    locations=sf.get("organization_locations"),
                    num_employees_ranges=sf.get("organization_num_employees_ranges"),
                    page=1, per_page=1,
                )
                total = probe.get("pagination", {}).get("total_entries", 0) if probe else 0
                per_page = 100
                TARGET_RATE = 0.35
                pages_30 = max(1, int(30 / (per_page * TARGET_RATE)) + 1)
                pages_all = max(1, (total + per_page - 1) // per_page) if total > 0 else 1

                result["apollo_preview"] = {
                    "total_available": total,
                    "filters_applied": sf,
                    "cost_default_30_targets": {"credits": min(pages_30, pages_all), "estimated_targets": int(min(pages_30, pages_all) * per_page * TARGET_RATE)},
                    "cost_full_run": {"credits": pages_all, "estimated_targets": int(total * TARGET_RATE)},
                    "target_conversion_rate": f"{int(TARGET_RATE * 100)}%",
                }
                result["message"] = (
                    f"Suggested Apollo filters:\n"
                    f"  Keywords: {sf.get('q_organization_keyword_tags', [])}\n"
                    f"  Location: {sf.get('organization_locations', ['(any)'])}\n"
                    f"  Size: {sf.get('organization_num_employees_ranges', ['(any)'])}\n"
                    f"  Total available: {total:,} companies\n\n"
                    f"Cost options:\n"
                    f"  Default (≈30 targets): {min(pages_30, pages_all)} credits → ≈{int(min(pages_30, pages_all) * per_page * TARGET_RATE)} targets\n"
                    f"  Full run (all {total:,}): {pages_all} credits → ≈{int(total * TARGET_RATE):,} targets\n"
                    f"  (estimated target conversion: {int(TARGET_RATE * 100)}%)\n\n"
                    f"Proceed? Call tam_gather with these filters + confirm_filters=true."
                )
            except Exception as e:
                logger.warning(f"Apollo probe in suggest_filters failed: {e}")

        return result

    if tool_name == "run_full_pipeline":
        user = await _get_user(token, session)
        # Start with gather phase — same as tam_gather
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")
        import hashlib, json as _json
        filter_hash = hashlib.sha256(_json.dumps(args["filters"], sort_keys=True).encode()).hexdigest()[:16]
        run = GatheringRun(
            project_id=project.id, company_id=project.company_id,
            source_type=args["source_type"], filters=args["filters"],
            filter_hash=filter_hash, status="running", current_phase="gather",
            triggered_by=f"mcp:full_pipeline:user:{user.id}",
        )
        session.add(run)
        await session.flush()
        return {"run_id": run.id, "message": "Full pipeline started. Use pipeline_status to track progress."}

    # ── CRM Queries ──
    if tool_name == "query_contacts":
        user = await _get_user(token, session)

        query = select(ExtractedContact).order_by(ExtractedContact.created_at.desc())

        project_id = args.get("project_id")
        if not project_id and user.active_project_id:
            project_id = user.active_project_id
        if project_id:
            query = query.where(ExtractedContact.project_id == project_id)
        else:
            # NEVER return global data — scope to user's projects
            user_pids = await session.execute(select(Project.id).where(Project.user_id == user.id))
            pids = [pid for (pid,) in user_pids.all()]
            if pids:
                query = query.where(ExtractedContact.project_id.in_(pids))
            else:
                return {"total": 0, "contacts": [], "message": "No projects yet."}

        search = args.get("search")
        if search:
            query = query.where(
                (ExtractedContact.email.ilike(f"%{search}%")) |
                (ExtractedContact.first_name.ilike(f"%{search}%")) |
                (ExtractedContact.last_name.ilike(f"%{search}%"))
            )

        limit = args.get("limit", 20)
        query = query.limit(min(limit, 100))
        result = await session.execute(query)
        contacts = result.scalars().all()

        # Build CRM deep link
        crm_params = []
        if project_id:
            crm_params.append(f"project_id={project_id}")
        if search:
            crm_params.append(f"search={search}")
        if args.get("has_replied"):
            crm_params.append("has_replied=true")
        if args.get("needs_followup"):
            crm_params.append("needs_followup=true")
        if args.get("reply_category"):
            crm_params.append(f"reply_category={args['reply_category']}")
        if args.get("pipeline_run_id"):
            crm_params.append(f"pipeline={args['pipeline_run_id']}")
        crm_link = f"{settings.UI_BASE}/crm" + ("?" + "&".join(crm_params) if crm_params else "")

        return {
            "total": len(contacts),
            "contacts": [
                {"email": c.email, "name": f"{c.first_name or ''} {c.last_name or ''}".strip() or None, "job_title": c.job_title, "source": c.email_source}
                for c in contacts
            ],
            "message": f"Found {len(contacts)} contacts. View in CRM: {crm_link}",
            "_links": {"crm": crm_link},
        }

    if tool_name == "crm_stats":
        user = await _get_user(token, session)
        from sqlalchemy import func as sa_func

        project_id = args.get("project_id") or user.active_project_id

        # User-scope: get user's project IDs
        user_pids = await session.execute(select(Project.id).where(Project.user_id == user.id))
        pids = [pid for (pid,) in user_pids.all()]

        if project_id:
            scope_filter_ec = ExtractedContact.project_id == project_id
            scope_filter_dc = DiscoveredCompany.project_id == project_id
        elif pids:
            scope_filter_ec = ExtractedContact.project_id.in_(pids)
            scope_filter_dc = DiscoveredCompany.project_id.in_(pids)
        else:
            return {"total_contacts": 0, "total_companies": 0, "blacklisted_domains": 0, "targets": 0, "message": "No projects yet."}

        total_contacts = (await session.execute(select(sa_func.count(ExtractedContact.id)).where(scope_filter_ec))).scalar() or 0
        total_companies = (await session.execute(select(sa_func.count(DiscoveredCompany.id)).where(scope_filter_dc))).scalar() or 0
        blacklisted = (await session.execute(select(sa_func.count(DiscoveredCompany.id)).where(DiscoveredCompany.is_blacklisted == True, scope_filter_dc))).scalar() or 0
        targets = (await session.execute(select(sa_func.count(DiscoveredCompany.id)).where(DiscoveredCompany.is_target == True, scope_filter_dc))).scalar() or 0

        crm_link = f"{settings.UI_BASE}/crm" + (f"?project_id={project_id}" if project_id else "")

        return {
            "total_contacts": total_contacts,
            "total_companies": total_companies,
            "blacklisted_domains": blacklisted,
            "targets": targets,
            "message": f"{total_contacts} contacts, {total_companies} companies ({targets} targets, {blacklisted} blacklisted).",
            "_links": {"crm": crm_link},
        }

    # ── SmartLead Campaign Import ──
    if tool_name == "list_smartlead_campaigns":
        user = await _get_user(token, session)
        ctx = UserServiceContext(user.id, session)
        sl = await ctx.get_smartlead_service()
        if not sl.is_configured():
            raise ValueError("SmartLead not connected. Use configure_integration first.")
        campaigns = await sl.get_campaigns()
        search = (args.get("search") or "").lower()
        if search:
            campaigns = [c for c in campaigns if search in (c.get("name") or "").lower()]
        return {
            "campaigns": [
                {"id": c.get("id"), "name": c.get("name"), "status": c.get("status"), "leads": c.get("lead_count", 0)}
                for c in campaigns[:50]
            ],
            "total": len(campaigns),
            "message": f"Found {len(campaigns)} campaigns" + (f" matching '{search}'" if search else ""),
        }

    if tool_name == "import_smartlead_campaigns":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        ctx = UserServiceContext(user.id, session)
        sl = await ctx.get_smartlead_service()
        if not sl.is_configured():
            raise ValueError("SmartLead not connected")

        rules = args.get("rules", {})
        prefixes = rules.get("prefixes", [])
        tags = rules.get("tags", [])
        contains = rules.get("contains", [])
        exact_names = rules.get("exact_names", [])

        # Fetch all campaigns
        all_campaigns = await sl.get_campaigns()

        # Match campaigns against rules
        matched = []
        for c in all_campaigns:
            name = c.get("name", "")
            if exact_names and name in exact_names:
                matched.append(c)
            elif prefixes and any(name.startswith(p) for p in prefixes):
                matched.append(c)
            elif contains and any(s.lower() in name.lower() for s in contains):
                matched.append(c)
            # Tags matching would need campaign tags from SmartLead API

        # Warn if 0 campaigns matched — don't silently succeed
        if not matched:
            # Show similar campaign names to help user fix their query
            all_names = [c.get("name", "") for c in all_campaigns[:20]]
            return {
                "campaigns_imported": 0,
                "error": "no_campaigns_matched",
                "message": (
                    f"No campaigns matched your rules.\n\n"
                    f"Rules applied: prefixes={prefixes}, contains={contains}, exact={exact_names}, tags={tags}\n\n"
                    f"Available campaigns (first 20):\n" + "\n".join(f"  - {n}" for n in all_names) + "\n\n"
                    f"Try again with different rules. For example:\n"
                    f"  contains=['brand'] — matches any campaign with 'brand' in the name\n"
                    f"  prefixes=['ES Global'] — matches campaigns starting with 'ES Global'"
                ),
                "_links": {"setup": "{settings.UI_BASE}/setup"},
            }

        # TIER 1: Preview matched campaigns before importing
        if not args.get("confirm"):
            campaign_preview = [{"name": c.get("name", ""), "id": c.get("id"), "status": c.get("status", "")} for c in matched[:20]]
            return {
                "status": "awaiting_confirmation",
                "campaigns_matched": len(matched),
                "preview": campaign_preview,
                "message": (
                    f"Found {len(matched)} campaigns matching your rules:\n"
                    + "\n".join(f"  - {c['name']} (ID {c['id']})" for c in campaign_preview)
                    + f"\n\nI will download all contacts from these campaigns and add their domains to the blacklist.\n"
                    f"This ensures the pipeline won't gather companies already contacted.\n\n"
                    f"Approve import?"
                ),
                "next_action": {"tool": "import_smartlead_campaigns", "args": {**args, "confirm": True}},
            }

        # ACTUALLY DOWNLOAD contacts from each campaign → build blacklist
        from app.services.domain_service import normalize_domain
        import logging as _log

        total_contacts = 0
        total_domains = set()
        campaign_names = []
        campaign_details = []

        for camp in matched:
            camp_name = camp.get("name", "")
            camp_id = camp.get("id")
            campaign_names.append(camp_name)

            # Save campaign record
            existing_camp = await session.execute(
                select(Campaign).where(Campaign.external_id == str(camp_id))
            )
            if not existing_camp.scalar_one_or_none():
                session.add(Campaign(
                    project_id=project.id, company_id=project.company_id,
                    name=camp_name, external_id=str(camp_id),
                    platform="smartlead", status=camp.get("status", "active"),
                ))

            # DOWNLOAD ALL LEADS from this campaign
            leads = await sl.export_campaign_leads(camp_id)
            leads_count = len(leads)
            total_contacts += leads_count

            # Store contacts and extract domains for blacklist
            domains_in_camp = set()
            for lead in leads:
                domain = normalize_domain(lead.get("domain", ""))
                if domain:
                    total_domains.add(domain)
                    domains_in_camp.add(domain)

                # Save as extracted contact in MCP DB
                email = lead.get("email", "")
                if email:
                    existing_contact = await session.execute(
                        select(ExtractedContact).where(
                            ExtractedContact.project_id == project.id,
                            ExtractedContact.email == email,
                        )
                    )
                    if not existing_contact.scalar_one_or_none():
                        session.add(ExtractedContact(
                            discovered_company_id=None,
                            project_id=project.id,
                            first_name=lead.get("first_name"),
                            last_name=lead.get("last_name"),
                            email=email,
                            email_source="smartlead_import",
                            source_data={
                                "campaign": camp_name,
                                "campaign_id": camp_id,
                                "company_name": lead.get("company_name"),
                            },
                        ))

            # Create DiscoveredCompany records for each domain (for blacklisting)
            for domain in domains_in_camp:
                existing_dc = await session.execute(
                    select(DiscoveredCompany).where(
                        DiscoveredCompany.project_id == project.id,
                        DiscoveredCompany.domain == domain,
                    )
                )
                if not existing_dc.scalar_one_or_none():
                    session.add(DiscoveredCompany(
                        project_id=project.id,
                        company_id=project.company_id,
                        domain=domain,
                        is_blacklisted=True,
                        blacklist_reason=f"existing_campaign:{camp_name}",
                    ))

            campaign_details.append({"name": camp_name, "leads": leads_count, "domains": len(domains_in_camp)})
            _log.getLogger(__name__).info(f"Imported {leads_count} contacts from '{camp_name}' ({len(domains_in_camp)} unique domains)")

        # Save campaign rules on project
        project.campaign_filters = campaign_names
        await session.flush()

        # START BACKGROUND REPLY ANALYSIS — runs in parallel, doesn't block
        # Uses 3-tier funnel: SmartLead FREE → keyword OOO filter → GPT-4o-mini for real conversations
        campaign_id_map = {camp.get("id"): camp.get("name", "") for camp in matched}
        matched_ids = [camp.get("id") for camp in matched if camp.get("id")]
        try:
            from app.services.reply_analysis_service import start_reply_analysis_background
            # Pass OpenAI key for Tier 3 AI classification
            openai_key = None
            from app.services.encryption import decrypt_value
            openai_setting = await session.execute(
                select(MCPIntegrationSetting).where(
                    MCPIntegrationSetting.user_id == user.id,
                    MCPIntegrationSetting.integration_name == "openai",
                )
            )
            openai_row = openai_setting.scalar_one_or_none()
            if openai_row and openai_row.api_key_encrypted:
                try:
                    openai_key = decrypt_value(openai_row.api_key_encrypted)
                except Exception:
                    pass
            start_reply_analysis_background(sl, matched_ids, campaign_id_map, project.id, openai_key)
            reply_analysis_status = f"Reply analysis started in background for {len(matched_ids)} campaigns (3-tier: SmartLead→OOO filter→GPT-4o-mini)."
        except Exception as e:
            reply_analysis_status = f"Reply analysis failed to start: {e}"

        return {
            "campaigns_imported": len(matched),
            "campaigns": campaign_details,
            "contacts_downloaded": total_contacts,
            "unique_domains_blacklisted": len(total_domains),
            "reply_analysis": reply_analysis_status,
            "message": f"Downloaded {total_contacts} contacts from {len(matched)} campaigns. "
                       f"{len(total_domains)} unique domains added to blacklist. "
                       f"Reply analysis running in background — will classify warm/meeting/interested replies. "
                       f"Ask 'which replies are warm?' after a minute.",
            "_links": {
                "crm": f"{settings.UI_BASE}/crm?project_id={project.id}",
                "crm_warm": f"{settings.UI_BASE}/crm?project_id={project.id}&reply_category=interested",
                "crm_meetings": f"{settings.UI_BASE}/crm?project_id={project.id}&reply_category=meeting",
                "pipelines": "{settings.UI_BASE}/pipeline",
            },
        }

    if tool_name == "set_campaign_rules":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")
        rules = args.get("rules", {})
        # Store rules in campaign_filters as JSON
        project.campaign_filters = rules
        return {
            "updated": True,
            "project_id": project.id,
            "rules": rules,
            "message": f"Campaign rules saved for '{project.name}'. These will be used for blacklisting.",
        }

    # ── Utility ──
    if tool_name == "estimate_cost":
        source = args.get("source_type", "apollo.companies.api")
        filters = args.get("filters") or {}
        max_pages = filters.get("max_pages", 10)
        per_page = filters.get("per_page", 100)
        if "api" in source:
            return {"estimated_credits": max_pages, "estimated_cost_usd": 0,
                    "estimated_companies": max_pages * per_page,
                    "note": f"{max_pages} pages × {per_page} per page = {max_pages * per_page} companies. 1 credit per page."}
        return {"estimated_credits": 0, "estimated_cost_usd": 0, "note": "Free source"}

    if tool_name == "blacklist_check":
        user = await _get_user(token, session)
        domains = args.get("domains", [])
        return {"checked": len(domains), "blacklisted": 0, "clean": len(domains),
                "note": "Blacklist check against user's campaigns — full implementation coming"}

    # ── Reply tools ──
    if tool_name in ("replies_summary", "replies_list", "replies_followups", "replies_deep_link"):
        user = await _get_user(token, session)
        return await _handle_reply_tool(tool_name, args, user, session)

    # ── Feedback & Editing tools ──
    if tool_name == "smartlead_edit_sequence":
        user = await _get_user(token, session)
        seq = await session.get(GeneratedSequence, args["sequence_id"])
        if not seq:
            raise ValueError("Sequence not found")
        project = await session.get(Project, seq.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Sequence not found")

        steps = seq.sequence_steps or []
        step_idx = args["step_number"] - 1
        if step_idx < 0 or step_idx >= len(steps):
            raise ValueError(f"Step {args['step_number']} not found (sequence has {len(steps)} steps)")

        old_step = steps[step_idx].copy()
        if "subject" in args and args["subject"] is not None:
            steps[step_idx]["subject"] = args["subject"]
        if "body" in args and args["body"] is not None:
            steps[step_idx]["body"] = args["body"]
        seq.sequence_steps = steps
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(seq, "sequence_steps")

        # Push to SmartLead if campaign exists
        smartlead_pushed = False
        if seq.pushed_campaign_id:
            campaign = await session.get(Campaign, seq.pushed_campaign_id)
            if campaign and campaign.external_id:
                from app.services.smartlead_service import SmartLeadService
                sl = SmartLeadService()
                if sl.is_configured():
                    await sl.set_campaign_sequences(int(campaign.external_id), steps)
                    smartlead_pushed = True

        return {
            "updated": True,
            "step": args["step_number"],
            "old_subject": old_step.get("subject", ""),
            "new_subject": steps[step_idx].get("subject", ""),
            "smartlead_synced": smartlead_pushed,
            "message": f"Email {args['step_number']} updated and synced." + (f" Changes pushed to SmartLead." if smartlead_pushed else " Save to push changes to SmartLead."),
        }

    if tool_name == "edit_campaign_accounts":
        user = await _get_user(token, session)
        from app.services.smartlead_service import SmartLeadService
        sl = SmartLeadService()
        if not sl.is_configured():
            raise ValueError("SmartLead not configured")
        await sl.set_campaign_email_accounts(args["campaign_id"], args["email_account_ids"])
        return {
            "updated": True,
            "campaign_id": args["campaign_id"],
            "accounts_set": len(args["email_account_ids"]),
            "message": f"{len(args['email_account_ids'])} email accounts assigned to campaign {args['campaign_id']}.",
        }

    if tool_name == "override_company_target":
        user = await _get_user(token, session)
        company = await session.get(DiscoveredCompany, args["company_id"])
        if not company:
            raise ValueError("Company not found")
        project = await session.get(Project, company.project_id)
        if not project or project.user_id != user.id:
            raise ValueError("Company not found")

        old_target = company.is_target
        company.is_target = args["is_target"]
        company.analysis_reasoning = (
            f"[USER OVERRIDE] {args.get('reasoning', 'No reason given')}. "
            f"Previous: is_target={old_target}, AI: {(company.analysis_reasoning or 'none')[:200]}"
        )
        return {
            "updated": True,
            "company": company.name or company.domain,
            "was_target": old_target,
            "now_target": args["is_target"],
            "message": f"Override applied: {'target' if args['is_target'] else 'NOT target'} — {company.name or company.domain}. User override stored for learning.",
        }

    if tool_name == "provide_feedback":
        user = await _get_user(token, session)
        project = await session.get(Project, args["project_id"])
        if not project or project.user_id != user.id:
            raise ValueError("Project not found")

        from datetime import datetime
        log = MCPUsageLog(
            user_id=user.id,
            tool_name="user_feedback",
            action=args["feedback_type"],
            extra_data={
                "project_id": args["project_id"],
                "feedback_type": args["feedback_type"],
                "feedback_text": args["feedback_text"],
                "context": args.get("context"),
                "timestamp": str(datetime.utcnow()),
            },
        )
        session.add(log)

        feedback_type = args["feedback_type"]
        feedback_text = args["feedback_text"]

        # ── ACTIVE responses by feedback type ──
        if feedback_type == "filters":
            # Find latest run for this project
            latest_run = (await session.execute(
                select(GatheringRun).where(GatheringRun.project_id == project.id)
                .order_by(GatheringRun.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            current_filters = latest_run.filters if latest_run else {}

            return {
                "stored": True,
                "feedback_type": "filters",
                "message": (
                    f"Feedback stored: \"{feedback_text}\"\n\n"
                    f"Current Apollo filters: {current_filters.get('q_organization_keyword_tags', [])}\n"
                    f"Location: {current_filters.get('organization_locations', [])}\n"
                    f"Size: {current_filters.get('organization_num_employees_ranges', [])}\n\n"
                    f"To apply new filters, call tam_gather with updated filters (new pipeline run).\n"
                    f"Or tell me what to change and I'll prepare the updated filters for your approval."
                ),
                "current_filters": current_filters,
                "next_action": {
                    "tool": "tam_gather",
                    "description": "Start new pipeline run with adjusted filters. Show preview first.",
                },
                "_links": {"pipelines": "{settings.UI_BASE}/pipeline"},
            }

        elif feedback_type == "targets":
            # Find latest run with CP2 gate
            latest_run = (await session.execute(
                select(GatheringRun).where(GatheringRun.project_id == project.id)
                .order_by(GatheringRun.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            run_id = latest_run.id if latest_run else None

            return {
                "stored": True,
                "feedback_type": "targets",
                "message": (
                    f"Feedback stored: \"{feedback_text}\"\n\n"
                    f"Next: call tam_re_analyze to re-classify companies with improved prompt.\n"
                    f"Previous results preserved — new iteration will show before/after."
                ),
                "next_action": {
                    "tool": "tam_re_analyze",
                    "args": {"run_id": run_id} if run_id else {},
                    "description": "Re-analyze with feedback incorporated into prompt. Will ask approval first.",
                },
            }

        else:
            return {
                "stored": True,
                "feedback_type": feedback_type,
                "message": f"Feedback stored for '{project.name}'.",
            }

    if tool_name == "activate_campaign":
        user = await _get_user(token, session)
        if not args.get("user_confirmation"):
            raise ValueError("SAFETY: user_confirmation required. Quote the user's exact words confirming activation.")

        ctx = UserServiceContext(user.id, session)
        sl = await ctx.get_smartlead_service()
        if not sl.is_configured():
            raise ValueError("SmartLead not configured. Use configure_integration to add your SmartLead API key.")
        await sl.update_campaign_status(args["campaign_id"], "START")

        # Enable reply monitoring on activation
        from app.models.campaign import Campaign as CampaignModel
        campaign_result = await session.execute(
            select(CampaignModel).where(CampaignModel.external_id == str(args["campaign_id"]))
        )
        campaign_record = campaign_result.scalar_one_or_none()
        if campaign_record:
            campaign_record.status = "active"
            campaign_record.monitoring_enabled = True
            campaign_record.created_by = "mcp"

        from datetime import datetime
        log = MCPUsageLog(
            user_id=user.id,
            tool_name="activate_campaign",
            action="campaign_activated",
            extra_data={
                "campaign_id": args["campaign_id"],
                "user_confirmation": args["user_confirmation"],
                "monitoring_enabled": True,
                "timestamp": str(datetime.utcnow()),
            },
        )
        session.add(log)
        # Check if user has Telegram connected for notification prompt
        telegram_connected = False
        try:
            tg_setting = await session.execute(
                select(MCPIntegrationSetting).where(
                    MCPIntegrationSetting.user_id == user.id,
                    MCPIntegrationSetting.integration_name == "telegram",
                )
            )
            telegram_connected = tg_setting.scalar_one_or_none() is not None
        except Exception:
            pass

        telegram_msg = ""
        if telegram_connected:
            telegram_msg = "\n\nTelegram notifications are ON — you'll get pinged for warm replies."
        else:
            telegram_msg = (
                "\n\nWant Telegram notifications for warm replies? "
                f"Connect your Telegram account: {settings.UI_BASE}/setup (Telegram section)."
            )

        # CRM link with project filter
        project_id = campaign_record.project_id if campaign_record and hasattr(campaign_record, 'project_id') else None
        crm_link = f"{settings.UI_BASE}/crm?campaign={args['campaign_id']}"
        if project_id:
            crm_link += f"&project_id={project_id}"

        return {
            "activated": True,
            "campaign_id": args["campaign_id"],
            "status": "ACTIVE",
            "monitoring_enabled": True,
            "telegram_connected": telegram_connected,
            "message": (
                f"Campaign {args['campaign_id']} is now ACTIVE. Reply monitoring is ON."
                f"{telegram_msg}"
            ),
            "_links": {
                "smartlead": f"https://app.smartlead.ai/app/email-campaigns-v2/{args['campaign_id']}/analytics",
                "crm": crm_link,
            },
        }

    raise ValueError(f"Unknown tool: {tool_name}")


async def _resolve_project_id(project_name: str, user, session) -> int | None:
    """Resolve a project name to project_id. Returns None if not found.
    If multiple projects match, returns the most recent one.
    """
    from sqlalchemy import func as sa_func
    result = await session.execute(
        select(Project).where(
            Project.user_id == user.id,
            sa_func.lower(Project.name) == project_name.lower(),
        ).order_by(Project.id.desc()).limit(1)
    )
    project = result.scalar_one_or_none()
    return project.id if project else None


async def _resolve_project(project_name: str, user, session):
    """Resolve project name to full Project object.
    If multiple projects match, returns the most recent one.
    """
    from sqlalchemy import func as sa_func
    result = await session.execute(
        select(Project).where(
            Project.user_id == user.id,
            sa_func.lower(Project.name) == project_name.lower(),
        ).order_by(Project.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


def _get_campaign_name_filter(project) -> str | None:
    """Extract a campaign name substring filter from project's campaign_filters.

    campaign_filters is a list of full campaign names (e.g. ["Brand US Q1", "Brand EU Q2"]).
    We find the longest common prefix across all names — that's the search pattern.
    For ["Brand US Q1", "Brand EU Q2", "Brand APAC Q1"] → "Brand" (appears in all).
    """
    if not project or not project.campaign_filters:
        return None
    filters = project.campaign_filters
    if isinstance(filters, list) and filters:
        str_filters = [f for f in filters if isinstance(f, str) and len(f) > 2]
        if not str_filters:
            return None

        # Find common words across all campaign names
        # Split each name into words, find words that appear in most names
        from collections import Counter
        word_counts = Counter()
        for name in str_filters:
            words = set(w.lower() for w in name.split() if len(w) > 2)
            for w in words:
                word_counts[w] += 1

        # Find the word that appears in the most campaign names
        if word_counts:
            best_word, count = word_counts.most_common(1)[0]
            # Only use if it appears in at least 60% of campaigns
            if count >= len(str_filters) * 0.6:
                return best_word

        # Fallback: use longest common prefix
        sorted_filters = sorted(str_filters)
        first, last = sorted_filters[0], sorted_filters[-1]
        prefix = ""
        for a, b in zip(first, last):
            if a.lower() == b.lower():
                prefix += a
            else:
                break
        prefix = prefix.strip(" -_")
        if len(prefix) >= 3:
            return prefix

        # Last resort: shortest filter
        return min(str_filters, key=len)

    if isinstance(filters, dict):
        prefixes = filters.get("prefixes", [])
        contains = filters.get("contains", [])
        candidates = prefixes + contains
        if candidates:
            return min(candidates, key=len)
    return None


async def _call_replies_api(path: str, params: dict | None = None) -> dict:
    """Call MCP's OWN replies API (NOT main backend — fully independent).

    ARCHITECTURE RULE: MCP NEVER calls main backend. All data is in MCP's own DB.
    This function calls the MCP backend's own /api/replies/ endpoints.
    """
    import httpx
    try:
        async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=15) as client:
            resp = await client.get(f"/api/{path}", params=params, headers={"X-Company-ID": "1"})
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"API returned {resp.status_code}", "detail": resp.text[:500]}
    except Exception as e:
        return {"error": f"MCP replies API unreachable: {e}"}


async def _handle_reply_tool(tool_name: str, args: dict, user, session) -> dict:
    """Handle all reply-related tool calls.

    Data source: MCP's own reply data (mcp_replies table + analysis cache).
    NEVER calls main backend. Fully independent.
    """
    from app.services.reply_analysis_service import get_cached_analysis

    UI_BASE = settings.UI_BASE

    if tool_name == "replies_summary":
        project_name = args["project_name"]
        project_id = await _resolve_project_id(project_name, user, session)
        if not project_id:
            return {"error": f"Project '{project_name}' not found"}

        # Try MCP cache first
        cached = get_cached_analysis(project_id)
        if cached and cached.get("replies"):
            replies = cached["replies"]
            summary = cached.get("summary", {})
            cats = summary.get("by_category", {})
            total = len(replies)
            warm = sum(1 for r in replies if r.get("category") in ("interested", "meeting_request"))
            needs_reply = sum(1 for r in replies if r.get("needs_reply"))
            return {
                "project": project_name,
                "total_replies": total,
                "categories": cats,
                "warm_leads": warm,
                "needs_reply": needs_reply,
                "ooo_filtered": summary.get("ooo_skipped", 0),
                "ai_classified": summary.get("ai_classified", 0),
                "analysis_duration": f"{summary.get('duration_seconds', 0)}s",
                "analyzed_at": cached.get("analyzed_at", ""),
                "campaigns_analyzed": cached.get("campaigns", []),
                "message": f"Reply summary for '{project_name}': {total} total replies across {len(cached.get('campaigns', []))} campaigns. "
                           f"{warm} warm leads, {needs_reply} need reply. "
                           f"({summary.get('ooo_skipped', 0)} OOO auto-filtered, {summary.get('ai_classified', 0)} AI-classified).",
                "_links": {
                    "replies_ui": f"{UI_BASE}/tasks?project={project_name}",
                    "warm_replies": f"{UI_BASE}/crm?reply_category=interested&project={project_name}",
                    "meetings": f"{UI_BASE}/crm?reply_category=meeting_request&project={project_name}",
                    "followups": f"{UI_BASE}/crm?needs_followup=true&project={project_name}",
                },
            }

        # Fallback to MCP's own replies API — scope by campaign_filters
        project = await _resolve_project(project_name, user, session)
        campaign_filter = _get_campaign_name_filter(project)
        # /counts doesn't support campaign_name_contains, so fetch replies and aggregate
        params = {"received_since": "all", "page_size": 100, "group_by_contact": "true"}
        if campaign_filter:
            params["campaign_name_contains"] = campaign_filter
        data = await _call_replies_api("replies/", params)
        if "error" in data:
            return data

        replies = data.get("replies", [])
        total = data.get("total", len(replies))
        # Aggregate categories
        cats = {}
        for r in replies:
            cat = r.get("category", "other")
            cats[cat] = cats.get(cat, 0) + 1
        warm = cats.get("interested", 0) + cats.get("meeting_request", 0)
        return {
            "project": project_name,
            "total_replies": total,
            "categories": cats,
            "warm_leads": warm,
            "campaign_filter": campaign_filter,
            "message": f"Reply summary for '{project_name}' (campaigns matching '{campaign_filter}'): {total} total replies, {warm} warm.",
            "_links": {
                "replies_ui": f"{UI_BASE}/tasks?project={project_name}",
                "warm_replies": f"{UI_BASE}/crm?reply_category=interested&project={project_name}",
            },
        }

    if tool_name == "replies_list":
        project_name = args.get("project_name")
        category_filter = args.get("category")
        search_filter = args.get("search")
        needs_reply_filter = args.get("needs_reply")

        if project_name:
            project_id = await _resolve_project_id(project_name, user, session)
            if not project_id:
                return {"error": f"Project '{project_name}' not found"}

            # Try MCP cache
            cached = get_cached_analysis(project_id)
            if cached and cached.get("replies"):
                replies = cached["replies"]

                # Apply filters
                if category_filter:
                    replies = [r for r in replies if r.get("category") == category_filter]
                if search_filter:
                    s = search_filter.lower()
                    replies = [r for r in replies if s in (r.get("email", "") + r.get("name", "") + r.get("company", "")).lower()]
                if needs_reply_filter is not None:
                    val = str(needs_reply_filter).lower() == "true"
                    replies = [r for r in replies if r.get("needs_reply") == val]

                # Sort by received_at desc
                replies.sort(key=lambda r: r.get("received_at", ""), reverse=True)

                cards = []
                for r in replies[:30]:
                    cards.append({
                        "lead_name": r.get("name", ""),
                        "lead_email": r.get("email", ""),
                        "company": r.get("company", ""),
                        "category": r.get("category", "other"),
                        "confidence": r.get("confidence", 0),
                        "campaign": r.get("campaign_name", ""),
                        "message_preview": r.get("reply_text", "")[:200],
                        "received_at": r.get("received_at", ""),
                        "needs_reply": r.get("needs_reply", False),
                        "reasoning": r.get("reasoning", ""),
                    })

                return {
                    "total": len(replies),
                    "replies": cards,
                    "message": f"Found {len(replies)} replies" + (f" in category '{category_filter}'" if category_filter else ""),
                    "_links": {
                        "replies_ui": f"{UI_BASE}/tasks?project={project_name}" + (f"&category={category_filter}" if category_filter else ""),
                        "crm_filtered": f"{UI_BASE}/crm?reply_category={category_filter}&project={project_name}" if category_filter else f"{UI_BASE}/crm?project={project_name}",
                    },
                }

        # Fallback to MCP's own replies API — scope by campaign_filters
        params: dict = {"received_since": "all", "page_size": 30, "group_by_contact": "true"}
        if project_name:
            project = await _resolve_project(project_name, user, session)
            campaign_filter = _get_campaign_name_filter(project)
            if campaign_filter:
                params["campaign_name_contains"] = campaign_filter
        if category_filter:
            params["category"] = category_filter
        if search_filter:
            params["lead_email"] = search_filter
        if needs_reply_filter is not None:
            params["needs_reply"] = str(needs_reply_filter).lower()
        if args.get("page"):
            params["page"] = args["page"]

        data = await _call_replies_api("replies/", params)
        if "error" in data:
            return data
        replies = data.get("replies", [])
        cards = [{
            "lead_name": r.get("lead_name") or r.get("lead_email", ""),
            "lead_email": r.get("lead_email", ""),
            "company": r.get("company_name") or r.get("lead_company", ""),
            "category": r.get("category", "unknown"),
            "campaign": r.get("campaign_name", ""),
            "message_preview": (r.get("email_body") or r.get("reply_text") or "")[:200],
            "received_at": r.get("received_at", ""),
        } for r in replies[:30]]

        return {
            "total": data.get("total", len(cards)),
            "replies": cards,
            "message": f"Found {data.get('total', len(cards))} replies",
            "_links": {"replies_ui": f"{UI_BASE}/tasks" + (f"?project={project_name}" if project_name else "")},
        }

    if tool_name == "replies_followups":
        project_name = args.get("project_name")

        if project_name:
            project_id = await _resolve_project_id(project_name, user, session)
            if not project_id:
                return {"error": f"Project '{project_name}' not found"}

            # Try MCP cache — followups = needs_reply=true
            cached = get_cached_analysis(project_id)
            if cached and cached.get("replies"):
                replies = [r for r in cached["replies"] if r.get("needs_reply")]
                replies.sort(key=lambda r: r.get("received_at", ""), reverse=True)

                cards = [{
                    "lead_name": r.get("name", ""),
                    "lead_email": r.get("email", ""),
                    "company": r.get("company", ""),
                    "category": r.get("category", ""),
                    "campaign": r.get("campaign_name", ""),
                    "received_at": r.get("received_at", ""),
                    "reasoning": r.get("reasoning", ""),
                } for r in replies[:30]]

                return {
                    "total": len(replies),
                    "replies": cards,
                    "message": f"{len(replies)} leads need follow-up for '{project_name}'. "
                               f"These are interested, meeting requests, and questions that haven't been replied to yet.",
                    "_links": {
                        "followups_ui": f"{UI_BASE}/crm?needs_followup=true&project={project_name}",
                        "warm_leads": f"{UI_BASE}/crm?reply_category=interested&project={project_name}",
                    },
                }

        # Fallback — scope by campaign_filters
        params = {"needs_followup": "true", "received_since": "all", "page_size": 30}
        if project_name:
            project = await _resolve_project(project_name, user, session)
            campaign_filter = _get_campaign_name_filter(project)
            if campaign_filter:
                params["campaign_name_contains"] = campaign_filter
        data = await _call_replies_api("replies/", params)
        if "error" in data:
            return data
        replies = data.get("replies", [])
        cards = [{
            "lead_name": r.get("lead_name") or r.get("lead_email", ""),
            "lead_email": r.get("lead_email", ""),
            "company": r.get("company_name", ""),
            "category": r.get("category", ""),
            "campaign": r.get("campaign_name", ""),
            "received_at": r.get("received_at", ""),
        } for r in replies[:30]]
        return {
            "total": data.get("total", len(cards)),
            "replies": cards,
            "message": f"{data.get('total', len(cards))} leads need follow-up",
            "_links": {"followups_ui": f"{UI_BASE}/tasks/followups"},
        }

    if tool_name == "replies_deep_link":
        project_name = args["project_name"]
        category = args.get("category", "")
        tab = args.get("tab", "")

        # Build CRM deep link (more useful than tasks page)
        query_params = [f"project={project_name}"]
        if category:
            query_params.append(f"reply_category={category}")

        crm_url = f"{UI_BASE}/crm?" + "&".join(query_params)
        tasks_url = f"{UI_BASE}/tasks?" + "&".join(query_params)

        return {
            "crm_url": crm_url,
            "tasks_url": tasks_url,
            "message": f"Open replies for '{project_name}' in the browser",
            "_links": {"crm": crm_url, "tasks": tasks_url},
        }

    return {"error": f"Unknown reply tool: {tool_name}"}
