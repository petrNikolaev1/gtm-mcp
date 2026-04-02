"""Apollo Filter Intelligence — translates natural language to optimal Apollo filters.

Approach: Probe + Scrape + Return to Agent
1. GPT-4o-mini generates candidate filters from NL (cheap bootstrapping)
2. Probe search (1 page = 1 credit, up to 100 companies)
3. Scrape top 10 websites via Apify residential proxies (FREE, parallel)
4. Return everything to the AGENT (Claude Opus running in Claude Code)
5. Agent evaluates quality and decides: use these filters or re-probe with adjusted keywords
6. If good → enrich TOP 5 for real keyword taxonomy (5 credits)

The AGENT is the evaluator, not GPT. The backend just provides data.

Apollo credit costs:
  /mixed_companies/api_search — 1 credit / page (up to 100 companies)
  /organizations/enrich      — 1 credit / result
  /people/bulk_match         — 1 credit / net-new email
"""
import asyncio
import logging
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import Counter

import httpx

logger = logging.getLogger(__name__)


async def probe_and_scrape(
    query: str,
    apollo_service,
    filters: Optional[Dict] = None,
    openai_key: Optional[str] = None,
    target_count: int = 10,
) -> Dict[str, Any]:
    """Probe Apollo + scrape websites. Returns raw data for AGENT to evaluate.

    The agent (Opus in Claude Code) decides if results are good.
    If not, agent adjusts filters and calls this again.
    """

    # Step 1: Generate candidate filters from NL if none provided
    if not filters or not filters.get("q_organization_keyword_tags"):
        candidate = await _llm_generate_candidates(query, openai_key)
        if not candidate:
            return {"error": "Failed to generate candidate filters from query"}
        filters = candidate.get("filters", {})

    credits_spent = {"search_pages": 0, "enrichments": 0}

    # Step 2: Probe search (1 credit, up to 100 companies)
    probe_results = await apollo_service.search_organizations(
        keyword_tags=filters.get("q_organization_keyword_tags", []),
        locations=filters.get("organization_locations"),
        num_employees_ranges=filters.get("organization_num_employees_ranges"),
        page=1,
        per_page=100,
    )
    credits_spent["search_pages"] = 1

    if not probe_results:
        return {
            "filters_used": filters,
            "credits_spent": credits_spent,
            "companies": [],
            "total_available": 0,
            "website_texts": {},
            "message": "Probe search returned no results. Broaden your keywords.",
        }

    companies = probe_results.get("organizations", []) or probe_results.get("accounts", [])
    total_available = probe_results.get("pagination", {}).get("total_entries", 0)

    if not companies:
        return {
            "filters_used": filters,
            "credits_spent": credits_spent,
            "companies": [],
            "total_available": total_available,
            "website_texts": {},
            "message": "No companies matched these filters. Try different keywords.",
        }

    # Step 3: Scrape top 10 websites in parallel (FREE via Apify proxies)
    from app.services.scraper_service import ScraperService
    scraper = ScraperService()
    domains_to_scrape = []
    for comp in companies[:10]:
        domain = comp.get("domain") or comp.get("primary_domain")
        if domain:
            domains_to_scrape.append(domain)

    raw_texts = await scraper.scrape_domains_fast(domains_to_scrape, timeout=10, max_concurrent=10)
    # Cap at 2000 chars per site — enough for agent to understand what the company does
    website_texts = {d: t[:2000] for d, t in raw_texts.items()}

    # Build company summaries for the agent
    company_summaries = []
    for c in companies[:15]:
        domain = c.get("domain") or c.get("primary_domain", "")
        summary = {
            "name": c.get("name", ""),
            "domain": domain,
            "industry": c.get("industry"),
            "employees": c.get("estimated_num_employees") or c.get("num_contacts"),
        }
        if domain in website_texts:
            summary["website_excerpt"] = website_texts[domain][:1000]
        company_summaries.append(summary)

    # Calculate estimated gathering cost
    per_page = 100
    companies_needed = int(target_count / 0.3)
    pages_needed = max(1, (companies_needed + per_page - 1) // per_page)

    return {
        "filters_used": filters,
        "companies": company_summaries,
        "companies_in_page": len(companies),
        "total_available": total_available,
        "websites_scraped": len(website_texts),
        "credits_spent": credits_spent,
        "estimated_gathering": {
            "target_count": target_count,
            "pages_needed": pages_needed,
            "credits_needed": pages_needed,
            "total_companies": min(total_available, pages_needed * per_page),
        },
        "message": (
            f"Probe: {len(companies)} companies ({total_available} total available). "
            f"Scraped {len(website_texts)} websites. "
            f"Review the company summaries and website excerpts below. "
            f"If these are the RIGHT kind of companies, proceed with gathering. "
            f"If NOT, tell me what's wrong and I'll adjust the filters."
        ),
    }


async def enrich_for_taxonomy(
    apollo_service,
    domains: List[str],
) -> Dict[str, Any]:
    """Enrich top companies to extract Apollo's real keyword taxonomy.
    Called AFTER agent confirms probe quality is good."""

    enriched_keywords = Counter()
    enriched_industries = Counter()
    credits_spent = 0

    # Use bulk_enrich (1 API call for up to 10 domains) instead of sequential single-enrich
    orgs = await apollo_service.bulk_enrich_organizations(domains[:10])
    for org_data in orgs:
        if org_data:
            credits_spent += 1
            ind = org_data.get("industry")
            if ind:
                enriched_industries[ind.lower()] += 1
            for ind_item in (org_data.get("industries") or []):
                enriched_industries[ind_item.lower()] += 1
            for kw in (org_data.get("keywords") or []):
                kw_clean = kw.lower().strip()
                if 2 < len(kw_clean) < 50:
                    enriched_keywords[kw_clean] += 1

    return {
        "top_keywords": dict(enriched_keywords.most_common(20)),
        "top_industries": dict(enriched_industries.most_common(10)),
        "enriched_count": credits_spent,
        "credits_spent": credits_spent,
    }


def build_refined_filters(
    original_filters: Dict,
    taxonomy_keywords: Dict[str, int],
    taxonomy_industries: Dict[str, int],
) -> Dict:
    """Build refined filters from taxonomy. Called after enrich_for_taxonomy."""
    # Use top keywords from enrichment (frequency ≥ 2 or top 7)
    sorted_kw = sorted(taxonomy_keywords.items(), key=lambda x: -x[1])
    refined_keywords = [kw for kw, count in sorted_kw if count >= 2]
    if len(refined_keywords) < 3:
        refined_keywords = [kw for kw, _ in sorted_kw[:7]]

    # Merge with original keywords
    original_kw = [k.lower() for k in original_filters.get("q_organization_keyword_tags", [])]
    all_keywords = list(dict.fromkeys(refined_keywords + original_kw))[:10]

    return {
        "organization_locations": original_filters.get("organization_locations"),
        "q_organization_keyword_tags": all_keywords,
        "organization_num_employees_ranges": original_filters.get("organization_num_employees_ranges", ["11,50", "51,200"]),
    }


# ── Legacy wrapper for backward compatibility ──

async def suggest_filters(
    query: str,
    apollo_service,
    openai_key: Optional[str] = None,
    anthropic_key: Optional[str] = None,
    gemini_key: Optional[str] = None,
    target_count: int = 10,
) -> Dict[str, Any]:
    """Legacy wrapper — probes, scrapes, enriches in one call.
    Returns probe data + enriched taxonomy for the agent to review."""

    # Probe + scrape
    probe = await probe_and_scrape(query, apollo_service, openai_key=openai_key, target_count=target_count)
    if probe.get("error") or not probe.get("companies"):
        return probe

    # Auto-enrich top 5 domains for keyword taxonomy
    domains = [c["domain"] for c in probe["companies"] if c.get("domain")]
    taxonomy = await enrich_for_taxonomy(apollo_service, domains)

    # Build refined filters
    refined = build_refined_filters(
        probe["filters_used"],
        taxonomy["top_keywords"],
        taxonomy["top_industries"],
    )

    total_credits = probe["credits_spent"]["search_pages"] + taxonomy["credits_spent"]

    return {
        "suggested_filters": refined,
        "probe_results": {
            "companies_found": probe["companies_in_page"],
            "total_available": probe["total_available"],
            "top_industries": taxonomy["top_industries"],
            "top_keywords": taxonomy["top_keywords"],
            "sample_companies": probe["companies"][:5],
            "websites_scraped": probe["websites_scraped"],
        },
        "credits_spent": {"search_pages": probe["credits_spent"]["search_pages"], "enrichments": taxonomy["credits_spent"], "total": total_credits},
        "estimated": probe["estimated_gathering"],
        "message": probe["message"],
    }


# ── Initial filter generation (cheap, gpt-4o-mini) ────────

async def _llm_generate_candidates(query: str, openai_key: Optional[str] = None) -> Optional[Dict]:
    """Use GPT to bootstrap initial Apollo filter candidates from real taxonomy.
    Maps user's natural language to actual Apollo industry/keyword values."""
    if not openai_key:
        from app.config import settings
        openai_key = settings.OPENAI_API_KEY
    if not openai_key:
        return _basic_parse(query)

    # Load Apollo taxonomy
    taxonomy_path = Path(__file__).parent.parent.parent.parent / "apollo_filters" / "apollo_taxonomy.json"
    industries_list = ""
    try:
        if taxonomy_path.exists():
            tax_data = json.loads(taxonomy_path.read_text())
            industries_list = ", ".join(tax_data.get("industries", []))
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": f"""You translate natural language business queries into Apollo.io search filters.

APOLLO INDUSTRIES (use ONLY exact values from this list for industry filtering):
{industries_list or "(not available)"}

Return ONLY valid JSON:
{{
  "filters": {{
    "organization_locations": ["City, Country" or "Country"],
    "q_organization_keyword_tags": ["keyword1", "keyword2", ...],
    "organization_num_employees_ranges": ["1,10", "11,50", "51,200"]
  }}
}}
Rules:
- Keywords: BROAD industry terms + synonyms (3-7 tags) — these are free-text
- Location: include country if city given
- Size: cover likely range for the segment"""},
                        {"role": "user", "content": f"Generate Apollo search filters for: {query}"},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.1,
                },
            )
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(clean)
    except Exception as e:
        logger.error(f"LLM filter generation failed: {e}")
        return _basic_parse(query)


def _basic_parse(query: str) -> Dict:
    """Fallback: basic keyword extraction without LLM."""
    words = query.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in ("find", "gather", "search", "companies", "businesses", "firms")]
    locations = []
    for loc in ["london", "new york", "dubai", "berlin", "paris", "mumbai", "singapore", "sydney", "tokyo"]:
        if loc in query.lower():
            locations.append(loc.title())
    return {
        "filters": {
            "organization_locations": locations or None,
            "q_organization_keyword_tags": keywords[:5] or ["business services"],
            "organization_num_employees_ranges": ["1,10", "11,50", "51,200"],
        }
    }
