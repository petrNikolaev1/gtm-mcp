"""Background reply analysis — GOD-tier 3-layer funnel.

Architecture:
  Tier 0 (FREE): SmartLead status=REPLIED → get all replied leads
  Tier 1 (FREE): Keyword pre-filter → OOO/unsubscribe/bounce → skip AI
  Tier 2 (FREE): Fetch message history only for real conversations
  Tier 3 (CHEAP): GPT-4o-mini classification → only real conversations

Runs IN PARALLEL with blacklist import when user connects campaigns.
Results stored in ExtractedContact.source_data + in-memory cache for tool queries.
"""
import asyncio
import logging
import time
import re
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.db import async_session_maker
from app.models.pipeline import ExtractedContact

logger = logging.getLogger(__name__)

# ── Tier 1: keyword pre-filters (FREE, no AI) ──

OOO_PATTERNS = re.compile(
    r"out of (?:the )?office|ooo|on vacation|on holiday|currently away|"
    r"i(?:'m| am) (?:out|away|off)|auto[- ]?reply|automatic reply|"
    r"will (?:be )?(?:back|return)|maternity|paternity|sick leave|"
    r"limited access|(?:i'?ll|will) respond (?:when|after)|"
    r"thank you for your (?:email|message|patience)",
    re.IGNORECASE,
)

UNSUB_PATTERNS = re.compile(
    r"unsubscribe|opt[- ]?out|remove (?:me|my)|stop (?:emailing|sending|contacting)|"
    r"don'?t (?:email|contact|message)|take me off|no longer wish",
    re.IGNORECASE,
)

BOUNCE_PATTERNS = re.compile(
    r"delivery (?:failed|failure)|undeliverable|mailbox (?:full|unavailable)|"
    r"message not delivered|recipient rejected|address rejected|"
    r"user unknown|no such user",
    re.IGNORECASE,
)

# Categories that need AI classification (real conversations)
AI_CATEGORIES = {"interested", "meeting_request", "not_interested", "question", "wrong_person", "other"}

# GPT-4o-mini classification prompt — same logic as main app
CLASSIFY_SYSTEM_PROMPT = """You classify email replies into exactly one category.

Categories:
- interested: ANY positive signal (wants more info, says yes, shares contact, asks pricing)
- meeting_request: Wants to schedule call/meeting, shares availability, mentions calendar
- not_interested: EXPLICIT decline (polite or direct refusal)
- question: Has specific questions before deciding
- wrong_person: Not the right contact, suggests someone else, left the company
- other: Doesn't fit above categories

Rules:
- Short affirmative replies ("sure", "sounds good", "yes") = interested
- If person shares contact info on different channel = interested
- "нужно" (need) without negation = interested
- Only "не нужно" / "don't need" = not_interested
- When in doubt between interested and question → interested

Return ONLY valid JSON: {"category": "...", "confidence": 0.85, "reasoning": "..."}"""


def _tier1_prefilter(text: str) -> Optional[Tuple[str, float]]:
    """Tier 1: Free keyword pre-filter. Returns (category, confidence) or None for AI."""
    if not text or len(text.strip()) < 5:
        return ("other", 0.3)

    # OOO — very reliable pattern
    if OOO_PATTERNS.search(text):
        return ("out_of_office", 0.95)

    # Unsubscribe
    if UNSUB_PATTERNS.search(text):
        return ("unsubscribe", 0.90)

    # Bounce (shouldn't reach here but safety net)
    if BOUNCE_PATTERNS.search(text):
        return None  # skip entirely, not a real reply

    return None  # needs AI classification


async def _tier3_classify_batch(
    replies: List[Dict[str, Any]],
    openai_key: str,
    concurrency: int = 10,
) -> List[Dict[str, Any]]:
    """Tier 3: GPT-4o-mini batch classification. Only for real conversations."""
    import httpx
    import json as _json

    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def classify_one(reply: Dict) -> Dict:
        text = reply.get("reply_text", "")
        subject = reply.get("subject", "")
        email = reply.get("email", "")

        user_prompt = f"Subject: {subject}\n\nReply:\n{text[:2000]}"

        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": 100,
                            "temperature": 0,
                        },
                    )
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    clean = content.strip()
                    if clean.startswith("```"):
                        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
                    parsed = _json.loads(clean)
                    return {
                        **reply,
                        "category": parsed.get("category", "other"),
                        "confidence": parsed.get("confidence", 0.5),
                        "reasoning": parsed.get("reasoning", ""),
                    }
            except Exception as e:
                logger.debug(f"Classification failed for {email}: {e}")
                return {**reply, "category": "other", "confidence": 0.3, "reasoning": f"Classification error: {str(e)[:80]}"}

    tasks = [classify_one(r) for r in replies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    classified = []
    for r in results:
        if isinstance(r, dict):
            classified.append(r)
        elif isinstance(r, Exception):
            logger.warning(f"Classification exception: {r}")
    return classified


def _strip_html(html: str) -> str:
    """Strip HTML tags and entities from email body."""
    if not html:
        return ""
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:div|p|li|tr)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    try:
        from html import unescape
        text = unescape(text)
    except Exception:
        pass
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_latest_reply(messages: List[Dict]) -> Optional[str]:
    """Extract the latest inbound reply from a message history thread."""
    # Messages are usually chronological; find latest REPLY type
    for msg in reversed(messages):
        msg_type = (msg.get("type") or "").upper()
        if msg_type == "REPLY":
            body = msg.get("email_body") or msg.get("body") or ""
            return _strip_html(body)
    return None


# ── In-memory reply cache for tool queries ──
# project_id -> {"analyzed_at": datetime, "replies": [...], "summary": {...}}
_reply_cache: Dict[int, Dict] = {}


def get_cached_analysis(project_id: int) -> Optional[Dict]:
    """Get cached reply analysis for a project. Used by reply tools."""
    return _reply_cache.get(project_id)


async def analyze_campaign_replies(
    smartlead_service,
    campaign_ids: List[int],
    campaign_names: Dict[int, str],
    project_id: int,
    openai_key: Optional[str] = None,
):
    """GOD-tier background reply analysis with 3-layer funnel.

    Tier 0: SmartLead status=REPLIED (FREE)
    Tier 1: Keyword pre-filter for OOO/unsub (FREE)
    Tier 2: Fetch thread only for real conversations (FREE)
    Tier 3: GPT-4o-mini for ambiguous replies (CHEAP)
    """
    t_start = time.time()
    logger.info(f"[ReplyAnalysis] Starting for project {project_id}, {len(campaign_ids)} campaigns")

    stats = {
        "total_replied": 0,
        "tier1_ooo": 0,
        "tier1_unsub": 0,
        "tier1_bounce": 0,
        "tier2_thread_fetched": 0,
        "tier2_thread_failed": 0,
        "tier3_ai_classified": 0,
        "by_category": {},
    }
    all_analyzed: List[Dict] = []

    # Get OpenAI key from settings if not provided
    if not openai_key:
        try:
            from app.config import settings
            openai_key = getattr(settings, "OPENAI_API_KEY", None)
        except Exception:
            pass

    async with async_session_maker() as session:
        try:
            for camp_id in campaign_ids:
                camp_name = campaign_names.get(camp_id, f"Campaign #{camp_id}")
                logger.info(f"[ReplyAnalysis] Campaign '{camp_name}' (id={camp_id})")

                # ── Tier 0: Get all replied leads (FREE) ──
                replied_leads = await smartlead_service.get_campaign_leads_with_status(
                    camp_id, status="REPLIED", limit=500
                )
                if not replied_leads:
                    logger.info(f"  → No replied leads")
                    continue

                needs_ai: List[Dict] = []

                for lead_data in replied_leads:
                    lead = lead_data.get("lead", lead_data)
                    email = lead.get("email", "")
                    if not email:
                        continue

                    stats["total_replied"] += 1

                    # Get preview text if available (some SmartLead formats include it)
                    preview = ""
                    if isinstance(lead_data.get("reply"), dict):
                        preview = lead_data["reply"].get("text", "")
                    elif isinstance(lead_data.get("reply"), str):
                        preview = lead_data["reply"]
                    # Also check top-level fields
                    if not preview:
                        preview = lead.get("reply_text", "") or lead.get("preview_text", "")

                    lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
                    lead_company = lead.get("company_name", "")

                    # ── Tier 1: Free keyword pre-filter ──
                    prefilter_result = _tier1_prefilter(preview) if preview else None

                    if prefilter_result:
                        cat, conf = prefilter_result
                        if cat == "out_of_office":
                            stats["tier1_ooo"] += 1
                        elif cat == "unsubscribe":
                            stats["tier1_unsub"] += 1

                        entry = {
                            "email": email,
                            "name": lead_name,
                            "company": lead_company,
                            "campaign_id": camp_id,
                            "campaign_name": camp_name,
                            "category": cat,
                            "confidence": conf,
                            "reasoning": f"Pre-filtered: {cat}",
                            "reply_text": preview[:300],
                            "needs_reply": False,
                            "received_at": lead.get("reply_time") or lead.get("replied_at") or "",
                        }
                        all_analyzed.append(entry)
                        continue

                    # ── Tier 2: Fetch thread for real conversations ──
                    reply_text = preview
                    subject = lead.get("email_subject", "")

                    if not reply_text:
                        # Need to fetch message history
                        try:
                            messages = await smartlead_service.get_lead_message_history(camp_id, email)
                            if messages:
                                latest = _extract_latest_reply(messages)
                                if latest:
                                    reply_text = latest
                                    stats["tier2_thread_fetched"] += 1

                                    # Re-check tier 1 with full text
                                    recheck = _tier1_prefilter(reply_text)
                                    if recheck:
                                        cat, conf = recheck
                                        if cat == "out_of_office":
                                            stats["tier1_ooo"] += 1
                                        entry = {
                                            "email": email, "name": lead_name, "company": lead_company,
                                            "campaign_id": camp_id, "campaign_name": camp_name,
                                            "category": cat, "confidence": conf,
                                            "reasoning": f"Pre-filtered after thread fetch: {cat}",
                                            "reply_text": reply_text[:300], "needs_reply": False,
                                            "received_at": lead.get("reply_time", ""),
                                        }
                                        all_analyzed.append(entry)
                                        continue
                            else:
                                stats["tier2_thread_failed"] += 1
                        except Exception as e:
                            stats["tier2_thread_failed"] += 1
                            logger.debug(f"Thread fetch failed for {email}: {e}")

                    if not reply_text:
                        # No text at all — can't classify
                        all_analyzed.append({
                            "email": email, "name": lead_name, "company": lead_company,
                            "campaign_id": camp_id, "campaign_name": camp_name,
                            "category": "other", "confidence": 0.2,
                            "reasoning": "No reply text available",
                            "reply_text": "", "needs_reply": False,
                            "received_at": lead.get("reply_time", ""),
                        })
                        continue

                    # ── Queue for Tier 3: AI classification ──
                    needs_ai.append({
                        "email": email, "name": lead_name, "company": lead_company,
                        "campaign_id": camp_id, "campaign_name": camp_name,
                        "reply_text": reply_text, "subject": subject,
                        "received_at": lead.get("reply_time", ""),
                    })

                # ── Tier 3: GPT-4o-mini batch classification ──
                if needs_ai and openai_key:
                    classified = await _tier3_classify_batch(needs_ai, openai_key)
                    for r in classified:
                        cat = r.get("category", "other")
                        r["needs_reply"] = cat in ("interested", "meeting_request", "question")
                        all_analyzed.append(r)
                        stats["tier3_ai_classified"] += 1
                elif needs_ai:
                    # No OpenAI key — fall back to keyword classification
                    for r in needs_ai:
                        text = r.get("reply_text", "")
                        cat, conf = _keyword_classify(text)
                        r.update({"category": cat, "confidence": conf, "reasoning": "Keyword fallback (no OpenAI key)"})
                        r["needs_reply"] = cat in ("interested", "meeting", "question")
                        all_analyzed.append(r)

            # ── Store results in DB ──
            stored = 0
            for entry in all_analyzed:
                cat = entry.get("category", "other")
                stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1

                email = entry.get("email")
                if not email:
                    continue

                result = await session.execute(
                    select(ExtractedContact).where(
                        ExtractedContact.project_id == project_id,
                        ExtractedContact.email == email,
                    )
                )
                contact = result.scalar_one_or_none()
                if contact:
                    sd = contact.source_data or {}
                    sd["has_replied"] = True
                    sd["reply_category"] = cat
                    sd["reply_confidence"] = entry.get("confidence", 0.5)
                    sd["reply_text_preview"] = entry.get("reply_text", "")[:300]
                    sd["reply_time"] = entry.get("received_at", str(datetime.utcnow()))
                    sd["reply_campaign"] = entry.get("campaign_name", "")
                    sd["reply_reasoning"] = entry.get("reasoning", "")
                    sd["needs_reply"] = entry.get("needs_reply", False)
                    contact.source_data = sd
                    flag_modified(contact, "source_data")
                    stored += 1

            await session.commit()

            # ── Build summary and cache ──
            duration = time.time() - t_start
            summary = {
                "total_replied": stats["total_replied"],
                "total_analyzed": len(all_analyzed),
                "ooo_skipped": stats["tier1_ooo"],
                "unsub_skipped": stats["tier1_unsub"],
                "ai_classified": stats["tier3_ai_classified"],
                "threads_fetched": stats["tier2_thread_fetched"],
                "threads_failed": stats["tier2_thread_failed"],
                "stored_in_db": stored,
                "by_category": stats["by_category"],
                "duration_seconds": round(duration, 1),
                "needs_reply_count": sum(1 for r in all_analyzed if r.get("needs_reply")),
                "warm_count": sum(1 for r in all_analyzed if r.get("category") in ("interested", "meeting_request")),
            }

            # Cache for tool queries
            _reply_cache[project_id] = {
                "analyzed_at": datetime.utcnow().isoformat(),
                "replies": all_analyzed,
                "summary": summary,
                "campaigns": list(campaign_names.values()),
            }

            logger.info(
                f"[ReplyAnalysis] DONE in {duration:.1f}s: "
                f"{stats['total_replied']} replied → {stats['tier1_ooo']} OOO skipped → "
                f"{stats['tier3_ai_classified']} AI classified → "
                f"{summary['warm_count']} warm, {summary['needs_reply_count']} need reply"
            )

            return summary

        except Exception as e:
            logger.error(f"[ReplyAnalysis] FAILED: {e}", exc_info=True)
            await session.rollback()
            return {"error": str(e)}


def _keyword_classify(text: str) -> Tuple[str, float]:
    """Fallback keyword classifier when OpenAI is unavailable."""
    if not text:
        return "other", 0.3

    lower = text.lower()

    # Meeting signals
    meeting_kw = ["meeting", "call", "schedule", "calendar", "demo", "zoom", "teams", "available", "slot", "time today"]
    if any(kw in lower for kw in meeting_kw):
        return "meeting_request", 0.7

    # Interested
    interested_kw = ["interested", "tell me more", "sounds good", "like to know", "send me", "more info", "pricing", "proposal"]
    if any(kw in lower for kw in interested_kw):
        return "interested", 0.7

    # Not interested
    not_interested_kw = ["not interested", "no thank", "don't contact", "no need", "not looking", "pass", "decline"]
    if any(kw in lower for kw in not_interested_kw):
        return "not_interested", 0.7

    # Wrong person
    wrong_kw = ["wrong person", "not the right", "no longer", "left the company", "try reaching"]
    if any(kw in lower for kw in wrong_kw):
        return "wrong_person", 0.7

    # Question
    if "?" in text and len(text) > 20:
        return "question", 0.6

    return "other", 0.4


async def start_background_analysis(project_id: int, user_id: int):
    """M7: Start background reply analysis for a project (called during blacklist phase)."""
    try:
        from app.db import async_session_maker
        from app.models.campaign import Campaign
        from app.services.smartlead_service import SmartLeadService
        from sqlalchemy import select

        async with async_session_maker() as session:
            campaigns = (await session.execute(
                select(Campaign).where(Campaign.project_id == project_id, Campaign.external_id.isnot(None))
            )).scalars().all()
            if not campaigns:
                logger.info(f"[ReplyAnalysis] No campaigns for project {project_id}, skipping background analysis")
                return
            sl = SmartLeadService()
            if not sl.is_configured():
                return
            campaign_ids = [int(c.external_id) for c in campaigns if c.external_id]
            campaign_names = {int(c.external_id): c.name for c in campaigns if c.external_id}
            await analyze_campaign_replies(sl, campaign_ids, campaign_names, project_id)
    except Exception as e:
        logger.warning(f"[ReplyAnalysis] Background analysis failed for project {project_id}: {e}")


def start_reply_analysis_background(smartlead_service, campaign_ids, campaign_names, project_id, openai_key=None):
    """Fire and forget — runs reply analysis in background asyncio task."""
    loop = asyncio.get_event_loop()
    task = loop.create_task(
        analyze_campaign_replies(smartlead_service, campaign_ids, campaign_names, project_id, openai_key)
    )
    logger.info(f"[ReplyAnalysis] Background task started: project {project_id}, {len(campaign_ids)} campaigns")
    return task
