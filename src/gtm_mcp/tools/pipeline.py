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


def _recover_run_data(run_data: dict) -> dict:
    """Recover from agent save_data(mode='write') overwrites.

    Pipeline tools save to protected keys (probe_companies, gather_companies,
    gather_requests). If agent overwrites 'companies' or 'requests' with empty
    data, this restores from the protected copies. Called before any read.
    """
    # Recover companies
    companies = run_data.get("companies", {})
    if not companies or len(companies) == 0:
        # Try protected keys in priority order
        for key in ("gather_companies", "probe_companies"):
            protected = run_data.get(key, {})
            if protected and len(protected) > 0:
                run_data["companies"] = protected
                logger.info("Recovered %d companies from %s", len(protected), key)
                break

    # Recover requests
    requests = run_data.get("requests", [])
    if not requests or len(requests) == 0:
        protected = run_data.get("gather_requests", [])
        if protected:
            run_data["requests"] = protected
            logger.info("Recovered %d requests from gather_requests", len(protected))

    return run_data


async def pipeline_probe(
    keywords: list[str],
    industry_tag_ids: list[str],
    locations: list[str],
    employee_ranges: list[str],
    funding_stages: list[str] | None = None,
    max_sample: int = 20,
    *,
    project: str,
    run_id: str,
    config=None,
    workspace=None,
) -> dict:
    """Deterministic probe — 6 Apollo searches + batch scrape in ONE call.

    Replaces: 6 agent apollo_search_companies calls + 1 scrape_batch call.
    Fires up to 3 keyword + 3 industry searches (page 1 only, 6 credits max).
    Scrapes ~20 sample companies. Saves everything to run file.

    Returns: breakdown per filter, scraped_texts for agent classification,
    total_available per filter (for cost estimation).

    After this, pipeline_gather_and_scrape will auto-skip probe companies
    (they're already in the run file → loaded into seen_domains).
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()
    api_key = config.get("apollo_api_key")
    apify_key = config.get("apify_proxy_password")
    if not api_key:
        return {"success": False, "error": "apollo_api_key not configured"}

    from gtm_mcp.tools.apollo import apollo_search_companies
    from gtm_mcp.tools.scraping import scrape_batch

    # 1. Fire up to 6 Apollo searches in parallel (3 keywords + 3 industries)
    probe_keywords = keywords[:3]
    probe_industries = industry_tag_ids[:3]
    seen = set()
    all_companies: dict[str, dict] = {}
    breakdown = []
    credits_used = 0

    async def search_one(filter_type, filter_value):
        nonlocal credits_used
        filters = {
            "organization_locations": locations,
            "organization_num_employees_ranges": employee_ranges,
        }
        if filter_type == "keyword":
            filters["q_organization_keyword_tags"] = [filter_value]
        else:
            filters["organization_industry_tag_ids"] = [filter_value]
        if funding_stages:
            filters["organization_latest_funding_stage_cd"] = funding_stages

        result = await apollo_search_companies(api_key, filters, page=1, per_page=100)
        credits_used += 1
        if not result.get("success"):
            return {"type": filter_type, "name": filter_value, "total": 0, "companies": 0}

        raw = result.get("companies", [])
        total_available = result.get("total_entries", result.get("pagination", {}).get("total_entries", len(raw)))
        new_count = 0
        for c in raw:
            domain = c.get("primary_domain", "") or c.get("domain", "")
            if not domain or domain in seen:
                continue
            seen.add(domain)
            new_count += 1
            all_companies[domain] = {
                "domain": domain,
                "name": c.get("name", ""),
                "apollo_id": c.get("apollo_id", "") or c.get("id", ""),
                "apollo_data": {
                    "industry": c.get("industry", ""),
                    "industry_tag_id": c.get("industry_tag_id", ""),
                    "employee_count": c.get("employee_count"),
                    "country": c.get("country", ""),
                    "city": c.get("city", ""),
                    "state": c.get("state", ""),
                    "founded_year": c.get("founded_year"),
                    "linkedin_url": c.get("linkedin_url", ""),
                    "keywords": c.get("keywords", []),
                    "funding_stage": c.get("funding_stage", ""),
                    "revenue": c.get("organization_revenue") or c.get("revenue", ""),
                    "phone": c.get("sanitized_phone", "") or c.get("phone", ""),
                    "headcount_6m_growth": c.get("organization_headcount_six_month_growth"),
                    "headcount_12m_growth": c.get("organization_headcount_twelve_month_growth"),
                },
                "discovery": {"found_by": f"{filter_type}:{filter_value}", "probe": True},
            }
        return {"type": filter_type, "name": filter_value, "total": total_available, "companies": new_count}

    tasks = []
    for kw in probe_keywords:
        tasks.append(search_one("keyword", kw))
    for tid in probe_industries:
        tasks.append(search_one("industry", tid))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, dict):
            breakdown.append(r)

    # 2. Scrape sample companies (deterministic batch)
    sample_domains = list(all_companies.keys())[:max_sample]
    scraped_texts = {}
    if sample_domains:
        urls = [f"https://{d}" for d in sample_domains]
        scrape_result = await scrape_batch(urls, apify_proxy_password=apify_key, max_concurrent=20)
        if scrape_result.get("success"):
            for entry in scrape_result.get("data", {}).get("results", []):
                if entry.get("success") and entry.get("url"):
                    domain = entry["url"].replace("https://", "").replace("http://", "").rstrip("/")
                    scraped_texts[domain] = (entry.get("text", ""))[:3000]

    # 3. Save to run file (so gather phase skips these domains)
    # Save probe companies under BOTH "companies" and "probe_companies" keys.
    # "probe_companies" survives even if agent later overwrites "companies" with mode="write".
    # pipeline_gather_and_scrape loads from BOTH to build seen_domains.
    probe_companies_dict = {
        d: {**c, "scrape": {
            "status": "success" if d in scraped_texts else "not_scraped",
            "text_length": len(scraped_texts.get(d, "")),
        }} for d, c in all_companies.items()
    }

    if workspace and project and run_id:
        run_path = f"runs/{run_id}.json"
        run_data = workspace.load(project, run_path) or {}
        run_data["probe"] = {
            "credits_used": credits_used,
            "companies_from_probe": len(all_companies),
            "breakdown": breakdown,
        }
        run_data["probe_companies"] = probe_companies_dict  # survives agent overwrite
        run_data["companies"] = {**run_data.get("companies", {}), **probe_companies_dict}
        # Probe credits also count as search credits (probe IS the search when gather is skipped)
        run_data["totals"] = {
            **run_data.get("totals", {}),
            "total_credits_probe": credits_used,
            "total_credits_search": credits_used,  # probe = search when gather skipped; gather overwrites if called
            "total_credits": credits_used,
        }
        # Save probe requests so leaderboard works even without gather
        # Tagged with _from_probe so gather merge preserves them
        if not run_data.get("requests"):
            run_data["requests"] = [
                {"type": b["type"], "filter_value": b["name"], "page": 1, "funded": False, "_from_probe": True,
                 "result": {"credits_used": 1, "raw_returned": b["total"], "new_unique": b["companies"]}}
                for b in breakdown
            ]
        workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "credits_used": credits_used,
            "companies_found": len(all_companies),
            "companies_scraped": len(scraped_texts),
            "breakdown": breakdown,
            "scraped_texts": scraped_texts,
            "sample_domains": sample_domains,
        },
    }


async def pipeline_gather_and_scrape(
    keywords: list[str],
    industry_tag_ids: list[str],
    locations: list[str],
    employee_ranges: list[str],
    funding_stages: list[str] | None = None,
    max_companies: int = 400,
    scrape_concurrent: int = 100,
    max_pages_per_stream: int = 5,
    keyword_start_pages: dict[str, int] | None = None,
    max_credits: int | None = None,
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
    # Load existing companies from run file (probe phase saves here first)
    # --- DETERMINISTIC: Load all prior state from run files (agent can't mess this up) ---
    _probe_unscraped: list[str] = []
    _probe_companies_data: dict[str, dict] = {}
    _auto_start_pages: dict[str, int] = {}  # computed from ALL run requests

    if workspace and project:
        project_dir = workspace.base / "projects" / project
        runs_dir = project_dir / "runs"

        # Scan ALL run files to build complete seen_domains + keyword page history
        if runs_dir.exists():
            import json as _json
            for rf in sorted(runs_dir.glob("run-*.json")):
                try:
                    rd = _json.loads(rf.read_text())
                except Exception:
                    continue
                # Collect all domains from all runs (dedup across runs)
                for key in ("companies", "probe_companies"):
                    if isinstance(rd.get(key), dict):
                        for d, c in rd[key].items():
                            seen_domains.add(d)
                            # For CURRENT run: track probe companies for scraping
                            if rf.stem == run_id:
                                if d not in _probe_companies_data:
                                    _probe_companies_data[d] = c
                                if c.get("scrape", {}).get("status") in ("not_scraped", None):
                                    if d not in _probe_unscraped:
                                        _probe_unscraped.append(d)
                # Build keyword page map from ALL runs' requests
                for req in rd.get("requests", []):
                    kw = req.get("filter_value", "")
                    page = req.get("page", 1)
                    if kw and page >= _auto_start_pages.get(kw, 0):
                        _auto_start_pages[kw] = page + 1  # next page to fetch

            if seen_domains:
                logger.info("Loaded %d seen domains from %d run files, %d keyword start pages",
                           len(seen_domains), len(list(runs_dir.glob("run-*.json"))),
                           len(_auto_start_pages))

    companies: dict[str, dict] = {}
    # Pre-populate companies with probe data (so scrape results update them)
    for d, c in _probe_companies_data.items():
        companies[d] = c
    requests: list[dict] = []
    scrape_queue: asyncio.Queue = asyncio.Queue()
    scrape_results: dict[str, dict] = {}
    gather_done = asyncio.Event()
    req_counter = 0
    # max_companies means NEW companies to find (on top of existing)
    # Offset seen_domains count so the cap applies to new discoveries only
    _seen_before_gather = len(seen_domains)
    _effective_max = _seen_before_gather + max_companies

    # --- Phase 1: Apollo gather (CONTROLLED concurrency, feeds scrape queue) ---
    # Semaphore limits concurrent Apollo API calls. Without this, asyncio.gather
    # fires ALL coroutines at once — 350 API calls before any counter check can stop them.
    # With semaphore(10), only 10 run at a time. After each batch completes,
    # req_counter and seen_domains are updated, so the next batch can stop early.
    _apollo_sem = asyncio.Semaphore(10)

    # DETERMINISTIC: merge agent-provided start pages with auto-computed from run history
    # Auto-computed wins (it's from actual data), agent value is fallback for edge cases
    _start_pages = {**(keyword_start_pages or {}), **_auto_start_pages}

    async def search_one(filter_type: str, filter_value: str, funded: bool = False):
        nonlocal req_counter
        start = _start_pages.get(filter_value, 1)
        for page in range(start, start + max_pages_per_stream):
            # Check BEFORE acquiring semaphore (fast exit for late coroutines)
            if len(seen_domains) >= _effective_max:
                return
            if max_credits is not None and max_credits > 0 and req_counter >= max_credits:
                return

            async with _apollo_sem:
                # Re-check AFTER acquiring semaphore (counters updated while waiting)
                if len(seen_domains) >= _effective_max:
                    return
                if max_credits is not None and max_credits > 0 and req_counter >= max_credits:
                    return

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
                    return

                raw_companies = result.get("companies", [])
                if not raw_companies:
                    return  # exhausted

                new_unique = 0
                for c in raw_companies:
                    domain = c.get("primary_domain", "") or c.get("domain", "")
                    if not domain or domain in seen_domains:
                        continue
                    if len(seen_domains) >= _effective_max:
                        break
                    seen_domains.add(domain)
                    new_unique += 1
                    # Apollo search returns sparse data with non-standard keys.
                    # Map what's available; enrichment backfills the rest for targets.
                    companies[domain] = {
                        "domain": domain,
                        "name": c.get("name", ""),
                        "apollo_id": c.get("apollo_id", "") or c.get("id", ""),
                        "apollo_data": {
                            "industry": c.get("industry", ""),
                            "industry_tag_id": c.get("industry_tag_id", ""),
                            "employee_count": c.get("employee_count"),
                            "country": c.get("country", ""),
                            "city": c.get("city", ""),
                            "state": c.get("state", ""),
                            "founded_year": c.get("founded_year"),
                            "linkedin_url": c.get("linkedin_url", ""),
                            "keywords": c.get("keywords", []),
                            "funding_stage": c.get("funding_stage", ""),
                            "revenue": c.get("organization_revenue") or c.get("revenue", ""),
                            "phone": c.get("sanitized_phone", "") or c.get("phone", ""),
                            "headcount_6m_growth": c.get("organization_headcount_six_month_growth"),
                            "headcount_12m_growth": c.get("organization_headcount_twelve_month_growth"),
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
                    return
                # Exhausted: page returned companies but 0 new unique → stop
                if new_unique == 0:
                    return

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
        # Feed unscraped probe companies into scrape queue FIRST
        for d in _probe_unscraped:
            await scrape_queue.put(d)
        if _probe_unscraped:
            logger.info("Queued %d unscraped probe companies for scraping", len(_probe_unscraped))
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
    # PROTECTED: saves to BOTH "companies" AND "gather_companies" (survives agent overwrite)
    # Same pattern as probe_companies — agent can't destroy this data.
    if project and run_id and workspace:
        run_path = f"runs/{run_id}.json"
        existing_run = workspace.load(project, run_path) or {}
        existing_run["companies"] = response_companies
        existing_run["gather_companies"] = response_companies  # survives save_data(mode="write")
        # Merge requests: keep probe requests, add gather requests. Also save protected copy.
        probe_requests = [r for r in existing_run.get("requests", []) if r.get("_from_probe")]
        merged_requests = probe_requests + requests
        existing_run["requests"] = merged_requests
        existing_run["gather_requests"] = merged_requests  # survives save_data(mode="write")
        # Read probe credits from run file (agent saved them before calling this tool)
        probe_credits = existing_run.get("probe", {}).get("credits_used", 0)
        existing_run["totals"] = {
            **existing_run.get("totals", {}),
            "rounds_completed": len(existing_run.get("rounds", [])) or 1,
            "total_api_requests": len(requests),
            "total_credits_probe": probe_credits,
            "total_credits_search": total_credits,
            "total_credits": probe_credits + total_credits,  # running total (people added later)
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
    run_data = _recover_run_data(run_data)

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
                "last_page": 0,
                "exhausted": False,
            }
        s = keyword_stats[key]
        s["credits_used"] += req.get("result", {}).get("credits_used", 1)
        s["unique_companies"] += req.get("result", {}).get("new_unique", 0)
        page = req.get("page", 1)
        if page > s["last_page"]:
            s["last_page"] = page
        # Mark exhausted if page returned <100 results (no more pages to fetch)
        raw = req.get("result", {}).get("raw_returned", 0)
        if raw < 100:
            s["exhausted"] = True

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
            "next_page": s["last_page"] + 1 if not s["exhausted"] else None,
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


async def pipeline_import_blacklist(
    project: str,
    campaign_id: int,
    *,
    config=None,
    workspace=None,
) -> dict:
    """Export leads from SmartLead campaign + save as project-level blacklist. One call.

    Deterministic. Zero LLM. Guarantees blacklist exists before pipeline_gather_and_scrape.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    from gtm_mcp.tools.smartlead import smartlead_export_leads
    result = await smartlead_export_leads(campaign_id, config=config)
    if not result.get("success"):
        return {"success": False, "error": f"Export failed: {result.get('error')}"}

    leads = result["data"].get("leads", [])
    domains = result["data"].get("domains", [])

    # Save as project-level blacklist
    now = datetime.now(timezone.utc).isoformat()
    bl = {d: {"source": "smartlead_campaign", "campaign_id": campaign_id, "blacklisted_at": now}
          for d in domains}
    workspace.save(project, "blacklist.json", bl)

    return {
        "success": True,
        "data": {
            "campaign_id": campaign_id,
            "leads_exported": len(leads),
            "domains_blacklisted": len(domains),
        },
    }


async def pipeline_save_intelligence(
    project: str,
    run_id: str,
    *,
    workspace=None,
) -> dict:
    """Save cross-run intelligence from run's keyword leaderboard. Zero LLM.

    Updates ~/.gtm-mcp/filter_intelligence.json with keyword quality scores
    and segment playbooks. Future runs start with proven keywords.
    """
    workspace = workspace or _default_workspace()

    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file not found"}

    leaderboard = run_data.get("keyword_leaderboard", [])
    if not leaderboard:
        return {"success": False, "error": "No keyword_leaderboard in run file. Call pipeline_compute_leaderboard first."}

    # Load or create global intelligence file
    import json
    intel_path = workspace.base / "filter_intelligence.json"
    if intel_path.exists():
        intel = json.loads(intel_path.read_text())
    else:
        intel = {"keyword_knowledge": {}, "segment_playbooks": {}}

    # Update keyword knowledge
    kk = intel.get("keyword_knowledge", {})
    for entry in leaderboard:
        key = entry.get("filter_value", "")
        if not key:
            continue
        if key in kk:
            old = kk[key]
            old["times_used"] = old.get("times_used", 0) + 1
            old["avg_target_rate"] = (old.get("avg_target_rate", 0) + entry.get("target_rate", 0)) / 2
            if entry.get("quality_score", 0) > old.get("best_quality_score", 0):
                old["best_quality_score"] = entry["quality_score"]
        else:
            kk[key] = {
                "type": entry.get("type", "keyword"),
                "times_used": 1,
                "avg_target_rate": entry.get("target_rate", 0),
                "best_quality_score": entry.get("quality_score", 0),
                "unique_companies": entry.get("unique_companies", 0),
            }
    intel["keyword_knowledge"] = kk

    # Update segment playbook
    project_data = workspace.load(project, "project.yaml")
    if project_data:
        segments = project_data.get("offer", project_data).get("segments", [])
        if segments:
            seg_name = segments[0].get("name", "UNKNOWN") if isinstance(segments[0], dict) else str(segments[0])
            top_keywords = [e["filter_value"] for e in leaderboard[:10] if e.get("quality_score", 0) > 0]
            intel.setdefault("segment_playbooks", {})[seg_name] = {
                "best_keywords": top_keywords,
                "avg_target_rate": sum(e.get("target_rate", 0) for e in leaderboard) / max(len(leaderboard), 1),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

    intel_path.write_text(json.dumps(intel, indent=2, ensure_ascii=False))

    return {
        "success": True,
        "data": {
            "keywords_updated": len(kk),
            "segment_playbooks": list(intel.get("segment_playbooks", {}).keys()),
        },
    }


async def pipeline_save_contacts(
    project: str,
    run_id: str,
    contacts: list[dict],
    people_credits: int = 0,
    *,
    workspace=None,
    **_kwargs,  # absorb deprecated search_credits if agent passes it
) -> dict:
    """Deterministic save: contacts to BOTH contacts.json AND run file.

    Credits are computed FROM the run file, never passed by the agent.
    total_credits = probe + search + people (always correct, agent can't mess it up).
    Also marks people_extracted on companies for Phase 0 reuse.
    """
    workspace = workspace or _default_workspace()

    # 1. Save contacts.json — MERGE with existing (don't overwrite on continuation runs)
    existing_contacts = workspace.load(project, "contacts.json") or []
    existing_emails = {c.get("email") for c in existing_contacts if c.get("email")}
    new_contacts = [c for c in contacts if c.get("email") not in existing_emails]
    merged = existing_contacts + new_contacts
    workspace.save(project, "contacts.json", merged)

    # 2. Load run file, update contacts + totals, write back
    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}
    run_data = _recover_run_data(run_data)

    run_data["contacts"] = contacts
    kpi_target = run_data.get("kpi", {}).get("target_people", 100)

    # Mark companies that had people extracted (for Phase 0 reuse in future runs)
    enriched_domains = {c.get("company_domain") for c in contacts if c.get("company_domain")}
    companies = run_data.get("companies", {})
    for domain in enriched_domains:
        if domain in companies:
            companies[domain]["people_extracted"] = True

    # Credits computed FROM run file — never passed by agent
    existing_totals = run_data.get("totals", {})
    probe_credits = existing_totals.get("total_credits_probe",
                      run_data.get("probe", {}).get("credits_used", 0))
    search_credits = existing_totals.get("total_credits_search", 0)
    total = probe_credits + search_credits + people_credits

    run_data["totals"] = {
        **existing_totals,
        "contacts_extracted": len(contacts),
        "kpi_met": len(contacts) >= kpi_target,
        "total_credits_probe": probe_credits,
        "total_credits_people": people_credits,
        "total_credits": total,
    }

    workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "contacts_saved": len(contacts),
            "kpi_met": len(contacts) >= kpi_target,
            "kpi_target": kpi_target,
            "total_credits": total,
            "credits_breakdown": {
                "probe": probe_credits,
                "search": search_credits,
                "people": people_credits,
            },
        },
    }


async def pipeline_prepare_continuation(
    project: str,
    campaign_ref: str,
    additional_kpi: int = 50,
    contacts_per_company: int = 3,
    *,
    workspace=None,
) -> dict:
    """Deterministic continuation state builder. ONE call, ZERO LLM.

    Reads previous run state and returns everything needed for "gather more":
    - unused_targets (classified but not enriched — FREE to harvest)
    - keyword_start_pages (skip already-fetched pages)
    - dynamic_scaling (max_companies, max_credits from KPI + target_rate)
    - phase_0_sufficient (can unused targets alone cover KPI?)

    The agent uses this to decide: Phase 0 only, or Phase 0 + new gather.
    """
    import math
    workspace = workspace or _default_workspace()

    # 1. Find campaign
    campaign_data = None
    campaign_slug = None
    campaign_id = None

    # Try by ID
    if campaign_ref.isdigit():
        campaign_id = int(campaign_ref)
    # Try by slug — scan campaigns dirs
    project_dir = workspace.base / "projects" / project
    campaigns_dir = project_dir / "campaigns"
    if campaigns_dir.exists():
        for slug_dir in campaigns_dir.iterdir():
            camp_file = slug_dir / "campaign.yaml"
            if camp_file.exists():
                import yaml
                camp = yaml.safe_load(camp_file.read_text())
                if (campaign_ref.isdigit() and camp.get("campaign_id") == campaign_id) or \
                   camp.get("slug") == campaign_ref or camp.get("name") == campaign_ref:
                    campaign_data = camp
                    campaign_slug = camp.get("slug") or slug_dir.name
                    campaign_id = camp.get("campaign_id")
                    break

    if not campaign_data:
        return {"success": False, "error": f"Campaign '{campaign_ref}' not found in project {project}"}

    # 2. Load last run
    run_ids = campaign_data.get("run_ids", [])
    last_run = None
    last_run_id = None
    if run_ids:
        last_run_id = run_ids[-1]
        last_run = workspace.load(project, f"runs/{last_run_id}.json")

    if not last_run:
        # Scan for any run file
        runs_dir = project_dir / "runs"
        if runs_dir.exists():
            run_files = sorted(runs_dir.glob("run-*.json"))
            if run_files:
                last_run_id = run_files[-1].stem
                import json
                last_run = json.loads(run_files[-1].read_text())

    if not last_run:
        return {"success": False, "error": f"No previous run found for project {project}"}
    last_run = _recover_run_data(last_run)

    # 3. DETERMINISTIC: Scan campaign's run files for complete intelligence
    # Only runs linked to THIS campaign — different campaigns have different segments/keywords
    import json as _json
    campaign_run_ids = set(campaign_data.get("run_ids", []))
    # Also include the last_run if not already in the list
    if last_run_id:
        campaign_run_ids.add(last_run_id)
    all_companies: dict[str, dict] = {}  # domain → company (merged from campaign runs)
    all_run_requests: list[dict] = []
    all_filter_snapshots: list[dict] = []

    if (project_dir / "runs").exists():
        for rf in sorted((project_dir / "runs").glob("run-*.json")):
            if rf.stem not in campaign_run_ids:
                continue  # skip runs from other campaigns
            try:
                rd = _json.loads(rf.read_text())
                rd = _recover_run_data(rd)
            except Exception:
                continue
            # Merge companies (later runs overwrite earlier for same domain)
            for d, c in rd.get("companies", {}).items():
                if d not in all_companies or c.get("classification"):
                    all_companies[d] = c
            all_run_requests.extend(rd.get("requests", []))
            all_filter_snapshots.extend(rd.get("filter_snapshots", []))

    # 4. Find unused targets across ALL runs — targets classified but never enriched
    all_contacts = workspace.load(project, "contacts.json") or []
    all_contact_domains = {c.get("company_domain") for c in all_contacts if c.get("company_domain")}
    all_contact_emails = {c.get("email") for c in all_contacts if c.get("email")}

    unused_targets = []
    for domain, comp in all_companies.items():
        cls = comp.get("classification", {})
        if cls.get("is_target") and not comp.get("people_extracted") and domain not in all_contact_domains:
            unused_targets.append(domain)

    # 5. Build keyword intelligence from ALL requests (deterministic)
    keyword_start_pages = {}
    exhausted_keywords = []
    fired_keywords = set()
    # Track per-keyword quality: unique companies and targets found
    keyword_stats_map: dict[str, dict] = {}  # kw → {unique, targets, credits, pages}

    for req in all_run_requests:
        kw = req.get("filter_value", "")
        if not kw:
            continue
        page = req.get("page", 1)
        fired_keywords.add(kw)
        if page >= keyword_start_pages.get(kw, 0):
            keyword_start_pages[kw] = page + 1
        raw = req.get("result", {}).get("raw_returned", 100)
        new_unique = req.get("result", {}).get("new_unique", 0)
        if raw < 100 and kw not in exhausted_keywords:
            exhausted_keywords.append(kw)
        # Accumulate keyword quality stats
        if kw not in keyword_stats_map:
            keyword_stats_map[kw] = {"unique": 0, "targets": 0, "credits": 0, "pages": []}
        keyword_stats_map[kw]["unique"] += new_unique
        keyword_stats_map[kw]["credits"] += 1
        keyword_stats_map[kw]["pages"].append(page)

    # Count targets per keyword from company discovery data
    for domain, comp in all_companies.items():
        found_by = comp.get("discovery", {}).get("found_by", "")
        if ":" in found_by:
            kw = found_by.split(":", 1)[1]
            if kw in keyword_stats_map and comp.get("classification", {}).get("is_target"):
                keyword_stats_map[kw]["targets"] += 1

    # Compute quality_score per keyword: target_rate × log(unique+1) / credits
    import math
    for kw, stats in keyword_stats_map.items():
        uc = stats["unique"]
        targets = stats["targets"]
        credits = max(stats["credits"], 1)
        target_rate_kw = targets / uc if uc > 0 else 0
        stats["target_rate"] = round(target_rate_kw, 3)
        stats["quality_score"] = round(target_rate_kw * math.log(uc + 1) / credits, 4) if uc > 0 else 0

    # Also merge leaderboard data from last run
    leaderboard = last_run.get("keyword_leaderboard", [])
    sorted_lb = sorted(leaderboard, key=lambda x: -x.get("quality_score", 0))
    for entry in sorted_lb:
        kw = entry.get("filter_value", "")
        fired_keywords.add(kw)
        if entry.get("exhausted") and kw not in exhausted_keywords:
            exhausted_keywords.append(kw)

    # 6. Get filters — scan ALL filter snapshots, use latest
    last_filters = all_filter_snapshots[-1].get("filters", {}) if all_filter_snapshots else {}

    # Build OPTIMIZED keyword order:
    # 1. Never-fired (fresh, zero cost)
    # 2. Best performers with more pages (sorted by quality_score — target_rate × volume)
    # 3. Remaining with more pages
    all_keywords = last_filters.get("keywords", [])
    never_fired = [k for k in all_keywords if k not in fired_keywords and k not in exhausted_keywords]
    exhausted_set = set(exhausted_keywords)
    has_more_pages = [kw for kw in fired_keywords if kw not in exhausted_set]
    # Sort by quality_score (best target-producing keywords first)
    has_more_pages.sort(key=lambda kw: -keyword_stats_map.get(kw, {}).get("quality_score", 0))
    optimized_keywords = never_fired + has_more_pages

    # 7. Compute dynamic scaling from ALL runs' actual data
    companies = all_companies  # use merged data
    classified = [c for c in companies.values() if c.get("classification")]
    targets_from_data = [c for c in classified if c.get("classification", {}).get("is_target")]
    targets_count = len(targets_from_data)
    classified_count = len(classified)
    target_rate = targets_count / classified_count if classified_count > 0 else 0.35
    scrape_loss = 0.85
    last_totals = last_run.get("totals", {})

    needed_companies = math.ceil(additional_kpi / contacts_per_company)
    phase_0_sufficient = len(unused_targets) >= needed_companies

    # If Phase 0 doesn't cover it, compute gather params for the delta
    if not phase_0_sufficient:
        delta_needed = needed_companies - len(unused_targets)
        gather_companies = max(50, math.ceil(delta_needed / target_rate / scrape_loss * 1.5))
    else:
        gather_companies = 0

    # 7. Compute new run_id
    existing_runs = sorted((project_dir / "runs").glob("run-*.json")) if (project_dir / "runs").exists() else []
    next_num = len(existing_runs) + 1
    new_run_id = f"run-{next_num:03d}"

    # 8. Collect all seen domains for dedup
    seen_domains = set()
    for run_file in existing_runs:
        try:
            import json
            rd = json.loads(run_file.read_text())
            seen_domains.update(rd.get("companies", {}).keys())
        except Exception:
            pass

    return {
        "success": True,
        "data": {
            "project": project,
            "campaign_id": campaign_id,
            "campaign_slug": campaign_slug,
            "prev_run_id": last_run_id,
            "new_run_id": new_run_id,
            "unused_targets": {
                "count": len(unused_targets),
                "domains": unused_targets[:200],  # cap response size
                "estimated_contacts": len(unused_targets) * contacts_per_company,
            },
            "continuation_filters": last_filters,
            "optimized_keywords": optimized_keywords,  # ORDERED: never-fired first, then best performers
            "keyword_start_pages": keyword_start_pages,  # {kw: page} for keywords that need page 2+
            "keyword_stats": {
                "total_generated": len(all_keywords),
                "fired": len(fired_keywords),
                "never_fired": len(never_fired),
                "exhausted": len(exhausted_keywords),
                "has_more_pages": len(has_more_pages),
            },
            "exhausted_keywords": exhausted_keywords,
            "seen_domains_count": len(seen_domains),
            "dynamic_scaling": {
                "kpi": additional_kpi,
                "target_rate": round(target_rate, 3),
                "max_companies": gather_companies,
                "max_credits": max(50, math.ceil(additional_kpi * 2)),
            },
            "phase_0_sufficient": phase_0_sufficient,
            "previous_totals": {
                "credits": last_totals.get("total_credits", 0),
                "contacts": last_totals.get("contacts_extracted", 0),
            },
        },
    }


async def pipeline_people_to_push(
    project: str,
    run_id: str,
    campaign_name: str,
    sending_account_ids: list[int],
    country: str,
    segment: str,
    sequence_steps: list[dict],
    test_email: str = "",
    max_people_per_company: int = 3,
    person_seniorities: list[str] | None = None,
    create_sheet: bool = True,
    mode: str = "create",
    existing_campaign_id: int | None = None,
    include_domains: list[str] | None = None,
    exclude_emails: list[str] | None = None,
    *,
    config=None,
    workspace=None,
) -> dict:
    """Atomic post-classification → SmartLead ready. ONE call, ZERO LLM.

    After classification is done (targets in run file), this tool does EVERYTHING:
    1. Load target domains from run file (or use include_domains for Phase 0)
    2. apollo_search_people_batch — search all targets (FREE)
    3. apollo_enrich_people — bulk enrich (1 credit per verified email)
    4. Save contacts to contacts.json + run file + update totals
    5. Export to Google Sheet (optional)
    6. Push to SmartLead — create campaign OR append to existing
    7. Update campaign.yaml + run file with campaign data

    mode: "create" → new campaign via campaign_push
          "append" → add leads to existing_campaign_id via smartlead_add_leads
    include_domains: Phase 0 — override target lookup with specific domains (unused targets)
    exclude_emails: Mode 3 — skip emails already in campaign

    Replaces 6+ separate tool calls. Eliminates all agent decisions post-classification.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    # 1. Load targets from run file or use include_domains (Phase 0)
    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}
    run_data = _recover_run_data(run_data)

    companies = run_data.get("companies", {})

    if include_domains:
        target_domains = include_domains
    else:
        target_domains = [d for d, c in companies.items()
                          if c.get("classification", {}).get("is_target")]

    if not target_domains:
        return {"success": False, "error": "No target companies found in run file"}

    logger.info("pipeline_people_to_push: %d targets from %d companies", len(target_domains), len(companies))

    # 2. Search people (FREE — no credits)
    from gtm_mcp.tools.apollo import apollo_search_people_batch, apollo_enrich_people

    api_key = config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "apollo_api_key not configured"}

    seniorities = person_seniorities or ["c_suite", "vp", "head", "director", "manager"]
    search_result = await apollo_search_people_batch(
        api_key, target_domains, person_seniorities=seniorities, per_page=10,
    )
    if not search_result.get("success"):
        return {"success": False, "error": f"People search failed: {search_result.get('error')}", "step": "search"}

    # 3. Collect top N person IDs per company, then enrich
    all_person_ids = []
    search_data = search_result.get("data", search_result)  # handle both {data:{results:[]}} and {results:[]}
    for entry in search_data.get("results", []):
        people = entry.get("people", [])
        top_n = [p.get("id") for p in people[:max_people_per_company] if p.get("id")]
        all_person_ids.extend(top_n)

    if not all_person_ids:
        return {"success": False, "error": "No people found across target companies", "step": "search",
                "targets_searched": len(target_domains)}

    enrich_result = await apollo_enrich_people(api_key, all_person_ids)
    if not enrich_result.get("success"):
        return {"success": False, "error": f"Enrichment failed: {enrich_result.get('error')}", "step": "enrich"}

    # Build contacts list — apollo_enrich_people returns "matches" key
    contacts = []
    _seen_emails: set[str] = set()  # dedup within batch
    for person in enrich_result.get("matches", enrich_result.get("people", [])):
        if not person.get("email"):
            continue
        email_lower = person["email"].lower()
        if email_lower in _seen_emails:
            continue  # skip duplicate within same enrichment batch
        _seen_emails.add(email_lower)
        domain = person.get("company_domain", "") or person.get("org_domain", "")
        if not domain:
            continue  # skip contacts with no company domain (orphan enrichment results)
        org = person.get("org_data", {})
        contacts.append({
            "email": person["email"],
            "first_name": person.get("first_name", ""),
            "last_name": person.get("last_name", ""),
            "name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
            "title": person.get("title", ""),
            "seniority": person.get("seniority", ""),
            "linkedin_url": person.get("linkedin_url", ""),
            "phone": person.get("phone", ""),
            "company_domain": domain,
            "company_name_normalized": person.get("company_name", "")
                or companies.get(domain, {}).get("name", ""),
            "segment": companies.get(domain, {}).get(
                "classification", {}).get("segment", segment),
            # Store org_data directly on contact — sheet uses this WITHOUT run file join
            "org_data": {
                "industry": org.get("industry", ""),
                "country": org.get("country", ""),
                "city": org.get("city", ""),
                "employee_count": org.get("employee_count"),
                "keywords": org.get("keywords", []),
                "revenue": org.get("revenue"),
                "founded_year": org.get("founded_year"),
                "funding_stage": org.get("funding_stage", ""),
            },
        })

    # Filter out emails already in campaign (Mode 3 dedup)
    if exclude_emails:
        exclude_set = set(e.lower() for e in exclude_emails)
        before = len(contacts)
        contacts = [c for c in contacts if c["email"].lower() not in exclude_set]
        logger.info("pipeline_people_to_push: deduped %d → %d contacts (excluded %d campaign emails)",
                     before, len(contacts), before - len(contacts))

    # Credits = person IDs sent to enrich (you pay per request, not per email returned)
    people_credits = len(all_person_ids)
    logger.info("pipeline_people_to_push: %d contacts from %d enrichment requests (%d credits)", len(contacts), people_credits, people_credits)

    # 3b. Backfill company apollo_data from enrichment org_data
    # Apollo search returns sparse data (null for most fields).
    # Enrichment returns FULL org data (39 fields) — use it to fill gaps.
    enriched_people = enrich_result.get("matches", enrich_result.get("people", []))
    for person in enriched_people:
        domain = person.get("company_domain", "")
        org_data = person.get("org_data", {})
        if domain and domain in companies and org_data:
            existing_ad = companies[domain].get("apollo_data", {})
            # Only overwrite empty fields — don't lose data from search
            for key, val in org_data.items():
                if val and not existing_ad.get(key):
                    existing_ad[key] = val
            companies[domain]["apollo_data"] = existing_ad
            # Also update name if missing
            if not companies[domain].get("name") and person.get("company_name"):
                companies[domain]["name"] = person["company_name"]

    # Save updated companies back to run file
    run_data["companies"] = companies
    workspace.save(project, run_path, run_data)

    # 4. Save contacts (atomic — credits computed FROM run file, not passed)
    await pipeline_save_contacts(project, run_id, contacts, people_credits,
                                 workspace=workspace)

    # Reload run_data after save_contacts updated totals
    run_data = workspace.load(project, run_path) or run_data

    # 5. Google Sheet export (optional)
    # On append: reuse existing sheet_id from campaign.yaml (so URL stays the same)
    sheet_url = ""
    if create_sheet and contacts:
        from gtm_mcp.tools.sheets import sheets_export_contacts
        existing_sheet_id = ""
        if mode == "append" and existing_campaign_id:
            # Find campaign.yaml to get sheet_id
            import re as _re
            slug_guess = _re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")
            camp_data = workspace.load(project, f"campaigns/{slug_guess}/campaign.yaml")
            if camp_data:
                existing_sheet_id = camp_data.get("sheet_id", "")
        sheet_result = await sheets_export_contacts(
            project, sheet_id=existing_sheet_id, config=config, workspace=workspace)
        if sheet_result.get("success"):
            sheet_url = sheet_result["data"].get("sheet_url", "")
            # Store sheet_id in campaign for future reuse
            sheet_id_val = sheet_result["data"].get("sheet_id", "")
            if sheet_id_val and mode == "create":
                import re as _re
                slug_guess = _re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")
                camp_data = workspace.load(project, f"campaigns/{slug_guess}/campaign.yaml")
                if camp_data:
                    camp_data["sheet_id"] = sheet_id_val
                    camp_data["sheet_url"] = sheet_url
                    workspace.save(project, f"campaigns/{slug_guess}/campaign.yaml", camp_data)

    # 6. Save leads file for push
    import json
    leads = []
    for c in contacts:
        leads.append({
            "email": c["email"],
            "first_name": c.get("first_name", ""),
            "last_name": c.get("last_name", ""),
            "company_name": c.get("company_name_normalized", ""),
            "linkedin_url": c.get("linkedin_url", ""),
            "phone": c.get("phone", ""),
        })
    workspace.save(project, "leads_for_push.json", leads)

    # 7. Campaign push
    campaign_id = None
    campaign_slug = None
    leads_uploaded = 0

    if mode == "create":
        from gtm_mcp.tools.campaign_push import campaign_push
        push_result = await campaign_push(
            project, campaign_name, sending_account_ids, country, segment,
            sequence_steps, "leads_for_push.json", test_email, run_id=run_id,
            config=config, workspace=workspace,
        )
        if push_result.get("success"):
            campaign_id = push_result["data"]["campaign_id"]
            campaign_slug = push_result["data"]["campaign_slug"]
            leads_uploaded = push_result["data"]["leads_uploaded"]
            # Save sheet_id to campaign.yaml for future append reuse
            if sheet_url:
                camp_data = workspace.load(project, f"campaigns/{campaign_slug}/campaign.yaml")
                if camp_data:
                    sheet_id_from_url = sheet_url.split("/d/")[1].split("/")[0] if "/d/" in sheet_url else ""
                    camp_data["sheet_id"] = sheet_id_from_url
                    camp_data["sheet_url"] = sheet_url
                    workspace.save(project, f"campaigns/{campaign_slug}/campaign.yaml", camp_data)
        else:
            logger.error("Campaign creation failed: %s", push_result.get("error"))
            return {"success": False, "error": f"Campaign creation failed: {push_result.get('error')}",
                    "step": "campaign_push", "contacts_saved": len(contacts)}
    elif mode == "append" and not existing_campaign_id:
        return {"success": False, "error": "mode='append' requires existing_campaign_id",
                "step": "campaign_push", "contacts_saved": len(contacts)}
    elif mode == "append":
        from gtm_mcp.tools.smartlead import smartlead_add_leads, smartlead_export_leads
        campaign_id = existing_campaign_id
        # DETERMINISTIC dedup: fetch actual emails from SmartLead, remove duplicates BEFORE push
        existing_emails_sl = set()
        try:
            export = await smartlead_export_leads(existing_campaign_id, config=config)
            if export.get("success"):
                for lead in export.get("data", {}).get("leads", []):
                    email = lead.get("email", "").lower()
                    if email:
                        existing_emails_sl.add(email)
                logger.info("Fetched %d existing emails from SmartLead for dedup", len(existing_emails_sl))
        except Exception:
            pass
        before_dedup = len(leads)
        if existing_emails_sl:
            leads = [l for l in leads if l.get("email", "").lower() not in existing_emails_sl]
            if before_dedup != len(leads):
                logger.info("Deduped %d → %d leads (removed %d already in SmartLead)",
                           before_dedup, len(leads), before_dedup - len(leads))
        # Single API call with retry — SmartLead upload is FREE, never lose contacts
        leads_uploaded = 0
        if leads:
            for attempt in range(3):
                add_result = await smartlead_add_leads(existing_campaign_id, leads, config=config)
                if add_result.get("success"):
                    leads_uploaded = len(leads)
                    break
                if attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))
        if leads_uploaded > 0:
            # Update campaign.yaml
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")
            campaign_slug = slug
            existing_camp = workspace.load(project, f"campaigns/{slug}/campaign.yaml")
            if existing_camp:
                existing_camp["total_leads_pushed"] = existing_camp.get("total_leads_pushed", 0) + len(leads)
                existing_run_ids = existing_camp.get("run_ids", [])
                if run_id not in existing_run_ids:
                    existing_run_ids.append(run_id)
                existing_camp["run_ids"] = existing_run_ids
                workspace.save(project, f"campaigns/{slug}/campaign.yaml", existing_camp)

        # Update run file with campaign link
        run_data = workspace.load(project, run_path) or {}
        run_data["campaign_id"] = campaign_id
        run_data["campaign_slug"] = campaign_slug
        from datetime import datetime, timezone as tz
        run_data["campaign"] = {
            "campaign_id": campaign_id,
            "leads_pushed": leads_uploaded,
            "pushed_at": datetime.now(tz.utc).isoformat(),
        }
        workspace.save(project, run_path, run_data)

    # Read final totals from run file (authoritative after save_contacts)
    final_totals = run_data.get("totals", {})

    # Compute CAMPAIGN totals across ALL runs (so agent shows full breakdown)
    campaign_totals = {"probe": 0, "search": 0, "people": 0, "total": 0,
                       "contacts": 0, "per_run": []}
    camp_data = workspace.load(project, f"campaigns/{campaign_slug}/campaign.yaml") if campaign_slug else None
    camp_run_ids = (camp_data or {}).get("run_ids", [run_id])
    for rid in camp_run_ids:
        rd = workspace.load(project, f"runs/{rid}.json")
        if rd:
            rt = rd.get("totals", {})
            rp = rt.get("total_credits_probe", 0)
            rs = rt.get("total_credits_search", 0)
            rpe = rt.get("total_credits_people", 0)
            rtot = rt.get("total_credits", 0)
            rc = rt.get("contacts_extracted", 0)
            campaign_totals["probe"] += rp
            campaign_totals["search"] += rs
            campaign_totals["people"] += rpe
            campaign_totals["total"] += rtot
            campaign_totals["contacts"] += rc
            campaign_totals["per_run"].append({
                "run_id": rid, "probe": rp, "search": rs, "people": rpe, "total": rtot, "contacts": rc,
            })

    return {
        "success": True,
        "data": {
            "targets": len(target_domains),
            "contacts": len(contacts),
            "people_credits": people_credits,
            "total_credits": final_totals.get("total_credits", 0),
            "credits_breakdown": {
                "probe": final_totals.get("total_credits_probe", 0),
                "search": final_totals.get("total_credits_search", 0),
                "people": people_credits,
            },
            "kpi_met": len(contacts) >= run_data.get("kpi", {}).get("target_people", 100),
            "campaign_id": campaign_id,
            "campaign_slug": campaign_slug,
            "leads_uploaded": leads_uploaded,
            "sheet_url": sheet_url,
            "mode": mode,
            "campaign_totals": campaign_totals,  # full breakdown across all runs
        },
    }


def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()

def _default_workspace():
    from gtm_mcp.workspace import WorkspaceManager
    return WorkspaceManager(_default_config().dir)
