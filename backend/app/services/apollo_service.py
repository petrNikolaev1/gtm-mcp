"""Apollo Service — adapted for MCP with per-user API keys.

Official Apollo API credit costs (from Apollo pricing page):
  /mixed_people/api_search     — FREE (partial profile, max 100/page)
  /mixed_companies/search  — 1 credit / page returned (max 100/page)
  /people/match                — 1 credit / net-new email, 1 / firmographic, 5 / phone
  /people/bulk_match           — same as /people/match per result
  /organizations/enrich        — 1 credit / result returned
  /organizations/bulk_enrich   — 1 credit / company returned (max 10/page)
  /organizations/{id}/job_postings — 1 credit / result (max 10,000/page)
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import text as sa_text

from app.services.cost_tracker import get_tracker

logger = logging.getLogger(__name__)


class ApolloService:
    RATE_LIMIT_INTERVAL = 0.3

    def __init__(self, api_key: Optional[str] = None):
        self.base_url = "https://api.apollo.io/api/v1"
        self._api_key = api_key
        self.credits_used: int = 0
        self._last_call_time: float = 0

    @property
    def api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        from app.config import settings
        return settings.APOLLO_API_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Cache-Control": "no-cache"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    async def _rate_limit(self):
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self.RATE_LIMIT_INTERVAL:
            await asyncio.sleep(self.RATE_LIMIT_INTERVAL - elapsed)
        self._last_call_time = time.monotonic()

    async def _api_call(self, method: str, endpoint: str, json_data: dict = None, skip_rate_limit: bool = False) -> Optional[dict]:
        MAX_RETRIES = 3
        backoff_waits = [30, 60, 120]
        from app.services.adaptive_semaphore import APOLLO_SEM
        sem = APOLLO_SEM()

        for attempt in range(MAX_RETRIES + 1):
            if not skip_rate_limit:
                await self._rate_limit()
            try:
                async with sem.acquire():
                    async with httpx.AsyncClient(timeout=30) as client:
                        if method == "POST":
                            resp = await client.post(f"{self.base_url}{endpoint}", json=json_data, headers=self._get_headers())
                        else:
                            resp = await client.get(f"{self.base_url}{endpoint}", headers=self._get_headers())
                        if resp.status_code == 429:
                            sem.report_429()
                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(backoff_waits[attempt])
                                self._last_call_time = time.monotonic()
                                continue
                            return None
                        sem.report_ok()
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Apollo API {endpoint}: {e.response.status_code}")
                return None
            except Exception as e:
                logger.error(f"Apollo API {endpoint} failed: {e}")
                return None
        return None

    async def search_organizations(
        self, keyword_tags: Optional[List[str]] = None,
        industry_tag_ids: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        num_employees_ranges: Optional[List[str]] = None,
        latest_funding_stages: Optional[List[str]] = None,
        q_organization_name: Optional[str] = None,
        page: int = 1, per_page: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Search Apollo companies.

        Best filter priority:
        1. industry_tag_ids (from enrichment) → organization_industry_tag_ids → BEST pagination
        2. keyword_tags → q_organization_keyword_tags → inconsistent pagination
        3. q_organization_name → text search → OK pagination
        """
        if not self.api_key:
            return None
        payload: Dict[str, Any] = {
            "page": page, "per_page": min(per_page, 100),
        }
        # Prefer industry_tag_ids (real Apollo IDs from enrichment — best pagination)
        # CRITICAL: do NOT combine with keyword_tags — Apollo filters are AND across types,
        # combining NARROWS results and breaks pagination. Use one or the other.
        if industry_tag_ids:
            payload["organization_industry_tag_ids"] = industry_tag_ids
            # Do NOT add keyword_tags when we have industry_tag_ids
        elif keyword_tags:
            payload["q_organization_keyword_tags"] = keyword_tags
        # Text search by company name
        if q_organization_name:
            payload["q_organization_name"] = q_organization_name
        if locations:
            payload["organization_locations"] = locations
        if num_employees_ranges:
            payload["organization_num_employees_ranges"] = num_employees_ranges
        if latest_funding_stages:
            payload["organization_latest_funding_stage_cd"] = latest_funding_stages
        result = await self._api_call("POST", "/mixed_companies/search", payload)
        if result:
            self.credits_used += 1
            get_tracker().log_apollo(1, "search")
        return result

    async def search_organizations_all_pages(
        self, keyword_tags: Optional[List[str]] = None,
        industry_tag_ids: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        num_employees_ranges: Optional[List[str]] = None,
        latest_funding_stages: Optional[List[str]] = None,
        q_organization_name: Optional[str] = None,
        max_pages: int = 50, per_page: int = 100,
        batch_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple pages in parallel batches of batch_size.
        Prefers industry_tag_ids (from enrichment) for best pagination."""
        all_orgs: List[Dict[str, Any]] = []
        total_pages_apollo = max_pages

        for batch_start in range(1, max_pages + 1, batch_size):
            batch_end = min(batch_start + batch_size, max_pages + 1)
            pages_to_fetch = list(range(batch_start, batch_end))

            async def fetch_page(page):
                return page, await self.search_organizations(
                    keyword_tags=keyword_tags, industry_tag_ids=industry_tag_ids,
                    locations=locations, num_employees_ranges=num_employees_ranges,
                    latest_funding_stages=latest_funding_stages,
                    q_organization_name=q_organization_name,
                    page=page, per_page=per_page,
                )

            results = await asyncio.gather(*[fetch_page(p) for p in pages_to_fetch])

            batch_orgs = 0
            stop = False
            for page, data in sorted(results, key=lambda x: x[0]):
                if not data:
                    stop = True
                    break
                orgs = data.get("organizations", []) or data.get("accounts", [])
                all_orgs.extend(orgs)
                batch_orgs += len(orgs)
                pagination = data.get("pagination", {})
                total_pages_apollo = pagination.get("total_pages", total_pages_apollo)
                if page >= total_pages_apollo:
                    stop = True
                    break

            logger.info(f"Apollo batch {batch_start}-{batch_end-1}: {batch_orgs} orgs (total: {len(all_orgs)}, apollo_pages: {total_pages_apollo})")

            if stop:
                break

        return all_orgs

    async def enrich_by_domain(
        self, domain: str, limit: int = 3,
        titles: Optional[List[str]] = None,
        seniorities: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Optimized people extraction: 2 FREE searches → prioritize → enrich only top N.

        Step 1: Two parallel FREE searches (/mixed_people/api_search):
          A) By seniorities (owner, founder, c_suite, vp, head, director) — gets C-level
          B) By no filter (per_page=20) — gets everyone for coverage
        Step 2: Merge + dedup by person_id
        Step 3: Filter has_email=true only
        Step 4: Prioritize by title match to target_roles
        Step 5: Take top `limit` (default 3)
        Step 6: bulk_match ONLY those (limit credits, not 20+)

        Cost: `limit` credits per company (typically 3).
        """
        if not self.api_key:
            return []

        default_seniorities = seniorities or ["owner", "founder", "c_suite", "vp", "head", "director"]

        # Step 1: Single FREE search by seniority (returns 3-25 C-level/director/VP people)
        # Tested: seniority search gives best results. Title search adds ~0-1 extra.
        # Combined seniority+titles = AND (kills results). Don't combine.
        search_payload = {
            "q_organization_domains": domain, "page": 1, "per_page": 25,
            "person_seniorities": default_seniorities,
        }
        search_data = await self._api_call("POST", "/mixed_people/api_search", search_payload, skip_rate_limit=True)  # FREE endpoint, no rate limit needed

        all_people = search_data.get("people", []) if search_data else []

        # Step 2: Filter has_email=true only
        with_email = [p for p in all_people if p.get("has_email")]

        logger.info(f"People search {domain}: {len(all_people)} total, {len(with_email)} with email")

        if not with_email:
            return []

        # Step 3: GPT-powered role selection — dynamic, no hardcoding.
        # Send candidate list + target roles to GPT → returns ranked indices.
        # Works for ANY industry — fintech, fashion, SaaS, whatever the document says.

        if titles and len(with_email) > 0:
            gpt_selected = await self._gpt_rank_candidates(with_email, titles, domain)
            # GPT-selected first, then fill remaining slots from all candidates by seniority
            # This ensures avg 2.5+ people per company while prioritizing target roles
            seen_ids = {p.get("id") for p in gpt_selected}
            fallback = [p for p in with_email if p.get("id") not in seen_ids]
            prioritized = gpt_selected + fallback  # Target matches first, then seniority fallback
        else:
            prioritized = with_email

        # Step 4-6: Enrich in rounds until `limit` verified emails or candidates exhausted
        enriched_all = []
        tried_ids = set()
        remaining = list(prioritized)  # Full ranked candidate list
        max_rounds = 3  # Max retry rounds to avoid excessive credit spend

        for round_num in range(max_rounds):
            if len(enriched_all) >= limit:
                break

            # Pick next batch of candidates (skip already tried)
            batch = []
            for p in remaining:
                if p.get("id") and p["id"] not in tried_ids:
                    # Only enrich people matching target roles (prioritized list already ranked)
                    batch.append(p)
                    tried_ids.add(p["id"])
                    if len(batch) >= limit - len(enriched_all):
                        break

            if not batch:
                break  # No more candidates

            people_ids = [p["id"] for p in batch]
            enriched = await self.enrich_people_emails(people_ids)
            enriched_all.extend(enriched)

            if round_num > 0:
                logger.info(f"People retry round {round_num + 1} for {domain}: "
                           f"+{len(enriched)} verified (total: {len(enriched_all)}/{limit})")

        logger.info(f"People enrichment {domain}: {len(enriched_all)} verified emails "
                    f"(from {len(with_email)} candidates, {len(tried_ids)} enriched)")
        return enriched_all[:limit]

    async def enrich_people_emails(self, people_ids: List[str]) -> List[Dict[str, Any]]:
        """Enrich people with emails via bulk_match (1 credit per person)."""
        logger.info(f"bulk_match: enriching {len(people_ids)} people IDs")
        if not self.api_key or not people_ids:
            return []
        details = [{"id": pid} for pid in people_ids]
        bulk_data = await self._api_call("POST", "/people/bulk_match", {"details": details, "reveal_personal_emails": True})
        if not bulk_data:
            return []
        matches = bulk_data.get("matches", [])
        matched_count = sum(1 for m in matches if m)
        self.credits_used += matched_count
        get_tracker().log_apollo(matched_count, "bulk_match")
        results = []
        for match in matches:
            if not match:
                continue
            # ONLY return verified emails — spec: "Only verified emails kept"
            if match.get("email_status") != "verified":
                continue
            phone = None
            if match.get("phone_numbers"):
                phone = match["phone_numbers"][0].get("sanitized_number")
            results.append({
                "email": match.get("email"),
                "first_name": match.get("first_name"),
                "last_name": match.get("last_name"),
                "title": match.get("title"),
                "linkedin_url": match.get("linkedin_url"),
                "is_verified": True,
                "phone": phone,
            })
        return results

    async def _gpt_rank_candidates(self, candidates: list, target_titles: list, domain: str) -> list:
        """Use GPT to select which candidates match target roles. Dynamic — no hardcoding.
        Returns candidates sorted by relevance (best matches first, non-matches removed).
        """
        import httpx, json

        # Build candidate list for GPT
        cand_lines = []
        for i, p in enumerate(candidates):
            title = p.get("title", "Unknown")
            name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            cand_lines.append(f"{i}: {name} — {title}")

        # Expand CRO to be unambiguous
        expanded_titles = []
        for t in target_titles:
            if t.upper() == "CRO":
                expanded_titles.append("CRO (Chief Revenue Officer — NOT Chief Risk Officer)")
            else:
                expanded_titles.append(t)

        prompt = (
            f"Select up to 3 people to contact for an outreach campaign.\n"
            f"TARGET ROLES (select people matching these): {', '.join(expanded_titles)}\n\n"
            f"CANDIDATES at {domain}:\n" + "\n".join(cand_lines) + "\n\n"
            f"SELECTION RULES:\n"
            f"1. Select people whose function matches a TARGET ROLE above (highest priority)\n"
            f"2. For compound titles ('X & Y'), the Y part is the function — select only if Y matches a target role\n"
            f"3. 'Chief Risk Officer' ≠ 'Chief Revenue Officer' — only match CRO if it means Revenue\n"
            f"4. Skip generic titles without a department ('Director', 'VP' alone)\n"
            f"5. If fewer than 3 match exactly, also select CEO/Founder/President as universal contacts\n"
            f"6. If still fewer than 3, prefer senior business-facing roles over technical/operational ones\n\n"
            f"Return JSON: {{\"selected\": [0, 3, 5]}} — up to 3 indices, best first."
        )

        try:
            # Use the OpenAI key from wherever it was configured
            openai_key = getattr(self, '_openai_key', None)
            if not openai_key:
                # Try to get from environment
                import os
                openai_key = os.environ.get("OPENAI_API_KEY", "")

            if not openai_key:
                # No key — fall back to simple title matching
                return candidates

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 100, "temperature": 0,
                    },
                )
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(content)
                selected_indices = parsed.get("selected", [])

                if not selected_indices:
                    return candidates  # GPT found no matches — return all

                # Return selected candidates in GPT's priority order
                result = []
                for idx in selected_indices:
                    if 0 <= idx < len(candidates):
                        result.append(candidates[idx])

                logger.info(f"GPT role filter {domain}: {len(result)}/{len(candidates)} selected "
                           f"for roles {target_titles[:3]}")
                return result if result else candidates

        except Exception as e:
            logger.warning(f"GPT role ranking failed for {domain}: {e}")
            return candidates  # Fallback: return all

    async def enrich_organization(self, domain: str) -> Optional[Dict[str, Any]]:
        """Get FULL company data by domain. Delegates to bulk_enrich for efficiency."""
        results = await self.bulk_enrich_organizations([domain])
        return results[0] if results else None

    async def bulk_enrich_organizations(self, domains: List[str]) -> List[Dict[str, Any]]:
        """Bulk enrich companies by domain — returns full Apollo labels.
        Max 10 per call, 1 credit per company returned. Auto-extends industry map."""
        if not self.api_key or not domains:
            return []
        results = []
        for i in range(0, len(domains), 10):
            batch = domains[i:i+10]
            data = await self._api_call("POST", "/organizations/bulk_enrich", {"domains": batch})
            if data and data.get("organizations"):
                orgs = data["organizations"]
                self.credits_used += len(orgs)
                get_tracker().log_apollo(len(orgs), "bulk_enrich")
                results.extend(orgs)
                for org in orgs:
                    await self._extend_industry_map(org)
        return results

    async def _extend_industry_map(self, org: Dict):
        """Auto-extend apollo_industry_map table with any new industry discovered."""
        tag_id = org.get("industry_tag_id")
        industry = org.get("industry")
        domain = org.get("primary_domain") or ""
        if not tag_id or not industry:
            return
        try:
            from app.db import async_session_maker
            async with async_session_maker() as session:
                await session.execute(
                    sa_text(
                        "INSERT INTO apollo_industry_map (tag_id, industry_name, sample_domain) "
                        "VALUES (:tid, :name, :domain) "
                        "ON CONFLICT (tag_id) DO UPDATE SET updated_at = now()"
                    ),
                    {"tid": tag_id, "name": industry, "domain": domain},
                )
                await session.commit()
        except Exception as e:
            logger.debug(f"Industry map extend failed: {e}")

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        data = await self._api_call("POST", "/mixed_people/api_search", {"q_organization_domains": "apollo.io", "per_page": 1})
        return data is not None

    async def get_account_info(self) -> dict:
        """Get account info including plan type and credit limit."""
        if not self.api_key:
            return {}
        data = await self._api_call("GET", "/auth/check")
        if not data:
            return {}
        bd = data.get("bootstrapped_data", {})
        return {
            "is_core": bd.get("is_core", False),
            "free_lead_credit_limit": bd.get("free_lead_credit_limit", 0),
            "team_id": bd.get("current_team_id"),
        }

    def reset_credits(self):
        self.credits_used = 0


# Module-level singleton for backward compat with older imports
apollo_service = ApolloService()
