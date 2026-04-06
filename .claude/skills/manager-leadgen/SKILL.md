# Leadgen Pipeline Manager — Reference

> **NOTE**: This skill is REFERENCE documentation. The actual orchestration lives in `/launch` command (`.claude/commands/launch.md`). The command IS the orchestrator — read it directly.

This document describes the pipeline architecture for understanding. Do NOT follow this file for execution — follow `/launch` instead.

## Architecture Summary

- **7 steps**, flat orchestration in `/launch` command
- **2 human checkpoints**: Step 3 (strategy approval) + Step 7 (launch approval)
- **Agent spawning** in Step 4: background agents for batch scrape+classify
- **State tracking**: `state.yaml` updated at every step boundary
- **Resume**: if state.yaml exists on re-run, skip completed steps

## Phase Skip Matrix

| Step | Mode 1 (Fresh) | Mode 2 (New Campaign) | Mode 3 (Append) |
|------|:-:|:-:|:-:|
| 1. Offer Extraction | AUTO | SKIP | SKIP |
| 2. Filters + Probe | AUTO | AUTO | AUTO (seeded) |
| 3. Strategy Approval | **CHECKPOINT 1** | **CHECKPOINT 1** | **CHECKPOINT 1** |
| 4. Gather + Scrape + Classify | AUTO (agents) | AUTO (agents) | AUTO + dedup |
| 5. People Extraction | AUTO | AUTO | AUTO + dedup |
| 6. Sequence Generation | AUTO | AUTO | SKIP |
| 7. Campaign Push | CREATE → **CP2** | CREATE → **CP2** | ADD → **CP2** |

## Three Modes

- **Mode 1** (`/launch https://acme.com payments in US`): Full pipeline, all steps
- **Mode 2** (`/launch project=X segment=LENDING geo=UK`): Reuses project, new campaign
- **Mode 3** (`/launch campaign=3070919 kpi=+100`): Appends contacts to existing campaign

## Domain Skills (read by /launch at each step)

| Step | Skill | Purpose |
|------|-------|---------|
| 1 | offer-extraction | Extraction schema and rules |
| 2 | apollo-filter-mapping | Industry taxonomy, keyword generation |
| 3 | quality-gate | Cost estimation section |
| 4 | company-qualification | Via negativa classification rules |
| 4 | quality-gate | Keyword regeneration angles, exhaustion detection |
| 5 | pipeline-state | People extraction retry logic |
| 6 | email-sequence | 12-rule GOD_SEQUENCE |
