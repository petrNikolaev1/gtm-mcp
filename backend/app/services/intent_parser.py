"""Intent Parser — analyzes user's gathering query into structured segments.

Takes raw user input like "find IT consulting and media production companies in Miami"
and outputs structured segment definitions for pipeline creation.

Uses Apollo's real taxonomy (112 industries) to ensure generated filters
match actual Apollo database values — not invented keywords.
"""
import logging
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.cost_tracker import extract_openai_usage

logger = logging.getLogger(__name__)

# Load Apollo taxonomy once at import time
_TAXONOMY_PATH = Path(__file__).parent.parent.parent.parent / "apollo_filters" / "apollo_taxonomy.json"
_APOLLO_INDUSTRIES: List[str] = []
try:
    if _TAXONOMY_PATH.exists():
        _data = json.loads(_TAXONOMY_PATH.read_text())
        _APOLLO_INDUSTRIES = _data.get("industries", [])
        logger.info(f"Loaded {len(_APOLLO_INDUSTRIES)} Apollo industries from taxonomy")
except Exception as e:
    logger.warning(f"Failed to load Apollo taxonomy: {e}")

PARSE_INTENT_PROMPT = """Translate this query into Apollo.io search filters that MAXIMIZE coverage of target companies.

USER'S QUERY: {query}

USER'S COMPANY (what they sell — use for competitor exclusion):
{user_offer}

APOLLO INDUSTRIES (use EXACT strings from this list, pick 3-5 that cover the target space):
{apollo_industries}

The goal: find as many relevant companies as possible. More industries = more coverage.
But ALL industries must be relevant to the query. Don't add random ones.

Think in concentric circles:
1. CORE industry (most direct match)
2. ADJACENT industries (where similar companies might be listed)
3. PERIPHERAL industries (where some targets might surprisingly be listed)

RULES:
- apollo_industries: MUST be exact values from the list. Pick 3-5.
- apollo_keywords: free-text search terms describing TARGET COMPANIES (not our product). 3-5 tags.
- Each DISTINCT business segment = separate entry in segments array
- "IT consulting and media production" = 2 segments
- Generate a CAPS_LOCKED segment label
- List competitor exclusions based on what the user sells

Return ONLY valid JSON:
{{
  "segments": [
    {{
      "label": "IT_CONSULTING",
      "description": "IT consulting firms providing technology advisory services",
      "apollo_keywords": ["IT consulting", "technology consulting", "IT services"],
      "apollo_industries": ["information technology & services", "computer software", "management consulting"],
      "geo": "Miami, Florida, United States",
      "country": "United States",
      "city": "Miami"
    }}
  ],
  "competitor_exclusions": [
    "Companies that provide the same service as us"
  ],
  "pipelines_needed": 1
}}"""


async def parse_gathering_intent(
    query: str,
    user_offer: str = "",
    openai_key: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Parse user's gathering query into structured segments.

    Uses GPT-4o (OpenAI) — only requires OpenAI key.
    """
    prompt = PARSE_INTENT_PROMPT.format(
        query=query,
        user_offer=user_offer or "(not provided — skip competitor exclusion)",
        apollo_industries=", ".join(_APOLLO_INDUSTRIES) if _APOLLO_INDUSTRIES else "(taxonomy not loaded — use best guesses)",
    )

    # Use OpenAI (GPT-4o for quality, GPT-4o-mini as fallback)
    if openai_key:
        result = await _call_openai(openai_key, prompt)
        if result:
            return result

    # Last resort — single segment from raw query
    logger.warning("No AI key available for intent parsing, using raw query")
    return _fallback_parse(query)


async def _call_gemini(api_key: str, prompt: str) -> Optional[Dict]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
                },
            )
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return _parse_json(text)
    except Exception as e:
        logger.warning(f"Gemini intent parsing failed: {e}")
        return None


async def _call_openai(api_key: str, prompt: str) -> Optional[Dict]:
    """Call OpenAI for intent parsing.

    Uses gpt-4.1-mini (94% accuracy, tested across 40 model×prompt setups).
    This is the ONE step where a stronger model matters — initial filter quality
    determines everything downstream.
    """
    import httpx
    # gpt-4.1-mini primary (94% accuracy), gpt-4o-mini fallback (90%)
    for model in ["gpt-4.1-mini", "gpt-4o-mini"]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "You parse B2B lead generation queries into structured JSON. Return ONLY valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 800,
                        "temperature": 0,
                    },
                )
                data = resp.json()
                if "error" in data:
                    logger.warning(f"Intent parsing with {model} failed: {data['error']}")
                    continue
                extract_openai_usage(data, model, "parse_gathering_intent")
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                result = _parse_json(content)
                if result:
                    logger.info(f"Intent parsed with {model}: {len(result.get('segments', []))} segments")
                    return result
        except Exception as e:
            logger.warning(f"Intent parsing with {model} failed: {e}")
            continue
    return None


def _parse_json(text: str) -> Optional[Dict]:
    import re
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "segments" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _fallback_parse(query: str) -> Dict:
    """Simple regex fallback when no AI is available."""
    import re
    label = re.sub(r'[^a-z0-9]+', '_', query.lower().split(' in ')[0].split(' from ')[0]).upper().strip('_')[:30]
    geo_match = re.search(r'\bin\s+(.+?)(?:\s+for|\s*$)', query, re.IGNORECASE)
    geo = geo_match.group(1).strip() if geo_match else ""

    return {
        "segments": [{
            "label": label or "TARGET",
            "description": query,
            "apollo_keywords": query.split(' in ')[0].split(' and '),
            "geo": geo,
            "country": "",
            "city": "",
        }],
        "competitor_exclusions": [],
        "pipelines_needed": 1,
    }
