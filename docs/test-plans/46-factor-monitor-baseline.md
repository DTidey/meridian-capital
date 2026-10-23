# Test Plan: factor-monitor-baseline

Path: `docs/test-plans/46-factor-monitor-baseline.md`

## What changed
- `risk/factor_monitor.py`: skips a book without both a LONG and a SHORT position; the baseline
  is the book's own past spreads from `portfolio_history` (`_load_history_spreads`), replacing the
  universe top-half/bottom-half spread.
- `docs/specs/05-risk-management.md` section 8: algorithm steps 1-3 corrected.
- `tests/test_risk_factor_monitor.py`: new (no factor monitor tests existed).

All tests use an in-memory SQLite DB with `initialise_schema`, as
`tests/test_risk_circuit_breakers.py` does. The fixture book is L1 LONG / S1 SHORT; history days
set L1 and S1 momentum so the spread equals a chosen value, and all other factors are 50.

## Acceptance criteria coverage
- AC1: `TestNoPositions::test_returns_no_alerts_and_logs_ok[empty|long-only|short-only]`,
  `test_does_not_load_history`, `test_whatif_writes_nothing`, `test_empty_frame_without_columns`
- AC2: `TestBaseline::test_spreads_use_same_date_scores`, `test_date_without_scores_is_skipped`,
  `test_one_sided_snapshot_is_skipped`, `test_two_commits_use_latest_only`,
  `test_ticker_closed_between_commits_does_not_leak`, `test_today_snapshot_is_excluded`,
  `test_capped_at_252_most_recent_dates`
- AC3: `TestInsufficientHistory::test_nine_dates_skip`, `test_ten_dates_score`
- AC4: `TestZScore::test_spread_at_mean_gives_zero_and_constant_factors_skipped`,
  `test_two_std_above_mean_alerts`, `test_within_threshold_no_alert`
- AC5: `TestPriority::test_crowded_factor_is_high` (also checks the `risk_log` WARNING row),
  `test_unflagged_pair_is_medium`
- AC6: `TestEmptyBookRegression::test_empty_positions_with_long_score_history[True|False]`
- AC7: review of the `docs/specs/05-risk-management.md` diff
- AC8: `make lint`, `make test`, `make security`

## Edge cases
- From spec: one-sided book (AC1 parametrised cases); unscored ticker in a past snapshot
  (`TestBaseline::test_unscored_ticker_counts_as_neutral`); today's own snapshot
  (`test_today_snapshot_is_excluded`); constant history (`TestZScore` first test).
- Additional adversarial cases:
  - A null `weight` on a past snapshot row contributes nothing and does not crash
    (`TestBaseline::test_null_weight_row_is_ignored`)
  - An all-null weight column (object dtype from the DB) does not crash; both sides default to
    50.0 (`test_all_null_weights_default_to_neutral`)
  - An earlier same-day commit with the opposite direction does not leak into the book
    (`test_two_commits_use_latest_only`)
  - A ticker in the first commit but closed by the second is excluded
    (`test_ticker_closed_between_commits_does_not_leak`)
  - Old dates with an extreme spread beyond the 252 cap do not move the baseline
    (`test_capped_at_252_most_recent_dates`)
  - A bare `pd.DataFrame()` with no columns is treated as no positions, with today's scores
    present so the no-positions guard is the one exercised
    (`TestNoPositions::test_empty_frame_without_columns`)
  - No `portfolio_history` at all gives empty lists, not an error
    (`TestBaseline::test_no_history_returns_empty_lists`)

## Notes
- Checked against `main`: the AC6 regression test fails there with 8 MEDIUM alerts
  (momentum z = -176.7), the same pattern as the nightly logs.
- Its universe drifts a little day to day on purpose: a constant universe has zero baseline std,
  which the old code skipped, so the test would have passed on `main`.

## Commands
```bash
make lint
make test
make security
```
