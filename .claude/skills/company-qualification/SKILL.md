# Company Qualification Skill

Classify gathered companies as target/not-target using via negativa approach. This skill replaces exploration_service.py, prompt_tuner.py, streaming_pipeline.py classification, and refinement_engine.py.

## When to Use

- After gathering companies from Apollo
- During pipeline's classify phase
- When user runs /qualify command
- When re-analyzing after user feedback

## Via Negativa Classification Method

Instead of defining what a target IS, define what a target is NOT. This approach achieves 97% accuracy.

### 7 Exclusion Rules (check in order)

1. **DIRECT COMPETITOR**: Sells the exact same product/service as our offer. Exclude.
   - Example: If our offer is "payroll platform", exclude other payroll platforms
   - BUT: A company that USES payroll (potential customer) is NOT a competitor

2. **COMPLETELY UNRELATED**: Zero overlap with any segment. Exclude.
   - Example: If targeting fintech, a restaurant chain is unrelated
   - BUT: A restaurant chain with 500+ locations MIGHT need payroll → check context

3. **WRONG GEOGRAPHY/SIZE**: Outside the specified location or employee range. Exclude.
   - This is already handled by Apollo filters, but verify from website content

4. **FREELANCER/SOLO CONSULTANT**: Individual, not a company. Exclude.
   - Clue: personal name as company name, "consultant", "freelance" in title

5. **PLACEHOLDER/PARKED/UNDER CONSTRUCTION**: No real business content. Exclude.
   - Clue: "coming soon", "under construction", single-page with no product info

6. **SHUT DOWN/INACTIVE**: Company no longer operating. Exclude.
   - Clue: "closed", "acquired", no recent activity, dead links

7. **INSUFFICIENT DATA**: Website has too little info to classify. Mark as low confidence.
   - If scrape returned <100 chars of useful text → confidence < 0.3

### Inclusion Signals (what makes a company a TARGET)

- They would BUY our product (they're a CUSTOMER, not a competitor)
- Agency doing work in the segment (they need our tools)
- Platform operating in the space (they need our infrastructure)
- Brand in the target activity (they're the end customer)
- Growing company (funding, hiring, expanding)

### Special Rules

- Recruitment agency ≠ buyer of recruiting tools (unless specifically targeting them)
- General digital marketing agency ≠ buyer of marketing SaaS (unless targeting them)
- "IT services" company could be consulting (target) or product company (competitor) — check website
- Company with multiple business lines → classify based on PRIMARY business

## Classification Output

For EACH company, return:

```json
{
  "is_target": true,
  "confidence": 85,
  "segment": "PAYMENTS",
  "reasoning": "Their website describes PayFlex as an 'enterprise payment orchestration platform' offering APIs for multi-acquirer routing and smart payment retries. The product page mentions PCI DSS Level 1 compliance and integration with 40+ PSPs. This is B2B payment infrastructure — they sell to merchants and marketplaces processing high-volume transactions. Classic buyer for outbound sales pipeline generation."
}
```

### Segment Labels
- For TARGETS: use the segment label from offer extraction (PAYMENTS, LENDING, BAAS, etc.)
- For NON-TARGETS: use what the company ACTUALLY IS (COMPETITOR, CONSULTING_FIRM, RESTAURANT, etc.)
- Always CAPS_SNAKE_CASE, max 30 chars

### Confidence Scoring (0-100 scale)
- 90-100: Clear match/non-match with strong evidence
- 70-89: Good match, some ambiguity
- 40-69: Borderline, needs human review (trigger 2-pass re-evaluation)
- 0-39: Likely non-match or insufficient data

### Reasoning Quality — MUST cite website evidence

**The reasoning field is a PARAGRAPH (3-5 sentences), not a label restatement.**

It MUST include:
1. **What the company does** — cite specific phrases from the scraped website text
2. **Product/service evidence** — quote their product names, features, pricing model
3. **Why target/non-target** — explain the buyer logic (or exclusion reason)
4. **Confidence justification** — if borderline, say what's uncertain

**GOOD reasoning** (specific, evidence-grounded):
> "Their website describes PayFlex as an 'enterprise payment orchestration platform' offering APIs for multi-acquirer routing. The developer docs page and enterprise pricing tiers confirm B2B infrastructure. They serve merchants processing 10K+ transactions/day — exactly the buyer profile for outbound pipeline services."

**BAD reasoning** (generic label — NEVER do this):
> "B2B payment processing company operating in PAYMENTS segment."

**BAD reasoning** (hallucinated — NEVER do this):
> "Company is focused on scaling sales through qualified appointments."

If the scraped text is thin (just a homepage tagline), cite exactly what you have:
> "Homepage says 'Next-gen banking APIs' with no detail page. Likely BaaS infrastructure based on tagline + Apollo industry tag. Low confidence — website is too sparse for definitive classification."

## Dynamic Prompt Generation

The classification prompt is generated FRESH for each project — never hardcoded. It combines:

1. **OUR PRODUCT**: {offer from project}
2. **TARGET SEGMENT**: {ICP description}
3. **EXCLUSION RULES**: from document exclusion_list (if available) + 7 via negativa rules above
4. **INCLUSION SIGNALS**: what makes a company a customer
5. **USER FEEDBACK**: Any corrections from previous iterations (HIGHEST PRIORITY)

### User Feedback Integration

User feedback ALWAYS overrides default rules:
- "Roobet is an operator, not a provider" → add to exclusion: "crypto/iGaming operators"
- "Include agencies, they're our target" → override rule that would exclude agencies
- Format: "[USER OVERRIDE] {feedback text}" stored in iteration history

## Iterative Prompt Tuning Algorithm

If initial accuracy is low:

1. Classify all companies with current prompt
2. Compare vs user/agent verdicts → accuracy, mismatches
3. If accuracy >= 95%: DONE
4. Extract false positive/false negative patterns
5. Improve prompt based on patterns:
   - FP (said target but isn't): add to exclusion rules
   - FN (said not target but is): add to inclusion signals
6. Re-classify with improved prompt
7. Repeat (max 5 iterations)

**Rules for improved prompts:**
- Keep via negativa approach
- No specific company names/domains in rules
- No hardcoded industries/keywords
- More PRECISE based on mismatch patterns
- Must generalize to ANY company in segment

## Classification Prompt Generation

The classification prompt is built dynamically from the project context. NEVER hardcode segment names, industry terms, or company names.

**Structure** (5-8 exclusion rules + 3-4 inclusion signals):

```
YOU ARE CLASSIFYING COMPANIES FOR: {offer_description}

TARGET SEGMENT: {icp_text}

EXCLUSION RULES (if ANY match → NOT a target):
1. DIRECT COMPETITOR: sells {our_product_type} (same product we sell)
2. {exclusion from document, e.g. "iGaming operators — they're customers of our targets, not our targets"}
3. {exclusion from document}
4. COMPLETELY UNRELATED: no connection to {segment}
5. FREELANCER/SOLO CONSULTANT: individual, not a company
6. PLACEHOLDER/PARKED WEBSITE: no real business content
7. SHUT DOWN/INACTIVE: no longer operating
(add 1-3 more from document exclusion_list if available)

INCLUSION SIGNALS (what makes a company a TARGET):
1. Would BUY {our_product} (they're a CUSTOMER)
2. {inclusion from segments, e.g. "operates payment infrastructure"}
3. {inclusion from segments, e.g. "provides lending-as-a-service"}
4. Growing company (funding, hiring, expanding)

CLASSIFY from the WEBSITE TEXT (not Apollo labels).
Return JSON: {"is_target": bool, "confidence": 0-100, "segment": "CAPS_LABEL", "reasoning": "3-5 sentences citing website evidence"}
For targets: segment = one of {segment_labels}
For non-targets: segment = what the company ACTUALLY IS (COMPETITOR, RESTAURANT, etc.)
```

**If document has exclusion_list**: Extract exclusion items and add as numbered rules after the 7 defaults. These take priority.

**If no document**: Use the generic 7 exclusion rules above + 4 default inclusion signals from the offer_summary.

## 2-Pass Re-evaluation

**Trigger**: After initial classification, for companies with confidence 40-70 (borderline).

**Algorithm**:
1. Filter companies where 40 <= confidence <= 70
2. For each borderline company, re-classify with:
   - FULL scraped text (not truncated)
   - Apollo enrichment data if available (employee count, funding, keywords)
   - The SAME classification prompt
3. If re-classification changes verdict → update classification, note `reclassified: true`
4. If still borderline → mark `needs_human_review: true`

**When to trigger**: After each classification pass. NOT for every single company — only borderline batch.

## Exploration Enrichment Algorithm

**Trigger**: Quality gate says `suggest_exploration: true` (targets exist but rate < 50%, or user requests it).

**Algorithm** (costs 5 credits total):
1. Pick top 5 confirmed targets by confidence (highest first). Must be `is_target: true` and `confidence >= 70`.
2. Call `apollo_enrich_companies([domain1, domain2, ..., domain5])` — returns full Apollo data including `industry_tag_id`, `keywords`, `sic_codes`.
3. Extract common patterns across the 5 enriched companies:
   - `industry_tag_ids`: Count frequency. Take top 2-3 that appear in ≥2 of 5 companies.
   - `keywords`: Aggregate all `keywords` arrays. Count frequency. Take top 15 that appear in ≥2 companies. Exclude generic terms (tech, company, solutions).
   - `industries`: Most frequent industry name across 5.
   - `sic_codes`: Top 3 most common (if available).
4. Build improved filter set:
   - NEW industry_tag_ids from enrichment (may differ from original LLM-picked ones)
   - NEW keywords informed by actual target company labels
   - Same locations and employee_ranges (unchanged)
5. Create new FilterSnapshot with trigger `exploration_improved`, parent = current snapshot
6. Present to user: "Exploration found these patterns: {industries}, {keywords}. Re-search with improved filters?"
7. If user approves → start new round with improved filters

**The enrichment data also auto-extends the taxonomy** — new industry_tag_ids discovered here get stored for future use.

## Concurrency

- Scrape websites: 100 concurrent (via scrape_website tool)
- Classify companies: 100 concurrent LLM calls
- Each company: scrape → classify (streaming, process as they arrive)

## Website Scraping for Classification

1. Call `scrape_website` tool for each company domain
2. Use the scraped text (NOT Apollo industry label) for classification
3. Max 5000 chars of cleaned text per company

**Scrape failure handling**:
- `success` + text_length > 100 → classify from scraped text (normal)
- `success` + text_length < 100 → classify with `confidence < 30`, mark `classified_from: "insufficient_text"`
- `failed` with error `TIMEOUT` → do NOT retry (site is slow, skip)
- `failed` with error `BLOCKED` or `RATE_LIMITED` → retry once with proxy if available
- `failed` with error `DNS_ERROR` or `CONNECTION_ERROR` → skip (domain dead)
- `failed` with error `SSL_ERROR` → already handled by scraper's HTTP fallback
- Any failure → classify from Apollo data only (industry, employee_count, keywords), set `confidence < 30`, mark `classified_from: "apollo_data_only"`

**Never skip classification entirely.** Every company gets a verdict — even if low confidence. Low-confidence companies show up as "needs review" in quality gate.

## Per-Company Tracking Fields

Every classified company must track:
- `domain`: normalized domain
- `found_by`: array of keyword/industry values that found this company
- `found_in_round`: which pipeline round discovered it
- `funded_stream`: true if found via funded Apollo call
- `scrape_status`: success/failed/timeout
- `is_target`: bool
- `confidence`: 0-100
- `segment`: CAPS_SNAKE_CASE label
- `reasoning`: 3-5 sentences citing website evidence

## Iteration Lifecycle

Pipeline has ITERATIONS. Each change = new iteration. All visible to user.

```
Iteration 1: Initial search + classify (draft filters, initial prompt)
  → User reviews → provides feedback
Iteration 2: Improved prompt + optimized Apollo filters (from exploration enrichment)
  → Better accuracy, better target rate
Iteration 3+: Scale — same prompt + filters, more pages from Apollo
  → "find more" = increase max_pages, next offset
```

Each iteration records: filters used, prompt used, companies count, target count, target rate. User can compare iterations.

## Batch Processing

For large batches (100+ companies):
1. Process in parallel (100 concurrent)
2. Stream results as they complete
3. Track per-company: all fields above
4. Dedup by domain (if same company appears from multiple keyword streams)
