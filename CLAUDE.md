# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero AI Daily Papers — an automated pipeline that fetches arXiv papers, analyzes them with an LLM (default: DeepSeek-V4-Pro via ModelScope, with multi-model fallback), archives into Zotero with structured notes, and sends notifications via Feishu/WeChat Work webhooks.

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

# One-time Feishu Wiki layout bootstrap (optional)
python bootstrap_feishu_wiki_layout.py

# Standalone Feishu Wiki integration test (requires Feishu env vars)
python test_feishu_wiki.py
```

No test suite or linter is configured in the repo. Code style follows PEP 8.

## Data Flow

```
categories.json ──┬──> main.py ──────────────────────────────────────┐
                  │    │                                              │
                  │    ├─ Fetch from arXiv (API + OAI-PMH fallback)  │
                  │    ├─ Local keyword filtering                    │
state.json ───────┤    ├─ Phase 1: relevance scoring (0-10)         │
                  │    │    ↑ knowledge_base.json (short reviews)    │
history.json ─────┤    ├─ Phase 2: deep analysis (>= 7 only)        │
                  │    │    ↑ knowledge_base.json (full notes)       │
                  │    ├─ Write to Zotero (collections + HTML notes) │
                  │    ├─ Mirror to Feishu Wiki (optional)           │
                  │    └─ Send notifications (Feishu + WeChat Work)  │
                  │                                                   │
                  └──> zotero_indexer.py ──> knowledge_base.json ────┘
```

## Architecture

### Core Pipeline (4 files)

- **main.py** (~1400 lines) — Main orchestrator. The `CONFIG` dict at the top controls LLM model settings and thresholds. Research categories loaded from `categories.json`. Contains `MultiModelLLM` class, two-stage analysis, arXiv fetching (standard API + OAI-PMH fallback), Zotero writing, and notification dispatch.
- **zotero_indexer.py** (~260 lines) — Reads all papers from Zotero's "DailyPapers" collection and builds `knowledge_base.json` (title, short review, full notes per paper). Reads category list from `categories.json`.
- **notifier.py** (~560 lines) — `WxWorkNotifier` and `FeishuNotifier` classes for webhook-based push notifications, composed via `NotificationManager`. Exports a global `notifier = NotificationManager()` singleton.
- **feishu_wiki.py** (~410 lines) — `FeishuWikiClient` mirrors Zotero paper notes to a Feishu Wiki, replicating the Zotero collection hierarchy. Optional; activated when `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `FEISHU_WIKI_ROOT_NODE_TOKEN` are all set.

### Key Entry Points

| Concern | File | Function/Class |
|---------|------|---------------|
| Pipeline entry | `main.py` | `main()` |
| LLM calls + failover | `main.py` | `MultiModelLLM.call()` |
| Relevance filtering | `main.py` | `check_relevance_phase_one()` |
| Deep analysis | `main.py` | `deep_analyze_phase_two()` |
| arXiv fetching | `main.py` | `fetch_arxiv_single()`, `fetch_papers_oai_pmh()` |
| Zotero writes | `main.py` | `write_to_zotero()`, `create_zotero_note()` |
| Knowledge base build | `zotero_indexer.py` | `build_knowledge_base()` |
| Notification dispatch | `notifier.py` | `NotificationManager` methods |
| Feishu Wiki mirror | `feishu_wiki.py` | `FeishuWikiClient.mirror_paper_to_wiki()` |

### Two-Stage LLM Analysis

1. **Phase One (lightweight filtering):** `check_relevance_phase_one()` — sends title + abstract + short reviews from knowledge base. Returns a relevance score (0-10). Papers scoring >= 7 proceed.
2. **Phase Two (deep analysis):** `deep_analyze_phase_two()` — sends paper with full notes from matched knowledge base entries. Returns structured JSON: recommendation ("must-read"/"worth-reading"/"skipable"), methodology, core concepts, sharp review, comparison with existing papers.

### Multi-Model LLM Failover

All LLM calls go through `MultiModelLLM.call()` which provides automatic failover: on 429 rate limit or 401 auth error, it switches to the next model. Default fallback chain: DeepSeek-V4-Pro -> GLM-5.1 -> MiniMax-M2.5 -> Kimi-K2.5 (all via ModelScope API). Two rounds of attempts with a 60s pause between rounds. Override with `LLM_MODEL` and `BASE_URL` env vars.

### arXiv Fetching Strategy

Two fetching methods with automatic fallback:
1. **Standard arXiv API** — primary method, 30s request interval, max 3 retries with 60s wait on 429.
2. **OAI-PMH** — fallback when the standard API is rate-limited. Fetches recent papers via OAI-PMH protocol, then applies local keyword filtering using arXiv query syntax parsing.

`fetch_arxiv_single` returns `None` on failure (distinguished from empty results). Failed keywords are tracked and reported in error notifications.

### Cold Start vs Incremental

- `state.json` tracks `is_first_run`, `last_date`, and initialized categories.
- First run: fetches top-10 latest + top-10 most relevant per category (up to 20 total), single-paper deep analysis.
- Subsequent runs: only papers newer than `last_date`, uses the two-stage pipeline.

### Configuration Files

- **categories.json** — Research category definitions (keywords, arXiv categories, descriptions). Can be edited directly on GitHub. Both `main.py` and `zotero_indexer.py` read from this file.
- **CONFIG dict** (top of `main.py`) — LLM model settings and scoring thresholds.

### Auto-Generated State Files (gitignored)

- `state.json` — first-run flag, last processed date, initialized categories
- `history.json` — list of processed arXiv paper IDs (deduplication)
- `knowledge_base.json` — indexed Zotero paper data
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
| `LLM_MODEL` / `BASE_URL` | Override default LLM model and API endpoint |

## CI/CD

One GitHub Actions workflow in `.github/workflows/`:

- **daily_paper.yml** — runs at 00:00 UTC daily (+ manual dispatch), builds knowledge base, runs main.py, commits state files back to repo. 120-minute timeout. Uses Python 3.10. Concurrency group `paperread-daily` prevents parallel runs.

## Conventions

- Research category configuration lives in `categories.json` (external config file, editable on GitHub). LLM model settings and thresholds are in the `CONFIG` dict at the top of `main.py`.
- `mainold.py` is a legacy file and not used in production.
- Chinese is the primary documentation language; `README_EN.md` provides an English translation.
- Commit messages follow Conventional Commits: `type(scope): description` (types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`).
- All API keys and secrets must be set via environment variables, never hardcoded.
