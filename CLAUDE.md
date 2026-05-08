# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero AI Daily Papers — an automated pipeline that fetches arXiv papers, analyzes them with an LLM (ModelScope Qwen or OpenAI GPT), archives into Zotero with structured notes, and sends notifications via Feishu/WeChat Work webhooks.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Build knowledge base from existing Zotero papers (prerequisite for first run)
python zotero_indexer.py

# Run the main pipeline
python main.py

# Dry run (no Zotero writes)
DRY_RUN=1 python main.py

# Debug mode (verbose phase-one LLM output)
DEBUG_PHASE_ONE=1 python main.py

# Console entry points (after pip install -e .)
paperread        # runs main:main
paperread-index  # runs zotero_indexer:build_knowledge_base
```

No test suite or linter is configured in the repo. Code style follows PEP 8.

## Architecture

### Core Pipeline (4 files)

- **main.py** (~1200 lines) — Main orchestrator: fetches from arXiv, runs two-stage LLM analysis, writes to Zotero, sends notifications, optionally syncs to Feishu Wiki. The `CONFIG` dict at the top controls research categories, LLM model settings, and thresholds.
- **zotero_indexer.py** (~237 lines) — Reads all papers from Zotero's "DailyPapers" collection and builds `knowledge_base.json` (title, short review, full notes per paper).
- **notifier.py** (~500 lines) — `WxWorkNotifier` and `FeishuNotifier` classes for webhook-based push notifications, composed via `NotificationManager`.
- **feishu_wiki.py** (~350 lines) — `FeishuWikiClient` mirrors Zotero paper notes to a Feishu Wiki, replicating the Zotero collection hierarchy. Optional; activated when `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `FEISHU_WIKI_ROOT_NODE_TOKEN` are all set.

### Two-Stage LLM Analysis

1. **Phase One (lightweight filtering):** `check_relevance_phase_one()` — sends title + abstract + short reviews from knowledge base. Returns a relevance score (0-10). Papers scoring >= 7 proceed.
2. **Phase Two (deep analysis):** `deep_analyze_phase_two()` — sends paper with full notes from matched knowledge base entries. Returns structured JSON: recommendation ("must-read"/"worth-reading"/"skipable"), methodology, core concepts, sharp review, comparison with existing papers.

### Cold Start vs Incremental

- `state.json` tracks `is_first_run` and `last_date`.
- First run: fetches top-10 latest + top-10 most relevant per category (up to 20 total), single-paper deep analysis.
- Subsequent runs: only papers newer than `last_date`, uses the two-stage pipeline.

### Async & Rate Limiting

Uses `aiohttp` for async HTTP. Built-in delays: 6-10s between arXiv API requests, 7-19s on 429 errors, exponential backoff retries.

### Auto-Generated State Files (gitignored)

- `state.json` — first-run flag and last processed date
- `history.json` — list of processed arXiv paper IDs (deduplication)
- `knowledge_base.json` — indexed Zotero paper data (~430KB)
- `feishu_wiki_node_cache.json` — Feishu Wiki node ID cache (avoids repeated lookups)

### Zotero Collection Structure

Root collection "DailyPapers" with sub-collections per research category (e.g., UAV_VLN, MultiAgent_Game_Theory, MARL). Each item gets a structured HTML note with recommendation badge, methodology, core concepts, and critical review.

## Environment Variables

See `.env.example` for the full list. Key ones:

| Variable | Purpose |
|----------|---------|
| `ZOTERO_USER_ID` / `ZOTERO_API_KEY` | Zotero API credentials |
| `MODELSCOPE_API_KEY` | ModelScope LLM API key |
| `OPENAI_API_KEY` | OpenAI fallback (if configured) |
| `FEISHU_WEBHOOK_URL` | Feishu notification webhook |
| `WXWORK_WEBHOOK_URL` | WeChat Work notification webhook |
| `DRY_RUN` | Set to `1` to skip Zotero writes |
| `DEBUG_PHASE_ONE` | Set to `1` for verbose phase-one output |
| `ENABLE_NOTIFICATION` | Set to `0` to disable notifications |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | Feishu self-built app credentials (for wiki mirroring) |
| `FEISHU_WIKI_ROOT_NODE_TOKEN` | Target Feishu Wiki root node token |

## CI/CD

Two GitHub Actions workflows in `.github/workflows/`:

- **daily.yml** — runs at 00:00 UTC, builds knowledge base, runs main.py, commits state files back to repo.
- **daily_paper.yml** — runs at 09:00 UTC, uploads state files as artifacts.

## Conventions

- Configuration (research categories, LLM model, thresholds) lives in the `CONFIG` dict at the top of `main.py` — not in external config files.
- `mainold.py` is a legacy file and not used in production.
- Chinese is the primary documentation language; `README_EN.md` provides an English translation.
