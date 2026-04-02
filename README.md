# MCP LeadGen — AI-Powered Lead Generation Platform

An open-source MCP (Model Context Protocol) server that enables AI agents (Claude, GPT, etc.) to manage B2B lead generation pipelines end-to-end.

> **Want to skip the setup?** Use the hosted version at [gtm-mcp.com](https://gtm-mcp.com/) — sign up and start generating leads in minutes, no infrastructure required.

## What It Does

Connect your AI assistant to this MCP server and it can:
- **Search** Apollo for target companies and contacts
- **Scrape** and analyze company websites
- **Classify** leads using AI (OpenAI / Gemini / Anthropic)
- **Enrich** contacts with verified emails
- **Push** qualified leads to SmartLead email campaigns
- **Automate** LinkedIn outreach via GetSales
- **Monitor** campaign replies and categorize responses
- **Learn** from operator corrections — the system tracks approved/dismissed reply drafts to improve over time

## Example Queries

Just tell your AI agent what you want in plain English:

### Single-segment campaign
```
easystaff.io
IT consulting companies in Miami
```
Creates a project from the website, then gathers IT consulting companies in Miami via Apollo, classifies them, extracts contacts, and pushes to a SmartLead campaign as DRAFT.

### Multi-segment campaign
```
easystaff.io
IT consulting companies in Miami
Video production in London
```
Creates separate pipelines per segment. Each runs independently with its own keywords, filters, and campaign.

### From a website
```
https://thefashionpeople.com/
Fashion brands in Italy
```
Scrapes the website to understand the offer, auto-generates 20+ Apollo keywords for fashion brands, gathers and classifies companies in Italy.

### Extended geo for faster KPI
```
IT consulting companies in US
Video production in UK
```
Broader geo = more companies available = KPI (100 contacts) reached faster.

### Check your data
```
How much total contacts in apollo?
```

### Multi-segment in one message
```
Find IT consulting companies in Miami and video production companies in London
```
The system parses this into 2 separate segments and confirms before launching.

### Launch from a strategy document
Write your outreach strategy in a file (offer, ICP, segments, sequence style, sender info) and tell your AI agent:
```
Launch outreach-plan.md
```
The agent reads your file and passes it to `create_project` with `document_text`. The system automatically extracts everything — offer, target roles, segments, email sequence style, campaign settings — and creates a fully configured project. You just confirm the offer and it runs.

Example `outreach-plan.md`:
```markdown
# Company: EasyStaff
## Offer
Global payroll & contractor payments for companies hiring remote teams.
## ICP
- IT outsourcing companies, 50-500 employees
- SaaS companies with distributed teams
- Digital agencies scaling internationally
## Segments
- IT consulting companies in US
- SaaS companies in DACH region
- Digital agencies in UK
## Sender
Name: Alex, Position: Partnership Manager, Company: EasyStaff
## Sequence style
Casual, short, question-based. 4 emails. Reference their tech stack.
```

This replaces manual project setup — one file drives the entire campaign creation flow.

## Pipeline Flow

The strict flow ensures no credits are wasted:

```
1. create_project (website → offer extraction)     → WAIT for approval
2. confirm_offer                                    → WAIT
3. align_email_accounts (select SmartLead senders)  → WAIT
4. tam_gather PREVIEW (probe Apollo, show strategy) → WAIT for "Proceed?"
5. tam_gather CONFIRM → pipeline runs autonomously:
   gather → scrape → classify → extract people → push to SmartLead DRAFT
```

Default KPIs: **100 contacts, 3 per company**. Pipeline stops automatically when KPI is hit.

## Architecture

```
AI Agent (Claude/GPT) ──MCP Protocol──► Backend (FastAPI)
                                            │
                                    ┌───────┼───────┐
                                    ▼       ▼       ▼
                                PostgreSQL Redis  External APIs
                                (pgvector)        (Apollo, SmartLead,
                                                   OpenAI, GetSales)
                                            │
Frontend (React) ◄──────────────────────────┘
```

- **Backend**: FastAPI + async SQLAlchemy + MCP SDK (SSE transport)
- **Frontend**: React 19 + TypeScript + Vite + Tailwind + AG Grid
- **Database**: PostgreSQL with pgvector extension
- **Cache**: Redis for rate limiting and session data

## Quick Start

### Prerequisites
- Docker and Docker Compose

### 1. Clone and configure

```bash
git clone <repo-url> mcp-leadgen
cd mcp-leadgen
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD and add your API keys
```

### 2. Start services

```bash
docker compose up --build
```

### 3. Access the UI

Open http://localhost:3000 — create an account, then configure your API keys on the Setup page.

### 4. Connect your AI agent

The MCP SSE endpoint is available at:
```
http://localhost:8002/mcp/sse?token=YOUR_MCP_TOKEN
```

Get your token from the Setup page after signing up.

## API Keys

Configure these on the Setup page (per-user, encrypted at rest):

| Service | Purpose | Required |
|---------|---------|----------|
| **OpenAI** | Lead analysis & classification | Yes (or Gemini) |
| **Apollo** | Company/people search | Yes |
| **SmartLead** | Email campaign management | For outreach |
| **Apify** | Website scraping (proxy) | For scraping |
| **GetSales** | LinkedIn outreach automation | Optional |

## All MCP Tools

### Account (3)
| Tool | Description |
|------|-------------|
| `get_context` | Get user state, active project, and auth via token |
| `configure_integration` | Connect an external service (apollo, smartlead, openai, apify, getsales) |
| `check_integrations` | List all connected integrations and their status |

### Projects (5)
| Tool | Description |
|------|-------------|
| `create_project` | Create project from website URL or strategy document |
| `confirm_offer` | Approve or adjust the extracted offer (mandatory before gathering) |
| `select_project` | Set active working project |
| `list_projects` | List all projects |
| `update_project` | Update project ICP or sender info |

### Intent Parsing (1)
| Tool | Description |
|------|-------------|
| `parse_gathering_intent` | Parse multi-segment queries (e.g. "IT consulting Miami AND fashion brands Italy") |

### Pipeline — Gathering (7)
| Tool | Description |
|------|-------------|
| `tam_gather` | Search Apollo with auto-generated 20+ keywords. PREVIEW mode probes Apollo and shows strategy; CONFIRM mode starts gathering |
| `tam_blacklist_check` | Check gathered companies against existing campaigns |
| `tam_pre_filter` | Deterministic pre-filtering (remove trash domains, too-small companies) |
| `tam_scrape` | Scrape company websites (free, no credits) |
| `tam_analyze` | GPT-4o-mini classifies companies via negativa. Supports custom prompts and multi-step chains |
| `tam_explore` | Enrich top 5 targets to discover better Apollo filters (5 credits) |
| `tam_enrich_from_examples` | Reverse-engineer Apollo filters from example domains |

### Pipeline — Classification (2)
| Tool | Description |
|------|-------------|
| `tam_re_analyze` | Re-classify companies with a better prompt (new iteration, previous results preserved) |
| `tam_list_sources` | List available gathering sources with filter schemas |

### Pipeline — Refinement (2)
| Tool | Description |
|------|-------------|
| `refinement_status` | Get self-refinement run status: iterations, accuracy, patterns |
| `refinement_override` | Accept current accuracy and stop refinement early |

### Pipeline — Orchestration (6)
| Tool | Description |
|------|-------------|
| `run_auto_pipeline` | Full autonomous pipeline: scrape → classify → extract people → auto-push to SmartLead |
| `run_full_pipeline` | Full pipeline with checkpoints at each phase for manual approval |
| `pipeline_status` | Live progress: phase, KPIs, timing, ETA, credits, campaign info |
| `set_pipeline_kpi` | Change target contacts, companies, or max per company on a running pipeline |
| `control_pipeline` | Pause or resume a running pipeline |
| `set_people_filters` | Change which roles/titles to search for (VP Marketing, CTO, etc.) |

### Pipeline — People (1)
| Tool | Description |
|------|-------------|
| `extract_people` | Extract contacts from target companies (free Apollo mixed_people endpoint) |

### SmartLead Email Campaigns (9)
| Tool | Description |
|------|-------------|
| `smartlead_score_campaigns` | Score and rank campaigns by quality (warm reply rate, meetings) |
| `smartlead_extract_patterns` | Extract reusable patterns from top-performing campaigns |
| `smartlead_generate_sequence` | Generate 4-5 step email sequence for a campaign |
| `smartlead_approve_sequence` | Mark a generated sequence as approved |
| `smartlead_edit_sequence` | Edit a specific step (subject, body) of a sequence |
| `smartlead_push_campaign` | Push approved sequence to SmartLead as DRAFT with full config |
| `check_destination` | Check which outreach platforms are configured |
| `align_email_accounts` | Select sending email accounts for the campaign (two-step: preview → confirm) |
| `list_email_accounts` | List available SmartLead email accounts |

### SmartLead Campaign Management (5)
| Tool | Description |
|------|-------------|
| `send_test_email` | Send a test email to preview in your inbox |
| `edit_campaign_accounts` | Change sending accounts on a campaign |
| `activate_campaign` | START sending to real leads (requires explicit user confirmation) |
| `list_smartlead_campaigns` | Browse SmartLead campaigns |
| `import_smartlead_campaigns` | Import previous campaigns into blacklist |
| `set_campaign_rules` | Save campaign detection rules for blacklisting |

### GetSales LinkedIn Automation (5)
| Tool | Description |
|------|-------------|
| `gs_generate_flow` | Generate LinkedIn automation flow (5 types: standard, networking, product, volume, event) |
| `gs_approve_flow` | Approve a generated GetSales flow |
| `gs_list_sender_profiles` | List available LinkedIn accounts |
| `gs_push_to_getsales` | Push approved flow to GetSales as DRAFT |
| `gs_activate_flow` | Activate flow — start sending LinkedIn requests (requires explicit confirmation) |

### CRM (2)
| Tool | Description |
|------|-------------|
| `query_contacts` | Search contacts with filters (replied, needs follow-up, category, pipeline) |
| `crm_stats` | CRM statistics: total contacts by status, source, project |

### Replies & Learning (4)
| Tool | Description |
|------|-------------|
| `replies_summary` | Reply counts by category (interested, meeting, not_interested, OOO, etc.) |
| `replies_list` | List/search replies filtered by category, project, or search |
| `replies_followups` | List leads needing follow-up |
| `replies_deep_link` | Generate browser URL to view specific replies |

### Feedback & Editing (4)
| Tool | Description |
|------|-------------|
| `provide_feedback` | Submit feedback (targets, filters, sequence, general) — triggers next action |
| `override_company_target` | Override a company's target/not-target status with reasoning |
| `estimate_cost` | Estimate credits needed before starting a run |
| `blacklist_check` | Quick check: are these domains already in any campaign? |

## Auto-Replies & Operator Learning

The system includes a built-in learning loop for campaign replies:

1. **Reply monitoring** — Background poller watches SmartLead campaigns for new replies (every 3 minutes)
2. **AI classification** — Each reply is categorized (interested, meeting request, not interested, OOO, wrong person, etc.) using GPT-4o-mini
3. **Draft generation** — AI drafts a response using Gemini 2.5 Pro based on the reply context
4. **Operator review** — Operators approve or dismiss drafts on the Learning page (Actions tab)
5. **Analytics** — The Learning page (Analytics tab) tracks approval rates, category breakdowns, and pipeline accuracy over time

The operator's corrections feed back into the system, building a dataset of golden examples that improve future classifications and draft quality.

## Development

### Run backend locally
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload
```

### Run frontend locally
```bash
cd frontend
npm install
npm run dev
```

Frontend dev server runs on port 3000 with proxy to backend at 8002.

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | Database password |
| `UI_BASE` | Frontend URL for links in tool responses |
| `OPENAI_API_KEY` | System-level OpenAI key (fallback) |
| `ENCRYPTION_KEY` | Key for encrypting stored API keys |

## License

MIT
