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
    project: str,
    run_id: str,
    config=None,
    workspace=None,
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

    # Load PROJECT-LEVEL blacklist (not global — different projects may target same contacts)
    if workspace and project:
        bl_data = workspace.load(project, "blacklist.json")
        if bl_data and isinstance(bl_data, dict):
            seen_domains.update(bl_data.keys())
            logger.info("Loaded %d blacklisted domains for project %s", len(bl_data), project)
        elif bl_data and isinstance(bl_data, list):
            seen_domains.update(bl_data)
            logger.info("Loaded %d blacklisted domains for project %s", len(bl_data), project)
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

            result = await apollo_search_companies(api_key, filters, page=page, per_page=100)
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
            # Exhausted: page returned companies but 0 new unique → stop
            if new_unique == 0:
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

    _gather_completed_at: list = []  # mutable container for closure

    async def run_gather():
        await asyncio.gather(*gather_tasks, return_exceptions=True)
        _gather_completed_at.append(datetime.now(timezone.utc))  # FIX #3: timestamp before scrape finishes
        # Send sentinel values to stop workers
        for _ in range(_WORKER_COUNT):
            await scrape_queue.put(None)

    # --- Phase 2: Scrape (workers with sentinel shutdown, semaphore for concurrency) ---

    scrape_sem = asyncio.Semaphore(scrape_concurrent)
    scrape_started = datetime.now(timezone.utc)
    _WORKER_COUNT = min(scrape_concurrent, 20)  # FIX #1: 20 workers, not 100. Semaphore limits actual concurrency.

    async def scrape_worker():
        while True:
            domain = await scrape_queue.get()  # FIX #1: no timeout polling. Sentinel (None) terminates.
            if domain is None:
                scrape_queue.task_done()
                break

            async with scrape_sem:
                url = f"https://{domain}"
                result = await scrape_website(url, apify_proxy_password=apify_key)
                scrape_results[domain] = {
                    "status": "success" if result.get("success") else "failed",
                    "text_length": len(result.get("text", "")),
                    "text": (result.get("text", ""))[:3000],  # FIX #4: 3K per company (400 × 3K = 1.2MB, fits MCP)
                }
                scrape_queue.task_done()

    # Run both phases concurrently — scraping starts as domains arrive from Apollo
    scrape_workers = [scrape_worker() for _ in range(_WORKER_COUNT)]

    await asyncio.gather(
        run_gather(),
        *scrape_workers,
        return_exceptions=True,
    )

    gather_completed = _gather_completed_at[0] if _gather_completed_at else datetime.now(timezone.utc)
    scrape_completed = datetime.now(timezone.utc)

    # --- Save scraped text to workspace file (not in MCP response — too large) ---
    # FIX #4: Response returns companies WITHOUT full text. Text saved to file.

    for domain, comp in companies.items():
        sr = scrape_results.get(domain, {"status": "not_scraped", "text_length": 0, "text": ""})
        comp["scrape"] = {
            "status": sr["status"],
            "text_length": sr["text_length"],
        }
        comp["_scraped_text"] = sr.get("text", "")  # kept in-memory for file save, stripped from response

    completed_at = datetime.now(timezone.utc)
    total_credits = sum(r["result"]["credits_used"] for r in requests)

    # Build response: companies WITHOUT full scraped text (too large for MCP response)
    # Agent uses save_data to persist, then reads text per-company for classification
    response_companies = {}
    for domain, comp in companies.items():
        rc = dict(comp)
        rc.pop("_scraped_text", None)  # strip from response
        response_companies[domain] = rc

    # Also build a separate dict for the agent to pass to classification agents
    # Key: domain, Value: first 2500 chars of scraped text
    scraped_texts = {
        d: comp.get("_scraped_text", "")[:2500]
        for d, comp in companies.items()
        if comp.get("scrape", {}).get("status") == "success"
    }

    # Auto-save companies + requests to run file (if project + run_id provided)
    # This ensures scrape metadata + discovery provenance persist even if agent
    # fails to save. Classification agents later MERGE into these company records.
    if project and run_id and workspace:
        run_path = f"runs/{run_id}.json"
        existing_run = workspace.load(project, run_path) or {}
        existing_run["companies"] = response_companies
        existing_run["requests"] = requests
        existing_run["totals"] = {
            **existing_run.get("totals", {}),
            "total_api_requests": len(requests),
            "total_credits_search": total_credits,
            "unique_companies": len(companies),
            "companies_scraped": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") == "success"),
        }
        existing_run["rounds"] = existing_run.get("rounds", [])
        if not existing_run["rounds"]:
            existing_run["rounds"].append({})
        existing_run["rounds"][0] = {
            **existing_run["rounds"][0],
            "id": "round-001",
            "status": "completed",
            "timestamps": {
                "gather_started": gather_started.isoformat(),
                "gather_completed": gather_completed.isoformat(),
                "scrape_started": scrape_started.isoformat(),
                "scrape_completed": scrape_completed.isoformat(),
            },
            "gather_phase": {"total_requests": len(requests), "unique_companies": len(companies), "credits_used": total_credits},
            "scrape_phase": {
                "total": len(companies),
                "success": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") == "success"),
                "failed": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") != "success"),
                "concurrent": scrape_concurrent,
            },
        }
        workspace.save(project, run_path, existing_run)
        logger.info("Auto-saved %d companies + %d requests to %s", len(companies), len(requests), run_path)

    return {
        "success": True,
        "data": {
            "companies": response_companies,
            "scraped_texts": scraped_texts,
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


async def pipeline_compute_leaderboard(
    project: str,
    run_id: str,
    *,
    workspace=None,
) -> dict:
    """Compute keyword + industry leaderboard from run's request + company data.

    For each keyword/industry: count unique companies, targets, target rate,
    credits, quality_score. Saves to run file. Zero LLM.
    """
    import math
    workspace = workspace or _default_workspace()

    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}

    requests = run_data.get("requests", [])
    companies = run_data.get("companies", {})

    if not requests:
        return {"success": False, "error": "No requests tracked in run file"}

    # Build per-keyword stats
    keyword_stats: dict[str, dict] = {}
    for req in requests:
        key = f"{req['type']}:{req.get('filter_value', '')}"
        if key not in keyword_stats:
            keyword_stats[key] = {
                "type": req["type"],
                "filter_value": req.get("filter_value", ""),
                "unique_companies": 0,
                "targets": 0,
                "credits_used": 0,
            }
        s = keyword_stats[key]
        s["credits_used"] += req.get("result", {}).get("credits_used", 1)
        s["unique_companies"] += req.get("result", {}).get("new_unique", 0)

    # Count targets per keyword using company.discovery.found_by
    for domain, comp in companies.items():
        found_by = comp.get("discovery", {}).get("found_by", "")
        if found_by and found_by in keyword_stats:
            is_target = comp.get("classification", {}).get("is_target", False)
            if is_target:
                keyword_stats[found_by]["targets"] += 1

    # Compute quality scores
    leaderboard = []
    for key, s in keyword_stats.items():
        uc = s["unique_companies"]
        targets = s["targets"]
        credits = max(s["credits_used"], 1)
        target_rate = targets / uc if uc > 0 else 0
        quality_score = target_rate * math.log(uc + 1) / credits if uc > 0 else 0

        leaderboard.append({
            **s,
            "target_rate": round(target_rate, 3),
            "quality_score": round(quality_score, 4),
        })

    leaderboard.sort(key=lambda x: -x["quality_score"])

    # Save to run file
    run_data["keyword_leaderboard"] = leaderboard
    workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "entries": len(leaderboard),
            "top_5": leaderboard[:5],
        },
    }


async def pipeline_save_contacts(
    project: str,
    run_id: str,
    contacts: list[dict],
    search_credits: int = 0,
    people_credits: int = 0,
    *,
    workspace=None,
) -> dict:
    """Deterministic save: contacts to BOTH contacts.json AND run file.

    Also updates run totals (credits, kpi_met). One tool call, no LLM needed.
    Fixes the persistent bug where contacts were in contacts.json but not run file.
    """
    import json
    workspace = workspace or _default_workspace()

    # 1. Save contacts.json
    workspace.save(project, "contacts.json", contacts)

    # 2. Load run file, update contacts + totals, write back
    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}

    run_data["contacts"] = contacts
    kpi_target = run_data.get("kpi", {}).get("target_people", 100)
    run_data["totals"] = {
        **run_data.get("totals", {}),
        "contacts_extracted": len(contacts),
        "kpi_met": len(contacts) >= kpi_target,
        "total_credits_people": people_credits,
        "total_credits": search_credits + people_credits,
    }

    workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "contacts_saved": len(contacts),
            "kpi_met": len(contacts) >= kpi_target,
            "kpi_target": kpi_target,
            "total_credits": search_credits + people_credits,
        },
    }


def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()
