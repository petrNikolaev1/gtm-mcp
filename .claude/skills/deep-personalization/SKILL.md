# Deep Personalization Skill

Per-contact email sequences grounded in PUBLIC research. Two-tier research (company, then person), per-lead body composition, push to SmartLead with the entire email body as a custom variable.

Tested on AdNetwork segment (April 2026). Reply rate uplift: 2-3x over template-only when ≥70% of contacts get a real hook.

## When to Use

- Pipeline phase: AFTER KPI is reached (Step 5 complete, contacts.json finalized) and BEFORE SmartLead push (Step 7).
- During `/outreach --personalize` on existing contacts.
- Contact volume 5-500. Above 500, sample top N by seniority/match-score first.

## When NOT to Use

- Junior roles (analyst/manager) — public footprint too thin, ROI per minute spent is low.
- Mass-volume freemium-style outreach (>2000 contacts) — economics don't justify research time.
- Pure transactional offers (one-shot, low-touch) — template wins.

## Why It Works

Standard cold email personalizes by COMPANY (name, industry, size). Deep Personalization personalizes by PERSON or COMPANY-with-role-framing — career, public quotes, growth metrics, role-specific pain. The reader can tell within 5 seconds whether the sender treated them as a name on a list or as a person.

| Style | Opener |
|-------|--------|
| Template | "How are you managing mass payouts to your network of publishers?" |
| Deep | "Shreyans, at 1.5B impressions/month, RTBDemand is processing thousands of publisher payouts. Quick math — if even 10% goes through SWIFT at $30-50/wire, that's a six-figure line item." |

## Pipeline Placement

**Phase 5.5 — runs after KPI met, before SmartLead push — AND ONLY IF THE USER OPTS IN.**

```
Step 5: People extraction → contacts.json populated, KPI met
   ↓
Step 5.5 — OPT-IN GATE (see "User Opt-in Protocol" below)
   ├─ Ask user: "Apply deep personalization? (cost/time tradeoff shown)"
   ├─ Default: NO. User must explicitly type yes/personalize/apply.
   └─ Save decision to project.yaml → personalization.enabled
   ↓
If opted in → Deep Personalization (THIS SKILL):
   ├─ Tier 1: Company research (one pass per unique target domain)
   ├─ Tier 2: Person research (one pass per verified contact)
   ├─ Routing: pick tier per contact (person > company > default)
   ├─ Composition: write entire 4-step sequence per contact
   └─ Persist: write per-lead bodies to contacts.json + leads_for_push.json
   ↓
If NOT opted in → Skip straight to Step 6 (default sequence for all leads)
   ↓
Step 6: Sequence template = a single thin shell using {{email_N_body}} variables
   ↓
Step 7: campaign_push → SmartLead substitutes per-lead bodies at send time
```

By placing this AFTER KPI, cost is spent only on contacts we'll actually push — no waste on contacts we'd discard later.

## User Opt-in Protocol

**Default: OFF. Every pipeline run asks explicitly. Never auto-run even if a previous run opted in.** Personalization takes 5-30 minutes extra wall clock and $0.50-3.00 extra; the user has to consciously accept that trade-off each time.

### The ask (shown after KPI reached, before push)

```
KPI REACHED: 100/100 verified contacts across 33 target companies.
Cost so far: 127 Apollo credits ($1.27).

DEEP PERSONALIZATION (optional):
  What it does: per-contact research (LinkedIn, press, Crunchbase) → each lead gets
                a fully-custom 4-email sequence. Email 1 uses a person hook; Email 2
                uses a fresh company hook when both signals exist.
  Expected coverage: ~70% deep personalized, ~30% default sequence
  Estimated cost: ~$1.20 LLM + free WebSearch
  Estimated time: ~8-12 min wall clock (4 parallel researchers)
  Expected uplift: 2-3x reply rate (PDF benchmark, April 2026)

  Your last decision for this project: {last_decision or "never asked"}

  Apply deep personalization?
    [yes / personalize / apply]     → run Phase 5.5
    [no / skip / default]           → go straight to default sequence
    [details]                       → show example output + full methodology
```

### Acceptable affirmative responses

`yes`, `y`, `personalize`, `apply`, `go`, `do it`. Anything else (including ambiguous "maybe", "not sure", empty) → treat as NO and proceed with default sequence.

### Persistence — `project.yaml`

Save every decision (yes or no) to `project.yaml`:

```yaml
personalization:
  enabled: true                        # current-run choice
  first_enabled_at: "2026-04-16T..."   # when user first said yes (null until first yes)
  last_decision: "yes"                 # "yes" | "no"
  last_decision_at: "2026-04-16T..."
  history:                             # keep the last 5 decisions for context
    - {run_id: "run-001", decision: "yes", at: "2026-04-16T..."}
    - {run_id: "run-002", decision: "no",  at: "2026-04-18T..."}
  preferences:                         # optional, populated by user feedback
    skip_emails_2_4: false             # only personalize Email 1
    default_sequence_ref: "V3_Luma"    # which default to use when NOT opted in
    hook_blacklist: []                 # e.g. ["math_on_their_data"] if user dislikes math hooks
    tone_notes: null                   # free-text user-provided style guide
```

### Next-run behavior

- Always re-ask. Don't silently carry over the previous decision.
- Show the last decision as context ("Your last decision: yes") so the user can quickly repeat it.
- If the user explicitly says "always personalize on this project" or "never ask again" → save a flag `preferences.always_apply: true` (or `always_skip: true`) and auto-route future runs without asking. This is the only path to skipping the gate.

### Mode 3 (append) — inherit from parent run

- If the ORIGINAL campaign was created with personalization, the appended contacts should also be personalized (maintains narrative consistency for anyone comparing emails).
- Still show the gate, but with default suggestion = "yes (original campaign was personalized)".

### Mode 2 (new campaign on existing project) — independent decision

- Each campaign gets its own decision. Don't inherit across campaigns within a project.
- Still show last-decision context.

### Feedback capture during the ask

If user types something other than yes/no (e.g. "only first email", "use V3 Luma as default", "don't use math hooks"):
1. Parse for preferences and save to `project.yaml.personalization.preferences`
2. Apply to THIS run
3. Re-ask the opt-in question with the adjusted scope shown
4. These preferences stick across runs until the user changes them

### Telemetry (useful for retrospective ROI analysis)

When Phase 5.5 runs, capture in `runs/{run_id}.json.personalization_stats`:
- decision: yes|no
- decided_at: timestamp
- cost_usd (actual, after composition)
- wall_clock_seconds (actual)
- tier_distribution (person/company/company_light/default counts)
- multi_angle_count
- hook_distribution
- coverage_pct (person+company share)

Later, when replies come back, correlate against these stats to measure actual uplift per project/segment. This closes the feedback loop on the 2-3x claim.

## Two-Tier Research

### Tier 1 — Company Research (per unique target domain)

For each company that has at least one verified contact, run ONE WebSearch:

```
"{company_name}" interview OR raised OR growth OR revenue site:linkedin.com OR site:crunchbase.com OR site:techcrunch.com
```

Extract structured facts:

```json
"company_research": {
  "growth_metrics": ["600% revenue growth in 2025"],
  "recent_funding": "Series B, $40M (Dec 2025)",
  "scale_metrics": ["1.5B impressions/month", "12 markets"],
  "public_ceo_quotes": ["..."],
  "recent_news": ["Expanded to APAC, hired 50 publishers Q1"],
  "business_model_signals": ["performance marketing", "ad agency"],
  "sources": ["https://...", "https://..."],
  "researched_at": "2026-04-16T...",
  "quality_score": 3   // 0-3, see scoring below
}
```

Cached in `companies[domain].research` in the run file. Future runs (Mode 2/3) for the same domain reuse this.

**Why one pass per company, not per contact**: at ~3 contacts per company average, three contacts at one company would otherwise re-research the same growth metric three times. Wasteful AND if two of them compare emails, identical openers expose the templating.

### Tier 2 — Person Research (per verified contact)

For each contact, ONE WebSearch:

```
"{first_name} {last_name}" "{company_name}" {title}
```

If thin, ONE more:

```
"{first_name} {last_name}" {company_domain} interview OR speaker OR raised
```

Extract:

```json
"person_research": {
  "career_path": ["Goldman Sachs → RTBDemand"],
  "prior_companies": ["Goldman Sachs"],
  "tenure_signals": ["Founded RTBDemand 2018", "Sales Mgr → Group Director"],
  "public_quotes": ["..."],
  "thought_leadership": ["Speaker at Programmatic IO 2025"],
  "role_specific_signals": ["onboards publishers in HK/APAC"],
  "sources": ["https://linkedin.com/in/...", "https://..."],
  "researched_at": "2026-04-16T...",
  "quality_score": 2   // 0-3, see scoring below
}
```

The Tier-2 agent receives the Tier-1 company research as CONTEXT — so it doesn't re-discover the same growth metric.

### Quality Score (0-3) — for routing

For each tier, score 1 point per category present **with at least one source URL**:

**Company tier** (max 3):
- +1 — concrete metric (revenue, growth %, scale number)
- +1 — recent event (funding, expansion, leadership change in last 12 months)
- +1 — public CEO/leadership quote OR business-model-specific public signal

**Person tier** (max 3):
- +1 — career arc (2+ verifiable prior companies in correct order)
- +1 — concrete person signal (interview quote, conference talk, founding role, public number)
- +1 — role-specific signal tying their daily work to our offer

## Routing — Person > Company > Default + Multi-Angle Across Sequence

A god-tier SDR doesn't burn all their ammunition in Email 1. They lead with the strongest hook, then come back with a fresh angle in Email 2. The same prospect sees TWO different reasons to reply across two touches.

```
For each contact:
  person_q   = person_quality_score    (0-3)
  company_q  = company_quality_score   (0-3)

  # Tier (drives Email 1 lead angle):
  if person_q >= 2:
    tier = "person"
  elif company_q >= 2:
    tier = "company"
  elif person_q == 1 or company_q == 1:
    tier = "company_light"
  else:
    tier = "default"

  # Multi-angle eligibility (both tiers have something to say):
  multi_angle = (person_q >= 2 AND company_q >= 1) OR (company_q >= 2 AND person_q >= 1)
```

### Per-email angle rotation

| Tier | Email 1 (Day 0) | Email 2 (Day 3) | Email 3 (Day 4) | Email 4 (Day 7) |
|------|-----------------|-----------------|-----------------|-----------------|
| `person` + multi_angle | **PERSON hook** (career / quote / role) | **COMPANY hook** (growth / scale / funding) — fresh angle, not callback | Pricing/social proof — light reference to either hook | 2-3 lines, channel switch — combined narrative |
| `person` (no company signal) | PERSON hook | Callback to person hook + competitor positioning | Pricing/social proof | Channel switch |
| `company` + multi_angle | **COMPANY hook** (we lead with what's strongest) | **PERSON hook** as Email 2 angle (still adds variety) | Pricing/social proof | Channel switch |
| `company` (no person signal) | COMPANY hook | Different company angle (e.g. funding fact if Email 1 used growth fact) + competitor positioning | Pricing/social proof | Channel switch |
| `company_light` | Single weak signal, light personalization | Competitor positioning | Pricing/social proof | Channel switch |
| `default` | Default sequence text | Default | Default | Default |

**Why this is the god-SDR move:**
- A real top SDR doesn't tell a prospect everything in one email. They lead with the personal hook ("you came from Goldman"), then in Email 2 come back with a different angle ("by the way — at 1.5B impressions/month, the SWIFT math is six figures").
- Each touch lands a fresh reason to reply. Inbox attention is finite — two distinct angles double the odds something resonates.
- It mirrors how an inbound investor or recruiter would write — sequenced, not stacked.

**Person preferred for Email 1** because a personal hook reads as the most handcrafted. But Email 2 then leverages the company facts we ALREADY researched — zero extra cost.

**Multi-contact-per-company protection**: when two contacts at the same company both end up in `tier=company`, vary the role-framing so openers don't look identical. The composition step handles this — see "Email Composition" below.

## Hook Taxonomy

Pick exactly ONE hook per email opener.

| Rank | Hook Type | Example | When to Use |
|------|-----------|---------|-------------|
| 1 | `concrete_number` | "growing 600% in a year means everything breaks" | Company has public growth/scale number |
| 2 | `math_on_their_data` | "1.5B impressions × 10% via SWIFT × $30-50 = six figures" | Company has volume metric we can multiply |
| 3 | `career_path` | "from Zedo to Adform to Innity — both sides of the stack" | Person has 2+ prior companies in same industry |
| 4 | `scaling_pain` | "growing 600% means everything breaks" | Company in hyper-growth phase |
| 5 | `role_daily_pain` | "when onboarding publishers, does payment speed come up?" | Person's role has known operational pain |
| 6 | `path_within_company` | "you built the trading desk — Sales Mgr to Group Director" | Person's tenure shows progression at this company |
| 7 | `fallback_template` | "{{first_name}}, quick question about {{company_name}}." | No quality signals — default tier |

## Email Composition

### Per-contact, per-step output

For high/medium tier contacts, the agent writes the FULL body for all 4 sequence steps — not just an icebreaker. The hook USED varies by email per the angle-rotation table above. Each email is a complete, standalone-readable message.

### When `multi_angle = true` (both person AND company facts available)

```
Email 1 (Day 0) — PERSON hook (Tier 2 facts lead)
  ¶1 (icebreaker — 2-3 sentences using PERSON fact: career / quote / role-pain)
       e.g., "Shreyans, with your Goldman background heading RTBDemand..."
  ¶2 (who we are — 1-2 sentences, our company + offer + credentials)
  ¶3 (social proof — 1 sentence, true or plausible-generalization)
  ¶4 (CTA — short, casual, optionally tied back to the person hook)
  Total: 3-4 ¶, < 600 chars

Email 2 (Day 3) — COMPANY hook (Tier 1 facts lead — FRESH angle, NOT a callback)
  ¶1 (open with COMPANY fact framed as new context, not "as I mentioned")
       e.g., "Quick context — at 1.5B impressions/month, even 10% of payouts via SWIFT
              at $30-50/wire is six figures sitting in a P&L line item."
  ¶2 (competitor positioning OR product fit OR specific bullet benefits — 3-5 bullets if used)
  ¶3 (CTA — different from Email 1, references the math/company angle)
  Total: ~100 words

Email 3 (Day 4) — pricing + social proof
  ¶1 (concrete pricing tied to their likely volume — uses company scale fact for sizing)
  ¶2 (social proof case ideally matched to their segment / size / hook flavor)
  ¶3 (CTA — different again)
  Total: ~80 words

Email 4 (Day 7) — ultra-short channel switch
  2-3 lines, casual tone. Pulls in BOTH the person and company narrative briefly.
  e.g., "Shreyans — Goldman habits + 1.5B impressions = you'll know in 2 minutes
         if this fits. LinkedIn instead of email?"
  Total: 2-3 lines
```

### When tier=`person` but no company signal

```
Email 1: PERSON hook (full body, as above)
Email 2: callback to person hook + competitor positioning OR product fit + 3-5 bullets
Email 3: pricing/social proof
Email 4: channel switch, last person reference
```

### When tier=`company` (person signal weak/absent)

```
Email 1: COMPANY hook (full body, role-framed for this contact)
Email 2:
  - if multi_angle (person_q >= 1): use the available person signal as Email 2 angle
  - else: use a DIFFERENT company fact — e.g., if Email 1 used growth metric, Email 2 uses recent funding
Email 3: pricing/social proof
Email 4: channel switch
```

### When tier=`default`

All 4 emails use the default template text verbatim (with SmartLead's standard `{{first_name}}`, `{{company_name}}`, `{{city}}` substitution).

### Cohesion vs variety — the balance

- **Within an email**: ONE hook. Don't stack person + company + math in the same paragraph.
- **Across the sequence (multi_angle)**: TWO different hooks across Emails 1-2. Email 3 references one of them lightly. Email 4 weaves both for a brief send-off.
- **Bad cohesion**: Email 2 saying "as I mentioned in my last email about your 600% growth..." → reads like a desperate follow-up. Just open with the new angle as fresh context.
- **Good cohesion**: Email 4 implicitly proves you're the same sender by combining narrative threads, but doesn't quote Email 1 or 2.

### For default-tier contacts

Use the default template (the one chosen by Step 6 — V3 Luma if provided in document, GOD_SEQUENCE otherwise). The default text is filled into the same `email_N_body` custom fields so the SmartLead sequence body is uniform: just `{{email_N_body}}` with no special-casing.

### Subject lines

Subject 1 is also personalized for person/company tiers:
- person tier: `"{{first_name}}, {hook_keyword}"` (e.g., "Shreyans, the SWIFT math")
- company tier: `"{{first_name}}, quick on {{company_name}}'s {fact}"` (e.g., "Rafi, quick on Kendago's 600%")
- default: standard GOD_SEQUENCE subject

Subjects 2-4 stay empty (reply-thread maintenance — see email-sequence skill rule 7).

## Examples (Real, April 2026)

### Example 1 — `tier=company` then upgraded to `tier=person`

**Contact**: Rafi Magen, CEO, Kendago (Israel)
**Company research**: 600% growth, $150M revenue for clients (CTech interview) → quality_score=2
**Person research**: Founder of Kendago since 2014, performance marketing expert (LinkedIn) → quality_score=2
**Routing**: person tier (both ≥2, prefer person)
**Hook**: `scaling_pain` — uses company growth fact through founder's lens

```
Rafi, growing 600% in a year means everything breaks at some point — ops,
billing, payouts. Curious if cross-border payments to your media partners is
one of those things that still runs on SWIFT and manual wires at Kendago.

We're INXY — regulated crypto payout infrastructure (EU VASP/Canadian MSB).
Ad agencies like yours use our API to pay partners in 30+ countries,
same-day, under 1%.

Just helped a similar performance group cut $1.2M/year in payout costs after
they outgrew their bank setup.

Worth a 15-min look?
```

### Example 2 — `tier=person`, math hook

**Contact**: Shreyans Chopra, Founder, RTBDemand (Singapore)
**Company research**: 1.5B impressions/month, $100K+ monthly profit → quality_score=2
**Person research**: ex-Goldman Sachs, founder since 2018 → quality_score=3
**Routing**: person tier
**Hook**: `math_on_their_data` + personal CTA tied to Goldman background

```
Shreyans, at 1.5 billion impressions a month, I'd guess RTBDemand is
processing thousands of publisher payouts across multiple geos. Quick math —
if even 10% of that goes through SWIFT at $30-50 per wire, that's a
six-figure line item.

We're INXY — regulated payout infrastructure (EU VASP/Canadian MSB). Ad
networks use our API to settle with publishers in USDT, same-day, under 1%.
No crypto on your balance sheet — publishers get paid, you keep clean fiat.

With your Goldman background you'll see the P&L impact in 5 minutes. Worth
a quick look?
```

### Example 3 — `tier=person`, career path

**Contact**: Nilesh Jadhav, Regional Director Programmatic, Innity (Malaysia)
**Company research**: 12 markets, programmatic marketplace → quality_score=1
**Person research**: ex-Zirca (Adform India), ex-Zedo APAC/US → quality_score=3
**Routing**: person tier
**Hook**: `career_path`

```
Nilesh, you've been on both sides of the programmatic stack — from Zedo's
ad server to Adform's DSP to Innity's marketplace. You know better than
most how much friction sits between an impression and a publisher actually
getting paid.

We're INXY — regulated payout rails (EU VASP/Canadian MSB) that ad
platforms use to pay publishers across Asia in minutes instead of days, at
under 1% vs 3-5% SWIFT.

Innity runs 12 markets — who on your side owns the payout ops? Would love
to show them the numbers.
```

### Example 4 — `tier=company`, role-framed

**Contact**: Henry Chan, Associate Director of Sales, Innity (Hong Kong)
**Company research**: APAC ad platform, publisher network → quality_score=2
**Person research**: HK-based, sales role (RocketReach only — no quotes/talks) → quality_score=1
**Routing**: company tier (person <2, company ≥2)
**Hook**: `role_daily_pain` — company fact framed through Henry's sales role

```
Henry, curious — when you're onboarding publishers in Hong Kong and across
Asia, does payment speed ever come up as a friction point? We keep hearing
from ad platforms that smaller publishers especially care about getting
paid fast.

We're INXY — regulated crypto payout infrastructure (EU VASP/Canadian MSB).
Platforms like Innity use our API to settle with publishers same-day across
Asia, under 1% fees. No SWIFT delays, no intermediary bank charges.

Is this something that would help your publisher relationships? Happy to
show how it works.
```

### Example 5 — `tier=person` + multi_angle (full 4-email sequence)

**Contact**: Yeeyin Chong, Group Director Trading, Innity (Malaysia)
**Person research**: Sales Mgr → Group Director Trading (full path on ZoomInfo), built trading desk → quality_score=2
**Company research**: 12 markets, programmatic marketplace, recent expansion → quality_score=2
**Routing**: person tier + multi_angle (both ≥ 2 → prefer person for Email 1, use company for Email 2)
**Hook E1**: `path_within_company` (PERSON)
**Hook E2**: `math_on_their_data` (COMPANY — fresh angle, not callback)

**Email 1 (Day 0) — PERSON hook**:

```
Yeeyin, you've built the trading desk at Innity from the ground up — Sales
Manager to Group Director across multiple markets. The money flowing
through your desk is massive. Curious how much of the margin gets eaten by
cross-border settlement costs on the publisher side.

We're INXY — regulated payout rails (EU VASP/Canadian MSB). Ad platforms
use our API to settle with publishers in minutes at under 1%, vs days and
3-5% through banks.

For someone running programmatic trading across 12 markets — would a quick
cost comparison be useful?
```

**Email 2 (Day 3) — COMPANY hook (fresh angle, NO callback to Email 1)**:

```
Quick context — Innity running 12 markets means publisher payouts scale
linearly with margin compression. If even a third of those settlements hit
3-5% SWIFT fees, you're handing the banks a six-figure annual line item
that doesn't show up in any P&L review.

Unlike traditional bank rails or PSPs, our API settles in USDT same-day,
keeps your fiat clean, and lets publishers in any of those 12 markets pull
funds without intermediary banks taking a cut.

A 10-minute cost-per-payout breakdown by market — useful?
```

**Email 3 (Day 4) — pricing + social proof**:

```
Concrete numbers for an Innity-shaped platform: ~$0.60 per payout flat,
no FX markup, no minimums. A Singapore-based ad network with similar
publisher volume cut $480K/year in settlement costs after switching from
their bank rail.

Worth running the numbers on Innity's payout volume?
```

**Email 4 (Day 7) — channel switch, BOTH narratives**:

```
Yeeyin — running the trading desk + 12-market settlement math = you'll
know in 2 minutes if this fits. LinkedIn instead of email?
```

This is what a top SDR sequence looks like at scale — two distinct angles across two touches, narrative compressed in Email 4. Same prospect, two different reasons to reply, four total touches that don't read as the same email reformatted.

## Whole-Sequence-as-Variable Pattern (SmartLead Integration)

### Sequence template — same for ALL leads

```
Email 1: subject="{{subject_1}}",        body="{{email_1_body}}"
Email 2: subject="",                     body="{{email_2_body}}"
Email 3: subject="",                     body="{{email_3_body}}"
Email 4: subject="",                     body="{{email_4_body}}"
```

The SmartLead sequence body is a one-line shell. All real content lives in per-lead `custom_fields`. SmartLead substitutes at send time. Single uniform code path — no branching by tier inside SmartLead.

### Per-lead `custom_fields` schema

```json
{
  "email": "rafi@kendago.com",
  "first_name": "Rafi",
  "last_name": "Magen",
  "company_name": "Kendago",
  "company_domain": "kendago.com",
  "linkedin_url": "https://linkedin.com/in/rafi-magen",
  "phone": "...",
  "title": "CEO",
  "custom_fields": {
    "subject_1": "Rafi, quick on Kendago's 600%",
    "email_1_body": "Rafi, growing 600% in a year means everything breaks...<br><br>We're INXY...<br><br>Just helped...<br><br>Worth a 15-min look?<br><br>{{signature}}",
    "email_2_body": "Quick context — performance agencies running at this growth rate typically...<br><br>Unlike legacy bank rails...<br><br>...",
    "email_3_body": "Concrete numbers for a 600%-growth performance agency...<br><br>$X for Y...<br><br>...",
    "email_4_body": "Rafi — 600% growth + cross-border ad-spend = you'll know in 2 minutes...",
    "personalization_tier": "person",
    "multi_angle": "yes",
    "personalization_confidence": "high",
    "email_1_hook": "scaling_pain",
    "email_2_hook": "concrete_number",
    "facts_cited": "600% growth (CTech 2025); media partners 30+ countries (LinkedIn); founder of Kendago since 2014",
    "sources": "https://www.calcalistech.com/...; https://linkedin.com/in/rafi-magen"
  }
}
```

`facts_cited` and `sources` are joined into a single string (SmartLead custom fields are flat strings, not arrays) — visible in SmartLead's "Other Details" panel for QA.

`{{signature}}` inside the body is still SmartLead's standard signature variable — works as before.

### Why ALL emails as variables (not just Email 1)

- **Narrative cohesion**: Email 2 referencing Email 1's hook ("you mentioned growing 600% — here's why others outgrew their bank setup") only works if both bodies are personalized together
- **Single code path**: every lead gets all 4 custom_fields, sequence body never branches, fewer failure modes
- **Cheap**: writing 4 emails per contact instead of 1 only adds ~3x LLM tokens (~$0.012 per contact instead of $0.005). At KPI=100, total ~$1.20 vs $0.50.
- **Trade-off accepted**: emails are locked at lead-add time. If you want to A/B test Email 2 mid-campaign, you'd need to update every lead's `email_2_body`. For most campaigns this is fine — write once, let it run.

### Default-tier contacts use the SAME mechanism

Even default-tier leads have `custom_fields.email_N_body` populated — with the chosen default sequence text (V3 Luma or GOD_SEQUENCE). SmartLead substitutes uniformly. Their `personalization_tier="default"` flag is just metadata for tracking.

### Default sequence selection (which template to use as fallback)

In Phase 5.5, before composing per-lead bodies:

1. If `project.yaml.sequences[]` has a sequence flagged as default (V3 Luma per PDF, or first sequence in the list) → use its text for default-tier leads
2. Otherwise → use GOD_SEQUENCE from email-sequence skill

The chosen default text becomes the literal value of `email_N_body` for every default-tier contact (with `{{first_name}}`, `{{company_name}}`, `{{city}}` substituted by SmartLead's standard variables).

## Rules

### DO

- ONLY public sources: LinkedIn, interviews, press releases, conference pages, company news, Crunchbase
- Tie ONE specific fact to the specific pain the offer solves
- Run math on their numbers when figures exist
- Recognize achievements as context, not flattery
- Vary role framing across multiple contacts at the same company (avoid identical openers)
- Write peer-to-peer

### DO NOT

- ❌ **Don't invent** — if Goldman background isn't found, don't write "your Goldman background"
- ❌ **Don't fabricate** quotes or opinions
- ❌ **Don't use private info**: family, health, vacation, religion, politics
- ❌ **Don't inflate** metrics — "~1.5B" beats fabricated "2B"
- ❌ **Don't fake social proof** — must trace to a real or plausible-generalization case
- ❌ **Don't be creepy** — "saw your vacation pics" → NO. "read your CTech interview" → YES
- ❌ **Don't repeat the same hook in Emails 1-4** — vary framing per email; reuse the FACT, not the sentence
- ❌ **Don't downgrade default-tier into a "thin personalization"** — fake personalization is worse than honest template

## Verification — every personalization before it ships

For each contact, before writing to the chunk file:

1. Each cited fact has at least one source URL
2. Numbers came from a public source
3. Career path companies in correct order
4. Social proof case traces to provided real-case basis
5. Email 1 body length: 3-4 paragraphs, < 600 chars total
6. Email 4 body length: 2-3 lines max
7. No banned phrases ("hope this email finds you well", "just following up", "touching base")
8. All 4 emails reference the same hook/fact (cohesion check)

If ANY fact fails verification → drop the unverified claim. If multiple fail → downgrade tier.

## Confidence & Tier Distribution Targets

| Tier | Target % | Trigger |
|------|---------:|---------|
| `person` | 40-60% | person_quality ≥ 2 |
| `company` | 15-30% | company_quality ≥ 2, person_quality < 2 |
| `company_light` | 5-15% | exactly one tier scored 1 |
| `default` | 10-30% | both tiers scored 0 |

If `default` exceeds 50% → WARN: contacts mostly lack public footprint. Recommend lowering KPI or restricting to senior-only roles.

## Output Schema (per contact)

```json
"personalization": {
  "tier": "person",
  "multi_angle": true,
  "confidence": "high",
  "email_1_hook_type": "path_within_company",
  "email_2_hook_type": "math_on_their_data",
  "facts_cited": [
    "Sales Mgr → Group Director Trading at Innity (LinkedIn/ZoomInfo)",
    "Innity runs 12 markets (company press)"
  ],
  "sources": [
    "https://linkedin.com/in/yeeyin-chong",
    "https://www.innity.com/about"
  ],
  "subject_1": "Yeeyin, the trading desk + 12-market math",
  "email_1_body": "Yeeyin, you've built the trading desk at Innity from the ground up...<br><br>...",
  "email_2_body": "Quick context — Innity running 12 markets means publisher payouts...<br><br>...",
  "email_3_body": "Concrete numbers for an Innity-shaped platform...<br><br>...",
  "email_4_body": "Yeeyin — running the trading desk + 12-market settlement math = ...",
  "person_quality_score": 2,
  "company_quality_score": 2,
  "researched_at": "2026-04-16T12:00:00Z",
  "researcher": "agent-batch-3"
}
```

For `default` tier:

```json
"personalization": {
  "tier": "default",
  "multi_angle": false,
  "confidence": "low",
  "email_1_hook_type": "fallback_template",
  "email_2_hook_type": "fallback_template",
  "facts_cited": [],
  "sources": [],
  "subject_1": "{{first_name}}, quick question about {{company_name}}",
  "email_1_body": "Hi {{first_name}},<br><br>{default V3 Luma or GOD_SEQUENCE Email 1 text}<br><br>{{signature}}",
  "email_2_body": "{default Email 2 text}",
  "email_3_body": "{default Email 3 text}",
  "email_4_body": "{default Email 4 text}",
  "person_quality_score": 0,
  "company_quality_score": 0,
  "researched_at": "...",
  "researcher": "agent-batch-3"
}
```

ALL leads have ALL fields populated. Empty `email_N_body` would render as literal `{{email_N_body}}` in the recipient's inbox. Never empty.

## Run-File Schema Additions

### `companies[domain].research`

```json
"research": {
  "growth_metrics": ["..."],
  "recent_funding": "...",
  "scale_metrics": ["..."],
  "public_quotes": ["..."],
  "recent_news": ["..."],
  "business_model_signals": ["..."],
  "sources": ["..."],
  "quality_score": 2,
  "researched_at": "..."
}
```

Persisted at run-file level. Mode 2/3 future runs reuse this without re-researching.

### `contacts[i].personalization`

See "Output Schema" above. Persisted in `contacts.json` AND copied into `leads_for_push.json.custom_fields`.

## Scaling — Batch Subagents

ONE agent per chunk. Each agent owns ~10-15 contacts AND the unique companies among them. Inside the agent, company research happens first (and is cached in agent memory across its own contacts), then person research, then composition. No cross-agent coordination needed.

| Contacts | Agents | Per Agent | Wall Clock |
|---------:|:------:|:---------:|:----------:|
| < 10 | inline (orchestrator) | — | 2-5 min |
| 10-50 | 2 | 5-25 | 5-8 min |
| 50-150 | 4 | 12-38 | 8-12 min |
| 150-300 | 6 | 25-50 | 12-18 min |
| 300+ | 8 | 38+ | 18-25 min |

**Model**: Sonnet. Research synthesis + composing 4 cohesive emails benefits from reasoning depth more than Haiku gives.

### Subagent Prompt Template

Each agent receives the exact prompt below (filled in by the orchestrator):

```
You are writing per-contact email sequences using the deep-personalization skill.
READ the skill: .claude/skills/deep-personalization/SKILL.md

OFFER CONTEXT:
  Our company: {our_company}
  What we do: {primary_offer}
  Credentials: {licenses}
  Real social proof cases (truth basis — paraphrase, never invent):
    - {case_1}
    - {case_2}
  Default sequence (use verbatim for tier=default contacts):
    Email 1 subject: {default_subject_1}
    Email 1 body: {default_email_1_body}
    Email 2 body: {default_email_2_body}
    Email 3 body: {default_email_3_body}
    Email 4 body: {default_email_4_body}

CONTACTS ({chunk_size}):
  [email | name | title | company | domain | linkedin | segment | org_data]
  ...

PROCESS:

1. UNIQUE COMPANIES in this chunk: {unique_company_list}
   For each company:
     a. WebSearch: "{company_name}" interview OR raised OR growth OR revenue
     b. Extract: growth_metrics, recent_funding, scale_metrics, public_quotes, recent_news, business_model_signals, sources
     c. Score quality (0-3): +1 concrete metric, +1 recent event, +1 public quote/business signal
     d. Cache in memory keyed by domain

2. PER CONTACT:
   a. WebSearch: "{first_name} {last_name}" "{company}" {title}
      If thin: "{first_name} {last_name}" {company_domain} interview OR speaker
   b. Extract person facts: career_path, prior_companies, public_quotes, role_signals, sources
   c. Score quality (0-3): +1 career arc (2+ prior companies), +1 person signal (quote/talk/founder), +1 role-specific signal
   d. ROUTE (Email 1 lead angle):
      person_q >= 2                     → tier=person
      company_q >= 2 (person_q < 2)     → tier=company
      either == 1                       → tier=company_light
      both == 0                         → tier=default
   e. MULTI-ANGLE check:
      multi_angle = (person_q >= 2 AND company_q >= 1) OR (company_q >= 2 AND person_q >= 1)
   f. PICK HOOKS:
      - Email 1 hook: from chosen tier (person if tier=person, company if tier=company)
      - Email 2 hook: if multi_angle, use the OTHER tier — fresh angle, NOT a callback
                     if NOT multi_angle, use competitor-positioning fallback
      - Each hook = exactly ONE from the priority taxonomy
   g. COMPOSE 4 emails (subject_1 + email_1_body + email_2_body + email_3_body + email_4_body):
      - Email 1: full body using Email 1 hook (3-4 ¶, < 600 chars)
      - Email 2: full body using Email 2 hook OR competitor positioning (~100 words)
                 if multi_angle: open as fresh context, NEVER "as I mentioned"
      - Email 3: pricing/social-proof tied to their volume/segment (~80 words)
      - Email 4: 2-3 line channel switch — if multi_angle, weave both narratives briefly
      - For tier=default: use the provided default sequence verbatim
   h. VARY ROLE FRAMING for multiple contacts at the same company (no identical openers)
   i. VERIFY (skill section "Verification") — drop unverified facts, downgrade tier if needed
   j. WRITE entry to chunk dict

3. WRITE to tmp/personalization_chunk_{N}.json:
   {
     "_execution": {"agent_index": N, "started_at": "...", "completed_at": "...",
                    "person": <count>, "company": <count>, "company_light": <count>, "default": <count>},
     "company_research": {
       "{domain}": { ...company research... },
       ...
     },
     "results": {
       "{email_lower}": { ...personalization output schema... },
       ...
     }
   }

SAVE EVERY CONTACT — never skip. Default tier still gets a complete entry.

SILENCE PROTOCOL: produce no chat output. Only the chunk file.
```

### Merge Protocol (orchestrator, sequential — race-free)

```
all_personalizations = {}
all_company_research = {}
for i in range(1, num_agents + 1):
  chunk = load_data(project, f"tmp/personalization_chunk_{i}.json")
  if chunk.success:
    all_personalizations.update(chunk.data.results)
    all_company_research.update(chunk.data.company_research)

# Update contacts.json
contacts_path = f"campaigns/{slug}/contacts.json"
contacts = load_data(project, contacts_path).data
for c in contacts:
  if c["email"].lower() in all_personalizations:
    c["personalization"] = all_personalizations[c["email"].lower()]
  else:
    # Should never happen — agents must save every contact. Defensive fallback.
    c["personalization"] = build_default_personalization(c, default_sequence)
save_data(project, contacts_path, contacts)

# Update run file with company research (persistent, reusable across runs)
run = load_data(project, f"campaigns/{slug}/runs/{run_id}.json").data
for domain, research in all_company_research.items():
  if domain in run["companies"]:
    run["companies"][domain]["research"] = research
save_data(project, f"campaigns/{slug}/runs/{run_id}.json", run)

# Build leads_for_push.json (drives campaign_push)
leads = []
for c in contacts:
  p = c["personalization"]
  leads.append({
    "email": c["email"],
    "first_name": c["first_name"],
    "last_name": c["last_name"],
    "company_name": c["company_name_normalized"],
    "company_domain": c["company_domain"],
    "linkedin_url": c.get("linkedin_url", ""),
    "phone": c.get("phone", ""),
    "title": c["title"],
    "custom_fields": {
      "subject_1": p["subject_1"],
      "email_1_body": p["email_1_body"],
      "email_2_body": p["email_2_body"],
      "email_3_body": p["email_3_body"],
      "email_4_body": p["email_4_body"],
      "personalization_tier": p["tier"],
      "multi_angle": "yes" if p["multi_angle"] else "no",
      "personalization_confidence": p["confidence"],
      "email_1_hook": p["email_1_hook_type"],
      "email_2_hook": p["email_2_hook_type"],
      "facts_cited": "; ".join(p["facts_cited"]),
      "sources": "; ".join(p["sources"]),
    }
  })
save_data(project, f"campaigns/{slug}/leads_for_push.json", leads)
```

## Quality Gate (before SmartLead push approval)

Surface to user at Checkpoint 2:

```
PERSONALIZATION COVERAGE (KPI=100):
  Person tier:         58 (58%)  ← Email 1 person hook
  Company tier:        19 (19%)  ← Email 1 company hook, role-framed
  Company-light tier:   8 (8%)   ← single weak signal
  Default tier:        15 (15%)  ← default sequence text

  Multi-angle eligible: 64 (64%)  ← Email 1 person + Email 2 company (or vice versa)
  Single-angle:         22 (22%)
  Default-only:         15 (15%)

  Person+Company combined: 77% (target ≥ 70%)  ← PASS

  Email 1 hook distribution:
    concrete_number     22
    math_on_their_data  14
    career_path         18
    scaling_pain        11
    role_daily_pain      9
    path_within_company  3
    fallback_template   15

  Email 2 hook distribution (multi-angle leads only):
    math_on_their_data  19
    concrete_number     18
    scaling_pain        12
    role_daily_pain      8
    competitor_position 22 (when no multi-angle company fact)

  Sample (random 3 high-confidence multi-angle):
    Yeeyin (Group Dir Trading, Innity)
      E1: "Yeeyin, you've built the trading desk from the ground up..."  ← person
      E2: "Quick context — Innity running 12 markets means publisher payouts..."  ← company
    Rafi (CEO, Kendago)
      E1: "Rafi, growing 600% in a year means everything breaks..."  ← person (founder)
      E2: "Performance agencies at this growth rate typically..."  ← company
    Shreyans (Founder, RTBDemand)
      E1: "Shreyans, with your Goldman background heading RTBDemand..."  ← person
      E2: "Quick math — at 1.5B impressions/month, even 10% via SWIFT..."  ← company

  Cost: $1.18 (research + composition for 100 contacts)
  Time: 8m 42s (4 parallel agents)
```

If person+company < 50% → WARN strongly.
If default > 50% → WARN strongly + recommend reducing KPI or restricting to senior-only roles.

## Cost & Time

| KPI | Unique companies | Tier-1 cost | Tier-2 cost | Composition cost | Wall clock (4-8 parallel) |
|----:|-----------------:|------------:|------------:|-----------------:|--------------------------:|
|  50 |  ~17 | ~$0.05 | ~$0.25 | ~$0.30 | 5-8 min |
| 100 |  ~33 | ~$0.10 | ~$0.50 | ~$0.60 | 8-12 min |
| 200 |  ~67 | ~$0.20 | ~$1.00 | ~$1.20 | 12-18 min |
| 500 | ~167 | ~$0.50 | ~$2.50 | ~$3.00 | 20-30 min |

Total ~$1.20 per 100 contacts. About the same as Apollo credits ($1-2 per 100). Worth it for 2-3x reply rate.

## Mode 3 (Append) — incremental personalization

When `/launch` runs in append mode:
- Existing contacts already in the SmartLead campaign are NOT re-personalized — their custom_fields are locked
- Only new deduped contacts go through Phase 5.5
- Phase 5.5 reads only contacts WITHOUT a `personalization` field

Company research is reused across runs:
```
new_contacts = [c for c in contacts if "personalization" not in c]
domains_needing_research = {c["company_domain"] for c in new_contacts
                            if not run.companies[c["company_domain"]].get("research")}
# Only research domains we haven't researched before. Saves Tier-1 cost on Mode 3.
```

## User Feedback Integration

Highest-priority overrides — applied for the rest of the run AND saved to `~/.gtm-mcp/projects/{slug}/feedback.json` for future runs:

- "Make hooks shorter" → cap icebreaker at 2 sentences
- "Don't use math" → drop `math_on_their_data` from hook priority
- "More casual tone" → adjust subagent prompt
- "Use my voice" + sample → subagents inherit the voice
- "Don't personalize Emails 2-4" → composition writes only Email 1, Emails 2-4 use default text
- "Increase default-tier threshold" → e.g., require quality_score ≥ 3 for person tier

## Anti-Patterns

- **Hook stacking**: career_path + concrete_number + math in one paragraph → reads like a dossier, not an email. ONE hook per opener.
- **Fake numbers as filler**: "10x growth" with no source → if recipient knows it's wrong, kills credibility AND deliverability.
- **Praise without context**: "I admire your work" — flattery, not personalization. Cite WHAT you read.
- **Long icebreakers**: >3 sentences → reader skims past the value prop.
- **Identical openers across contacts at same company**: if Rafi and his CFO both get "growing 600% in a year means..." → comparing notes exposes the templating. Vary role-framing.
- **Personalizing low-tier as if high-tier**: thin signal stretched into a full custom email reads worse than honest default. If quality_score < 2, don't fake it — use default tier.
- **Re-researching across runs**: company research is project-persistent. Don't re-WebSearch a company already in `companies[domain].research`.
