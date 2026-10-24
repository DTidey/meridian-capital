# Test Plan: nav-from-broker

Path: `docs/test-plans/50-nav-from-broker.md`

## What changed
- `execution/book_sync.py`: records Alpaca equity in `portfolio_nav` (same transaction);
  summary carries `equity`.
- `reporting/nav_series.py`: `build_nav_series(engine)` fills `spy_close` / `drawdown_pct` only.
- `risk/circuit_breakers.py`: `_compute_pnl` from `portfolio_nav` (`_nav_on`, `_nav_latest`);
  history-based NAV helpers removed.
- `portfolio/state.py`: `get_nav(config, conn, score_date)`, `with_nav`.
- `run_portfolio.py`, `run_risk_check.py`: size and check with the recorded NAV;
  `run_reporting.py`: new `build_nav_series` call.
- `reporting/turnover.py`: turnover from filled `execution_orders`.
- Tests: `test_reporting_nav.py` and the circuit-breaker trigger tests rewritten on recorded
  NAVs; new `TestNavFromBroker`, `TestSyncNav`, `TestRecordedNav`, `test_reporting_turnover.py`.

## Acceptance criteria coverage
- AC1: `tests/test_book_sync.py::TestSyncNav::test_equity_recorded_as_nav`,
  `test_flat_book_still_records_nav`, `test_resync_replaces_nav`, `test_dry_run_records_no_nav`,
  `test_failed_commit_records_no_nav`, `test_resync_keeps_reporting_columns`
- AC2: `tests/test_reporting_nav.py::test_recorded_nav_never_changed`,
  `test_drawdown_calculation`, `test_spy_close_filled`, `test_no_rows_added`
- AC3: `tests/test_risk_circuit_breakers.py::TestNavFromBroker::test_losing_day_moves_drawdown`,
  `test_returns_on_previous_nav_not_config`, `test_weekly_against_nav_seven_days_back`,
  `test_weekly_falls_back_to_daily`, `test_first_recorded_day_is_flat`,
  `test_no_nav_today_warns_and_does_not_trigger`, `test_weekly_uses_nav_exactly_seven_days_back`,
  `test_wiped_out_account_trips_kill_switch`; trigger tests rewritten on `portfolio_nav`
  (`test_size_down_daily_loss`, `test_close_all_large_daily_loss`, `test_drawdown_kill_switch`,
  `test_whatif_no_db_changes`)
- AC4: `tests/test_state.py::TestRecordedNav::test_latest_recorded_nav`,
  `test_on_or_before_score_date`, `test_falls_back_to_config`, `test_non_positive_nav_ignored`,
  `test_with_nav_copies_config`; runner wiring by review and the production read-only check
- AC5: `tests/test_reporting_turnover.py::test_turnover_from_fills`,
  `test_partial_fill_on_cancelled_order_counts`, `test_unfilled_orders_do_not_count`,
  `test_price_drift_is_not_turnover`

## Edge cases
- From spec: first recorded day (`test_first_recorded_day_is_flat`); missing night
  (`test_no_nav_today_warns_and_does_not_trigger`); zero equity (`test_non_positive_nav_ignored`);
  partial fills on cancelled orders (`test_partial_fill_on_cancelled_order_counts`).
- Additional adversarial cases:
  - A fund half the configured size: returns use the previous NAV, so a 2% loss is 2%
    (`test_returns_on_previous_nav_not_config`)
  - Yesterday's loss is not replayed when today's NAV is missing
    (`test_no_nav_today_warns_and_does_not_trigger`)
  - A rerun for an old date ignores later peaks (`test_later_navs_ignored`)
  - History P&L no longer changes the recorded NAV (`test_recorded_nav_never_changed`)
  - Price drift between snapshots is not turnover (`test_price_drift_is_not_turnover`)
  - NAV is not written when the book-sync commit fails after the NAV insert
    (`test_failed_commit_records_no_nav`)
  - A zero recorded NAV trips the kill switch instead of being treated as missing
    (`test_wiped_out_account_trips_kill_switch`)
  - A manual re-sync keeps reporting's drawdown and SPY close
    (`test_resync_keeps_reporting_columns`)

## Notes
- Against `main`, 11 of the circuit-breaker and turnover tests fail.
- Read-only production check on 2026-10-24: `get_nav` returns 100,000 (the 2026-05-21 row);
  `_compute_pnl` for 2026-10-23 warns (no NAV recorded yet) and returns zeros; the first NAV row
  is written by the 2026-10-26 nightly sync.

## Commands
```bash
make lint
make test
make security
```
