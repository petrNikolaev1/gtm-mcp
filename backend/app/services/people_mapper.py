"""A7: People Filter Mapper — maps offer to person titles + seniorities.

Infers who at the target company would BUY this product.
Default: C-level. Adjustable by user ("change roles to VP Marketing").
"""
import json
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Seniority values accepted by Apollo
VALID_SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry"]


async def infer_people_filters(
    offer: str,
    openai_key: str,
    user_preference: str = "",
) -> Dict:
    """Infer person_titles and person_seniorities from offer text.

    Args:
        offer: What the company sells (e.g. "payroll platform")
        openai_key: OpenAI API key
        user_preference: User's explicit role request (e.g. "VP Marketing and CMO")

    Returns:
        {
            "person_titles": ["VP HR", "CHRO", "Head of People Operations"],
            "person_seniorities": ["vp", "director", "c_suite"],
            "contacts_per_company": 3,
            "reasoning": "Payroll → HR decision makers"
        }
    """
    prompt = f"""Who at a company would decide to BUY this product?

Product: {offer[:500]}
{f"User preference: {user_preference}" if user_preference else ""}

Pick the JOB TITLES of people who would make the buying decision.
Pick 3-5 titles. Include the seniority level.

Available seniorities: {VALID_SENIORITIES}

Return JSON:
{{"person_titles": ["title1", "title2", "title3"],
  "person_seniorities": ["seniority1", "seniority2"],
  "reasoning": "1 sentence why these roles"}}"""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0,
                },
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)

            # Validate seniorities
            valid = set(VALID_SENIORITIES)
            result["person_seniorities"] = [s for s in result.get("person_seniorities", []) if s in valid]
            if not result["person_seniorities"]:
                result["person_seniorities"] = ["c_suite", "vp", "director"]

            result["contacts_per_company"] = 3
            return result

    except Exception as e:
        logger.warning(f"People filter inference failed: {e}")
        return {
            "person_titles": ["CEO", "CTO", "COO"],
            "person_seniorities": ["c_suite", "vp", "director"],
            "contacts_per_company": 3,
            "reasoning": "Default C-level (inference failed)",
        }
