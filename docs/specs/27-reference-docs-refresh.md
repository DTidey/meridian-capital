# Reference Docs Refresh

**Spec file:** `docs/specs/27-reference-docs-refresh.md`
**Spec slug:** reference-docs-refresh
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The user asked for a review of `README.md` and `docs/` on 2026-10-14. The living reference docs
  had drifted from the code over packets 08-26: hard-coded test counts (505, 528), six dashboard
  pages (now seven), a $10M NAV (config: $100,000), SQLAlchemy 1.4 / pandas 2.x, FRED "not yet
  used", FMP as the fundamentals source, AI analysis credited to "Claude", 13-F and fundamentals
  behaviour before packets 17-26, missing CLI flags and environment variables, and an ERD missing
  5 of 30 tables and several columns.
- `.env.example` lacked the Alpaca variables that execution requires.

## Scope
In scope:
- `README.md`, `.env.example`, `docs/architecture.md`, `docs/erd.md`, `docs/specification.md`,
  `docs/run_dashboard.md`; new `docs/README.md`.

Out of scope / non-goals:
- Historical records: `docs/specs/`, `docs/test-plans/`, `docs/prompts/`,
  `docs/CHANGES_2026-06-15.md`.
- Code, including the inconsistent step labels in `run_data.py` logging ("[1/8]" ... "[7/9]").

## Assumptions
- Facts were checked against the code and config on 2026-10-14 (schema via SQLAlchemy metadata,
  versions via `requirements.txt`, flags via argparse, dashboard pages via `dashboard/app.py`).

## Proposed behavior / API
- Documentation only; no behaviour change.

## Acceptance criteria
- AC1: No living reference doc or `README.md` states a test count; each says to run `make test`.
- AC2: `docs/erd.md` lists all 30 tables, with columns matching the SQLAlchemy definitions.
- AC3: Dashboard docs describe seven pages including Ticker, and state what the dashboard writes or
  calls, including that the Research page can submit orders to Alpaca (live when
  `ALPACA_PAPER=false`), and the LLM calls (Letter page on open, Regenerate, JARVIS, summaries).
- AC4: `specification.md`, `architecture.md` and `README.md` reflect current providers (yfinance
  fundamentals, FMP or 8-K transcripts, FRED in tail risk, OpenFIGI, OpenAI/OpenRouter with
  configurable models), the $100,000 default NAV, and the `--refresh-fundamentals` /
  `--refetch-13f` flags.
- AC5: `.env.example` lists `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` and `ALPACA_PAPER`.
- AC6: `docs/README.md` separates living references from historical records.

## Security considerations
- No meaningful security impact: documentation and an example environment file with empty values.

## Edge cases
- None.

## Test guidance
- AC1 -> `grep -rnE "[0-9]{3} tests" README.md docs/*.md` finds nothing
- AC2 -> script comparing `docs/erd.md` blocks with SQLAlchemy metadata (test plan)
- AC3-AC6 -> review

## Decision log
- 2026-10-14: ERD columns generated from the schema rather than hand-written, so drift can be
  checked mechanically; noted in the ERD and `docs/README.md`.
- 2026-10-14: Historical packet docs left unchanged; superseded definitions get a pointer instead.
- 2026-10-14 (Reviewer fact-check): one blocker fixed: the first draft said the dashboard never
  places orders, but Research's "Execute All Approved Trades" runs `run_execution.py`. Also
  fixed: the Letter page generates a letter on open; models come from `analysis.analyzer_models`
  per analyzer; 16 ratios, not 24; earnings lookahead 90 days; docker-compose location;
  `--no-filings` also skips transcripts; ERD pre-trade and left-join relationships; NAV wording
  (config value, not code default); temp-file SQLite in tests; `run_transcripts.py` in the tree.
