"""Pipeline REST API — read-only endpoints for frontend, auth-required for writes."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func, outerjoin

from app.db import get_session
from app.models.user import MCPUser, MCPApiToken
from app.models.project import Project, Company
from app.models.gathering import GatheringRun, ApprovalGate, CompanyScrape, CompanySourceLink
from app.models.pipeline import DiscoveredCompany
from app.models.campaign import GeneratedSequence, Campaign
from app.models.usage import MCPUsageLog, MCPConversationLog
from app.auth.dependencies import get_current_user, get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict = {}


@router.post("/tool-call")
async def direct_tool_call(
    req: ToolCallRequest,
    request: Request,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Direct REST tool call — same as MCP protocol but synchronous.

    Logs to conversation history + usage logs.
    Use for testing and for non-SSE clients.
    """
    from app.mcp.dispatcher import _dispatch, _safe_truncate
    import time as _time
    from datetime import datetime

    # Extract raw token from request header (needed by _dispatch internals)
    token = request.headers.get("X-MCP-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")

    start = _time.monotonic()

    try:
        result = await _dispatch(req.tool_name, req.arguments, token, session)
        latency = int((_time.monotonic() - start) * 1000)

        # Log request + response to conversation history
        session_tag = f"rest-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        try:
            # Request
            session.add(MCPConversationLog(
                user_id=user.id,
                session_id=session_tag,
                direction="client_to_server",
                method="tools/call",
                message_type="tool_call",
                content_summary=f"Tool call: {req.tool_name}({_safe_truncate(req.arguments)})",
                raw_json={"tool": req.tool_name, "args": req.arguments},
            ))
            # Response
            result_summary = ""
            if isinstance(result, dict):
                result_summary = str({k: v for k, v in result.items() if k not in ("_links", "prompt_text")})[:300]
            session.add(MCPConversationLog(
                user_id=user.id,
                session_id=session_tag,
                direction="server_to_client",
                method="tools/call",
                message_type="tool_result",
                content_summary=f"Result: {result_summary}",
                raw_json={"tool": req.tool_name, "result": _safe_truncate(result) if isinstance(result, dict) else str(result)[:500]},
            ))
        except Exception:
            pass

        # Log usage
        try:
            log_extra = {"args": _safe_truncate(req.arguments), "latency_ms": latency}
            session.add(MCPUsageLog(
                user_id=user.id,
                tool_name=req.tool_name,
                action="tool_call",
                extra_data=log_extra,
            ))
        except Exception:
            pass

        await session.commit()
        return {"result": result, "latency_ms": latency}

    except Exception as e:
        await session.rollback()
        raise HTTPException(400, str(e))


async def _get_user_project_ids(user, session) -> list[int]:
    """Get all project IDs owned by this user."""
    result = await session.execute(
        select(Project.id).where(Project.user_id == user.id, Project.is_active == True)
    )
    return [pid for (pid,) in result.all()]


class ProjectCreateRequest(BaseModel):
    name: str
    website: Optional[str] = None
    target_segments: Optional[str] = None
    target_industries: Optional[str] = None
    sender_name: Optional[str] = None
    sender_company: Optional[str] = None
    sender_position: Optional[str] = None


@router.post("/projects")
async def create_project(
    req: ProjectCreateRequest,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Company).limit(1))
    company = result.scalar_one_or_none()
    if not company:
        company = Company(name=f"{user.name}'s Company")
        session.add(company)
        await session.flush()

    # Scrape website to extract offer context (blind discovery)
    target_segments = req.target_segments or ""
    website_scraped = False
    if req.website:
        import httpx, re
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(req.website)
                if resp.status_code == 200:
                    html = resp.text
                    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()[:2000]
                    target_segments = (target_segments + f"\n\nCompany website ({req.website}): {text}").strip()
                    website_scraped = True
        except Exception as e:
            logger.warning(f"Website scrape failed for {req.website}: {e}")

    project = Project(
        company_id=company.id, user_id=user.id, name=req.name,
        target_segments=target_segments or None, target_industries=req.target_industries,
        sender_name=req.sender_name, sender_company=req.sender_company,
        sender_position=req.sender_position,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return {
        "project_id": project.id, "name": project.name,
        "website_scraped": website_scraped,
        "context_length": len(target_segments) if target_segments else 0,
    }


@router.get("/projects")
async def list_projects(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return []
    query = select(Project).where(Project.is_active == True, Project.user_id == user.id)
    result = await session.execute(query)
    return [{"id": p.id, "name": p.name, "website": getattr(p, 'website', None),
             "target_segments": p.target_segments,
             "offer_summary": p.offer_summary if isinstance(p.offer_summary, dict) and p.offer_summary.get("primary_offer") else None,
             "offer_approved": getattr(p, 'offer_approved', False),
             "sender_name": p.sender_name, "sender_company": p.sender_company,
             "campaign_filters": p.campaign_filters or []} for p in result.scalars().all()]


# ── Read-only endpoints (no auth required — shared via links) ──

@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models.pipeline import ExtractedContact
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    # User-scope: verify run belongs to user's project
    if user:
        project = await session.get(Project, run.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Run not found")

    # Get project name
    project = await session.get(Project, run.project_id)

    # All gates for this run
    gates_result = await session.execute(
        select(ApprovalGate).where(ApprovalGate.gathering_run_id == run_id)
        .order_by(ApprovalGate.created_at)
    )
    all_gates = gates_result.scalars().all()

    # Count discovered companies — scoped to THIS run via CompanySourceLink
    dc_count = await session.execute(
        select(sa_func.count(DiscoveredCompany.id))
        .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
        .where(CompanySourceLink.gathering_run_id == run_id)
    )
    total_companies = dc_count.scalar() or 0

    # Count scrapes — from DiscoveredCompany.status field (streaming pipeline)
    scrape_stats = await session.execute(
        select(DiscoveredCompany.status, sa_func.count(DiscoveredCompany.id))
        .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
        .where(CompanySourceLink.gathering_run_id == run_id)
        .group_by(DiscoveredCompany.status)
    )
    status_counts = {row[0] or "gathered": row[1] for row in scrape_stats.all()}
    scraped_ok = status_counts.get("scraped", 0) + status_counts.get("target", 0) + status_counts.get("rejected", 0)
    scraped_err = status_counts.get("scrape_failed", 0)
    classified_count = status_counts.get("target", 0) + status_counts.get("rejected", 0) + status_counts.get("classify_failed", 0)
    targets_classified = status_counts.get("target", 0)
    rejected_classified = status_counts.get("rejected", 0)

    # Count targets with 0 contacts
    targets_no_contacts = 0
    if targets_classified > 0:
        _tnc = await session.execute(
            select(sa_func.count(DiscoveredCompany.id))
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .outerjoin(ExtractedContact, ExtractedContact.discovered_company_id == DiscoveredCompany.id)
            .where(
                CompanySourceLink.gathering_run_id == run_id,
                DiscoveredCompany.is_target == True,
                ExtractedContact.id == None,
            )
        )
        targets_no_contacts = _tnc.scalar() or 0

    # Fallback: old CompanyScrape table
    if scraped_ok == 0 and scraped_err == 0:
        scrape_result = await session.execute(
            select(CompanyScrape.scrape_status)
            .join(DiscoveredCompany, DiscoveredCompany.id == CompanyScrape.discovered_company_id)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(CompanySourceLink.gathering_run_id == run_id)
        )
        scrapes = scrape_result.all()
        scraped_ok = sum(1 for s in scrapes if s[0] == "success")
        scraped_err = sum(1 for s in scrapes if s[0] != "success")

    # Find campaign linked to this run (direct FK first, then via sequence)
    campaign_info = None
    campaign = None
    if run.campaign_id:
        campaign = await session.get(Campaign, run.campaign_id)
    if not campaign:
        seq_result = await session.execute(
            select(GeneratedSequence).where(
                GeneratedSequence.project_id == run.project_id,
                GeneratedSequence.pushed_campaign_id.isnot(None),
            ).order_by(GeneratedSequence.pushed_at.desc()).limit(1)
        )
        seq = seq_result.scalar_one_or_none()
        if seq and seq.pushed_campaign_id:
            campaign = await session.get(Campaign, seq.pushed_campaign_id)
    if campaign:
        campaign_info = {
            "id": campaign.id,
            "name": campaign.name,
            "smartlead_id": campaign.external_id,
            "smartlead_url": f"https://app.smartlead.ai/app/email-campaigns-v2/{campaign.external_id}/analytics" if campaign.external_id else None,
            "status": campaign.status,
            "email_accounts": getattr(campaign, 'email_account_ids', None) or [],
        }

    # Count extracted contacts for this run's targets
    people_count_q = await session.execute(
        select(sa_func.count(ExtractedContact.id))
        .join(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
        .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
        .where(CompanySourceLink.gathering_run_id == run_id, DiscoveredCompany.is_target == True)
    )
    live_people_count = people_count_q.scalar() or 0

    # Count target companies
    targets_count_q = await session.execute(
        select(sa_func.count(DiscoveredCompany.id))
        .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
        .where(CompanySourceLink.gathering_run_id == run_id, DiscoveredCompany.is_target == True)
    )
    live_targets_count = targets_count_q.scalar() or 0

    # KPI fields
    from math import ceil
    tp = run.target_people or 100
    mpc = run.max_people_per_company or 3
    tc = run.target_companies or ceil(tp / mpc)
    people_found = live_people_count or run.total_people_found or 0
    targets_found = live_targets_count or run.total_targets_found or 0

    # Timing — use duration_seconds for completed runs, live calculation for running
    from datetime import datetime, timezone as tz
    elapsed_seconds = None
    eta_seconds = None
    if run.duration_seconds and run.status in ('completed', 'insufficient', 'failed'):
        elapsed_seconds = run.duration_seconds
    elif run.started_at:
        now = datetime.now(tz.utc)
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=tz.utc)
        elapsed_seconds = int((now - started).total_seconds())
        if people_found > 0 and people_found < tp and elapsed_seconds > 0:
            rate = people_found / elapsed_seconds
            eta_seconds = int((tp - people_found) / rate)

    return {
        "id": run.id,
        "status": run.status,
        "current_phase": run.current_phase,
        "source_type": run.source_type,
        "filters": run.filters,
        "project_id": run.project_id,
        "project_name": project.name if project else "Unknown",
        "new_companies": run.new_companies_count,
        "duplicates": run.duplicate_count,
        "rejected": run.rejected_count,
        "total_companies": total_companies,
        "scraped_ok": scraped_ok,
        "scraped_errors": scraped_err,
        "classified_count": classified_count if 'classified_count' in dir() else None,
        "targets_classified": targets_classified if 'targets_classified' in dir() else None,
        "rejected_classified": rejected_classified if 'rejected_classified' in dir() else None,
        "targets_no_contacts": targets_no_contacts if 'targets_no_contacts' in dir() else None,
        "scrape_rate_pct": round(scraped_ok / total_companies * 100) if total_companies > 0 else 0,
        "target_rate_pct": round(targets_classified / scraped_ok * 100) if scraped_ok > 0 and 'targets_classified' in dir() else (run.target_rate or 0),
        "credits_used": run.credits_used,
        "apollo_total": run.raw_results_count,  # Total companies in Apollo for these filters
        "apollo_pages_available": (run.raw_results_count // 100 + 1) if run.raw_results_count else None,
        "people_filters": run.people_filters or (lambda: {
            "person_titles": ((project.offer_summary or {}).get("target_roles", {}).get("titles", []) if project else []),
            "person_seniorities": ((project.offer_summary or {}).get("target_roles", {}).get("seniorities", None) if project else None) or ["c_suite", "vp", "director"],
            "max_people_per_company": run.max_people_per_company or 3,
        })(),
        "created_at": str(run.created_at) if run.created_at else None,
        "campaign": campaign_info,
        # KPI + Progress
        "kpi": {
            "target_people": tp,
            "max_people_per_company": mpc,
            "target_companies": tc,
        },
        "progress": {
            "targets_found": targets_found,
            "people_found": people_found,
            "scraped": scraped_ok,
            "classified": classified_count if 'classified_count' in dir() and classified_count else (scraped_ok if scraped_ok else 0),
            "pages_fetched": run.pages_fetched or 0,
            "iteration": run.current_iteration or 0,
            "people_pct": round(people_found / tp * 100, 1) if tp > 0 else 0,
            "targets_pct": round(targets_found / tc * 100, 1) if tc > 0 else 0,
        },
        "timing": {
            "started_at": str(run.started_at) if run.started_at else None,
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": eta_seconds,
        },
        "paused_at": str(run.paused_at) if run.paused_at else None,
        "gates": [
            {
                "gate_id": g.id,
                "type": g.gate_type,
                "label": g.gate_label,
                "status": g.status,
                "scope": g.scope,
                "decided_at": str(g.decided_at) if g.decided_at else None,
            }
            for g in all_gates
        ],
    }


@router.post("/runs/{run_id}/pause")
async def pause_run(
    run_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Pause a running pipeline. Progress is saved; resume continues from here."""
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if user:
        project = await session.get(Project, run.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Run not found")
    if run.status != "running":
        raise HTTPException(400, f"Cannot pause: status is '{run.status}'")

    from datetime import datetime, timezone
    run.status = "paused"
    run.paused_at = datetime.now(timezone.utc)
    await session.commit()

    return {"id": run.id, "status": "paused", "paused_at": str(run.paused_at)}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Resume a paused pipeline from where it stopped."""
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if user:
        project = await session.get(Project, run.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Run not found")
    if run.status not in ("paused", "insufficient"):
        raise HTTPException(400, f"Cannot resume: status is '{run.status}'")

    from datetime import datetime, timezone
    run.status = "running"
    run.resumed_at = datetime.now(timezone.utc)
    await session.commit()

    # Spawn background orchestrator task
    from app.services.pipeline_orchestrator import is_pipeline_running, start_pipeline_background
    if not is_pipeline_running(run.id):
        start_pipeline_background(run.id, run.filters or {}, user.id if user else 0)

    return {"id": run.id, "status": "running", "resumed_at": str(run.resumed_at)}


class KPIUpdateRequest(BaseModel):
    target_people: Optional[int] = None
    max_people_per_company: Optional[int] = None
    target_companies: Optional[int] = None


@router.patch("/runs/{run_id}/kpi")
async def update_run_kpi(
    run_id: int,
    body: KPIUpdateRequest,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Update KPI targets on a pipeline run."""
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if user:
        project = await session.get(Project, run.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Run not found")

    from math import ceil
    user_set = set()
    if body.target_people is not None:
        run.target_people = body.target_people
        user_set.add("target_people")
    if body.max_people_per_company is not None:
        run.max_people_per_company = body.max_people_per_company
        user_set.add("max_people_per_company")
    if body.target_companies is not None:
        run.target_companies = body.target_companies
        user_set.add("target_companies")

    # KPI alignment
    tp = run.target_people or 100
    mpc = run.max_people_per_company or 3
    if "target_companies" in user_set and "target_people" not in user_set:
        tp = run.target_companies * mpc
        run.target_people = tp
    else:
        run.target_companies = ceil(tp / mpc)

    await session.commit()

    return {
        "id": run.id,
        "kpi": {
            "target_people": run.target_people,
            "max_people_per_company": run.max_people_per_company,
            "target_companies": run.target_companies,
        },
    }


@router.get("/runs/{run_id}/companies")
async def get_run_companies(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    iteration: Optional[int] = Query(None, description="Filter to specific gathering run ID"),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Get unique company IDs (dedup by domain) + latest scrape per company
    unique_dc_ids_q = (
        select(sa_func.min(DiscoveredCompany.id).label("dc_id"))
        .where(DiscoveredCompany.project_id == run.project_id)
        .group_by(DiscoveredCompany.domain)
    ).subquery()

    latest_scrape_q = (
        select(
            CompanyScrape.discovered_company_id,
            sa_func.max(CompanyScrape.id).label("scrape_id"),
        )
        .where(CompanyScrape.is_current == True)
        .group_by(CompanyScrape.discovered_company_id)
    ).subquery()

    stmt = (
        select(DiscoveredCompany, CompanyScrape)
        .join(unique_dc_ids_q, DiscoveredCompany.id == unique_dc_ids_q.c.dc_id)
        .outerjoin(latest_scrape_q, latest_scrape_q.c.discovered_company_id == DiscoveredCompany.id)
        .outerjoin(CompanyScrape, CompanyScrape.id == latest_scrape_q.c.scrape_id)
    )

    stmt = stmt.order_by(DiscoveredCompany.domain).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    rows = result.all()

    # Total count — deduplicated by domain
    count_stmt = (
        select(sa_func.count(sa_func.distinct(DiscoveredCompany.domain)))
        .where(DiscoveredCompany.project_id == run.project_id)
    )
    total_companies = (await session.execute(count_stmt)).scalar() or 0

    # Count contacts per company
    from app.models.pipeline import ExtractedContact
    contact_counts: dict = {}
    cc_result = await session.execute(
        select(ExtractedContact.discovered_company_id, sa_func.count(ExtractedContact.id))
        .where(ExtractedContact.project_id == run.project_id)
        .group_by(ExtractedContact.discovered_company_id)
    )
    for dc_id, cnt in cc_result.all():
        contact_counts[dc_id] = cnt

    # Check if any targets exist (to signal frontend to show contacts column)
    has_targets = any(c.is_target for c, _ in rows)

    companies = [_company_to_dict(c, scrape=s, truncate_reasoning=True, contacts_count=contact_counts.get(c.id, 0)) for c, s in rows]
    return {
        "companies": companies,
        "has_targets": has_targets,
        "total_contacts": sum(contact_counts.values()),
        "total_companies": total_companies,
        "page": page,
        "page_size": page_size,
    }


@router.get("/companies/{company_id}")
async def get_company_detail(
    company_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Full detail for a single company — user-scoped."""
    company = await session.get(DiscoveredCompany, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    if user:
        project = await session.get(Project, company.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Company not found")
    return await _get_company_detail(company, session)


@router.get("/runs/{run_id}/companies/{company_id}")
async def get_run_company_detail(
    run_id: int,
    company_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Full detail for a single company scoped to a run — user-scoped."""
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if user:
        project = await session.get(Project, run.project_id)
        if project and project.user_id != user.id:
            raise HTTPException(404, "Run not found")

    company = await session.get(DiscoveredCompany, company_id)
    if not company or company.project_id != run.project_id:
        raise HTTPException(404, "Company not found in this run's project")

    return await _get_company_detail(company, session)


async def _get_company_detail(company, session):
    """Full company detail with scrape text and raw source data."""
    # Get current scrape
    scrape_result = await session.execute(
        select(CompanyScrape)
        .where(
            CompanyScrape.discovered_company_id == company.id,
            CompanyScrape.is_current == True,
        )
        .limit(1)
    )
    scrape = scrape_result.scalar_one_or_none()

    data = _company_to_dict(company, scrape=scrape, truncate_reasoning=False)

    # Add full detail fields
    if scrape:
        data["scrape_text"] = scrape.clean_text
        data["scrape_error"] = scrape.error_message
        data["scrape_http_code"] = scrape.http_status_code
        data["scrape_timestamp"] = str(scrape.scraped_at) if scrape.scraped_at else None
    else:
        data["scrape_text"] = None
        data["scrape_error"] = None
        data["scrape_http_code"] = None
        data["scrape_timestamp"] = None

    # Full source_data (already included but explicitly confirm it's not stripped)
    data["source_data"] = company.source_data or {}

    return data


@router.get("/iterations")
async def list_iterations(
    project_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_session),
    user: MCPUser = Depends(get_optional_user),
):
    """List gathering runs (iterations) scoped to user's projects."""
    query = select(GatheringRun).order_by(GatheringRun.created_at.desc())
    if user:
        user_projects = await session.execute(select(Project.id).where(Project.user_id == user.id))
        pids = [pid for (pid,) in user_projects.all()]
        if pids:
            query = query.where(GatheringRun.project_id.in_(pids))
        else:
            return []
    if project_id is not None:
        query = query.where(GatheringRun.project_id == project_id)
    query = query.limit(50)

    result = await session.execute(query)
    runs = result.scalars().all()

    iterations = []
    for r in runs:
        # Count targets for this run's project that were discovered in this run
        target_count_result = await session.execute(
            select(sa_func.count(DiscoveredCompany.id))
            .join(
                CompanySourceLink,
                CompanySourceLink.discovered_company_id == DiscoveredCompany.id,
            )
            .where(
                CompanySourceLink.gathering_run_id == r.id,
                DiscoveredCompany.is_target == True,
            )
        )
        target_count = target_count_result.scalar() or 0

        # Get project name
        project = await session.get(Project, r.project_id)

        # Count ACTUAL companies linked to this run (not the stale field)
        actual_count_result = await session.execute(
            select(sa_func.count(CompanySourceLink.id))
            .where(CompanySourceLink.gathering_run_id == r.id)
        )
        actual_companies = actual_count_result.scalar() or 0

        iterations.append({
            "id": r.id,
            "source_type": r.source_type,
            "filters": r.filters,
            "people_filters": getattr(r, 'people_filters', None),
            "new_companies": actual_companies,
            "new_companies_count": actual_companies,
            "current_phase": r.current_phase,
            "status": r.status,
            "target_count": target_count,
            "project_id": r.project_id,
            "project_name": project.name if project else "Unknown",
            "created_at": str(r.created_at) if r.created_at else None,
        })

    return iterations


@router.get("/usage-logs")
async def get_usage_logs(
    run_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Return MCP usage logs — user-scoped."""
    query = select(MCPUsageLog).order_by(MCPUsageLog.created_at.desc())
    if user:
        query = query.where(MCPUsageLog.user_id == user.id)

    if run_id is not None:
        # Filter logs where extra_data contains the run_id
        query = query.where(
            MCPUsageLog.extra_data["run_id"].as_integer() == run_id
        )

    query = query.limit(limit)
    result = await session.execute(query)
    logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "tool_name": log.tool_name,
            "metadata": log.extra_data,
            "created_at": str(log.created_at) if log.created_at else None,
        }
        for log in logs
    ]


def _compute_company_status(c, scrape=None):
    """Derive a single status string from the company's pipeline state flags."""
    if c.is_blacklisted:
        return "blacklisted"
    if c.is_pre_filtered:
        return "filtered"
    # Check scrape state
    if scrape:
        if scrape.scrape_status != "success":
            return "scrape_failed"
    # Check analysis state
    if c.is_target is True:
        if c.is_enriched:
            return "verified"
        return "target"
    if c.is_target is False:
        return "rejected"
    # is_target is None — analysis hasn't run yet
    if scrape and scrape.scrape_status == "success":
        return "scraped"
    # No scrape yet, not blacklisted, not filtered
    return "gathered"


# SIC codes → human labels (4-digit for precision, 2-digit as fallback)
_SIC = {
    "7371": "Computer Programming", "7372": "Software", "7374": "Data Processing",
    "7375": "Computer Facilities Management", "7376": "Computer Maintenance",
    "7378": "Computer Maintenance", "7379": "Computer Services",
    "7361": "Staffing & Recruiting", "7363": "Staffing & Recruiting",
    "73": "IT & Business Services", "72": "Computer Services", "48": "Communications",
    "36": "Electronics", "35": "Industrial Equipment", "38": "Instruments",
    "50": "Wholesale", "59": "Retail", "60": "Banking", "61": "Credit",
    "62": "Securities", "63": "Insurance", "65": "Real Estate",
    "80": "Healthcare", "82": "Education", "87": "Engineering & Management",
    "27": "Publishing", "49": "Utilities", "15": "Construction",
}

# NAICS codes → human labels (5-digit for precision, 3-digit as fallback)
_NAICS = {
    "54151": "Computer Systems Design", "54161": "Management Consulting",
    "54171": "Scientific R&D", "54131": "Architectural Services",
    "54111": "Legal Services", "54121": "Accounting",
    "51121": "Software Publishing", "51913": "Internet Publishing",
    "51821": "Data Processing & Hosting", "51911": "News Syndicates",
    "54169": "Management & Technical Consulting",
    "51611": "Internet Publishing", "51711": "Wired Telecom",
    "54181": "Advertising", "56132": "Staffing & Recruiting",
    "511": "Software & Publishing", "518": "Data & Hosting", "519": "Web & Search",
    "541": "Professional Services", "561": "Business Support", "517": "Telecom",
    "522": "Banking", "523": "Securities", "524": "Insurance", "531": "Real Estate",
    "611": "Education", "621": "Healthcare", "512": "Media", "334": "Electronics",
    "454": "E-Commerce",
}


def _company_to_dict(c, scrape=None, truncate_reasoning=False, contacts_count=0):
    sd = c.source_data or {}

    # Build apollo_url if apollo_id present
    apollo_id = sd.get("apollo_id") or sd.get("organization_id") or sd.get("id")
    apollo_url = None
    if apollo_id:
        apollo_url = f"https://app.apollo.io/#/organizations/{apollo_id}"

    # Keywords: translate SIC/NAICS codes to human labels
    keywords = sd.get("keywords") or sd.get("tags") or sd.get("keyword_tags") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keywords:
        parts = []
        for code in (sd.get("sic_codes") or []):
            sc = str(code)
            label = _SIC.get(sc) or _SIC.get(sc[:4]) or _SIC.get(sc[:2])
            if label:
                parts.append(label)
        for code in (sd.get("naics_codes") or []):
            nc = str(code)
            label = _NAICS.get(nc) or _NAICS.get(nc[:5]) or _NAICS.get(nc[:3])
            if label:
                parts.append(label)
        # Deduplicate and remove empties
        seen = set()
        keywords = []
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                keywords.append(p)
        keywords = ", ".join(keywords) if keywords else ""

    reasoning = c.analysis_reasoning
    if truncate_reasoning and reasoning and len(reasoning) > 100:
        reasoning = reasoning[:100] + "..."

    result = {
        "id": c.id, "domain": c.domain,
        "name": c.name,  # normalized name (cleaned by GPT)
        "source_company_name": sd.get("source_company_name"),  # original from Apollo
        "industry": c.industry,
        "employee_count": c.employee_count,
        "employee_count_note": "Apollo contacts (not actual headcount)" if c.employee_count and c.employee_count < 50 else None,
        "employee_range": c.employee_range,
        "country": c.country, "city": c.city,
        "description": c.description, "linkedin_url": c.linkedin_url,
        # Apollo enrichment fields from source_data
        "revenue": sd.get("revenue") or sd.get("organization_revenue_printed"),
        "revenue_raw": sd.get("revenue_raw") or sd.get("organization_revenue"),
        "founded_year": sd.get("founded_year"),
        "phone": sd.get("phone") or sd.get("primary_phone"),
        "headcount_growth_6m": sd.get("headcount_6m_growth") or sd.get("organization_headcount_six_month_growth"),
        "headcount_growth_12m": sd.get("headcount_12m_growth") or sd.get("organization_headcount_twelve_month_growth"),
        "num_contacts_apollo": sd.get("num_contacts_in_apollo") or sd.get("num_contacts"),
        "website_url": c.website_url if hasattr(c, 'website_url') else sd.get("website_url"),
        # New fields for pipeline page
        "status": _compute_company_status(c, scrape),
        "keywords": keywords,
        "apollo_url": apollo_url,
        # Scrape info (from JOIN)
        "scrape_status": scrape.scrape_status if scrape else None,
        "scrape_text_size": scrape.text_size_bytes if scrape else None,
        "scrape_text_preview": (
            (scrape.clean_text[:150] + "...") if scrape and scrape.clean_text and len(scrape.clean_text) > 150
            else (scrape.clean_text if scrape and scrape.clean_text else None)
        ) or (
            (c.scraped_text[:150] + "...") if c.scraped_text and len(c.scraped_text) > 150
            else c.scraped_text
        ),
        # Analysis
        "analysis_reasoning": reasoning,
        # Pipeline state
        "is_blacklisted": c.is_blacklisted,
        "blacklist_reason": c.blacklist_reason,
        "is_pre_filtered": c.is_pre_filtered,
        "pre_filter_reason": c.pre_filter_reason,
        "is_target": c.is_target,
        "analysis_confidence": c.analysis_confidence,
        "analysis_segment": c.analysis_segment,
        "is_enriched": c.is_enriched,
        "enrichment_source": c.enrichment_source,
        # Contacts/people status
        "contacts_count": contacts_count,
        "contacts_status": "found" if contacts_count > 0 else ("gathering" if c.is_enriched else None),
        "source_data": sd,
    }
    return result


@router.get("/sequences/{seq_id}")
async def get_sequence(
    seq_id: int,
    session: AsyncSession = Depends(get_session),
):
    seq = await session.get(GeneratedSequence, seq_id)
    if not seq:
        raise HTTPException(404, "Sequence not found")
    project = await session.get(Project, seq.project_id)
    return {
        "id": seq.id,
        "campaign_name": seq.campaign_name,
        "status": seq.status,
        "steps": seq.sequence_steps,
        "step_count": seq.sequence_step_count,
        "rationale": seq.rationale,
        "project_name": project.name if project else None,
        "pushed_at": str(seq.pushed_at) if seq.pushed_at else None,
        "model_used": seq.model_used,
    }


@router.get("/runs")
async def list_runs(
    session: AsyncSession = Depends(get_session),
    user: MCPUser = Depends(get_optional_user),
):
    if not user:
        return []
    query = select(GatheringRun).order_by(GatheringRun.created_at.desc()).limit(20)
    from app.models.project import Project
    user_projects = await session.execute(select(Project.id).where(Project.user_id == user.id))
    pids = [pid for (pid,) in user_projects.all()]
    if pids:
        query = query.where(GatheringRun.project_id.in_(pids))
    else:
        return []
    result = await session.execute(query)
    runs = result.scalars().all()

    # Get project names for all runs
    project_ids = {r.project_id for r in runs}
    project_names = {}
    if project_ids:
        pn_result = await session.execute(
            select(Project.id, Project.name).where(Project.id.in_(project_ids))
        )
        project_names = {pid: pname for pid, pname in pn_result.all()}

    # Batch queries instead of N+1
    run_ids = [r.id for r in runs]
    targets_map = {}
    segments_map = {}
    people_map = {}
    raw_map = {}

    if run_ids:
        # 1. Raw company counts per run (batch)
        raw_result = await session.execute(
            select(CompanySourceLink.gathering_run_id, sa_func.count(CompanySourceLink.id))
            .where(CompanySourceLink.gathering_run_id.in_(run_ids))
            .group_by(CompanySourceLink.gathering_run_id)
        )
        raw_map = dict(raw_result.all())

        # 2. Target counts per run (batch)
        tc_result = await session.execute(
            select(CompanySourceLink.gathering_run_id, sa_func.count(DiscoveredCompany.id))
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(CompanySourceLink.gathering_run_id.in_(run_ids), DiscoveredCompany.is_target == True)
            .group_by(CompanySourceLink.gathering_run_id)
        )
        targets_map = dict(tc_result.all())

        # 3. Primary segment per run (most common among targets)
        seg_result = await session.execute(
            select(
                CompanySourceLink.gathering_run_id,
                DiscoveredCompany.analysis_segment,
                sa_func.count(DiscoveredCompany.id).label("cnt"),
            )
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(
                CompanySourceLink.gathering_run_id.in_(run_ids),
                DiscoveredCompany.is_target == True,
                DiscoveredCompany.analysis_segment.isnot(None),
            )
            .group_by(CompanySourceLink.gathering_run_id, DiscoveredCompany.analysis_segment)
            .order_by(sa_func.count(DiscoveredCompany.id).desc())
        )
        for rid, seg, cnt in seg_result.all():
            if rid not in segments_map:
                segments_map[rid] = seg  # Take only the most frequent

        # 4. People counts per run (batch)
        from app.models.pipeline import ExtractedContact
        pc_result = await session.execute(
            select(CompanySourceLink.gathering_run_id, sa_func.count(ExtractedContact.id))
            .join(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(CompanySourceLink.gathering_run_id.in_(run_ids))
            .group_by(CompanySourceLink.gathering_run_id)
        )
        people_map = dict(pc_result.all())

    return [
        {"id": r.id, "status": r.status, "phase": r.current_phase,
         "source_type": r.source_type,
         "raw_companies": raw_map.get(r.id, r.new_companies_count or 0),
         "targets": targets_map.get(r.id, 0),
         "people": people_map.get(r.id, 0),
         "segment": segments_map.get(r.id),
         "credits_used": r.credits_used or 0,
         "project_id": r.project_id,
         "project_name": project_names.get(r.project_id, ""),
         "created_at": str(r.created_at)}
        for r in runs
    ]


@router.get("/runs/{run_id}/prompts")
async def get_run_prompts(
    run_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Get analysis prompts used for this pipeline run."""
    # Check usage logs for analysis prompts
    result = await session.execute(
        select(MCPUsageLog).where(
            MCPUsageLog.tool_name == "analysis_prompt",
            MCPUsageLog.extra_data["run_id"].as_integer() == run_id,
        ).order_by(MCPUsageLog.created_at.desc())
    )
    logs = result.scalars().all()

    # Also check dispatcher tool calls for this run
    tool_calls = await session.execute(
        select(MCPUsageLog).where(
            MCPUsageLog.extra_data["args"].astext.contains(str(run_id)),
            MCPUsageLog.tool_name.in_(["tam_gather", "tam_analyze", "tam_blacklist_check", "tam_scrape"]),
        ).order_by(MCPUsageLog.created_at)
    )
    calls = tool_calls.scalars().all()

    prompts = []
    for log in logs:
        ed = log.extra_data or {}
        prompts.append({
            "id": log.id,
            "type": "analysis",
            "segment": ed.get("target_segment", ""),
            "prompt_text": ed.get("prompt_text", ""),
            "model": ed.get("model", "gpt-4o-mini"),
            "targets_found": ed.get("targets_found", 0),
            "total_analyzed": ed.get("total_analyzed", 0),
            "created_at": str(log.created_at),
        })

    for call in calls:
        ed = call.extra_data or {}
        prompts.append({
            "id": call.id,
            "type": "tool_call",
            "tool": call.tool_name,
            "args": ed.get("args", {}),
            "created_at": str(call.created_at),
        })

    return prompts


# ── Deep links helper ──

@router.get("/deep-links")
async def get_deep_links(
    project_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Generate CRM/Tasks deep links for a project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    base = "/tasks"
    crm_base = "/crm"
    name = project.name
    return {
        "all_replies": f"{base}?project={name}",
        "warm_replies": f"{base}?project={name}&category=interested",
        "meetings": f"{base}?project={name}&tab=meetings",
        "questions": f"{base}?project={name}&tab=questions",
        "followups": f"{base}?project={name}&tab=follow-ups",
        "crm_all": f"{crm_base}?project_id={project_id}",
        "crm_targets": f"{crm_base}?project_id={project_id}&is_target=true",
        "pipeline": f"/pipeline",
        "projects": f"/projects",
    }


# ── Gate approval + pipeline actions (auth required) ──


class GateApprovalRequest(BaseModel):
    notes: Optional[str] = None


@router.post("/gates/{gate_id}/approve")
async def approve_gate(
    gate_id: int,
    req: GateApprovalRequest = GateApprovalRequest(),
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Approve an approval gate (checkpoint)."""
    from datetime import datetime, timezone
    gate = await session.get(ApprovalGate, gate_id)
    if not gate:
        raise HTTPException(404, "Gate not found")
    if gate.status != "pending":
        raise HTTPException(400, f"Gate already {gate.status}")
    gate.status = "approved"
    gate.decided_at = datetime.now(timezone.utc)
    if req.notes:
        gate.scope = {**(gate.scope or {}), "approval_notes": req.notes}

    # Advance the run phase
    run = await session.get(GatheringRun, gate.gathering_run_id)
    if run:
        phase_map = {
            "awaiting_scope_ok": "pre_filter",
            "awaiting_targets_ok": "prepare_verification",
            "awaiting_verify_ok": "verified",
        }
        if run.current_phase in phase_map:
            run.current_phase = phase_map[run.current_phase]

    await session.commit()
    return {"gate_id": gate_id, "status": "approved", "run_phase": run.current_phase if run else None}


@router.post("/runs/{run_id}/generate-sequence")
async def generate_sequence(
    run_id: int,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Generate email sequence for a pipeline run."""
    from app.services.campaign_intelligence import CampaignIntelligenceService
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    svc = CampaignIntelligenceService()
    result = await svc.generate_sequence(session, run_id=run_id, project_id=run.project_id)
    await session.commit()
    return result


@router.post("/runs/{run_id}/create-campaign")
async def create_campaign(
    run_id: int,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create SmartLead campaign from pipeline run."""
    from app.services.campaign_intelligence import CampaignIntelligenceService
    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    svc = CampaignIntelligenceService()
    result = await svc.push_to_smartlead(session, run_id=run_id, project_id=run.project_id)
    await session.commit()
    return result


class SendTestEmailRequest(BaseModel):
    test_email: str = "test@example.com"
    sequence_number: int = 1


@router.post("/runs/{run_id}/send-test-email")
async def send_test_email(
    run_id: int,
    req: SendTestEmailRequest = SendTestEmailRequest(),
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Send a test email via SmartLead's native send-test-email API.

    Uses the proper API: POST /campaigns/{id}/send-test-email with
    leadId + sequenceNumber + selectedEmailAccountId + customEmailAddress.
    """
    from app.services.smartlead_service import SmartLeadService

    run = await session.get(GatheringRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Find the generated sequence that was pushed for this run
    seq_result = await session.execute(
        select(GeneratedSequence).where(
            GeneratedSequence.project_id == run.project_id,
            GeneratedSequence.pushed_campaign_id.isnot(None),
        ).order_by(GeneratedSequence.pushed_at.desc()).limit(1)
    )
    seq = seq_result.scalar_one_or_none()

    if not seq or not seq.pushed_campaign_id:
        raise HTTPException(400, "No SmartLead campaign found for this run. Create a campaign first.")

    campaign = await session.get(Campaign, seq.pushed_campaign_id)
    if not campaign or not campaign.external_id:
        raise HTTPException(400, "Campaign has no SmartLead ID")

    smartlead_campaign_id = int(campaign.external_id)
    sl = SmartLeadService()

    if not sl.is_configured():
        raise HTTPException(500, "SmartLead API key not configured")

    # Use native SmartLead send-test-email API (auto-resolves account + lead)
    result = await sl.send_test_email(
        campaign_id=smartlead_campaign_id,
        test_email=req.test_email,
        sequence_number=req.sequence_number,
    )

    return {
        "campaign_name": campaign.name,
        "smartlead_campaign_id": smartlead_campaign_id,
        **result,
    }


@router.get("/reply-analysis-status")
async def reply_analysis_status(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Return reply analysis results from background analysis cache."""
    from app.services.reply_analysis_service import get_cached_analysis
    if not user:
        return {}
    # Check all user's projects for cached analysis
    user_pids = await _get_user_project_ids(user, session)
    for pid in user_pids:
        cached = get_cached_analysis(pid)
        if cached and cached.get("summary"):
            return cached["summary"]
    return {}


# ── Campaigns ──

@router.get("/campaigns")
async def list_campaigns_full(
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """List campaigns with sequence data for Campaigns page."""
    if not user:
        return []
    user_pids = await _get_user_project_ids(user, session)
    if not user_pids:
        return []

    query = select(Campaign).where(Campaign.project_id.in_(user_pids)).order_by(Campaign.id.desc())
    if project_id:
        query = query.where(Campaign.project_id == project_id)
    if status:
        query = query.where(Campaign.status == status)

    result = await session.execute(query)
    campaigns = result.scalars().all()

    # Get project names + sequence data
    project_names = {}
    if user_pids:
        pn = await session.execute(select(Project.id, Project.name).where(Project.id.in_(user_pids)))
        project_names = {pid: pname for pid, pname in pn.all()}

    out = []
    for c in campaigns:
        # Get sequence
        seq_result = await session.execute(
            select(GeneratedSequence).where(
                GeneratedSequence.pushed_campaign_id == c.id
            ).order_by(GeneratedSequence.created_at.desc()).limit(1)
        )
        seq = seq_result.scalar_one_or_none()

        # Get run that produced this campaign
        run_id = None
        if seq:
            run_result = await session.execute(
                select(GatheringRun.id).where(
                    GatheringRun.project_id == c.project_id
                ).order_by(GatheringRun.created_at.desc()).limit(1)
            )
            run_row = run_result.first()
            if run_row:
                run_id = run_row[0]

        out.append({
            "id": c.id,
            "name": c.name,
            "project_id": c.project_id,
            "project_name": project_names.get(c.project_id, ""),
            "platform": c.platform or "smartlead",
            "status": c.status,
            "external_id": c.external_id,
            "smartlead_url": f"https://app.smartlead.ai/app/email-campaigns-v2/{c.external_id}/analytics" if c.external_id else None,
            "leads_count": c.leads_count or 0,
            "created_by": getattr(c, 'created_by', None) or "user",
            "monitoring_enabled": getattr(c, 'monitoring_enabled', False) or False,
            "sequence_steps": seq.sequence_steps if seq else [],
            "sequence_id": seq.id if seq else None,
            "pipeline_run_id": run_id,
            "timezone": "America/New_York",
            "email_accounts": getattr(c, 'email_account_ids', None) or [],
            "created_at": str(c.created_at) if hasattr(c, 'created_at') and c.created_at else None,
        })

    return out


@router.get("/campaigns/{campaign_id}")
async def get_campaign_detail(
    campaign_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Campaign detail — sequence + email accounts."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Sequence
    seq_result = await session.execute(
        select(GeneratedSequence).where(
            GeneratedSequence.pushed_campaign_id == campaign.id
        ).order_by(GeneratedSequence.created_at.desc()).limit(1)
    )
    seq = seq_result.scalar_one_or_none()
    # Fallback: check sequence_id FK
    if not seq and campaign.sequence_id:
        seq = await session.get(GeneratedSequence, campaign.sequence_id)

    # Pipeline run
    run_result = await session.execute(
        select(GatheringRun.id).where(GatheringRun.campaign_id == campaign.id).limit(1)
    )
    run_row = run_result.first()
    run_id = run_row[0] if run_row else None

    # Project name
    project_name = ""
    if campaign.project_id:
        p = await session.get(Project, campaign.project_id)
        if p:
            project_name = p.name

    return {
        "id": campaign.id,
        "name": campaign.name,
        "project_id": campaign.project_id,
        "project_name": project_name,
        "platform": campaign.platform or "smartlead",
        "status": campaign.status,
        "external_id": campaign.external_id,
        "smartlead_url": f"https://app.smartlead.ai/app/email-campaigns-v2/{campaign.external_id}/analytics" if campaign.external_id else None,
        "leads_count": campaign.leads_count or 0,
        "created_by": getattr(campaign, 'created_by', None) or "user",
        "monitoring_enabled": getattr(campaign, 'monitoring_enabled', False) or False,
        "sequence_steps": seq.sequence_steps if seq else [],
        "sequence_id": seq.id if seq else None,
        "pipeline_run_id": run_id,
        "email_accounts": getattr(campaign, 'email_account_ids', None) or [],
        "timezone": "America/New_York",
        "created_at": str(campaign.created_at) if campaign.created_at else None,
    }


@router.post("/campaigns/{campaign_id}/activate")
async def activate_campaign_rest(
    campaign_id: int,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Activate a DRAFT campaign — starts sending emails."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    project = await session.get(Project, campaign.project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(404, "Campaign not found")
    if not campaign.external_id:
        raise HTTPException(400, "Campaign has no SmartLead ID")

    from app.services.smartlead_service import SmartLeadService
    sl = SmartLeadService()
    await sl.update_campaign_status(int(campaign.external_id), "START")
    campaign.status = "active"
    campaign.monitoring_enabled = True

    # Audit log
    session.add(MCPUsageLog(
        user_id=user.id,
        tool_name="activate_campaign",
        action="campaign_activated_via_ui",
        extra_data={"campaign_id": campaign.id, "external_id": campaign.external_id, "monitoring_enabled": True},
    ))
    await session.commit()

    return {"activated": True, "status": "active", "monitoring_enabled": True}


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign_rest(
    campaign_id: int,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Pause an active campaign."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    project = await session.get(Project, campaign.project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(404)

    from app.services.smartlead_service import SmartLeadService
    sl = SmartLeadService()
    await sl.update_campaign_status(int(campaign.external_id), "PAUSED")
    campaign.status = "paused"
    await session.commit()
    return {"paused": True}


@router.post("/campaigns/{campaign_id}/monitoring")
async def toggle_monitoring(
    campaign_id: int,
    body: dict,
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Toggle reply monitoring for a campaign."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    project = await session.get(Project, campaign.project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(404)
    campaign.monitoring_enabled = bool(body.get("enabled", False))
    await session.commit()
    return {"monitoring_enabled": campaign.monitoring_enabled}


# ── CRM: all companies across all pipelines ──

@router.get("/crm/companies")
async def crm_companies(
    project_id: int = None,
    is_target: bool = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    query = select(DiscoveredCompany).order_by(DiscoveredCompany.domain)
    # User-scope: only show companies from user's projects
    if user:
        user_pids = await _get_user_project_ids(user, session)
        query = query.where(DiscoveredCompany.project_id.in_(user_pids))
    if project_id:
        query = query.where(DiscoveredCompany.project_id == project_id)
    if is_target is not None:
        query = query.where(DiscoveredCompany.is_target == is_target)
    result = await session.execute(query.limit(500))
    companies = result.scalars().all()
    return [_company_to_dict(c) for c in companies]


@router.get("/crm/contacts")
async def crm_contacts(
    project_id: int = None,
    pipeline: int = None,
    search: str = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """CRM contacts view — people extracted from pipeline. Filter by pipeline run ID."""
    from app.models.pipeline import ExtractedContact

    # JOIN with DiscoveredCompany to get company info + pipeline run link
    stmt = (
        select(ExtractedContact, DiscoveredCompany)
        .outerjoin(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
        .order_by(ExtractedContact.created_at.desc())
    )

    # User-scope
    if user:
        user_pids = await _get_user_project_ids(user, session)
        stmt = stmt.where(ExtractedContact.project_id.in_(user_pids))

    if project_id:
        stmt = stmt.where(ExtractedContact.project_id == project_id)

    if pipeline:
        # Filter to contacts whose company was gathered in this pipeline run
        stmt = stmt.where(
            DiscoveredCompany.id.in_(
                select(CompanySourceLink.discovered_company_id)
                .where(CompanySourceLink.gathering_run_id == pipeline)
            )
        )

    if search:
        stmt = stmt.where(
            (ExtractedContact.email.ilike(f"%{search}%")) |
            (ExtractedContact.first_name.ilike(f"%{search}%")) |
            (ExtractedContact.last_name.ilike(f"%{search}%"))
        )

    result = await session.execute(stmt.limit(500))
    rows = result.all()

    contacts = []
    for contact, company in rows:
        # Find pipeline run IDs for this company
        run_ids = []
        if company:
            links = await session.execute(
                select(CompanySourceLink.gathering_run_id)
                .where(CompanySourceLink.discovered_company_id == company.id)
            )
            run_ids = [r[0] for r in links.all()]

        contacts.append({
            "id": contact.id,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "email": contact.email,
            "job_title": contact.job_title,
            "linkedin_url": contact.linkedin_url,
            "phone": contact.phone,
            "email_verified": contact.email_verified,
            "email_source": contact.email_source,
            "domain": company.domain if company else None,
            "company_name": company.name if company else None,
            "industry": company.industry if company else None,
            "country": company.country if company else None,
            "pipeline_run_ids": run_ids,
            "created_at": str(contact.created_at) if contact.created_at else None,
        })

    return {"contacts": contacts, "total": len(contacts)}


@router.delete("/cleanup-test-data")
async def cleanup_test_data(
    session: AsyncSession = Depends(get_session),
    user: MCPUser = Depends(get_current_user),
):
    """Soft-delete ALL projects for this user — sets is_active=False.
    Data stays in DB for recovery. Used between test cycles to start fresh."""
    from sqlalchemy import update

    # Soft-delete: mark all projects as inactive (data preserved, recoverable)
    result = await session.execute(
        update(Project)
        .where(Project.user_id == user.id, Project.is_active == True)
        .values(is_active=False)
    )
    count = result.rowcount
    await session.commit()

    return {"disabled": count, "message": f"Disabled {count} projects (soft-delete — data preserved, recoverable)"}


# ── Learning / Corrections (for @main OperatorActionsPage) ──
# NOTE: These use a SEPARATE router without /pipeline prefix because
# @main components call /api/projects/{id}/learning/... not /api/pipeline/projects/...

from fastapi import APIRouter as _LearningRouter
learning_router = _LearningRouter(prefix="/projects", tags=["learning"])

@learning_router.get("/{project_id}/learning/corrections")
async def get_corrections(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    channel: Optional[str] = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Operator corrections — what AI drafted vs what was sent. Built from mcp_replies."""
    from app.models.reply import MCPReply

    query = select(MCPReply).where(MCPReply.project_id == project_id)

    if action_type:
        if action_type == "send":
            query = query.where(MCPReply.approval_status == "approved")
        elif action_type == "dismiss":
            query = query.where(MCPReply.approval_status == "dismissed")
    if category:
        query = query.where(MCPReply.category == category)
    if channel:
        query = query.where(MCPReply.channel == channel)

    # Count
    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await session.execute(count_q)).scalar() or 0

    # Paginate
    query = query.order_by(MCPReply.received_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    replies = result.scalars().all()

    items = []
    for r in replies:
        action = "send" if r.approval_status == "approved" else "dismiss" if r.approval_status == "dismissed" else "pending"
        items.append({
            "id": r.id,
            "action_type": action,
            "was_edited": False,
            "reply_category": r.category,
            "channel": r.channel or "email",
            "lead_company": r.lead_company,
            "lead_email": r.lead_email,
            "campaign_name": r.campaign_name,
            "ai_draft_preview": (r.draft_reply or "")[:200],
            "ai_draft_full": r.draft_reply,
            "sent_preview": (r.draft_reply or "")[:200],
            "sent_full": r.draft_reply,
            "ai_draft_subject": r.draft_subject,
            "sent_subject": r.draft_subject,
            "inbox_link": None,
            "actor": "operator",
            "related_log_id": None,
            "created_at": str(r.received_at) if r.received_at else None,
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@learning_router.get("/{project_id}/learning/overview")
async def get_learning_overview(
    project_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Learning overview — stats for the project."""
    from app.models.reply import MCPReply

    total = (await session.execute(
        select(sa_func.count(MCPReply.id)).where(MCPReply.project_id == project_id)
    )).scalar() or 0

    approved = (await session.execute(
        select(sa_func.count(MCPReply.id)).where(MCPReply.project_id == project_id, MCPReply.approval_status == "approved")
    )).scalar() or 0

    dismissed = (await session.execute(
        select(sa_func.count(MCPReply.id)).where(MCPReply.project_id == project_id, MCPReply.approval_status == "dismissed")
    )).scalar() or 0

    with_draft = (await session.execute(
        select(sa_func.count(MCPReply.id)).where(MCPReply.project_id == project_id, MCPReply.draft_reply.isnot(None))
    )).scalar() or 0

    return {
        "total_replies": total,
        "approved": approved,
        "dismissed": dismissed,
        "pending": total - approved - dismissed,
        "with_drafts": with_draft,
        "approve_rate": f"{approved/total*100:.0f}%" if total > 0 else "N/A",
        "golden_examples": 0,
        "reference_examples": 0,
        "learning_cycles": 0,
    }


@learning_router.get("/{project_id}/learning/examples")
async def get_learning_examples(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Reference examples for this project — stub for now."""
    return {"items": [], "total": 0, "page": page, "page_size": page_size}
