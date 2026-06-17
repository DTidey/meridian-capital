## Summary
- Implements Layer 1 (Data Ingestion): populates PostgreSQL with S&P 500 universe, 3 years of daily OHLCV prices, quarterly fundamentals with 24 derived ratios, SEC EDGAR filings, 13-F institutional holdings, short interest, analyst estimates, earnings calendar, and earnings transcripts.
- Provides dialect-aware upsert helpers (`insert_or_replace`, `insert_or_ignore`) so tests run against SQLite in-memory and production runs against PostgreSQL without code changes.
- Centralises provider selection (yfinance vs Polygon for prices; yfinance vs FMP for fundamentals) in `data/providers.py`, activated by the presence of `POLYGON_API_KEY` or `FMP_API_KEY` environment variables.
- Exposes a single `initialise_schema(engine)` call that creates all tables across all layers from a shared SQLAlchemy `metadata` object.

## Spec
- Spec: `docs/specs/01-data-ingestion.md`
- Test plan: `docs/test-plans/01-data-ingestion.md`
- PR draft path: `.ai/pr-description/01-data-ingestion.md`

## Acceptance Criteria
- [x] AC1: The system scrapes S&P 500 constituents from Wikipedia (with a 7-day cache TTL) and stores them in `sp500_universe`, and adds 18 benchmark/ETF tickers to `benchmark_tickers`, so that `get_all_tickers()` returns the combined deduplicated list.
- [x] AC2: The system fetches up to 3 years of daily OHLCV prices incrementally (only bars newer than the stored max date) and upserts them into `daily_prices` using a dialect-aware `INSERT … ON CONFLICT` statement, so that re-runs are safe and do not duplicate rows.
- [x] AC3: The system fetches quarterly and annual financial statements and computes 24 derived ratios (margins, returns, growth, leverage, cash, efficiency) in-process, storing both raw fields and ratios in the `fundamentals` table, so that downstream layers never need to re-derive them.
- [x] AC4: The system fetches SEC EDGAR 10-K, 10-Q, 8-K, and Form 4 filings at no more than 8 requests/second, using `SEC_USER_AGENT` and `SEC_USER_EMAIL` from environment variables as required by the SEC, and stores results in `sec_filings` and `insider_transactions`.
- [x] AC5: The system parses Form 4 transactions to set `is_open_market = 1` for codes P and S, `is_ceo_cfo = 1` when the insider title contains CEO or CFO, and raises a cluster flag in `insider_cluster_flags` when 3 or more insiders buy within a 30-day window.
- [x] AC6: The system fetches 13-F institutional holdings for 9 tracked funds from SEC EDGAR, stores per-fund rows in `institutional_holdings`, and aggregates them into `institutional_summary` (funds_holding count, net_share_change, new_positions count).
- [x] AC7: The system stores daily snapshots of short interest and analyst estimates (appending one row per ticker per run date) into `short_interest` and `analyst_estimates` respectively, so that time-series history accumulates for Layer 2 revision scoring.
- [x] AC8: All tables across all layers are created in a single `initialise_schema(engine)` call via a shared SQLAlchemy `metadata` object, and all tests pass using an in-memory SQLite engine without code changes to business logic.
- [x] AC9: The entry point `run_data.py` supports `--no-filings`, `--no-13f`, `--forms`, `--force-universe`, `--tickers`, and `--verbose` flags, and prints a structured summary on completion.
- [x] AC10: Provider selection (yfinance vs Polygon for prices; yfinance vs FMP for fundamentals) is determined at startup by the presence of `POLYGON_API_KEY` or `FMP_API_KEY` environment variables, centralised in `data/providers.py`.

## Security Review
- [x] Security considerations were reviewed and updated in the linked spec
- [x] No meaningful security impact beyond API key handling via environment variables

## Validation
- [x] `make lint`
- [x] `make test`
- [x] `make security`

## GitHub Checks
- Required checks for `main`:
  - `CI / test`
  - `CodeQL / analyze`

## Changelog
- [x] Add to `CHANGELOG.md` under `## Unreleased`

## Open Risks
- None. All tests passing. Code is read-only with respect to external systems during tests.
