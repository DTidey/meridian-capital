# Test Plan: snapshot-reliability

Path: `docs/test-plans/40-snapshot-reliability.md`

## What changed
- New `data/yahoo_info.py`: `call_with_retry` (3 attempts, waits 5 s then 15 s), `YahooInfo`
  (memoised `.info` per run, failures not memoised), `fetch_in_two_passes` (60 s pause, one more
  pass over failed tickers), `log_coverage` (INFO `N/M tickers fetched`, ERROR below 95%).
- `data/short_interest.py`, `data/estimates.py`: take `info: YahooInfo | None`; parse fields
  from the shared `.info`; second pass and coverage log.
- `data/earnings_calendar.py`: `_fetch_earnings_date` returns `None` on a failed fetch, `[]` on
  an empty success; retry, second pass, coverage log; superseded future dates deleted after a
  successful fetch that stored dates.
- `run_data.py`: one `YahooInfo` passed to steps 4 and 5.
- `factors/loader.py`: `_EST_LOOKBACK_DAYS = 105`. `factors/revisions.py`: prior snapshot must be
  on or before the cutoff and no more than 7 days before it.
- Tests: new `tests/test_yahoo_info.py`, `tests/test_snapshot_coverage.py`, helper
  `tests/yahoo_fake.py` (scripted in-memory `yf.Ticker` with call counts); additions to
  `tests/test_earnings_calendar.py`, `tests/test_revisions.py`, `tests/test_loader.py`,
  `tests/test_run_data_args.py`. yfinance is faked, sleeps are recorded or patched to zero,
  databases are throw-away SQLite; no network or production database.

## Acceptance criteria coverage
- AC1: `tests/test_yahoo_info.py::TestRetry::test_raise_then_succeed_returns_data`,
  `test_waits_increase` (sleeps `[5, 15]`), `test_three_failures_count_as_failed` (3 requests,
  `None`), `test_rate_limit_error_retried` (`YFRateLimitError`)
- AC2: `tests/test_yahoo_info.py::TestSharedInfo::test_repeat_get_makes_no_request`,
  `test_short_interest_then_estimates_one_request_each`, `test_estimates_log_shared_count`,
  `test_standalone_callers_get_own_info`;
  `tests/test_run_data_args.py::test_one_info_fetch_per_ticker`
- AC3: `tests/test_snapshot_coverage.py::TestSecondPass::test_info_step_second_pass_success_stored`
  (short interest, estimates), `test_calendar_second_pass_success_stored`
- AC4: `tests/test_earnings_calendar.py::TestFetchResult::test_failed_request_returns_none`,
  `test_empty_success_returns_empty_list`, `test_dates_returned`,
  `test_empty_counts_as_fetched_no_second_pass`, `test_none_counts_against_coverage_and_retries`
- AC5: `tests/test_earnings_calendar.py::TestSupersededDates::test_success_with_dates_deletes_other_future_rows`
  (past row and other ticker kept), `test_failed_or_empty_fetch_keeps_rows` (`None`, `[]`),
  `test_only_out_of_window_dates_keeps_rows`
- AC6: `tests/test_snapshot_coverage.py::TestCoverageError::test_below_95_logs_error_and_continues`
  (all three steps), `test_exactly_95_no_error`, `test_all_fail_logs_error`;
  `tests/test_run_data_args.py::test_low_coverage_run_continues`
- AC7: `tests/test_revisions.py::TestFridayWindow::test_rev_90d_computed_on_friday` (weekday
  snapshots loaded through `factors.loader._load_estimates`);
  `tests/test_loader.py::TestLoadEstimates::test_lookback_at_least_105_days`
- AC8: `tests/test_revisions.py::TestPriorTolerance::test_prior_more_than_7_days_before_cutoff_not_used`
- AC9: `make lint`, `make test`, `make security`

## Edge cases
- From spec:
  - Ticker already snapshotted today is skipped and not attempted
    (`test_skipped_today_not_attempted`; existing `TestTodaySkip`).
  - `.info` without the step's fields is a success with no row
    (`test_missing_fields_not_retried`).
  - Calendar date outside the lookahead only: not stored, no deletion
    (`test_only_out_of_window_dates_keeps_rows`).
  - Scoped run: coverage over tickers attempted (`test_skipped_today_not_attempted`,
    `test_below_95_logs_error_and_continues` with two tickers).
  - No snapshot within the 7-day tolerance: NaN, scored 50
    (`test_prior_more_than_7_days_before_cutoff_not_used`).
  - Thanksgiving week: Friday 2027-02-26, cutoff Saturday 2026-11-28, no Thursday or Friday
    snapshot; Wednesday's is used (`test_thanksgiving_friday_cutoff_uses_wednesday`).
- Additional adversarial cases:
  - An empty `.info` is a success, not retried (`test_empty_info_is_success`).
  - A failed `.info` is not memoised; a later step can recover it (`test_failure_not_cached`).
  - No failures: no 60 s pause and no extra requests (`test_no_pause_without_failures`).
  - A ticker failing both passes is requested exactly 6 times (`test_fail_both_passes`).
  - Coverage of exactly 95% (19/20) logs no ERROR (`test_exactly_95_no_error`); nothing to
    attempt logs no ERROR (`test_nothing_attempted_no_error`).
  - A stored future date beyond the lookahead window is superseded too
    (`test_beyond_window_future_row_superseded`).
  - Today's date counts as future and is kept when re-confirmed alongside a second date
    (`test_two_dates_kept_when_both_returned`).
  - Prior snapshot exactly 7 days before the cutoff is used
    (`test_prior_exactly_7_days_before_cutoff_used`); a gap only blanks the window it affects
    (`test_other_windows_unaffected`).

## Security
- Superseded delete scoped to one ticker and to `earnings_date >= today`: covered by
  `test_success_with_dates_deletes_other_future_rows` (past row and MSFT rows unchanged) and
  `test_failed_or_empty_fetch_keeps_rows`. The delete uses SQLAlchemy bound parameters.
- Request volume: one `.info` per ticker per run (AC2 tests); retries only after failures, with
  backoff (AC1 tests). Residual: actual Yahoo throttling behaviour is only observable in
  production.

## Notes
- Before the code change, run against a copy of the branch head: `test_yahoo_info.py` and
  `test_snapshot_coverage.py` failed to import (`data.yahoo_info` missing); calendar AC4/AC5 tests
  errored for the same reason; `TestFridayWindow` (both), `TestPriorTolerance` (more than 7
  days) and `test_lookback_at_least_105_days` failed.
- Suite: 1177 tests before, 1219 after (42 new).

## Commands
```bash
make lint
make test
make security
```
