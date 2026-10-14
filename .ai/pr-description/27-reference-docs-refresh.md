## Summary

Brings the living reference docs back in line with the code after packets 08-26. Documentation
only.

- `README.md`: Ticker page and JARVIS described; LLM wording; `OPENFIGI_API_KEY`; new
  `run_data.py` flags and `run_all.py` options; configurable models; test count removed; new
  Contributing section.
- `.env.example`: Alpaca variables added (execution needs them).
- `docs/erd.md`: all 30 tables (5 Layer 7 tables were missing) with columns generated from the
  SQLAlchemy definitions; notes on month-end periods, 13-F quarters and saved summaries.
- `docs/architecture.md` and `docs/specification.md`: current library versions, providers
  (yfinance fundamentals, FMP or 8-K transcripts, FRED in tail risk, OpenFIGI, OpenAI/OpenRouter),
  $100,000 default NAV, seven-page dashboard, today's 13-F and fundamentals behaviour, missing
  environment variables, no hard-coded test counts.
- `docs/run_dashboard.md`: seven pages, `make sync`, and what the dashboard writes or calls (it
  isn't read-only).
- New `docs/README.md`: which docs are living references and which are historical records.

Historical packet specs, test plans and build prompts are unchanged.

## Spec

- Spec: `docs/specs/27-reference-docs-refresh.md`
- Test plan: `docs/test-plans/27-reference-docs-refresh.md`
- PR draft path: `.ai/pr-description/27-reference-docs-refresh.md`

## Acceptance Criteria

- [x] AC1: No hard-coded test counts in living docs or README.
- [x] AC2: ERD lists all 30 tables with columns matching the schema.
- [x] AC3: Dashboard docs cover seven pages and what the dashboard writes or calls, including
  that Research can submit orders to Alpaca.
- [x] AC4: Providers, NAV, models and CLI flags current.
- [x] AC5: `.env.example` lists the Alpaca variables.
- [x] AC6: `docs/README.md` separates living references from historical records.

## Security Review

- [x] Security considerations reviewed in `docs/specs/27-reference-docs-refresh.md` (no
  meaningful impact; example env file has empty values)

## Reviewer fact-check

A fresh subagent checked every changed statement against the code. One blocker (the draft said
the dashboard never places orders; Research's execute button runs `run_execution.py`) and seven
Important inaccuracies were fixed; see the spec's decision log.

## Validation

- ERD check script: 30 of 30 tables, no column mismatches
- Stale-number grep: nothing found
- `make lint`: pass
- `make test`: 868 passed (no code changed)
