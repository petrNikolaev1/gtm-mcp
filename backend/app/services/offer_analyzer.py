"""Offer Analyzer — infers target company size and ICP from offer/website text.

Used to auto-set Apollo employee_count filter without asking user.
"""
import json
import logging
from typing import Dict, Optional

import httpx

from app.services.cost_tracker import extract_openai_usage

logger = logging.getLogger(__name__)

INFER_SIZE_PROMPT = """What SIZE companies would BUY this product? Pick ONE narrow range, NOT "all sizes".

Product: {offer_text}

RULES:
- Pick the MOST LIKELY buyer, not the broadest possible range
- "All sizes" is WRONG. Every product has a sweet spot.
- The range must be NARROW (max 10x spread, e.g. 10-200 not 1-10000)

Mapping (use these):
- Payroll/contractor management/EOR → 10-200 (SMBs that can't afford internal HR)
- Enterprise SaaS/security/compliance → 200-5000
- Small business tools (accounting, POS) → 1-50
- Freelancer marketplace → 1-20
- B2B consulting/services → 50-500
- DevOps/infrastructure → 50-1000
- Marketing tools → 10-500
- Recruitment/staffing platform → 20-500

Return ONLY JSON:
{{"min_employees": N, "max_employees": N, "apollo_range": "N,N", "reasoning": "1 sentence"}}"""


async def infer_target_size(offer_text: str, openai_key: str) -> Dict:
    """Infer target company size from offer text using GPT-4o-mini.

    Returns: {"min_employees": 10, "max_employees": 200, "apollo_range": "11,200", "reasoning": "..."}
    """
    if not offer_text or not openai_key:
        return {"min_employees": 10, "max_employees": 500, "apollo_range": "11,500",
                "reasoning": "Default range (no offer text provided)"}

    prompt = INFER_SIZE_PROMPT.format(offer_text=offer_text[:2000])

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0,
                },
            )
            data = resp.json()
            extract_openai_usage(data, "gpt-4o-mini", "infer_target_size")
            content = data["choices"][0]["message"]["content"]
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(clean)

            # Validate and format for Apollo
            min_emp = result.get("min_employees", 10)
            max_emp = result.get("max_employees", 500)
            result["apollo_range"] = f"{min_emp},{max_emp}"
            logger.info(f"Inferred target size: {min_emp}-{max_emp} ({result.get('reasoning', '')})")
            return result

    except Exception as e:
        logger.warning(f"Size inference failed: {e}")
        return {"min_employees": 10, "max_employees": 500, "apollo_range": "11,500",
                "reasoning": f"Default (inference failed: {str(e)[:50]})"}


INFER_ROLES_PROMPT = """Who at the target company would BUY this product? Pick 3-5 job titles that make the buying decision.

Product: {offer_text}

RULES:
- Pick titles of DECISION MAKERS, not end users
- Be specific: "VP of HR" not just "VP"
- Consider who has budget authority for this type of purchase
- Seniorities must be from: owner, founder, c_suite, partner, vp, head, director, manager

Return ONLY JSON:
{{"person_titles": ["title1", "title2", "title3"], "person_seniorities": ["seniority1", "seniority2"], "reasoning": "1 sentence"}}"""


VALID_SENIORITIES = {"owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry"}


async def infer_people_roles(offer_text: str, openai_key: str) -> Dict:
    """Infer target people roles/titles from offer text using GPT-4o-mini.

    Returns: {"person_titles": ["VP HR", ...], "person_seniorities": ["vp", "director"], "reasoning": "..."}
    """
    if not offer_text or not openai_key:
        return {"person_titles": ["CEO", "CTO", "COO"], "person_seniorities": ["c_suite", "vp", "director"],
                "reasoning": "Default C-level (no offer text)"}

    prompt = INFER_ROLES_PROMPT.format(offer_text=offer_text[:2000])

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0,
                },
            )
            data = resp.json()
            extract_openai_usage(data, "gpt-4o-mini", "infer_people_roles")
            content = data["choices"][0]["message"]["content"]
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(clean)

            # Validate seniorities
            result["person_seniorities"] = [s for s in result.get("person_seniorities", []) if s in VALID_SENIORITIES]
            if not result["person_seniorities"]:
                result["person_seniorities"] = ["c_suite", "vp", "director"]

            logger.info(f"Inferred roles: {result.get('person_titles', [])} ({result.get('reasoning', '')})")
            return result

    except Exception as e:
        logger.warning(f"Role inference failed: {e}")
        return {"person_titles": ["CEO", "CTO", "COO"], "person_seniorities": ["c_suite", "vp", "director"],
                "reasoning": f"Default C-level (inference failed: {str(e)[:50]})"}
