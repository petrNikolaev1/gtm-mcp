"""Contacts API compatibility layer — serves same shape as main app's /api/contacts
but reads from MCP's ExtractedContact + DiscoveredCompany tables.

This allows the main app's CRM UI (AG Grid) to work with MCP data unchanged."""
from math import ceil
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func as sa_func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.auth.dependencies import get_optional_user
from app.models.user import MCPUser
from app.models.pipeline import DiscoveredCompany, ExtractedContact
from app.models.gathering import CompanySourceLink
from app.models.project import Project

router = APIRouter(prefix="/contacts", tags=["contacts-compat"])


async def _get_user_project_ids(user, session):
    result = await session.execute(select(Project.id).where(Project.user_id == user.id))
    return [r[0] for r in result.all()]


@router.get("")
async def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    search: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    pipeline_run_id: Optional[int] = Query(None),
    segment: Optional[str] = Query(None),
    geo: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    # Accept but ignore main app params that don't apply to MCP
    status: Optional[str] = Query(None),
    campaign: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
    has_replied: Optional[bool] = Query(None),
    needs_followup: Optional[bool] = Query(None),
    created_after: Optional[str] = Query(None),
    created_before: Optional[str] = Query(None),
    suitable_for: Optional[str] = Query(None),
    reply_category: Optional[str] = Query(None),
    reply_since: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    status_external: Optional[str] = Query(None),
    is_qualified: Optional[bool] = Query(None),
    has_smartlead: Optional[bool] = Query(None),
    has_getsales: Optional[bool] = Query(None),
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """List contacts — compatible with main app CRM UI."""
    if not user:
        return {"contacts": [], "total": 0, "page": 1, "page_size": page_size, "total_pages": 0}

    user_pids = await _get_user_project_ids(user, session)
    if not user_pids:
        return {"contacts": [], "total": 0, "page": 1, "page_size": page_size, "total_pages": 0}

    # Base query
    stmt = (
        select(ExtractedContact, DiscoveredCompany)
        .outerjoin(DiscoveredCompany, DiscoveredCompany.id == ExtractedContact.discovered_company_id)
        .where(ExtractedContact.project_id.in_(user_pids))
    )

    if project_id:
        stmt = stmt.where(ExtractedContact.project_id == project_id)

    if pipeline_run_id:
        stmt = stmt.where(
            DiscoveredCompany.id.in_(
                select(CompanySourceLink.discovered_company_id)
                .where(CompanySourceLink.gathering_run_id == pipeline_run_id)
            )
        )

    if search:
        stmt = stmt.where(
            or_(
                ExtractedContact.email.ilike(f"%{search}%"),
                ExtractedContact.first_name.ilike(f"%{search}%"),
                ExtractedContact.last_name.ilike(f"%{search}%"),
                DiscoveredCompany.name.ilike(f"%{search}%"),
            )
        )

    if segment:
        segs = [s.strip() for s in segment.split(",")]
        stmt = stmt.where(DiscoveredCompany.analysis_segment.in_(segs))

    if geo:
        stmt = stmt.where(DiscoveredCompany.country.ilike(f"%{geo}%"))

    if domain:
        domains = [d.strip() for d in domain.split(",")]
        stmt = stmt.where(DiscoveredCompany.domain.in_(domains))

    # Count total
    count_stmt = select(sa_func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    # Sort
    sort_map = {
        "email": ExtractedContact.email,
        "first_name": ExtractedContact.first_name,
        "company_name": DiscoveredCompany.name,
        "job_title": ExtractedContact.job_title,
        "created_at": ExtractedContact.created_at,
        "domain": DiscoveredCompany.domain,
    }
    sort_field = sort_map.get(sort_by, ExtractedContact.created_at)
    stmt = stmt.order_by(sort_field.desc() if sort_order == "desc" else sort_field.asc())

    # Paginate
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    rows = result.all()

    # Project names
    project_names = {}
    if user_pids:
        pn = await session.execute(select(Project.id, Project.name).where(Project.id.in_(user_pids)))
        project_names = {r[0]: r[1] for r in pn.all()}

    contacts = []
    for contact, company in rows:
        contacts.append({
            "id": contact.id,
            "email": contact.email or "",
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "company_name": company.name if company else "",
            "domain": company.domain if company else "",
            "job_title": contact.job_title or "",
            "segment": company.analysis_segment if company else "",
            "geo": company.country if company else "",
            "source": contact.email_source or "apollo",
            "source_id": "",
            "status": "new",
            "status_external": "",
            "phone": contact.phone or "",
            "linkedin_url": contact.linkedin_url or "",
            "location": f"{company.city}, {company.country}" if company and company.city else (company.country if company else ""),
            "project_id": contact.project_id,
            "project_name": project_names.get(contact.project_id, ""),
            "created_at": str(contact.created_at) if contact.created_at else None,
            "has_replied": False,
            "needs_followup": False,
            "campaigns": [],
            "suitable_for": [],
        })

    return {
        "contacts": contacts,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(total / page_size) if page_size > 0 else 0,
    }


@router.get("/stats")
async def contact_stats(
    project_id: Optional[int] = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return {"total": 0}
    user_pids = await _get_user_project_ids(user, session)
    stmt = select(sa_func.count(ExtractedContact.id)).where(ExtractedContact.project_id.in_(user_pids))
    if project_id:
        stmt = stmt.where(ExtractedContact.project_id == project_id)
    total = (await session.execute(stmt)).scalar() or 0
    return {"total": total, "new": total, "contacted": 0, "replied": 0, "meeting": 0, "won": 0, "lost": 0}


@router.get("/filters")
async def filter_options(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return {"segments": [], "geos": [], "sources": [], "campaigns": []}
    user_pids = await _get_user_project_ids(user, session)
    # Segments
    seg_result = await session.execute(
        select(DiscoveredCompany.analysis_segment).where(
            DiscoveredCompany.project_id.in_(user_pids),
            DiscoveredCompany.analysis_segment.isnot(None),
        ).distinct()
    )
    segments = [r[0] for r in seg_result.all() if r[0]]
    # Geos
    geo_result = await session.execute(
        select(DiscoveredCompany.country).where(
            DiscoveredCompany.project_id.in_(user_pids),
            DiscoveredCompany.country.isnot(None),
        ).distinct()
    )
    geos = [r[0] for r in geo_result.all() if r[0]]
    return {"segments": segments, "geos": geos, "sources": ["apollo"], "campaigns": []}


@router.get("/campaigns")
async def list_campaigns(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    return []


@router.get("/projects/list")
async def list_projects(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return []
    result = await session.execute(select(Project).where(Project.user_id == user.id))
    return [{"id": p.id, "name": p.name} for p in result.scalars().all()]


@router.get("/projects/names")
async def list_project_names(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return []
    result = await session.execute(select(Project.id, Project.name).where(Project.user_id == user.id))
    return [{"id": r[0], "name": r[1]} for r in result.all()]
