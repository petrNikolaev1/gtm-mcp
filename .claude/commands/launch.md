---
description: Run the full lead generation pipeline — from offer extraction through Apollo search to SmartLead campaign
argument-hint: "[website/file/text] — e.g. 'https://acme.com payments in US' or 'outreach-plan.md' or 'campaign=3070919 kpi=+100'"
---

# /launch $ARGUMENTS

Full pipeline: input → gather → qualify → SmartLead campaign. **Two human checkpoints, everything else autonomous.**

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
- `campaign=` → MODE 3. Call `find_campaign(campaign_ref)` → get project + campaign data. Call `smartlead_get_campaign(campaign_id)` → verify not STOPPED.
- `project=` → MODE 2. Call `load_data(project, "project.yaml")` → verify `offer_approved == true`. Check segment not already used in `project.campaigns[]`.
- Neither → MODE 1. Fresh project.

## Resume Check

```
existing_state = load_data(project, "state.yaml")
If exists and status != "completed":
  → Show progress, ask "Resume from {current_phase}?"
  → If yes: skip completed/skipped steps
    → For "in_progress" phases: load run file, check what's already done:
      - round_loop in_progress: read run.companies{} → already gathered/scraped/classified
        companies are KEPT. Resume from next keyword batch, don't re-gather.
      - people_extraction in_progress: read run.contacts[] → already extracted contacts
        are KEPT. Resume from next target company, don't re-enrich.
      - campaign_push in_progress: check if campaign already created in SmartLead
        → if yes, skip creation, just push remaining leads.
    → This prevents wasting Apollo credits on re-doing completed work within a phase.
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

Update state: `save_data(project, "state.yaml", {..., phase_states: {offer_extraction: "completed"}})`

---

## Step 2: Generate Filters + Probe (AUTONOMOUS)

Use the **apollo-filter-mapping** skill rules.

**Get taxonomy:**
```
taxonomy = apollo_get_taxonomy()
```

**Generate filters** from offer + taxonomy:
- Pick 2-3 industry tag_ids (SPECIFIC > BROAD)
- Generate 20-30 keywords (product names, not generic terms)
- Map locations and employee sizes

**Mode 3 extra**: Load `keyword_leaderboard` from previous runs → seed with top performers, exclude exhausted.

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

**Resolve email accounts (user MUST specify):**
```
# Step 1: Cache all accounts (returns summary only — count + domains)
smartlead_list_accounts()

# Step 2: User MUST tell you which accounts. Ask if not provided:
#   "Which email accounts should I use? (e.g. 'accounts with Sally', 'danila@', domain name)"
# NEVER assume accounts — always ask or use the hint from user input.

# Step 3: Search by user's hint
smartlead_search_accounts("sally")  # or whatever the user specified
→ Returns matching account IDs ready for campaign creation
```

**Create run file:**
```
save_data(project, "runs/run-001.json", {
  run_id: "run-001", project, campaign_id, campaign_slug, mode,
  status: "running", kpi: {target_people: kpi, max_people_per_company: 3, max_credits: max_cost},
  probe: {breakdown, credits_used}, filter_snapshots: [fs_001],
  rounds: [], requests: [], companies: {}, contacts: [], totals: {...}
})
```

Update state: `save_data(project, "state.yaml", {..., phase_states: {filter_generation: "completed"}})`

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

### Round loop

**CRITICAL: 1 keyword per request, 1 tag_id per request. NEVER combine.** Combining changes Apollo's ranking and yields fewer unique companies.

**Pagination rules per keyword/industry stream:**
- Max 5 pages per stream (diminishing returns beyond that)
- If page 1 returns <10 companies → stop that stream immediately (low yield)
- If 3 consecutive pages return 0 new unique companies → stream exhausted, stop
- Each page = 1 Apollo credit

**Funding cascade:** If funding filter specified in offer:
- Run BOTH funded AND unfunded variants of each keyword/industry simultaneously
- If funded stream exhausted → continue unfunded only
- Unfunded often has sparse pagination — that's expected, not an error

**Execute in keyword batches** (10 keywords per batch to avoid overwhelming):

BATCH 1 — first 10 keywords + all tag_ids (parallel):
```
# Each as a SEPARATE tool call, all in parallel:
apollo_search_companies({q_organization_keyword_tags: ["keyword1"], organization_locations: [...], organization_num_employees_ranges: [...]})
apollo_search_companies({q_organization_keyword_tags: ["keyword2"], ...})
...
apollo_search_companies({organization_industry_tag_ids: ["tag_id_1"], ...})
...
```

Dedup by domain across ALL results. Skip `seen_domains` (Mode 3). Probe companies from Step 2 are already gathered — skip page 1 for probed filters.

**Track EVERY Apollo request** in the run file as an APIRequest entity:
```
For each apollo_search_companies call, record in run.requests[]:
{
  id: "req-{NNN}",              # sequential within run
  round_id: "round-001",
  filter_snapshot_id: "fs-001",
  type: "keyword" | "industry",
  filter_value: "payment gateway" | "5567cd82...",
  funded: true | false,
  page: 1,
  result: {raw_returned: 100, new_unique: 72, duplicates: 28, credits_used: 1}
}
```

**Track each company's provenance**: `company.discovery.found_by_requests = ["req-003", "req-017"]` — which keyword/industry requests found it.

**Wrap each gather→scrape→classify cycle in a Round**:
```
run.rounds[]: {
  id: "round-001",
  filter_snapshot_id: "fs-001",
  status: "completed",
  gather_phase: {keywords_used: [...], request_ids: [...], unique_companies: N, credits_used: N},
  scrape_phase: {total: N, success: N, failed: N},
  classify_phase: {targets: N, rejected: N, target_rate: 0.27},
  people_phase: {contacts_extracted: N, credits_used: N}   # filled in Step 5
}
```

**Stop adding new keyword batches when 400 unique companies reached in this round.**

### Streaming: scrape → classify → people (don't wait for full phase completion)

**pipeline_spec.md rule: every phase starts IMMEDIATELY when input is available.**
- 1 company found in Apollo → scrape it NOW
- 1 website scraped → classify it NOW
- 1 target confirmed → start people extraction NOW

**In practice with Claude Code**: process in overlapping mini-batches, not sequential full phases.

**STREAMING LOOP** (repeat until KPI met or 400 companies processed):

```
MINI-BATCH (20 companies at a time):

1. SCRAPE — call scrape_website MCP tool directly (NOT agents, NOT Fetch):
   scrape_website("https://" + domain_1)   # 15-20 in parallel
   scrape_website("https://" + domain_2)
   ...
   Store: company.scrape = {status, text_length, text}

2. CLASSIFY — inline (you ARE the LLM, read company-qualification skill rules):
   For each successfully scraped company in this batch:
   - Read scraped text, apply via negativa rules
   - Output: is_target, confidence (0-100), segment, reasoning
   - Normalize company name (strip Inc., LLC, etc.)
   Store: company.classification = {is_target, confidence, segment, reasoning}

3. PEOPLE — for targets in this batch, start extraction immediately:
   For each target (is_target == true) in this mini-batch:
     apollo_search_people(domain=target.domain, person_seniorities=[...])  # FREE
     apollo_enrich_people(person_ids=[top_3])  # 1 credit/person
   Store contacts, update totals.

4. KPI CHECK — after each mini-batch:
   If total_verified_contacts >= kpi.target_people → STOP, proceed to Step 6
   If credits_used >= max_credits → STOP with warning

5. SAVE — update run file after each mini-batch:
   save_data(project, "runs/{run_id}.json", {companies: {...}, contacts: [...], totals: {...}}, mode="merge")
```

**Why inline classification (not agents)?**
- Each mini-batch is 20 companies. You classify them as you read the scraped text.
- Spawning agents for 20 companies adds overhead (agent startup, context loading).
- Agents are valuable for 100+ companies. For streaming mini-batches, inline is faster.
- You already have the company-qualification skill loaded — just apply the rules.

**When to spawn classification agents instead**: If you gathered 400 companies and want to classify all at once (non-streaming mode), spawn 3-4 agents each processing ~100 companies' scraped text. Pass the text in the prompt, NOT domains to scrape.

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

**FREE search → PAID enrichment per target:**
```
# FREE — no credits
people = apollo_search_people(domain=target.domain, person_seniorities=["owner","founder","c_suite","vp","head","director"])

# Pick top 3 matching target_roles. Priority: owner > founder > c_suite > vp > head > director

# 1 credit per person
enriched = apollo_enrich_people(person_ids=[top_3_ids])
```

**Retry logic** (if <3 verified emails):
- Round 2: next 3 candidates (different people, same company)
- Round 3: remaining candidates
- Max 3 enrichment rounds, 12 credits per company
- If still <3 → accept what you have, move on

**Contact dedup**: skip duplicate emails within run. Mode 3: also skip `seen_emails`.

**Side effect**: `apollo_enrich_people` may return `industry_tag_id` → auto-extends taxonomy.

**When KPI met**, save everything:
```
save_data(project, "contacts.json", all_contacts, mode="write")
save_data(project, "runs/{run_id}.json", {..., contacts: all_contacts, totals: {kpi_met: true}}, mode="merge")
save_data(project, "state.yaml", {..., phase_states: {round_loop: "completed", people_extraction: "completed"}})
```

---

## Step 6: Generate Sequence (AUTONOMOUS)

**Mode 3**: SKIP — sequence already on campaign.

Use **email-sequence** skill rules (12-rule GOD_SEQUENCE):
- 4-5 steps, Day 0/3/7/14
- ≤120 words per email
- A/B subjects on Email 1
- SmartLead variables: `{{first_name}}`, `{{company_name}}`, `{{city}}`, `{{signature}}`
- `<br>` for line breaks

If user's input document had sequences → use those instead.

```
save_data(project, "sequences.json", {steps: sequence_steps})
save_data(project, "state.yaml", {..., phase_states: {sequence_generation: "completed"}})
```

---

## Step 7: Campaign Push — CHECKPOINT 2

Update state: `save_data(project, "state.yaml", {..., current_phase: "campaign_push", phase_states: {campaign_push: "in_progress"}})`

### Create campaign (Mode 1/2)

```
campaign = smartlead_create_campaign(project, "{Segment} — {Geo}", account_ids, country_code, segment)
smartlead_set_sequence(project, campaign.slug, campaign.campaign_id, sequence_steps)
```

### Upload contacts (all modes)

```
smartlead_add_leads(campaign_id, [{email, first_name, last_name, company_name, custom_fields: {segment, city}}])
```

### Update tracking

```
save_data(project, f"campaigns/{slug}/campaign.yaml", {..., run_ids: [run_id], total_leads_pushed: N})
save_data(project, "project.yaml", {..., campaigns: [..., {slug, campaign_id, segment, country, status}]}, mode="merge")
save_data(project, "runs/run-001.json", {..., campaign: {campaign_id, leads_pushed: N, pushed_at: now}}, mode="merge")
```

### Test email

```
user_email from get_config() → user_email field. If not set, ask.
smartlead_send_test_email(campaign_id, user_email)
```

### Google Sheet (if configured)

```
sheets_export_contacts(project, campaign_slug)
→ Creates sheet with target_confidence + target_reasoning columns
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

# 2. Save leaderboard to run file
save_data(project, "runs/{run_id}.json", {
  keyword_leaderboard: keyword_leaderboard,
  industry_leaderboard: [...same computation for industries...]
}, mode="merge")

# 3. Update global filter intelligence
existing_intel = load_data("_global", "filter_intelligence.json")

For each keyword in leaderboard:
  If keyword exists in intel.keyword_knowledge:
    Update: avg_target_rate, increment times_used, update best_target_rate
  Else:
    Create new entry with this run's stats

# Update segment playbook for this segment
segment_name = run's primary segment
intel.segment_playbooks[segment_name] = {
  best_keywords: top 5 by quality_score,
  avg_target_rate: from this + previous runs,
  avg_cost_per_contact: total_credits / contacts_extracted
}

save_data("_global", "filter_intelligence.json", intel)
```

This ensures Mode 3 future runs and new projects in similar segments start with proven keywords.

---

## Mode 3 Output (append to active campaign)

```
Contacts Added:
  Campaign: {name} (ID: {id})
  NEW: {N} contacts (deduped against {existing})
  TOTAL: {total} in campaign
  New leads entering sending queue automatically.
```
