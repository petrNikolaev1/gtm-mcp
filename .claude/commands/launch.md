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
| Scrape 15-20 probe companies | 15 individual `scrape_website` calls (2+ min!) | `scrape_batch(urls=[...])` ONE call (~3s) |
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

**This command also handles "gather N more" / "add N contacts" / "find more" WITHOUT /launch.**
If the user says "gather 50 more" in an existing conversation where a project + campaign exist:
1. Detect: the user wants Mode 3 continuation
2. Find the active project + campaign from context (state.yaml, or ask if ambiguous)
3. Call `pipeline_prepare_continuation(project, campaign_ref, additional_kpi=50)`
4. Follow the "Gather More" flow (see end of this document)
This works whether user types `/launch campaign=X kpi=+50` OR just says "gather 50 more".

```
Named params:
  project=<slug>        → Mode 2 (new campaign on existing project)
  campaign=<id_or_slug> → Mode 3 (append to existing campaign)
  segment=<name>        → target segment
  geo=<location>        → geography
  kpi=<number>          → target contacts (default 100, "+N" for relative)
  max_cost=<credits>    → Apollo credit cap (auto-calculated if not set)

## Dynamic Scaling — ALL constants derive from KPI

**NOTHING is hardcoded.** Every pipeline parameter scales from KPI + probe_target_rate:

```
# After probe, compute all pipeline parameters:
target_rate = min(probe_target_rate, 0.60)         # CAP at 60% — probe is a small sample,
                                                    # Apollo labels inflate the rate.
                                                    # Real via negativa rates are 40-60%.
                                                    # Overestimating → too few companies gathered → KPI miss.
contacts_per_company = 3                           # avg
scrape_loss = 0.85                                 # ~15% fail to scrape
enrich_loss = 0.85                                 # ~15% enrichment miss

needed_targets = ceil(kpi / contacts_per_company)  # KPI=50 → 17, KPI=100 → 34, KPI=200 → 67
needed_companies = ceil(needed_targets / target_rate / scrape_loss)
max_companies = max(50, ceil(needed_companies * 1.5))  # 1.5x safety margin, min 50
max_credits = max(50, ceil(kpi * 2))               # rough 2x rule, min 50
min_keywords = max(30, ceil(max_companies / 5))    # ~5 unique companies per keyword avg
```

**Examples at different KPIs:**

| KPI | probe_rate | capped_rate | needed_targets | needed_companies | max_companies | min_keywords | max_credits |
|-----|:----------:|:-----------:|:--------------:|:----------------:|:-------------:|:------------:|:-----------:|
| 50  | 40% | 40% | 17 | 50  | **75**  | 30 | 100 |
| 50  | 77% | 60% | 17 | 34  | **51**  | 30 | 100 |
| 100 | 40% | 40% | 34 | 100 | **150** | 30 | 200 |
| 100 | 77% | 60% | 34 | 67  | **101** | 30 | 200 |
| 100 | 94% | 60% | 34 | 67  | **101** | 30 | 200 |
| 200 | 40% | 40% | 67 | 197 | **296** | 60 | 400 |
| 200 | 77% | 60% | 67 | 132 | **198** | 40 | 400 |
| 500 | 40% | 40% | 167 | 491 | **737** | 148 | 1000 |

**Use these computed values everywhere** — in `pipeline_gather_and_scrape(max_companies=...)`, keyword generation count, credit cap display, cost estimate.

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
    → If user provided offer URL (e.g. acme.io), scrape and save to project.yaml
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
   → If found: smartlead_list_accounts() + smartlead_search_accounts(hint, project=project_slug)
   → If NOT found: ASK "Which email accounts should I use? (e.g. 'accounts with Rinat')"
   **CRITICAL: ALWAYS pass project=project_slug to smartlead_search_accounts.**
   The project slug is derived from the project name (create_project returns it).
   If the project doesn't exist yet, call create_project FIRST, then search accounts.
   
   **After selecting accounts, ALWAYS tell the user:**
   "{N} accounts selected. Full list saved to ~/.gtm-mcp/projects/{slug}/selected_accounts.json — review if needed."
   Show the domain breakdown from the tool response (by_domain field).
   Wait for user to confirm before proceeding. Do NOT continue silently.

2. Blacklist: look for SmartLead campaign URL, "blacklist campaign X", campaign ID
   → If found: pipeline_import_blacklist(project, campaign_id)
   → If NOT found: ASK "Any existing campaigns to blacklist? (URL/name/ID, or 'skip')"

3. Geography: ALWAYS extract AND verify. This is the #1 source of wasted credits.
   → Search scraped website/document for: "available in", "serving", "Excl.", "excluding",
     "not available", "restricted", "sanctioned", geo pages, footer locations, legal terms
   → Extract BOTH:
     - target_locations: countries/regions where they WANT to reach clients
     - excluded_locations: countries/regions they CANNOT serve ("Excl. US, UK", "sanctioned countries")
   → If EXCLUSIONS found: REMOVE them from Apollo locations filter. Show user:
     "Website says 'Excl. {excluded}'. I'll search {remaining_locations}. Correct?"
   → If INCLUSIONS found: confirm with user:
     "Website targets {locations}. Search these? Or broaden/narrow?"
   → If NOTHING found about geo on website: ASK explicitly:
     "No geo restrictions found on the website. Where should I search?
      a) Worldwide (all countries)
      b) Specific countries: ___
      c) Exclude specific countries: ___"
   → NEVER default to "US" silently. NEVER proceed without geo confirmation.
```

**This is a HARD REQUIREMENT. Do NOT proceed to filter generation without geo confirmation from the user.** WARNING: Past runs wasted significant credits because geo was wrong — website listed exclusions but the agent searched IN those excluded regions.

**WARNING:** If the website says "Excl. {countries}" but the agent searches IN those countries, the resulting contacts will be completely unusable. This wastes both search and people credits on wrong-geo contacts.

**Mode 3 (append to existing campaign):**
- **Accounts**: skip question IF campaign already has accounts assigned. Otherwise ask.
- **Blacklist**: AUTO-IMPORT from the target campaign. `pipeline_import_blacklist(project, campaign_id)`.
  Don't ask — if the user says "add more to campaign X", obviously don't re-contact X's existing leads.
- **Geo**: STILL verify geo matches the existing campaign's target regions.

**The document should provide these.** A good outreach plan includes sender info, geo restrictions, and prior campaign context. If the user's document has this info, extract it — don't re-ask. Only ask when the input is genuinely missing this information.

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
save_data(project, "project.yaml", {
  name: project_name,
  slug: project_slug,
  _source: "document" | "website" | "chat",
  primary_offer: extracted.primary_offer,
  value_proposition: extracted.value_proposition,
  target_audience: extracted.target_audience,
  target_roles: extracted.target_roles,
  segments: extracted.segments,                    # ALL segments with keywords
  sequences: extracted.sequences,                  # ALL sequences (not just first!)
  apollo_filters: extracted.apollo_filters,        # locations, excluded_locations, employee_range, etc.
  exclusion_list: extracted.exclusion_list,
  email_accounts: {filter_used, count},
  campaign_settings: extracted.campaign_settings,
  offer_approved: false,
  campaigns: [],
}, mode="write")
# CRITICAL: project.yaml MUST have segments, sequences, apollo_filters, exclusion_list.
# Previous runs failed because agent saved these only to pipeline-config but not project.yaml.
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
- Generate **150-200 keywords** (product names, not generic terms, seeded from examples)
  Keywords are FREE (LLM generation, zero Apollo cost). Generate a LARGE pool upfront.
  Only ~40-60 keywords fire per round (max_companies cap). The rest are saved for
  "gather more" continuation — the deterministic pipeline uses them without LLM.
  More keywords upfront = more rounds possible without keyword regeneration.
- Map locations and employee sizes

**CRITICAL: Generate at least `min_keywords` keywords (from Dynamic Scaling section).** Keywords are FREE (LLM generation). Each keyword fires a separate Apollo request discovering different companies. More keywords = more unique companies. The cost is per-page (1 credit each), but most keywords exhaust in 1 page. The `max_companies` cap stops gathering long before all keywords are used. For KPI=100 at 77% target rate: ~30 keywords. For KPI=200 at 40%: ~88 keywords. Always scale to KPI.

**Probe — ONE deterministic tool call (6 credits max):**
```
probe_result = pipeline_probe(
  project=project_slug,
  run_id=run_id,
  keywords=keywords[:3],
  industry_tag_ids=industry_tag_ids[:3],
  locations=locations,
  employee_ranges=employee_ranges,
  funding_stages=funding_stages,
  max_sample=20,
)
```
**This ONE call does EVERYTHING**: 6 Apollo searches in parallel + batch scrape of ~20 companies.
Returns: breakdown per filter, scraped_texts for classification, total_available per keyword.
Saves probe companies + scraped data to run file automatically.

**Then classify probe results (LLM — the ONLY non-deterministic part of probe):**
```
# Classify from probe_result.data.scraped_texts
For each domain, text in probe_result.data.scraped_texts:
  → Classify using company-qualification skill (via negativa)
  → Record: is_target, confidence, segment, reasoning
```

Calculate `probe_target_rate` = targets / scraped_successfully.

**Zero duplication**: `pipeline_gather_and_scrape` loads probe companies from the run file
into `seen_domains` at startup → skips them entirely. No re-fetching, no re-scraping.

```
# Save probe results to run file immediately
save_data(project, f"runs/{run_id}.json", {
  probe: {breakdown, credits_used, companies_from_probe: len(probe_companies)},
  probe_classified: probe_classifications  # {domain: {is_target, confidence, segment, reasoning}}
}, mode="merge")
```

Show examples in strategy doc:
```
  PROBE CLASSIFICATION (20 sampled, 0 credits):
    Targets: 7/18 scraped (39%)
    ✓ Stax Payments — payment infrastructure (PAYMENTS, 92%)
    ✓ Synctera — BaaS platform (BAAS, 88%)
    ✗ Chase Bank — traditional bank, too large
    ✗ Robinhood — B2C consumer fintech
    ...
```

**Estimate cost** using real probe target rate + actual keyword count:
```
cost = apollo_estimate_cost(
  target_count=kpi,
  contacts_per_company=3,
  target_rate=probe_target_rate,
  num_keywords=len(keywords),         # CRITICAL: search credits scale with keyword count
  num_industries=len(industry_tag_ids),
  has_funding_filter=bool(funding_stages),
  probe_credits=6
)
# max_credits is auto-calculated from KPI (see Dynamic Scaling) unless user specified max_cost=
```
**Without num_keywords, the estimate is 11x off.** Each keyword fires a separate Apollo request (1 credit).
104 keywords × 2 (funded/unfunded) × 1.3 pages = ~270 search credits. The old formula estimated 15.

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
  kpi: {target_people: kpi, max_people_per_company: 3, max_credits: max_credits},  # max_credits from Dynamic Scaling
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

  COST (based on {probe_target_rate}% target rate, {actual_keywords} keywords):
    Search:  ~{search_credits} credits (${search_usd})
    People:  ~{people_credits} credits (${people_usd})
    TOTAL:   ~{total_credits} credits (${total_usd})
    MAX CAP: {max_credits} credits — pipeline STOPS if exceeded

  CAMPAIGN SETUP:
    KPI:         {kpi} contacts, 3/company
    Companies:   max {max_companies} (auto-scaled from KPI + target rate)
    Keywords:    {actual_keywords} generated (min {min_keywords})
    Accounts:    {N} selected
    Sequence:    {sequence_name} ({N} steps)

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
  filters: {keywords_count, keywords: [full_list], industry_tag_ids, locations, employee_ranges},
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

### Phase 0: REUSE UNUSED TARGETS (before spending ANY credits)

**This is the fastest path to contacts — already classified targets from previous runs that never had people extracted.**

```
# Load ALL previous runs for this project
unused_targets = []
for run_file in list_runs(project):  # runs/run-001.json, run-002.json, etc.
  prev = load_data(project, f"runs/{run_file}")
  if not prev: continue
  
  # Find targets that were classified but never enriched
  prev_contacts = {c.get("company_domain") for c in prev.get("contacts", [])}
  for domain, company in prev.get("companies", {}).items():
    cls = company.get("classification", {})
    if cls.get("is_target") and domain not in prev_contacts:
      unused_targets.append({**company, "_source_run": run_file})

# Also check contacts.json for project-level dedup
existing_contacts = load_data(project, "contacts.json") or []
enriched_domains = {c.get("company_domain") for c in existing_contacts}
unused_targets = [t for t in unused_targets if t["domain"] not in enriched_domains]
```

**If unused targets exist — skip straight to people enrichment:**
```
if unused_targets:
  # Sort by confidence (best targets first)
  unused_targets.sort(key=lambda t: -t.get("classification", {}).get("confidence", 0))
  
  # How many do we need?
  needed_companies = ceil(kpi / contacts_per_company)  # e.g. 34 for KPI=100
  batch = unused_targets[:needed_companies]
  
  # Skip Phase A (gather) + Phase B (classify) entirely
  # Go directly to Phase C (people enrichment) with these targets
  target_domains = [t["domain"] for t in batch]
  
  # Log what we're reusing
  "Reusing {len(batch)} pre-classified targets from previous runs.
   Skipping gather + scrape + classify. Going straight to people enrichment.
   Saved: ~{estimated_search_credits} search credits, ~{len(batch) * 3}s scrape time, $0 LLM classify."
  
  # If batch covers KPI → Phase C directly (ZERO Apollo search credits)
  # If batch < needed → Phase C for batch, THEN Phase A for the delta
  remaining_kpi = kpi - (len(batch) * contacts_per_company)
  if remaining_kpi > 0:
    # Recalculate dynamic scaling for the smaller delta
    delta_kpi = remaining_kpi
    delta_max_companies = max(50, ceil(delta_kpi / contacts_per_company / target_rate / scrape_loss * 1.5))
    # Continue to Phase A with reduced max_companies
```

**Why this matters:**
- KPI=100, 162 unused targets from run-001 → **ZERO gather/scrape/classify needed**
- Mode 2 new segment on same project → reuse targets from different segment? No — segments differ. Only reuse same-segment targets.
- Mode 3 append → YES, reuse all unused targets from the same campaign's runs

**This is the FIRST thing checked in Step 4. Before any Apollo API call.**

### Mode 3 dedup + page continuation setup
```
For run_id in campaign.run_ids:
  prev = load_data(project, f"runs/{run_id}.json")
  seen_domains.add(prev.companies.keys())
existing = smartlead_export_leads(campaign_id)
seen_emails = {lead.email for lead in existing.leads}

# Build keyword_start_pages from previous run's leaderboard
# So we don't re-fetch page 1 (already got those companies → now in seen_domains)
keyword_start_pages = {}
for entry in prev.keyword_leaderboard:
  if entry.get("next_page"):  # not exhausted
    keyword_start_pages[entry["filter_value"]] = entry["next_page"]
  # Skip exhausted keywords entirely (next_page = null)
```

### Phase A: GATHER + SCRAPE — one atomic streaming tool call (~30-90s)

**ONE tool call does ALL deterministic I/O. Streaming inside via asyncio.**

```
result = pipeline_gather_and_scrape(
  project=project_slug,                # REQUIRED
  run_id=run_id,                       # REQUIRED
  keywords=approved_keywords,          # min_keywords from Dynamic Scaling section
  industry_tag_ids=approved_tag_ids,   # 2-3 tag_ids from Step 2
  locations=approved_locations,
  employee_ranges=approved_ranges,
  funding_stages=approved_funding,     # or null
  max_companies=max_companies,         # from Dynamic Scaling (KPI-driven, NOT hardcoded 400)
  max_credits=max_credits,             # from Dynamic Scaling — STOPS gathering when hit
  scrape_concurrent=100,
  max_pages_per_stream=5,
  keyword_start_pages=keyword_start_pages,  # Mode 3: skip already-fetched pages
)
```

**What happens INSIDE this one call (asyncio streaming):**
1. Fires ALL keyword + industry Apollo searches in parallel (1 per request)
2. If funding: funded + unfunded variants simultaneously
3. As EACH domain arrives from Apollo → immediately queued for scraping
4. 100 concurrent Apify scrape workers consume the queue
5. Low-yield streams auto-stop (<10 on page 1)
6. Stops at `max_companies` unique companies (from Dynamic Scaling)
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

**ALL intermediate files go to `tmp/` dir** — scrape chunks, classify chunks, debug data.
Keeps project root clean. `tmp/` is kept for debugging, never auto-deleted.

**CRITICAL: Each agent writes to its OWN chunk file, NOT the run file.**

WARNING: Past runs lost the majority of targets because multiple agents wrote to the same run file concurrently.
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
    - For each: is_target (bool), confidence (0-100), segment (CAPS_SNAKE_CASE), reasoning (3-5 sentences)
    - For non-targets: set segment to the REJECTION reason (e.g. B2C_CONSUMER, COMPETITOR)
    - REASONING MUST cite specific evidence from the scraped text (product names, features, pricing)
    - NEVER write generic reasoning like 'B2B company in PAYMENTS segment' — cite WHAT they do from website

    SAVE TO CHUNK FILE (NOT the run file!):
      save_data('{project}', 'tmp/classify_chunk_{N}.json',
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
  # Try with .json first, then without (agents sometimes drop the extension)
  chunk = load_data(project, f"tmp/classify_chunk_{i}.json")
  if not chunk.success:
    chunk = load_data(project, f"tmp/classify_chunk_{i}")
  if chunk.success:
    all_classified.update(chunk.data)
    
# Load run file (has companies from pipeline_gather_and_scrape)
run = load_data(project, f"runs/{run_id}.json")

# Merge classifications INTO existing company records
# NORMALIZE: agents sometimes output flat {is_target, ...} instead of {classification: {...}}
for domain, cls_data in all_classified.items():
  # Normalize flat → nested if needed
  if "is_target" in cls_data and not isinstance(cls_data.get("classification"), dict):
    cls_data = {"classification": cls_data}
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

### Phase C+D+E: PEOPLE → CONTACTS → SHEET → CAMPAIGN — ONE atomic tool

**After classification, ONE tool call does EVERYTHING. Zero agent decisions.**

```
result = pipeline_people_to_push(
  project=project_slug,
  run_id=run_id,
  campaign_name="{project_name} {segment_name} {DD/MM}",  # e.g. "Acme SaaS PAYMENTS 07/04"
  sending_account_ids=selected_account_ids,
  country=country_code,
  segment=segment_name,
  sequence_steps=sequence_steps,        # from Step 6 or from document
  test_email=user_email,
  max_people_per_company=3,
  create_sheet=true,
  mode="create",                        # or "append" for Mode 3
  existing_campaign_id=campaign_id,     # only for Mode 3
)
→ Returns: {targets, contacts, people_credits, total_credits, kpi_met,
            campaign_id, leads_uploaded, sheet_url}
```

**What happens INSIDE this one call:**
1. Load target domains from run file (from classification)
2. `apollo_search_people_batch` — all targets in parallel (FREE)
3. `apollo_enrich_people` — bulk enrich (1 credit per verified email)
4. Save contacts to contacts.json + run file + update totals + set kpi_met + mark people_extracted
5. Export to Google Sheet (auto-share with user_email)
6. Save leads_for_push.json
7. Create SmartLead campaign + sequence + upload leads + test email (Mode 1/2)
   OR append leads to existing campaign + update campaign.yaml (Mode 3)
8. Update all tracking files

**This replaces 6+ separate tool calls and eliminates ALL post-classification errors.**
Fixes: contacts not in run file (#72), campaign.yaml not updated (#73), credit accounting (#64).

Record: `round.timestamps.people_completed = "{now}"`

### KPI check + save

```
If len(verified_contacts) >= kpi.target_people → proceed to Step 6
If credits_used >= max_credits → STOP with warning
If not enough targets → next keyword batch (Round 2)
```

**CRITICAL: Save EVERYTHING to the run file.** WARNING: Past runs failed because contacts and campaign data weren't saved.

```
# 1. Mark enriched companies (so Phase 0 knows which targets are "used")
for domain in target_domains:
  if domain in companies:
    companies[domain]["people_extracted"] = true

# 2. Save contacts to BOTH contacts.json AND run file
save_data(project, "contacts.json", all_contacts, mode="write")
save_data(project, "runs/{run_id}.json", {
  companies: companies,                  # includes people_extracted=true flags
  contacts: all_contacts,
  totals: {
    ...existing_totals,
    contacts_extracted: len(all_contacts),
    kpi_met: len(all_contacts) >= kpi.target_people,
    total_credits: total_credits_search + people_credits  # FIX #64: sum ALL credits
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

## Step 5: People Extraction Details (REFERENCE ONLY — pipeline_people_to_push handles this)

**If using `pipeline_people_to_push` (recommended), skip Steps 5-7 entirely.** The atomic tool does all of this internally.
The details below are reference for understanding what happens inside the tool, or for manual fallback if the tool fails.

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

**CRITICAL: Save contacts via pipeline_save_contacts — NOT manual save_data.**

This has been broken in EVERY test run. The agent saves contacts.json but forgets the run file.
`pipeline_save_contacts` saves to BOTH files AND updates totals AND sets kpi_met in one atomic call.

```
# MANDATORY — ONE tool call for ALL contact saving:
pipeline_save_contacts(
  project=project_slug,
  run_id=run_id,
  contacts=all_contacts,
  people_credits=len(verified_contacts)  # 1 credit per verified contact
)
# Credits computed FROM run file automatically:
# total_credits = probe (from run.probe) + search (from gather) + people
# Agent does NOT pass search_credits — eliminates accounting bugs.
```

**NEVER save contacts manually with save_data.** NEVER save contacts.json separately.
This ONE call handles: contacts.json + run file contacts + totals.total_credits + totals.kpi_met.
Past runs showed 0 contacts in the run file when contacts were saved manually instead of using this tool.

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

### Campaign naming convention

**Format: `{project_name} {segment_name} {DD/MM}`**

Examples:
- "{project_name} {segment_name} DD/MM" — e.g. "Acme SaaS PAYMENTS 07/04"
- "{project_name} {segment_name} DD/MM" — e.g. "BetaPay NETWORKS 07/04"
- "{project_name} {segment_name} DD/MM" — e.g. "EasyStaff LENDING 15/03"

NEVER use generic names like "Fintech — Global" or "Campaign 1".

### Create campaign + upload leads — ONE atomic tool call (Mode 1/2)

**Do NOT call smartlead_create_campaign, smartlead_set_sequence, smartlead_add_leads separately.**
**Use `campaign_push` — one tool call does everything deterministically.**

```
# First, save leads to a file (tool reads from disk — no size limit)
save_data(project, "leads_for_push.json", all_leads_array)

# ONE tool call: create → sequence → upload all leads → test email
result = campaign_push(
  project=project_slug,
  campaign_name="{project_name} {segment_name} {DD/MM}",  # e.g. "Acme SaaS PAYMENTS 07/04"
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

### Mode 3 (append): Add leads to EXISTING campaign — NOT campaign_push

**Mode 3 does NOT create a new campaign.** The campaign already exists in SmartLead.

```
# Save leads file
save_data(project, "leads_for_push.json", all_leads_array)

# Add leads to existing campaign (chunks of 100 internally)
smartlead_add_leads(campaign_id=existing_campaign_id, leads=all_leads_array)

# MANDATORY: Update local campaign.yaml with new totals
campaign_yaml = load_data(project, f"campaigns/{slug}/campaign.yaml")
save_data(project, f"campaigns/{slug}/campaign.yaml", {
  total_leads_pushed: campaign_yaml.total_leads_pushed + len(all_leads_array),
  run_ids: [...campaign_yaml.run_ids, run_id]
}, mode="merge")

# Update run file
save_data(project, f"runs/{run_id}.json", {
  campaign_id: existing_campaign_id,
  campaign_slug: slug,
  campaign: {campaign_id: existing_campaign_id, leads_pushed: len(all_leads_array), pushed_at: "{now}"}
}, mode="merge")
```

**WARNING:** Past runs showed leads uploaded to SmartLead successfully but campaign.yaml not updated (stale lead count and empty run_ids=[]).

### Update tracking — ALL THREE files must be updated

**CRITICAL: Past runs failed here — agent didn't update run file or campaign.yaml after push.**

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
Campaign Ready (DRAFT) — Checkpoint 2:

  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics
  Google Sheet: {url}

  Accounts:   {N} assigned
  Sequence:   {sequence_name} — {N} steps (Day {cadence})
  Contacts:   {N} verified → uploaded
  Test email: sent to {user_email} — check your inbox

  COST:
    Search:   {search_credits} credits (${search_usd})
    People:   {people_credits} credits (${people_usd})
    TOTAL:    {total_credits} credits (${total_usd})

  PIPELINE STATS:
    {total_companies} gathered → {scraped} scraped ({scrape_pct}%) → {targets} targets ({target_pct}%) → {contacts} contacts
    {companies_with_contacts} companies, avg {avg_contacts_per_company} contacts/company
    Segments: {segment_breakdown}

  TOP KEYWORDS (by quality score):
    "{keyword_1}" — {target_rate}% target rate, {companies} companies
    "{keyword_2}" — {target_rate}% target rate, {companies} companies
    "{keyword_3}" — {target_rate}% target rate, {companies} companies
    "{keyword_4}" — {target_rate}% target rate, {companies} companies

  UNUSED TARGETS — next run sweetest spot:
    {unused_count} pre-classified targets available (not yet enriched)
    Segments: {unused_segment_breakdown}
    Estimated contacts: ~{unused_count * avg_contacts_per_company} (at {avg_contacts_per_company}/company)
    Estimated cost: ~{unused_count * contacts_per_company} people credits only ($X) — ZERO search/scrape/classify
    → Say "add {unused_contacts} more" to harvest for free

  KEYWORDS — {never_fired_count} never-fired, {has_more_pages_count} with more pages:
    Full list: ~/.gtm-mcp/projects/{project_slug}/runs/{run_id}.json → filter_snapshots[0].filters.keywords
    Top unused: {top_5_never_fired_keywords}

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

---

## "Gather More" — Continuation After KPI Met

**When the user says "gather 50 more" / "add 50 contacts" / "find more" after a completed run:**

This triggers Mode 3 continuation. The flow is deterministic:

```
# Step 1: Prepare continuation state (ONE deterministic tool call)
state = pipeline_prepare_continuation(
  project=project_slug,
  campaign_ref=campaign_id_or_slug,
  additional_kpi=50
)

# Step 2: Check Phase 0 — can unused targets cover KPI?
if state.data.phase_0_sufficient:
  # FAST PATH: Zero search credits. Just enrich unused targets.
  pipeline_people_to_push(
    project=project_slug,
    run_id=state.data.new_run_id,
    campaign_name=existing_campaign_name,
    sending_account_ids=existing_account_ids,
    country=country, segment=segment,
    sequence_steps=existing_sequence,
    mode="append",
    existing_campaign_id=state.data.campaign_id,
    include_domains=state.data.unused_targets.domains[:needed],  # Phase 0
    exclude_emails=existing_campaign_emails,
  )
  # DONE. Show: "Added {N} contacts from {unused} pre-classified targets. $X people credits only."

# Step 3: If Phase 0 insufficient — gather more + classify + push
else:
  # 3a. Enrich whatever unused targets exist (partial Phase 0)
  if state.data.unused_targets.count > 0:
    # Enrich unused targets first, reduce remaining KPI
    # ... (call pipeline_people_to_push with include_domains for the partial batch)
  
  # 3b. Gather new companies — OPTIMIZED keyword order
  # pipeline_prepare_continuation returns optimized_keywords:
  #   1. Never-fired keywords (fresh, zero cost, untouched Apollo data)
  #   2. Best performers from leaderboard (proven high target_rate, page 2+)
  # This ensures max new companies per credit spent.
  pipeline_gather_and_scrape(
    keywords=state.data.optimized_keywords,               # NOT all keywords — OPTIMIZED order
    industry_tag_ids=state.data.continuation_filters.industry_tag_ids,
    locations=state.data.continuation_filters.locations,
    employee_ranges=state.data.continuation_filters.employee_ranges,
    keyword_start_pages=state.data.keyword_start_pages,   # page 2+ for fired keywords
    max_companies=state.data.dynamic_scaling.max_companies,
    max_credits=state.data.dynamic_scaling.max_credits,
    project=project_slug,
    run_id=state.data.new_run_id,
  )
  
  # 3c. Classify new companies (LLM — spawn Haiku agents)
  # ... same as normal Phase B
  
  # 3d. Push to campaign
  pipeline_people_to_push(
    ..., mode="append", existing_campaign_id=state.data.campaign_id,
  )
```

**Key benefits:**
- `pipeline_prepare_continuation` computes EVERYTHING the agent needs (zero math)
- Phase 0 = zero search credits (just people enrichment on pre-classified targets)
- `optimized_keywords` = never-fired first, then best performers — max companies per credit
- `keyword_start_pages` skips already-fetched pages
- `max_credits` enforces budget
- `exclude_emails` deduplicates against existing campaign contacts

### When to regenerate keywords (LLM — rare)

**Only when `pipeline_prepare_continuation` returns `keyword_stats.never_fired == 0 AND keyword_stats.has_more_pages == 0`.**
This means ALL keywords are exhausted — every keyword has been tried on all available pages.

In practice this is rare: 150-200 keywords × 3-5 pages each = 450-1000 potential Apollo requests.
At ~100 companies per round, you need 3-5 rounds to exhaust everything.

If exhausted:
1. `pipeline_prepare_continuation` sets `keywords_exhausted: true` in the response
2. Agent uses quality-gate skill's 10 regen angles to generate 50 NEW keywords from:
   - Apollo keywords found on TARGET companies (reverse-engineer what works)
   - Adjacent product terms not yet tried
   - Competitor/alternative product names
3. Save new keywords to filter_snapshot (append, don't overwrite)
4. Run gather with the new keywords

**This is the ONLY moment LLM is needed for keywords after the initial generation.**
