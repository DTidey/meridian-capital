# Test Plan: held-prices-layer4

Path: `docs/test-plans/63-held-prices-layer4.md`
Spec: `docs/specs/63-held-prices-layer4.md`

## What changed
- `run_portfolio.py`: new `_price_tickers(candidates, current)`; `load_positions` moved before
  the price load; `_load_prices` gets candidates plus held tickers. Betas, schedule warnings,
  factor scores and the optimiser still get candidates only.
- `portfolio/rebalance.py`: new `_warn_missing_prices`, called from `generate_trades` after the
  rows are built.
- New `tests/test_held_prices_layer4.py`.

## Acceptance criteria coverage
- AC1: `tests/test_held_prices_layer4.py::TestRunWiring::test_held_non_candidate_priced`
  (`run()` on a SQLite DB with stored closes; VICI 24.00 SELL, CRL 150.00 COVER),
  `TestPriceTickers::test_held_non_candidate_added`
- AC2: `TestRunWiring::test_held_non_candidate_has_cost` (non-zero `estimated_cost_usd`; the
  printed trade-list line shows 24.00, not $0.00)
- AC3: `TestTurnover::test_held_closure_counts_in_trade_value_and_summary` (`needed_usd`,
  `kept_usd` = 107 x 24), `TestTurnover::test_held_long_counts_in_build_up_gap` (allowance is
  10000 - 7568, so the held long is on the current-gross side)
- AC4: `TestRunWiring::test_optimiser_universe_unchanged` (spies on `compute_betas`,
  `check_events`, `_load_factor_scores`, optimiser: all get `["AAA", "ZZZ"]`; held names have
  target 0)
- AC5: `TestPriceTickers::test_candidates_only_unchanged`,
  `TestRunWiring::test_no_held_non_candidates_same_tickers`; all existing
  `tests/test_rebalance.py` and `tests/test_run_portfolio.py` tests
- AC6: `TestMissingPriceWarning::test_warns_for_unpriced_ticker`,
  `test_no_warning_when_all_priced`, `test_nan_and_zero_close_warned`

## Edge cases
- From spec:
  - Held ticker that is also a candidate listed once
    (`TestPriceTickers::test_candidates_only_unchanged`)
  - Held ticker with no stored close: row still emitted as COVER at price 0, warned
    (`TestMissingPriceWarning::test_warns_for_unpriced_ticker`)
  - Empty book: candidate tickers only (`TestPriceTickers::test_candidates_only_unchanged`)
- Additional adversarial cases:
  - NaN last close, zero close and an empty price frame are all named in one warning, and none
    adds to `needed_usd` (`TestMissingPriceWarning::test_nan_and_zero_close_warned`)
  - A stored price for a ticker that is neither candidate nor held is not loaded
    (`TestRunWiring::test_no_held_non_candidates_same_tickers`)
  - Held names do not leak into the optimiser, betas, schedule checks or factor scores
    (`TestRunWiring::test_optimiser_universe_unchanged`)
  - The printed trade list (what the nightly log shows) carries the real price
    (`TestRunWiring::test_held_non_candidate_has_cost`)

## Notes
- Flaky risks: none; SQLite temp DB, optimiser / betas / tail risk / NAV monkeypatched, no
  network.
- Determinism: rows looked up by ticker (`generate_trades` iterates a set).
- 6 of the 11 new tests failed before the fix; the other 5 pin unchanged behaviour.

## Commands
```bash
make lint
make test
make security
```
