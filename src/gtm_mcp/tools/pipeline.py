"""Pipeline execution — deterministic gather+scrape in one atomic tool call.

After Checkpoint 1, the agent has approved filters. This tool runs ALL
deterministic I/O in Python with asyncio streaming:

1. Apollo search: all keywords + industries in parallel (1 per request)
2. Dedup by domain as results arrive
3. Scrape: 100 concurrent Apify — starts AS SOON AS first domains arrive
4. Return: companies with scraped text, ready for LLM classification

The ONLY part the agent handles is classification (LLM) and people extraction.
This mirrors magnum-opus's streaming_pipeline.py but as a single MCP tool.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def pipeline_gather_and_scrape(
    keywords: list[str],
    industry_tag_ids: list[str],
    locations: list[str],
    employee_ranges: list[str],
    funding_stages: list[str] | None = None,
    max_companies: int = 400,
    scrape_concurrent: int = 100,
    max_pages_per_stream: int = 5,
    *,
    config=None,
) -> dict:
    """Atomic gather + scrape pipeline. One tool call, full streaming inside.

    Fires all Apollo searches in parallel (1 keyword/industry per request).
    As domains arrive, immediately queues them for scraping (100 concurrent).
    Returns all companies with scraped text — ready for agent classification.

    Returns:
        companies: [{domain, name, apollo_data, scraped_text, scrape_status}]
        requests: [{type, filter_value, funded, page, raw_returned, new_unique, credits_used}]
        stats: {gather_seconds, scrape_seconds, total_seconds, credits_used, ...}
    """
    config = config or _default_config()
    api_key = config.get("apollo_api_key")
    apify_key = config.get("apify_proxy_password")
    if not api_key:
        return {"success": False, "error": "apollo_api_key not configured"}

    from gtm_mcp.tools.apollo import apollo_search_companies
    from gtm_mcp.tools.scraping import scrape_website

    started_at = datetime.now(timezone.utc)
    seen_domains: set[str] = set()
    companies: dict[str, dict] = {}
    requests: list[dict] = []
    scrape_queue: asyncio.Queue = asyncio.Queue()
    scrape_results: dict[str, dict] = {}
    gather_done = asyncio.Event()
    req_counter = 0

    # --- Phase 1: Apollo gather (all parallel, feeds scrape queue) ---

    async def search_one(filter_type: str, filter_value: str, funded: bool = False):
        nonlocal req_counter
        for page in range(1, max_pages_per_stream + 1):
            if len(seen_domains) >= max_companies:
                break

            filters: dict[str, Any] = {
                "organization_locations": locations,
                "organization_num_employees_ranges": employee_ranges,
            }
            if filter_type == "keyword":
                filters["q_organization_keyword_tags"] = [filter_value]
            else:
                filters["organization_industry_tag_ids"] = [filter_value]
            if funded and funding_stages:
                filters["organization_latest_funding_stage_cd"] = funding_stages

            result = await apollo_search_companies(filters, page=page, per_page=100, config=config)
            if not result.get("success"):
                break

            raw_companies = result.get("companies", [])
            if not raw_companies:
                break  # exhausted

            new_unique = 0
            for c in raw_companies:
                domain = c.get("primary_domain", "") or c.get("domain", "")
                if not domain or domain in seen_domains:
                    continue
                if len(seen_domains) >= max_companies:
                    break
                seen_domains.add(domain)
                new_unique += 1
                companies[domain] = {
                    "domain": domain,
                    "name": c.get("name", ""),
                    "apollo_id": c.get("id", ""),
                    "apollo_data": {
                        "industry": c.get("industry", ""),
                        "industry_tag_id": c.get("industry_tag_id", ""),
                        "employee_count": c.get("employee_count"),
                        "country": c.get("country", ""),
                        "city": c.get("city", ""),
                        "founded_year": c.get("founded_year"),
                        "linkedin_url": c.get("linkedin_url", ""),
                    },
                    "discovery": {
                        "found_by": f"{filter_type}:{filter_value}",
                        "funded": funded,
                        "page": page,
                    },
                }
                # Feed to scrape queue immediately
                await scrape_queue.put(domain)

            req_counter += 1
            requests.append({
                "id": f"req-{req_counter:03d}",
                "type": filter_type,
                "filter_value": filter_value,
                "funded": funded,
                "page": page,
                "result": {
                    "raw_returned": len(raw_companies),
                    "new_unique": new_unique,
                    "duplicates": len(raw_companies) - new_unique,
                    "credits_used": 1,
                },
            })

            # Low yield: <10 on page 1 → stop
            if page == 1 and len(raw_companies) < 10:
                break

    gather_started = datetime.now(timezone.utc)

    gather_tasks = []
    for kw in keywords:
        gather_tasks.append(search_one("keyword", kw, funded=False))
        if funding_stages:
            gather_tasks.append(search_one("keyword", kw, funded=True))
    for tag_id in industry_tag_ids:
        gather_tasks.append(search_one("industry", tag_id, funded=False))
        if funding_stages:
            gather_tasks.append(search_one("industry", tag_id, funded=True))

    async def run_gather():
        await asyncio.gather(*gather_tasks, return_exceptions=True)
        gather_done.set()

    # --- Phase 2: Scrape (100 concurrent, starts as domains arrive) ---

    scrape_sem = asyncio.Semaphore(scrape_concurrent)
    scrape_started = datetime.now(timezone.utc)

    async def scrape_worker():
        while True:
            try:
                domain = await asyncio.wait_for(scrape_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if gather_done.is_set() and scrape_queue.empty():
                    break
                continue

            async with scrape_sem:
                url = f"https://{domain}"
                result = await scrape_website(url, apify_proxy_password=apify_key)
                scrape_results[domain] = {
                    "status": "success" if result.get("success") else "failed",
                    "text_length": len(result.get("text", "")),
                    "text": (result.get("text", ""))[:5000],  # cap per company
                }
                scrape_queue.task_done()

    # Run both phases concurrently — scraping starts as domains arrive
    scrape_workers = [scrape_worker() for _ in range(scrape_concurrent)]

    await asyncio.gather(
        run_gather(),
        *scrape_workers,
        return_exceptions=True,
    )

    gather_completed = datetime.now(timezone.utc)
    scrape_completed = datetime.now(timezone.utc)

    # --- Merge scrape results into companies ---

    for domain, comp in companies.items():
        sr = scrape_results.get(domain, {"status": "not_scraped", "text_length": 0, "text": ""})
        comp["scrape"] = sr

    completed_at = datetime.now(timezone.utc)
    total_credits = sum(r["result"]["credits_used"] for r in requests)

    return {
        "success": True,
        "data": {
            "companies": companies,
            "requests": requests,
            "stats": {
                "total_companies": len(companies),
                "scraped_success": sum(1 for d, c in companies.items() if c.get("scrape", {}).get("status") == "success"),
                "scraped_failed": sum(1 for d, c in companies.items() if c.get("scrape", {}).get("status") == "failed"),
                "total_requests": len(requests),
                "total_credits": total_credits,
                "gather_started": gather_started.isoformat(),
                "gather_completed": gather_completed.isoformat(),
                "scrape_started": scrape_started.isoformat(),
                "scrape_completed": scrape_completed.isoformat(),
                "gather_seconds": (gather_completed - gather_started).total_seconds(),
                "scrape_seconds": (scrape_completed - scrape_started).total_seconds(),
                "total_seconds": (completed_at - started_at).total_seconds(),
            },
        },
    }


def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()
