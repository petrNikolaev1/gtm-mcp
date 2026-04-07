# Offer Extraction Skill

Extract structured ICP (Ideal Customer Profile) from a website URL, strategy document, or free-text description. This skill replaces 3 legacy LLM services: document_extractor, offer_analyzer, people_mapper.

## When to Use

- User provides a website URL for project creation
- User provides a strategy document (markdown, text, PDF)
- User describes their offer in chat
- User says "create project" with any input

## Input Sources

### Website URL (3-Layer Fallback)

**Layer 1**: Call `scrape_website` tool with the URL (Apify residential proxy, 15s timeout)
- If success and text_length > 200 → proceed to extraction

**Layer 2**: If Layer 1 fails → call `scrape_website` again without proxy (direct HTTP + meta extraction)
- Falls back to `http://` if `https://` fails (SSL error)

**Layer 3**: If both fail OR text too short (<200 chars) → extract from GPT knowledge
- Use domain name + any partial text obtained
- Mark `_source: "gpt_knowledge"` (lower confidence)
- GPT can infer company type from well-known domains (stripe.com → payments)

After ANY layer succeeds: analyze the scraped text to extract offer_summary.

### Strategy Document
1. Claude Code reads the file from disk (user provides filename)
2. Analyze the document text below (truncate to 25,000 chars if needed)

### Free-Text Description
1. User describes their product/service in chat
2. Analyze the description below

## Extraction Output Schema

Extract ALL of the following into a structured JSON. Skip fields you can't determine — never hallucinate.

```json
{
  "_source": "document | website | chat",
  "primary_offer": "One-sentence value proposition (what the company sells)",
  "value_proposition": "Problem solved for customers",
  "target_audience": "ICP description in 1-2 sentences",
  
  "target_roles": {
    "primary": ["VP Sales", "CRO", "Head of Sales"],
    "secondary": ["Head of Growth", "VP Marketing", "CMO"],
    "tertiary": ["CEO", "Co-founder"],
    "seniorities": ["c_suite", "vp", "head", "director"],
    "exclude_titles": ["Chief Risk Officer", "General Counsel"]
  },
  
  "segments": [
    {
      "name": "PAYMENTS",
      "keywords": ["payment gateway API", "PSP platform", "payment orchestration", "checkout integration", "recurring billing", "merchant acquiring", "card processing SDK", "payment infrastructure"]
    },
    {
      "name": "LENDING",
      "keywords": ["lending-as-a-service", "loan origination platform", "credit scoring API", "BNPL infrastructure", "debt marketplace", "underwriting automation"]
    }
  ],
  
  "apollo_filters": {
    "combined_keywords": ["all segment keywords merged, 60-80 total"],
    "locations": ["Germany", "Netherlands", "Cyprus", "Israel"],
    "excluded_locations": ["United States", "United Kingdom", "sanctioned countries"],
    "geo_source": "website_footer",
    "employee_range": "20,500",
    "industries": ["financial services"],
    "funding_stages": ["series_a", "series_b", "series_c", "series_d"]
  },
  
  "exclusion_list": [
    {"type": "competitors", "items": ["CompetitorA", "CompetitorB"], "reason": "Direct competitors offering same product"},
    {"type": "wrong_industry", "items": ["consumer fintech", "personal finance"], "reason": "B2C, not our target"},
    {"type": "too_large", "reason": "Enterprise banks with 10K+ employees have in-house solutions"}
  ],
  
  "example_companies": [
    {"domain": "stripe.com", "name": "Stripe", "reason": "Similar product, different market segment"}
  ],
  
  "sequences": [
    {
      "name": "Pipeline Pain",
      "steps": [
        {"step": 1, "day": 0, "subject": "pipeline at {{company_name}}", "body": "..."},
        {"step": 2, "day": 3, "subject": "Re: pipeline at {{company_name}}", "body": "..."},
        {"step": 3, "day": 7, "subject": "quick question, {{first_name}}", "body": "..."},
        {"step": 4, "day": 14, "subject": "closing the loop", "body": "..."}
      ],
      "cadence_days": [0, 3, 7, 14]
    },
    {
      "name": "Fresh Funding",
      "steps": [
        {"step": 1, "day": 0, "subject": "congrats on the round, {{first_name}}", "body": "..."},
        {"step": 2, "day": 4, "subject": "Re: congrats on the round", "body": "..."},
        {"step": 3, "day": 10, "subject": "{{company_name}} + pipeline", "body": "..."}
      ],
      "cadence_days": [0, 4, 10]
    }
  ],

  "IMPORTANT_sequences_rule": "Extract ALL sequences from the document — save them ALL to project.yaml as a reference library for future use. But for the campaign, CHOOSE THE ONE sequence that works with available data. SmartLead variables are limited to: {{first_name}}, {{last_name}}, {{company_name}}, {{city}}, {{email}}. Any sequence requiring data we don't have (e.g. {{funding_round}}, {{competitor_agency}}, {{hiring_role}}) is saved but NOT used for the campaign. Pick the most generic/universal sequence.",
  
  "campaign_settings": {
    "tracking": false,
    "stop_on_reply": true,
    "plain_text": true,
    "daily_limit_per_mailbox": 35
  },
  
  "seed_data": {
    "keywords": ["all segment keywords, deduped"],
    "industry_tag_ids": [],
    "source": "document"
  }
}
```

## Extraction Rules

### Geography — CRITICAL, #1 source of wasted credits

**Search the ENTIRE scraped text for geo signals. Check ALL of these:**
- Footer/legal: "Available in", "Serving clients in", "Offices in"
- Restrictions: "Excl.", "Excluding", "Not available in", "Restricted in", "Sanctioned"
- Compliance: "Licensed in", "Regulated by", "GDPR only", "US-only"
- About page: HQ location, team locations, market coverage statements

**Extract BOTH inclusion AND exclusion:**
- `locations[]` = where they target clients (search HERE)
- `excluded_locations[]` = where they CANNOT serve (do NOT search here)
- `geo_source` = where you found this info ("website_footer", "legal_page", "about_page", "none_found")

**Examples:**
- "Global coverage across Europe, Asia and LatAm *Excl. sanctioned countries, UK and US*"
  → locations: ["Germany", "France", "Netherlands", ...EU + Asia + LatAm]
  → excluded_locations: ["United States", "United Kingdom", "Russia", "Iran", "North Korea"]
  → geo_source: "website_footer"

- "Serving US and Canadian markets"
  → locations: ["United States", "Canada"]
  → excluded_locations: []
  → geo_source: "about_page"

- No geo mentioned anywhere
  → locations: []
  → excluded_locations: []
  → geo_source: "none_found"

**NEVER put excluded countries in the locations array. This wastes 100% of credits on those geos.**

### Segments
- Use CAPS_SNAKE_CASE labels: PAYMENTS, LENDING, BAAS, REGTECH, WEALTHTECH, CRYPTO
- 8-10 SPECIFIC product/technology keywords per segment (NOT generic categories)
- Keywords should be things a target company would have on their website or Apollo profile
- Example of GOOD keywords: "payment gateway API", "loan origination platform", "KYC compliance SaaS"
- Example of BAD keywords: "technology", "innovation", "business services"

### Target Roles
- Think about who DECIDES TO BUY this product (budget authority)
- Primary = main decision maker
- Secondary = influencer
- Tertiary = executive sponsor
- Valid Apollo seniorities: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry

### Role Inference by Offer Type
| Offer Type | Primary Roles | Secondary Roles |
|---|---|---|
| Payroll/HR/EOR | VP HR, CHRO, Head of People | CFO, COO |
| SaaS/DevTools | CTO, VP Engineering | Head of Product |
| Sales/Marketing tools | VP Sales, CRO, CMO | Head of Growth |
| Fashion/Retail | Brand Director, CMO | Head of E-commerce |
| Security/Compliance | CISO, VP Security | CTO, Head of Risk |
| Recruiting/Staffing | VP HR, Head of Talent | COO |

### Employee Size Inference
| Offer Type | Typical Range | Apollo Format |
|---|---|---|
| Payroll/contractor/EOR | 10-200 | "11,50" + "51,200" |
| Enterprise SaaS/security | 200-5000 | "201,500" + "501,1000" + "1001,5000" |
| Small business tools | 1-50 | "1,10" + "11,50" |
| Freelancer marketplace | 1-20 | "1,10" |
| B2B consulting | 50-500 | "51,200" + "201,500" |
| DevOps/infrastructure | 50-1000 | "51,200" + "201,500" + "501,1000" |
| Marketing tools | 10-500 | "11,50" + "51,200" + "201,500" |

**Rule**: Pick the MOST LIKELY buyer size, not broadest range. Range max 10x spread. "All sizes" is WRONG.

### Apollo Employee Range Formats
Valid formats: "1,10", "11,50", "51,200", "201,500", "501,1000", "1001,5000", "5001,10000", "10001,"

### Sequences (if found in document)
- Preserve EXACT email text from document
- SmartLead variable format: `{{first_name}}`, `{{last_name}}`, `{{company_name}}`, `{{city}}`, `{{signature}}`
- Variable normalization: firstname->first_name, lastName->last_name, company->company_name, phoneNumber->phone_number
- If document has unfillable variables (e.g. {{case_study_metric}}), replace with natural language equivalent while preserving exact original text word-for-word

### Exclusion List
- Extract from document: who should NOT be targeted
- Types: competitors, wrong_industry, too_large, too_small, wrong_geography
- These become classification rules later

### Funding Stages (Prioritization)
- If document mentions "funded", "Series A-D", "raised funding" → include funding_stages
- This is a PRIORITIZATION filter, not a hard requirement
- Funded companies are searched FIRST, then unfunded as fallback
- Valid values: "seed", "angel", "series_a", "series_b", "series_c", "series_d", "series_e", "ipo"

## Exclusion List — Competitor Conquest Caveat

If the document describes targeting users of competing vendors (e.g. a "competitor conquest" sequence), those competing vendors' CLIENTS are high-intent TARGETS, not exclusions. Only list companies the document EXPLICITLY says to avoid, skip, or never contact.

## Multi-Segment Handling

When multiple segments are found:
1. Ask user: "I found N segments. Launch as 1 campaign or N separate?"
2. If "one campaign": merge all segment keywords, classification prompt includes ALL segments as possible categories
3. If "separate": create separate pipeline per segment

## Offer Confirmation & Feedback Re-extraction

After extraction, present to user and ask: "Does this look right?"

**If user approves**: Set `offer_approved: true` on the project. Proceed to email accounts → filter generation → pipeline.

**If user provides feedback** (e.g. "wrong roles", "also target CMOs", "size should be 200-5000"):
1. Re-run extraction with the ORIGINAL input + user feedback as additional context
2. Merge: keep what user didn't object to, update what they corrected
3. Present updated offer_summary to user again
4. Max 3 re-extraction cycles — if user keeps correcting, accept on 3rd attempt and note "offer confirmed after 3 revisions"
5. Each re-extraction preserves the original `_source` but adds `feedback_applied: true`

**Key rule**: Never silently skip fields. If extraction missed something (no sequences, no exclusions), that's OK — silently skip what can't be automated. But if user CORRECTS a field, that correction is absolute.
