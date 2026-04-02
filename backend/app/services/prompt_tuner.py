"""Prompt Tuner — adjusts GPT classification prompt until it matches agent verdicts.

The loop:
1. Classify all companies with current prompt
2. Compare vs agent's verdicts (truth)
3. If accuracy ≥ 95%: done
4. GPT generates improved prompt based on mismatches
5. Re-classify with new prompt
6. Repeat (max 5 iterations)

Each iteration is logged for UI visibility.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

logger = logging.getLogger(__name__)


async def tune_classification_prompt(
    companies: List[Dict],
    agent_verdicts: Dict[str, Dict],
    offer: str,
    query: str,
    openai_key: str,
    max_iterations: int = 1,
    target_accuracy: float = 0.95,
    model: str = "gpt-4.1-mini",
    return_history: bool = False,
) -> Union[Tuple[str, float, int], Tuple[str, float, int, List[Dict]]]:
    """Tune GPT classification prompt until it matches agent verdicts.

    Args:
        companies: List of {domain, scraped_text, name, ...}
        agent_verdicts: {domain: {target: bool, reason: str}}
        offer: What we sell
        query: Segment query
        openai_key: OpenAI API key
        max_iterations: Max prompt adjustment rounds
        target_accuracy: Stop when this accuracy reached
        model: GPT model for classification
        return_history: If True, return iteration history as 4th element

    Returns:
        (tuned_prompt, accuracy, iterations_count) or
        (tuned_prompt, accuracy, iterations_count, history) if return_history=True
    """
    current_prompt = _build_initial_prompt(offer, query)
    history = []

    for iteration in range(max_iterations):
        # Classify all companies with current prompt
        gpt_results = await _classify_batch(companies, current_prompt, openai_key, model)

        # Compare vs agent truth
        accuracy, mismatches = _compare(gpt_results, agent_verdicts)

        history.append({
            "iteration": iteration,
            "prompt": current_prompt,
            "accuracy": accuracy,
            "mismatches": mismatches,
            "gpt_results": {d: r.get("is_target", False) for d, r in gpt_results.items()},
        })

        logger.info(f"Prompt tuning iteration {iteration}: accuracy={accuracy:.0%}, "
                     f"mismatches={len(mismatches)}")

        if accuracy >= target_accuracy:
            logger.info(f"Prompt tuned to {accuracy:.0%} in {iteration + 1} iterations")
            result = (current_prompt, accuracy, iteration + 1)
            return (*result, history) if return_history else result

        # Generate improved prompt based on mismatches
        current_prompt = await _improve_prompt(
            current_prompt, mismatches, offer, query, openai_key
        )

    # Didn't converge — return best effort
    result = (current_prompt, accuracy, max_iterations)
    return (*result, history) if return_history else result


def _build_initial_prompt(offer: str, query: str) -> str:
    """Build the initial classification prompt (via negativa, no hardcode)."""
    return f"""EXCLUDE companies that should NOT be contacted. Keep everything else.

WE SELL: {offer}
TARGET SEGMENT: {query}

EXCLUDE ONLY IF one of these is true:
1. DIRECT COMPETITOR: sells the SAME type of product (same category, same buyer)
2. COMPLETELY UNRELATED: zero overlap with the target segment or its supply chain
3. WRONG GEOGRAPHY or WRONG INDUSTRY: explicitly outside the search criteria

INCLUDE if ANY of these is true:
- Company is an AGENCY that does work in the target segment
- Company is a PLATFORM that operates in the target space
- Company is a BRAND that actively engages in the activity our product supports
- Company manages TALENT/PEOPLE in the target space

A recruitment agency that places people in the industry ≠ buyer (they recruit, not operate).
A general digital marketing agency (PPC/SEO) ≠ buyer (unless specifically in the target segment).
A media company covering the industry ≠ buyer (they report, not operate).

Return JSON array:
[{{"domain": "...", "is_target": true/false, "segment": "CAPS_LABEL", "reasoning": "1 sentence"}}]

Companies:
"""


async def _classify_batch(
    companies: List[Dict], prompt: str, openai_key: str, model: str = "gpt-4.1-mini"
) -> Dict[str, Dict]:
    """Classify all companies with a given prompt. Returns {domain: {is_target, reasoning}}."""
    full_prompt = prompt
    for c in companies:
        domain = c.get("domain", c.get("primary_domain", "?"))
        name = c.get("name", domain)
        text = c.get("scraped_text", "")[:500]
        full_prompt += f"\n--- {name} ({domain}) ---\n{text}\n"

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": full_prompt}],
                    "max_tokens": 2000,
                    "temperature": 0,
                },
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            classifications = json.loads(content)

            results = {}
            for cls in classifications:
                domain = cls.get("domain", "")
                results[domain] = {
                    "is_target": cls.get("is_target", False),
                    "segment": cls.get("segment", ""),
                    "reasoning": cls.get("reasoning", ""),
                }
            return results

    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {}


def _compare(
    gpt_results: Dict[str, Dict], agent_verdicts: Dict[str, Dict]
) -> Tuple[float, List[Dict]]:
    """Compare GPT results vs agent verdicts. Returns (accuracy, mismatches)."""
    correct = 0
    total = 0
    mismatches = []

    for domain, verdict in agent_verdicts.items():
        gpt = gpt_results.get(domain, {})
        agent_target = verdict.get("target", False)
        gpt_target = gpt.get("is_target", False)
        total += 1

        if agent_target == gpt_target:
            correct += 1
        else:
            mismatches.append({
                "domain": domain,
                "agent_says": agent_target,
                "gpt_says": gpt_target,
                "agent_reason": verdict.get("reason", ""),
                "gpt_reason": gpt.get("reasoning", ""),
            })

    accuracy = correct / total if total > 0 else 0
    return accuracy, mismatches


async def _improve_prompt(
    current_prompt: str,
    mismatches: List[Dict],
    offer: str,
    query: str,
    openai_key: str,
) -> str:
    """Ask GPT to improve the classification prompt based on mismatches."""
    mismatch_text = ""
    for m in mismatches:
        direction = "should be TARGET but GPT said NOT" if m["agent_says"] else "should NOT be target but GPT said YES"
        mismatch_text += f"- {m['domain']}: {direction}. Agent reason: {m['agent_reason']}\n"

    improve_prompt = f"""You are improving a classification prompt for a B2B lead generation system.

Current prompt:
{current_prompt}

The prompt was tested on real companies and made these MISTAKES:
{mismatch_text}

Our product: {offer}
Target segment: {query}

Generate an IMPROVED version of the classification prompt that would correctly classify these cases.
Rules:
- Keep the via negativa approach (exclude bad, keep everything else)
- Do NOT include any specific company names or domains
- Do NOT hardcode specific industries or keywords
- Make the rules MORE PRECISE based on the mismatch patterns
- The prompt must work for ANY companies in this segment, not just these

Return ONLY the improved prompt text (no explanation, no markdown)."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4.1-mini",
                    "messages": [{"role": "user", "content": improve_prompt}],
                    "max_tokens": 1000,
                    "temperature": 0.3,
                },
            )
            data = resp.json()
            improved = data["choices"][0]["message"]["content"].strip()
            # Remove markdown wrapping if present
            if improved.startswith("```"):
                improved = improved.split("\n", 1)[1].rsplit("```", 1)[0]
            return improved

    except Exception as e:
        logger.error(f"Prompt improvement failed: {e}")
        return current_prompt
