# Test Plan: price-history-integrity

Path: `docs/test-plans/38-price-history-integrity.md`

## What changed
- `data/market_data.py`: all-null rows dropped before storing; download end includes the day's
  bar from 16:30 New York; full re-download on adjustment change, gap or `refresh=True`, with
  delete and insert in one transaction; INFO counts of re-downloads by reason and empty rows
  dropped.
- `data/db.py`: `daily_prices.volume` is `BigInteger`; `initialise_schema` alters the PostgreSQL
  column to `BIGINT` only when it is `integer`.
- `run_data.py`: `--refresh-prices`.
- `run_scoring.py`: freshness gate before loading data.
- Tests: `tests/test_market_data.py` (`test_nan_ohlcv_stored_as_null` replaced, as the spec
  says), `tests/test_db.py`, new `tests/test_scoring_freshness.py` and
  `tests/test_run_data_args.py`. yfinance is replaced by an in-memory fake with yfinance's
  exclusive `end`; New York and UTC "now" are patched; no network or production database.

## Acceptance criteria coverage
- AC1: `TestUpsertPrices::test_all_null_row_not_stored`
- AC2: `TestDownloadEnd::test_before_close_is_today`, `test_at_close_threshold_is_tomorrow`,
  `test_after_close_is_tomorrow`, `test_dst_boundary`,
  `test_cron_time_includes_day_in_summer_and_winter`,
  `test_update_prices_stores_todays_bar_after_close`,
  `test_update_prices_skips_partial_bar_before_close`
- AC3: `TestAdjustmentCheck::test_split_triggers_full_redownload`,
  `test_small_dividend_triggers_full_redownload` (0.08%), `test_stale_row_removed`
- AC4: `TestAdjustmentCheck::test_unchanged_history_no_redownload`,
  `test_difference_within_tolerance_no_redownload` (exactly 0.01%)
- AC5: `TestGapCheck::test_missing_calendar_date_triggers_redownload`,
  `test_complete_history_no_redownload`, `test_gap_in_spy_detected`,
  `test_null_row_on_calendar_date_is_a_gap` (a stored empty row does not hide the gap)
- AC6: `TestFullRedownload::test_empty_fetch_keeps_rows`,
  `test_empty_fetch_after_adjustment_keeps_rows`, `test_all_null_fetch_keeps_rows`
- AC7: `TestFullRedownload::test_refresh_redownloads_all`;
  `tests/test_run_data_args.py::test_refresh_prices_flag`, `test_refresh_prices_off_by_default`
- AC8: `TestUpsertPrices::test_volume_above_int32`;
  `tests/test_db.py::test_volume_migration_alters_integer_column`,
  `test_volume_migration_noop_when_bigint`, `test_volume_migration_noop_on_sqlite`
- AC9: `tests/test_scoring_freshness.py::test_stale_spy_stops_scoring` (exit non-zero, both
  dates logged, no `factor_scores` or `regime_state` rows), `test_fresh_spy_runs`
- AC10: `make lint`, `make test`, `make security`

## Edge cases
- From spec:
  - Newly listed ticker downloads from the cutoff and is not counted as a re-download
    (`test_new_ticker_downloads_from_cutoff`).
  - Provider has no row for the last stored date: no adjustment re-download
    (`test_no_provider_row_for_last_date_no_redownload`).
  - Last stored close is null: comparison skips it and uses adj_close
    (`test_null_stored_close_compares_adj_close_only`).
  - Daytime run before 16:30: no partial bar (`test_update_prices_skips_partial_bar_before_close`).
  - Holiday rows with no values dropped and counted (`test_empty_rows_dropped_counted`).
  - Holiday: no SPY bar for the score date stops scoring (`test_stale_spy_stops_scoring`).
- Additional adversarial cases:
  - Insert failure after the delete rolls back, leaving the old rows
    (`test_insert_failure_rolls_back_delete`).
  - Re-adjusted history with an empty full fetch keeps the old rows and does not mix in the
    newly adjusted incremental bars (`test_empty_fetch_after_adjustment_keeps_rows`).
  - A full fetch of only all-null rows counts as empty (`test_all_null_fetch_keeps_rows`).
  - A row with only a close is kept; a row with only volume is dropped
    (`test_row_with_only_close_is_stored`, `test_volume_without_prices_not_stored`).
  - Calendar threshold: a stray date held by one of three tickers is not a calendar date; one
    held by exactly half is (`test_minority_date_is_not_a_calendar_date`,
    `test_date_held_by_exactly_half_is_a_calendar_date`); null-close rows do not count
    (`test_null_close_rows_do_not_make_a_calendar_date`).
  - A ticker whose history starts later than others is not a gap
    (`test_history_starting_late_is_not_a_gap`).
  - New York date used after UTC midnight (`test_new_york_date_used_after_utc_midnight`).
  - Freshness gate: no SPY rows, a null-close SPY row on the score date, or a fresh QQQ with
    stale SPY all stop scoring; a historical `--date` with later bars stored still runs
    (`test_no_spy_rows_stops_scoring`, `test_null_spy_close_on_score_date_stops_scoring`,
    `test_other_ticker_fresh_does_not_pass_gate`,
    `test_historical_score_date_with_later_bars_runs`).
  - Re-download log line names ticker and reason; summary counts by reason
    (`test_redownload_reason_logged`, `test_refresh_redownloads_all`).

## Security
- Delete-before-insert shares one transaction and an empty fetch never deletes: covered by
  `test_insert_failure_rolls_back_delete`, `test_empty_fetch_keeps_rows`,
  `test_all_null_fetch_keeps_rows`.
- Migration DDL is fixed text with no input; the PostgreSQL path is tested with a recording fake
  connection, not a real server (residual: first real run is on production).

## Notes
- Before the fix, the new `tests/test_market_data.py` failed to import (`_download_end`
  missing), four freshness tests and both flag tests failed.
- Suite: 1069 tests before, 1113 after.

## Commands
```bash
make lint
make test
make security
```
