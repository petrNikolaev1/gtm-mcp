"""Document Extraction Service — extracts project data from strategy documents.

Reads a user-provided document (outreach plan, ICP definition, campaign brief)
and extracts structured data: offer, roles, filters, sequences, settings.
Silently skips what can't be automated.
"""
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


async def extract_from_document(
    text: str,
    website: str,
    openai_key: str,
    model: str = "gpt-4.1-mini",
) -> Dict[str, Any]:
    """Extract project data from a strategy document.

    Extracts what can be automated. Silently skips the rest.
    """
    prompt = f"""You are an expert at extracting structured campaign data from strategy documents.

Website: {website}

Extract the following from this document. Return ONLY valid JSON.
If a field cannot be determined, omit it (don't guess).
Silently skip anything that requires manual work, external signals, or data not in the document.

Extract:
1. "offer" — the company's product/service in 1-2 sentences
2. "value_prop" — the key value proposition
3. "target_audience" — who they're selling to
4. "target_roles" — object with:
   - "primary": [list of primary decision-maker titles]
   - "secondary": [list of secondary titles]
   - "tertiary": [list of tertiary/fallback titles]
   - "seniorities": [Apollo seniority levels: "owner", "founder", "c_suite", "vp", "head", "director"]
5. "segments" — array of INDUSTRY SUB-VERTICALS (not ICP categories).
   Look for sections about sub-verticals, market segments, or industry niches.
   Each segment is a distinct INDUSTRY TYPE (e.g. "Payments", "Lending", "RegTech").
   NOT generic categories like "Companies with Sales Teams" or "Funded Companies".
   Each with:
   - "name": SHORT industry label in CAPS (e.g. "PAYMENTS", "LENDING", "REGTECH")
   - "keywords": [Apollo search keywords for this specific sub-vertical]
6. "apollo_filters" — object with:
   - "combined_keywords": [ALL keywords from ALL segments merged]
   - "locations": [target countries/regions]
   - "employee_range": "min,max" (e.g. "20,500")
   - "industries": [Apollo industry names if identifiable]
   - "funding_stages": [if mentioned: "series_a", "series_b", etc.]
7. "sequences" — array of email sequences:
   - "name": sequence name
   - "steps": array of {{"day": N, "subject": "...", "body": "..."}}
   - Use ONLY SmartLead variable format (snake_case): {{{{first_name}}}}, {{{{last_name}}}}, {{{{company_name}}}}, {{{{email}}}}, {{{{city}}}}, {{{{phone_number}}}}
   - IMPORTANT: SmartLead uses snake_case! NOT {{{{firstName}}}} or {{{{company}}}}. Use {{{{first_name}}}} and {{{{company_name}}}}.
   - For signature use {{{{signature}}}} (SmartLead built-in).
   - For ANY non-standard variable: replace with natural text that works without external data.
   - The sequence MUST work as-is in SmartLead with only lead fields filled.
8. "campaign_settings" — object with:
   - "daily_limit_per_mailbox": number
   - "tracking": boolean
   - "stop_on_reply": boolean
   - "plain_text": boolean
9. "example_companies" — array of example/seed companies mentioned in the document:
   - "domain": company website domain (e.g. "softswiss.com")
   - "name": company name
   - "reason": why they're a good example (1 line)
   Look for sections titled "examples", "seed companies", "top companies", "filter seeds", etc.
   If no examples mentioned, omit this field.
10. "exclusion_list" — array of company types or specific companies to EXCLUDE:
   Look for sections titled "exclude", "shit list", "not target", "negative list", etc.
   - "type": category to exclude (e.g. "Casino Operators", "Recruitment Agencies")
   - "reason": why to exclude
   If no exclusions mentioned, omit this field.

Return ONLY the JSON object, no markdown formatting.

DOCUMENT:
{text[:15000]}"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4000,
                    "temperature": 0,
                },
            )
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(clean)

            # Post-process sequences: GPT rewrites emails with unfillable variables
            # Keeps user's tone/intent but makes ALL variables standard SmartLead ones
            import re
            # SmartLead variable name mapping: normalize to match lead column names
            VAR_MAP = {
                "firstname": "first_name", "firstName": "first_name",
                "lastname": "last_name", "lastName": "last_name",
                "company": "company_name", "companyName": "company_name", "company_name": "company_name",
                "city": "city", "email": "email",
                "phone": "phone_number", "phoneNumber": "phone_number", "phone_number": "phone_number",
                "signature": "signature",
                "first_name": "first_name", "last_name": "last_name",
            }
            STANDARD_VARS = set(VAR_MAP.values()) | set(VAR_MAP.keys())

            # First pass: normalize all variable names to SmartLead format
            for seq in result.get("sequences", []):
                for step in seq.get("steps", []):
                    for field in ("subject", "body"):
                        text = step.get(field, "")
                        for old_var, new_var in VAR_MAP.items():
                            if old_var != new_var:
                                text = text.replace("{{" + old_var + "}}", "{{" + new_var + "}}")
                        step[field] = text

            for seq in result.get("sequences", []):
                has_unfillable = False
                for step in seq.get("steps", []):
                    for field in ("subject", "body"):
                        variables = re.findall(r'\{\{(\w+)\}\}', step.get(field, ""))
                        if any(v.lower() not in STANDARD_VARS for v in variables):
                            has_unfillable = True
                            break

                if has_unfillable:
                    # GPT rewrites the sequence to use only standard variables
                    try:
                        rewrite_prompt = (
                            f"Rewrite this email sequence to use ONLY these variables: "
                            f"{{{{first_name}}}}, {{{{last_name}}}}, {{{{company_name}}}}, {{{{city}}}}, {{{{signature}}}}\n"
                            f"IMPORTANT: Use snake_case variable names — {{{{first_name}}}} NOT {{{{firstName}}}}, {{{{company_name}}}} NOT {{{{company}}}}.\n\n"
                            f"KEEP: the same tone, structure, day spacing, and sales intent.\n"
                            f"REPLACE: any custom variables (like {{{{hiring_role_or_signal}}}}, {{{{estimated_acv}}}}, "
                            f"{{{{calendly_link}}}}) with natural language that works WITHOUT external data.\n"
                            f"The emails must read naturally with ONLY the standard variables filled.\n\n"
                            f"Original sequence:\n"
                        )
                        for step in seq.get("steps", []):
                            rewrite_prompt += f"\nEmail (Day {step.get('day')}):\nSubject: {step.get('subject')}\nBody: {step.get('body')}\n"

                        rewrite_prompt += (
                            f"\n\nReturn JSON array: [{{\"day\": N, \"subject\": \"...\", \"body\": \"...\"}}]\n"
                            f"Keep {{{{company}}}}, {{{{firstName}}}}, {{{{signature}}}} as variables. Replace everything else with natural text."
                        )

                        async with httpx.AsyncClient(timeout=30) as rewrite_client:
                            rr = await rewrite_client.post(
                                "https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": rewrite_prompt}],
                                      "max_tokens": 3000, "temperature": 0.3},
                            )
                            rr_data = rr.json()
                            rr_content = rr_data["choices"][0]["message"]["content"].strip()
                            if rr_content.startswith("```"):
                                rr_content = rr_content.split("\n", 1)[1].rsplit("```", 1)[0]
                            rewritten = json.loads(rr_content)
                            seq["steps"] = rewritten
                            logger.info(f"GPT rewrote sequence '{seq.get('name','')}' — {len(rewritten)} emails, all variables standard")
                    except Exception as e:
                        # Fallback: simple replacement
                        logger.warning(f"GPT sequence rewrite failed: {e}, using simple replacement")
                        for step in seq.get("steps", []):
                            for field in ("subject", "body"):
                                text = step.get(field, "")
                                variables = re.findall(r'\{\{(\w+)\}\}', text)
                                for var in variables:
                                    if var.lower() not in STANDARD_VARS:
                                        replacement = var.replace("_", " ")
                                        text = text.replace("{{" + var + "}}", replacement)
                                        logger.info(f"Replaced unfillable {{{{{var}}}}} → '{replacement}'")
                                step[field] = text

            # Add metadata
            result["_model"] = model
            result["_tokens"] = data.get("usage", {})
            result["_website"] = website

            logger.info(f"Document extraction ({model}): "
                       f"{len(result.get('segments', []))} segments, "
                       f"{len(result.get('sequences', []))} sequences, "
                       f"{len(result.get('apollo_filters', {}).get('combined_keywords', []))} keywords")

            return result

    except Exception as e:
        logger.error(f"Document extraction failed ({model}): {e}")
        return {"error": str(e), "_model": model}


async def test_model_extraction(
    text: str,
    website: str,
    openai_key: str,
    models: list = None,
) -> list:
    """Test document extraction across multiple models. Returns scored results."""
    if not models:
        models = [
            "gpt-4o-mini",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "gpt-5-nano",
            "gpt-5-mini",
            "gpt-5.4-nano",
            "gpt-5.4-mini",
        ]

    results = []
    for model in models:
        logger.info(f"Testing model: {model}")
        result = await extract_from_document(text, website, openai_key, model)
        result["_model"] = model
        results.append(result)

    return results


def score_extraction(result: Dict, ground_truth: Dict) -> Dict:
    """Score an extraction result against ground truth. Returns per-field scores."""
    scores = {}

    # Offer (1 point)
    if result.get("offer") and len(result["offer"]) > 20:
        scores["offer"] = 1
    else:
        scores["offer"] = 0

    # Target audience (1 point)
    if result.get("target_audience") and len(result["target_audience"]) > 10:
        scores["target_audience"] = 1
    else:
        scores["target_audience"] = 0

    # Roles (3 points — 1 per tier)
    roles = result.get("target_roles", {})
    gt_roles = ground_truth.get("target_roles", {})
    for tier in ["primary", "secondary", "tertiary"]:
        extracted = set(r.lower() for r in roles.get(tier, []))
        expected = set(r.lower() for r in gt_roles.get(tier, []))
        if extracted and len(extracted & expected) >= len(expected) * 0.5:
            scores[f"roles_{tier}"] = 1
        else:
            scores[f"roles_{tier}"] = 0

    # Keywords (1 point — need 10+)
    kw = result.get("apollo_filters", {}).get("combined_keywords", [])
    scores["keywords"] = 1 if len(kw) >= 10 else 0

    # Locations (1 point — need 3+)
    locs = result.get("apollo_filters", {}).get("locations", [])
    scores["locations"] = 1 if len(locs) >= 3 else 0

    # Employee range (1 point)
    emp = result.get("apollo_filters", {}).get("employee_range", "")
    scores["employee_range"] = 1 if "20" in emp and "500" in emp else 0

    # Sequence (4 points — 1 per email)
    seqs = result.get("sequences", [])
    if seqs:
        steps = seqs[0].get("steps", [])
        for i in range(min(4, len(steps))):
            step = steps[i]
            has_subject = bool(step.get("subject"))
            has_body = bool(step.get("body")) and len(step.get("body", "")) > 50
            scores[f"sequence_email_{i+1}"] = 1 if has_subject and has_body else 0
        for i in range(len(steps), 4):
            scores[f"sequence_email_{i+1}"] = 0
    else:
        for i in range(4):
            scores[f"sequence_email_{i+1}"] = 0

    # Campaign settings (1 point)
    settings = result.get("campaign_settings", {})
    scores["settings"] = 1 if settings.get("stop_on_reply") is not None else 0

    scores["total"] = sum(scores.values())
    scores["max"] = 13
    scores["pct"] = round(scores["total"] / 13 * 100)

    return scores
