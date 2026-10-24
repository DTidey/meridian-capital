# Test Plan: book-targets

Path: `docs/test-plans/51-book-targets.md`

## What changed
- `config.yaml`: `target_long_gross` 0.80, `target_short_gross` 0.70.
- New `portfolio/book_targets.py`; `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py`
  take their targets from it.
- Tests: new `tests/test_book_targets.py`; `tests/test_optimizer.py` and
  `tests/test_mvo_optimizer.py` configs and sums moved to 0.80 / 0.70.

## Acceptance criteria coverage
- AC1: `tests/test_book_targets.py::test_production_config_consistent`
- AC2: `tests/test_book_targets.py::TestBounds::test_gross_must_match_gross_exposure`,
  `test_net_above_portfolio_max`, `test_net_below_portfolio_min`, `test_net_outside_pre_trade`,
  `test_gross_above_pre_trade_max`, `test_negative_target`
- AC3: `tests/test_book_targets.py::test_optimisers_use_book_targets`,
  `test_mvo_fallback_does_not_hide_bad_targets`
- AC4: `tests/test_optimizer.py::TestOptimizerWeights::test_long_weights_sum_to_target`,
  `test_short_weights_sum_to_target`; `tests/test_mvo_optimizer.py` long/short sums

## Edge cases
- From spec: net exactly on a bound (`test_net_on_the_bound_allowed`); bounds absent
  (`test_absent_bounds_not_checked`); defaults checked like set values
  (`test_defaults_are_checked_too`).
- Additional adversarial cases:
  - The pre-packet config (0.90 / 0.60) fails against both the portfolio and pre-trade net
    maxima (`test_net_above_portfolio_max`, `test_net_outside_pre_trade`)
  - MVO's catch-all fallback does not swallow an inconsistent config
    (`test_mvo_fallback_does_not_hide_bad_targets`)
  - Partial configs check only signs, so scripts and tests keep working
    (`test_absent_bounds_not_checked`)

## Notes
- Production dry run (`run_portfolio.py --whatif --date 2026-10-23`): 20 long / 20 short,
  gross 150.0%, net 10.0%, net beta -0.326. The beta breach is H3 (next packet).

## Commands
```bash
make lint
make test
make security
```
