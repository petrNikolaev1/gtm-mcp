"""SSE Progress Notification Emitter — sends real-time progress updates to MCP clients."""
import asyncio
import logging
from typing import Any, Dict, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# Progress channels: session_id -> list of pending events
_progress_channels: Dict[str, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=100))


async def emit_progress(session_id: str, event_type: str, data: Dict[str, Any]):
    """Emit a progress event to a specific MCP session."""
    if session_id in _progress_channels:
        try:
            _progress_channels[session_id].put_nowait({
                "type": event_type,
                **data,
            })
        except asyncio.QueueFull:
            logger.warning(f"Progress queue full for session {session_id}")


async def get_progress_events(session_id: str, timeout: float = 0.1) -> list:
    """Get all pending progress events for a session."""
    events = []
    queue = _progress_channels.get(session_id)
    if not queue:
        return events
    try:
        while True:
            event = queue.get_nowait()
            events.append(event)
    except asyncio.QueueEmpty:
        pass
    return events


def create_progress_callback(session_id: str, phase: str):
    """Create a callback for batch operations (scraping, analysis, etc.)."""
    async def callback(completed: int, total: int, detail: Optional[str] = None):
        await emit_progress(session_id, "progress", {
            "phase": phase,
            "completed": completed,
            "total": total,
            "percentage": round(completed / total * 100, 1) if total > 0 else 0,
            "detail": detail,
        })
    return callback


# Phase-specific helpers
async def emit_gather_progress(session_id: str, page: int, total_pages: int, companies_found: int):
    await emit_progress(session_id, "gather_progress", {
        "phase": "gather",
        "page": page,
        "total_pages": total_pages,
        "companies_found": companies_found,
        "detail": f"Page {page}/{total_pages}: {companies_found} companies",
    })


async def emit_scrape_progress(session_id: str, completed: int, total: int, current_domain: str = ""):
    await emit_progress(session_id, "scrape_progress", {
        "phase": "scrape",
        "completed": completed,
        "total": total,
        "percentage": round(completed / total * 100, 1) if total > 0 else 0,
        "detail": f"Scraped {completed}/{total}" + (f" ({current_domain})" if current_domain else ""),
    })


async def emit_analysis_progress(session_id: str, completed: int, total: int):
    await emit_progress(session_id, "analysis_progress", {
        "phase": "analyze",
        "completed": completed,
        "total": total,
        "percentage": round(completed / total * 100, 1) if total > 0 else 0,
    })


async def emit_refinement_progress(session_id: str, iteration: int, accuracy: float, target: float):
    await emit_progress(session_id, "refinement_progress", {
        "phase": "refine",
        "iteration": iteration,
        "accuracy": round(accuracy, 3),
        "target_accuracy": target,
        "detail": f"Iter {iteration}: {accuracy:.1%} accuracy (target: {target:.0%})",
    })


async def emit_verification_progress(session_id: str, completed: int, total: int, emails_found: int):
    await emit_progress(session_id, "verification_progress", {
        "phase": "verify",
        "completed": completed,
        "total": total,
        "emails_found": emails_found,
        "detail": f"Verified {completed}/{total}, {emails_found} emails found",
    })


def cleanup_session(session_id: str):
    """Clean up progress channel for a disconnected session."""
    _progress_channels.pop(session_id, None)
