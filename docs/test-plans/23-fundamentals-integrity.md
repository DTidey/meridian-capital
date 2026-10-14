# Test Plan: fundamentals-integrity

Path: `docs/test-plans/23-fundamentals-integrity.md`

## What changed
- `data/fundamentals.py`: outstanding shares (not issued); statements aligned by month-end
  period; `period_end` stored as month-end; `merge_period_duplicates`; `refresh` option.
- `run_data.py`: `--refresh-fundamentals`.
- `dashboard/ticker_valuation.py`: balance-sheet items from the latest quarter that has them.

## Acceptance criteria coverage
- AC1: `tests/test_fundamentals.py::TestSharesOutstanding` (5 tests, JPM figures)
- AC2: `TestAlignment` (4 tests: BAC-style missing latest balance sheet, WMT-style income gap,
  52/53-week vs month-end dates, empty statement)
- AC3: `TestMonthEnd` (7 tests)
- AC4: `TestMergePeriodDuplicates` (4 tests: AAPL FY2025 pair, newest wins whichever date it
  has, idempotent, run by `update_fundamentals`)
- AC5: `TestRefresh` (2 tests)
- AC6: `tests/test_ticker_valuation.py::TestValuation::test_balance_sheet_from_latest_quarter_that_has_one`
- AC7: `tests/test_quality.py::TestIncomeOnlyNewestQuarter` (fails without the fix)
- AC2 through ingestion: `TestProcessTickerAlignment::test_bac_shaped_statements_stored_by_period`;
  `test_newest_column_wins_shared_month`; merge tie: `test_merge_tie_prefers_month_end_row`

## Edge cases
- From spec: single non-month-end row re-dated (`test_newest_kept_and_redated`, AMD); treasury
  without issued (`test_treasury_without_issued_uses_last_key`); empty statement.
- Additional adversarial cases:
  - Present-but-NaN "Ordinary Shares Number": `test_nan_ordinary_falls_through`
  - Fiscal year ending 2 January: `test_nearest_month_end[2026-01-02-2025-12-31]`
  - Leap-year February: `test_nearest_month_end[2024-02-26-2024-02-29]`
  - Older-dated row is the newer fetch: `test_newest_wins_even_if_older_date_is_newer_fetch`

## Notes
- Dry run (2026-10-14) on a SQLite copy of production with a full yfinance refresh (503 tickers,
  about 10 minutes): 12 duplicates merged and none left; all dates month-end; BAC and GS
  quarters realigned; 41 of 519 latest share counts fell by more than 3% and none rose.

## Commands
```bash
make lint
make test
python run_data.py --no-filings --no-13f --refresh-fundamentals   # once, after merge
```
