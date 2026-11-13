# Test Plan: pretrade-sector-gross

Path: `docs/test-plans/66-pretrade-sector-gross.md`
Spec: `docs/specs/66-pretrade-sector-gross.md`

## What changed
- `risk/pre_trade.py`: `_check_sector_concentration` takes the trade's ticker and current
  weight; it sums gross over the trade's own sector only, and only a trade that adds to its own
  absolute weight can fail it.
- `portfolio/rebalance.py`: new `_position_cap` and `_cap_shares`; `generate_trades` caps each
  rounded target at the position limit before deriving action, delta and cost.
- New `tests/test_pretrade_sector_gross_2026_11_12.py`.
- Existing fixtures in `tests/test_rebalance.py`, `tests/test_run_portfolio.py`,
  `tests/test_short_cover_target_zero.py` and `tests/test_held_prices_layer4.py` hold 10-15%
  names to exercise turnover; they now set `max_position_pct` explicitly (1.0, or 0.15 to
  match the portfolio cap already in `test_run_portfolio.py`) so the new default 5% cap does
  not change what they test.

## Acceptance criteria coverage
- AC1: `TestSectorCheck::test_other_sector_trade_passes` (MOS buy with Industrials at 28%),
  `test_add_in_unbreached_sector_passes_beside_breach`
- AC2: `TestSectorCheck::test_reducing_flip_in_breaching_sector_passes` (HON 5% long -> 3%
  short, Industrials still 26%)
- AC3: `TestSectorCheck::test_adding_trade_in_breaching_sector_rejected` (exact reason text),
  `test_larger_flip_in_breaching_sector_rejected`, `test_trade_taking_own_sector_over_rejected`
- AC4: `TestPositionCap::test_long_and_short_capped`, `test_rounding_up_capped` (51.55 shares
  at $97 rounds to 52 = 5.04%, capped to 51), `test_stricter_pre_trade_cap_used`,
  `test_held_oversized_trimmed_to_cap` (SELL 20), `test_float_boundary_never_above_cap`,
  `test_share_dearer_than_cap_goes_to_zero`
- AC5: `TestPositionCap::test_capped_target_approved_by_pre_trade` (CF, DVA, AIZ written by
  `generate_trades(commit=True)` then run through `run_pre_trade`)
- AC6: `TestRegression20261112::test_run_outcome` (pro-forma Industrials 26.9%: MOS, COP, BEN,
  HON flip and CAT trim approved; DE add rejected with the 26.9% reason)
- AC7: `TestPositionCap::test_targets_under_cap_unchanged`, `test_unpriced_not_capped`,
  `test_nan_close_not_capped`; all existing pre-trade, rebalance and Layer 4 tests

## Edge cases
- From spec:
  - Ticker without a sector passes check 5 (`TestSectorCheck::test_trade_without_sector_passes`)
  - Larger flip in a breaching sector rejected (`test_larger_flip_in_breaching_sector_rejected`)
  - Held oversize trimmed to the cap (`test_held_oversized_trimmed_to_cap`)
  - Float edge of `floor(cap x NAV / price)` (`test_float_boundary_never_above_cap`)
  - Missing / NaN price not capped (`test_unpriced_not_capped`, `test_nan_close_not_capped`)
  - Share dearer than the cap -> 0 shares (`test_share_dearer_than_cap_goes_to_zero`)
- Additional adversarial cases:
  - Sweep of ~33,000 prices x 3 NAVs x both signs: capped value never above 5%, sign kept
    (`test_float_boundary_never_above_cap`)
  - Pre-trade cap stricter than the portfolio cap: the stricter one wins
    (`test_stricter_pre_trade_cap_used`)
  - Industrials add rejected and an Energy add approved in the same run
    (`test_add_in_unbreached_sector_passes_beside_breach`)
  - Exact-cap short (-49.6 rounds to -50 = 5.0%) is not trimmed
    (`test_targets_under_cap_unchanged`)

## Notes
- 11 of the first 17 new tests failed before the fix (the regression test reproduced the
  11-12 reason `Industrials pro-forma gross 26.9% > max 25.0%` on MOS, and `POSITION_SIZE: 5.1%`
  on CF); the rest pin unchanged behaviour. `test_nan_close_not_capped` was added after the
  existing `test_nan_and_zero_close_warned` caught a NaN close reaching `math.floor`.
- Flaky risks: none; in-memory SQLite, flat prices, no network.

## Commands
```bash
make lint
make test
make security
```
