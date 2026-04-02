"""Replies API — MCP's own reply data from MCPReply table.

Fully independent from main backend. No proxy needed.
Compatible with main app's TasksPage component (@main alias).
"""
from fastapi import APIRouter, Query, Depends
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.db import get_session
from app.models.reply import MCPReply
from app.models.project import Project
from app.models.user import MCPUser
from app.auth.dependencies import get_optional_user

router = APIRouter(prefix="/replies", tags=["replies"])


async def _get_user_project_ids(user, session) -> list[int]:
    if not user:
        return []
    result = await session.execute(select(Project.id).where(Project.user_id == user.id))
    return [pid for (pid,) in result.all()]


@router.get("")
@router.get("/")
async def list_replies(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    needs_reply: Optional[bool] = None,
    needs_followup: Optional[bool] = None,
    category: Optional[str] = None,
    project_id: Optional[int] = None,
    campaign_name: Optional[str] = None,
    campaign_name_contains: Optional[str] = None,
    lead_email: Optional[str] = None,
    search: Optional[str] = None,
    received_since: Optional[str] = None,
    group_by_contact: Optional[bool] = None,
    include_all: Optional[bool] = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """List replies from MCP's own database."""
    pids = await _get_user_project_ids(user, session)
    if not pids:
        return {"replies": [], "total": 0, "page": page, "page_size": page_size}

    query = select(MCPReply).where(MCPReply.project_id.in_(pids))

    if project_id:
        query = query.where(MCPReply.project_id == project_id)
    if category:
        query = query.where(MCPReply.category == category)
    if needs_reply is not None:
        query = query.where(MCPReply.needs_reply == needs_reply)
    if needs_followup:
        query = query.where(MCPReply.needs_reply == True, MCPReply.category.in_(["interested", "meeting_request", "question"]))
    if campaign_name:
        query = query.where(MCPReply.campaign_name == campaign_name)
    if campaign_name_contains:
        query = query.where(MCPReply.campaign_name.ilike(f"%{campaign_name_contains}%"))
    if lead_email:
        query = query.where(MCPReply.lead_email == lead_email)
    if search:
        from sqlalchemy import or_
        query = query.where(or_(
            MCPReply.lead_email.ilike(f"%{search}%"),
            MCPReply.lead_name.ilike(f"%{search}%"),
            MCPReply.lead_company.ilike(f"%{search}%"),
        ))

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_q)).scalar() or 0

    # Fetch
    query = query.order_by(desc(MCPReply.received_at)).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    replies = result.scalars().all()

    return {
        "replies": [
            {
                "id": r.id,
                "lead_email": r.lead_email,
                "lead_name": r.lead_name,
                "lead_company": r.lead_company,
                "company_name": r.lead_company,
                "campaign_name": r.campaign_name,
                "category": r.category,
                "category_confidence": r.category_confidence,
                "email_subject": r.email_subject,
                "email_body": r.reply_text,
                "reply_text": r.reply_text,
                "received_at": str(r.received_at) if r.received_at else None,
                "needs_reply": r.needs_reply,
                "approval_status": r.approval_status,
                "channel": r.channel,
                "source": r.source,
                "draft_reply": r.draft_reply,
            }
            for r in replies
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/counts")
async def reply_counts(
    project_id: Optional[int] = None,
    include_all: Optional[bool] = None,
    received_since: Optional[str] = None,
    campaign_name_contains: Optional[str] = None,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Reply counts by category from MCP's own data."""
    pids = await _get_user_project_ids(user, session)
    if not pids:
        return {"total": 0, "category_counts": {}}

    base = select(MCPReply.category, func.count(MCPReply.id)).where(MCPReply.project_id.in_(pids))
    if project_id:
        base = base.where(MCPReply.project_id == project_id)
    if campaign_name_contains:
        base = base.where(MCPReply.campaign_name.ilike(f"%{campaign_name_contains}%"))

    base = base.group_by(MCPReply.category)
    result = await session.execute(base)
    cats = {row[0]: row[1] for row in result.all() if row[0]}

    total_q = select(func.count(MCPReply.id)).where(MCPReply.project_id.in_(pids))
    if campaign_name_contains:
        total_q = total_q.where(MCPReply.campaign_name.ilike(f"%{campaign_name_contains}%"))
    total = (await session.execute(total_q)).scalar() or 0

    return {"total": total, "category_counts": cats}


@router.get("/{reply_id}")
async def get_reply(
    reply_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Single reply detail."""
    from fastapi import HTTPException
    reply = await session.get(MCPReply, reply_id)
    if not reply:
        raise HTTPException(404)
    # User-scope
    if user:
        pids = await _get_user_project_ids(user, session)
        if reply.project_id not in pids:
            raise HTTPException(404)
    return {
        "id": reply.id,
        "lead_email": reply.lead_email,
        "lead_name": reply.lead_name,
        "category": reply.category,
        "reply_text": reply.reply_text,
        "draft_reply": reply.draft_reply,
        "campaign_name": reply.campaign_name,
        "received_at": str(reply.received_at) if reply.received_at else None,
    }


@router.get("/{reply_id}/conversation")
async def get_conversation(
    reply_id: int,
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Conversation history for a reply — all messages for the same lead email."""
    from fastapi import HTTPException
    reply = await session.get(MCPReply, reply_id)
    if not reply:
        raise HTTPException(404)
    if user:
        pids = await _get_user_project_ids(user, session)
        if reply.project_id not in pids:
            raise HTTPException(404)

    # Get all replies for this lead (conversation thread)
    all_replies = await session.execute(
        select(MCPReply)
        .where(MCPReply.lead_email == reply.lead_email, MCPReply.project_id == reply.project_id)
        .order_by(MCPReply.received_at)
    )
    thread = all_replies.scalars().all()

    messages = []
    for r in thread:
        # Inbound message (lead's reply)
        if r.reply_text:
            messages.append({
                "direction": "inbound",
                "body": r.reply_text,
                "subject": r.email_subject,
                "timestamp": str(r.received_at) if r.received_at else None,
                "channel": r.channel or "email",
            })
        # Outbound message (our draft/sent)
        if r.draft_reply and r.approval_status == "approved":
            messages.append({
                "direction": "outbound",
                "body": r.draft_reply,
                "subject": r.draft_subject,
                "timestamp": str(r.approved_at) if hasattr(r, 'approved_at') and r.approved_at else str(r.received_at) if r.received_at else None,
                "channel": r.channel or "email",
            })

    return {"messages": messages, "lead_email": reply.lead_email, "total": len(messages)}


@router.post("/contact-info-batch")
async def contact_info_batch(
    body: dict = {},
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Batch contact info lookup — returns company/title for lead emails."""
    emails = body.get("emails", [])
    if not emails:
        return {"contacts": {}}

    from app.models.pipeline import ExtractedContact
    result = await session.execute(
        select(ExtractedContact).where(ExtractedContact.email.in_(emails[:100]))
    )
    contacts = {}
    for c in result.scalars().all():
        contacts[c.email] = {
            "company_name": c.source_data.get("company_name") if c.source_data else None,
            "job_title": c.job_title,
            "first_name": c.first_name,
            "last_name": c.last_name,
        }
    return {"contacts": contacts}


@router.post("/sync")
async def trigger_sync(
    user: MCPUser = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """Trigger reply sync for all tracked campaigns."""
    if not user:
        from fastapi import HTTPException
        raise HTTPException(401)
    from app.services.reply_service import sync_all_tracked_replies
    result = await sync_all_tracked_replies(user.id)
    return result


@router.post("/webhook/smartlead")
async def smartlead_webhook(body: dict):
    """SmartLead webhook — receives reply notifications in real-time.

    SmartLead sends: {event_type, lead, campaign_id, ...}
    We process and store in MCPReply table.
    """
    import logging
    logger = logging.getLogger(__name__)

    event_type = body.get("event_type", "")
    lead = body.get("lead", {})
    campaign_id = body.get("campaign_id")

    if event_type not in ("REPLY", "INTERESTED", "MEETING_BOOKED"):
        return {"status": "ignored", "event_type": event_type}

    email = lead.get("email", "")
    if not email:
        return {"status": "ignored", "reason": "no email"}

    # Use own session (don't block on shared pool)
    from app.db import async_session_maker
    from app.models.campaign import Campaign

    try:
        async with async_session_maker() as session:
            campaign = None
            if campaign_id:
                campaign = (await session.execute(
                    select(Campaign).where(Campaign.external_id == str(campaign_id))
                )).scalar_one_or_none()

            if not campaign or not campaign.project_id:
                return {"status": "ignored", "reason": "campaign not found in MCP"}

            # Verify project is active
            from app.models.project import Project
            project = await session.get(Project, campaign.project_id)
            if not project or not project.is_active:
                return {"status": "ignored", "reason": "project inactive or deleted"}

            existing = (await session.execute(
                select(MCPReply).where(MCPReply.lead_email == email)
            )).scalar_one_or_none()
            if existing:
                return {"status": "duplicate"}

            reply_text = body.get("reply_text", body.get("message", ""))[:2000]
            category = "interested" if event_type == "INTERESTED" else "meeting_request" if event_type == "MEETING_BOOKED" else "other"

            reply = MCPReply(
                project_id=campaign.project_id,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                lead_email=email,
                lead_name=f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                reply_text=reply_text,
                category=category,
                source="smartlead",
                smartlead_lead_id=str(lead.get("id", "")),
            )
            session.add(reply)
            await session.commit()

            logger.info(f"Webhook: new {category} reply from {email}")
            return {"status": "processed", "category": category}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "detail": str(e)[:200]}
