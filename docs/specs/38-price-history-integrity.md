# Price History Integrity

**Spec file:** `docs/specs/38-price-history-integrity.md`
**Spec slug:** price-history-integrity
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
The 2026-10-21 data audit found that `daily_prices` is wrong in ways that change which stocks the
fund trades. `update_prices` fetches each ticker only from its last stored date, so anything
yfinance changes in older rows is never picked up:

- Splits after the initial load were never applied to older rows. APH's 2:1 split on 2026-08-31
  shows as a 50% one-day loss. APH, CVNA, KLAC and CRWD are scored SHORT on these artefacts
  (APH composite 2.7, 8th lowest in the universe). Affected: CVNA, FDX, KLAC, DD, CRWD, HON,
  SPGI, MNST, APH.
- `adj_close` is dividend-adjusted only up to the load date. Each later dividend appears as a
  fake drop. Momentum differs from correct data by a median 2.7 points, and by more than 10
  points for 33 tickers.
- 4,194 all-null rows are stored (460 tickers on 2026-09-22 alone) and never repaired. They shift
  momentum lookbacks and cut the universe-wide beta window from 60 to 12 days.
- yfinance treats `end` as exclusive and the code passes `end=today`. So the 22:00 UTC run never
  stores that day's close, and scores dated D use prices up to D-1, with VIX and the regime also a
  day behind.
- `volume` is a 32-bit column. The largest stored value is 72% of its limit, and a full
  re-download could overflow it and fail the whole batch insert.

Goal: stored prices match the provider's current, fully adjusted history every night, include the
day's close, contain no empty rows, and scoring refuses to run on stale prices.

## Scope
In scope:
- `data/market_data.py`:
  - Drop all-null rows before storing.
  - Include today's bar once the US session has closed.
  - Re-download a ticker's full history when its stored history no longer matches the provider
    (adjustment change or gap).
  - A full-refresh mode for every ticker.
- `data/db.py`: `daily_prices.volume` becomes 64-bit, with an idempotent migration for PostgreSQL.
- `run_data.py`: `--refresh-prices` flag (full re-download of every price ticker).
- `run_scoring.py`: freshness gate. Scoring stops with an error when SPY has no bar dated
  `score_date`.

Out of scope / non-goals:
- The Polygon backend's adjusted-close handling (not in use; added to `docs/backlog.md`).
- Beta and covariance missing-data handling in `portfolio/` (a later packet). This packet only
  removes the null rows that trigger it.
- Regime and tail-risk handling of a missing VIX (a later packet).
- Rescoring historical `factor_scores` rows.
- Running the production backfill. That is a separate, confirmed step after merge.

## Assumptions
- yfinance `Close` (with `auto_adjust=False`) is split-adjusted, and `Adj Close` is split- and
  dividend-adjusted, both back-adjusted over the whole history. So after a split or ex-dividend
  date, the provider's values for the last stored date change.
- The US session is complete for daily-bar purposes from 16:30 America/New_York. The cron runs at
  22:00 UTC, which is 17:00 or 18:00 New York time, so it always includes the day's bar.
- yfinance returns no bar for a market holiday. On a holiday, SPY therefore has no bar for
  `score_date` and the freshness gate stops scoring. That is the intended outcome: no new market
  data means no new scores.
- A full re-download replaces all of the ticker's stored rows. Rows older than the lookback
  (`market_data.lookback_years`) are not reloaded and so are removed. Nothing reads beyond 800
  days (`factors/loader.py`), which is inside the 3-year lookback.
- About 2% of tickers go ex-dividend on a given day, so roughly 10 full re-downloads a night are
  expected.

## Proposed behavior / API
### Public interface
- `update_prices(conn, tickers, config, providers, refresh: bool = False) -> dict[str, int]`.
  `refresh=True` re-downloads every ticker's full history.
- `run_data.py --refresh-prices`: passes `refresh=True`.
- `run_scoring.py`: freshness gate before loading data.

### Inputs / outputs
- **End date.** Download `end` = today + 1 day when New York time is 16:30 or later on that date.
  Otherwise `end` = today, which excludes the in-progress session.
- **Empty rows.** A row with no open, high, low or close is never stored, from any backend.
- **Adjustment check.** Each incremental download already starts at the ticker's last stored
  date. If the provider's `close` or `adj_close` for that date differs from the stored value by
  more than 0.01% (relative), the ticker gets a full re-download.
- **Gap check.** Before downloading, the trading calendar is the set of dates on which at least
  half of all stored tickers have a non-null close. A ticker with fewer stored rows than calendar
  dates between its own first and last stored dates gets a full re-download.
- **Full re-download.**
  - Fetch from the lookback cutoff to `end`.
  - In one transaction, delete all of the ticker's rows and insert the fetched ones.
  - If the fetch returns no usable rows, keep the existing rows and log a WARNING.
- **Volume.** `daily_prices.volume` is `BigInteger`. `initialise_schema` alters the PostgreSQL
  column to `BIGINT` only when it is currently `integer`.
- **Freshness gate.** `run_scoring.py` looks up the latest SPY row with a non-null close. If it is
  not dated `score_date`, scoring logs an ERROR naming both dates and exits with a non-zero
  status. `run_all.py` then stops, as it does for any layer failure.
- **Logging.** INFO counts of re-downloads by reason (adjustment, gap, refresh) and of empty rows
  dropped. A re-download log line names the ticker and reason.

### Examples
```text
$ python run_data.py --refresh-prices          # one-off: rebuild every ticker's history
INFO  Prices: 537 full re-downloads (refresh), 0 empty rows dropped
$ python run_data.py --no-filings --no-13f     # nightly
INFO  Prices: 9 full re-downloads (8 adjustment, 1 gap), 3 empty rows dropped
$ python run_scoring.py                        # on 2026-11-26 (Thanksgiving)
ERROR Latest SPY bar is 2026-11-25, not the score date 2026-11-26: scoring stopped
```

## Acceptance criteria
- AC1: `_upsert_prices` stores no row whose open, high, low and close are all missing. It returns
  the number of rows stored.
- AC2: The download end date is today + 1 day at or after 16:30 New York time, and today before
  16:30.
- AC3: When the provider's close or adj_close for the last stored date differs from the stored
  value by more than 0.01%, `update_prices` re-downloads that ticker's full history. Its old rows
  (including rows absent from the new download) are replaced.
- AC4: A difference of 0.01% or less does not trigger a re-download.
- AC5: A ticker missing a calendar date between its first and last stored dates gets a full
  re-download. A ticker with complete history does not.
- AC6: If a full re-download returns no usable rows, the ticker's existing rows are unchanged.
- AC7: `update_prices(..., refresh=True)` and `run_data.py --refresh-prices` re-download every
  ticker's full history.
- AC8: `daily_prices.volume` stores values above 2^31 - 1. The PostgreSQL migration is a no-op
  when the column is already `bigint`.
- AC9: `run_scoring.py` exits non-zero without writing scores when SPY has no non-null bar dated
  `score_date`, and runs normally when it does.
- AC10: `make lint`, `make test` and `make security` pass.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new. The migration DDL uses fixed identifiers with no
  user input.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact:
  - A few more yfinance downloads each night (full re-downloads for about 10 tickers).
  - A one-off full download for `--refresh-prices`, which is 6 batch calls for 537 tickers.
- Dependency or supply-chain impact: none (`zoneinfo` is standard library).
- Security notes for reviewers/testers:
  - A full re-download deletes before inserting. The delete and insert must share one
    transaction, and an empty fetch must not delete.

## Edge cases
- **Newly listed ticker:** no stored rows. It gets a full download from the cutoff, as today.
- **Ticker delisted or halted for days:** the gap check re-downloads it each night. That costs one
  extra download and is logged.
- **Provider returns no row for the last stored date:** no comparison is possible, so there is no
  adjustment re-download (the gap check still applies).
- **Last stored close is null:** no such row exists after this packet. The comparison skips
  missing values.
- **Daytime manual run before 16:30 New York:** no partial bar is stored. Scoring for today stops
  at the freshness gate. Run with `--date <previous session>` to score the last close.
- **^VIX batched with stocks:** holiday rows with no values are dropped by AC1.
- **Old (pre-packet) null rows:** removed by `--refresh-prices`. Afterwards the gap check repairs
  any new hole.

## Test guidance
- AC1 -> `tests/test_market_data.py::TestUpsertPrices::test_all_null_row_not_stored`.
  This replaces `test_nan_ohlcv_stored_as_null`, which asserted the old behaviour.
- AC2 -> `tests/test_market_data.py::TestDownloadEnd`: before and after 16:30, and the DST
  boundary.
- AC3, AC4 -> `tests/test_market_data.py::TestAdjustmentCheck`: split ratio, small dividend
  (0.08%), unchanged, stale row removed.
- AC5 -> `tests/test_market_data.py::TestGapCheck`.
- AC6 -> `tests/test_market_data.py::TestFullRedownload::test_empty_fetch_keeps_rows`.
- AC7 -> `tests/test_market_data.py::TestFullRedownload::test_refresh_redownloads_all`;
  `tests/test_run_data_args.py` (flag parsing).
- AC8 -> `tests/test_market_data.py::TestUpsertPrices::test_volume_above_int32`.
- AC9 -> `tests/test_scoring_freshness.py`.
- AC10 -> `make lint`; `make test`; `make security`.

## Decision log
- 2026-10-21: Detect adjustment changes from the overlapping last stored row instead of
  re-downloading everything nightly. One overlap row catches every split and dividend at the cost
  of about 10 re-downloads a night, and avoids rewriting 460k rows on the remote database each run.
- 2026-10-21: The tolerance is 0.01%, not 0.1%. AAPL's quarterly dividend moves adj_close by about
  0.08%, which 0.1% would miss.
- 2026-10-21: The calendar is the dates shared by at least half the tickers, not SPY's dates. A
  gap in SPY itself would otherwise go undetected.
- 2026-10-21: The freshness gate stops scoring instead of warning. Scores labelled with a date
  they do not reflect are the bug being fixed, and stopping also prevents holiday runs from
  writing scores.
