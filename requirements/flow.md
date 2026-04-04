# GTM-MCP Open Source — Pipeline Flow

How everything works when a user interacts with the system.

---

## Setup (one-time)

```bash
git clone <repo> gtm-mcp
cd gtm-mcp
pip install -e ".[dev]"          # installs gtm-mcp binary
```

Create `~/.gtm-mcp/config.yaml`:
```yaml
apollo_api_key: "your-key"
smartlead_api_key: "your-key"
getsales_api_key: ""             # optional
apify_proxy_password: ""         # optional, improves scraping
```

Open Claude Code in this directory:
```bash
claude
```

Claude Code auto-discovers:
- `CLAUDE.md` — architecture, rules, critical pipeline constraints
- `.mcp.json` — local MCP server config (stdio, `gtm-mcp` binary)
- `.claude/skills/` — 8 domain knowledge files (the brain)
- `.claude/commands/` — `/leadgen`, `/qualify`, `/outreach`, `/replies`

No signup. No token. No server deployment. No database.

---

## Architecture: Who Does What

```
┌────────────────────────────┐
│  User's Claude Code Agent  │  ← ALL reasoning happens here
│  (reads skills, applies    │
│   rules, makes decisions)  │
└────────────┬───────────────┘
             │ stdio (MCP protocol)
             ▼
┌────────────────────────────┐
│  gtm-mcp (local binary)   │  ← ZERO intelligence, just API wrappers
│  27 tools: apollo_search,  │
│  smartlead_create_campaign,│
│  scrape_website, etc.      │
└────────────┬───────────────┘
             │ HTTPS
             ▼
┌────────────────────────────┐
│  External APIs             │
│  Apollo, SmartLead,        │
│  GetSales, Apify           │
└────────────────────────────┘
```

**No GPT/OpenAI/Anthropic calls inside the server.** The user's Claude Code subscription powers all reasoning.

---

## Example Flow: "launch smartlead as per outreach-plan-fintech.md"

### Phase 1: Document Extraction

**What happens:**
1. Claude reads `outreach-plan-fintech.md` (direct file read — it's in the repo)
2. Claude reads `.claude/skills/offer-extraction.md` skill
3. Skill tells Claude: extract offer, segments, roles, keywords, geo, funding, sequences, exclusions
4. **Claude itself extracts** (no server call, no GPT) → structured data:

```json
{
  "offer": "Done-for-you lead generation — qualified appointments through omnichannel outreach",
  "segments": [
    {"name": "PAYMENTS", "keywords": ["payment gateway API", "PSP platform", ...]},
    {"name": "LENDING", "keywords": ["lending-as-a-service", "loan origination", ...]},
    {"name": "REGTECH", "keywords": ["KYC API", "AML platform", ...]},
    ...all 8 sub-verticals from the document
  ],
  "roles": {
    "primary": ["VP Sales", "Head of Sales", "CRO"],
    "secondary": ["Head of Growth", "VP Marketing", "CMO"],
    "tertiary": ["CEO", "Co-founder"]
  },
  "geo": ["US", "UK", "EU", "UAE", "Singapore"],
  "funding": ["series_a", "series_b", "series_c", "series_d"],
  "size": "20,500",
  "sequences": [3 email sequences from the document],
  "exclusions": []  // competitor conquest = TARGETS, not exclusions
}
```

5. Claude saves this to `runs/run-001.json` via MCP workspace tool

### Phase 2: Filter Generation

**What happens:**
1. Claude reads `.claude/skills/apollo-filter-mapping.md` skill
2. Skill tells Claude:
   - Generate 20-30 keywords from segment keywords (flat list, not per-segment)
   - Pick 2-3 Apollo industries from the 67 real ones (list provided in skill)
   - **1 keyword per Apollo request** — NEVER combine (7x more unique companies)
   - Funding is prioritization filter (nice-to-have, graceful degradation)
   - Employee range inferred from offer context
3. **Claude generates filters itself:**

```json
{
  "keywords": ["payment gateway API", "PSP platform", "merchant acquiring",
               "lending-as-a-service", "KYC API", "neobank", "embedded finance",
               "regtech", "compliance software", ...20-30 total],
  "industry_tag_ids": ["5567cdd67369643e64020000"],  // financial services
  "locations": ["United States", "United Kingdom", "Singapore"],
  "employee_ranges": ["20,500"],
  "funding_stages": ["series_a", "series_b", "series_c", "series_d"]
}
```

### Phase 3: Apollo Probe (Preview)

**What happens:**
1. Claude calls MCP tool `apollo_search` with first 3 keywords + first industry tag (max 6 calls)
2. Each call returns raw company data + Apollo's total_entries estimate
3. Claude shows user a preview:

```
Estimated companies:
  Industry: financial services → ~3,200
  Keyword: payment gateway API → ~850
  Keyword: lending-as-a-service → ~200
  Keyword: KYC API → ~9

25 keywords ready. Estimated cost: ~30 credits.
Default KPI: 100 verified contacts.

Proceed?
```

4. Claude waits for user confirmation. **Never spends credits without explicit approval.**

### Phase 4: Round-Based Gathering

**What happens after user confirms:**

```
ROUND 1:
  Claude calls apollo_search × 25 (1 keyword per call, all parallel)
  Claude calls apollo_search × 1 (industry tag_id, parallel with keywords)
  If funding: also funded variants of all above (parallel)
  → Raw companies stream back
  → Claude deduplicates by domain → ~400-900 unique
  → Saved to runs/run-001.json with found_by tracking per company
```

Each MCP tool call is just:
```python
# src/gtm_mcp/tools/apollo.py — zero intelligence
async def apollo_search(keyword_tags, locations, ...):
    resp = await httpx.post("https://api.apollo.io/...", json=payload)
    return resp.json()
```

### Phase 5: Scrape + Classify

**What happens:**
1. Claude calls MCP tool `scrape_website` for each company domain
   - Tool returns raw HTML → clean text. No AI.
2. Claude reads `.claude/skills/company-qualification.md` skill
3. **Claude classifies each company itself** using via negativa:
   - Read scraped text
   - Apply skill rules: "EXCLUDE if clearly NOT a buyer, INCLUDE if could be a customer"
   - Assign segment label (PAYMENTS, LENDING, REGTECH, etc.)
   - Output: `{is_target, segment, reasoning}` per company

For 400+ companies, Claude spawns a **batch agent** (`.claude/agents/`) to handle the volume without overwhelming the main conversation.

4. Claude reads `.claude/skills/quality-gate.md` skill — checks KPIs:
   - Enough targets for 100 contacts? (need ~34 targets at 3 contacts each)
   - If YES → proceed to people extraction
   - If NO → generate more keywords (skill has 10 regeneration angles) → Round 2

### Phase 6: People Extraction

**What happens:**
1. For each target company, Claude calls:
   - MCP tool `apollo_people_search(domain, seniorities)` — FREE endpoint
   - MCP tool `apollo_enrich_people(person_ids)` — 1 credit per person
2. Roles from the extraction (VP Sales, CRO, CMO) used for search
3. Only verified emails kept
4. Claude tracks: total contacts found, credits spent
5. When 100 contacts reached → STOP

### Phase 7: Email Sequence Generation

**What happens:**
1. Claude reads `.claude/skills/email-sequence.md` skill (GOD_SEQUENCE — 12 rules)
2. The document already has 3 sequences (Fintech Pipeline Pain, Fresh Funding, Competitor Conquest)
3. Claude adapts them to SmartLead format:
   - `{{first_name}}`, `{{company_name}}`, `{{signature}}` (SmartLead variables)
   - `<br>` for line breaks
   - No em dashes
4. **Claude writes the sequences** — the skill provides structure + rules, Claude applies them

### Phase 8: SmartLead Campaign Push

**What happens:**
1. Claude calls MCP tools in sequence:

```
smartlead_create_campaign(name="Fintech Pipeline Pain — US")
  → Returns campaign_id

smartlead_add_leads(campaign_id, contacts=[...])
  → Uploads 100+ contacts with segment as custom field

smartlead_set_sequences(campaign_id, steps=[...])
  → Uploads 4-step email sequence

smartlead_set_settings(campaign_id, {
  plain_text: true,
  stop_on_reply: true,
  tracking: false,
  schedule: "Mon-Fri 9-18 target timezone"
})

smartlead_send_test_email(campaign_id, user_email)
  → Test email to user's inbox
```

2. Campaign created as **DRAFT** — NEVER activated without user saying "activate"

### Phase 9: User Activates

```
User: "activate"
Claude: smartlead_activate_campaign(campaign_id)
→ Campaign goes LIVE
→ Reply monitoring starts (if configured)
```

---

## Pipeline State: File-Based Tracking

Every run produces `runs/run-{id}.json`:

```json
{
  "id": "001",
  "project": {
    "offer": "...",
    "segments": [...],
    "geo": [...],
    "seed_keywords": [...]
  },
  "rounds": [
    {
      "round": 1,
      "requests": [
        {"type": "keyword", "value": "payment gateway API", "pages": 3,
         "raw": 245, "new_unique": 198, "targets": 62, "credits": 3},
        {"type": "industry", "value": "5567cdd6...", "pages": 5,
         "raw": 450, "new_unique": 380, "targets": 95, "credits": 5}
      ],
      "companies": {"total": 487, "targets": 52, "target_rate": 0.107}
    }
  ],
  "companies": {
    "stripe.com": {"found_by": ["payment gateway API"], "is_target": true, "segment": "PAYMENTS"},
    "lendio.com": {"found_by": ["lending-as-a-service"], "is_target": true, "segment": "LENDING"}
  },
  "contacts": [...],
  "summary": {
    "total_credits": 23,
    "total_companies": 487,
    "total_targets": 52,
    "total_contacts": 134,
    "kpi_met": true
  }
}
```

No database. No server state. Everything in files — inspectable, version-controlled, portable.

---

## Cross-Run Learning

`~/.gtm-mcp/filter_intelligence.json` accumulates quality scores:

```json
{
  "keywords": {
    "payment gateway API": {"times_used": 3, "avg_target_rate": 0.31, "total_targets": 186},
    "KYC API": {"times_used": 2, "avg_target_rate": 0.02, "total_targets": 1}
  },
  "industries": {
    "financial services": {"times_used": 5, "avg_target_rate": 0.25}
  }
}
```

Future runs start with proven keywords as seeds — bad keywords deprioritized automatically.

---

## Key Differences: Open-Source vs Hosted (gtm-mcp.com)

| Aspect | Open-Source | Hosted |
|--------|------------|--------|
| **AI reasoning** | User's Claude Code | Server-side GPT calls |
| **Auth** | None (local) | Token + signup |
| **Storage** | JSON files | PostgreSQL |
| **Deployment** | `pip install` + local | Docker on Hetzner |
| **Cost** | User's Claude subscription + API keys | Platform fee + API keys |
| **Multi-user** | No (single user) | Yes |
| **UI** | Terminal only | React dashboard |
| **State** | File-based runs/ | DB tables |
