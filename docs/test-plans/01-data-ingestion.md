# Test Plan: data-ingestion

Path: `docs/test-plans/01-data-ingestion.md`

## What changed
- Initial implementation of Layer 1: Data Ingestion. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_universe.py` — `TestCacheIsFresh`, `TestFetchSp500`, `TestLoadBenchmarks`, `TestGetAllTickers`
- AC2: `tests/test_market_data.py` — `TestLastStoredDate`, `TestUpsertPrices`, `TestFloat`
- AC3: `tests/test_fundamentals.py`
- AC4: `tests/test_sec_data.py`
- AC5: `tests/test_sec_data.py` — `TestIsCeoCfo`, `TestParseForm4Xml`, `TestFlagClusterBuys`
- AC6: `tests/test_institutional.py` — `TestParseInfotable`, `TestPriorQuarterReportDate`, `TestSummariseHoldings`
- AC7: `tests/test_short_interest.py`, `tests/test_estimates.py`
- AC8: `tests/test_db.py` — `test_all_tables_created`, `test_all_indexes_created`, `test_wal_mode_enabled`, `test_foreign_keys_enabled`, `test_schema_is_idempotent`, `test_get_engine_creates_parent_dirs`, `test_daily_prices_primary_key`, `test_insider_transactions_unique_constraint`
- AC9: `tests/test_run_data.py`
- AC10: `tests/test_providers.py` — `test_no_keys_all_fallback`, `test_polygon_key_routes_prices`, `test_fmp_key_routes_transcripts_only`, `test_fred_key_routes_macro`, `test_empty_string_key_treated_as_absent`, `test_all_keys_present`

## Edge cases
- From spec:
  - Ticker normalisation: `.` replaced with `-` before passing to yfinance (e.g. `BRK.B` → `BRK-B`)
  - 7-day Wikipedia cache TTL: re-scrape is skipped unless cache is expired or `--force-universe` is passed
  - SEC rate limiting: enforced at 8 req/s using `time.monotonic()` — not wall-clock time
  - Form 4 codes `A`, `M`, `F` (grants/exercises) must not set `is_open_market = 1`
  - Insider cluster flag: only triggered when >= 3 insiders buy (code `P`) within a 30-day rolling window — sells do not count
  - `insert_or_replace` upsert: re-running price ingestion must not duplicate rows
  - Incremental prices: only bars newer than the stored max date are requested

- Additional adversarial cases:
  - Wikipedia HTML structure changes: `pandas.read_html` returns zero rows or raises; the universe loader should propagate an exception rather than silently store an empty universe
  - All tickers already have today's price: `_last_stored_date` returns today, so no HTTP request is issued and no rows are inserted
  - SEC EDGAR rate limiter under burst: firing 9 requests rapidly should block the 9th until the 1-second window reopens, verifiable by asserting monotonic elapsed time >= 1 s
  - `FMP_API_KEY` present but `POLYGON_API_KEY` absent: prices use yfinance, fundamentals use FMP — provider selection must not bleed between subsystems
  - Form 4 XML with missing `<transactionCode>` element: parser should skip the transaction gracefully rather than raising `KeyError`
  - Insider cluster window spanning a month boundary: e.g. three buys on 2026-03-15, 2026-03-28, 2026-04-10 — all within 30 days — flag must be raised despite the calendar-month crossing

## Notes
- Flaky risks: External API calls are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures.
