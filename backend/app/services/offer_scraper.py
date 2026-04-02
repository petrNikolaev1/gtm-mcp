"""Background offer scraper — analyzes websites for all projects missing offers.

Architecture:
1. INLINE: create_project does 3-layer scrape during the tool call (immediate)
2. BACKGROUND: this service catches anything missed (session broke, timeout, etc.)
   - Runs 5s after startup, then every 30s
   - Max 3 retries per project, then marks as failed
   - Can also be triggered on-demand via queue_offer_analysis(project_id)

Reliability:
- Query finds: NULL offer, empty offer, AND failed offers with retries < MAX
- Retry tracking in offer_summary JSONB (_retries, _error, _last_attempt)
- confirm_offer with feedback resets retry state
- Survives backend restarts — no session dependency
"""
import asyncio
import logging
import re
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, or_, and_, text, cast
from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE
from sqlalchemy.orm.attributes import flag_modified

from app.db import async_session_maker
from app.models.project import Project
from app.models.integration import MCPIntegrationSetting
from app.services.encryption import decrypt_value

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_pending_ids: set[int] = set()


def queue_offer_analysis(project_id: int):
    """Queue a project for immediate offer analysis (called from create_project)."""
    _pending_ids.add(project_id)


def _needs_analysis(project: Project) -> bool:
    """Check if a project needs offer analysis."""
    if not project.website:
        return False
    os = project.offer_summary
    # No offer at all
    if os is None or os == {}:
        return True
    # Has retry tracking but not maxed out, and no real offer data
    if isinstance(os, dict):
        has_real_offer = "primary_offer" in os or "products" in os
        if has_real_offer:
            return False  # Already analyzed successfully
        retries = os.get("_retries", 0)
        return retries < MAX_RETRIES
    return False


async def scrape_pending_offers():
    """Find all projects needing offer analysis, process them."""
    async with async_session_maker() as session:
        # Priority 1: explicitly queued projects
        queued = list(_pending_ids)
        _pending_ids.clear()

        # Priority 2: ALL projects with websites — filter in Python for reliability
        result = await session.execute(
            select(Project).where(
                Project.website.isnot(None),
                Project.website != "",
                Project.is_active == True,
                Project.offer_approved == False,
            )
        )
        all_candidates = result.scalars().all()

        # Merge: queued first, then candidates needing analysis
        seen = set()
        projects = []

        if queued:
            queued_result = await session.execute(
                select(Project).where(Project.id.in_(queued))
            )
            for p in queued_result.scalars().all():
                if p.id not in seen and _needs_analysis(p):
                    projects.append(p)
                    seen.add(p.id)

        for p in all_candidates:
            if p.id not in seen and _needs_analysis(p):
                projects.append(p)
                seen.add(p.id)

        if not projects:
            return

        logger.info(f"Offer scraper: {len(projects)} projects to analyze")

        for project in projects:
            try:
                success = await _analyze_project_offer(session, project)
                await session.commit()
                if success:
                    logger.info(f"Offer scraper: ✓ {project.name} ({project.website})")
                else:
                    logger.info(f"Offer scraper: retry {_get_retries(project)}/{MAX_RETRIES} for {project.name}")
            except Exception as e:
                logger.error(f"Offer scraper failed for {project.id}: {e}")
                await session.rollback()


def _get_retries(project: Project) -> int:
    os = project.offer_summary
    if isinstance(os, dict):
        return os.get("_retries", 0)
    return 0


async def _analyze_project_offer(session, project: Project) -> bool:
    """Run 3-layer offer extraction. Returns True if offer was extracted."""
    website = project.website
    if not website:
        return False
    if not website.startswith("http"):
        website = f"https://{website}"

    retries = _get_retries(project)

    # Get user's keys
    openai_row = (await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == project.user_id,
            MCPIntegrationSetting.integration_name == "openai",
            MCPIntegrationSetting.is_connected == True,
        )
    )).scalar_one_or_none()
    openai_key = decrypt_value(openai_row.api_key_encrypted) if openai_row else None
    if not openai_key:
        # Don't count as retry — user hasn't set up OpenAI yet
        return False

    apify_row = (await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == project.user_id,
            MCPIntegrationSetting.integration_name == "apify",
            MCPIntegrationSetting.is_connected == True,
        )
    )).scalar_one_or_none()
    apify_key = decrypt_value(apify_row.api_key_encrypted) if apify_row else None

    wt = ""  # website text

    # Layer 1: Apify proxy scrape
    try:
        from app.services.scraper_service import ScraperService
        scraper = ScraperService(apify_proxy_password=apify_key)
        r = await scraper.scrape_website(website)
        wt = r.get("text", "")[:4000]
    except Exception as e:
        logger.debug(f"Proxy scrape {website}: {e}")

    # Layer 2: Direct fetch + meta tags
    if not wt:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                resp = await c.get(website, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    h = resp.text
                    mp = []
                    for p in [r'<meta\s+name="description"\s+content="([^"]+)"',
                              r'<meta\s+property="og:description"\s+content="([^"]+)"',
                              r'<title>([^<]+)</title>']:
                        m = re.search(p, h, re.IGNORECASE)
                        if m:
                            mp.append(m.group(1).strip())
                    b = re.sub(r'<script[^>]*>.*?</script>', '', h, flags=re.DOTALL | re.IGNORECASE)
                    b = re.sub(r'<style[^>]*>.*?</style>', '', b, flags=re.DOTALL | re.IGNORECASE)
                    b = re.sub(r'<[^>]+>', ' ', b)
                    b = re.sub(r'\s+', ' ', b).strip()[:4000]
                    wt = b if len(b) > 100 else ("Meta: " + " | ".join(mp) if mp else "")
        except Exception:
            pass

    # Layer 3: GPT analysis (scraped text OR GPT knowledge)
    dom = website.replace("https://", "").replace("http://", "").rstrip("/")
    if wt:
        prompt = f"Analyze this company website.\nWebsite: {website}\nContent: {wt[:3000]}\n\n"
    else:
        prompt = f"What does the company at {dom} do? Use your knowledge.\n\n"

    prompt += ('Return JSON: {"company_name":"...","products":[{"name":"...","description":"..."}],'
               '"primary_offer":"main product in 1 sentence","value_proposition":"problem solved",'
               '"target_audience":"who buys","key_differentiators":["..."],'
               '"target_roles":{"titles":["CEO","CMO","VP of X","Head of Y","Director of Z"],'
               '"seniorities":["c_suite","vp","head","director"],'
               '"reasoning":"1 sentence: who at the target company would BUY this product"}}\n\n'
               'RULES for target_roles:\n'
               '- ALWAYS include CEO and relevant C-level (CTO, CMO, CFO depending on product)\n'
               '- Add 2-3 specific roles that would champion this purchase\n'
               '- These are DECISION MAKERS with budget authority, not end users\n'
               '- Seniorities: owner, founder, c_suite, partner, vp, head, director, manager\n\n'
               'If unknown: {"unknown":true}\nOnly JSON.')

    try:
        async with httpx.AsyncClient(timeout=25) as c:
            rr = await c.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 800, "temperature": 0})
            ct = rr.json()["choices"][0]["message"]["content"].strip()
            if ct.startswith("```"):
                ct = ct.split("\n", 1)[1].rsplit("```", 1)[0]
            offer = json.loads(ct)

            if offer.get("unknown"):
                project.offer_summary = {"_retries": retries + 1, "_failed": retries + 1 >= MAX_RETRIES,
                                         "_reason": f"GPT doesn't know {dom}",
                                         "_last_attempt": datetime.now(timezone.utc).isoformat()}
                flag_modified(project, "offer_summary")
                return False

            if wt:
                offer["_raw_website_text"] = wt[:2000]
            offer["_source"] = "website" if wt else "gpt_knowledge"
            offer["_analyzed_at"] = datetime.now(timezone.utc).isoformat()

            project.offer_summary = offer
            project.target_segments = f"{offer.get('primary_offer', '')}. Target: {offer.get('target_audience', '')}"
            flag_modified(project, "offer_summary")
            return True

    except Exception as e:
        project.offer_summary = {"_retries": retries + 1, "_failed": retries + 1 >= MAX_RETRIES,
                                 "_error": str(e)[:200],
                                 "_last_attempt": datetime.now(timezone.utc).isoformat()}
        flag_modified(project, "offer_summary")
        logger.error(f"Offer GPT failed for {dom}: {e}")
        return False


def start_offer_scraper():
    """Run offer scraper on startup + every 30 seconds."""
    async def _loop():
        await asyncio.sleep(5)
        while True:
            try:
                await scrape_pending_offers()
            except Exception as e:
                logger.error(f"Offer scraper loop: {e}")
            await asyncio.sleep(30)

    asyncio.get_event_loop().create_task(_loop())
