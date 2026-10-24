# Test Plan: betas

Path: `docs/test-plans/52-betas.md`

## What changed
- `portfolio/beta.py`: `compute_betas` pairwise against SPY on the shared calendar, at least 20
  shared returns, Blume-adjusted (`_raw_beta`, `_blume`).
- `portfolio/optimizer.py`: `_adjust_beta` reweights within books (`_tilt_books`,
  `_bounded_scale`, `_net_beta`), k by bisection; new call signature.
- `portfolio/mvo_optimizer.py`: covariance keeps days with any data.
- Tests: `tests/test_beta.py` (`TestPairwise`, `TestShrinkage`; FLAT now 0.33),
  `tests/test_optimizer.py::TestAdjustBeta`,
  `tests/test_mvo_optimizer.py::test_cov_keeps_days_with_gaps`.

## Acceptance criteria coverage
- AC1: `tests/test_beta.py::TestPairwise::test_beta_same_alone_or_with_gappy_peer`,
  `test_lookback_counts_spy_days`, `test_peer_with_off_calendar_date`
- AC2: `tests/test_beta.py::TestShrinkage::test_blume_adjustment`, `test_extreme_beta_pulled_in`,
  `test_negative_beta_shrunk_towards_one`, `test_fewer_than_twenty_shared_returns_defaults`;
  `TestComputeBetas::test_spy_beta_is_one`, `test_missing_ticker_defaults_to_one`,
  `test_insufficient_history_defaults_to_one`
- AC3: `tests/test_optimizer.py::TestAdjustBeta::test_net_beta_brought_within_limit`,
  `test_gross_and_bounds_kept`, `test_positive_excess_tilts_the_other_way`,
  `test_production_bounds_twenty_names`, `test_book_too_small_for_gross_is_skipped`,
  `test_single_short_cannot_flip_net_beta`
- AC4: `TestAdjustBeta::test_within_limit_unchanged`, `test_unreachable_warns`,
  `test_old_scaling_no_op_is_gone`
- AC5: `tests/test_mvo_optimizer.py::test_cov_keeps_days_with_gaps`

## Edge cases
- From spec: equal betas within each book (`test_unreachable_warns`); missing ticker
  (`test_missing_ticker_defaults_to_one`); negative raw beta
  (`test_negative_beta_shrunk_towards_one`).
- Additional adversarial cases:
  - A peer trading every third day does not change another ticker's beta
    (`test_beta_same_alone_or_with_gappy_peer`)
  - The old scale-then-renormalise no-op is gone (`test_old_scaling_no_op_is_gone`)
  - Gross targets, position bounds and the set of names survive the tilt
    (`test_gross_and_bounds_kept`)
  - A 4.0 raw beta is pulled in below 3.5 (`test_extreme_beta_pulled_in`)
  - The beta window counts SPY returns, not other tickers' rows (`test_lookback_counts_spy_days`)
  - A peer's off-calendar row does not change another ticker's beta
    (`test_peer_with_off_calendar_date`; fails with the SPY-calendar step removed)
  - A short book too small to hold its gross is not shrunk by the tilt
    (`test_book_too_small_for_gross_is_skipped`)
  - One capped short cannot push net beta past the limit on the other side
    (`test_single_short_cannot_flip_net_beta`)
  - Production bounds (5% cap, 20 names a book) (`test_production_bounds_twenty_names`)
  - MVO covariance with a ticker missing every 5th day exercises the per-ticker fill
    (`test_cov_keeps_days_with_gaps`)

## Notes
- Against `main`, 12 of these tests fail.
- Production dry run (`run_portfolio.py --whatif --date 2026-10-23`): gross 150.0%, net 10.0%,
  net beta -0.150 (was -0.326).
- Read-only pre-trade simulation of that whole target book from a flat book: 24 pass, 16
  `BLACKOUT_REDUCED`, 0 rejected (audit H4's Verify).

## Commands
```bash
make lint
make test
make security
```
