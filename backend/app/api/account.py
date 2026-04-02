"""Account API — credits tracking, API keys, usage stats."""
import logging
from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db import get_session
from app.models.user import MCPUser
from app.models.integration import MCPIntegrationSetting
from app.models.gathering import GatheringRun
from app.models.usage import MCPUsageLog
from app.models.pipeline import ExtractedContact, DiscoveredCompany
from app.models.campaign import Campaign
from app.models.project import Project
from app.auth.dependencies import get_current_user, get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


@router.get("")
@router.get("/")
async def get_account(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
    date_from: Optional[str] = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, alias="to", description="End date YYYY-MM-DD"),
):
    """Account overview — credits, usage, API keys status. Filterable by date range."""
    if not user:
        return {"authenticated": False, "message": "Not logged in"}

    # Integrations
    integrations_result = await session.execute(
        select(MCPIntegrationSetting).where(MCPIntegrationSetting.user_id == user.id)
    )
    integrations = [
        {"name": i.integration_name, "connected": i.is_connected, "info": i.connection_info}
        for i in integrations_result.scalars().all()
    ]

    # Get user's project IDs for scoping
    user_projects = await session.execute(
        select(Project.id).where(Project.user_id == user.id)
    )
    project_ids = [pid for (pid,) in user_projects.all()]

    # ── Date range filter ──
    from datetime import datetime
    date_filter_from = None
    date_filter_to = None
    if date_from:
        try:
            date_filter_from = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            date_filter_to = datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # ── Apollo credits ──

    # 1. Gathering credits (from GatheringRun.credits_used, scoped to user's projects)
    gathering_credits = 0
    if project_ids:
        q = select(func.coalesce(func.sum(GatheringRun.credits_used), 0)).where(
            GatheringRun.project_id.in_(project_ids)
        )
        if date_filter_from:
            q = q.where(GatheringRun.created_at >= date_filter_from)
        if date_filter_to:
            q = q.where(GatheringRun.created_at <= date_filter_to)
        gathering_credits = (await session.execute(q)).scalar() or 0

    # 2. Filter discovery credits (from usage log extra_data.credits_spent)
    filter_logs = await session.execute(
        select(MCPUsageLog.extra_data).where(
            MCPUsageLog.user_id == user.id,
            MCPUsageLog.tool_name == "suggest_apollo_filters",
        )
    )
    filter_discovery_credits = 0
    filter_discovery_calls = 0
    for (extra,) in filter_logs.all():
        filter_discovery_calls += 1
        if isinstance(extra, dict):
            cs = extra.get("credits_spent", {})
            if isinstance(cs, dict):
                filter_discovery_credits += cs.get("total", 0) or cs.get("total_apollo", 0) or 0
            else:
                filter_discovery_credits += 6  # fallback
        else:
            filter_discovery_credits += 6  # fallback

    total_apollo = gathering_credits + filter_discovery_credits

    # ── Tool call counts ──
    total_tool_calls = (await session.execute(
        select(func.count(MCPUsageLog.id)).where(MCPUsageLog.user_id == user.id)
    )).scalar() or 0

    # GPT analysis calls (tam_analyze + tam_re_analyze)
    analysis_calls = (await session.execute(
        select(func.count(MCPUsageLog.id)).where(
            MCPUsageLog.user_id == user.id,
            MCPUsageLog.tool_name.in_(["tam_analyze", "tam_re_analyze"]),
        )
    )).scalar() or 0

    # ── User-scoped stats ──
    total_contacts = 0
    total_companies = 0
    total_campaigns = 0
    if project_ids:
        total_contacts = (await session.execute(
            select(func.count(ExtractedContact.id)).where(
                ExtractedContact.project_id.in_(project_ids)
            )
        )).scalar() or 0

        total_companies = (await session.execute(
            select(func.count(DiscoveredCompany.id)).where(
                DiscoveredCompany.project_id.in_(project_ids)
            )
        )).scalar() or 0

        total_campaigns = (await session.execute(
            select(func.count(Campaign.id)).where(
                Campaign.project_id.in_(project_ids)
            )
        )).scalar() or 0

    # ── Pipeline runs with credits ──
    pipeline_runs = []
    if project_ids:
        runs_result = await session.execute(
            select(GatheringRun)
            .where(GatheringRun.project_id.in_(project_ids))
            .order_by(GatheringRun.created_at.desc())
            .limit(20)
        )
        for run in runs_result.scalars().all():
            pipeline_runs.append({
                "id": run.id,
                "source_type": run.source_type,
                "phase": run.current_phase,
                "companies": run.new_companies_count or 0,
                "targets": int((run.target_rate or 0) * (run.new_companies_count or 0)),
                "target_rate": f"{(run.target_rate or 0)*100:.0f}%",
                "credits_used": run.credits_used or 0,
                "created_at": str(run.created_at) if run.created_at else None,
            })

    # ── OpenAI detailed costs (from usage logs with service=openai) ──
    openai_by_model = {}
    openai_total_cost = 0.0
    openai_total_tokens = 0

    # Match both old format (action="api_cost") and new format (action="cost_openai", "cost_apollo", etc.)
    usage_logs_q = select(MCPUsageLog.extra_data).where(
        MCPUsageLog.user_id == user.id,
        (MCPUsageLog.action == "api_cost") | (MCPUsageLog.action.like("cost_%")),
    )
    if date_filter_from:
        usage_logs_q = usage_logs_q.where(MCPUsageLog.created_at >= date_filter_from)
    if date_filter_to:
        usage_logs_q = usage_logs_q.where(MCPUsageLog.created_at <= date_filter_to)

    for (extra,) in (await session.execute(usage_logs_q)).all():
        if not isinstance(extra, dict) or extra.get("service") != "openai":
            continue
        model = extra.get("model", "unknown")
        if model not in openai_by_model:
            openai_by_model[model] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0}
        openai_by_model[model]["calls"] += 1
        openai_by_model[model]["input_tokens"] += extra.get("input_tokens", 0)
        openai_by_model[model]["output_tokens"] += extra.get("output_tokens", 0)
        openai_by_model[model]["cost_usd"] += extra.get("cost_usd", 0)
        openai_total_cost += extra.get("cost_usd", 0)
        openai_total_tokens += extra.get("total_tokens", 0)

    # ── Apify costs ──
    apify_domains = 0
    apify_bytes = 0
    apify_cost = 0.0

    # ── MCP protocol tokens ──
    mcp_input_tokens = 0
    mcp_output_tokens = 0
    mcp_calls = 0

    # Re-query for apify + mcp (can't reuse exhausted iterator)
    for (extra,) in (await session.execute(usage_logs_q)).all():
        if not isinstance(extra, dict):
            continue
        svc = extra.get("service")
        if svc == "apify":
            apify_domains += extra.get("domains_scraped", 0)
            apify_bytes += extra.get("bytes_used", 0)
            apify_cost += extra.get("cost_usd", 0)
        elif svc == "mcp":
            mcp_input_tokens += extra.get("input_tokens", 0)
            mcp_output_tokens += extra.get("output_tokens", 0)
            mcp_calls += 1

    apollo_cost_usd = total_apollo * 0.01  # $0.01 per credit estimate

    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        },
        "integrations": integrations,
        "costs": {
            "total_usd": round(apollo_cost_usd + openai_total_cost + apify_cost, 4),
            "apollo": {
                "credits": total_apollo,
                "gathering_credits": gathering_credits,
                "enrichment_credits": filter_discovery_credits,
                "cost_usd": round(apollo_cost_usd, 4),
            },
            "openai": {
                "total_tokens": openai_total_tokens,
                "total_cost_usd": round(openai_total_cost, 4),
                "by_model": {
                    model: {
                        "calls": d["calls"],
                        "input_tokens": d["input_tokens"],
                        "output_tokens": d["output_tokens"],
                        "cost_usd": round(d["cost_usd"], 4),
                    }
                    for model, d in openai_by_model.items()
                },
            },
            "apify": {
                "websites_scraped": apify_domains,
                "bytes_used": apify_bytes,
                "gb_used": round(apify_bytes / (1024**3), 3) if apify_bytes else 0,
                "cost_usd": round(apify_cost, 4),
            },
            "mcp": {
                "tool_calls": mcp_calls,
                "input_tokens": mcp_input_tokens,
                "output_tokens": mcp_output_tokens,
                "total_tokens": mcp_input_tokens + mcp_output_tokens,
                # MCP cost estimate: ~$1/campaign (20 campaigns/mo = $20/mo)
                # Breakdown per campaign: ~50 tool calls × 8K tokens avg = 400K tokens
                # At gpt-4o-mini pricing: $0.15/1M input + $0.60/1M output ≈ $0.30/campaign
                # Apollo: ~15 credits × $0.01 = $0.15
                # Apify: ~100 websites × $0.0001 = $0.01
                # OpenAI (classification): ~1000 companies × $0.003 = $3.00
                # Total per campaign: ~$3.50, 20 campaigns = $70/mo realistic
                # $20/mo covers ~6 campaigns (budget mode)
                "cost_usd": round((mcp_input_tokens * 0.15 + mcp_output_tokens * 0.60) / 1_000_000, 4),
            },
            "estimate_20_campaigns_mo": {
                "apollo_credits": 300,
                "apollo_usd": 3.00,
                "openai_usd": 60.00,
                "apify_usd": 2.00,
                "mcp_usd": 6.00,
                "total_usd": 71.00,
                "note": "Estimate for 20 campaigns/mo, 1000 contacts each, at current pricing",
            },
        },
        "stats": {
            "total_contacts": total_contacts,
            "total_companies": total_companies,
            "total_campaigns": total_campaigns,
            "total_tool_calls": total_tool_calls,
        },
        "pipeline_runs": pipeline_runs,
    }


@router.get("/usage")
async def get_usage(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Detailed usage breakdown by tool."""
    if not user:
        return {"authenticated": False}

    # Usage by tool
    result = await session.execute(
        select(MCPUsageLog.tool_name, func.count(MCPUsageLog.id))
        .where(MCPUsageLog.user_id == user.id)
        .group_by(MCPUsageLog.tool_name)
        .order_by(func.count(MCPUsageLog.id).desc())
    )
    by_tool = {row[0]: row[1] for row in result.all()}

    # Recent activity
    recent = await session.execute(
        select(MCPUsageLog)
        .where(MCPUsageLog.user_id == user.id)
        .order_by(MCPUsageLog.created_at.desc())
        .limit(20)
    )
    recent_logs = [
        {"tool": log.tool_name, "action": log.action, "at": str(log.created_at)}
        for log in recent.scalars().all()
    ]

    return {
        "by_tool": by_tool,
        "recent": recent_logs,
    }


@router.get("/conversations")
async def get_conversations(
    user: MCPUser = Depends(get_optional_user),
    session_id: str = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """Full conversation log — every message between user's Claude agent and MCP."""
    if not user:
        return {"authenticated": False}

    from app.models.usage import MCPConversationLog
    q = (
        select(MCPConversationLog)
        .where(MCPConversationLog.user_id == user.id)
        .order_by(MCPConversationLog.created_at.desc())
        .limit(limit)
    )
    if session_id:
        q = q.where(MCPConversationLog.session_id == session_id)

    result = await session.execute(q)
    logs = result.scalars().all()

    return {
        "conversations": [
            {
                "id": log.id,
                "direction": log.direction,
                "method": log.method,
                "message_type": log.message_type,
                "summary": log.content_summary,
                "raw": log.raw_json,
                "session_id": log.session_id,
                "at": str(log.created_at),
            }
            for log in logs
        ],
        "total": len(logs),
    }
