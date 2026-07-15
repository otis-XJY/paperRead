# Repository Guidelines

## Project Structure & Module Organization

This Python 3.8+ project uses a flat module layout. `main.py` runs paper discovery and LLM analysis. `zotero_indexer.py` builds the Zotero knowledge base; `notifier.py` and `feishu_wiki.py` handle notifications and Wiki mirroring. Configuration starts from `.env.example`, with categories in `categories.json`. Workflow state resides in `history.json`, `knowledge_base.json`, `state.json`, and `feishu_wiki_node_cache.json`. Documentation is under `docs/`; automation and templates are under `.github/`.

## Build, Test, and Development Commands

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and supply the required Zotero and LLM credentials. Common commands are:

```bash
python zotero_indexer.py       # refresh knowledge_base.json
DRY_RUN=1 python main.py       # exercise the pipeline without Zotero writes
DEBUG_PHASE_ONE=1 python main.py  # show verbose relevance-filter output
python test_feishu_wiki.py     # run the credentialed Feishu integration check
```

On PowerShell, set flags first, for example `$env:DRY_RUN='1'; python main.py`. Packaging metadata is in `pyproject.toml`; `python -m build` creates distributions when the `build` package is installed.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants and environment variables. Add concise docstrings to public functions and classes and preserve UTF-8 encoding for bilingual documentation. No formatter or linter is enforced, so review imports, line length, and naming manually.

## Testing Guidelines

There is no configured unit-test framework or coverage threshold yet. New logic should add isolated `test_*.py` tests, preferably under a new `tests/` directory, and avoid real API calls through mocks. Treat `test_feishu_wiki.py` as a live integration test: it requires Feishu credentials and creates remote content. Always run the dry-run pipeline before submitting workflow changes.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style prefixes such as `feat:` and `chore:`. Write imperative, present-tense subjects under 72 characters; reference issues with `Fixes #123` when applicable. Pull requests should follow `.github/PULL_REQUEST_TEMPLATE.md`: summarize motivation and scope, identify the change type, list reproducible test steps and configuration, link the issue, update relevant docs (including both READMEs when needed), and include screenshots only for visible output changes.

## Security & Generated Data

Never commit `.env`, API keys, webhook URLs, or credentials. Use GitHub Secrets in Actions. Inspect generated JSON before committing it because workflow state or indexed metadata may contain sensitive information.
