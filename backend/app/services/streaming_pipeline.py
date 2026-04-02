"""Streaming Pipeline — SINGLE MODE, every company flows through immediately.

Architecture:
  scrape_queue → Scraper (100 concurrent) → classify_queue → Classifier (100 concurrent) → people_queue → People (20 concurrent)

  1. Workers start FIRST (scraper, classifier, people)
  2. Existing companies (from tam_gather probe+confirm) fed to scrape_queue — flow immediately
  3. If KPI not met: Apollo pages fetched in PARALLEL batches of 10 → results fed to same scrape_queue
  4. Exhaustion: 20 empty pages → regenerate keywords via GPT (up to 5 times per strategy)
  5. KPI checked after each person — pipeline stops immediately when target met
  6. On completion or exhaustion: auto-push gathered contacts to SmartLead

No batch phases. No serial waiting. Each company flows through scrape→classify→people
as soon as it's discovered — whether from tam_gather or fresh Apollo pages.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from math import ceil
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select, or_, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gathering import GatheringRun, CompanySourceLink
from app.models.pipeline import DiscoveredCompany, ExtractedContact
try:
    from app.models.project import Project
except ImportError:
    from app.models.contact import Project

logger = logging.getLogger(__name__)

DEFAULT_TARGET_COUNT = 100
DEFAULT_CONTACTS_PER_COMPANY = 3


class StreamingPipeline:
    """Streaming pipeline — each company flows through all phases immediately."""

    def __init__(self, session: AsyncSession, run: GatheringRun,
                 openai_key: str, apollo_service=None, apify_proxy: Optional[str] = None):
        # NOTE: session stored for backward compat but NEVER used — all ops use own sessions
        self.run = run
        self.openai_key = openai_key
        self.apollo = apollo_service
        # Pass OpenAI key to Apollo for GPT-powered role filtering
        if apollo_service and openai_key:
            apollo_service._openai_key = openai_key
        self.apify_proxy = apify_proxy

        # KPI
        self.target_count = run.target_people or DEFAULT_TARGET_COUNT
        self.max_per_company = run.max_people_per_company or DEFAULT_CONTACTS_PER_COMPANY

        # Counters
        self.total_companies = 0
        self.total_scraped = 0
        self.total_classified = 0
        self.total_targets = 0
        self.total_people = 0
        self.pages_fetched = 0

        # Queues (for streaming phase only — Apollo page discovery)
        self.scrape_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.classify_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.people_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # State
        self._kpi_met = False
        self._stop = False
        self._domains_seen: Set[str] = set()
        self._started_at = time.time()
        self._tam_pages = run.pages_fetched or 0  # Pages already consumed by tam_gather

        # Shared services (initialized once, reused across phases)
        self._scraper = None
        self._offer_text = None
        self._person_titles = None

    async def _init_services(self):
        """Initialize shared services once. Own session — no shared state."""
        from app.services.scraper_service import ScraperService
        from app.db import async_session_maker
        try:
            self._scraper = ScraperService(apify_proxy_password=self.apify_proxy)
        except TypeError:
            self._scraper = ScraperService()  # Server version takes no args

        async with async_session_maker() as ws:
            project = await ws.get(Project, self.run.project_id)
            self._offer_text = project.target_segments if project else ""
            self._segments = []
            self._person_titles = None
            self._person_exclude_titles = None

            if project and project.offer_summary and isinstance(project.offer_summary, dict):
                offer = project.offer_summary
                target_roles = offer.get("target_roles", {})
                if target_roles.get("titles"):
                    self._person_titles = target_roles["titles"]
                # Negative role list from document or defaults
                if target_roles.get("exclude_titles"):
                    self._person_exclude_titles = target_roles["exclude_titles"]
                # Segments from document extraction
                segments = offer.get("segments", [])
                if segments:
                    self._segments = [s.get("name", "") for s in segments if s.get("name")]
                # Load geo filters from document extraction (ALWAYS applied, never dropped)
                apollo_filters = offer.get("apollo_filters", {})
                self._project_locations = apollo_filters.get("locations", [])
                self._project_employee_range = apollo_filters.get("employee_range")
                # Load exclusion list from document for Agent #2
                if offer.get("exclusion_list"):
                    self._exclusion_list = offer["exclusion_list"]
                # Use stored classification prompt if available
                if offer.get("classification_prompt"):
                    self._classification_prompt = offer["classification_prompt"]

            # Generate classification prompt if not stored
            if not getattr(self, "_classification_prompt", None):
                # If document has exclusion list → use Agent #2 to generate from it
                if getattr(self, '_exclusion_list', None):
                    try:
                        self._classification_prompt = await self._generate_classification_prompt()
                    except Exception:
                        self._classification_prompt = self._build_via_negativa_prompt()
                else:
                    # No exclusion list from document → use minimal via negativa
                    self._classification_prompt = self._build_via_negativa_prompt()

    def _build_via_negativa_prompt(self) -> str:
        """Build via negativa classification prompt — dynamic from project context.

        Uses offer text, segments, and document exclusion list.
        No generic hardcoded categories — exclusions derived from context.
        """
        segments_line = ""
        if self._segments:
            segments_line = (
                f"\nTARGET SEGMENTS: {', '.join(self._segments)}\n"
                f"If target, assign ONE of these segments.\n"
            )

        # Include document's exclusion list if available
        exclusion_rules = ""
        if getattr(self, '_exclusion_list', None):
            rules = [f"- {ex.get('type','')}: {ex.get('reason','')}" for ex in self._exclusion_list]
            exclusion_rules = "\nDOCUMENT EXCLUSIONS:\n" + "\n".join(rules) + "\n"

        return (
            f"You classify companies as potential customers using VIA NEGATIVA.\n\n"
            f"WE SELL: {self._offer_text}\n"
            f"{segments_line}"
            f"{exclusion_rules}\n"
            f"EXCLUDE (is_target=false) if the company would clearly NOT buy what we sell.\n"
            f"INCLUDE (is_target=true) if the company could realistically be a customer.\n"
            f"When in doubt → include.\n\n"
            f"Return JSON: {{\"is_target\": true/false, \"segment\": \"CAPS_LABEL\", \"reasoning\": \"1 line\"}}"
        )

    async def _generate_classification_prompt(self) -> str:
        """Agent #2: Generate optimal classification prompt from project context.

        Reads offer, segments, target audience, AND document exclusion list
        → generates exclusion rules specific to THIS campaign. No hardcoded categories.
        """
        import httpx, json

        # Get exclusion list from project offer_summary if available
        exclusion_context = ""
        if hasattr(self, '_exclusion_list') and self._exclusion_list:
            exclusion_lines = [f"- {ex.get('type','')}: {ex.get('reason','')}" for ex in self._exclusion_list]
            exclusion_context = f"\nDOCUMENT EXCLUSION LIST (from user's strategy doc):\n" + "\n".join(exclusion_lines) + "\n"

        meta_prompt = (
            f"Generate a classification prompt for filtering companies in a lead generation pipeline.\n\n"
            f"CONTEXT:\n"
            f"We sell: {self._offer_text}\n"
            f"Target segments: {', '.join(self._segments) if self._segments else 'general'}\n"
            f"{exclusion_context}\n"
            f"Generate a VIA NEGATIVA classification prompt that:\n"
            f"1. Lists 5-8 EXCLUSION rules specific to this campaign (what types of companies are NOT potential buyers)\n"
            f"2. INCORPORATE the document's exclusion list above (if provided) into the rules\n"
            f"3. Lists 3-4 INCLUSION rules (what makes a company a good target)\n"
            f"4. Uses the offer context to derive exclusions — don't use generic rules\n"
            f"5. Ends with: Return JSON: {{\"is_target\": true/false, \"segment\": \"CAPS_LABEL\", \"reasoning\": \"1 line\"}}\n\n"
            f"Return ONLY the prompt text, no explanation."
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": meta_prompt}],
                          "max_tokens": 1000, "temperature": 0},
                )
                data = resp.json()
                generated = data["choices"][0]["message"]["content"].strip()
                if generated.startswith("```"):
                    generated = generated.split("\n", 1)[1].rsplit("```", 1)[0]
                logger.info(f"Agent #2 generated classification prompt: {len(generated)} chars")
                return generated
        except Exception as e:
            logger.warning(f"Agent #2 prompt generation failed: {e}")
            return self._build_via_negativa_prompt()


    async def run_until_kpi(self, filters: Dict) -> Dict:
        """Main entry — SINGLE STREAMING MODE for everything.

        All companies (existing + new Apollo) flow through the same queue workers:
          scrape_queue → scraper (100) → classify_queue → classifier (100) → people_queue → people (20)

        Each company flows immediately — no waiting for batches.
        """
        # Use own session for all run updates — never share with workers
        from app.db import async_session_maker as _asm
        from sqlalchemy import update as _upd
        async with _asm() as _ws:
            await _ws.execute(_upd(GatheringRun).where(GatheringRun.id == self.run.id).values(started_at=datetime.now(timezone.utc)))
            await _ws.commit()
        await self._init_services()

        logger.info(f"Streaming pipeline started: target={self.target_count} people, "
                     f"max {self.max_per_company}/company")

        # Start streaming workers — they run the ENTIRE time
        workers = [
            asyncio.create_task(self._scraper_worker()),
            asyncio.create_task(self._classifier_worker()),
            asyncio.create_task(self._people_worker()),
        ]

        # Feed existing companies (probe 100) → scrape starts INSTANTLY
        existing = await self._load_existing_companies()
        if existing:
            logger.info(f"Feeding {len(existing)} existing companies to streaming queue")
            self.total_companies = len(existing)
            for dc in existing:
                self._domains_seen.add(dc.domain)
                await self.scrape_queue.put(dc)

        # Apollo pages run IN PARALLEL with scraping (not blocking)
        # Workers are ALREADY processing probe companies while Apollo fetches pages 2-10+
        try:
            if not self._kpi_met:
                await self._feed_apollo_pages(filters)
        except Exception as e:
            logger.error(f"Apollo page fetching failed: {e}")
        finally:
            # ALWAYS send poison pill — workers must stop even if Apollo crashes
            await self.scrape_queue.put(None)

        await asyncio.gather(*workers, return_exceptions=True)

        # Final progress persist
        await self._persist_progress()

        elapsed = time.time() - self._started_at
        logger.info(f"Pipeline done: {self.total_scraped} scraped, {self.total_targets} targets, "
                    f"{self.total_people} people in {elapsed:.0f}s")
        return self._build_result(elapsed)

    async def _load_existing_companies(self) -> List[DiscoveredCompany]:
        """Load companies for this run. Uses own session, returns DETACHED objects."""
        from app.db import async_session_maker
        async with async_session_maker() as ws:
            result = await ws.execute(
                select(DiscoveredCompany)
                .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
                .where(
                    CompanySourceLink.gathering_run_id == self.run.id,
                    or_(
                        DiscoveredCompany.status.in_(["new", "gathered"]),
                        DiscoveredCompany.status.is_(None),
                    ),
                    DiscoveredCompany.scraped_text.is_(None),
                )
            )
            companies = result.scalars().all()
            if companies:
                from types import SimpleNamespace
                return [SimpleNamespace(
                    id=dc.id, domain=dc.domain, name=dc.name, industry=dc.industry,
                    employee_count=dc.employee_count, country=dc.country, city=dc.city,
                    project_id=dc.project_id, company_id=dc.company_id,
                    scraped_text=dc.scraped_text, status=dc.status, is_target=dc.is_target,
                    analysis_segment=dc.analysis_segment, analysis_reasoning=dc.analysis_reasoning,
                ) for dc in companies]

            result = await ws.execute(
                select(DiscoveredCompany)
                .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
                .where(
                    CompanySourceLink.gathering_run_id == self.run.id,
                    DiscoveredCompany.scraped_text.isnot(None),
                    DiscoveredCompany.is_target.is_(None),
                )
            )
            companies = result.scalars().all()
            from types import SimpleNamespace
            return [SimpleNamespace(
                id=dc.id, domain=dc.domain, name=dc.name, industry=dc.industry,
                employee_count=dc.employee_count, country=dc.country, city=dc.city,
                project_id=dc.project_id, company_id=dc.company_id,
                scraped_text=dc.scraped_text, status=dc.status, is_target=dc.is_target,
                analysis_segment=dc.analysis_segment, analysis_reasoning=dc.analysis_reasoning,
            ) for dc in companies]

    # ── Apollo page fetching + streaming workers ──

    # Per-strategy page limits (from pipeline_spec.md)
    PAGES_PER_STRATEGY = 25       # Level 1 + Level 2: 25 pages each
    PAGES_PER_REGEN_CYCLE = 20    # Level 3: 20 pages per regeneration cycle
    MAX_KEYWORD_REGENERATIONS = 5  # 5 regen cycles × 20 pages = 100 max
    EXHAUSTION_THRESHOLD = 10     # 10 consecutive empty pages = strategy exhausted
    MAX_TOTAL_PAGES = 150         # Absolute safety cap: 25 + 25 + 100 = 150

    async def _feed_apollo_pages(self, filters: Dict):
        """3-level strategy cascade per pipeline_spec.md.

        Uses Apollo service DIRECTLY (not main app's GatheringService adapter).
        Industry-first: L1 industry(25p) → L2 keywords(25p) → L3 regen(5×20p)
        Keywords-first:  L1 keywords(25p) → L2 regen(5×20p) → L3 industry(25p)
        """
        if not self.apollo:
            logger.warning("No Apollo service — cannot feed pages")
            return

        # Pre-load existing domains
        from app.db import async_session_maker as _asm
        async with _asm() as _ws:
            existing_result = await _ws.execute(
                select(DiscoveredCompany.domain).where(DiscoveredCompany.project_id == self.run.project_id)
            )
            self._domains_seen.update(r[0] for r in existing_result.all())

        per_page = filters.get("per_page", 100)
        all_tried_keywords = set(k.lower() for k in (filters.get("q_organization_keyword_tags") or []))

        # HARD FILTERS from project — ALWAYS applied, never dropped
        # Inject project locations if not already in filters
        if not filters.get("organization_locations") and getattr(self, '_project_locations', None):
            filters["organization_locations"] = self._project_locations
            logger.info(f"Injected project locations: {self._project_locations}")
        # Inject project employee range if not already in filters
        if not filters.get("organization_num_employees_ranges") and getattr(self, '_project_employee_range', None):
            filters["organization_num_employees_ranges"] = [self._project_employee_range]

        # Build strategy cascade — funding as Level 0 priority (soft filter)
        # Funding prioritizes but doesn't exclude: funded first → all → regen → industry
        strategy = filters.get("filter_strategy", "keywords_only")
        has_industry = bool(filters.get("organization_industry_tag_ids"))
        has_keywords = bool(filters.get("q_organization_keyword_tags"))
        has_funding = bool(filters.get("organization_latest_funding_stage_cd") or
                          filters.get("mapping_details", {}).get("funding_stages"))

        # Extract funding stages from filters
        funding_stages = (filters.get("organization_latest_funding_stage_cd") or
                         filters.get("mapping_details", {}).get("funding_stages"))

        levels = []

        # Level 0: Funding + primary strategy (highest quality — funded companies first)
        if has_funding and funding_stages:
            if strategy == "industry_first" and has_industry:
                l0_filters = self._make_industry_filters(filters)
            else:
                l0_filters = self._make_keywords_filters(filters)
            l0_filters["organization_latest_funding_stage_cd"] = funding_stages
            levels.append(("L0_funded", l0_filters, self.PAGES_PER_STRATEGY))

        # Levels 1+: Same strategy WITHOUT funding (broader pool)
        if strategy == "industry_first" and has_industry:
            levels.extend([
                ("L1_industry", self._make_industry_filters(filters), self.PAGES_PER_STRATEGY),
                ("L2_keywords", self._make_keywords_filters(filters), self.PAGES_PER_STRATEGY),
            ])
        else:
            levels.append(("L1_keywords", self._make_keywords_filters(filters), self.PAGES_PER_STRATEGY))
            if has_industry:
                levels.append(("L3_industry", self._make_industry_filters(filters), self.PAGES_PER_STRATEGY))

        total_pages = 0

        for level_name, level_filters, max_pages in levels:
            if self._kpi_met or self._stop or total_pages >= self.MAX_TOTAL_PAGES:
                break

            exhausted = await self._run_level(
                level_filters, per_page, level_name,
                max_pages=max_pages,
                start_page=(self._tam_pages + 1) if "L1" in level_name else 1,
            )
            total_pages += self.pages_fetched  # approximate

            if exhausted and not self._kpi_met:
                # Level exhausted → try keyword regeneration before next level
                for regen_num in range(1, self.MAX_KEYWORD_REGENERATIONS + 1):
                    if self._kpi_met or self._stop or total_pages >= self.MAX_TOTAL_PAGES:
                        break
                    new_kw = await self._regenerate_keywords(level_filters, all_tried_keywords)
                    if not new_kw:
                        logger.info(f"Regen #{regen_num}: no new keywords. Moving to next level.")
                        break
                    all_tried_keywords.update(k.lower() for k in new_kw)
                    regen_filters = dict(level_filters)
                    regen_filters["q_organization_keyword_tags"] = new_kw
                    regen_filters.pop("organization_industry_tag_ids", None)
                    regen_filters["filter_strategy"] = "keywords_first"

                    regen_exhausted = await self._run_level(
                        regen_filters, per_page, f"regen_{regen_num}",
                        max_pages=self.PAGES_PER_REGEN_CYCLE, start_page=1,
                    )
                    if not regen_exhausted:
                        break  # Got results, KPI may be met

        if not self._kpi_met:
            logger.info(f"Phase 2 fully exhausted: {self.pages_fetched} pages, "
                       f"{self.total_companies} companies, {self.total_people} people")

    async def _run_level(self, filters: Dict, per_page: int,
                          label: str, max_pages: int, start_page: int = 1) -> bool:
        """Run one strategy level. Returns True if exhausted (10 consecutive empty)."""
        consecutive_empty = 0
        pages_this_level = 0
        page = start_page

        logger.info(f"[{label}] start page={page}, max={max_pages} pages")

        while pages_this_level < max_pages:
            if self._kpi_met or self._stop:
                return False

            batch_size = min(10, max_pages - pages_this_level)
            # Fetches pages AND feeds companies to scrape queue AS each page arrives
            page_counts = await self._fetch_pages_parallel(
                filters, per_page, page, batch_size
            )

            # Process results (companies already in scrape queue from fetch_and_feed)
            for page_num, new_count in page_counts:
                if self._kpi_met or self._stop:
                    return False
                self.pages_fetched += 1
                pages_this_level += 1
                if new_count <= 0:  # 0 = empty, -1 = error
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                if consecutive_empty >= self.EXHAUSTION_THRESHOLD:
                    logger.info(f"[{label}] exhausted after {pages_this_level} pages "
                               f"({consecutive_empty} consecutive empty)")
                    await self._persist_progress()
                    return True

            logger.info(f"[{label}] batch pages {page}-{page+batch_size-1}: "
                       f"{self.total_companies} companies, {self.total_people} people")
            await self._persist_progress()
            page += batch_size

        logger.info(f"[{label}] completed {pages_this_level}/{max_pages} pages")
        return pages_this_level >= max_pages  # Hit page limit = exhausted

    def _make_industry_filters(self, base: Dict) -> Dict:
        """Industry-only filters (drop keywords)."""
        f = dict(base)
        f.pop("q_organization_keyword_tags", None)
        f["filter_strategy"] = "industry_first"
        return f

    def _make_keywords_filters(self, base: Dict) -> Dict:
        """Keywords-only filters (drop industry)."""
        f = dict(base)
        f.pop("organization_industry_tag_ids", None)
        f["filter_strategy"] = "keywords_first"
        return f

    async def _fetch_pages_parallel(self, filters: Dict, per_page: int,
                                     start_page: int, count: int) -> list:
        """Fetch `count` pages in parallel using self.apollo DIRECTLY.
        Feed companies to scrape_queue AS EACH PAGE ARRIVES.
        NEVER uses main app's GatheringService or adapter pattern."""
        results_list = []

        # Extract Apollo search params from filters
        keyword_tags = filters.get("q_organization_keyword_tags")
        industry_tag_ids = filters.get("organization_industry_tag_ids")
        locations = filters.get("organization_locations")
        num_employees = filters.get("organization_num_employees_ranges")
        funding_stages = filters.get("organization_latest_funding_stage_cd")

        async def fetch_and_feed(page_num):
            """Fetch one page via self.apollo and immediately feed to scrape queue."""
            try:
                data = await self.apollo.search_organizations(
                    keyword_tags=keyword_tags,
                    industry_tag_ids=industry_tag_ids,
                    locations=locations,
                    num_employees_ranges=num_employees,
                    latest_funding_stages=funding_stages,
                    page=page_num,
                    per_page=per_page,
                )
                orgs = (data or {}).get("organizations", [])
                # Track Apollo search credit (1 credit per page)
                self.run.credits_used = (self.run.credits_used or 0) + 1
                # Convert to pipeline format
                results = []
                for org in orgs:
                    domain = org.get("primary_domain") or org.get("domain", "")
                    if domain:
                        if domain.startswith("http"):
                            from urllib.parse import urlparse
                            domain = urlparse(domain).hostname or domain
                        if domain.startswith("www."):
                            domain = domain[4:]
                        results.append({
                            "domain": domain.strip().lower(),
                            "name": org.get("name", domain),
                            "industry": org.get("industry"),
                            "employee_count": org.get("estimated_num_employees"),
                            "country": org.get("country"),
                            "city": org.get("city"),
                        })
                # Feed to scrape queue IMMEDIATELY
                if results:
                    new_count = await self._ingest_page_results(results)
                    results_list.append((page_num, new_count))
                else:
                    results_list.append((page_num, 0))
            except Exception as e:
                logger.warning(f"Apollo page {page_num} failed: {e}")
                results_list.append((page_num, -1))

        tasks = [fetch_and_feed(start_page + i) for i in range(count)]
        await asyncio.gather(*tasks)
        return sorted(results_list, key=lambda x: x[0])

    async def _regenerate_keywords(self, current_filters: Dict, all_tried: Set[str]) -> Optional[list]:
        """Generate fresh keywords when current ones are exhausted.

        Args:
            current_filters: Current strategy filters
            all_tried: ALL keywords tried across ALL strategies and regenerations (lowercase)
        """
        try:
            from app.db import async_session_maker
            async with async_session_maker() as ws:
                project = await ws.get(Project, self.run.project_id)
                if not project:
                    return None
                query = project.target_segments or ", ".join(list(all_tried)[:5])
                offer = project.sender_company or ""

                from app.services.user_context import UserServiceContext
                user_id_str = self.run.triggered_by.split(":")[-1] if self.run.triggered_by else "0"
                try:
                    user_id_int = int(user_id_str)
                except ValueError:
                    user_id_int = 0
                ctx = UserServiceContext(user_id_int, ws)
                openai_key = await ctx.get_key("openai")

            old_keywords = list(all_tried)
            if not openai_key:
                return None

            import httpx, json
            prompt = (
                f"I'm searching Apollo.io for: {query}\n"
                f"Our product: {offer}\n\n"
                f"ALL these keywords have been tried and exhausted ({len(old_keywords)} total):\n"
                f"{json.dumps(old_keywords[:100])}\n\n"
                f"Generate 20-30 COMPLETELY NEW keyword variations.\n"
                f"Think creatively: synonyms, adjacent niches, specific product/service names,\n"
                f"alternate phrasings, industry jargon, related technologies.\n"
                f"Do NOT repeat ANY of the exhausted keywords above.\n\n"
                f"Return ONLY a JSON array of strings: [\"keyword1\", \"keyword2\", ...]"
            )
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 1000, "temperature": 0.7},
                )
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                new_keywords = json.loads(content)
                # Filter out ALL previously tried keywords
                fresh = [k for k in new_keywords if k.lower() not in all_tried]
                logger.info(f"Regenerated {len(fresh)} fresh keywords ({len(all_tried)} previously tried)")
                return fresh if fresh else None
        except Exception as e:
            logger.warning(f"Keyword regeneration failed: {e}")
            return None

    # _build_strategies REMOVED — replaced by 3-level cascade in _feed_apollo_pages

    async def _ingest_page_results(self, results) -> int:
        """Ingest Apollo page results. Own session — no conflicts with workers."""
        from app.db import async_session_maker
        page_companies = []
        for company_data in (results or []):
            if self._kpi_met:
                break
            domain = company_data.get("domain", "").lower().strip()
            if not domain or domain in self._domains_seen:
                continue
            self._domains_seen.add(domain)
            page_companies.append(company_data)

        if not page_companies:
            return 0

        # Create companies in own session
        created_dcs = []
        async with async_session_maker() as ws:
            for company_data in page_companies:
                domain = company_data.get("domain", "").lower().strip()
                dc = DiscoveredCompany(
                    project_id=self.run.project_id,
                    company_id=self.run.company_id,
                    domain=domain,
                    name=company_data.get("name"),
                    industry=company_data.get("industry"),
                    employee_count=company_data.get("employee_count"),
                    country=company_data.get("country"),
                    city=company_data.get("city"),
                    source_data=company_data,
                )
                ws.add(dc)
            await ws.flush()
            for dc in ws.new:
                pass  # flush assigned IDs
            # Re-query to get IDs
            from sqlalchemy import text as sa_text
            r = await ws.execute(sa_text(
                f"SELECT id, domain, name, industry, employee_count, country, city "
                f"FROM discovered_companies WHERE project_id={self.run.project_id} "
                f"AND domain IN ({','.join(repr(c.get('domain','').lower().strip()) for c in page_companies)}) "
                f"ORDER BY id DESC LIMIT {len(page_companies)}"
            ))
            rows = r.fetchall()
            for row in rows:
                # Use simple namespace — NOT a SQLAlchemy model (avoids session tracking)
                from types import SimpleNamespace
                dc = SimpleNamespace(
                    id=row[0], domain=row[1], name=row[2], industry=row[3],
                    employee_count=row[4], country=row[5], city=row[6],
                    project_id=self.run.project_id, company_id=self.run.company_id,
                    scraped_text=None, status=None, is_target=None,
                    analysis_segment=None, analysis_reasoning=None,
                )
                created_dcs.append(dc)
                ws.add(CompanySourceLink(discovered_company_id=dc.id, gathering_run_id=self.run.id))
            await ws.commit()

        for dc in created_dcs:
            self.total_companies += 1
            await self.scrape_queue.put(dc)

        return len(created_dcs)

    async def _scraper_worker(self):
        """Streaming scrape worker — 100 concurrent. Own session for DB writes."""
        from app.db import async_session_maker
        sem = asyncio.Semaphore(100)

        async def scrape_one(dc):
            async with sem:
                try:
                    result = await self._scraper.scrape_website(f"https://{dc.domain}")
                    if result.get("success"):
                        text = result["text"][:50000]
                        # Write to DB with own session (no conflicts)
                        async with async_session_maker() as ws:
                            await ws.execute(
                                select(DiscoveredCompany).where(DiscoveredCompany.id == dc.id)
                            )  # load into session
                            from sqlalchemy import update
                            await ws.execute(
                                update(DiscoveredCompany).where(DiscoveredCompany.id == dc.id).values(
                                    scraped_text=text,
                                    scraped_at=datetime.now(timezone.utc),
                                    status="scraped",
                                )
                            )
                            await ws.commit()
                        dc.scraped_text = text  # keep in memory for classifier
                        dc.status = "scraped"
                        self.total_scraped += 1
                        await self.classify_queue.put(dc)
                    else:
                        async with async_session_maker() as ws:
                            from sqlalchemy import update
                            await ws.execute(update(DiscoveredCompany).where(DiscoveredCompany.id == dc.id).values(status="scrape_failed"))
                            await ws.commit()
                        dc.status = "scrape_failed"
                except Exception as e:
                    dc.status = "scrape_failed"
                    logger.debug(f"Scrape {dc.domain}: {e}")

        tasks = []
        while True:
            dc = await self.scrape_queue.get()
            if dc is None:
                break
            if self._kpi_met:
                continue
            tasks.append(asyncio.create_task(scrape_one(dc)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.classify_queue.put(None)

    async def _classifier_worker(self):
        """Streaming classify worker — 100 concurrent. One shared httpx client."""
        from app.db import async_session_maker
        import httpx
        import json
        sem = asyncio.Semaphore(100)
        client = httpx.AsyncClient(timeout=30)

        # Log the classification prompt ONCE so Prompts page can display it
        try:
            system_prompt = self._classification_prompt
            user_id_str = self.run.triggered_by.split(":")[-1] if self.run.triggered_by else "0"
            try:
                uid = int(user_id_str)
            except ValueError:
                uid = 1
            async with async_session_maker() as log_s:
                from sqlalchemy import text as sa_text
                await log_s.execute(
                    sa_text("INSERT INTO mcp_usage_logs (user_id, tool_name, action, metadata, created_at) VALUES (:uid, :tool, :action, :data::jsonb, now())"),
                    {"uid": uid, "tool": "analysis_prompt", "action": "classify", "data": json.dumps({"run_id": self.run.id, "prompt_text": system_prompt, "model": "gpt-4o-mini"})},
                )
                await log_s.commit()
        except Exception as e:
            logger.debug(f"Prompt logging failed: {e}")

        async def classify_one(dc):
            async with sem:
                if not dc.scraped_text or self._kpi_met:
                    return
                try:
                    company_text = f"Company: {dc.name or dc.domain}\nDomain: {dc.domain}"
                    company_text += f"\n\nWebsite:\n{dc.scraped_text[:3000]}"

                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.openai_key}",
                                 "Content-Type": "application/json"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": self._classification_prompt},
                                {"role": "user", "content": company_text},
                            ],
                            "max_tokens": 200, "temperature": 0.1,
                        },
                    )
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    clean = content.strip()
                    if clean.startswith("```"):
                        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
                    parsed = json.loads(clean)

                    is_target = parsed.get("is_target", False)
                    segment = parsed.get("segment", "")
                    reasoning = parsed.get("reasoning", "")
                    confidence = parsed.get("confidence", "high")

                    # TWO-PASS: low/medium confidence → re-evaluate with gpt-4o
                    if confidence in ("low", "medium"):
                        try:
                            resp2 = await client.post(
                                "https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {self.openai_key}",
                                         "Content-Type": "application/json"},
                                json={
                                    "model": "gpt-4o",
                                    "messages": [
                                        {"role": "system", "content": self._classification_prompt},
                                        {"role": "user", "content": company_text},
                                    ],
                                    "max_tokens": 200, "temperature": 0,
                                },
                            )
                            data2 = resp2.json()
                            content2 = data2.get("choices", [{}])[0].get("message", {}).get("content", "")
                            clean2 = content2.strip()
                            if clean2.startswith("```"):
                                clean2 = clean2.split("\n", 1)[1].rsplit("```", 1)[0]
                            parsed2 = json.loads(clean2)
                            is_target = parsed2.get("is_target", is_target)
                            segment = parsed2.get("segment", segment)
                            reasoning = f"[2-pass] {parsed2.get('reasoning', reasoning)}"
                        except Exception:
                            pass  # Keep pass-1 result

                    status = "target" if is_target else "rejected"

                    # Write to DB with own session
                    async with async_session_maker() as ws:
                        from sqlalchemy import update
                        await ws.execute(
                            update(DiscoveredCompany).where(DiscoveredCompany.id == dc.id).values(
                                is_target=is_target, analysis_segment=segment,
                                analysis_reasoning=reasoning, status=status,
                            )
                        )
                        await ws.commit()

                    dc.is_target = is_target
                    dc.analysis_segment = segment
                    dc.status = status
                    self.total_classified += 1
                    if is_target:
                        self.total_targets += 1
                        await self.people_queue.put(dc)
                except Exception as e:
                    dc.status = "classify_failed"
                    logger.debug(f"Classify {dc.domain}: {e}")

        tasks = []
        while True:
            dc = await self.classify_queue.get()
            if dc is None:
                break
            if self._kpi_met:
                continue
            tasks.append(asyncio.create_task(classify_one(dc)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()  # Clean up shared httpx client
        await self.people_queue.put(None)

    async def _people_worker(self):
        """Streaming people extraction — 20 concurrent. Own session for DB writes."""
        from app.db import async_session_maker
        if not self.apollo:
            return
        sem = asyncio.Semaphore(20)

        async def extract_one(dc):
            if self._kpi_met:
                return
            async with sem:
                try:
                    people = await self.apollo.enrich_by_domain(
                        dc.domain, limit=self.max_per_company,
                        titles=self._person_titles,
                    )
                    if not people:
                        return
                    # Write contacts to DB with own session
                    async with async_session_maker() as ws:
                        for person in people:
                            ws.add(ExtractedContact(
                                project_id=self.run.project_id,
                                discovered_company_id=dc.id,
                                email=person.get("email"),
                                first_name=person.get("first_name"),
                                last_name=person.get("last_name"),
                                job_title=person.get("title") or person.get("job_title"),
                                linkedin_url=person.get("linkedin_url"),
                                email_verified=person.get("is_verified", False),
                                email_source="apollo" if person.get("is_verified") else None,
                                source_data=person,
                            ))
                            self.total_people += 1
                            if self.total_people >= self.target_count:
                                self._kpi_met = True
                                logger.info(f"KPI MET: {self.total_people} people >= {self.target_count}")
                                break
                        await ws.commit()
                    # Update progress with own session
                    self.run.credits_used = (self.run.credits_used or 0) + len(people)
                    await self._persist_progress()
                except Exception as e:
                    logger.debug(f"People search {dc.domain}: {e}")

        tasks = []
        while True:
            dc = await self.people_queue.get()
            if dc is None:
                break
            tasks.append(asyncio.create_task(extract_one(dc)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Helpers ──

    async def _persist_progress(self):
        """Write counters to DB for frontend polling. Uses own session."""
        try:
            from app.db import async_session_maker
            from sqlalchemy import update
            async with async_session_maker() as ws:
                await ws.execute(
                    update(GatheringRun).where(GatheringRun.id == self.run.id).values(
                        new_companies_count=self.total_companies,
                        pages_fetched=self._tam_pages + self.pages_fetched,
                        total_targets_found=self.total_targets,
                        total_people_found=self.total_people,
                        credits_used=self.run.credits_used or 0,
                    )
                )
                await ws.commit()
        except Exception as e:
            logger.debug(f"Progress persist failed: {e}")

    def _build_result(self, elapsed: float) -> Dict:
        total_pages = self._tam_pages + self.pages_fetched
        target_rate = round(self.total_targets / max(self.total_classified, 1) * 100)
        scrape_rate = round(self.total_scraped / max(self.total_companies, 1) * 100)

        issues = []
        if not self._kpi_met:
            if self.total_people == 0:
                issues.append("No contacts found — check filters and target segments")
            elif self.total_people < self.target_count:
                remaining = self.target_count - self.total_people
                issues.append(f"Only {self.total_people}/{self.target_count} contacts found ({remaining} short)")
            if self.total_companies > 0 and target_rate < 20:
                issues.append(f"Low target rate ({target_rate}%) — keywords may be too broad")
            if self.total_companies > 0 and scrape_rate < 30:
                issues.append(f"Low scrape rate ({scrape_rate}%) — many websites unreachable")
            if total_pages > 0 and self.pages_fetched > 0:
                issues.append(f"Apollo exhausted after {total_pages} pages — broaden location or add keywords")
            elif total_pages > 0 and self.pages_fetched == 0 and not self._kpi_met:
                issues.append(f"Not enough companies from {total_pages} pages — try broader filters")

        if self._kpi_met:
            msg = (f"Pipeline complete: {self.total_targets} targets, "
                   f"{self.total_people}/{self.target_count} people "
                   f"in {elapsed:.0f}s ({total_pages} pages, {self.run.credits_used or 0} credits)")
        else:
            msg = (f"Pipeline stopped: {self.total_people}/{self.target_count} people "
                   f"({self.total_targets} targets, {target_rate}% target rate). "
                   + (f"Sending {self.total_people} gathered contacts to SmartLead."
                      if self.total_people > 0 else "No contacts to send."))

        return {
            "status": "completed" if self._kpi_met else "insufficient",
            "kpi_met": self._kpi_met,
            "total_companies": self.total_companies,
            "total_scraped": self.total_scraped,
            "total_classified": self.total_classified,
            "total_targets": self.total_targets,
            "total_people": self.total_people,
            "pages_fetched": total_pages,
            "elapsed_seconds": round(elapsed, 1),
            "credits_used": self.run.credits_used or 0,
            "target_rate_pct": target_rate,
            "scrape_rate_pct": scrape_rate,
            "companies_per_page_avg": round(self.total_companies / max(total_pages, 1), 1),
            "issues": issues,
            "message": msg,
        }
