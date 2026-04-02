"""Adaptive Semaphore — starts at max, shrinks on 429, grows back when OK.

Per-service concurrency control that learns the real limits from production.
No prior knowledge needed — just set a high initial max and let it adapt.

Usage:
    sem = AdaptiveSemaphore("openai", initial=100, min_concurrent=5)
    async with sem.acquire() as token:
        resp = await client.post(...)
        if resp.status_code == 429:
            sem.report_429()
        else:
            sem.report_ok()
"""
import asyncio
import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AdaptiveSemaphore:
    """Concurrency controller that adapts to real-world rate limits."""

    def __init__(self, name: str, initial: int = 100, min_concurrent: int = 5, grow_interval: float = 10.0):
        self.name = name
        self.initial = initial
        self.min_concurrent = min_concurrent
        self.grow_interval = grow_interval  # seconds between grow attempts

        self._current_limit = initial
        self._semaphore = asyncio.Semaphore(initial)
        self._lock = asyncio.Lock()
        self._last_429_time = 0.0
        self._last_grow_time = time.time()
        self._total_ok = 0
        self._total_429 = 0
        self._consecutive_ok = 0

    @property
    def current_limit(self) -> int:
        return self._current_limit

    def report_429(self):
        """Called when a request gets 429. Shrinks concurrency by 50%."""
        self._total_429 += 1
        self._consecutive_ok = 0
        self._last_429_time = time.time()
        old = self._current_limit
        new = max(self.min_concurrent, self._current_limit // 2)
        if new < old:
            self._current_limit = new
            # Rebuild semaphore with lower limit
            self._semaphore = asyncio.Semaphore(new)
            logger.info(f"AdaptiveSem[{self.name}]: 429 → shrink {old}→{new}")

    def report_ok(self):
        """Called when a request succeeds. Grows concurrency after sustained OK."""
        self._total_ok += 1
        self._consecutive_ok += 1
        now = time.time()
        # Grow by 25% if: enough consecutive OKs AND enough time since last grow AND not at max
        if (self._consecutive_ok >= self._current_limit
                and now - self._last_grow_time > self.grow_interval
                and self._current_limit < self.initial):
            old = self._current_limit
            new = min(self.initial, int(self._current_limit * 1.25) + 1)
            self._current_limit = new
            self._semaphore = asyncio.Semaphore(new)
            self._last_grow_time = now
            self._consecutive_ok = 0
            logger.info(f"AdaptiveSem[{self.name}]: grow {old}→{new}")

    def acquire(self):
        """Use as: async with sem.acquire(): ..."""
        return self._semaphore

    def stats(self) -> Dict:
        return {
            "name": self.name,
            "current_limit": self._current_limit,
            "initial": self.initial,
            "total_ok": self._total_ok,
            "total_429": self._total_429,
        }


# ── Per-user per-service semaphores ──
# Key: "user_{user_id}:{service}" → each user's API key has its own rate limits
_semaphores: Dict[str, AdaptiveSemaphore] = {}


def get_semaphore(service: str, user_id: int = 0, initial: int = 100, min_concurrent: int = 5) -> AdaptiveSemaphore:
    """Get or create an adaptive semaphore for a user+service combo.

    Rate limits are per API key = per user account. Different users have different
    OpenAI tiers, Apify plans, Apollo quotas. Each gets their own semaphore.
    """
    key = f"user_{user_id}:{service}"
    if key not in _semaphores:
        _semaphores[key] = AdaptiveSemaphore(f"{service}:u{user_id}", initial, min_concurrent)
    return _semaphores[key]


# Pre-configured defaults based on real testing
def OPENAI_SEM(user_id: int = 0): return get_semaphore("openai", user_id, initial=100, min_concurrent=10)
def APIFY_SEM(user_id: int = 0): return get_semaphore("apify", user_id, initial=100, min_concurrent=10)
def APOLLO_SEM(user_id: int = 0): return get_semaphore("apollo", user_id, initial=5, min_concurrent=1)
