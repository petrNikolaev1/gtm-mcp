"""Base adapter interface for gathering sources."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseGatheringAdapter(ABC):
    """All gathering adapters implement this interface."""

    source_type: str = ""
    description: str = ""

    @abstractmethod
    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """Execute the gathering operation. Returns list of company dicts."""
        ...

    @abstractmethod
    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        """Validate filter schema. Returns (is_valid, error_message)."""
        ...

    def estimate_cost(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Estimate cost before running."""
        return {"credits": 0, "cost_usd": 0, "note": "Free"}
