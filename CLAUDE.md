# GTM-MCP Open Source

## Architecture

Thin MCP server (tools) + domain knowledge (skills). Zero LLM calls inside the server.

### How It Works
- **Tools** (`src/gtm_mcp/`): Thin API wrappers. Return raw data only.
- **Skills** (`.claude/skills/`): Domain knowledge the calling agent reads and applies.
- **Commands** (`.claude/commands/`): Multi-step workflow orchestrators.
- **Agents** (`.claude/agents/`): Batch-processing agent definitions.

## Skills (13 total)

### Domain Skills (8)
| Skill | Purpose |
|-------|---------|
| offer-extraction | Extract ICP from website/document/text (3-layer fallback) |
| apollo-filter-mapping | NL query → Apollo API filters (84 industries with tag_ids, keyword expansion, funding) |
| company-qualification | Via negativa classification (97% accuracy, 2-pass re-eval, exploration enrichment) |
| quality-gate | Pipeline checkpoints, KPI logic, exhaustion detection, 10 keyword regen angles |
| email-sequence | 12-rule GOD_SEQUENCE cold email generation |
| linkedin-sequence | GetSales flow generation (5 types from 414 live flows) |
| reply-classification | 3-tier reply funnel (regex FREE → keywords FREE → LLM cheap) |
| pipeline-state | Entity model, run file format, round loop, filter tracking, cross-run intelligence |

### Orchestration Skills (5 — adapted from claude-pipe)
| Skill | Purpose |
|-------|---------|
| **manager-leadgen** | **7-phase pipeline orchestrator with gates, state tracking, resume** |
| io-state-safe | state.yaml schema + validation + safe update protocol |
| silence-protocol | Background workers produce zero chat output |
| phase-checkpoint | Optional git checkpoint after phase completion |
| resume-checkpoint | Restore pipeline from state.yaml or git tag |

## Commands

| Command | What It Does |
|---------|---|
| **/launch** | **Full pipeline with 3 modes: fresh (new project), new-campaign (existing project, new segment), append (add contacts to existing campaign). Pre-flight → approval → round loop → SmartLead push. Zero interaction after approval.** |
| /qualify | Batch company qualification with versioned snapshots |
| /outreach | Generate email/LinkedIn sequences + push to platforms |
| /replies | Sync + classify + triage campaign replies |

## Pipeline Tracking (pipeline-state skill)

Every pipeline run produces `runs/run-{id}.json` — a single file containing 6 linked entities:

| Entity | Purpose |
|--------|---------|
| FilterSnapshot | Immutable filter record with parent chain (evolution history) |
| Round | One gather→scrape→classify→people cycle with streaming phase timelines |
| APIRequest | Every Apollo API call (1 keyword per request, page, funded flag) |
| Company | Keyed by domain, references request IDs that found it |
| Contact | Extracted people with enrichment retry tracking |
| Iteration | Classification pass with feedback applied |

**Every target company traces back to**: company → found_by_requests → request → filter_snapshot → generation_details

**Cross-run learning**: `~/.gtm-mcp/filter_intelligence.json` accumulates keyword/industry quality scores across all projects. Future runs start with proven keywords as seeds.

## Critical Rules

- **1 keyword per Apollo request** — 7x more unique companies vs combined
- **Funding filter fixes sparse pagination** — essential for funded segments
- **Classify from scraped text ONLY** — never use Apollo industry label
- **KPI = 100 verified contacts** (not companies). Stop immediately when reached.
- **400 companies per round max** — then wait for scrape+classify before deciding next round
- **Never spend credits without user confirmation**
- **ONE question per response**

## Pipeline Orchestration (manager-leadgen)

/launch supports 3 modes:
- **Mode 1 (fresh):** `/launch https://acme.com payments in US` — new project + campaign
- **Mode 2 (new campaign):** `/launch project=easystaff segment=LENDING geo=UK` — reuse project, new campaign
- **Mode 3 (append):** `/launch campaign=3070919 kpi=200` — add contacts to existing campaign

7 gated phases (some skipped by mode):

| Phase | Mode 1 | Mode 2 | Mode 3 |
|-------|:------:|:------:|:------:|
| 1. Offer Extraction | RUN | SKIP | SKIP |
| 2. Filter Generation | RUN | RUN | RUN (seeded) |
| 3. Cost Gate | RUN | RUN | RUN |
| 4. Round Loop | RUN | RUN | RUN + dedup |
| 5. People Extraction | RUN | RUN | RUN + dedup |
| 6. Sequence Generation | RUN | RUN | SKIP |
| 7. Campaign Push | CREATE | CREATE | ADD leads |

State tracked in `state.yaml` per project. Pipeline resumes from last incomplete phase on re-run. Git checkpoints optional for debugging.

Pattern adapted from [claude-pipe](https://github.com/bluzir/claude-pipe) conventions.

## Quick Start

Open Claude Code in this directory and say:
```
/launch https://yourcompany.com SaaS companies in US
```

That's it. `uv run` handles venv creation and dependency installation automatically via `.mcp.json`.

## Config

Keys in `.env` (not tracked by git). Loaded automatically by the server.
Precedence (later wins): `~/.gtm-mcp/config.yaml` → `.env` → shell env vars.
See `.env.example` for the full variable list.

## Project Structure

```
.claude/
  skills/           # 8 domain knowledge skills
  commands/          # 4 orchestrator commands
  agents/            # 2 batch agents
  settings.json
.mcp.json
src/gtm_mcp/
  server.py          # 37 tools via FastMCP (stdio)
  config.py          # ~/.gtm-mcp/config.yaml
  workspace.py       # File-based storage (write/merge/append/versioned)
  tools/             # apollo.py, smartlead.py, getsales.py, scraping.py
pyproject.toml       # pip install → gtm-mcp binary
```
