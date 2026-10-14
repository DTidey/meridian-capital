# Test Plan: acceleration-trend

Path: `docs/test-plans/26-acceleration-trend.md`

## What changed
- `data/fundamentals.py`: `_upsert_period` merges raw figures over blanks; ratios kept only for a
  fully blank fetch.
- `factors/growth.py`: `grw_rev_accel` is the slope of up to four YoY growth points (at least 2).
- `docs/specs/02-factor-scoring.md`: acceleration definition marked as superseded.

## Acceptance criteria coverage
- AC1: `tests/test_fundamentals.py::TestUpsertKeepsHistory` (6 tests: fully blank fetch, partial
  fetch without stale ratios, NaN as blank, restated value overwrites, blank then filled)
- AC2: `tests/test_growth.py::TestAccelerationTrend::test_accelerating_is_positive`,
  `test_decelerating_is_negative`, `test_seasonality_ignored`
- AC3: `test_two_points_minimum`, `test_one_point_is_nan`, `test_uses_at_most_four_points`;
  updated `TestAcceleration::test_insufficient_history_returns_nan` (n=5 is one point)
- AC4: `test_non_contiguous_points_use_positions`, updated `TestDateBased::test_acceleration_by_date`
  and `TestDateBasedMore::test_acceleration_without_two_years_of_history`

## Edge cases
- From spec: restatement overwrites, zero prior skipped (`test_zero_prior_point_skipped`),
  non-contiguous points.
- Additional adversarial cases:
  - A wild quarter beyond the latest four points ignored: `test_uses_at_most_four_points`
  - Stale ratio after a partial fetch: `test_partial_fetch_merges_figures_but_not_stale_ratios`
  - Seasonal pattern with steady growth gives zero: `test_seasonality_ignored`

## Notes
- PostgreSQL behaviour checked in a rolled-back transaction on production (made-up ticker): a
  fully blank fetch keeps figures and ratios; a partial fetch keeps revenue, takes the new net
  income and blanks the stale margin; no rows left after rollback.
- Live (production data, scoring loader): acceleration available for 203 of 503 tickers (was 0);
  median about 0, NVDA +0.16 per quarter, WMT -0.014, MSFT -0.006.

## Commands
```bash
make lint
make test
```
