"""Manual domain list adapter — direct domain input."""
from typing import Any, Dict, List
from app.services.gathering_adapters.base import BaseGatheringAdapter


class ManualAdapter(BaseGatheringAdapter):
    source_type = "manual.companies.manual"
    description = "Direct domain list (free)"

    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        if not filters.get("domains"):
            return False, "Need 'domains' list"
        return True, ""

    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        domains = filters.get("domains", [])
        return [
            {"domain": d.strip(), "name": None, "source_data": {"input": "manual"}}
            for d in domains if d.strip()
        ]
