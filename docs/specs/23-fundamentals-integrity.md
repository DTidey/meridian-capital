# Fundamentals Integrity

**Spec file:** `docs/specs/23-fundamentals-integrity.md`
**Spec slug:** fundamentals-integrity
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
Three defects in yfinance fundamentals ingestion (`data/fundamentals.py`), found 2026-10-14 while
investigating 12 near-duplicate annual rows:
1. **Share counts include treasury stock.** `shares_outstanding` reads "Share Issued" before
   anything else. For companies holding treasury stock this overstates shares: JPM 4.11B stored
   vs 2.66B outstanding ("Ordinary Shares Number"), so its market cap shows $1.36T instead of
   about $0.88T, and value-factor yields are understated. ADBE had 601M stored for four
   quarters (about 400M outstanding).
2. **Statements paired by position, not date.** Balance-sheet and cash-flow values are read at
   the income statement's column index. Yahoo sometimes returns different quarters per
   statement: BAC and GS lack the latest balance sheet and cash flow, so every stored quarter has
   the previous quarter's equity, debt, shares and cash flow; WMT's income statement skips a
   quarter, shifting older rows. 7 of 40 statement sets in a 20-ticker sample differed.
3. **Duplicate periods.** `period_end` comes straight from Yahoo, whose convention changed
   between the true fiscal end (AAPL 2025-09-27) and the month-end (2025-09-30). It is part of
   the primary key, so a re-fetch adds a second row: 12 annual pairs, with differing values.

Fundamentals feed the value and quality factors (quarterly) and the Ticker page (valuation:
quarterly; Fundamentals section: latest annual).

## Scope
In scope:
- yfinance extraction, statement alignment, `period_end` normalisation, a one-off merge of
  existing near-duplicate rows, and a forced refresh option.

Out of scope / non-goals:
- The FMP backend's fetching (not selected: `Providers.fundamentals` is always yfinance). Its
  rows do pass through `_upsert_period`, so their dates are month-end-normalised too.
- Rows older than yfinance's window (about 5 quarters / 4 years) keep their old share counts and
  pairing; nothing compares shares that far back, but `_roe_stability` (12 quarters) reads them.
- QoQ and YoY growth ratios still pair income periods by position, so a skipped income quarter
  (WMT) compares the wrong periods. Follow-up.
- Other line items' definitions (e.g. whether total debt includes leases).
- Recomputing historical factor scores (the nightly run rescores).

## Assumptions
- yfinance balance sheets carry "Ordinary Shares Number" (outstanding), "Share Issued" and
  "Treasury Shares Number" (checked live for 8 tickers on 2026-10-14).
- A fiscal period end is never more than 10 days from a calendar month-end (52/53-week years end
  within a few days of one).

## Proposed behavior / API
### Public interface
- `data.fundamentals.month_end(date_str) -> str`
- `data.fundamentals._shares_outstanding(balance, col)`
- `data.fundamentals._align(df, columns)`
- `data.fundamentals.merge_period_duplicates(conn) -> int`
- `update_fundamentals(conn, tickers, config, providers, refresh=False)`
- CLI: `run_data.py --refresh-fundamentals`

## Acceptance criteria
- AC1: Shares outstanding = "Ordinary Shares Number" if present; else "Share Issued" minus
  "Treasury Shares Number" when both are present; else "Share Issued"; else "Common Stock Shares
  Outstanding".
- AC2: Balance-sheet and cash-flow values are taken from the column with the same period
  (after AC3 normalisation) as the income statement column; a statement without that period
  gives empty values for that row, never a neighbouring quarter's.
- AC3: `period_end` is stored as the nearest calendar month-end (e.g. 2025-09-27 becomes
  2025-09-30, 2026-01-02 becomes 2025-12-31).
- AC4: `merge_period_duplicates` groups rows by ticker, period type and month-end-normalised
  `period_end`; per group it keeps the most recently updated row, deletes the others, and sets
  the kept row's `period_end` to the normalised date. It returns the number of rows deleted,
  logged at WARNING when non-zero. `update_fundamentals` runs it first; a second run deletes
  nothing.
- AC5: `update_fundamentals(..., refresh=True)` fetches every ticker regardless of
  `refresh_days`; `run_data.py --refresh-fundamentals` passes it.
- AC6: The Ticker page valuation (packet 21) takes shares, debt, cash and equity from the latest
  quarter that has a share count (of the six most recent), because after AC2 the newest quarter
  can have an income statement but no balance sheet yet (BAC, GS). TTM figures still use the
  four latest quarters, so TTM FCF and dividends show "—" until the newest cash flow is published.
- AC7: The quality factor (`factors/quality.py`) scores from quarters that have `total_assets`,
  so an income-only newest quarter can't fail every balance-sheet test. (The value factor
  already takes the latest non-empty value per field.)

## Security considerations
- No meaningful security impact. AC4 deletes rows that duplicate another row's period; the kept
  row is the most recent, and all data is re-fetchable from yfinance.

## Edge cases
- Group with a single row whose date isn't a month-end: re-dated only.
- Kept row has empty fields the deleted row had: the newest row wins entirely (no mixing of
  values from different fetches).
- Treasury count present but issued missing: AC1 falls through to "Common Stock Shares
  Outstanding".
- Statement with no columns: values empty, as before.

## Test guidance
- AC1 -> `tests/test_fundamentals.py::TestSharesOutstanding`
- AC2 -> `TestAlignment`
- AC3 -> `TestMonthEnd`
- AC4 -> `TestMergePeriodDuplicates`
- AC5 -> `TestRefresh`
- AC6 -> `tests/test_ticker_valuation.py::TestValuation::test_balance_sheet_from_latest_quarter_that_has_one`
- AC7 -> `tests/test_quality.py::TestIncomeOnlyNewestQuarter`
- AC2 wiring -> `tests/test_fundamentals.py::TestProcessTickerAlignment`

## Decision log
- 2026-10-14: Align and key on month-end rather than exact dates, because Yahoo's convention
  changed over time and differs between statements.
- 2026-10-14: Newest row wins in a merge, rather than combining fields, so one row never mixes
  two fetches with different definitions (AAPL FY2025 debt: 112.4B vs 98.7B).
- 2026-10-14: Dry run on a copy of production, with a full yfinance refresh: 12 duplicates
  merged, none left; BAC and GS quarters realigned; 41 of 519 tickers' latest share counts fell
  by more than 3% (GS 935M to 295M, WFC 5.48B to 3.03B, C 3.07B to 1.68B, JPM 4.11B to 2.66B),
  none rose. That run showed the newest BAC/GS quarter now has no balance sheet, which led to
  AC6.
- 2026-10-14 (Reviewer pass): no blockers. Important: the realigned income-only newest quarter
  would have penalised BAC/GS in the quality factor (AC7 added), and alignment wasn't tested
  through ingestion (test added). Also: the newest column wins when two share a month-end, and
  merge ties prefer the month-end row. Limitations noted under out of scope.
