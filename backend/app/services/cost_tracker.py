"""Cost Tracker — logs every external API call with tokens, cost, model.

Tracks: OpenAI (per model, per call), Apollo (credits), Apify (bytes, sites).
All stored in mcp_usage_logs with structured extra_data.

Pricing (as of 2026-03):
  OpenAI:
    gpt-4o-mini:    $0.15/1M input,  $0.60/1M output
    gpt-4.1-mini:   $0.40/1M input,  $1.60/1M output
    gpt-4.1-nano:   $0.10/1M input,  $0.40/1M output
    gpt-4o:         $2.50/1M input,  $10.00/1M output
    text-embedding-3-small: $0.02/1M tokens
  Apollo:
    search: 1 credit/page ($0.01/credit estimate)
    enrich: 1 credit/company
  Apify:
    Residential proxy: $8/GB
"""
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Pricing per 1M tokens
OPENAI_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}

APOLLO_COST_PER_CREDIT = 0.01
APIFY_COST_PER_GB = 8.00


class CostTracker:
    """Accumulates costs during a request. Flush to DB at end."""

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.entries: list = []

    def log_openai(self, model: str, input_tokens: int, output_tokens: int,
                   purpose: str = "", duration_ms: int = 0):
        """Log an OpenAI API call."""
        pricing = OPENAI_PRICING.get(model, {"input": 0.15, "output": 0.60})
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

        entry = {
            "service": "openai",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": round(cost, 6),
            "purpose": purpose,
            "duration_ms": duration_ms,
        }
        self.entries.append(entry)
        logger.debug(f"OpenAI {model}: {input_tokens}+{output_tokens} tokens = ${cost:.6f} ({purpose})")
        return entry

    def log_apollo(self, credits: int, action: str = "search"):
        """Log Apollo API credits."""
        cost = credits * APOLLO_COST_PER_CREDIT
        entry = {
            "service": "apollo",
            "credits": credits,
            "action": action,
            "cost_usd": round(cost, 4),
        }
        self.entries.append(entry)
        return entry

    def log_apify(self, domains_scraped: int, bytes_used: int = 0, duration_ms: int = 0):
        """Log Apify proxy usage."""
        gb = bytes_used / (1024 * 1024 * 1024)
        cost = gb * APIFY_COST_PER_GB
        entry = {
            "service": "apify",
            "domains_scraped": domains_scraped,
            "bytes_used": bytes_used,
            "gb_used": round(gb, 4),
            "cost_usd": round(cost, 4),
            "duration_ms": duration_ms,
        }
        self.entries.append(entry)
        return entry

    def summary(self) -> Dict[str, Any]:
        """Aggregate costs by service."""
        by_service = {}
        for e in self.entries:
            svc = e["service"]
            if svc not in by_service:
                by_service[svc] = {"total_cost_usd": 0, "calls": 0, "details": {}}
            by_service[svc]["total_cost_usd"] += e.get("cost_usd", 0)
            by_service[svc]["calls"] += 1

            if svc == "openai":
                model = e["model"]
                if model not in by_service[svc]["details"]:
                    by_service[svc]["details"][model] = {
                        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0
                    }
                by_service[svc]["details"][model]["calls"] += 1
                by_service[svc]["details"][model]["input_tokens"] += e.get("input_tokens", 0)
                by_service[svc]["details"][model]["output_tokens"] += e.get("output_tokens", 0)
                by_service[svc]["details"][model]["cost_usd"] += e.get("cost_usd", 0)

            elif svc == "apify":
                by_service[svc]["details"]["domains_scraped"] = by_service[svc]["details"].get("domains_scraped", 0) + e.get("domains_scraped", 0)
                by_service[svc]["details"]["bytes_total"] = by_service[svc]["details"].get("bytes_total", 0) + e.get("bytes_used", 0)

        total = sum(s["total_cost_usd"] for s in by_service.values())
        return {"total_cost_usd": round(total, 4), "by_service": by_service}


# Global tracker for current request (set per-request in middleware)
_current_tracker: Optional[CostTracker] = None


def get_tracker() -> CostTracker:
    """Get the current request's cost tracker."""
    global _current_tracker
    if _current_tracker is None:
        _current_tracker = CostTracker()
    return _current_tracker


def reset_tracker(user_id: Optional[int] = None) -> CostTracker:
    """Start a new cost tracker for a request."""
    global _current_tracker
    _current_tracker = CostTracker(user_id=user_id)
    return _current_tracker


def extract_openai_usage(response_data: dict, model: str, purpose: str = "") -> Dict:
    """Extract token usage from OpenAI API response and log it."""
    usage = response_data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    if input_tokens or output_tokens:
        return get_tracker().log_openai(model, input_tokens, output_tokens, purpose)
    return {}
