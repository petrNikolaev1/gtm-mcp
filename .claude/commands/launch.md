---
description: Run the full lead generation pipeline — from offer extraction through Apollo search to SmartLead campaign
argument-hint: "[website/file/text] — e.g. 'https://acme.com payments in US' or 'outreach-plan.md' or 'campaign=3070919 kpi=+100'"
---

# /launch $ARGUMENTS

Full pipeline: input → gather → qualify → SmartLead campaign. **Two human checkpoints, everything else autonomous.**

## Speed: Deterministic Tools First, LLM Only Where Essential

**The pipeline has 3 atomic deterministic tools that do 90% of the work. LLM is only for offer extraction, keyword generation, and classification.**

### Deterministic tools (Python, asyncio, zero LLM):

| Tool | What it does | Speed |
|------|-------------|-------|
| `pipeline_gather_and_scrape` | All Apollo searches + all scraping in one call, streaming | ~60-90s |
| `campaign_push` | Create campaign + sequence + upload ALL leads + test email | ~10s |
| `pipeline_save_contacts` | Save contacts to both contacts.json AND run file + totals | ~1s |
| `pipeline_compute_leaderboard` | Compute keyword quality scores from run data | ~1s |
| `sheets_export_contacts` | Create Google Sheet with contacts + reasoning | ~5s |
| `smartlead_export_leads` | Export campaign leads for blacklist | ~3s |
| `smartlead_get_campaign` | Load reference sequence from existing campaign | ~3s |
| `smartlead_list_accounts` | Cache 2000+ accounts, return summary | ~5s |
| `smartlead_search_accounts` | Filter cached accounts by name/domain | ~instant |
| `apollo_enrich_companies` | Enrich example companies for keyword seeds | ~5s |

### LLM needed (cannot be deterministic):

| Task | Why LLM | Speed | Can optimize? |
|------|---------|-------|---------------|
| **Offer extraction** | Analyze text → structured JSON | ~30s | No — requires understanding |
| **Keyword generation** | Pick industry-specific terms from taxonomy | ~20s | Partially — example enrichment reduces LLM work |
| **Classification** | Via negativa judgment on scraped text | ~3-5min (Haiku agents) | No — requires reasoning per company |

### NOT LLM (common mistakes to avoid):

| Task | WRONG (LLM) | RIGHT (deterministic tool) |
|------|-------------|---------------------------|
| Upload leads to SmartLead | Agent batching via LLM agent (4m 46s!) | `campaign_push` reads file, chunks 100 (~10s) |
| Save contacts to run file | Agent load→update→write (fails) | `pipeline_save_contacts` (~1s) |
| Compute keyword leaderboard | Agent computes inline (skips it) | `pipeline_compute_leaderboard` (~1s) |
| Export to Google Sheet | Could use agent | `sheets_export_contacts` (~5s) |
| Blacklist from campaign | Could use agent | `smartlead_export_leads` + `save_data` (~5s) |
| Sequence from reference campaign | Could use agent to copy | `smartlead_get_campaign` → extract steps (~3s) |
| Save run file after gather | Agent save_data (forgets fields) | `pipeline_gather_and_scrape` auto-saves internally |

**Rule: If a task has NO judgment/creativity, it MUST be a deterministic tool call. Never use LLM for I/O orchestration.**

**Timestamp EVERY phase** in run file for post-run speed analysis:
Record `{phase}_started` and `{phase}_completed` ISO timestamps in `round.timestamps`.
After run, verify: each phase starts within 5s of previous completing. Gaps > 30s = bug.

**Read before starting:**
- **pipeline-state** skill — run file entity format (FilterSnapshot, Company, Contact)
- **io-state-safe** skill — state.yaml schema and validation rules
- **quality-gate** skill — checkpoint thresholds and keyword regeneration angles

## Parse Arguments

```
Named params:
  project=<slug>        → Mode 2 (new campaign on existing project)
  campaign=<id_or_slug> → Mode 3 (append to existing campaign)
  segment=<name>        → target segment
  geo=<location>        → geography
  kpi=<number>          → target contacts (default 100, "+N" for relative)
  max_cost=<credits>    → Apollo credit cap (default 200)

Free text:
  URL (starts with http)     → scrape for offer
  File path (.md/.txt/.pdf)  → read for offer
  "accounts with Renat"      → email account hint
  Everything else            → offer description
```

**Mode detection:**

- User says "add more to campaign X" / `campaign=` / references SmartLead campaign URL → **MODE 3** (append).
  ```
  # Extract campaign_id from URL or argument
  # e.g. "https://app.smartlead.ai/app/email-campaign/3137079/analytics" → 3137079
  
  # Try local lookup first
  local = find_campaign(campaign_ref)
  
  If local found:
    → project + campaign data from local workspace
    
  If local NOT found (external campaign — created outside MCP):
    → Import it: call smartlead_get_campaign(campaign_id) to verify it exists
    → Create local project: derive slug from campaign name
      create_project(campaign_name)
    → Create local campaign.yaml:
      save_data(project, f"campaigns/{slug}/campaign.yaml", {
        campaign_id, name: from SmartLead, status: from SmartLead,
        sending_account_ids: from SmartLead, segment: from user input,
        run_ids: [], total_leads_pushed: existing lead count
      })
    → If user provided offer URL (e.g. inxy.io), scrape and save to project.yaml
    → Now proceed as normal Mode 3 (local data exists)
  
  Call smartlead_get_campaign(campaign_id) → verify not STOPPED.
  ```

- `project=` → **MODE 2**. Call `load_data(project, "project.yaml")` → verify `offer_approved == true`. Check segment not already used in `project.campaigns[]`.

- Neither → **MODE 1**. Fresh project.

**Key: the agent must recognize "add more to this campaign" even without explicit `campaign=` parameter.** If the user references a SmartLead campaign URL and says "add more" / "gather more" / "append" → it's Mode 3.

## Mandatory Questions — RESOLVE IMMEDIATELY

**These are REQUIRED for the pipeline. Check the user's input FIRST — the document or /launch args may already contain the answers. Only ask if missing.**

**Check user input for:**
```
1. Email accounts: look for "accounts with X", "use X accounts", sender name, domain hint
   → If found: smartlead_list_accounts() + smartlead_search_accounts(hint)
   → If NOT found: ASK "Which email accounts should I use? (e.g. 'accounts with Rinat')"

2. Blacklist: look for SmartLead campaign URL, "blacklist campaign X", campaign ID
   → If found: pipeline_import_blacklist(project, campaign_id)
   → If NOT found: ASK "Any existing campaigns to blacklist? (URL/name/ID, or 'skip')"
```

**Mode 3: skip both** — accounts already on campaign, blacklist = auto-dedup from campaign export.

**The document should provide these.** A good outreach plan includes sender info and prior campaign context. If the user's document has this info, extract it — don't re-ask. Only ask when the input is genuinely missing this information.

**Cache accounts in parallel** with offer extraction — `smartlead_list_accounts()` can run alongside `scrape_website()` in the same tool call batch.

## Initialize State

**Before ANY work**, create state.yaml with ALL required fields:
```
save_data(project, "state.yaml", {
  session_id: "launch-{project_slug}-{YYYYMMDD}-{HHMMSS}",
  project: project_slug,
  pipeline: "launch",
  mode: "fresh",                          # or "new_campaign" or "append"
  status: "running",
  current_phase: "offer_extraction",
  active_campaign_slug: null,             # set in Step 7
  active_campaign_id: null,               # set in Step 7
  active_run_id: "run-001",
  phase_states: {
    offer_extraction: "pending",          # Mode 2/3: "skipped"
    filter_generation: "pending",         # Mode 3: "skipped" (reuse previous filters)
    cost_gate: "pending",
    round_loop: "pending",
    people_extraction: "pending",
    sequence_generation: "pending",       # Mode 3: "skipped"
    campaign_push: "pending"
  },
  started_at: "{ISO timestamp}",
  last_updated: "{ISO timestamp}",
  error: null,
  completed_at: null
})
```

## Resume Check

```
existing_state = load_data(project, "state.yaml")
If exists and status != "completed":
  → Show progress, ask "Resume from {current_phase}?"
  → If yes: skip completed/skipped steps
    → For "in_progress" phases: load run file, check what's already done:
      - round_loop in_progress: read run.companies{} — KEEP existing, resume from next batch
      - people_extraction in_progress: read run.contacts[] — KEEP existing, resume from next company
      - campaign_push in_progress: check if campaign created → if yes, just push remaining leads
  → If no: archive old state, start fresh
```

---

## Step 1: Extract Offer (AUTONOMOUS)

**Mode 2/3**: SKIP — offer already approved. Show: "Using offer from {project}."

**Mode 1**:

If URL: `scrape_website(url)` → analyze scraped text.
If file: read file from disk → analyze text.
If text: analyze directly.

Use the **offer-extraction** skill rules to produce structured JSON:
- `primary_offer`, `segments[]` (name + 8-10 SPECIFIC keywords each — product names, not generic terms)
- `target_roles` (primary/secondary/tertiary with seniorities)
- `apollo_filters` (locations, employee_range, industries, funding_stages)
- `exclusion_list` (competitors, wrong_industry, too_large)
- `sequences` (if document contains email sequences — preserve exact text)
- `seed_data` (all segment keywords merged, deduped)

```
save_data(project, "project.yaml", {name, slug, offer: extracted, offer_approved: false, campaigns: []}, mode="merge")
```

**Update state** (do this at EVERY phase boundary — load current state, update, save):
```
save_data(project, "state.yaml", {
  ...current_state,
  current_phase: "filter_generation",
  phase_states: {...current_state.phase_states, offer_extraction: "completed"},
  last_updated: "{ISO timestamp}"
}, mode="write")
```

---

## Step 2: Generate Filters + Probe (AUTONOMOUS)

**Mode 3 (append): SKIP filter generation entirely.** Reuse filters from previous run:
```
# Load the last run for this campaign
prev_run_id = campaign_data.run_ids[-1]   # e.g. "run-001"
prev_run = load_data(project, f"runs/{prev_run_id}.json")

# Reuse the same filters — they already worked for this segment
filters = prev_run.filter_snapshots[-1]   # last (best) filter snapshot
keywords = filters.keywords               # or from keyword_leaderboard (top performers first)
industry_tag_ids = filters.industry_tag_ids
locations = filters.locations
employee_ranges = filters.employee_ranges

# Seed from leaderboard: put best-performing keywords FIRST
keyword_leaderboard = prev_run.keyword_leaderboard
if keyword_leaderboard:
  keywords = [kw.keyword for kw in sorted(keyword_leaderboard, key=lambda k: -k.quality_score)]
  # Exclude exhausted keywords (all pages fetched in previous run)
  # Add any remaining unused keywords at the end

# NO probe needed — we already know the target rate from previous run
# NO taxonomy call needed — tag_ids already resolved
# NO LLM needed — everything is deterministic

# Skip straight to Checkpoint 1 with reused filters
```

This saves ~2-5 minutes of LLM filter generation + 6 probe credits. The filters already proved 73% target rate — no reason to regenerate.

**Mode 1 and 2: Generate filters fresh.**

Use the **apollo-filter-mapping** skill rules.

**Get taxonomy:**
```
taxonomy = apollo_get_taxonomy()
```

**If user provided example companies** (domains like "adsterra.com", "imonetize.com"):
```
# Enrich to discover keywords + industry_tag_ids used by similar companies
enriched = apollo_enrich_companies(example_domains)
→ Extract: industry, industry_tag_id, keywords from each

# PERSIST enrichment to project.yaml seed_data (survives crash/resume)
save_data(project, "project.yaml", {
  seed_data: {
    keywords: [all unique keywords from enriched companies],
    industry_tag_ids: [all unique tag_ids],
    source: "example_companies",
    example_domains: example_domains
  }
}, mode="merge")
```

**Generate filters** from offer + taxonomy + example seeds:
- Pick 2-3 industry tag_ids (SPECIFIC > BROAD, informed by example enrichment)
- Generate **80-100 keywords minimum** (product names, not generic terms, seeded from examples)
- Map locations and employee sizes

**CRITICAL: Generate 80-100+ keywords, not 20-30.** Keywords are FREE (LLM generation). Each keyword fires a separate Apollo request discovering different companies. More keywords = more unique companies. The cost is per-page (1 credit each), but most keywords exhaust in 1 page. 100 keywords at 1 page each = 100 credits max, but the 400-company cap stops gathering long before all keywords are used.

**Probe (6 credits max):**
```
For tag_id in industry_tag_ids[:3]:
  apollo_search_companies({organization_industry_tag_ids: [tag_id], organization_locations: [...], organization_num_employees_ranges: [...]})

For keyword in keywords[:3]:
  apollo_search_companies({q_organization_keyword_tags: [keyword], organization_locations: [...], organization_num_employees_ranges: [...]})
```

Collect probe_breakdown: companies per filter, total available.
Dedup probe results by domain → `probe_companies` list.

**Probe classification (0 credits — scrape + LLM only):**

Pick ~15-20 companies from probe results (mix of keyword and industry streams, skip obvious giants):
```
For company in probe_companies[:20]:
  scraped = scrape_website(company.domain)
  → Classify using company-qualification skill (via negativa)
  → Record: is_target, confidence, segment, reasoning
```

Calculate `probe_target_rate` = targets / scraped_successfully.
This is the REAL target rate from actual data — not a guess. Show examples in strategy doc:
```
  PROBE CLASSIFICATION (20 sampled, 0 credits):
    Targets: 7/18 scraped (39%)
    ✓ Stax Payments — payment infrastructure (PAYMENTS, 92%)
    ✓ Synctera — BaaS platform (BAAS, 88%)
    ✗ Chase Bank — traditional bank, too large
    ✗ Robinhood — B2C consumer fintech
    ...
```

**Estimate cost** using real probe target rate:
```
apollo_estimate_cost(target_count=kpi, contacts_per_company=3, target_rate=probe_target_rate)
```

**Email accounts + blacklist: already asked upfront (see "Mandatory Questions" section above).**
By this point, you already have: selected account IDs + blacklist saved to project.

If user provides campaign for blacklist:
    → Exports leads, saves to projects/{project}/blacklist.json automatically
  → Show: "Blacklisted {N} domains from campaign {name} (project-level)."

If user provides Google Sheet URL:
  → Extract sheet_id from URL
  → sheets_read(sheet_id) → extract domain/email column
  → save_data(project, "blacklist.json", {domain: {"source": "google_sheet"} for domain in domains})

If user says "skip":
  → No blacklist. Show: "No blacklist applied."
```

**This is a HARD REQUIREMENT. Do NOT proceed to Checkpoint 1 without blacklist confirmation from the user.**

**Create run file with ALL required fields:**
```
save_data(project, "runs/run-001.json", {
  run_id: "run-001",
  project: project_slug,
  campaign_id: null,                    # set in Step 7
  campaign_slug: null,                  # set in Step 7
  mode: "fresh",                        # or "append"
  status: "running",
  created_at: "{ISO timestamp}",
  kpi: {target_people: kpi, max_people_per_company: 3, max_credits: max_cost},
  dedup_baseline: {previous_run_companies: 0, previous_run_contacts: 0, seen_domains_count: 0, seen_emails_count: 0},
  probe: {breakdown: [...], credits_used: N, companies_from_probe: N},
  filter_snapshots: [{id: "fs-001", trigger: "initial_generation", filters: {...}}],
  rounds: [],
  requests: [],                         # MUST be populated — see Step 4
  companies: {},
  contacts: [],
  totals: {
    rounds_completed: 0, total_api_requests: 0,
    total_credits_search: 0, total_credits_people: 0, total_credits: N,
    unique_companies: 0, targets: 0, contacts_extracted: 0,
    contacts_deduped_skipped: 0, kpi_met: false
  },
  keyword_leaderboard: [],
  campaign: null                        # populated in Step 7
})
```

**Update state:**
```
save_data(project, "state.yaml", {
  ...current_state,
  current_phase: "cost_gate",
  active_run_id: "run-001",
  phase_states: {..., filter_generation: "completed"},
  last_updated: "{ISO timestamp}"
}, mode="write")
```

---

## Step 3: Strategy Approval — CHECKPOINT 1

**This is the ONLY approval before spending Apollo credits.**

Present everything in ONE document:

```
Strategy Document:

  OFFER: {primary_offer}
  Segments: {segments}
  Target Roles: {roles}
  Exclusions: {exclusions}

  FILTERS:
    Keywords: {N} generated
    Industries: {names} ({N} tag_ids)
    Geo: {locations}
    Size: {employee_range}

  PROBE (6 credits):
    {keyword_1}: {total} companies
    {keyword_2}: {total} companies  
    {keyword_3}: {total} companies
    {industry_1}: {total} companies
    {industry_2}: {total} companies
    → {unique} unique from probe

  PROBE CLASSIFICATION ({N} sampled, 0 credits):
    Target rate: {targets}/{scraped} ({probe_target_rate}%)
    ✓ {company_1} — {reasoning} ({segment}, {confidence}%)
    ✓ {company_2} — {reasoning} ({segment}, {confidence}%)
    ✗ {company_3} — {exclusion_reason}
    ✗ {company_4} — {exclusion_reason}

  COST (based on {probe_target_rate}% real target rate):
    ~{total} credits (${usd}), max cap: {max_cost}
  KPI: {target} contacts, 3/company
  Accounts: {N} selected
  Sequence: GOD_SEQUENCE (4-5 steps)

  Proceed?
```

- "proceed" → set `offer_approved: true`, continue to autonomous gathering
- Feedback → adjust offer/filters, re-present

```
save_data(project, "project.yaml", {..., offer_approved: true}, mode="merge")

# Save the approval document for audit trail
save_data(project, "pipeline-config.yaml", {
  project: project_slug, mode: mode,
  offer: {primary_offer, segments, target_roles},
  filters: {keywords_count, industry_tag_ids, locations, employee_ranges},
  probe: {breakdown, credits_used},
  cost_estimate: {total_credits, usd},
  kpi: {target_people, max_credits},
  email_accounts: selected_account_ids,
  sequence: "from_document" | "GOD_SEQUENCE",
  blacklist: {domains_count},
  status: "approved", approved_at: "{now}"
})

save_data(project, "state.yaml", {..., phase_states: {cost_gate: "completed"}})
```

---

## Step 4: Gather + Scrape + Classify (AUTONOMOUS)

**After user says "proceed", this runs with ZERO interaction.**

Update state: `save_data(project, "state.yaml", {..., current_phase: "round_loop", phase_states: {round_loop: "in_progress"}})`

### Mode 3 dedup setup
```
For run_id in campaign.run_ids:
  prev = load_data(project, f"runs/{run_id}.json")
  seen_domains.add(prev.companies.keys())
existing = smartlead_export_leads(campaign_id)
seen_emails = {lead.email for lead in existing.leads}
```

### Phase A: GATHER + SCRAPE — one atomic streaming tool call (~30-90s)

**ONE tool call does ALL deterministic I/O. Streaming inside via asyncio.**

```
result = pipeline_gather_and_scrape(
  keywords=approved_keywords,          # 20-30 keywords from Step 2
  industry_tag_ids=approved_tag_ids,   # 2-3 tag_ids from Step 2
  locations=approved_locations,
  employee_ranges=approved_ranges,
  funding_stages=approved_funding,     # or null
  max_companies=400,
  scrape_concurrent=100,
  max_pages_per_stream=5
)
```

**What happens INSIDE this one call (asyncio streaming):**
1. Fires ALL keyword + industry Apollo searches in parallel (1 per request)
2. If funding: funded + unfunded variants simultaneously
3. As EACH domain arrives from Apollo → immediately queued for scraping
4. 100 concurrent Apify scrape workers consume the queue
5. Low-yield streams auto-stop (<10 on page 1)
6. Stops at 400 unique companies
7. Returns: companies with scraped text + all request tracking + timestamps

**This is the magnum-opus streaming pattern implemented as one MCP tool.**

The result contains:
- `data.companies`: {domain: {name, apollo_data, discovery, scrape: {status, text_length}}} — NO full text
- `data.scraped_texts`: {domain: text} — separate dict, used for classification agent prompts
- `data.requests`: [{type, filter_value, funded, page, result: {raw, new_unique, credits}}]
- `data.stats`: {gather_seconds, scrape_seconds, total_seconds, total_credits}

**CRITICAL: Save companies WITH scrape metadata to run file immediately.**
```
save_data(project, "runs/{run_id}.json", {
  companies: result.data.companies,   # includes scrape: {status, text_length} per company
  requests: result.data.requests,
  rounds: [{
    id: "round-001",
    timestamps: {
      gather_started: result.data.stats.gather_started,
      gather_completed: result.data.stats.gather_completed,
      scrape_started: result.data.stats.scrape_started,
      scrape_completed: result.data.stats.scrape_completed
    },
    gather_phase: {total_requests: N, unique_companies: N, credits_used: N},
    scrape_phase: {total: N, success: N, failed: N, concurrent: 100}
  }],
  totals: {total_credits_search: N, unique_companies: N}
}, mode="merge")
```

### Phase B: CLASSIFY — dynamic agent spawning with Haiku

Record: `round.timestamps.classify_started = "{now}"`

```
successfully_scraped = {d: text for d, text in result.data.scraped_texts.items()}
count = len(successfully_scraped)
```

**Dynamic agent count based on volume:**

| Scraped | Method | Agents | ~Per agent |
|---------|--------|:------:|:---------:|
| < 30 | Inline (you classify directly) | 0 | — |
| 30-100 | Spawn agents | 2 | ~50 |
| 100-200 | Spawn agents | 3 | ~65 |
| 200-300 | Spawn agents | 4 | ~75 |
| 300-400 | Spawn agents | 5 | ~80 |

**Model: Haiku.** Classification is rule-application (via negativa), not creative reasoning. Haiku is fast, cheap, and follows structured rules perfectly.

```
num_agents = 0 if count < 30 else min(2 + (count - 30) // 100, 5)
chunk_size = count // num_agents if num_agents > 0 else count
```

**CRITICAL: Each agent writes to its OWN chunk file, NOT the run file.**

Run #3 lost 45 of 73 targets because 4 agents wrote to the same run file concurrently.
Race condition: last agent overwrites chunks 1-3. Only chunk 4 survives.

**For each agent N, spawn in parallel:**

```
Agent(
  prompt: "You are a company classifier. Classify each company below.

    CONTEXT:
    Offer: {primary_offer}
    Segments: {segments with names}
    Exclusions: {exclusion_list}

    RULES:
    - Classify from the TEXT BELOW only. NEVER re-scrape. NEVER call scrape_website or Fetch.
    - Via negativa: focus on EXCLUDING non-matches
    - For each: is_target (bool), confidence (0-100), segment (CAPS_SNAKE_CASE), reasoning (1 sentence)
    - For non-targets: set segment to the REJECTION reason (e.g. B2C_CONSUMER, COMPETITOR)

    SAVE TO CHUNK FILE (NOT the run file!):
      save_data('{project}', 'classify_chunk_{N}.json',
        {domain: {classification: {is_target, confidence, segment, reasoning}, name_normalized: cleaned_name}})

    COMPANIES ({chunk_size}):
    1. domain1.com | {scraped_text}
    2. domain2.com | {scraped_text}
    ..."

  model: haiku
  subagent_type: general-purpose
  run_in_background: true
)
```

**Spawn ALL agents in ONE message. Wait for all to complete.**

**After ALL agents done — MERGE chunks into run file (sequential, no race):**

```
# Orchestrator merges — NOT agents. Zero race condition.
all_classified = {}
for i in range(1, num_agents + 1):
  chunk = load_data(project, f"classify_chunk_{i}.json")
  if chunk.success:
    all_classified.update(chunk.data)
    
# Load run file (has companies from pipeline_gather_and_scrape)
run = load_data(project, f"runs/{run_id}.json")

# Merge classifications INTO existing company records
for domain, cls_data in all_classified.items():
  if domain in run.data.companies:
    run.data.companies[domain].update(cls_data)
  else:
    run.data.companies[domain] = cls_data

# Count results
targets = sum(1 for c in run.data.companies.values() if c.get("classification", {}).get("is_target"))
total_classified = sum(1 for c in run.data.companies.values() if c.get("classification"))

# ONE atomic write — all data preserved
save_data(project, f"runs/{run_id}.json", run.data, mode="write")
```

This guarantees ALL classified companies survive. Zero data loss.

Record: `round.timestamps.classify_completed = "{now}"`

### Phase C: PEOPLE — batch search + batch enrich (~20-30s)

Record: `round.timestamps.people_started = "{now}"`

```
# Collect all target domains
target_domains = [d for d, c in companies.items() if c.classification.is_target]

# ONE tool call: search all targets in parallel (FREE, 20 concurrent)
search_results = apollo_search_people_batch(target_domains, person_seniorities=[...], per_page=10)

# Collect top 3 person IDs per company, flatten
all_person_ids = []
for result in search_results.data.results:
  top_3 = [p.id for p in result.people[:3]]
  all_person_ids.extend(top_3)

# ONE tool call: enrich all people (auto-chunks to 10, 1 credit per verified email)
enriched = apollo_enrich_people(all_person_ids)
```

**Two tool calls total for all people extraction.** Not per-company. Not per-person.

Record: `round.timestamps.people_completed = "{now}"`

### KPI check + save

```
If len(verified_contacts) >= kpi.target_people → proceed to Step 6
If credits_used >= max_credits → STOP with warning
If not enough targets → next keyword batch (Round 2)
```

**CRITICAL: Save EVERYTHING to the run file.** Test Run #1 failed because contacts and campaign data weren't saved.

```
# 1. Save contacts to BOTH contacts.json AND run file
save_data(project, "contacts.json", all_contacts, mode="write")
save_data(project, "runs/{run_id}.json", {
  contacts: all_contacts,
  totals: {
    ...existing_totals,
    contacts_extracted: len(all_contacts),
    kpi_met: len(all_contacts) >= kpi.target_people,
    total_credits: search_credits + people_credits
  }
}, mode="merge")

# 2. Save round with timestamps
save_data(project, "runs/{run_id}.json", {
  rounds: [{
    id: "round-001",
    timestamps: {
      gather_started, gather_completed,
      scrape_started, scrape_completed,
      classify_started, classify_completed,
      people_started, people_completed
    },
    gather_phase: {keywords_used, request_ids, unique_companies, credits_used},
    scrape_phase: {total, success, failed, concurrent: 100, duration_seconds},
    classify_phase: {method: "agents"|"inline", agents_spawned, targets, rejected, target_rate, duration_seconds},
    people_phase: {targets_processed, contacts_extracted, credits_used, duration_seconds}
  }]
}, mode="merge")

# 3. Compute keyword leaderboard
For each keyword in requests[]:
  quality_score = target_rate * log(unique_companies + 1) / max(credits, 1)
save_data(project, "runs/{run_id}.json", {keyword_leaderboard: sorted_by_quality}, mode="merge")
```
```

**After all mini-batches complete**, compute totals:
```
run = load_data(project, "runs/{run_id}.json")
targets = count companies where classification.is_target == true
total_classified = count companies where classification exists
total_scraped = count companies where scrape.status == "success"
total_gathered = count all companies
target_rate = targets / total_classified
scrape_success_rate = total_scraped / total_gathered
```

### Quality gate check (4 thresholds)

**After ALL scrape+classify agents complete**, check ALL of these:

```
1. Scrape success rate >= 60%
   FAIL → warn: "{N}% scrape failures. Check if Apify proxy is configured."
   (Continue anyway — classify what we have)

2. Target rate >= 15%
   FAIL → keywords are wrong → regenerate (see below)

3. Targets >= 34 (enough for 100 contacts at 3/company)
   FAIL → need more companies → next keyword batch

4. High-confidence rate >= 50% (confidence >= 80 in >50% of targets)
   FAIL → warn: "Low classification confidence. Consider providing more
   specific exclusion rules." (Continue anyway — people extraction will verify)
```

**Decision matrix:**

```
target_rate >= 15% AND targets >= 34:
  → PASS — proceed to Step 5

target_rate >= 15% BUT targets < 34:
  → Need more companies. Load next keyword batch → gather more → spawn more agents.

target_rate < 15%:
  → Keywords are wrong. Regenerate using quality-gate skill's 10 angles:
    1. Product names from found targets  2. Technology stacks
    3. Use cases and workflows           4. Buyer language  
    5. Adjacent niches                   6. Competitor names
    7. Industry jargon                   8. Problem descriptions
    9. Solution categories              10. Market segments
  → Create new FilterSnapshot (parent_id = previous)
  → New round with fresh keywords → gather → spawn agents
  → Max 5 regeneration cycles

If all 5 regen cycles exhausted AND still < 34 targets:
  → status: "insufficient". Report to user: "Found {N} targets ({rate}%). 
    Not enough for 100 contacts. Options: broaden filters, lower KPI, or proceed with what we have."
```

### Track performance

Per-keyword stats in run file: `unique_companies`, `targets`, `target_rate`, `credits_used`.
This becomes `keyword_leaderboard` — sorted by `quality_score = target_rate * log(unique_companies + 1) / credits`.
Mode 3 future runs seed from this leaderboard.

Update state: `save_data(project, "state.yaml", {..., phase_states: {round_loop: "completed"}})`

---

## Step 5: People Extraction Details

People extraction runs INSIDE Step 4's streaming loop (sub-step 3). These are the detailed rules:

**Use BATCH tools — not per-company calls:**

```
# Collect all target domains
target_domains = [d for d, c in run.companies.items() if c.classification.is_target]

# ONE batch call: search people for ALL targets (FREE, 20 concurrent)
search_results = apollo_search_people_batch(
  target_domains, 
  person_seniorities=["owner","founder","c_suite","vp","head","director"],
  per_page=10
)

# Collect top 3 person IDs per company, flatten into one list
all_person_ids = []
for result in search_results.data.results:
  # Pick top 3 matching target_roles. Priority: owner > founder > c_suite > vp > head > director
  top_3 = [p.id for p in result.people[:3]]
  all_person_ids.extend(top_3)

# ONE batch call: enrich ALL people (1 credit per verified email, auto-chunks to 10)
enriched = apollo_enrich_people(person_ids=all_person_ids)
```

**TWO tool calls for ALL people extraction.** Not per-company. Not per-person.

**Retry**: if total contacts < KPI after first pass, do a second pass with next-3 candidates per under-served company. Still batch calls.

**Contact dedup**: skip duplicate emails within run. Mode 3: also skip `seen_emails`.

**Save contacts — use deterministic tool (not manual load→update→write).**

```
# ONE tool call: saves to contacts.json + run file + updates totals + sets kpi_met
pipeline_save_contacts(
  project=project_slug,
  run_id=run_id,
  contacts=all_contacts,
  search_credits=search_credits,
  people_credits=people_credits
)
```

This is deterministic — no LLM needed. Fixes the persistent bug from Run #1 and #2.

```
save_data(project, "state.yaml", {
  ...current_state,
  current_phase: "sequence_generation",
  phase_states: {..., round_loop: "completed", people_extraction: "completed"},
  last_updated: "{ISO timestamp}"
}, mode="write")
```

---

## Step 6: Generate Sequence (AUTONOMOUS)

**Mode 3**: SKIP — sequence already on campaign.

**Sequence priority (first match wins):**

1. **User referenced existing SmartLead campaign** (e.g. "use sequence from campaign 3137079"):
   ```
   ref_campaign = smartlead_get_campaign(campaign_id)
   raw_sequences = ref_campaign.data.sequences
   
   # Convert SmartLead format → our format for campaign_push:
   sequence_steps = [{
     "step": s["seq_number"],
     "day": s.get("seq_delay_details", {}).get("delayInDays", s.get("seq_delay_details", {}).get("delay_in_days", 0)),
     "subject": s.get("subject", ""),
     "body": s.get("email_body", ""),
   } for s in raw_sequences]
   
   # Note: body is HTML from SmartLead — that's fine, smartlead_set_sequence will pass it through
   ```

2. **User's input document had sequences** → extract, validate, use.

3. **User provided example companies** (e.g. "target companies like iMonetizeIt, Adsterra"):
   ```
   # Enrich examples to understand the niche, then generate sequence for that niche
   enriched = apollo_enrich_companies(["imonetize.com", "adsterra.com", ...])
   → Use enrichment data as context for sequence generation
   ```

4. **Nothing provided** → use **GOD_SEQUENCE** from email-sequence skill:
   - 4-5 steps, Day 0/3/7/14
   - ≤120 words per email
   - A/B subjects on Email 1
   - SmartLead variables: `{{first_name}}`, `{{company_name}}`, `{{city}}`, `{{signature}}`
   - `<br>` for line breaks

```
save_data(project, "sequences.json", {steps: sequence_steps})
save_data(project, "state.yaml", {..., phase_states: {sequence_generation: "completed"}})
```

---

## Step 7: Campaign Push — CHECKPOINT 2

Update state: `save_data(project, "state.yaml", {..., current_phase: "campaign_push", phase_states: {campaign_push: "in_progress"}})`

### Create campaign + upload leads — ONE atomic tool call (Mode 1/2)

**Do NOT call smartlead_create_campaign, smartlead_set_sequence, smartlead_add_leads separately.**
**Use `campaign_push` — one tool call does everything deterministically.**

```
# First, save leads to a file (tool reads from disk — no size limit)
save_data(project, "leads_for_push.json", all_leads_array)

# ONE tool call: create → sequence → upload all leads → test email
result = campaign_push(
  project=project_slug,
  campaign_name="{Segment} — {Geo}",
  sending_account_ids=selected_account_ids,
  country=country_code,
  segment=segment_name,
  sequence_steps=sequence_steps,
  leads_file="leads_for_push.json",
  test_email=user_email                    # from get_config().values.user_email
)
→ Returns: campaign_id, leads_uploaded, test_email_sent
```

This replaces 4+ separate tool calls. Zero LLM needed. ~10 seconds total.

### Update tracking — ALL THREE files must be updated

**CRITICAL: Test Run #1 failed here — agent didn't update run file or campaign.yaml after push.**

```
# 1. Update campaign.yaml with run link + lead count
campaign_yaml = load_data(project, f"campaigns/{slug}/campaign.yaml").data
campaign_yaml["run_ids"] = [run_id]
campaign_yaml["total_leads_pushed"] = len(leads)
save_data(project, f"campaigns/{slug}/campaign.yaml", campaign_yaml)

# 2. Update project.yaml campaigns index
save_data(project, "project.yaml", {
  campaigns: [{slug: slug, campaign_id: campaign_id, segment: segment, country: country, status: "DRAFT"}]
}, mode="merge")

# 3. Update run file with campaign data
save_data(project, f"runs/{run_id}.json", {
  campaign_id: campaign_id,
  campaign_slug: slug,
  campaign: {
    campaign_id: campaign_id,
    leads_pushed: len(leads),
    pushed_at: "{ISO timestamp}"
  }
}, mode="merge")
```

**Verify all three were saved before presenting Checkpoint 2.**

### Test email

**CRITICAL: Use email from config, NOT from the document.**
```
config = get_config()
user_email = config.values.user_email    # from GTM_MCP_USER_EMAIL in .env
# If not set → ask user: "What email should I send the test to?"
# NEVER use an email from the outreach document for test emails.
smartlead_send_test_email(campaign_id, user_email)
```

### Google Sheet — ALWAYS attempt

```
# Always try to create Google Sheet. If credentials not configured, it will return an error — that's OK, skip gracefully.
sheet_result = sheets_export_contacts(project, campaign_slug)
if sheet_result.success:
  sheet_url = sheet_result.data.sheet_url
  # Sheet has contact headers + target_confidence + target_reasoning columns
else:
  sheet_url = null  # Google not configured — show "not configured" in output
```

### Present for activation

```
Campaign Ready (DRAFT):
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics
  Settings: plain text ✓, no tracking ✓, 40% followup ✓
  Accounts: {N} assigned
  Sequence: {N} steps set
  Contacts: {N} verified → uploaded
  Google Sheet: {url}
  Test email sent to {user_email} — check your inbox.
  Cost: {total} credits
  Stats: {companies} → {targets} targets → {contacts} contacts

  Type "activate" to start sending.
```

### Activate

```
smartlead_activate_campaign(campaign_id, "I confirm")
save_data(project, "state.yaml", {..., phase_states: {campaign_push: "completed"}, status: "completed"})
```

### Post-run: update cross-run intelligence

After pipeline completes (before or after activation), compute and save intelligence:

```
# 1. Build keyword leaderboard from this run's request tracking
run = load_data(project, "runs/{run_id}.json")

keyword_leaderboard = []
For each unique keyword in run.requests[]:
  requests_for_kw = run.requests where type=="keyword" and filter_value==keyword
  companies_for_kw = run.companies where found_by_requests intersects requests_for_kw.ids
  targets_for_kw = companies_for_kw where classification.is_target == true
  credits = sum(requests_for_kw.result.credits_used)
  target_rate = len(targets_for_kw) / len(companies_for_kw) if companies_for_kw else 0
  quality_score = target_rate * log(len(companies_for_kw) + 1) / max(credits, 1)
  
  keyword_leaderboard.append({keyword, unique_companies, targets, target_rate, credits, quality_score})

Sort by quality_score DESC.

# 2. TWO deterministic tool calls — compute leaderboard + save intelligence
pipeline_compute_leaderboard(project=project_slug, run_id=run_id)
pipeline_save_intelligence(project=project_slug, run_id=run_id)
```

Both are deterministic tools. Zero LLM. Guaranteed to run and persist.
This ensures Mode 3 future runs and new projects in similar segments start with proven keywords.

---

## Mode 3 Output (append to active campaign)

**Google Sheet + SmartLead link ALWAYS shown — same as Mode 1/2.**

```
sheets_export_contacts(project, campaign_slug)  # contacts with reasoning columns

Contacts Added to Campaign:
  Campaign: {name} (ID: {id})
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics
  NEW: {N} contacts (deduped against {existing})
  TOTAL: {total} in campaign
  Google Sheet: {sheet_url} (shared with {user_email})
  Cost: {credits} credits
  New leads entering sending queue automatically.
```

## When KPI Not Reached — DON'T GIVE UP

**If contacts < 100 after first round, DO NOT stop.** Analyze what worked and suggest next steps:

```
KPI Status: {contacts}/{target} contacts ({contacts/target * 100}%)

What worked:
  Best keywords: {top 3 from keyword_leaderboard by quality_score}
  Best industry: {top from industry results}
  Target rate: {targets}/{classified} ({rate}%)

Options to find more:
  1. "find more" — I'll generate new keywords based on which TARGET companies were found.
     Angles to try: {list unused regeneration angles}
     Example: targets like {top_target_domain} had Apollo keywords: {their_keywords}
     → these keywords could find more similar companies
  
  2. Broaden filters — current geo: {locations}. 
     Adding {suggested_new_geos} could yield {estimate} more companies.
  
  3. Try adjacent segments — targets often overlap with: {related_segments}
  
  4. Lower KPI — proceed with {contacts} contacts. 
     {contacts} contacts × 4% reply rate ≈ {int(contacts * 0.04)} replies.

Which approach? Or type "proceed with {contacts}" to create the campaign now.
```

**The agent MUST suggest concrete next steps based on the keyword_leaderboard data.** Never just say "not enough contacts" and stop.
