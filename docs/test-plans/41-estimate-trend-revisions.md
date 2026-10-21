# Test Plan: estimate-trend-revisions

Path: `docs/test-plans/41-estimate-trend-revisions.md`

## What changed
- `data/yahoo_info.py`: `YahooTrend` caches each ticker's `earningsTrend` for the run. Only
  successes are cached. `eps_trend` and `earnings_estimate` are read from one `yf.Ticker`
  inside one retried call. The result is `{period: {current, days_7, days_30, days_60, days_90,
  avg}}`, with missing values as `None` and `{}` without coverage. New `log_low_coverage` holds
  the ERROR half of `log_coverage`.
- `data/db.py`: new `estimate_trends` table, primary key `(ticker, date, period)`.
- `data/estimates.py`: `update_estimates(..., trend=None)` upserts `0y` and `+1y` rows. A
  period without `current` is skipped. The trend pass covers every ticker, including those
  whose `analyst_estimates` row for today already exists, and has its own second pass and
  breaker (`Analyst estimate trends`). The INFO line gains `; trends T/M`, with an ERROR below
  95%.
- `data/earnings_calendar.py`: `.calendar` is gone.
  - `_fetch_earnings_date(ticker, info, trend)` returns `None` when `.info` failed.
  - Otherwise it returns `earningsTimestampStart`, plus a different `earningsTimestampEnd`, as
    UTC dates, with `eps_estimate` from the `0q` average (`None` when the trend is missing).
  - `update_earnings_calendar(..., info=None, trend=None)`.
- `run_data.py`: one `YahooInfo` and one `YahooTrend` per run, shared by steps 4-6.
- `factors/_utils.py`: `latest_trends` (each ticker's newest row, never mixed across dates) and
  `guarded_current`.
- `factors/loader.py`: `data["estimate_trends"]` holds `+1y` rows from `score_date - 5` days to
  `score_date`. `data["estimates"]` is still loaded.
- `factors/revisions.py`: `rev_Nd = (g - days_N) / max(|days_N|, 0.10)`, from the latest
  snapshot, with an INFO line counting guard replacements. The snapshot-history path, the
  30-day warm-up and packet 40's prior tolerance are removed.
- `factors/value.py`: `val_fwd_earn_yield = guarded_current / price`. It no longer reads
  `analyst_estimates`.
- `tests/yahoo_fake.py`: scripted `earningsTrend` responses (`trend=` / `default_trend=`). Like
  yfinance, one counted request per `Ticker` object serves both frames. Adds the `trend()`
  helper and `PGR_TREND`, built from PGR's live values.
- yfinance is faked everywhere, sleeps are recorded or zero, and databases are throw-away
  SQLite. No test uses the network or the production database.

## Acceptance criteria coverage
- AC1:
  - `tests/test_yahoo_info.py::TestSharedTrend`:
    - `test_one_request_serves_trend_and_quarter_estimate`: PGR's `+1y` values and `0q` avg
      4.11543 come from one request.
    - `test_repeat_get_makes_no_request`.
    - `test_raise_then_succeed_retried`: sleeps `[5]`.
    - `test_three_failures_none_and_not_cached`: `YFRateLimitError` x3, then a recovery on a
      later `get`.
    - `test_no_coverage_is_empty_success`.
  - `tests/test_estimates.py::TestTrendRows`:
    - `test_stores_0y_and_plus_1y`: both rows with all five values; `0q` and `+1q` are not
      stored.
    - `test_row_has_date_and_fetched_at`.
    - `test_period_without_current_skipped`.
    - `test_missing_window_stored_as_null`.
    - `test_coverage_line`: `...(1 from the shared .info); trends 1/2`, plus the trend ERROR.
    - `test_trend_second_pass`.
    - `test_shared_trend_not_refetched`.
  - `tests/test_snapshot_coverage.py::TestTrendCircuitBreaker::test_trend_outage_bounded`: 60
    trend requests for 50 tickers, and the WARNING.
- AC2: `tests/test_run_data_args.py::test_two_requests_per_ticker`.
  - Runs `run_data.main` with the real steps 4-6.
  - `.info` and `earningsTrend` are requested once per ticker; `.calendar` 0 times.
  - It stores 4 trend rows and 2 calendar rows with `eps_estimate` 4.11543.
- AC3: `tests/test_earnings_calendar.py::TestInfoDates`:
  - `test_start_date_with_0q_average`: same-day end, one row, no `.calendar`.
  - `test_different_end_date_also_stored`.
  - `test_missing_timestamps_success_no_dates`.
  - `test_failed_info_is_failed_fetch`: both passes, coverage ERROR, no trend request.
  - `test_trend_failure_stores_null_eps`.
  - `test_no_0q_row_stores_null_eps`.
  - `test_reuses_the_runs_info_and_trend`.
  - `test_standalone_fetches_its_own`.
  - `test_superseded_rule_still_applies`.
  - Also `::TestSupersededDates` (kept, now patched on the new signature), `::TestFetchResult`
    (rewritten for `.info`), `::TestTodaySkip`, and
    `tests/test_snapshot_coverage.py::TestSecondPass::test_calendar_second_pass_success_stored`,
    `TestCoverageError`, `TestCircuitBreaker` (calendar now counted on `.info`).
- AC4: `tests/test_revisions.py::TestTrendRevisions`:
  - `test_percent_change_formula`.
  - `test_percent_not_dollars` (audit M2).
  - `test_denominator_floor`.
  - `test_negative_estimate_uses_absolute_denominator`.
  - `test_missing_window_only_blanks_that_sub_factor`.
  - `test_no_snapshot_scores_neutral`.
  - `test_empty_trends_all_neutral`.
  - `test_latest_snapshot_used`.
  - `test_stale_snapshot_not_loaded`: 6 days old is stale, exactly 5 days is fresh.
  - Loader: `tests/test_loader.py::TestLoadEstimateTrends::test_window_and_period`.
- AC5:
  - `tests/test_revisions.py::TestGlitchGuard`:
    - `test_pgr_glitch_uses_days_7`: current 1012, `days_7` 16.36848.
    - `test_large_move_replaced`, `test_normal_move_kept`, `test_days_7_missing_returns_current`.
    - `test_no_row_is_nan`.
    - `test_guard_count_logged`.
  - `tests/test_value.py::TestGlitchGuard::test_pgr_glitch_uses_days_7`: the yield is ranked
    from 16.37/price, not 1012/price.
- AC6: `tests/test_revisions.py::TestRollOver::test_forward_eps_step_ignored_trend_flat`.
  - The `forwardEps` snapshots step from 161.5 to 205.3 and the `+1y` trend is flat.
  - The raw revisions are 0, and MU ranks between an upgrade and a downgrade, not at 100.
- AC7: `tests/test_value.py::TestFwdYieldFromTrend`:
  - `test_yield_is_plus_1y_current_over_price`.
  - `test_no_trend_snapshot_neutral`.
  - `test_analyst_estimates_not_read`: the data dict has no `estimates` key.
  - Also `TestFwdEarningsYield`, `TestMissingData::test_missing_trend_yields_50_for_fwd_yield`.
- AC8: `tests/test_db.py`:
  - `test_all_tables_created` and `test_schema_is_idempotent` (`estimate_trends` in
    `EXPECTED_TABLES`).
  - `test_estimate_trends_added_to_existing_database`: a database built without the table
    gets it from `initialise_schema`, twice, with primary key `(ticker, date, period)`.
- AC9: `make lint`, `make test`, `make security`.
- AC10: `tests/test_estimates.py::TestTrendRowCoverage`: `test_empty_trends_raise_error`,
  `test_full_trends_no_error`, `test_0y_only_counts_as_missing` (adversarial),
  `test_one_in_twenty_missing_is_fine` (adversarial). Fakes in
  `test_run_data_args.py::test_low_coverage_run_continues` and two `TestCoverageError` tests
  now supply trend data, so they test only the `.info` failure they are about.

## Edge cases
- From spec:
  - No analyst coverage (no `+1y` row): nothing is stored and the ticker scores neutral
    (`test_no_coverage_is_empty_success`, `test_no_coverage_no_rows`,
    `test_no_snapshot_scores_neutral`, `test_no_trend_snapshot_neutral`).
  - `days_7` missing: the guard returns `current` (`test_days_7_missing_returns_current`).
  - `days_N` missing for one window: only that sub-factor is NaN
    (`test_missing_window_only_blanks_that_sub_factor`).
  - Negative estimates: -1.00 to -0.50 is +50%
    (`test_negative_estimate_uses_absolute_denominator`).
  - `.info` succeeds but `earningsTrend` fails: the date is stored with a NULL `eps_estimate`
    (`test_trend_failure_stores_null_eps`), and no trend row is written
    (`test_coverage_line`).
  - First night after deploy: Layer 1 writes the snapshot, and the loader reads a snapshot
    dated the score date (`test_window_and_period`, `test_two_requests_per_ticker`).
  - Daytime manual run: a same-day re-run upserts the trend rows, although `analyst_estimates`
    is skipped (`test_same_day_rerun_upserts_trend`).
- Additional adversarial cases:
  - A value Yahoo leaves out is `None`, not NaN, and the `currency` column is dropped
    (`test_missing_value_is_none`).
  - A failed `.info` does not stop the trend snapshot (`test_info_failure_still_stores_trend`).
  - Only the newest snapshot is used. An older day's `days_90` never fills a gap
    (`test_latest_snapshot_not_mixed_with_older`).
  - A `0y` row never feeds the `+1y` revisions (`test_only_plus_1y_period_used`).
  - Exactly a 50% move is kept, because the guard needs more than 50%
    (`test_exactly_half_kept`).
  - The $0.10 floor applies to the guard near zero (`test_floor_near_zero`).
  - A negative forward EPS ranks lowest and is not dropped (`test_negative_eps_ranks_lowest`).
  - `earningsTimestamp` (the previous report) is never stored
    (`test_previous_report_timestamp_ignored`).
  - Dates are UTC whatever the machine's time zone. The test uses 01:00 UTC with
    `TZ=America/New_York` (`test_timestamp_converted_in_utc`).
  - A malformed timestamp is a success with no date (`test_malformed_timestamp_no_date`).
  - An end timestamp without a start is not a date (`test_end_without_start_no_date`).

## Security
- New table writes use SQLAlchemy bound-parameter upserts (`insert_or_replace`). The Yahoo
  values are floats or `None`, and the period is one of the fixed `0y` / `+1y`.
- Request volume: one `.info` and one `earningsTrend` per ticker per run, and no `.calendar`
  (AC2 test). Retries and the breaker are covered by the AC1 and breaker tests.
- Residual risks:
  - Yahoo's real throttling of `quoteSummary` can only be seen in production.
  - yfinance turns a non-429 HTTP error on `earningsTrend` into an empty frame, which counts
    as no coverage rather than a failure.

## Notes
- Retired, because the behaviour they tested is removed: packet 40's
  `tests/test_revisions.py::TestFridayWindow` and `::TestPriorTolerance`, and the old
  `TestDegenerateMode` and `TestRevisionDeltas`. The 105-day lookback test in
  `tests/test_loader.py::TestLoadEstimates` stays, because the loader still loads
  `data["estimates"]`.
- Red before green: the tests were committed before the code (`95e34bf`). At that commit:
  - `test_revisions.py`, `test_yahoo_info.py`, `test_estimates.py`,
    `test_earnings_calendar.py` and `test_run_data_args.py` failed to import (`YahooTrend` and
    `estimate_trends` were missing).
  - 25 tests failed in `test_value.py`, `test_snapshot_coverage.py`, `test_loader.py` and
    `test_db.py`.
- Suite: 1225 tests before, 1278 after.

## Commands
```bash
make lint
make test
make security
```
