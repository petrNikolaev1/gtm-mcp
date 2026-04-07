"""Apollo.io API tools — company search, people search, enrichment, taxonomy.

Raw API responses saved to ~/.gtm-mcp/debug_apollo_*.json for debugging.

Credit costs:
  /mixed_companies/search    — 1 credit per page (max 100/page)
  /mixed_people/api_search   — FREE (partial profile, max 25/page)
  /people/bulk_match         — 1 credit per verified email
  /organizations/bulk_enrich — 1 credit per company returned (max 10/call)

CRITICAL FILTER RULES:
  - organization_industry_tag_ids and q_organization_keyword_tags CANNOT be combined.
    Apollo ANDs across filter types — combining narrows results and breaks pagination.
    Use ONE or the OTHER per request. Run parallel streams for both.
  - 1 keyword per request, 1 industry_tag_id per request for maximum coverage.
  - Locations + employee_ranges + funding_stages CAN be combined with either.
"""
import asyncio
import json
import json as _json  # alias for raw debug saves
import logging
import time
from pathlib import Path
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apollo.io/api/v1"
RATE_LIMIT_INTERVAL = 0.3
MAX_RETRIES = 3
BACKOFF_WAITS = [30, 60, 120]

_last_call_time = 0.0
_tags_path = Path(__file__).parent.parent / "reference" / "industry_tags.json"
_taxonomy_path = Path(__file__).parent.parent / "reference" / "apollo_taxonomy.json"
_cache_path = Path(__file__).parent.parent / "reference" / "apollo_taxonomy_cache.json"


async def _rate_limit():
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if elapsed < RATE_LIMIT_INTERVAL:
        await asyncio.sleep(RATE_LIMIT_INTERVAL - elapsed)
    _last_call_time = time.monotonic()


async def _api_call(
    api_key: str, method: str, endpoint: str,
    payload: dict | None = None, skip_rate_limit: bool = False,
) -> dict | None:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json", "Cache-Control": "no-cache"}

    for attempt in range(MAX_RETRIES + 1):
        if not skip_rate_limit:
            await _rate_limit()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "POST":
                    resp = await client.post(f"{BASE_URL}{endpoint}", json=payload, headers=headers)
                else:
                    resp = await client.get(f"{BASE_URL}{endpoint}", headers=headers)

                if resp.status_code == 429:
                    if attempt < MAX_RETRIES:
                        wait = BACKOFF_WAITS[attempt]
                        logger.warning(f"Apollo 429 on {endpoint}, retry {attempt+1} in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    return None

                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Apollo {endpoint}: HTTP {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Apollo {endpoint}: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_WAITS[min(attempt, len(BACKOFF_WAITS) - 1)])
                continue
            return None
    return None


async def apollo_search_companies(
    api_key: str, filters: dict, page: int = 1, per_page: int = 100,
) -> dict:
    """Search Apollo for companies. 1 credit per page.

    ENFORCED: Cannot combine q_organization_keyword_tags with organization_industry_tag_ids.
    Apollo ANDs them — kills results. Use separate calls (parallel streams).

    Pass EITHER keywords OR industry_tag_ids, plus optional: locations, employee_ranges, funding.
    """
    has_keywords = bool(filters.get("q_organization_keyword_tags"))
    has_tags = bool(filters.get("organization_industry_tag_ids"))

    if has_keywords and has_tags:
        return {
            "success": False,
            "error": "CANNOT combine q_organization_keyword_tags with organization_industry_tag_ids in same request. "
                     "Apollo ANDs them — destroys results. Make SEPARATE calls: "
                     "one with keywords only, one with industry_tag_ids only. Run in parallel.",
        }

    payload: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}

    if has_tags:
        payload["organization_industry_tag_ids"] = filters["organization_industry_tag_ids"]
    elif has_keywords:
        payload["q_organization_keyword_tags"] = filters["q_organization_keyword_tags"]

    if filters.get("organization_locations"):
        payload["organization_locations"] = filters["organization_locations"]
    if filters.get("organization_num_employees_ranges"):
        payload["organization_num_employees_ranges"] = filters["organization_num_employees_ranges"]
    if filters.get("organization_latest_funding_stage_cd"):
        payload["organization_latest_funding_stage_cd"] = filters["organization_latest_funding_stage_cd"]
    if filters.get("q_organization_name"):
        payload["q_organization_name"] = filters["q_organization_name"]

    data = await _api_call(api_key, "POST", "/mixed_companies/search", payload)
    if not data:
        return {"success": False, "error": "Apollo API call failed"}

    # Save raw response for debugging (last response only, overwritten each call)
    try:
        raw_path = Path.home() / ".gtm-mcp" / "debug_apollo_company_search.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(_json.dumps(data, indent=2, default=str)[:500_000])
    except Exception:
        pass

    companies = []
    for org in data.get("organizations") or data.get("accounts") or []:
        companies.append({
            "domain": (org.get("primary_domain") or org.get("domain") or "").lower().strip(),
            "name": org.get("name", ""),
            "industry": org.get("industry", ""),
            "industry_tag_id": org.get("industry_tag_id", ""),
            "employee_count": org.get("estimated_num_employees"),
            "employee_range": org.get("employee_range", ""),
            # Company search uses BOTH field name patterns
            "country": org.get("country") or org.get("organization_country", ""),
            "city": org.get("city") or org.get("organization_city", ""),
            "state": org.get("state") or org.get("organization_state", ""),
            "linkedin_url": org.get("linkedin_url", ""),
            "website_url": org.get("website_url", ""),
            "founded_year": org.get("founded_year"),
            "funding_stage": org.get("latest_funding_stage", ""),
            "funding_amount": org.get("latest_funding_amount"),
            "short_description": org.get("short_description", ""),
            "keywords": org.get("keywords") or org.get("keyword_tags") or [],
            "apollo_id": org.get("id", ""),
            "phone": org.get("phone") or (org.get("primary_phone") or {}).get("number"),
            "revenue": org.get("organization_revenue") or org.get("estimated_annual_revenue"),
            "revenue_printed": org.get("organization_revenue_printed", ""),
            "market_cap": org.get("market_cap", ""),
            "sic_codes": org.get("sic_codes"),
            "naics_codes": org.get("naics_codes"),
            "headcount_6m_growth": org.get("organization_headcount_six_month_growth"),
            "headcount_12m_growth": org.get("organization_headcount_twelve_month_growth"),
            "languages": org.get("languages"),
            "street_address": org.get("street_address", ""),
            "postal_code": org.get("postal_code", ""),
            "publicly_traded_symbol": org.get("publicly_traded_symbol", ""),
        })

    pagination = data.get("pagination", {})
    return {
        "success": True,
        "companies": companies,
        "total_entries": pagination.get("total_entries", 0),
        "total_pages": pagination.get("total_pages", 0),
        "page": page,
        "per_page": per_page,
        "credits_used": 1,
    }


async def apollo_search_people(
    api_key: str,
    domain: str,
    person_seniorities: list[str] | None = None,
    per_page: int = 25,
    enrich: bool = False,
    max_enrich: int = 3,
) -> dict:
    """Search Apollo for people at a company. FREE — no credits.

    Returns candidates with person IDs for enrichment via apollo_enrich_people.
    Filters has_email=true only. Default seniorities: owner→founder→c_suite→vp→head→director.

    If enrich=True: auto-enriches top max_enrich candidates (1 credit per verified email).
    Combines search + enrich in one call — saves a round trip.
    """
    seniorities = person_seniorities or ["owner", "founder", "c_suite", "vp", "head", "director"]

    payload = {
        "q_organization_domains": domain,
        "page": 1,
        "per_page": min(per_page, 25),
        "person_seniorities": seniorities,
    }

    data = await _api_call(api_key, "POST", "/mixed_people/api_search", payload, skip_rate_limit=True)
    if not data:
        return {"success": False, "error": "Apollo people search failed"}

    people = []
    for p in data.get("people", []):
        if not p.get("has_email"):
            continue
        people.append({
            "id": p.get("id", ""),
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "title": p.get("title", ""),
            "seniority": p.get("seniority", ""),
            "has_email": True,
            "linkedin_url": p.get("linkedin_url", ""),
            "organization_name": (p.get("organization") or {}).get("name", ""),
        })

    result = {
        "success": True,
        "people": people,
        "total": data.get("pagination", {}).get("total_entries", 0),
        "domain": domain,
        "credits_used": 0,
    }

    if enrich and people:
        top_ids = [p["id"] for p in people[:max_enrich] if p.get("id")]
        if top_ids:
            enriched = await apollo_enrich_people(api_key, top_ids)
            result["enriched"] = enriched.get("matches", [])
            result["credits_used"] = enriched.get("credits_used", 0)
            result["new_industry_tags_discovered"] = enriched.get("new_industry_tags_discovered")

    return result


async def apollo_search_people_batch(
    api_key: str,
    domains: list[str],
    person_seniorities: list[str] | None = None,
    per_page: int = 10,
    enrich: bool = False,
    max_enrich: int = 3,
    max_concurrent: int = 20,
) -> dict:
    """Search people across many domains in parallel. FREE — no credits for search.

    20 concurrent by default. If enrich=True, also enriches top candidates (1 credit each).
    Returns all results in one call — much faster than per-domain calls.
    """
    import asyncio
    sem = asyncio.Semaphore(max_concurrent)
    results: list[dict] = []

    async def process(domain: str):
        async with sem:
            r = await apollo_search_people(api_key, domain, person_seniorities, per_page, enrich, max_enrich)
            results.append(r)

    await asyncio.gather(*[process(d) for d in domains], return_exceptions=True)

    total_people = sum(len(r.get("people", [])) for r in results if r.get("success"))
    total_credits = sum(r.get("credits_used", 0) for r in results)
    successful = [r for r in results if r.get("success")]

    return {
        "success": True,
        "data": {
            "domains_searched": len(domains),
            "domains_with_people": len([r for r in successful if r.get("people")]),
            "total_candidates": total_people,
            "credits_used": total_credits,
            "results": results,
        },
    }


async def apollo_enrich_people(api_key: str, person_ids: list[str]) -> dict:
    """Enrich people by Apollo person IDs via bulk_match. 1 credit per verified email.

    Returns ONLY verified emails. Auto-chunks into batches of 10 (Apollo limit).
    Also extracts organization data (industry_tag_id) for taxonomy extension.
    """
    if not person_ids:
        return {"success": True, "matches": [], "credits_used": 0}

    # Auto-chunk: Apollo bulk_match fails on >10 IDs
    all_matches_raw = []
    _debug_raw = []  # Save raw responses for debugging
    for i in range(0, len(person_ids), 10):
        chunk = person_ids[i:i + 10]
        details = [{"id": pid} for pid in chunk]
        data = await _api_call(api_key, "POST", "/people/bulk_match", {
            "details": details, "reveal_personal_emails": True,
        })
        if data:
            all_matches_raw.extend(data.get("matches", []))
            _debug_raw.extend(data.get("matches", []))

    # Save raw enrichment response for debugging
    try:
        raw_path = Path.home() / ".gtm-mcp" / "debug_apollo_enrich.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(_json.dumps(_debug_raw[:5], indent=2, default=str)[:500_000])
    except Exception:
        pass

    if not all_matches_raw:
        return {"success": False, "error": "Apollo bulk_match failed"}

    matches = []
    credits = 0
    new_tag_ids = {}

    for match in all_matches_raw:
        if not match:
            continue

        if match.get("email_status") != "verified":
            continue
        credits += 1

        org = match.get("organization") or {}
        if org.get("industry_tag_id") and org.get("industry"):
            new_tag_ids[org["industry"].lower()] = org["industry_tag_id"]

        phone = None
        if match.get("phone_numbers"):
            phone = match["phone_numbers"][0].get("sanitized_number")

        matches.append({
            "email": match.get("email", ""),
            "email_verified": True,
            "first_name": match.get("first_name", ""),
            "last_name": match.get("last_name", ""),
            "name": f"{match.get('first_name', '')} {match.get('last_name', '')}".strip(),
            "title": match.get("title", ""),
            "seniority": match.get("seniority", ""),
            "linkedin_url": match.get("linkedin_url", ""),
            "phone": phone,
            "company_name": org.get("name", ""),
            "company_domain": org.get("primary_domain", "") or org.get("website_url", ""),
            "org_data": {
                "industry": org.get("industry", ""),
                "industry_tag_id": org.get("industry_tag_id", ""),
                "country": org.get("country", ""),
                "city": org.get("city", ""),
                "state": org.get("state", ""),
                "employee_count": org.get("estimated_num_employees"),
                "short_description": org.get("short_description", ""),
                "keywords": org.get("keywords") or [],
                "funding_stage": org.get("latest_funding_stage", ""),
                "revenue": org.get("organization_revenue") or org.get("estimated_annual_revenue"),
                "founded_year": org.get("founded_year"),
                "linkedin_url": org.get("linkedin_url", ""),
                "headcount_6m_growth": org.get("organization_headcount_six_month_growth"),
                "headcount_12m_growth": org.get("organization_headcount_twelve_month_growth"),
            },
        })

    if new_tag_ids:
        _extend_industry_tags(new_tag_ids)

    return {
        "success": True,
        "matches": matches,
        "credits_used": credits,
        "new_industry_tags_discovered": new_tag_ids if new_tag_ids else None,
    }


async def apollo_enrich_companies(api_key: str, domains: list[str]) -> dict:
    """Bulk enrich companies by domain. Max 10 per call, 1 credit per company.

    Returns full company data including industry_tag_id.
    Auto-extends industry taxonomy with discovered tag_ids.
    """
    if not domains:
        return {"success": True, "companies": [], "credits_used": 0}

    all_companies = []
    total_credits = 0
    new_tag_ids = {}
    failed_domains = []

    for i in range(0, len(domains), 10):
        batch = domains[i:i + 10]
        data = await _api_call(api_key, "POST", "/organizations/bulk_enrich", {"domains": batch})
        if not data:
            failed_domains.extend(batch)
            continue

        for org in data.get("organizations") or []:
            total_credits += 1

            if org.get("industry_tag_id") and org.get("industry"):
                new_tag_ids[org["industry"].lower()] = org["industry_tag_id"]

            all_companies.append({
                "domain": (org.get("primary_domain") or org.get("domain") or "").lower().strip(),
                "name": org.get("name", ""),
                "industry": org.get("industry", ""),
                "industry_tag_id": org.get("industry_tag_id", ""),
                "employee_count": org.get("estimated_num_employees"),
                "country": org.get("country", ""),
                "city": org.get("city", ""),
                "state": org.get("state", ""),
                "founded_year": org.get("founded_year"),
                "linkedin_url": org.get("linkedin_url", ""),
                "website_url": org.get("website_url", ""),
                "keywords": org.get("keywords") or [],
                "sic_codes": org.get("sic_codes"),
                "naics_codes": org.get("naics_codes"),
                "apollo_id": org.get("id", ""),
                "revenue": org.get("estimated_annual_revenue"),
                "headcount_6m_growth": org.get("headcount_6m_growth"),
                "headcount_12m_growth": org.get("headcount_12m_growth"),
                "latest_funding_stage": org.get("latest_funding_stage", ""),
                "latest_funding_amount": org.get("latest_funding_amount"),
            })

    if new_tag_ids:
        _extend_industry_tags(new_tag_ids)

    result = {
        "success": True,
        "companies": all_companies,
        "credits_used": total_credits,
        "new_industry_tags_discovered": new_tag_ids if new_tag_ids else None,
    }
    if failed_domains:
        result["failed_domains"] = failed_domains
        result["warning"] = f"{len(failed_domains)} domains failed enrichment"
    return result


def apollo_get_taxonomy() -> dict:
    """Return Apollo industry taxonomy with tag_ids + employee ranges.

    Loads from reference files:
    - industry_tags.json: 84 industry → hex tag_id mapping (from production DB)
    - apollo_taxonomy.json: full industry list (112 names)
    - apollo_taxonomy_cache.json: keywords + industries with metadata

    For the organization_industry_tag_ids API filter, you MUST use the hex tag_ids,
    NOT the industry name strings. Use the industry_tags mapping.
    """
    industry_tags = {}
    if _tags_path.exists():
        industry_tags = json.loads(_tags_path.read_text())

    industries = list(industry_tags.keys())

    if not industries and _taxonomy_path.exists():
        data = json.loads(_taxonomy_path.read_text())
        industries = data.get("industries", [])

    keywords = []
    if _cache_path.exists():
        try:
            cache = json.loads(_cache_path.read_text())
            keywords = list(cache.get("keywords", {}).keys())[:500]
        except Exception:
            pass

    return {
        "success": True,
        "industry_tags": industry_tags,
        "industries": industries,
        "industries_count": len(industries),
        "tag_ids_count": len(industry_tags),
        "employee_ranges": ["1,10", "11,50", "51,200", "201,500", "501,1000", "1001,5000", "5001,10000", "10001,"],
        "keywords_sample": keywords[:50],
        "keywords_total": len(keywords),
        "note": "Use industry_tags[name] to get hex tag_id for organization_industry_tag_ids filter. "
                "Keywords are free-text — generate any with LLM, no validation needed.",
    }


def apollo_estimate_cost(
    target_count: int = 100,
    contacts_per_company: int = 3,
    target_rate: float = 0.35,
    num_keywords: int = 0,
    num_industries: int = 0,
    has_funding_filter: bool = True,
    probe_credits: int = 6,
) -> dict:
    """Estimate Apollo credits needed. No API call.

    The streaming pipeline fires 1 request per keyword + 1 per industry tag.
    With funding filter, each stream has funded + unfunded variants (2x).
    Most keywords exhaust in 1 page; high-yield ones page 2-3x.
    """
    # Search credits: each keyword/industry = 1 request minimum
    streams = num_keywords + num_industries
    if has_funding_filter:
        streams *= 2  # funded + unfunded variants
    avg_pages_per_stream = 1.3  # empirical: most exhaust page 1, some go to 2-3
    search_credits = max(int(streams * avg_pages_per_stream), 1) + probe_credits

    # If no keyword info provided, fall back to page-based estimate
    if num_keywords == 0 and num_industries == 0:
        target_companies = target_count / contacts_per_company
        companies_from_apollo = target_companies / target_rate
        search_credits = int(companies_from_apollo / 60) + 1 + probe_credits

    people_credits = target_count
    total = search_credits + people_credits
    return {
        "success": True,
        "search_credits": search_credits,
        "people_credits": people_credits,
        "probe_credits": probe_credits,
        "total_credits": total,
        "total_usd": round(total * 0.01, 2),
        "estimate": f"~{total} credits (${round(total * 0.01, 2)})",
    }


def _extend_industry_tags(new_tags: dict[str, str]):
    """Auto-extend industry_tags.json with newly discovered tag_ids."""
    existing = {}
    if _tags_path.exists():
        try:
            existing = json.loads(_tags_path.read_text())
        except Exception:
            pass

    updated = False
    for name, tag_id in new_tags.items():
        if name not in existing or existing[name] != tag_id:
            existing[name] = tag_id
            updated = True

    if updated:
        try:
            _tags_path.write_text(json.dumps(existing, indent=2, sort_keys=True, ensure_ascii=False))
            logger.info(f"Industry taxonomy extended: {len(new_tags)} tag(s) updated")
        except Exception as e:
            logger.warning(f"Failed to extend industry tags: {e}")
