"""Campaign Intelligence Service — GOD_SEQUENCE pattern extraction + sequence generation.

Adapted for MCP: per-user API keys, no scheduler deps.
"""
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models.campaign import (
    CampaignSnapshot, CampaignPattern, CampaignIntelligenceRun,
    GeneratedSequence, Campaign,
)
from app.models.project import Project

logger = logging.getLogger(__name__)


async def _get_user_feedback(session: AsyncSession, project_id: int, feedback_type: str) -> Optional[str]:
    """Get user feedback for a specific project and type, ordered by most recent first.

    Returns concatenated feedback strings, most recent at top (highest priority).
    Used to inject user preferences into AI prompts.
    """
    from app.models.usage import MCPUsageLog
    result = await session.execute(
        select(MCPUsageLog).where(
            MCPUsageLog.tool_name == "user_feedback",
            MCPUsageLog.action == feedback_type,
            MCPUsageLog.extra_data["project_id"].as_integer() == project_id,
        ).order_by(desc(MCPUsageLog.created_at)).limit(10)
    )
    logs = result.scalars().all()
    if not logs:
        return None

    parts = []
    for log in logs:
        ed = log.extra_data or {}
        text = ed.get("feedback_text", "")
        ts = ed.get("timestamp", "")
        ctx = ed.get("context", {})
        ctx_str = f" (context: {ctx})" if ctx else ""
        parts.append(f"[{ts}] {text}{ctx_str}")

    return "\n".join(parts)


# Default timing pattern from top campaigns
DEFAULT_TIMING = [0, 3, 4, 7, 7]  # Days: send, follow-up 1, 2, 3, 4


class CampaignIntelligenceService:
    """GOD_SEQUENCE: learn from campaigns, extract patterns, generate sequences."""

    def __init__(self, openai_key: Optional[str] = None, gemini_key: Optional[str] = None):
        self._openai_key = openai_key
        self._gemini_key = gemini_key  # kept for backward compat, not used

    async def score_campaigns(self, session: AsyncSession, company_id: int,
                               project_id: Optional[int] = None) -> List[Dict]:
        """Score and rank campaigns by quality metrics."""
        query = select(CampaignSnapshot).where(
            CampaignSnapshot.company_id == company_id,
            CampaignSnapshot.is_latest == True,
        )
        if project_id:
            query = query.where(CampaignSnapshot.project_id == project_id)
        query = query.order_by(desc(CampaignSnapshot.quality_score))

        result = await session.execute(query)
        snapshots = result.scalars().all()

        return [
            {
                "campaign_id": s.campaign_id,
                "name": s.campaign_name,
                "quality_score": s.quality_score,
                "warm_reply_rate": s.warm_reply_rate,
                "meeting_rate": s.meeting_rate,
                "leads_count": s.leads_count,
                "market": s.market,
            }
            for s in snapshots
        ]

    async def get_patterns(self, session: AsyncSession, company_id: int,
                            scope_level: Optional[str] = None,
                            market: Optional[str] = None) -> List[Dict]:
        """Get active patterns for sequence generation."""
        query = select(CampaignPattern).where(
            CampaignPattern.company_id == company_id,
            CampaignPattern.is_active == True,
        )
        if scope_level:
            query = query.where(CampaignPattern.scope_level == scope_level)
        if market:
            query = query.where(
                (CampaignPattern.market == market) | (CampaignPattern.market == None)
            )

        result = await session.execute(query)
        patterns = result.scalars().all()

        return [
            {
                "id": p.id,
                "scope_level": p.scope_level,
                "pattern_type": p.pattern_type,
                "title": p.title,
                "description": p.description,
                "confidence": p.confidence,
                "market": p.market,
            }
            for p in patterns
        ]

    async def generate_sequence(
        self,
        session: AsyncSession,
        project_id: int,
        campaign_name: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> GeneratedSequence:
        """Generate a 5-step email sequence using patterns + project knowledge."""
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Assemble context from patterns (3-level knowledge)
        patterns = await self.get_patterns(session, project.company_id)

        # Build generation prompt
        context_parts = []
        if project.target_segments:
            context_parts.append(f"ICP: {project.target_segments}")
        if project.target_industries:
            context_parts.append(f"Industries: {project.target_industries}")
        if project.sender_name:
            context_parts.append(f"Sender: {project.sender_name}")
        if project.sender_company:
            context_parts.append(f"Company: {project.sender_company}")

        for p in patterns[:10]:
            context_parts.append(f"[{p['scope_level']}:{p['pattern_type']}] {p['title']}: {p['description'][:200]}")

        if instructions:
            context_parts.append(f"Additional instructions: {instructions}")

        # Inject user feedback — most recent first, highest priority
        feedback = await _get_user_feedback(session, project_id, "sequence")
        if feedback:
            context_parts.append(f"\n--- USER FEEDBACK (HIGHEST PRIORITY — follow these instructions exactly) ---\n{feedback}")

        generation_prompt = "\n".join(context_parts)
        name = campaign_name or f"{project.name} - Generated"

        # Generate sequence with AI (GPT-4o-mini) or fall back to template
        steps = await self._generate_steps_ai(project, generation_prompt, instructions)
        if not steps:
            steps = self._generate_steps_template(project)

        # Normalize subject lines — clean names, no special chars
        steps = self._normalize_subjects(steps)

        model = "gpt-4o-mini" if isinstance(steps, list) and len(steps) > 0 and "AI-generated" in str(steps[0].get("body", "")[:1]) == False else "template_v1"

        seq = GeneratedSequence(
            project_id=project_id,
            company_id=project.company_id,
            campaign_name=name,
            generation_prompt=generation_prompt,
            patterns_used=[p["id"] for p in patterns[:10]],
            sequence_steps=steps,
            sequence_step_count=len(steps),
            rationale="Generated using project ICP + GPT-4o-mini. Timing: Day 0/3/4/7/7.",
            status="draft",
            model_used="gpt-4o-mini",
        )
        session.add(seq)
        await session.flush()

        return seq

    # GOD_SEQUENCE checklist — every email must pass these before shipping
    SEQUENCE_CHECKLIST = """
MANDATORY CHECKLIST (every email must satisfy):
☐ Personalization: {{first_name}} in subject OR body of EVERY email
☐ Geo case study: {{city}} used in at least 1 email for geo-specific social proof
☐ Specific numbers: at least 2 emails must have real $ amounts, percentages, or quantities
☐ Competitor positioning: at least 1 email mentions competitor alternatives (Deel, Upwork, etc.)
☐ Distinct intent per email — no two emails can have the same purpose:
  - Email 1: Hook + value prop + geo case study with numbers
  - Email 2: Competitor comparison + bullet-point benefits (3-5 bullets)
  - Email 3: Transparent pricing + social proof numbers
  - Email 4: Ultra-short (2-3 lines), channel switch (LinkedIn/Telegram), casual tone
  - Email 5: Breakup — brief, respectful, no pressure (optional: "Sent from my iPhone" style)
☐ Subject lines: at least 2 must include {{first_name}}
☐ A/B subjects: Email 1 subject MUST use {{first_name}}, Email 3 subject MUST use {{company}}
☐ NO "I hope this message finds you well" or any variant — instant delete trigger
☐ NO identical closings — vary between: sender name only, "Sent from my iPhone", question ending, no closing
☐ HTML formatting: use <br><br> between paragraphs (SmartLead renders HTML)
☐ Each email ≤ 120 words (short = higher reply rate)
☐ Merge tags: {{first_name}}, {{company}}, {{city}} — double curly braces, these are SmartLead tags
"""

    # Reference sequence structure — proven 4% reply rate pattern
    REFERENCE_SEQUENCE = """
REFERENCE SEQUENCE (study this structure, adapt for the target ICP):

Email 1 — Subject: "{{first_name}} - [pain point question]?"
Body: Hi {{first_name}},<br><br>[1 sentence: what you do + key differentiator].<br><br>[2-3 sentences: specific capabilities].<br><br>Recently helped a {{city}} [industry] company [specific result with numbers].<br><br>Would you like to [soft CTA]?<br><br>{{sender_name}}<br>[Position], [Company]<br>[Social proof line]

Email 2 — Subject: (empty = reply thread)
Body: Hi {{first_name}},<br><br>Following up. Many companies we talk to [common pain point].<br><br>We offer a better way:<br>- [Benefit 1 with number]<br>- [Benefit 2]<br>- [Benefit 3]<br>- [Benefit 4]<br><br>Open to a quick demo call this week?

Email 3 — Subject: (empty = reply thread)
Body: Hi {{first_name}},<br><br>Just making sure my emails are getting through.<br><br>[Transparent pricing with exact numbers]. [Volume discount mention].<br><br>Can I send you a 2-minute walkthrough video?

Email 4 — Subject: (empty = reply thread)
Body: Would it be easier to connect on LinkedIn or Telegram?<br><br>If you already have a solution, happy to compare — many clients switch after seeing the total cost difference.<br><br>Sent from my iPhone

KEY PATTERNS TO REPLICATE:
- Email 1: {{city}}-based case study with exact $ savings
- Email 2: Competitor names + bullet benefits
- Email 3: Exact pricing (numbers build trust)
- Email 4: Ultra-short, no greeting, casual, channel switch
- Reply-thread subjects (empty subject = keeps thread)
- Numbers everywhere: specific metrics, savings, scale proof
"""

    async def _generate_steps_ai(self, project, context: str, instructions: Optional[str] = None) -> Optional[list]:
        """Generate sequence with Gemini 2.5 Pro using GOD_SEQUENCE structure.

        Falls back to GPT-4o-mini if Gemini unavailable.
        """
        import httpx, json

        sender = project.sender_name or "Team"
        company = project.sender_company or "our company"
        position = project.sender_position or "BDM"

        prompt = f"""Generate a 4-step cold email sequence for B2B outreach.

TARGET ICP & CONTEXT:
{context}
{f'Additional instructions: {instructions}' if instructions else ''}

SENDER: {sender}, {position} at {company}

{self.REFERENCE_SEQUENCE}

{self.SEQUENCE_CHECKLIST}

ADAPT the reference sequence for the target ICP above. Keep the STRUCTURE and TECHNIQUES but change:
- The value proposition to match what {company} actually offers to this ICP
- The case study numbers to be plausible for this industry/geo
- The competitor names to whatever this ICP currently uses
- The pricing to match {company}'s offering

TIMING: Day 0, 3, 4, 7 (4 emails, not 5 — last one is breakup)
MERGE TAGS: {{{{first_name}}}}, {{{{company}}}}, {{{{city}}}} (double curly braces — SmartLead format)
FORMAT: Body must use <br> for line breaks and <br><br> between paragraphs (SmartLead renders HTML)

Return ONLY a JSON array of 4 objects:
[{{"step": 1, "day": 0, "subject": "...", "subject_b": "...", "body": "..."}}, ...]

A/B TESTING:
- Email 1: "subject" uses {{{{first_name}}}}, "subject_b" uses {{{{company}}}} — SmartLead splits 50/50
- Emails 2-4: "subject" and "subject_b" both EMPTY string "" (reply thread)
- Body is the same for both variants (only subject differs)"""

        # Use GPT-4o for sequence generation (best OpenAI model for creative writing)
        # Falls back to GPT-4o-mini if GPT-4o fails
        try:
            from app.config import settings
            openai_key = self._openai_key or settings.OPENAI_API_KEY
            if not openai_key:
                return None

            # Try GPT-4o first (better quality for sequence generation)
            for model in ["gpt-4o", "gpt-4o-mini"]:
                try:
                    async with httpx.AsyncClient(timeout=60) as client:
                        resp = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                            json={"model": model, "messages": [
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": "Generate the sequence now. Return ONLY valid JSON."},
                            ], "max_tokens": 3000, "temperature": 0.7})
                        if resp.status_code == 200:
                            data = resp.json()
                            content = data["choices"][0]["message"]["content"]
                            result = self._parse_json_steps(content)
                            if result:
                                result = self._ensure_ab_variants(result)
                                logger.info(f"Sequence generated with {model}")
                                return result
                except Exception as e:
                    logger.warning(f"{model} sequence generation failed: {e}")
                    continue
        except Exception as e:
            logger.error(f"Sequence generation failed: {e}")

        # Legacy fallback path (should not reach here)
        try:
            from app.config import settings
            openai_key = self._openai_key or settings.OPENAI_API_KEY
            if not openai_key:
                return None

            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": [
                        {"role": "system", "content": "You are a top-tier SDR who writes cold email sequences that get 4%+ reply rates. Return ONLY valid JSON array. Study the reference sequence structure exactly."},
                        {"role": "user", "content": prompt},
                    ], "max_tokens": 3000, "temperature": 0.5},
                )
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                result = self._parse_json_steps(content)
                if result:
                    result = self._ensure_ab_variants(result)
                return result
        except Exception as e:
            logger.error(f"AI sequence generation failed: {e}")
        return None

    def _ensure_ab_variants(self, steps: list) -> list:
        """Ensure Email 1 has A/B subject variants for testing.

        If the AI didn't generate subject_b, create one automatically:
        - If subject has {{first_name}} → variant B uses {{company}}
        - If subject has {{company}} → variant B uses {{first_name}}
        """
        if not steps:
            return steps

        first = steps[0]
        subject_a = first.get("subject", "")

        if not first.get("subject_b") and subject_a:
            if "{{first_name}}" in subject_a:
                first["subject_b"] = subject_a.replace("{{first_name}}", "{{company}}")
            elif "{{company}}" in subject_a:
                first["subject_b"] = subject_a.replace("{{company}}", "{{first_name}}")
            else:
                # Neither tag — add {{first_name}} variant
                first["subject_b"] = f"{{{{first_name}}}} - {subject_a}"

        return steps

    async def _call_gemini(self, api_key: str, prompt: str) -> Optional[list]:
        """Call Gemini 2.5 Pro for sequence generation."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={api_key}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4000},
                    },
                )
                data = resp.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return self._parse_json_steps(text)
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return None

    def _parse_json_steps(self, content: str) -> Optional[list]:
        """Parse JSON steps from AI response."""
        import json
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            steps = json.loads(clean)
            if isinstance(steps, list) and len(steps) >= 3:
                return steps
        except Exception:
            pass
        return None

    def _normalize_subjects(self, steps: list) -> list:
        """Clean subject lines — strip special chars, asterisks, excess punctuation."""
        import re
        for step in steps:
            subj = step.get("subject", "")
            if not subj:
                continue
            # Remove asterisks, backticks, brackets used for emphasis
            subj = re.sub(r'[*`\[\]]', '', subj)
            # Collapse multiple spaces
            subj = re.sub(r'\s{2,}', ' ', subj)
            # Remove leading/trailing punctuation (except ?)
            subj = subj.strip(' -–—:;,.')
            step["subject"] = subj
        return steps

    def _generate_steps_template(self, project) -> list:
        """Fallback template sequence."""
        sender = project.sender_name or "Team"
        company = project.sender_company or "our company"
        return [
            {"step": 1, "day": 0, "subject": "Quick question about {{company}}", "body": f"Hi {{{{first_name}}}},\n\nI noticed {{{{company}}}} is expanding — we help companies like yours with {company}.\n\nWould it make sense to chat for 15 min this week?\n\nBest,\n{sender}"},
            {"step": 2, "day": 3, "subject": "Re: Quick question about {{company}}", "body": f"Hi {{{{first_name}}}},\n\nJust following up. Companies similar to {{{{company}}}} typically see results within the first month.\n\nHappy to share specifics — does Thursday or Friday work?\n\nBest,\n{sender}"},
            {"step": 3, "day": 4, "subject": "{{company}} + " + company, "body": f"Hi {{{{first_name}}}},\n\nOne more thought — I put together a quick overview of how {company} could help {{{{company}}}} specifically.\n\nWorth a look?\n\nBest,\n{sender}"},
            {"step": 4, "day": 7, "subject": "One more thought for {{company}}", "body": f"Hi {{{{first_name}}}},\n\nI know you're busy. We've helped companies in your space reduce costs by 30-40%. If that's interesting, I'd love 15 min.\n\nIf not, no worries at all.\n\nBest,\n{sender}"},
            {"step": 5, "day": 7, "subject": "Should I close the loop?", "body": f"Hi {{{{first_name}}}},\n\nThis will be my last note. If timing isn't right, I totally get it.\n\nBut if there's even a small chance this could be useful for {{{{company}}}}, I'd love to connect.\n\nBest,\n{sender}"},
        ]

    async def push_to_smartlead(
        self,
        session: AsyncSession,
        sequence_id: int,
        smartlead_service,
    ) -> Dict[str, Any]:
        """Push an approved sequence to SmartLead as a DRAFT campaign."""
        seq = await session.get(GeneratedSequence, sequence_id)
        if not seq:
            raise ValueError("Sequence not found")
        if seq.status != "approved":
            raise ValueError("Sequence must be approved before pushing")

        # Create DRAFT campaign in SmartLead
        campaign_data = await smartlead_service.create_campaign(seq.campaign_name or "MCP Generated")
        if not campaign_data:
            raise ValueError("Failed to create SmartLead campaign")

        campaign_id = campaign_data.get("id")

        # Set sequences
        await smartlead_service.set_campaign_sequences(campaign_id, seq.sequence_steps)

        # Update tracking
        seq.pushed_at = datetime.utcnow()
        seq.status = "pushed"

        # Create local campaign record
        campaign = Campaign(
            project_id=seq.project_id,
            company_id=seq.company_id,
            name=seq.campaign_name,
            external_id=str(campaign_id),
            platform="smartlead",
            status="draft",
        )
        session.add(campaign)
        await session.flush()
        seq.pushed_campaign_id = campaign.id

        return {
            "smartlead_campaign_id": campaign_id,
            "url": f"https://app.smartlead.ai/app/email-campaigns-v2/{campaign_id}/analytics",
            "status": "draft",
        }
