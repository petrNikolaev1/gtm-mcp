# Offer Extraction Skill

Extract structured ICP (Ideal Customer Profile) from a website URL, strategy document, or free-text description. This skill replaces 3 legacy LLM services: document_extractor, offer_analyzer, people_mapper.

## When to Use

- User provides a website URL for project creation
- User provides a strategy document (markdown, text, PDF)
- User describes their offer in chat
- User says "create project" with any input

## Input Sources

### Website URL
1. Call `scrape_website` tool with the URL
2. Analyze the scraped text below

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
    "locations": ["United States", "United Kingdom"],
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
        {
          "step": 1,
          "day": 0,
          "subject": "{{first_name}}, quick question about {{company_name}}",
          "body": "Email body text with {{first_name}}, {{company_name}}, {{city}} variables...",
          "subject_b": "Growth challenge at {{company_name}}"
        }
      ],
      "cadence_days": [0, 3, 7, 14]
    }
  ],
  
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
