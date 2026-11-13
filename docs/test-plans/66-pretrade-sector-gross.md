# Test Plan: pretrade-sector-gross

Path: `docs/test-plans/66-pretrade-sector-gross.md`
Spec: `docs/specs/66-pretrade-sector-gross.md`

## What changed
- `portfolio/limits.py` (new): `sector_gross_cap` (reads `risk.pre_trade.max_sector_pct`) and
  `position_cap` (moved from `rebalance.py`).
- `risk/pre_trade.py`: `_check_sector_concentration` takes the trade's ticker and current
  weight; it sums gross over the trade's own sector only, and only a trade that adds to its own
  absolute weight can fail it. The limit is read through `sector_gross_cap`.
- `portfolio/optimizer.py`: `_cap_sector_gross` (with `_refill_books`, `_warn_gross_shortfall`)
  after the sector-neutral step, and a scale-down-only pass after the beta step.
- `portfolio/mvo_optimizer.py`: one combined-gross inequality per sector; `_check_sector_gross`
  raises into the conviction-tilt fallback if the solution is more than 1e-6 over.
- `portfolio/rebalance.py`: `_cap_shares`, `_planned_shares`, `_cap_sector_shares`;
  `generate_trades` caps each rounded target at the position limit and each sector at the
  sector limit before deriving action, delta and cost.
- New `tests/test_pretrade_sector_gross_2026_11_12.py` and `tests/test_optimizer_sector_gross.py`.

## Fixture adjustments (explicit, with the reason)
- Position cap: `tests/test_rebalance.py`, `tests/test_run_portfolio.py`,
  `tests/test_short_cover_target_zero.py`, `tests/test_held_prices_layer4.py` hold 10-15% names
  to exercise turnover; they set `max_position_pct` (1.0, or 0.15 to match the portfolio cap in
  `test_run_portfolio.py`) so the default 5% cap does not change what they test.
- Sector combined gross (17 tests failed until adjusted; each fixture relied on per-side-only
  sector limits):
  - `tests/test_optimizer.py` `_CONFIG`: 1.50 gross over 5 sectors is 0.30 per sector, which no
    0.25 combined limit can hold. `risk.pre_trade.max_sector_pct` 0.50 (the most per side 0.25 +
    net 0.05 allowed before) keeps its gross-target tests as they were. Configs whose per-side
    cap 1.0 meant "never binds" (`test_position_cap`, `_loose_config`, buffered-name test) set
    the combined limit to 2.0 (off). Derived configs (`_BUFFER_CFG`) now carry `risk` through.
  - `tests/test_mvo_optimizer.py` `_CONFIG`: every candidate is "Technology", per-side 0.90
    never binds; combined limit 2.0 (off), carried into derived configs.
  - `tests/test_rebalance.py` `_CONFIG`: targets default to sector "Technology" at up to 150%
    gross; combined limit 2.0 (off), as already done for the position cap; the incumbent-buffer
    config with per-side 1.0 carries it too.
  - `tests/test_run_portfolio.py`: every candidate is sector "S", per-side 1.0; combined 2.0.

## Acceptance criteria coverage
In `tests/test_pretrade_sector_gross_2026_11_12.py`:
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

In `tests/test_optimizer_sector_gross.py` (candidates: 20 longs / 20 shorts over Industrials and
8 other sectors; before this packet conviction-tilt planned Industrials at 27.3% combined):
- AC8: `TestConviction::test_sector_gross_capped`, `test_gross_targets_kept` (0.80 / 0.70),
  `test_positions_within_cap`, `test_beta_tilt_cannot_reopen_breach`
- AC9: `TestMvo::test_sector_gross_capped`, `test_infeasible_falls_back_to_conviction`
- AC10: `TestConviction::test_too_few_sectors_short_of_gross_with_warning` (4 sectors x 0.25 for
  1.50 of gross)
- AC11: `TestSectorShares::test_rounding_over_limit_trimmed` (planned 24.97%, rounded 25.07%),
  `test_under_limit_unchanged`, `test_no_sector_not_capped`
- AC12: `TestPipeline::test_plan_passes_pre_trade_sector_check[conviction]` and `[mvo]`: broker
  book with Industrials at 26.9%, optimiser -> `generate_trades(commit=True)` ->
  `run_pre_trade`; every trade approved, no `SECTOR_CONCENTRATION`
- AC13: `TestSharedLimit::test_one_value_moves_plan_and_check` (limit 0.20: plan max 0.20 and no
  sector rejects; the same plan checked at 0.15 has sector rejects)

## Edge cases
- From spec:
  - Ticker without a sector passes check 5 (`TestSectorCheck::test_trade_without_sector_passes`)
  - Larger flip in a breaching sector rejected (`test_larger_flip_in_breaching_sector_rejected`)
  - Held oversize trimmed to the cap (`test_held_oversized_trimmed_to_cap`)
  - Sector already over the limit in the broker book (`TestPipeline`)
  - Too few sectors for the gross target (`test_too_few_sectors_short_of_gross_with_warning`,
    `TestMvo::test_infeasible_falls_back_to_conviction`)
  - Beta tilt pushing a sector over (`test_beta_tilt_cannot_reopen_breach`)
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
  - Rounding alone tips a sector planned under the limit over it, short side included
    (`test_rounding_over_limit_trimmed`)
  - The beta-tilt test fails with the post-beta pass removed (checked by hand)

## Notes
- Before the fix: 11 of the first 17 pre-trade/cap tests failed (the regression test reproduced
  the 11-12 reason `Industrials pro-forma gross 26.9% > max 25.0%` on MOS, and
  `POSITION_SIZE: 5.1%` on CF). In the optimiser file, the conviction, MVO and pipeline tests
  failed (Industrials 27.3% conviction, a sector at 35% MVO); `TestSectorShares` could not
  import its helper.
- Flaky risks: low; in-memory SQLite, seeded random walks (fixed seed), no network.

## Commands
```bash
make lint
make test
make security
```
