"""Gathering Service — pipeline orchestrator adapted for MCP.

Linear pipeline with 2 mandatory checkpoints:
  gather → blacklist → CP1 → pre_filter → scrape → analyze → CP2 → completed

Phase enforcement: each method checks current_phase before proceeding.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.gathering import (
    GatheringRun, CompanySourceLink, CompanyScrape,
    GatheringPrompt, AnalysisRun, AnalysisResult, ApprovalGate,
)
from app.models.pipeline import DiscoveredCompany
from app.models.project import Project
from app.services.domain_service import normalize_domain, matches_trash_pattern
from app.services.cost_tracker import get_tracker, extract_openai_usage

logger = logging.getLogger(__name__)

# Phase order for enforcement
PHASE_ORDER = [
    "gather", "blacklist", "awaiting_scope_ok", "pre_filter",
    "scrape", "analyze", "awaiting_targets_ok", "completed",
]


class GatheringService:
    """Pipeline orchestrator with phase enforcement and checkpoint gates."""

    # ── Phase enforcement ──

    def _check_phase(self, run: GatheringRun, expected: str):
        if run.current_phase != expected:
            raise ValueError(
                f"Run {run.id} is in phase '{run.current_phase}', "
                f"expected '{expected}'. Cannot skip phases."
            )

    def _advance_phase(self, run: GatheringRun, to_phase: str):
        run.current_phase = to_phase
        run.updated_at = datetime.utcnow()

    # ── Phase 1: Gather + Dedup ──

    async def start_gathering(
        self,
        session: AsyncSession,
        project_id: int,
        company_id: int,
        source_type: str,
        filters: Dict[str, Any],
        triggered_by: str = "mcp",
        apollo_service=None,
    ) -> GatheringRun:
        """Create a new gathering run and execute the gather phase."""
        filter_json = json.dumps(filters, sort_keys=True)
        filter_hash = hashlib.sha256(filter_json.encode()).hexdigest()[:16]

        run = GatheringRun(
            project_id=project_id,
            company_id=company_id,
            source_type=source_type,
            filters=filters,
            filter_hash=filter_hash,
            status="running",
            current_phase="gather",
            started_at=datetime.utcnow(),
            triggered_by=triggered_by,
        )
        session.add(run)
        await session.flush()

        # Execute adapter
        adapter = self._get_adapter(source_type, apollo_service=apollo_service)
        if adapter:
            try:
                results = await adapter.gather(filters)
                run.raw_results_count = len(results)

                # Dedup against existing discovered companies
                new_count = 0
                dup_count = 0
                for company_data in results:
                    domain = normalize_domain(company_data.get("domain", ""))
                    if not domain:
                        continue

                    # Check if already discovered for this project
                    existing = await session.execute(
                        select(DiscoveredCompany).where(
                            DiscoveredCompany.project_id == project_id,
                            DiscoveredCompany.domain == domain,
                        )
                    )
                    if existing.scalar_one_or_none():
                        dup_count += 1
                        continue

                    # Create new discovered company
                    dc = DiscoveredCompany(
                        project_id=project_id,
                        company_id=company_id,
                        domain=domain,
                        name=company_data.get("name"),
                        industry=company_data.get("industry"),
                        employee_count=company_data.get("employee_count"),
                        country=company_data.get("country"),
                        city=company_data.get("city"),
                        description=company_data.get("description"),
                        linkedin_url=company_data.get("linkedin_url"),
                        source_data=company_data.get("source_data"),
                    )
                    session.add(dc)
                    await session.flush()

                    # Link to this run
                    link = CompanySourceLink(
                        discovered_company_id=dc.id,
                        gathering_run_id=run.id,
                        source_data=company_data.get("source_data"),
                    )
                    session.add(link)
                    new_count += 1

                run.new_companies_count = new_count
                run.duplicate_count = dup_count

                # Persist Apollo credits from the service instance
                if apollo_service and hasattr(apollo_service, 'credits_used'):
                    run.credits_used = apollo_service.credits_used
                    logger.info(f"Run {run.id}: {apollo_service.credits_used} Apollo credits used")
            except Exception as e:
                logger.error(f"Gathering adapter {source_type} failed: {e}")
                run.error_message = str(e)
                run.error_count = 1

        self._advance_phase(run, "gather")  # Blacklist handled in chat flow, not pipeline phase
        return run

    def _get_adapter(self, source_type: str, apollo_service=None):
        """Get the appropriate gathering adapter."""
        if source_type == "apollo.companies.api":
            from app.services.gathering_adapters.apollo_org_api import ApolloOrgApiAdapter
            return ApolloOrgApiAdapter(apollo_service=apollo_service)
        elif source_type == "manual.companies.manual":
            from app.services.gathering_adapters.manual import ManualAdapter
            return ManualAdapter()
        elif source_type == "csv.companies.file":
            from app.services.gathering_adapters.csv_file import CSVFileAdapter
            return CSVFileAdapter()
        elif source_type == "google_sheets.companies.sheet":
            from app.services.gathering_adapters.google_sheet import GoogleSheetAdapter
            return GoogleSheetAdapter()
        elif source_type == "google_drive.companies.folder":
            from app.services.gathering_adapters.google_drive import GoogleDriveAdapter
            return GoogleDriveAdapter()
        return None

    # ── Phase 2: Blacklist Check ──

    async def blacklist_check(
        self, session: AsyncSession, run: GatheringRun
    ) -> ApprovalGate:
        """Check gathered companies against existing campaigns. Creates CP1 gate."""
        self._check_phase(run, "blacklist")

        # Get all discovered companies for this run
        result = await session.execute(
            select(DiscoveredCompany).where(
                DiscoveredCompany.project_id == run.project_id,
                DiscoveredCompany.is_blacklisted == False,
            )
        )
        companies = result.scalars().all()

        # Build blacklist domains from ALREADY BLACKLISTED companies in this project
        # (imported from SmartLead campaigns via import_smartlead_campaigns)
        blacklist_result = await session.execute(
            select(DiscoveredCompany.domain).where(
                DiscoveredCompany.project_id == run.project_id,
                DiscoveredCompany.is_blacklisted == True,
            )
        )
        blacklisted_domains = {row[0] for row in blacklist_result.all()}

        passed = 0
        rejected = 0
        for dc in companies:
            if dc.is_blacklisted:
                # Already blacklisted (e.g. imported from campaigns)
                rejected += 1
                continue
            if matches_trash_pattern(dc.domain):
                dc.is_blacklisted = True
                dc.blacklist_reason = "trash_domain"
                rejected += 1
            elif dc.domain in blacklisted_domains:
                dc.is_blacklisted = True
                dc.blacklist_reason = "existing_campaign_contact"
                rejected += 1
            else:
                passed += 1

        run.rejected_count = rejected

        # Get project info for the gate scope
        project = await session.get(Project, run.project_id)

        gate = ApprovalGate(
            project_id=run.project_id,
            gathering_run_id=run.id,
            gate_type="checkpoint_1",
            gate_label="Project scope + blacklist review",
            scope={
                "project_id": run.project_id,
                "project_name": project.name if project else "Unknown",
                "run_id": run.id,
                "source_type": run.source_type,
                "total_gathered": run.raw_results_count,
                "new_companies": run.new_companies_count,
                "passed_blacklist": passed,
                "rejected_blacklist": rejected,
            },
        )
        session.add(gate)
        self._advance_phase(run, "awaiting_scope_ok")
        await session.flush()
        return gate

    # ── Phase 3: Pre-Filter ──

    async def pre_filter(self, session: AsyncSession, run: GatheringRun) -> Dict:
        """Deterministic pre-filtering: remove companies too small, wrong industry, etc."""
        self._check_phase(run, "pre_filter")

        # Scope to THIS RUN's companies via CompanySourceLink
        result = await session.execute(
            select(DiscoveredCompany)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(
                CompanySourceLink.gathering_run_id == run.id,
                DiscoveredCompany.is_blacklisted == False,
                DiscoveredCompany.is_pre_filtered == False,
            )
        )
        companies = result.scalars().all()

        passed = 0
        filtered = 0
        for dc in companies:
            # Basic pre-filter: skip companies with no domain
            if not dc.domain or len(dc.domain) < 4:
                dc.is_pre_filtered = True
                dc.pre_filter_reason = "invalid_domain"
                filtered += 1
            else:
                passed += 1

        self._advance_phase(run, "scrape")
        return {"passed": passed, "filtered": filtered}

    # ── Phase 4: Scrape ──

    async def scrape(
        self, session: AsyncSession, run: GatheringRun,
        scraper_service=None,
    ) -> Dict:
        """Scrape websites for THIS RUN's companies only (not all project companies)."""
        self._check_phase(run, "scrape")

        # Scope to current run via CompanySourceLink join
        result = await session.execute(
            select(DiscoveredCompany)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .where(
                CompanySourceLink.gathering_run_id == run.id,
                DiscoveredCompany.is_blacklisted == False,
                DiscoveredCompany.is_pre_filtered == False,
            )
        )
        companies = result.scalars().all()

        scraped = 0
        errors = 0

        if scraper_service:
            # Concurrent scraping via existing scrape_batch (asyncio.gather + semaphore)
            # Keeps event loop responsive — other API requests won't timeout
            batch_items = []
            company_map = {}
            for dc in companies:
                url = dc.website_url or f"https://{dc.domain}"
                batch_items.append({"url": url, "row_id": dc.id})
                company_map[dc.id] = dc

            async def on_result(result):
                nonlocal scraped, errors
                dc_id = result.get("row_id")
                scrape_record = CompanyScrape(
                    discovered_company_id=dc_id,
                    url=result.get("url", ""),
                    scrape_status="success" if result.get("success") else "error",
                    clean_text=result.get("text"),
                    error_message=result.get("error"),
                    http_status_code=result.get("status_code"),
                    text_size_bytes=len(result.get("text", "")) if result.get("text") else 0,
                )
                session.add(scrape_record)
                if result.get("success"):
                    scraped += 1
                else:
                    errors += 1

            await scraper_service.scrape_batch(
                batch_items, timeout=15, max_concurrent=50,
                delay=0.02, on_result=on_result,
            )
        else:
            scraped = len(companies)

        self._advance_phase(run, "analyze")
        return {"scraped": scraped, "errors": errors, "total": len(companies)}

    # ── Phase 5: Analyze ──

    ANALYSIS_BATCH_SIZE = 200  # Process all at once — semaphore controls concurrency
    # Concurrency controlled by AdaptiveSemaphore: starts at 100, shrinks on 429, grows back

    async def analyze(
        self, session: AsyncSession, run: GatheringRun,
        prompt_text: Optional[str] = None,
        auto_refine: bool = False,
        target_accuracy: float = 0.9,
        openai_key: Optional[str] = None,
    ) -> ApprovalGate:
        """AI analysis with GPT-4o-mini — via negativa, segment labeling, batched.

        GPT does the cheap analysis ($0.003/company).
        The AGENT (Opus in Claude Code) reviews results at Checkpoint 2
        and re-analyzes if accuracy < 90%.
        """
        self._check_phase(run, "analyze")

        # Get THIS RUN's companies with scraped text (scoped via CompanySourceLink)
        from app.models.gathering import CompanyScrape
        result = await session.execute(
            select(DiscoveredCompany, CompanyScrape)
            .join(CompanySourceLink, CompanySourceLink.discovered_company_id == DiscoveredCompany.id)
            .outerjoin(CompanyScrape, (CompanyScrape.discovered_company_id == DiscoveredCompany.id) & (CompanyScrape.is_current == True))
            .where(
                CompanySourceLink.gathering_run_id == run.id,
                DiscoveredCompany.is_blacklisted == False,
                DiscoveredCompany.is_pre_filtered == False,
            )
        )
        rows = result.all()

        targets_found = 0
        total_analyzed = 0
        skipped_no_text = 0
        target_list = []
        borderline_rejections = []

        # Build ICP prompt — via negativa style
        from app.models.project import Project
        project = await session.get(Project, run.project_id)

        # ICP = segment query from the gathering filters (what user ASKED for)
        # + offer from project (what user SELLS) — both needed for correct classification
        segment_query = ""
        if run.filters:
            keywords = run.filters.get("q_organization_keyword_tags", [])
            locations = run.filters.get("organization_locations", [])
            if keywords:
                segment_query = ", ".join(keywords[:5])
            if locations:
                segment_query += f" in {', '.join(locations[:3])}"

        raw_icp = prompt_text or segment_query or (project.target_segments if project else None) or "General B2B companies"
        # If ICP contains website scrape (starts with or contains "Company website"), extract just the segment description
        if "Company website" in raw_icp and len(raw_icp) > 500:
            parts = raw_icp.split("Company website", 1)
            icp_text = parts[0].strip() or segment_query or "General B2B companies"
        else:
            icp_text = raw_icp[:2000]  # Allow long segment descriptions with exclusion rules

        # Always include segment query if available (even if offer text is the primary ICP)
        if segment_query and segment_query not in icp_text:
            icp_text = f"{segment_query} — {icp_text}"

        # Get user feedback on targets/analysis (most recent = highest priority)
        user_feedback_section = ""
        try:
            from app.models.usage import MCPUsageLog
            feedback_result = await session.execute(
                select(MCPUsageLog).where(
                    MCPUsageLog.tool_name == "user_feedback",
                    MCPUsageLog.action.in_(["targets", "analysis", "filters"]),
                    MCPUsageLog.extra_data["project_id"].as_integer() == run.project_id,
                ).order_by(MCPUsageLog.created_at.desc()).limit(5)
            )
            feedback_logs = feedback_result.scalars().all()
            if feedback_logs:
                fb_parts = [f"- {log.extra_data.get('feedback_text', '')}" for log in feedback_logs]
                user_feedback_section = "\n\nUSER FEEDBACK (HIGHEST PRIORITY — follow these over default rules):\n" + "\n".join(fb_parts)

            # Also get user overrides on specific companies (learning from corrections)
            override_result = await session.execute(
                select(DiscoveredCompany).where(
                    DiscoveredCompany.project_id == run.project_id,
                    DiscoveredCompany.analysis_reasoning.ilike("%[USER OVERRIDE]%"),
                ).limit(10)
            )
            overrides = override_result.scalars().all()
            if overrides:
                override_parts = []
                for o in overrides:
                    status = "IS a target" if o.is_target else "is NOT a target"
                    override_parts.append(f"- {o.name or o.domain} {status}: {o.analysis_reasoning[:200]}")
                user_feedback_section += "\n\nUSER CORRECTIONS (learn from these — similar companies should be treated the same way):\n" + "\n".join(override_parts)
        except Exception as e:
            logger.debug(f"Failed to load user feedback: {e}")

        # Derive target segment label from user's query/ICP
        target_segment_label = "TARGET"
        if icp_text:
            # Extract a clean segment label from the ICP text
            icp_lower = icp_text.lower()
            # Common patterns: "IT consulting" → IT_CONSULTING, "fashion brands" → FASHION_BRAND
            import re
            # Take first meaningful phrase (before "companies", "in", geo, etc.)
            match = re.match(r'^([A-Za-z\s&/]+?)(?:\s+companies|\s+firms|\s+agencies|\s+brands|\s+in\s|\s+from\s|\s+based\s|\,)', icp_lower)
            if match:
                raw = match.group(1).strip()
                target_segment_label = re.sub(r'[^a-z0-9]+', '_', raw).upper().strip('_')
            elif len(icp_text) < 50:
                target_segment_label = re.sub(r'[^a-z0-9]+', '_', icp_lower).upper().strip('_')[:30]

        # Build competitor exclusion — NARROW: only exclude companies offering the EXACT same product
        competitor_exclusion = ""
        if project and project.sender_company:
            competitor_exclusion = f"""
- Company is a DIRECT COMPETITOR to {project.sender_company} ONLY if they offer the exact same product/service category (e.g. both are payroll platforms, both are CRM tools). Companies in ADJACENT industries are POTENTIAL CUSTOMERS, not competitors. An IT consulting firm is a CUSTOMER for a payroll platform, NOT a competitor."""

        # Build clear offer description (not just company name)
        offer_description = project.sender_company or "our product"
        if project.target_segments:
            ts = project.target_segments or ""
            if "Company website" in ts:
                # Website scrape stored — extract the useful part (after the URL)
                # Format: "Company website (https://...): Company Name - Description..."
                after_url = ts.split("): ", 1)[1] if "): " in ts else ts.split("Company website", 1)[1]
                offer_description = after_url[:300].strip()
            elif len(ts) > 10:
                offer_description = ts[:300]

        via_negativa_system = f"""OUR PRODUCT: {offer_description}
TARGET SEGMENT: {icp_text}

CRITICAL RULE: Companies in the TARGET SEGMENT are our CUSTOMERS — they would BUY our product.
They are NOT our competitors. Our competitors are OTHER companies that sell the SAME product as us.

Example: If we sell payroll software, then IT consulting firms = CUSTOMERS (they buy payroll).
Only another payroll software company = COMPETITOR.

DO NOT confuse customers with competitors. The TARGET SEGMENT companies = CUSTOMERS.

Analyze the company website below using VIA NEGATIVA — focus on what RULES IT OUT.

Exclusion criteria (reject if ANY apply):
- Company is a DIRECT COMPETITOR (offers the exact same product/service as us){competitor_exclusion}
- Company is completely unrelated to the segment we're looking for
- Company is a freelancer/solo consultant (no real team)
- Website is a placeholder, parked domain, or under construction
- Company is in a completely unrelated industry
- Company has shut down or is clearly inactive
- Insufficient website data to determine what company does

If NONE of these exclusions apply → the company survives. Label it as target.
If INSUFFICIENT WEBSITE DATA to determine → reject (is_target: false, segment: INSUFFICIENT_DATA).
{user_feedback_section}

Respond ONLY with valid JSON:
{{
  "is_target": true,
  "segment": "{target_segment_label}",
  "reasoning": "1-2 sentence: what the company does and why it's target or why excluded"
}}

Rules:
- is_target: true ONLY if NO exclusion triggered AND clear evidence from website
- segment: If the company IS a target → use "{target_segment_label}" (ONE label for ALL targets, no variations, no sub-segments). If the company is NOT a target → use the company's ACTUAL business segment in CAPS_SNAKE_CASE (e.g. "OPTICAL_RETAIL", "FOOD_BEVERAGE", "LOGISTICS") — classify what the company actually IS. Use INSUFFICIENT_DATA only if website text is truly unreadable.
- Be strict. When in doubt, exclude."""

        if not openai_key:
            logger.warning("No OpenAI key — skipping GPT analysis")
            gate = self._create_checkpoint2_gate(session, run, 0, 0, 0, [], [], icp_text)
            return gate

        # Bug 14: Use gpt-4.1 to CRAFT domain-specific exclusion rules
        # Then gpt-4o-mini APPLIES them at scale. Smart model creates, cheap model executes.
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30) as _client:
                craft_resp = await _client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4.1-mini",
                        "messages": [
                            {"role": "system", "content": "You are an expert B2B sales analyst. Given a target segment description, output 3-5 SPECIFIC exclusion rules that distinguish REAL targets from common false positives. Be very specific to this exact industry."},
                            {"role": "user", "content": f"Target segment: {icp_text}\nOur company: {project.sender_company if project else 'unknown'}\n\nList 3-5 domain-specific exclusion rules. For each rule, explain WHO gets excluded and WHY they are NOT our target customer. Format: '- [rule]: [explanation]'"},
                        ],
                        "max_tokens": 300,
                        "temperature": 0.2,
                    },
                )
                craft_data = craft_resp.json()
                extract_openai_usage(craft_data, "gpt-4.1-mini", "analyze_craft_exclusion_rules")
                domain_rules = craft_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if domain_rules:
                    via_negativa_system += f"\n\nDOMAIN-SPECIFIC EXCLUSIONS (generated by expert analysis):\n{domain_rules}"
                    logger.info(f"[Analyze] Domain-specific rules crafted for '{icp_text[:50]}': {domain_rules[:200]}")
        except Exception as e:
            logger.warning(f"Domain-specific rule generation failed (continuing with generic rules): {e}")

        # Process in batches with concurrency
        import asyncio
        import httpx
        import json as _json

        from app.services.adaptive_semaphore import OPENAI_SEM
        sem = OPENAI_SEM()

        async def analyze_one(dc: DiscoveredCompany, scrape_text: Optional[str]) -> Dict:
            """Analyze a single company with GPT-4o-mini."""
            company_text = f"Company: {dc.name or dc.domain}\nDomain: {dc.domain}"
            if dc.industry:
                company_text += f"\nIndustry: {dc.industry}"
            if dc.country:
                company_text += f"\nCountry: {dc.country}"
            if dc.employee_count:
                company_text += f"\nEmployees: {dc.employee_count}"
            if scrape_text:
                company_text += f"\n\nWebsite content:\n{scrape_text[:12000]}"

            async with sem.acquire():
                try:
                    _MAX_RETRIES = 3
                    _BACKOFF = [2, 5, 10]
                    data = None
                    for _attempt in range(_MAX_RETRIES + 1):
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.post(
                                "https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                                json={
                                    "model": "gpt-4o-mini",
                                    "messages": [
                                        {"role": "system", "content": via_negativa_system},
                                        {"role": "user", "content": company_text},
                                    ],
                                    "max_tokens": 200,
                                    "temperature": 0.1,
                                },
                            )
                            if resp.status_code == 429:
                                sem.report_429()
                                if _attempt < _MAX_RETRIES:
                                    await asyncio.sleep(_BACKOFF[_attempt])
                                    continue
                                return {"domain": dc.domain, "error": "rate_limited"}
                            sem.report_ok()
                            data = resp.json()
                            break
                    if not data:
                        return {"domain": dc.domain, "error": "no_response"}
                    extract_openai_usage(data, "gpt-4o-mini", "analyze_company")
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    clean = content.strip()
                    if clean.startswith("```"):
                        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
                    parsed = _json.loads(clean)
                    return {"dc_id": dc.id, "domain": dc.domain, "name": dc.name, **parsed}
                except _json.JSONDecodeError:
                    return {"dc_id": dc.id, "domain": dc.domain, "name": dc.name,
                            "is_target": False, "confidence": 0, "segment": "PARSE_ERROR",
                            "reasoning": f"GPT response parse error: {content[:200]}"}
                except Exception as e:
                    return {"dc_id": dc.id, "domain": dc.domain, "name": dc.name,
                            "is_target": False, "confidence": 0, "segment": "ERROR",
                            "reasoning": f"Analysis error: {str(e)[:100]}"}

        # Build work items
        work_items = []
        dc_map = {}
        for dc, scrape in rows:
            scrape_text = scrape.clean_text if scrape and scrape.clean_text else None
            if not scrape_text:
                skipped_no_text += 1
                continue
            work_items.append((dc, scrape_text))
            dc_map[dc.id] = dc

        # Process in batches of 25
        for batch_start in range(0, len(work_items), self.ANALYSIS_BATCH_SIZE):
            batch = work_items[batch_start:batch_start + self.ANALYSIS_BATCH_SIZE]
            tasks = [analyze_one(dc, text) for dc, text in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    total_analyzed += 1
                    continue
                if not isinstance(r, dict):
                    total_analyzed += 1
                    continue

                dc = dc_map.get(r["dc_id"])
                if not dc:
                    continue

                dc.is_target = r.get("is_target", False)
                dc.analysis_confidence = 1.0 if dc.is_target else 0.0
                dc.analysis_segment = r.get("segment", "")
                dc.analysis_reasoning = r.get("reasoning", "")
                total_analyzed += 1

                if dc.is_target:
                    targets_found += 1
                    target_list.append({
                        "dc_id": dc.id, "domain": dc.domain, "name": dc.name,
                        "confidence": dc.analysis_confidence,
                        "segment": dc.analysis_segment,
                        "reasoning": dc.analysis_reasoning,
                    })
                elif r.get("segment") == "INSUFFICIENT_DATA":
                    borderline_rejections.append({
                        "domain": dc.domain, "name": dc.name,
                        "confidence": r.get("confidence", 0),
                        "segment": r.get("segment", ""),
                        "reasoning": r.get("reasoning", ""),
                    })

            # Flush after each batch
            await session.flush()
            logger.info(f"Analysis batch {batch_start//self.ANALYSIS_BATCH_SIZE + 1}: "
                        f"{targets_found} targets / {total_analyzed} analyzed")

        # Save analysis prompt + results for visibility (Prompts page, Learning page)
        try:
            from app.models.usage import MCPUsageLog
            prompt_log = MCPUsageLog(
                user_id=project.user_id if project else 1,  # actual user, not project_id
                tool_name="analysis_prompt",
                action="via_negativa_analysis",
                extra_data={
                    "run_id": run.id,
                    "project_id": run.project_id,
                    "target_segment": target_segment_label,
                    "prompt_text": via_negativa_system[:3000],
                    "targets_found": targets_found,
                    "total_analyzed": total_analyzed,
                    "skipped_no_text": skipped_no_text,
                    "model": "gpt-4o-mini",
                },
            )
            session.add(prompt_log)
        except Exception as e:
            logger.debug(f"Failed to save prompt log: {e}")

        # Sort target list by confidence DESC
        target_list.sort(key=lambda x: -x.get("confidence", 0))
        borderline_rejections.sort(key=lambda x: -x.get("confidence", 0))

        # Normalize company names for targets only (GPT-4o-mini, cheap)
        target_dc_ids = [t.get("dc_id") for t in target_list if t.get("dc_id")]
        if target_dc_ids and openai_key:
            target_dcs = [dc_map[dc_id] for dc_id in target_dc_ids if dc_id in dc_map]
            normalized = await self._normalize_company_names(target_dcs, openai_key)
            # Update target_list names to reflect normalization
            name_map = {dc.id: dc.name for dc in target_dcs}
            for t in target_list:
                if t.get("dc_id") in name_map:
                    t["name"] = name_map[t["dc_id"]]
            await session.flush()
            logger.info(f"Normalized {normalized} target company names")

        gate = self._create_checkpoint2_gate(
            session, run, targets_found, total_analyzed, skipped_no_text,
            target_list, borderline_rejections, icp_text,
        )

        run.target_rate = targets_found / total_analyzed if total_analyzed > 0 else 0
        run.avg_analysis_confidence = (
            sum(t["confidence"] for t in target_list) / len(target_list)
            if target_list else 0
        )
        self._advance_phase(run, "awaiting_targets_ok")
        await session.flush()
        return gate

    def _create_checkpoint2_gate(
        self, session, run, targets_found, total_analyzed, skipped_no_text,
        target_list, borderline_rejections, prompt_text,
    ) -> ApprovalGate:
        """Create Checkpoint 2 gate with full target list for agent QA."""
        avg_conf = sum(t["confidence"] for t in target_list) / len(target_list) if target_list else 0

        # Segment distribution
        segment_counts = {}
        for t in target_list:
            seg = t.get("segment", "UNKNOWN")
            segment_counts[seg] = segment_counts.get(seg, 0) + 1

        gate = ApprovalGate(
            project_id=run.project_id,
            gathering_run_id=run.id,
            gate_type="checkpoint_2",
            gate_label="Target list review",
            scope={
                "run_id": run.id,
                "targets_found": targets_found,
                "total_analyzed": total_analyzed,
                "skipped_no_scraped_text": skipped_no_text,
                "target_rate": f"{targets_found/max(total_analyzed,1)*100:.0f}%",
                "avg_confidence": round(avg_conf, 2),
                "segment_distribution": segment_counts,
                "target_list": target_list[:100],  # Top 100 targets for agent review
                "borderline_rejections": borderline_rejections[:20],  # Borderline for agent override
                "prompt_text": prompt_text[:500] if prompt_text else None,
            },
        )
        session.add(gate)
        return gate

    # ── Company Name Normalization (targets only) ──

    NORMALIZE_BATCH_SIZE = 200  # All at once — semaphore controls concurrency

    NORMALIZE_SYSTEM_PROMPT = (
        "You are a data normalization expert. Normalize the company name to its clean, "
        "canonical brand form. Strip legal suffixes (Inc, LLC, Ltd, GmbH, etc.), "
        "remove location/office tails after separators, drop parenthetical notes. "
        "Keep meaningful brand terms. Title Case output, preserve acronyms. "
        "Return ONLY the normalized name, nothing else."
    )

    async def _normalize_company_names(
        self, companies: List[DiscoveredCompany], openai_key: str
    ) -> int:
        """Normalize company names via GPT-4o-mini. Only called for confirmed targets.

        Updates dc.name in-place (original preserved in source_data).
        Returns count of names actually changed.
        """
        import asyncio
        import httpx

        from app.services.adaptive_semaphore import OPENAI_SEM
        semaphore = OPENAI_SEM().acquire()  # Adaptive: starts 100, shrinks on 429
        changed = 0

        async def normalize_one(dc: DiscoveredCompany) -> Optional[str]:
            if not dc.name or len(dc.name) < 3:
                return None
            async with semaphore:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [
                                    {"role": "system", "content": self.NORMALIZE_SYSTEM_PROMPT},
                                    {"role": "user", "content": dc.name},
                                ],
                                "max_tokens": 60,
                                "temperature": 0,
                            },
                        )
                        data = resp.json()
                        extract_openai_usage(data, "gpt-4o-mini", "normalize_company_name")
                        normalized = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        if normalized and normalized != dc.name:
                            return normalized
                except Exception as e:
                    logger.debug(f"Name normalization failed for {dc.domain}: {e}")
            return None

        for batch_start in range(0, len(companies), self.NORMALIZE_BATCH_SIZE):
            batch = companies[batch_start:batch_start + self.NORMALIZE_BATCH_SIZE]
            tasks = [normalize_one(dc) for dc in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for dc, result in zip(batch, results):
                if isinstance(result, str) and result and result != dc.name:
                    # Preserve original name in source_data
                    sd = dc.source_data or {}
                    if not sd.get("source_company_name"):
                        sd["source_company_name"] = dc.name
                        dc.source_data = sd
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(dc, "source_data")
                    dc.name = result
                    changed += 1

        return changed

    # ── Multi-Step Analysis (Custom Prompt Chains) ──

    async def analyze_multi_step(
        self, session: AsyncSession, run: GatheringRun,
        prompt_steps: List[Dict[str, Any]],
        openai_key: Optional[str] = None,
    ) -> ApprovalGate:
        """Run multi-step classification prompt chain.

        Each step:
        1. 'classify' steps run GPT on remaining companies, storing results
        2. 'filter' steps remove companies that don't match the condition

        Step results stored in source_data.custom_analysis[step_name].
        Final step's output becomes is_target/analysis_segment.
        """
        self._check_phase(run, "analyze")

        if not openai_key:
            raise ValueError("OpenAI key required for analysis")

        from app.models.gathering import CompanyScrape
        import asyncio
        import httpx
        import json as _json

        # Get all companies with scraped text
        result = await session.execute(
            select(DiscoveredCompany, CompanyScrape)
            .outerjoin(CompanyScrape, (CompanyScrape.discovered_company_id == DiscoveredCompany.id) & (CompanyScrape.is_current == True))
            .where(
                DiscoveredCompany.project_id == run.project_id,
                DiscoveredCompany.is_blacklisted == False,
                DiscoveredCompany.is_pre_filtered == False,
            )
        )
        rows = result.all()

        # Build working set
        working_set = {}  # dc_id -> (dc, scrape_text, step_results)
        for dc, scrape in rows:
            scrape_text = scrape.clean_text if scrape and scrape.clean_text else None
            if scrape_text:
                working_set[dc.id] = (dc, scrape_text, {})

        from app.services.adaptive_semaphore import OPENAI_SEM
        semaphore = OPENAI_SEM().acquire()  # Adaptive: starts 100, shrinks on 429

        for step_idx, step in enumerate(prompt_steps):
            step_name = step.get("name", f"step_{step_idx}")
            step_type = step.get("type", "classify")
            step_prompt = step.get("prompt", "")
            output_col = step.get("output_column", step_name)

            if step_type == "filter":
                # Filter step: remove companies based on previous step results
                condition = step.get("filter_condition") or step.get("prompt", "")
                remove_ids = []
                for dc_id, (dc, text, results) in working_set.items():
                    if not self._evaluate_filter(results, condition):
                        remove_ids.append(dc_id)
                for dc_id in remove_ids:
                    del working_set[dc_id]
                logger.info(f"Step '{step_name}': filtered {len(remove_ids)}, {len(working_set)} remaining")
                continue

            # Classify step: run GPT on remaining companies
            async def classify_one(dc_id, dc, text, step_prompt=step_prompt):
                company_context = f"Company: {dc.name or dc.domain}\nDomain: {dc.domain}"
                if dc.industry:
                    company_context += f"\nIndustry: {dc.industry}"
                if dc.country:
                    company_context += f"\nCountry: {dc.country}"
                if dc.employee_count:
                    company_context += f"\nEmployees: {dc.employee_count}"
                company_context += f"\n\nWebsite content:\n{text[:12000]}"

                async with semaphore:
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.post(
                                "https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                                json={
                                    "model": "gpt-4o-mini",
                                    "messages": [
                                        {"role": "system", "content": step_prompt},
                                        {"role": "user", "content": company_context},
                                    ],
                                    "max_tokens": 300,
                                    "temperature": 0.1,
                                },
                            )
                            data = resp.json()
                            extract_openai_usage(data, "gpt-4o-mini", "multi_step_analyze")
                            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                            return dc_id, content.strip()
                    except Exception as e:
                        return dc_id, f"ERROR: {str(e)[:100]}"

            # Process in batches
            items = [(dc_id, dc, text) for dc_id, (dc, text, _) in working_set.items()]
            for batch_start in range(0, len(items), self.ANALYSIS_BATCH_SIZE):
                batch = items[batch_start:batch_start + self.ANALYSIS_BATCH_SIZE]
                tasks = [classify_one(dc_id, dc, text) for dc_id, dc, text in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, Exception):
                        continue
                    dc_id, output = r
                    if dc_id in working_set:
                        working_set[dc_id][2][output_col] = output

            logger.info(f"Step '{step_name}': classified {len(working_set)} companies")
            await session.flush()

        # Final: set is_target based on last classify step's results
        targets_found = 0
        total_analyzed = len(working_set)
        target_list = []
        borderline_rejections = []

        for dc_id, (dc, text, step_results) in working_set.items():
            # Store step results in source_data
            sd = dc.source_data or {}
            sd["custom_analysis"] = step_results
            dc.source_data = sd
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(dc, "source_data")

            # Parse last step's output to determine target status
            last_output = list(step_results.values())[-1] if step_results else ""
            is_target, confidence, segment, reasoning = self._parse_step_output(last_output)

            dc.is_target = is_target
            dc.analysis_confidence = confidence
            dc.analysis_segment = segment
            dc.analysis_reasoning = reasoning

            if is_target:
                targets_found += 1
                target_list.append({
                    "dc_id": dc.id, "domain": dc.domain, "name": dc.name,
                    "confidence": confidence, "segment": segment,
                    "reasoning": reasoning, "step_results": step_results,
                })
            elif 0.4 <= confidence <= 0.6:
                borderline_rejections.append({
                    "domain": dc.domain, "name": dc.name,
                    "confidence": confidence, "segment": segment, "reasoning": reasoning,
                })

        target_list.sort(key=lambda x: -x.get("confidence", 0))
        borderline_rejections.sort(key=lambda x: -x.get("confidence", 0))

        gate = self._create_checkpoint2_gate(
            session, run, targets_found, total_analyzed,
            len(rows) - total_analyzed,  # skipped
            target_list, borderline_rejections,
            "\n---\n".join(s.get("prompt", "")[:200] for s in prompt_steps),
        )

        run.target_rate = targets_found / total_analyzed if total_analyzed > 0 else 0
        run.avg_analysis_confidence = (
            sum(t["confidence"] for t in target_list) / len(target_list)
            if target_list else 0
        )
        self._advance_phase(run, "awaiting_targets_ok")
        await session.flush()
        return gate

    def _evaluate_filter(self, step_results: Dict, condition: str) -> bool:
        """Evaluate a filter condition against step results.

        Simple conditions:
        - "segment != OTHER" → check last result doesn't contain "OTHER"
        - "NOT_VALID" → check last result doesn't contain "NOT_VALID"
        """
        if not step_results:
            return False
        last_value = list(step_results.values())[-1]
        if not last_value:
            return False

        # Simple negation check
        condition = condition.strip()
        if condition.startswith("!") or "NOT_VALID" in condition or "!=" in condition:
            # Remove from set if contains NOT_VALID or OTHER
            reject_terms = ["NOT_VALID", "OTHER", "NOT_A_MATCH"]
            return not any(term in last_value.upper() for term in reject_terms)

        # Positive match
        return condition.upper() in last_value.upper()

    def _parse_step_output(self, output: str) -> tuple:
        """Parse GPT output from a classification step.

        Handles both JSON and text output formats:
        - JSON: {"is_target": true, "confidence": 0.85, "segment": "X", "reasoning": "..."}
        - Text: "VALID" / "NOT_VALID" / "Classification: VALID\nAnalysis: ..."
        """
        import json as _json

        if not output:
            return False, 0.0, "NO_OUTPUT", "No output from classification step"

        # Try JSON parse
        try:
            clean = output.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
            parsed = _json.loads(clean)
            return (
                parsed.get("is_target", False),
                parsed.get("confidence", 0.5),
                parsed.get("segment", "UNKNOWN"),
                parsed.get("reasoning", ""),
            )
        except (_json.JSONDecodeError, ValueError):
            pass

        # Text format: check for VALID/NOT_VALID pattern
        upper = output.upper()
        if "NOT_VALID" in upper or "NOT_A_MATCH" in upper or "OTHER" in upper:
            return False, 0.3, "NOT_A_MATCH", output[:200]
        if "VALID" in upper or "TARGET" in upper:
            return True, 0.7, "TARGET", output[:200]

        # Ambiguous
        return False, 0.4, "UNCLEAR", output[:200]

    # ── Gate Management ──

    async def approve_gate(
        self, session: AsyncSession, gate: ApprovalGate,
        decided_by: str, note: Optional[str] = None,
    ) -> None:
        """Approve a checkpoint gate and advance the run."""
        gate.status = "approved"
        gate.decided_by = decided_by
        gate.decided_at = datetime.utcnow()
        gate.decision_note = note

        if gate.gathering_run_id:
            run = await session.get(GatheringRun, gate.gathering_run_id)
            if run:
                phase_map = {
                    "awaiting_scope_ok": "pre_filter",
                    "awaiting_targets_ok": "prepare_verification",
                    "awaiting_verify_ok": "verified",
                }
                next_phase = phase_map.get(run.current_phase)
                if next_phase:
                    self._advance_phase(run, next_phase)

    async def reject_gate(
        self, session: AsyncSession, gate: ApprovalGate,
        decided_by: str, note: Optional[str] = None,
    ) -> None:
        """Reject a checkpoint gate and cancel the run."""
        gate.status = "rejected"
        gate.decided_by = decided_by
        gate.decided_at = datetime.utcnow()
        gate.decision_note = note

        if gate.gathering_run_id:
            run = await session.get(GatheringRun, gate.gathering_run_id)
            if run:
                run.status = "cancelled"
                run.completed_at = datetime.utcnow()

    # ── Queries ──

    async def get_runs(
        self, session: AsyncSession, project_id: int
    ) -> List[GatheringRun]:
        result = await session.execute(
            select(GatheringRun)
            .where(GatheringRun.project_id == project_id)
            .order_by(GatheringRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending_gates(
        self, session: AsyncSession, project_id: int
    ) -> List[ApprovalGate]:
        result = await session.execute(
            select(ApprovalGate).where(
                ApprovalGate.project_id == project_id,
                ApprovalGate.status == "pending",
            )
        )
        return list(result.scalars().all())

    async def list_prompts(
        self, session: AsyncSession, company_id: int,
        project_id: Optional[int] = None,
    ) -> List[GatheringPrompt]:
        query = select(GatheringPrompt).where(
            GatheringPrompt.company_id == company_id,
            GatheringPrompt.is_active == True,
        )
        if project_id:
            query = query.where(GatheringPrompt.project_id == project_id)
        result = await session.execute(query.order_by(GatheringPrompt.created_at.desc()))
        return list(result.scalars().all())
