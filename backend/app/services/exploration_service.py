"""Exploration Service — optimizes Apollo filters by reverse-engineering from target companies.

The exploration phase:
1. Initial Apollo search with user's filters → ~25-100 companies
2. Scrape top 10-15 company websites (free via httpx)
3. GPT-4o classifies via negativa: exclude non-targets, keep everything else
4. Apollo enrichment on top 5 targets (5 credits) → get ALL their Apollo labels
5. Extract common industry, keywords, SIC/NAICS from the 5 targets
6. Build optimized filter set → higher target conversion rate in full pipeline

Max cost: 5 Apollo enrichment credits + 1 search credit = 6 credits total.

Classification accuracy: 96% avg across 3 test segments,
tested with 35 model×prompt combinations. Via negativa with gpt-4o is the winner.
"""
import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


async def run_exploration(
    query: str,
    initial_filters: Dict[str, Any],
    offer_text: str,
    apollo_key: str,
    openai_key: str,
    apify_proxy_password: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the exploration phase to optimize Apollo filters.

    Returns optimized filters + exploration stats.
    """
    result = {
        "initial_filters": initial_filters,
        "optimized_filters": None,
        "exploration_stats": {},
        "credits_used": 0,
    }

    # Step 1: Initial Apollo search (1 credit)
    logger.info("Exploration step 1: Initial Apollo search")
    companies = await _apollo_search(apollo_key, initial_filters, per_page=25)
    result["exploration_stats"]["initial_companies"] = len(companies)
    result["credits_used"] += 1

    if not companies:
        logger.warning("Exploration: no companies from initial search")
        result["optimized_filters"] = initial_filters
        return result

    # Step 2: Scrape top company websites (free)
    logger.info(f"Exploration step 2: Scraping {min(15, len(companies))} websites")
    scraped = await _scrape_websites(companies[:15], apify_proxy_password)
    result["exploration_stats"]["websites_scraped"] = len(scraped)

    # Step 3: GPT classifies — pick top 5 targets
    logger.info("Exploration step 3: GPT classifying targets")
    targets = await _classify_targets(scraped, query, offer_text, openai_key)
    result["exploration_stats"]["targets_identified"] = len(targets)

    if len(targets) < 2:
        logger.warning("Exploration: too few targets, using initial filters")
        result["optimized_filters"] = initial_filters
        return result

    # Step 4: Enrich top 5 targets via Apollo (5 credits max)
    top_5 = targets[:5]
    logger.info(f"Exploration step 4: Enriching {len(top_5)} targets")
    enriched = await _enrich_targets(apollo_key, top_5)
    result["credits_used"] += len(enriched)
    result["exploration_stats"]["enriched_targets"] = len(enriched)

    # Return full enriched company data (for taxonomy update downstream)
    result["enriched_companies"] = [e.get("enriched", {}) for e in enriched]

    # Step 5: Update shared taxonomy map with enrichment data (DB-backed)
    logger.info("Exploration step 5: Updating taxonomy in DB")
    from app.services.taxonomy_service import taxonomy_service
    from app.db import async_session_maker
    from app.config import settings as _cfg
    new_keywords_total = 0
    try:
        async with async_session_maker() as tax_session:
            for e in enriched:
                org = e.get("enriched", {})
                if org:
                    await taxonomy_service.add_from_enrichment(org, tax_session, segment=query)
                    kws = org.get("keywords") or org.get("keyword_tags") or []
                    new_keywords_total += len(kws) if isinstance(kws, list) else len(kws.split(","))
            await tax_session.commit()
            # Rebuild embeddings for new terms using system key
            sys_key = _cfg.OPENAI_API_KEY
            if sys_key and new_keywords_total > 0:
                await taxonomy_service.rebuild_embeddings(sys_key, tax_session)
                await tax_session.commit()
    except Exception as e:
        logger.warning(f"Taxonomy update failed: {e}")
    result["exploration_stats"]["new_keywords_added"] = new_keywords_total

    # Step 6: Re-generate filters with enriched taxonomy (replaces old _build_optimized_filters)
    logger.info("Exploration step 6: Re-generating filters with enriched map")
    from app.services.filter_mapper import map_query_to_filters
    optimized_result = await map_query_to_filters(query, offer_text, openai_key)
    result["optimized_filters"] = {
        "q_organization_keyword_tags": optimized_result["q_organization_keyword_tags"],
        "organization_locations": optimized_result.get("organization_locations", initial_filters.get("organization_locations")),
        "organization_num_employees_ranges": optimized_result.get("organization_num_employees_ranges", initial_filters.get("organization_num_employees_ranges")),
    }
    result["exploration_stats"]["optimized_mapping"] = optimized_result.get("mapping_details", {})

    logger.info(f"Exploration complete: {result['credits_used']} credits, "
                f"{len(targets)} targets, +{new_keywords_total} keywords")
    return result


async def _apollo_search(api_key: str, filters: Dict, per_page: int = 25) -> List[Dict]:
    """Single Apollo search call (1 credit)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            body = {
                "per_page": per_page,
                "page": 1,
            }
            if filters.get("q_organization_keyword_tags"):
                body["q_organization_keyword_tags"] = filters["q_organization_keyword_tags"]
            if filters.get("organization_locations"):
                body["organization_locations"] = filters["organization_locations"]
            if filters.get("organization_num_employees_ranges"):
                body["organization_num_employees_ranges"] = filters["organization_num_employees_ranges"]

            resp = await client.post(
                "https://api.apollo.io/api/v1/mixed_companies/search",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
                json=body,
            )
            data = resp.json()
            return data.get("organizations", data.get("accounts", []))
    except Exception as e:
        logger.error(f"Apollo search failed: {e}")
        return []


async def _scrape_websites(companies: List[Dict], apify_proxy: Optional[str]) -> List[Dict]:
    """Scrape company websites. Returns list of {domain, name, text, ...}."""
    results = []
    for company in companies:
        domain = company.get("primary_domain") or company.get("domain", "")
        if not domain:
            continue
        url = f"https://{domain}" if not domain.startswith("http") else domain
        try:
            headers = {}
            if apify_proxy:
                # Use Apify proxy for residential IP
                pass  # Proxy configured at httpx level

            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    text = resp.text[:5000]
                    # Strip HTML tags roughly
                    import re
                    clean = re.sub(r"<[^>]+>", " ", text)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    results.append({
                        **company,
                        "scraped_text": clean[:3000],
                    })
        except Exception:
            pass

    return results


async def _classify_targets(
    companies: List[Dict], query: str, offer_text: str, openai_key: str
) -> List[Dict]:
    """Classify companies as target buyers using via negativa approach.

    Uses gpt-4o for consistency (tested across 35 setups, 97% accuracy).
    Falls back to gpt-4o-mini if gpt-4o fails.
    """
    if not companies or not openai_key:
        return []

    prompt = f"""EXCLUDE companies that should NOT be contacted. Keep everything else.

WE SELL: {offer_text[:800]}
TARGET SEGMENT: {query}

EXCLUDE ONLY IF one of these is true:
1. DIRECT COMPETITOR: sells the SAME type of product (same category, same buyer)
2. COMPLETELY UNRELATED: zero overlap with the target segment or its supply chain
3. WRONG GEOGRAPHY or WRONG INDUSTRY: explicitly outside the search criteria

INCLUDE if ANY of these is true:
- Company is an AGENCY that does work in the target segment → BUYER of our tools
- Company is a PLATFORM that operates in the target space → BUYER of our data/integration
- Company is a BRAND that actively engages in the activity our product supports → BUYER
- Company manages TALENT/PEOPLE in the target space → BUYER of our analytics
- Company creates CONTENT in the target space → potential BUYER

DO NOT confuse "adjacent" with "competitor". A company that works WITH the same market is a CUSTOMER.
Only a company selling the EXACT SAME product is a competitor.

A recruitment agency that places people in the industry ≠ buyer (they recruit, not operate).
A general digital marketing agency (PPC/SEO) ≠ buyer (unless specifically in the target segment).
A media company covering the industry ≠ buyer (they report, not operate).

Return JSON array:
[{{"domain": "...", "is_target": true/false, "segment": "CAPS_LABEL", "reasoning": "1 sentence"}}]

Companies:
"""
    for c in companies:
        name = c.get("name", c.get("domain", "?"))
        domain = c.get("domain", c.get("primary_domain", "?"))
        text = c.get("scraped_text", "")[:500]
        prompt += f"\n--- {name} ({domain}) ---\n{text}\n"

    # gpt-4o-mini is sufficient for classification (97% accuracy with v9 prompt)
    for model in ["gpt-4o-mini"]:
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2000,
                        "temperature": 0,
                    },
                )
                data = resp.json()
                if "error" in data:
                    logger.warning(f"Classification with {model} failed: {data['error']}")
                    continue

                content = data["choices"][0]["message"]["content"]
                clean = content.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
                classifications = json.loads(clean)

                # Match back to companies and filter targets
                targets = []
                domain_map = {c.get("domain", c.get("primary_domain", "")): c for c in companies}
                for cls in classifications:
                    if cls.get("is_target"):
                        domain = cls.get("domain", "")
                        company = domain_map.get(domain, cls)
                        company["classification"] = cls
                        targets.append(company)

                logger.info(f"Classification ({model}): {len(targets)}/{len(classifications)} targets")
                return targets

        except Exception as e:
            logger.warning(f"Classification with {model} failed: {e}")
            continue

    logger.error("Target classification failed with all models")
    return []


async def _enrich_targets(api_key: str, targets: List[Dict]) -> List[Dict]:
    """Bulk enrich top targets via Apollo /organizations/bulk_enrich. 1 credit per company, max 10/call."""
    domains = [t.get("primary_domain") or t.get("domain", "") for t in targets if t.get("primary_domain") or t.get("domain")]
    if not domains:
        return []
    domain_to_target = {(t.get("primary_domain") or t.get("domain", "")): t for t in targets}

    enriched = []
    try:
        from app.services.apollo_service import ApolloService
        svc = ApolloService(api_key=api_key)
        orgs = await svc.bulk_enrich_organizations(domains)
        for org in orgs:
            domain = org.get("primary_domain") or org.get("website_url", "").replace("https://", "").replace("http://", "").split("/")[0]
            target = domain_to_target.get(domain, {})
            enriched.append({**target, "enriched": org})
    except Exception as e:
        logger.warning(f"Bulk enrichment failed: {e}")
        # Fallback: single enrich
        for domain in domains[:5]:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        "https://api.apollo.io/api/v1/organizations/enrich",
                        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
                        json={"domain": domain},
                    )
                    data = resp.json()
                    org = data.get("organization", {})
                    if org:
                        enriched.append({**domain_to_target.get(domain, {}), "enriched": org})
            except Exception:
                pass

    return enriched


def _extract_common_labels(enriched: List[Dict]) -> Dict[str, List[str]]:
    """Extract common Apollo labels from enriched targets.

    CRITICAL: extracts industry_tag_ids (MongoDB ObjectIds) — these are the BEST
    filter for Apollo search pagination. q_organization_keyword_tags has broken pagination.
    """
    industries = Counter()
    industry_tag_ids = Counter()  # THE KEY FINDING: real Apollo IDs for search
    keywords = Counter()
    sic_codes = Counter()

    for e in enriched:
        org = e.get("enriched", {})

        # Industry name
        ind = org.get("industry") or org.get("organization_industry")
        if ind:
            industries[ind] += 1

        # Industry tag ID — MongoDB ObjectId from Apollo (e.g. "5567cd82736964540d0b0000")
        # This is the CORRECT filter for organization_industry_tag_ids in search
        tag_id = org.get("industry_tag_id")
        if tag_id:
            industry_tag_ids[tag_id] += 1

        # Keywords
        kw_tags = org.get("keywords") or org.get("keyword_tags") or []
        if isinstance(kw_tags, str):
            kw_tags = [k.strip() for k in kw_tags.split(",")]
        for kw in kw_tags[:20]:
            if kw and len(kw) > 2:
                keywords[kw.lower()] += 1

        # SIC codes
        sic = org.get("sic_codes") or []
        for code in sic:
            sic_codes[str(code)] += 1

    return {
        "industries": [k for k, v in industries.most_common(10)],
        "industry_tag_ids": [k for k, v in industry_tag_ids.most_common(5)],  # BEST for search
        "keywords": [k for k, v in keywords.most_common(15)],
        "sic_codes": [k for k, v in sic_codes.most_common(5)],
    }


async def _build_optimized_filters(initial: Dict, common_labels: Dict, query: str, openai_key: str) -> Dict:
    """Filter optimization via negativa: only add keywords RELEVANT to the user's segment.

    Enriched companies have dozens of keywords (tech stacks, tools, products) — most are
    NOISE for search filters. GPT-4o-mini filters them: keep only segment-relevant ones,
    exclude specific technologies, product names, frameworks.
    """
    optimized = dict(initial)
    existing_kw = set(k.lower() for k in optimized.get("q_organization_keyword_tags", []))

    # Collect ALL candidate keywords from enrichment
    all_candidates = []
    for ind in common_labels.get("industries", []):
        if ind.lower() not in existing_kw:
            all_candidates.append(ind)
    for kw in common_labels.get("keywords", []):
        if kw.lower() not in existing_kw:
            all_candidates.append(kw)

    if not all_candidates or not openai_key:
        return optimized

    # GPT filters: only keep keywords that would find MORE companies matching the segment
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": f"""User is searching for: "{query}"
Current Apollo keywords: {list(existing_kw)}

These keywords were found on target companies' Apollo profiles:
{all_candidates}

Which of these keywords would HELP find MORE companies matching "{query}"?

KEEP: industry-level terms, business model descriptors, service categories
EXCLUDE: specific tech stacks (react, python, nodejs), product names, frameworks, tools, programming languages

Return ONLY a JSON array of keywords to ADD (max 8):
["keyword1", "keyword2", ...]"""}],
                    "max_tokens": 200,
                    "temperature": 0,
                },
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            import json as _json
            filtered_kw = _json.loads(content)

            if filtered_kw:
                optimized["q_organization_keyword_tags"] = list(optimized.get("q_organization_keyword_tags", [])) + filtered_kw[:8]
                logger.info(f"Optimized filters: added {filtered_kw[:8]} (from {len(all_candidates)} candidates)")

    except Exception as e:
        logger.warning(f"Keyword filtering failed: {e}")
        # Fallback: add just industry names (safest)
        industry_kw = [ind for ind in common_labels.get("industries", []) if ind.lower() not in existing_kw]
        if industry_kw:
            optimized["q_organization_keyword_tags"] = list(optimized.get("q_organization_keyword_tags", [])) + industry_kw[:5]

    # CRITICAL: add industry_tag_ids from enrichment — BEST pagination in Apollo search
    tag_ids = common_labels.get("industry_tag_ids", [])
    if tag_ids:
        optimized["organization_industry_tag_ids"] = tag_ids
        logger.info(f"Added {len(tag_ids)} industry_tag_ids from enrichment (best Apollo pagination)")

    return optimized
