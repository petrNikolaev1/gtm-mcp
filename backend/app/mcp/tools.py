"""MCP Tool definitions — 26 tools for the LeadGen pipeline."""

TOOLS = [
    # ═══════════════════════════════════════════════════════════════════════
    # STRICT FLOW — ONE action per message. Wait for user approval between each.
    # ALL config done BEFORE any Apollo credits are spent.
    #
    # Step 1: create_project (website → offer + roles + project link) → WAIT
    # Step 2: confirm_offer → ask "Previous campaigns for blacklist?" → WAIT
    # Step 3: align_email_accounts → "Which email accounts?" → WAIT
    # Step 4: tam_gather PREVIEW → creates pipeline (pending_approval) + probe Apollo
    #         Shows: strategy, ALL keywords, KPIs, cost, pipeline link → WAIT
    # Step 5: User approves → tam_gather CONFIRM → gathers + starts pipeline automatically
    #         Pipeline runs fully autonomously → SmartLead push on KPI hit
    #
    # NEVER skip steps. NEVER combine steps. NEVER ask KPI questions.
    # Default KPIs: target_people=100, max_people_per_company=3
    # ═══════════════════════════════════════════════════════════════════════

    # ── Account ──
    {
        "name": "get_context",
        "description": """Get the user's current state. Auth via token from SSE URL.
FIRST CALL: extract token from your .mcp.json URL (after ?token= or /sse/) and pass as token parameter.
After first call, auth is stored for the session.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "API token from SSE URL (mcp_...). Extract from your .mcp.json config URL. Only needed on first call."},
            },
        },
    },
    {
        "name": "configure_integration",
        "description": "Connect an external service by providing your API key. Required: apollo (find companies), smartlead (create campaigns), openai (AI analysis), apify (scrape websites). Optional: getsales (LinkedIn outreach).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "integration_name": {"type": "string", "enum": ["apollo", "smartlead", "openai", "apify", "getsales"]},
                "api_key": {"type": "string", "description": "Your API key for this service"},
            },
            "required": ["integration_name", "api_key"],
        },
    },
    {
        "name": "check_integrations",
        "description": "List all connected integrations and their status.",
        "inputSchema": {"type": "object", "properties": {}},
    },

    # ── Project (4) ──
    {
        "name": "select_project",
        "description": """Set your active working project. CRITICAL: You MUST call this before any pipeline operation if the user has multiple projects. This determines which project's campaigns are used for blacklisting.

When a user first connects or says something like 'gather companies' without specifying a project, you MUST:
1. Call list_projects to see their projects
2. If multiple projects exist, ASK which one they want to work on
3. Call select_project to set it
4. Show them the project's campaigns and blacklist scope

The response shows: project name, ICP, active campaigns, and which companies are already contacted.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project to set as active"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "create_project",
        "description": """Create a new project from website OR strategy document.

WHEN USER SAYS: "acme.com", "our website is X" → call with website=URL.
WHEN USER SAYS: "launch outreach-plan.md", "use this document" → call with document_text=content.
Document mode extracts EVERYTHING: offer, roles, segments, sequence, campaign settings.
AFTER creating project, show what was extracted and ask: "Does this look correct?" (ONE question only).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "website": {"type": "string", "description": "Company website URL (e.g. 'https://acme.com'). Scraped to extract offer."},
                "document_text": {"type": "string", "description": "Full text of strategy/outreach document. Extracts: offer, roles, segments, sequence, settings. Use instead of website when user provides a document."},
                "target_segments": {"type": "string", "description": "ICP description (e.g. 'Series A-B SaaS in DACH, 50-500 emp')"},
                "target_industries": {"type": "string", "description": "Target industries (e.g. 'SaaS, Fintech, IT Services')"},
                "sender_name": {"type": "string"},
                "sender_company": {"type": "string"},
                "sender_position": {"type": "string"},
            },
            # name is optional — auto-generated from website domain if not provided
        },
    },
    {
        "name": "confirm_offer",
        "description": """Confirm or adjust the project's offer. MANDATORY before gathering.

After create_project, show the extracted offer to user and ask ONLY: "Does this look correct?"
- If yes → call with approved=true. Then STOP and WAIT for user's next instruction.
- If no → call with feedback. Then STOP and show updated offer.

ONE action per message. After confirming, do NOT start gathering or ask other questions. Wait for user.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project ID (optional if active project set)"},
                "approved": {"type": "boolean", "description": "True = user confirms the offer is correct"},
                "feedback": {"type": "string", "description": "User's correction/clarification about the offer"},
            },
        },
    },
    {
        "name": "list_projects",
        "description": "List all your projects.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_project",
        "description": "Update a project's ICP or sender info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "name": {"type": "string"},
                "target_segments": {"type": "string"},
                "target_industries": {"type": "string"},
                "sender_name": {"type": "string"},
                "sender_company": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },

    # ── Intent Parsing ──
    {
        "name": "parse_gathering_intent",
        "description": """INTERNAL: Parse multi-segment queries. Usually you DON'T need to call this — tam_gather handles it automatically.
Only call explicitly when user provides 2+ DIFFERENT segments in one message (e.g. "IT consulting Miami AND fashion brands Italy") and you need to confirm how many pipelines to run.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User's raw gathering query"},
                "project_id": {"type": "integer", "description": "Project ID (to get user's offer for competitor exclusion)"},
            },
            "required": ["query", "project_id"],
        },
    },
    # ── Pipeline ──
    {
        "name": "tam_gather",
        "description": """Search Apollo for companies. Auto-generates 20+ keywords + industry filters.

PREVIEW (no confirm_filters): Creates pipeline in pending_approval, probes Apollo, shows:
  - Strategy reasoning (industry-first vs keywords-first and WHY)
  - All generated keywords (20+)
  - KPIs (100 people, 3/company — defaults, do NOT ask user)
  - Cost estimate
  - Pipeline link
  Show this to user. Ask ONLY: "Proceed?"

CONFIRM (confirm_filters=true): Gathers companies + starts streaming pipeline AUTOMATICALLY.
  Pipeline runs in background until KPI met → auto-pushes to SmartLead.

NEVER ask about target count, KPIs, or pages. Defaults are set. ONE question: "Proceed?"
Pass query= with natural language. Pass filters= with locations + sizes.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project to gather for"},
                "source_type": {
                    "type": "string",
                    "enum": ["apollo.companies.api",
                             "csv.companies.manual", "csv.companies.file",
                             "google_sheets.companies.manual", "google_sheets.companies.sheet",
                             "google_drive.companies.folder",
                             "manual.companies.manual"],
                    "description": "Source to gather from. Apollo API costs credits. CSV/Sheet/Drive/Manual are free.",
                },
                # target_count removed — default 100 people, 3/company. NEVER ask user.
                "filters": {
                    "type": "object",
                    "description": "Source-specific filters. For Apollo: keywords, locations, etc. For CSV: file_path. For Sheet: sheet_url. For Drive: drive_url or folder_path.",
                    "properties": {
                        "q_organization_keyword_tags": {"type": "array", "items": {"type": "string"}},
                        "organization_locations": {"type": "array", "items": {"type": "string"}},
                        "organization_num_employees_ranges": {"type": "array", "items": {"type": "string"}},
                        "max_pages": {"type": "integer"},
                        "per_page": {"type": "integer"},
                        "organization_latest_funding_stage_cd": {"type": "array", "items": {"type": "string"}},
                        "domains": {"type": "array", "items": {"type": "string"}},
                        "sheet_url": {"type": "string", "description": "Google Sheets URL for google_sheets.companies.sheet source"},
                        "file_path": {"type": "string", "description": "Path to CSV file for csv.companies.file source"},
                        "file_url": {"type": "string", "description": "URL to download CSV for csv.companies.file source"},
                        "drive_url": {"type": "string", "description": "Google Drive folder URL for google_drive.companies.folder source"},
                        "folder_path": {"type": "string", "description": "Local folder path for google_drive.companies.folder source (testing)"},
                        "tab_name": {"type": "string", "description": "Sheet tab name for google_sheets.companies.sheet source"},
                        "column_mapping": {"type": "object", "description": "Explicit column mapping override: {domain: 'Website', name: 'Company Name', ...}"},
                    },
                },
                "query": {"type": "string", "description": "Natural language query — triggers auto-filter discovery if no keywords provided"},
                "reuse_run_id": {"type": "integer", "description": "Reuse filters from a previous run. User says 'same filters, more targets'"},
                "confirm_filters": {"type": "boolean", "description": "For Apollo: set true AFTER user approves the filter preview. First call without this returns preview only."},
            },
            "required": ["project_id", "source_type", "filters"],
        },
    },
    {
        "name": "tam_blacklist_check",
        "description": "Phase 2: Check gathered companies against existing campaigns. Creates Checkpoint 1 gate.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_approve_checkpoint",
        "description": "Approve a pipeline checkpoint (CP1: scope, CP2: targets, CP3: cost).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gate_id": {"type": "integer"},
                "note": {"type": "string", "description": "Optional approval note"},
            },
            "required": ["gate_id"],
        },
    },
    {
        "name": "tam_pre_filter",
        "description": "Phase 3: Deterministic pre-filtering (remove trash domains, too-small companies).",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_scrape",
        "description": "Phase 4: Scrape websites for all non-blacklisted companies. Free, no credits used.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_analyze",
        "description": """Phase 5: GPT-4o-mini analyzes scraped companies via negativa. Labels segments, creates Checkpoint 2.

GPT is the cheap workhorse ($0.003/company). YOU (the agent = Opus) are the QA.
After this returns, review the target_list and borderline_rejections.
If GPT's accuracy < 90% (false positives), call tam_re_analyze with an adjusted prompt.

Via negativa: GPT focuses on EXCLUDING shit, not confirming matches.
Segments: CAPS_LOCKED labels (IT_OUTSOURCING, SAAS_COMPANY, AGENCY, etc.)

Custom prompts: Users can provide their own classification prompts via prompt_text.
Multi-step chains: Provide prompt_steps array for sequential classification (each step feeds into the next).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "prompt_text": {"type": "string", "description": "ICP description for via negativa analysis. Built from project knowledge if omitted. Users can provide their own classification prompt."},
                "prompt_steps": {
                    "type": "array",
                    "description": "Multi-step prompt chain. Each step runs sequentially; output feeds into next step.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Step name (e.g., 'classify_fashion')"},
                            "prompt": {"type": "string", "description": "Classification prompt for this step"},
                            "output_column": {"type": "string", "description": "Name of the output column/field"},
                            "type": {"type": "string", "enum": ["classify", "filter"], "description": "Step type: 'classify' runs GPT, 'filter' removes non-matching companies"},
                            "filter_condition": {"type": "string", "description": "For filter steps: condition like 'segment != OTHER'"},
                        },
                        "required": ["name", "prompt"],
                    },
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_explore",
        "description": """Exploration: enrich top 5 targets to discover better Apollo filters.

WHEN USER SAYS: "explore", "find better filters", "optimize", "enrich targets" → call THIS.
Costs 5 Apollo credits. Returns optimized filters with real Apollo vocabulary.
Call AFTER classification is done (targets exist in the pipeline).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer", "description": "Pipeline run to explore (must have targets)"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_enrich_from_examples",
        "description": """Reverse-engineer Apollo filters from example companies. User provides a list of domains (companies they KNOW are targets), Apollo enrichment reveals their real keyword tags and industries. Use the output as filters for tam_gather.

Use case: user says 'companies like X, Y, Z' or provides a file with example companies.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domains": {"type": "array", "items": {"type": "string"}, "description": "List of example company domains (max 10)"},
                "segment_description": {"type": "string", "description": "What kind of companies these are"},
                "locations": {"type": "array", "items": {"type": "string"}, "description": "Target locations for the search"},
            },
            "required": ["domains"],
        },
    },
    # define_targets REMOVED — tam_gather handles everything (filter generation, probe, gathering)
    {
        "name": "tam_re_analyze",
        "description": """Re-classify companies with better prompt. Creates new pipeline iteration.

WHEN USER SAYS: "these aren't right", "exclude X", "re-analyze", "wrong targets", "use this prompt" → call THIS.
User can provide their own classification prompt via prompt_text parameter.

TWO-STEP: Call WITHOUT confirm → shows preview (how many companies, prompt preview). Call WITH confirm=true → executes.

IMPORTANT: If user doesn't specify WHICH pipeline to re-analyze and has multiple runs, ASK which one first.
Applies to ALL companies in the specified run. Same companies, new classification. Previous results preserved (new iteration in UI + prompts history page).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "prompt_text": {"type": "string", "description": "Custom classification prompt from user. Can be pasted text or extracted from a file."},
                "agent_verdicts": {"type": "object", "description": "Auto-tune: {domain: {target: bool, reason: str}}"},
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves the preview"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "tam_list_sources",
        "description": "List available gathering sources with their filter schemas.",
        "inputSchema": {"type": "object", "properties": {}},
    },

    # ── Refinement (2) ──
    {
        "name": "refinement_status",
        "description": "Get the current status of a self-refinement run: iteration count, accuracy history, patterns found.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "refinement_override",
        "description": "Accept current accuracy and stop the refinement loop early.",
        "inputSchema": {
            "type": "object",
            "properties": {"refinement_run_id": {"type": "integer"}},
            "required": ["refinement_run_id"],
        },
    },

    # ── Campaign Sequence Tools (5) ──
    {
        "name": "smartlead_score_campaigns",
        "description": "Score and rank campaigns by quality (warm reply rate, meetings, volume).",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
        },
    },
    {
        "name": "smartlead_extract_patterns",
        "description": "Extract reusable patterns from top-performing campaigns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string"},
                "top_n": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "smartlead_generate_sequence",
        "description": """Generate email sequence for a campaign.

WHEN USER SAYS: "generate sequence", "create emails", "write the campaign" → call THIS.
Returns a DRAFT sequence (4-5 steps). Show preview to user, then call smartlead_approve_sequence.
FLOW: generate → user approves → smartlead_approve_sequence → smartlead_push_campaign.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "campaign_name": {"type": "string"},
                "instructions": {"type": "string", "description": "Additional instructions for sequence generation"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "smartlead_approve_sequence",
        "description": "Mark a generated sequence as approved.",
        "inputSchema": {
            "type": "object",
            "properties": {"sequence_id": {"type": "integer"}},
            "required": ["sequence_id"],
        },
    },
    {
        "name": "check_destination",
        "description": """Check which outreach platforms are configured (SmartLead, GetSales, or both).
MUST be called before pushing a campaign when you're not sure which platform to use.
If both are configured, returns a question asking the user to choose.""",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "smartlead_push_campaign",
        "description": """Push an approved sequence to SmartLead as a DRAFT campaign with FULL configuration.

NOTE: For auto-pipeline flows, use align_email_accounts BEFORE run_auto_pipeline instead —
accounts are pre-selected on the campaign and auto-push happens when KPI is hit.
This tool is for MANUAL push when not using auto-pipeline.

Requires email_account_ids — call list_email_accounts first to show available accounts.

Campaign settings (production-grade):
- Plain text, no tracking, stop on reply, 40% follow-up, Mon-Fri 9-18 target timezone
- Target contacts auto-uploaded with normalized company names + segment as custom fields
- Test email auto-sent to user's email after creation

Campaign is ALWAYS DRAFT — never activated without explicit user approval.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "integer"},
                "email_account_ids": {"type": "array", "items": {"type": "integer"}, "description": "SmartLead email account IDs to use. Get from list_email_accounts."},
                "target_country": {"type": "string", "description": "Country for timezone (from gathering geo filter). E.g. 'United States', 'Germany'"},
            },
            "required": ["sequence_id"],
        },
    },
    {
        "name": "list_email_accounts",
        "description": """List SmartLead email accounts available for campaigns. Shows accounts used in the user's existing campaigns (from blacklist import) so they can reuse the same sending accounts.

Call this BEFORE align_email_accounts to show available accounts.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Optional: show accounts from a specific campaign"},
            },
        },
    },
    {
        "name": "align_email_accounts",
        "description": """Select email accounts for the campaign. Called BEFORE tam_gather (no run exists yet).

Loads ALL accounts from SmartLead, filters by user query (e.g. 'all with john in name').
Creates draft campaign linked to the project. Run will be linked later when tam_gather creates it.

TWO-STEP: Call without confirm → preview matched accounts. Call with confirm=true → creates campaign.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project to link campaign to"},
                "run_id": {"type": "integer", "description": "Gathering run (optional — may not exist yet)"},
                "account_filter": {"type": "string", "description": "Filter: 'all with john', 'sales accounts', email/name substring"},
                "account_ids": {"type": "array", "items": {"type": "integer"}, "description": "Explicit account IDs (overrides filter)"},
                "campaign_name": {"type": "string", "description": "Campaign name override"},
                "confirm": {"type": "boolean", "description": "Set true after user approves account selection"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "send_test_email",
        "description": """Send a test email via SmartLead's native API. Uses the user's email as recipient.
Auto-resolves sending account and lead for variable substitution.

Call this after smartlead_push_campaign to let the user preview the email in their inbox.
Also called automatically when a campaign is created.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "SmartLead campaign ID (external_id)"},
                "test_email": {"type": "string", "description": "Override recipient email (defaults to user's email)"},
                "sequence_number": {"type": "integer", "default": 1, "description": "Which sequence step to test (1, 2, 3...)"},
            },
            "required": ["campaign_id"],
        },
    },

    # ── GetSales LinkedIn Automation (5) ──
    {
        "name": "gs_generate_flow",
        "description": """Generate a LinkedIn automation flow for GetSales using proven patterns from your 414 live flows.

Analyzes your project's ICP, sender info, and knowledge to create an optimized LinkedIn sequence.

Flow types (from GETSALES_AUTOMATION_PLAYBOOK.md):
- "standard": Qualifying question + 3 follow-ups (68% positive rate pattern)
- "networking": No connection note + soft intro + 3 messages (69% positive)
- "product": Generic connect + product showcase + 3 messages (85% on niche ICP)
- "volume": Value prop note + 4 msgs + InMail fallback (high volume play)
- "event": Event hook connection + 3 messages (52% positive)

The generated flow includes:
- Connection request note (or empty for networking)
- 3-4 follow-up messages with engagement actions between them
- Non-accept branch (like → visit → endorse → withdraw)
- Proven timing patterns from top campaigns""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "flow_name": {"type": "string", "description": "Campaign name (defaults to 'Project - LinkedIn Type')"},
                "flow_type": {"type": "string", "enum": ["standard", "networking", "product", "volume", "event"], "default": "standard"},
                "instructions": {"type": "string", "description": "Additional instructions for flow generation"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gs_approve_flow",
        "description": "Mark a generated GetSales flow as approved after user review.",
        "inputSchema": {
            "type": "object",
            "properties": {"sequence_id": {"type": "integer"}},
            "required": ["sequence_id"],
        },
    },
    {
        "name": "gs_list_sender_profiles",
        "description": """List GetSales sender profiles (LinkedIn accounts) available for automation flows.

Call this BEFORE gs_push_to_getsales to let the user choose which LinkedIn accounts to use.
Shows profile names, UUIDs, and which project each sender belongs to.""",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "gs_push_to_getsales",
        "description": """Push an approved flow to GetSales as a DRAFT automation with FULL configuration.

BEFORE calling this, you MUST:
1. Call gs_list_sender_profiles to show available LinkedIn accounts
2. Ask user which sender profiles to use
3. Wait for user to select — NEVER proceed without their choice

Creates the flow with:
- Node tree (connection request + messages + engagement + non-accept branch)
- Sender profiles with rotation strategy
- Schedule (Mon-Fri 9:00-18:00 in target timezone)
- Target leads auto-uploaded from pipeline

Flow is ALWAYS DRAFT — never activated without explicit user approval.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "integer"},
                "sender_profile_uuids": {"type": "array", "items": {"type": "string"}, "description": "GetSales sender profile UUIDs. Get from gs_list_sender_profiles."},
                "rotation_strategy": {"type": "string", "enum": ["fair", "random", "prior_engagement", "new_sender"], "default": "fair"},
                "workspace_uuid": {"type": "string", "description": "GetSales workspace UUID (folder). Optional."},
                "target_country": {"type": "string", "description": "Country for timezone. E.g. 'United States', 'Germany'"},
            },
            "required": ["sequence_id", "sender_profile_uuids"],
        },
    },
    {
        "name": "gs_activate_flow",
        "description": """Activate a GetSales flow — START sending LinkedIn connection requests to real leads.

CRITICAL: NEVER call without EXPLICIT user approval. User must confirm they reviewed:
1. Flow messages (connection note + all follow-ups)
2. Sender profiles (correct LinkedIn accounts)
3. Target leads
4. Schedule and timezone

Only call when user explicitly says "activate", "start", "launch", or "go live".""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "flow_uuid": {"type": "string", "description": "GetSales flow UUID"},
                "user_confirmation": {"type": "string", "description": "Quote user's exact words confirming activation"},
            },
            "required": ["flow_uuid", "user_confirmation"],
        },
    },

    # suggest_apollo_filters REMOVED — tam_gather handles filter discovery + preview + confirmation in one tool.
    # When tam_gather is called without confirm_filters, it auto-discovers filters, probes Apollo for total_available,
    # shows cost breakdown, and waits for user to confirm. No separate filter suggestion tool needed.

    # ── Orchestration (2) ──
    {
        "name": "run_full_pipeline",
        "description": "Run the full pipeline end-to-end: gather → blacklist → filter → scrape → analyze (with optional auto-refine). Stops at each checkpoint for approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "source_type": {"type": "string"},
                "filters": {"type": "object"},
                "auto_refine": {"type": "boolean", "default": True},
                "target_accuracy": {"type": "number", "default": 0.9},
            },
            "required": ["project_id", "source_type", "filters"],
        },
    },
    {
        "name": "pipeline_status",
        "description": """Pipeline status — phase, progress, KPIs, timing, targets found, campaign info.

WHEN USER SAYS: "pipeline status", "how is gathering going", "what's the progress" → call THIS.
DO NOT call get_context for pipeline questions — use pipeline_status with the run_id.

POLLING: After run_auto_pipeline starts, POLL this tool every 30 seconds until is_complete=true.
When is_complete=true, relay the completion_message to the user — it contains campaign info,
SmartLead link, test email status. The user should know immediately when the pipeline finishes.

Returns: status, KPIs, progress (people/targets + percentages), timing (elapsed, ETA),
cost (credits used/remaining), campaign info (when created), completion_message (when done).""",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "set_pipeline_kpi",
        "description": """Change pipeline KPI targets. Works on running or paused pipelines.

WHEN USER SAYS: "gather 200 contacts", "I need 50 companies", "set max 5 per company", "change target to 1000" → call THIS.

TWO-STEP: Call WITHOUT confirm → shows preview of changes + cost impact. Call WITH confirm=true → applies changes.
KPIs auto-align: changing one recalculates the others.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "target_people": {"type": "integer", "description": "Target total contacts to gather (e.g. 200)"},
                "target_companies": {"type": "integer", "description": "Target number of target companies (e.g. 50)"},
                "max_people_per_company": {"type": "integer", "description": "Maximum contacts per company (e.g. 5)"},
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves the preview"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "control_pipeline",
        "description": """Pause or resume a running pipeline.

WHEN USER SAYS: "pause", "stop gathering", "hold on", "wait" → action="pause"
WHEN USER SAYS: "resume", "continue", "keep going", "find more" → action="resume"

TWO-STEP: Call WITHOUT confirm → shows preview of action + pipeline state. Call WITH confirm=true → executes.
If user has 2+ pipelines, ALWAYS ask which one first.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "action": {"type": "string", "enum": ["pause", "resume"]},
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves the preview"},
            },
            "required": ["run_id", "action"],
        },
    },

    {
        "name": "set_people_filters",
        "description": """Change which roles/titles to search for when extracting contacts from target companies.

WHEN USER SAYS: "I want VP Marketing and CMO", "change roles to HR directors", "search for CTOs only" → call THIS.

TWO-STEP: Call WITHOUT confirm → shows preview of new filters. Call WITH confirm=true → applies.
People search is FREE (Apollo mixed_people endpoint).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer"},
                "person_titles": {"type": "array", "items": {"type": "string"}, "description": "Job titles to search (e.g. ['VP Marketing', 'CMO', 'Head of Marketing'])"},
                "person_seniorities": {"type": "array", "items": {"type": "string"}, "description": "Seniority levels: owner, founder, c_suite, partner, vp, head, director, manager, senior"},
                "max_people_per_company": {"type": "integer", "description": "Maximum contacts per company"},
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves the preview"},
            },
            "required": ["run_id"],
        },
    },

    {
        "name": "run_auto_pipeline",
        "description": """Run the full pipeline: scrape → classify → extract people → auto-push to SmartLead.

Uses DEFAULT KPIs — do NOT ask user about these:
  target_people=100, max_people_per_company=3

TWO-STEP:
  1. Call WITHOUT confirm → shows pipeline preview (KPIs, filters, accounts). STOP. Wait for approval.
  2. Call WITH confirm=true → pipeline runs in background.

Pipeline runs in BACKGROUND. Use pipeline_status to check progress.
On KPI hit: auto-generates sequence + pushes to SmartLead as DRAFT.

PREREQUISITE: align_email_accounts must be called first (hard gate).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer", "description": "Gathering run to auto-continue"},
                "filters": {"type": "object", "description": "Apollo filters (from tam_gather preview)"},
                "target_people": {"type": "integer", "description": "Target total contacts to gather (default 100)"},
                "max_people_per_company": {"type": "integer", "description": "Maximum contacts per company (default 3)"},
                "target_companies": {"type": "integer", "description": "Target companies (auto-derived: ceil(target_people/max_people_per_company))"},
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves the preview"},
            },
            "required": ["run_id"],
        },
    },

    {
        "name": "extract_people",
        "description": """Extract contacts (people) from target companies in a pipeline run.

Searches Apollo for 3 contacts per target company. Roles auto-adjusted to offer:
- Payroll offer → VP HR, CHRO, Head of People
- SaaS offer → CTO, VP Engineering
- Fashion → Brand Director, CMO

FREE — uses /mixed_people/api_search (no credits).
Call after targets are confirmed at Checkpoint 2.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "integer", "description": "Pipeline run with confirmed targets"},
                "people_per_company": {"type": "integer", "default": 3, "description": "Contacts per company (default 3)"},
            },
            "required": ["run_id"],
        },
    },

    # ── CRM Queries (2) ──
    {
        "name": "query_contacts",
        "description": """Search contacts in CRM with filters. Use this to answer questions like:
- "Which leads need follow-ups?" → query_contacts with needs_followup=true
- "Which replies are warm?" → query_contacts with reply_category=interested
- "Show me contacts from my campaigns" → query_contacts with search or pipeline filter
- "How many contacts do we have?" → query_contacts with no filters

Returns contacts + a CRM link with filters applied so the user can view them in the browser.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "search": {"type": "string", "description": "Search by email, name, company"},
                "has_replied": {"type": "boolean", "description": "Only contacts who replied"},
                "needs_followup": {"type": "boolean", "description": "Contacts needing follow-up"},
                "reply_category": {"type": "string", "description": "Filter by reply type: interested, meeting_request, question, not_interested, out_of_office, wrong_person"},
                "pipeline_run_id": {"type": "integer", "description": "Contacts from a specific pipeline run"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },
    {
        "name": "crm_stats",
        "description": "Get CRM statistics: total contacts, by status, by source, by project. Use this for overview questions like 'how many contacts do I have?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
        },
    },

    # ── SmartLead Campaign Import (3) ──
    {
        "name": "list_smartlead_campaigns",
        "description": "Browse your SmartLead campaigns. Use this to help the user identify which campaigns belong to their project for blacklisting. Shows campaign name, lead count, status. Pass 'search' to filter by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
            },
        },
    },
    {
        "name": "import_smartlead_campaigns",
        "description": """Import previous campaigns into blacklist.

WHEN USER SAYS: "campaigns with my name", "I launched X before", "previous campaigns include Y" → call THIS.
Loads existing contacts so pipeline knows who NOT to gather again.

TWO-STEP: Call WITHOUT confirm → shows matched campaigns. Call WITH confirm=true → downloads contacts.
If 0 campaigns matched → shows available campaigns and asks to clarify.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Project to import into"},
                "rules": {
                    "type": "object",
                    "description": "How to match campaigns",
                    "properties": {
                        "prefixes": {"type": "array", "items": {"type": "string"}, "description": "Campaign name prefixes (e.g. ['ES Global'])"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "SmartLead campaign tags"},
                        "contains": {"type": "array", "items": {"type": "string"}, "description": "Substrings in campaign names"},
                        "exact_names": {"type": "array", "items": {"type": "string"}, "description": "Exact campaign names"},
                    },
                },
                "confirm": {"type": "boolean", "description": "Set true AFTER user approves matched campaigns"},
            },
            "required": ["project_id", "rules"],
        },
    },
    {
        "name": "set_campaign_rules",
        "description": "Save campaign detection rules on a project. These rules determine which SmartLead campaigns belong to this project for blacklisting and reply tracking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "rules": {
                    "type": "object",
                    "properties": {
                        "prefixes": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "contains": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["project_id", "rules"],
        },
    },

    # ── Utility (2) ──
    {
        "name": "estimate_cost",
        "description": "Estimate the cost of a gathering run before starting. Returns credits needed and estimated company count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_type": {"type": "string", "description": "Source type (default: apollo.companies.api)"},
                "filters": {"type": "object", "description": "Optional filters (max_pages, per_page)"},
                "target_count": {"type": "integer", "description": "How many target companies you want"},
            },
        },
    },
    {
        "name": "blacklist_check",
        "description": "Quick check: are these domains already in any campaign?",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domains": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["domains"],
        },
    },

    # ── Replies (4) ──
    {
        "name": "replies_summary",
        "description": "Get a summary of reply categories for a project. Returns counts per category (interested, meeting_request, question, not_interested, ooo, wrong_person, unsubscribe). Use this when the user asks 'how are my campaigns doing?' or 'what replies did I get?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project name to filter replies for"},
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "replies_list",
        "description": "List replies filtered by category, project, or search. Returns reply cards with lead info, message preview, and category. Use for 'show me warm replies' or 'which leads are interested?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category: interested, meeting_request, question, not_interested, out_of_office, wrong_person, unsubscribe, other"},
                "project_name": {"type": "string", "description": "Project name to filter replies for"},
                "search": {"type": "string", "description": "Search by lead name, email, or company"},
                "needs_reply": {"type": "boolean", "description": "Only show replies that need operator action"},
                "page": {"type": "integer", "default": 1, "description": "Page number"},
            },
        },
    },
    {
        "name": "replies_followups",
        "description": "List leads that need follow-up. Returns leads where the last message was from us and no reply received within the follow-up window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project name to filter follow-ups for"},
                "page": {"type": "integer", "default": 1, "description": "Page number"},
            },
        },
    },
    {
        "name": "replies_deep_link",
        "description": "Generate a deep link URL to view specific replies in the browser UI. Use when the user asks to 'see' or 'open' replies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project name for the deep link"},
                "category": {"type": "string", "description": "Optional category filter for the link"},
                "tab": {"type": "string", "description": "Optional tab name: all, meetings, interested, questions, other, not_interested, ooo, wrong_person, unsubscribe, followups"},
            },
            "required": ["project_name"],
        },
    },
    # ── User Feedback & Editing Tools ──
    {
        "name": "smartlead_edit_sequence",
        "description": """Edit a specific step of a generated sequence. User can change subject, body, or both.
Changes are saved to DB and pushed to SmartLead if campaign already created.

Use when user says: "change the subject of email 1", "rewrite email 3 body", "add a case study to email 2".""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "integer", "description": "The generated sequence ID"},
                "step_number": {"type": "integer", "description": "Which email step to edit (1-5)"},
                "subject": {"type": "string", "description": "New subject line (omit to keep current)"},
                "body": {"type": "string", "description": "New email body (omit to keep current). Use <br> for line breaks."},
            },
            "required": ["sequence_id", "step_number"],
        },
    },
    {
        "name": "edit_campaign_accounts",
        "description": "Change the email sending accounts on a SmartLead campaign. User can add or replace accounts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "SmartLead campaign ID (external_id)"},
                "email_account_ids": {"type": "array", "items": {"type": "integer"}, "description": "New list of SmartLead email account IDs"},
            },
            "required": ["campaign_id", "email_account_ids"],
        },
    },
    {
        "name": "override_company_target",
        "description": """Override a company's target/not-target status with user feedback.

Use when user says: "this company IS a target" or "remove this, not a match".
Stores override + reasoning for learning.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "integer", "description": "DiscoveredCompany ID"},
                "is_target": {"type": "boolean", "description": "True = target, False = not target"},
                "reasoning": {"type": "string", "description": "Why the user thinks this should be changed"},
            },
            "required": ["company_id", "is_target"],
        },
    },
    {
        "name": "provide_feedback",
        "description": """Store feedback and get ACTIVE next steps. Feedback types:

- "targets": wrong classifications → stores feedback, then suggests tam_re_analyze (new iteration with approval)
- "filters": wrong Apollo filters → stores feedback, shows current filters, suggests tam_gather with adjusted filters
- "sequence": wrong email content → stores for next sequence generation
- "general": anything else

WHEN USER SAYS: "these aren't targets" → feedback_type="targets" → then call tam_re_analyze
WHEN USER SAYS: "wrong filters", "too broad", "add keyword X" → feedback_type="filters" → prepare new tam_gather
WHEN USER SAYS: "wrong roles", "search for CTOs" → use set_people_filters instead (direct tool)

Each feedback type returns specific next_action with the right tool to call.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "feedback_type": {"type": "string", "enum": ["sequence", "filters", "analysis", "targets", "general"]},
                "feedback_text": {"type": "string", "description": "The user's feedback in their own words"},
                "context": {"type": "object", "description": "Optional: step_number, company_id, filter_name, etc."},
            },
            "required": ["project_id", "feedback_type", "feedback_text"],
        },
    },
    {
        "name": "activate_campaign",
        "description": """Activate a campaign — START sending to real leads.

WHEN USER SAYS: "activate", "launch", "start the campaign", "go live" → call THIS.
NEVER call without explicit user approval.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "SmartLead campaign ID"},
                "user_confirmation": {"type": "string", "description": "Quote user's exact words confirming activation"},
            },
            "required": ["campaign_id", "user_confirmation"],
        },
    },
]
