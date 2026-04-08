# GTM-MCP — B2B Cold Outreach for AI Agents

An open-source MCP server that turns Claude Code into a full B2B cold outreach pipeline. Tell it what you sell and who you're targeting — it finds companies, verifies they're a fit, extracts contacts, writes email sequences, and creates ready-to-send campaigns.

## What is Cold Outreach?

You have a product. There are companies out there that need it but don't know you exist. Cold outreach is how you reach them — you send a message to someone who's never heard of you, explaining how you can help.

Outreach channels include email, LinkedIn, WhatsApp, Telegram, and cold calls. **This MCP focuses on email** (LinkedIn via [GetSales](https://getsales.io) is also supported).

### The Math

Cold outreach works on volume. Even with perfect targeting and great emails, expect roughly **1 positive reply per 100 people you contact**. That's normal — most people ignore cold emails. But if you contact 1,000 relevant people, that's ~10 interested leads. At 5,000 that's ~50.

So you need thousands of **verified, relevant** contacts. You can't do this manually — you need a pipeline.

### The Pipeline: How Email Outreach Works

Here's the full journey from "I have a product" to "replies in my inbox":

**1. Find companies (data provider)**

You need a database of millions of companies to search through. [Apollo](https://apollo.io) is one such provider — you search by industry, location, company size, keywords, and it returns thousands of results.

The problem: Apollo's filters are broad. Searching "fintech companies in US" returns thousands of results, but maybe only 30-40% actually need what you sell. Apollo gives you the raw material, not the finished list.

**2. Verify they're actually your target (scraping + AI)**

This is where most outreach fails — people skip this step and blast emails to everyone Apollo returns. The result: low reply rates and your domain gets flagged as spam.

Instead, this pipeline scrapes each company's website and has your AI agent read it: "Based on what this company does, would they actually need our product?" This filters out 60-70% of Apollo results, leaving only real targets.

For website scraping, we use [Apify](https://apify.com) — specifically its residential proxies, which make requests look like regular browser traffic so websites don't block you.

**3. Find the right people at each company**

At each verified target company, you need the right person's email — typically a decision-maker (CEO, VP Sales, Head of Partnerships, etc.). Apollo provides this too: first a FREE people search to find names, then 1 credit per person for a verified email address.

**4. Write email sequences**

You don't send one email — you send a sequence: an initial email, then 2-3 follow-ups spaced days apart. Each email should be short, plain text (no HTML, no images, no tracking links — these trigger spam filters), and focused on the value you provide.

The best emails reference real case studies: "We helped [similar company] achieve [specific result]" converts much better than generic pitches.

**5. Send via an email sequencer**

You can't send cold emails from Gmail — Google will shut you down. Instead, you use a sequencer like [SmartLead](https://smartlead.ai) that handles:
- Sending from multiple domains/inboxes to stay under spam thresholds
- Scheduling follow-ups automatically
- Tracking who replied, who opened, who bounced
- Warming up new email accounts (see below)

**6. Monitor replies and follow up**

When people reply, you triage: interested (hand to sales), not interested (stop emailing), out of office (wait and retry). This MCP also automates reply classification.

### Email Infrastructure You Need (~2 Weeks Setup, Rotate Every 4-6 Weeks)

Before sending cold emails, you need sending infrastructure. Initial setup takes ~2 weeks. Domains degrade over time as recipients mark emails as spam, so you'll need to rotate — buy new domains, warm them up, and retire old ones. Typical cycle: every 4-6 weeks, based on deliverability tests.

**Why not use your regular email?** Google/Microsoft will flag your domain as spam if you send bulk cold emails. You'll stop receiving normal business emails. Never send cold outreach from your primary domain.

**What to do instead:**

1. **Buy 5+ domains** on [Namecheap](https://namecheap.com) — variations of your main domain. If you're `acme.com`, buy `acme-team.com`, `getacme.com`, `tryacme.com`, etc. (~$10/domain/year)

2. **Create 2 email inboxes per domain** via [Google Workspace](https://workspace.google.com) — e.g., `john@acme-team.com` and `sarah@acme-team.com`. That gives you 10 inboxes across 5 domains.

3. **Connect all inboxes to [SmartLead](https://smartlead.ai)** and start warmup. Warmup means SmartLead automatically sends and receives friendly emails between your new inboxes and other users' inboxes — this builds reputation with Google so your emails don't land in spam.

4. **Wait ~2 weeks** for warmup to complete. After that, you're ready to send.

**Capacity:** Each domain safely sends ~100 emails/day (split across its 2 inboxes). With 5 domains, that's ~500 emails/day — enough for most campaigns.

---

**This MCP automates steps 1-6 above.** You tell it what you sell, it does the rest. Here's how:

## Real-World Examples

### Step 1: Generate an outreach strategy

You don't need to write strategy documents yourself. Open Claude Code in the gtm-mcp directory and ask:

```
Generate an outreach strategy for getsally.io.
Use all case studies from the website — focus on segments
where they have proven results and success stories.
```

**What Claude does:**
1. Scrapes getsally.io — reads the product page, pricing, case studies
2. Identifies segments with real success stories (e.g., "fintech companies reduced payment integration time by 60%")
3. For each segment, generates: target Apollo keywords, industry filters, and a tailored email sequence that references those case studies
4. Saves everything as `outreach-plan-fintech.md`

**Why focus on case studies?** When your cold email says "we helped [Company X] achieve [specific result]", that's proof — not a pitch. Outreaching segments where you have real success stories means your emails carry evidence. Much higher reply rates than generic "we do X, want a demo?" emails.

You review the document, adjust if needed, then move to Step 2.

### Step 2: Launch the campaign

Feed the strategy to the pipeline:

```
/launch outreach-plan-fintech.md
```

Or skip the document and launch from a URL + description:

```
/launch https://getsally.io fintech payments in US
```

**What happens — every step explained:**

1. **Offer extraction** — Claude reads your strategy document (or scrapes the website if you gave a URL). Extracts a structured profile: what you sell, who needs it, what problems you solve, what makes you different.

2. **Filter generation** — based on your offer, Claude generates Apollo search keywords (e.g., "payment processing", "embedded finance", "BNPL platform") and industry filters. Then runs a small probe search (costs 6 Apollo credits) to check: how many companies exist for these keywords? What percentage are actually our targets? This estimates the total cost before committing.

3. **Strategy approval** — Claude shows you the complete plan: all keywords it will search, industries, geography, estimated Apollo credits cost, and the draft email sequence. **You review and either approve or adjust** ("remove this keyword", "add Germany to geography", "make the first email shorter"). Nothing runs until you say yes.

4. **Gather + classify** — the big step. Claude searches Apollo for companies using each keyword one at a time (one keyword per request finds 7x more unique companies than combining them). For each batch of results, it scrapes every company's website and reads it: "Based on what this company actually does, would they benefit from our fintech product?" Companies that don't fit are discarded. This typically filters out 60-70% of Apollo results.

5. **People extraction** — at each verified target company, Claude finds decision-makers. First a FREE people search (no credits) to find names and titles, then a paid enrichment (1 Apollo credit per person) to get verified email addresses. Targets the right roles — CTO, VP Engineering, Head of Payments, not office managers.

6. **Blacklist + email accounts** — before creating the campaign, Claude checks: have we emailed any of these companies before? If so, they're skipped (no duplicate outreach). Then it shows you which of your SmartLead email accounts will be used for sending — the ones matching your outreach domains. **You confirm the accounts look right.**

7. **Campaign creation (DRAFT)** — creates the SmartLead campaign in DRAFT mode (not sending yet!). Uploads all contacts with personalization fields (first name, company name, etc.). Then **sends a test email to YOUR inbox** — the email you set in `GTM_MCP_USER_EMAIL` in `.env`. You see exactly what the recipient will see.

8. **Activation** — you check the test email in your inbox. Does it look good? Is the personalization correct? If yes, type `activate`. Only then does SmartLead start sending emails according to the schedule.

**Two moments where you decide:** strategy approval (step 3) and campaign activation (step 8). Everything between is autonomous.

### After launch: keep scaling

Once a campaign is running, you can add more contacts at any time:

```
gather 50 more
```

Claude finds 50 more target contacts (using the best-performing keywords from the previous run), checks they're not already in the campaign, and pushes only new ones. You preview and activate again.

### Step 3: Add contacts to an existing campaign

You don't need to start from scratch every time. Have a running campaign that's working well? Just add more contacts to it:

```
/launch campaign=3070919 kpi=+100
```

This reuses the same project, email accounts, and email sequence. Finds 100 MORE contacts. Deduplicates against everyone already in the campaign. Pushes only new, unique contacts.

#### Real example — scaling an Inxy.io campaign with affiliate networks:

This also works with campaigns you created manually in SmartLead (not through this MCP). Just paste the SmartLead campaign URL and describe what you want:

```
/launch add more contacts to this campaign
https://app.smartlead.ai/app/email-campaign/3137079/analytics

I want to add more Affiliate networks to the given campaign
and offer them inxy.io

Target CPA/CPL/RevShare networks that connect advertisers
with affiliates. Companies like:
- Adsterra (adsterra.com)
- PropellerAds (propellerads.com)
- Clickadu (clickadu.com)
- TrafficStars (trafficstars.com)
- iMonetizeIt (imonetizeit.com)
- Adverticals (adverticals.com)
- TrafficInMedia (trafficinmedia.com)
- SunDesire Media (sundesiremedia.com)
- Excellerate (excellerate.com)
```

**What happens:**
1. **Imports the existing campaign** from SmartLead — downloads all current leads and blacklists their domains (no one gets emailed twice)
2. **Scrapes inxy.io** to understand the product being offered
3. **Enriches example companies** (Adsterra, PropellerAds, etc.) via Apollo — discovers which search keywords and industries these companies appear under. This teaches the system what "affiliate network" looks like in Apollo's data
4. **Searches Apollo** for more companies matching those keywords — finds other affiliate networks you haven't contacted yet
5. **Scrapes and classifies each one** — reads each website to confirm: is this actually a CPA/CPL/RevShare affiliate network that connects advertisers with affiliates? Or just an ad network that doesn't fit?
6. **Extracts decision-maker contacts** at verified target companies, deduplicates against the existing campaign leads
7. **Pushes only new contacts** to the same campaign + sends a test email to your inbox
8. **You preview and activate**

The system remembers what worked: keywords that found the most targets in previous runs are tried first, companies already found are skipped, Apollo page offsets are preserved (no wasted credits re-fetching the same results).

### More ways to launch

**New segment within an existing project** — you already have a project for your product, but want to target a different vertical:
```
/launch project=easystaff segment=LENDING geo=UK
```
Reuses the approved offer (skips extraction). Generates new filters for LENDING companies in UK. Creates a separate SmartLead campaign with different email accounts.

**From a one-liner:**
```
/launch "We sell payroll software for SMBs in US and UK"
```
Full pipeline from scratch — Claude figures out the offer from your description.

---

## Setup

### Install

```bash
git clone https://github.com/impecablemee/gtm-mcp.git
cd gtm-mcp
cp .env.example .env    # fill in your API keys
```

Open Claude Code in this directory — `.mcp.json` auto-discovers the server. `uv run` handles venv + deps automatically.

### Configure `.env`

```bash
# Required
GTM_MCP_APOLLO_API_KEY=your_apollo_key        # apollo.io → Settings → API Keys
GTM_MCP_SMARTLEAD_API_KEY=your_smartlead_key  # smartlead.ai → Settings → API

# Your email — receives test emails before campaign activation
GTM_MCP_USER_EMAIL=you@company.com

# Optional — improves scraping success rate
GTM_MCP_APIFY_PROXY_PASSWORD=                 # apify.com → Proxy → Password

# Optional — LinkedIn outreach
GTM_MCP_GETSALES_API_KEY=
GTM_MCP_GETSALES_TEAM_ID=

# Optional — Google Sheets export
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_SHARED_DRIVE_ID=
```

### Run

```bash
claude    # open Claude Code in the gtm-mcp directory
# then type:
/launch https://yourcompany.com SaaS companies in US
```

## Architecture

```
Claude Code ──stdio──> gtm-mcp server (49 tools, 0 LLM calls)
                            |
                  +---------+---------+
                  v         v         v
              Apollo    SmartLead   GetSales
              (search)  (campaigns) (LinkedIn)
```

Zero LLM calls inside the server. Claude Code does all the reasoning using domain knowledge encoded as skills.

**Tools** (`src/gtm_mcp/`): Thin API wrappers. Only data access.

**Skills** (`.claude/skills/`): Domain knowledge in markdown — classification rules, email writing rules, filter strategies. Claude reads these and reasons.

**Commands** (`.claude/commands/`): The `/launch` command — orchestrates the full pipeline.

### Pipeline Steps

| Step | What Happens | Human Input |
|------|-------------|:-----------:|
| 1. Extract Offer | Scrape URL / read file / parse text -> structured ICP | - |
| 2. Generate Filters | Apollo taxonomy + keywords + probe (6 credits) | - |
| 3. Strategy Approval | Show offer + filters + cost estimate | **Approve?** |
| 4. Gather + Classify | Apollo search -> scrape websites -> AI classifies | - |
| 5. Extract People | FREE search -> PAID enrichment (1 credit/person) | - |
| 6. Generate Sequence | Email sequence from 12-rule template or reference | - |
| 7. Campaign Push | SmartLead DRAFT + test email + Google Sheet | **Activate?** |

### Key Rules

- **1 keyword per Apollo request** — 7x more unique companies vs combined
- **Via negativa classification** — exclude non-targets, don't define targets (97% accuracy)
- **Max 200 Apollo credits** per run (default, overridable)
- **100 verified contacts** KPI target (default, overridable)
- **Plain text emails**, no tracking, Mon-Fri 9-18 target timezone

## Tools (49)

### Config & Projects (8)
| Tool | Description |
|------|-------------|
| `get_config` | Get configuration status (which keys are set) |
| `set_config` | Set a configuration value |
| `create_project` | Create a new project |
| `list_projects` | List all projects |
| `save_data` | Save data to project workspace (write/merge/append/versioned) |
| `load_data` | Load data from project workspace |
| `find_campaign` | Find campaign by SmartLead ID or slug across projects |
| `get_project_costs` | Cost breakdown per project — totals, per-campaign, per-run |

### Blacklist (3)
| Tool | Description |
|------|-------------|
| `blacklist_check` | Check if a domain is blacklisted (supports time-windowed checks) |
| `blacklist_add` | Add domains with metadata (source, campaign, contact date) |
| `blacklist_import` | Import blacklist from a file |

### Apollo (6)
| Tool | Description |
|------|-------------|
| `apollo_search_companies` | Search by keywords, industries, location, size, funding |
| `apollo_search_people` | Search people at a company (FREE — no credits) |
| `apollo_enrich_people` | Enrich with verified emails (1 credit/person) |
| `apollo_enrich_companies` | Bulk enrich companies by domain |
| `apollo_get_taxonomy` | Get all 84 Apollo industries with tag_ids |
| `apollo_estimate_cost` | Estimate credits needed for a pipeline run |

### Scraping (2)
| Tool | Description |
|------|-------------|
| `scrape_website` | Scrape website text via Apify proxy with fallback |
| `scrape_batch` | Batch scrape many URLs in parallel (50 concurrent) |

### SmartLead (13)
| Tool | Description |
|------|-------------|
| `smartlead_list_campaigns` | List all campaigns |
| `smartlead_list_accounts` | List all email accounts (paginated, handles 2000+) |
| `smartlead_search_accounts` | Filter cached accounts by name/domain |
| `smartlead_create_campaign` | Create campaign with schedule and settings (DRAFT) |
| `smartlead_set_sequence` | Set email sequence steps with A/B variant support |
| `smartlead_add_leads` | Add leads to campaign with company name normalization |
| `smartlead_get_campaign` | Get campaign details (accounts, sequences, status) |
| `smartlead_get_lead_messages` | Fetch full message thread for reply classification |
| `smartlead_export_leads` | Export all leads from a campaign (for dedup/blacklist) |
| `smartlead_sync_replies` | Sync replied leads from a campaign |
| `smartlead_send_reply` | Send a reply to a lead |
| `smartlead_send_test_email` | Send test email to verify before activation |
| `smartlead_activate_campaign` | Activate campaign — start sending (requires confirmation) |

### GetSales — LinkedIn (4)
| Tool | Description |
|------|-------------|
| `getsales_list_profiles` | List LinkedIn profiles |
| `getsales_create_flow` | Create LinkedIn outreach flow |
| `getsales_add_leads` | Add leads to a GetSales flow (validates LinkedIn URLs) |
| `getsales_activate_flow` | Activate flow — start LinkedIn outreach |

### Google Sheets (3)
| Tool | Description |
|------|-------------|
| `sheets_create` | Create Google Sheet on Shared Drive with contact headers |
| `sheets_export_contacts` | Export contacts with classification reasoning |
| `sheets_read` | Read sheet data (for blacklist import, company lists) |

### Pipeline (7)
| Tool | Description |
|------|-------------|
| `pipeline_probe` | Probe search — 6 Apollo calls + batch scrape in ONE call |
| `pipeline_gather_and_scrape` | Full gather — all Apollo searches + all scraping, streaming |
| `pipeline_import_blacklist` | Export SmartLead campaign leads as project blacklist |
| `pipeline_save_contacts` | Save contacts to project + run file atomically |
| `pipeline_compute_leaderboard` | Compute keyword quality scores from run data |
| `pipeline_save_intelligence` | Save cross-run keyword intelligence for future runs |
| `campaign_push` | Atomic campaign setup — create + sequence + leads + test email |

### Assignment (2)
| Tool | Description |
|------|-------------|
| `assign_campaigns_to_projects` | Auto-assign SmartLead campaigns to projects |
| `learn_assignment_correction` | Learn from user correction for future auto-assignment |

### Utility (1)
| Tool | Description |
|------|-------------|
| `normalize_company_name` | Strip legal suffixes (Inc, LLC, Ltd, GmbH, etc.) |

## Data Storage

All project data in `~/.gtm-mcp/projects/<slug>/`:

```
~/.gtm-mcp/
├── config.yaml                  # API keys
├── blacklist.json               # global domain blacklist (structured, temporal)
├── filter_intelligence.json     # cross-run keyword quality scores
└── projects/
    └── sally-fintech/
        ├── project.yaml         # offer, segments, ICP
        ├── state.yaml           # pipeline phase progress
        ├── contacts.json        # extracted contacts
        ├── runs/
        │   └── run-001.json     # complete execution record
        └── campaigns/
            └── payments-us/
                ├── campaign.yaml
                ├── sequence.yaml
                └── replies.json
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Inspired By

[claude-pipe](https://github.com/bluzir/claude-pipe) — file-first agent orchestration framework. Core ideas adopted: state persists to YAML files, quality gates between phases, predictable costs through deterministic tool code.

## License

MIT
