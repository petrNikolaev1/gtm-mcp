"""Shared OpenAI client — wraps httpx calls with cost tracking.

Every OpenAI API call in the codebase should use this instead of raw httpx.
Automatically tracks tokens, model, cost via cost_tracker.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.services.cost_tracker import extract_openai_usage

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
EMBEDDING_URL = "https://api.openai.com/v1/embeddings"


async def chat_completion(
    api_key: str,
    model: str,
    messages: List[Dict],
    max_tokens: int = 500,
    temperature: float = 0,
    purpose: str = "",
    timeout: int = 30,
) -> Optional[Dict]:
    """Call OpenAI chat completion with automatic cost tracking.

    Returns the full response dict, or None on error.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            data = resp.json()

            if "error" in data:
                logger.warning(f"OpenAI {model} error: {data['error']}")
                return None

            # Track cost
            extract_openai_usage(data, model, purpose)

            return data

    except Exception as e:
        logger.error(f"OpenAI {model} call failed: {e}")
        return None


def extract_content(data: Dict) -> str:
    """Extract text content from OpenAI response."""
    if not data:
        return ""
    return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def parse_json_response(data: Dict) -> Optional[Dict]:
    """Extract and parse JSON from OpenAI response."""
    content = extract_content(data)
    if not content:
        return None
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        import re
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


async def embed_texts(
    api_key: str,
    texts: List[str],
    model: str = "text-embedding-3-small",
    purpose: str = "embedding",
) -> List[List[float]]:
    """Embed texts with cost tracking."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                EMBEDDING_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "input": texts},
            )
            data = resp.json()

            # Track cost
            extract_openai_usage(data, model, purpose)

            return [item["embedding"] for item in data.get("data", [])]

    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return []
