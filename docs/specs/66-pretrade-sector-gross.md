# Sector Gross and Position Limits Shared by Layer 4 and the Pre-Trade Check

**Spec file:** `docs/specs/66-pretrade-sector-gross.md`
**Spec slug:** pretrade-sector-gross
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-13

## Problem statement
Nightly run 2026-11-12: Layer 5 rejected 38 of 42 proposed trades; only 4 exits went out (sell
FOXA and NEM, cover ABT and IRM).

1. 35 rejections read `SECTOR_CONCENTRATION: Industrials pro-forma gross 26.9% > max 25.0%`,
   including trades in other sectors (MOS, COP, BEN). `risk/pre_trade.py`
   `_check_sector_concentration` sums gross per sector over the whole pro-forma book and rejects
   on the first sector over the limit, whatever the trade's own sector. Since packet 44 the
   pro-forma book is the broker book with every PENDING target applied, so it is the same book
   for every trade: one sector over 25% rejects every opening trade that night. The trade's own
   effect is never looked at, so a trade that cuts gross in the breaching sector is rejected too.
2. The 26.9% is Layer 4's plan. Both optimisers cap each side of a sector at
   `portfolio.max_sector_pct` (0.25) and the sector net at 0.05, so a sector can be planned at up
   to 50% combined gross; the pre-trade check caps combined long + short gross at
   `risk.pre_trade.max_sector_pct` (0.25). The 25% is meant as combined gross (confirmed by the
   owner, 2026-11-13). Layer 4 therefore plans adds the check is bound to reject.
3. CF, DVA and AIZ were rejected by check 4 (`POSITION_SIZE: x% > max 5.0%`). Layer 4 produces
   targets above the cap it is configured with:
   - the conviction-tilt optimiser (the default, `--optimize-method conviction`) clips weights
     to `portfolio.max_position_pct` in `_clamp_and_renorm` and then rescales each book back to
     its gross target, which lifts the clipped names above the cap; `_enforce_sector_neutral`
     rescales each book again at the end. With 20 longs at 0.80 gross the top-conviction name
     comes out at about 5.05%;
   - `portfolio/rebalance.py` `generate_trades` rounds target shares to the nearest whole share,
     which can add up to one share (0.1-0.3% of a $100k NAV for $100-$300 names).
   Pre-trade prices the same target with the same NAV (`with_nav`) and the same latest close, so
   any target above 5% is bound to be rejected. Already in the backlog ("Pre-trade
   POSITION_SIZE rejects from whole-share rounding": MU, ARE, BA between 2026-10-30 and 11-03).
   Whole-share rounding can push a sector planned at exactly 25% over it in the same way.

The nightly logs and the database for 2026-11-12 were not available to this packet; the causes
above are read from the code, and the figures (26.9%, the tickers) are from the run report.

## Scope
In scope:
- `portfolio/limits.py` (new): `sector_gross_cap(config)` reads the one sector limit,
  `risk.pre_trade.max_sector_pct` (0.25, combined long + short gross); `position_cap(config)`
  (moved from `rebalance.py`) is the smaller of `portfolio.max_position_pct` and
  `risk.pre_trade.max_position_pct`. Both optimisers, `generate_trades` and the pre-trade check
  read the sector limit through `sector_gross_cap`.
- `risk/pre_trade.py` check 5 (sector concentration):
  - sums gross over the trade's own sector only;
  - a trade that does not increase its own absolute weight (|target weight| <= |current
    weight|, both at the latest close over NAV) passes check 5 whatever its sector's gross;
  - a trade that increases its own absolute weight is rejected when its sector's pro-forma gross
    is above the sector limit (unchanged at 0.25).
- `portfolio/mvo_optimizer.py`: one more linear inequality per sector, long sum + |short sum|
  <= sector limit. A solve that fails, or reports success with a sector more than 1e-6 over the
  limit, raises and takes the existing fallback to conviction-tilt.
- `portfolio/optimizer.py` (conviction-tilt): `_cap_sector_gross` after the sector-neutral step
  scales every sector over the limit down to it (both sides by the same factor, so the sector
  net shrinks with it), then refills each book to its gross target from names in sectors below
  the limit, each within `max_position_pct`, and repeats until no sector is over. After the beta
  step a second pass only scales down (no refill), so the beta tilt cannot reopen a breach and a
  refill cannot undo the beta limit.
- `portfolio/rebalance.py` `generate_trades`, after rounding:
  - cap every target at `floor(position cap x NAV / price)` whole shares (sign kept);
  - for each sector (target `sector` column) whose combined target value is above the sector
    limit, truncate its targets toward zero, then take one share at a time off its largest
    position until it is at or below the limit.
  Action, delta and cost are derived from the capped targets. Rows with no usable price or no
  sector are not sector-capped.
- Per-side caps (`portfolio.max_sector_pct`) and the sector net cap
  (`portfolio.max_sector_net_pct`) are kept in both optimisers. At the production values (per
  side 0.25 = combined 0.25) the per-side cap is implied by the combined cap and no longer binds;
  it still binds if set lower.
- Pre-trade check 4 stays as the backstop, unchanged.
- Spec 05 check 5 wording; spec 04 sector constraint; `config.yaml` comments; backlog: remove
  the rounding entry.

Deliberate decisions (owner, 2026-11-13), not open questions:
- No active trimming of a sector already over the limit. The optimisers plan every sector at or
  below the limit, so the plan reduces it through the normal trade list and turnover budget;
  nothing prioritises those reductions.
- All adds in a sector whose pro-forma gross is over the limit are rejected together, even if
  accepting some would keep it under (whole-book pro-forma, packet 44).
- On nights the position or sector cap binds, the book may sit slightly below its gross target;
  the capped excess is not redistributed after rounding.

Out of scope / non-goals:
- No limit value changes; no limit loosened.
- Checks 6-8 still use the whole pro-forma book (packet 44), unchanged.
- Closing trades (SELL / COVER / target 0) still skip checks 2-8, unchanged.
- The weight renormalisation in `_clamp_and_renorm`: unchanged; the share cap covers it.

## Assumptions
- `position_approvals.current_shares` is signed (shorts negative), as written by
  `generate_trades` from the broker book.
- Layer 4 and Layer 5 see the same NAV (`get_nav` + `with_nav` in both runners) and the same
  latest close on or before the score date, so a target at or below a cap in Layer 4 is at or
  below it in Layer 5.
- Layer 4 candidates and the pre-trade check take sectors from the same `factor_scores` rows.
  Layer 4 labels a missing sector "Unknown" and caps it like any sector; pre-trade skips it.

## Proposed behavior / API
```python
# portfolio/limits.py
sector_gross_cap(config) -> float  # risk.pre_trade.max_sector_pct, default 0.25
position_cap(config) -> float      # min(portfolio, risk.pre_trade max_position_pct)

# risk/pre_trade.py
_check_sector_concentration(proforma_weights, sector_map, ticker, current_weight, max_sector_pct)

# portfolio/optimizer.py
_cap_sector_gross(df, cap, targets: dict[str, float] | None, max_pos) -> pd.DataFrame
# WARNING when a book cannot reach its gross target inside the sector limit

# portfolio/rebalance.py
_cap_shares(shares, price, nav, cap) -> float
_cap_sector_shares(shares, raw, price_of, nav, cap) -> dict[str, float]
```

## Acceptance criteria
- AC1: With one sector's pro-forma gross over the sector limit, an opening trade in another
  sector is not rejected by check 5.
- AC2: A trade that reduces its own absolute weight in an over-limit sector (e.g. a long flipped
  to a smaller short) is approved even though the sector stays over the limit.
- AC3: A trade that increases its own absolute weight in an over-limit sector is rejected with
  `SECTOR_CONCENTRATION` naming its own sector; a trade that takes its own sector over the limit
  is still rejected; the limit stays 0.25.
- AC4: `generate_trades` caps every priced target at `floor(cap x NAV / price)` whole shares
  (longs and shorts), with cap the smaller of the portfolio and pre-trade
  `max_position_pct`; action, delta and cost follow the capped target.
- AC5: A Layer 4 target above 5% of NAV, written by `generate_trades` and run through
  `run_pre_trade`, is APPROVED at the cap, not rejected by `POSITION_SIZE`.
- AC6: The 2026-11-12 situation (Industrials over 25%, trades in Materials / Energy / Financials,
  an Industrials reduction, an Industrials add) gives: other-sector trades and the reduction
  approved, the add rejected.
- AC7: Targets at or below the caps, and rows with no usable price, are unchanged.
- AC8: The conviction-tilt optimiser plans every sector at or below the sector limit combined
  (long + |short|), for a candidate set it planned with Industrials at 27.3% before this packet,
  keeps each book at its gross target when other sectors have room, and keeps every position
  within `max_position_pct`.
- AC9: The MVO optimiser plans every sector at or below the sector limit combined for the same
  candidate set; a solve that is infeasible under the limit falls back to conviction-tilt.
- AC10: When the sectors cannot hold a book's gross target inside the limit, the conviction-tilt
  book is left below its target with no sector over the limit, and a WARNING is logged.
- AC11: `generate_trades` keeps each sector's combined target value (whole shares at the latest
  close) at or below the sector limit after rounding.
- AC12: Optimiser -> `generate_trades` -> `run_pre_trade`, from a broker book holding Industrials
  at 26.9% combined, gives no `SECTOR_CONCENTRATION` rejection, for both optimisers.
- AC13: The optimisers, `generate_trades` and the pre-trade check all read the sector limit from
  `risk.pre_trade.max_sector_pct`: changing that one value changes the plan and the check.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; no new queries or inputs.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: none directly. More trades are approved for execution
  than before; every approved trade still passes the same limits (position 5%, sector 25% for
  adds, gross, net, beta, correlation), and Layer 4 targets can only get smaller or move to
  sectors with room.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: no limit value changed in `config.yaml`.

## Edge cases
- Trade ticker with no sector in `factor_scores`: passes check 5 (unchanged).
- Long-to-short flip with a larger absolute size in an over-limit sector: increases gross,
  rejected.
- Held position already above the position cap with a target above it: capped target gives a
  SELL / COVER trim to the cap (closing action, approved).
- Sector already over the limit in the broker book: the plan targets it at or below the limit
  (reductions are closing trades); its adds pass when the whole planned book is under the limit.
  If the turnover budget trims a reduction (TRIMMED keeps the broker size in the pro-forma), its
  adds can still be rejected that night (deliberate decision 2).
- Too few sectors for the gross target (e.g. 1.50 gross across 5 sectors at 0.25): conviction
  book short of gross with a WARNING; MVO infeasible, falls back to conviction-tilt.
- Beta tilt pushes a sector over the limit: scaled back down after the tilt.
- `floor(cap x NAV / price)` shares landing a hair above a cap from float rounding: one share
  fewer.
- Price 0 / missing / NaN: target not capped (no value known), as before.
- Share price above the position cap x NAV: target capped to 0 shares (a closure).

## Rollout
1. No schema or config value change (comments only). Takes effect on the next nightly run.
2. Expect the first run to plan reductions in any sector above 25% combined (Industrials).
3. Revert is the code changes; no data migration.

## Verify in paper trading
- Next nightly: no `POSITION_SIZE` rejections; `SECTOR_CONCENTRATION` rejections only for adds
  in the sector named in the reason, and none once the planned book is under the limit.
- Layer 4 target portfolio: no sector above 25% combined; no position above 5%.
- Watch for the "below gross target" WARNING.

## Test guidance
- AC1 -> `tests/test_pretrade_sector_gross_2026_11_12.py::TestSectorCheck::test_other_sector_trade_passes`,
  `test_add_in_unbreached_sector_passes_beside_breach`
- AC2 -> `TestSectorCheck::test_reducing_flip_in_breaching_sector_passes`
- AC3 -> `TestSectorCheck::test_adding_trade_in_breaching_sector_rejected`,
  `test_larger_flip_in_breaching_sector_rejected`, `test_trade_taking_own_sector_over_rejected`
- AC4 -> `TestPositionCap::test_long_and_short_capped`, `test_rounding_up_capped`,
  `test_stricter_pre_trade_cap_used`, `test_held_oversized_trimmed_to_cap`,
  `test_float_boundary_never_above_cap`, `test_share_dearer_than_cap_goes_to_zero`
- AC5 -> `TestPositionCap::test_capped_target_approved_by_pre_trade`
- AC6 -> `TestRegression20261112::test_run_outcome`
- AC7 -> `TestPositionCap::test_targets_under_cap_unchanged`, `test_unpriced_not_capped`,
  `test_nan_close_not_capped`
- AC8 -> `tests/test_optimizer_sector_gross.py::TestConviction::test_sector_gross_capped`,
  `test_gross_targets_kept`, `test_positions_within_cap`, `test_beta_tilt_cannot_reopen_breach`
- AC9 -> `TestMvo::test_sector_gross_capped`, `test_infeasible_falls_back_to_conviction`
- AC10 -> `TestConviction::test_too_few_sectors_short_of_gross_with_warning`
- AC11 -> `TestSectorShares::test_rounding_over_limit_trimmed`, `test_under_limit_unchanged`,
  `test_no_sector_not_capped`
- AC12 -> `TestPipeline::test_plan_passes_pre_trade_sector_check[conviction]`, `[mvo]`
- AC13 -> `TestSharedLimit::test_one_value_moves_plan_and_check`

## Decision log
- 2026-11-13: Judge "reduces gross" by the trade's own absolute weight (target vs current) rather
  than the sector total with and without the trade: in the whole-book pro-forma the two are the
  same for a single trade, and the per-trade form needs no second pass over the book.
- 2026-11-13: Cap positions in `generate_trades`, not in the optimisers: whole-share rounding
  happens there and is the last step that can push a target over, and it covers both
  optimisers. The pre-trade check stays as the backstop.
- 2026-11-13: Cap positions at the smaller of the two configured limits so the layers cannot
  disagree if one is changed without the other.
- 2026-11-13: The 25% sector limit is combined gross (owner). The single value is the existing
  `risk.pre_trade.max_sector_pct`: the check was already right, and no config key moves.
  `portfolio.max_sector_pct` stays as the per-side cap.
- 2026-11-13: Sector limit in the optimisers plus a whole-share guard in `generate_trades`: the
  optimisers plan up to exactly 25%, which rounding alone can tip over.
- 2026-11-13: Conviction-tilt scales a breaching sector's two sides by the same factor (keeps the
  sector net cap) and refills from sectors with room before the beta step; after the beta step
  it only scales down, because a refill there could undo the beta limit and a scale-down only
  removes risk.
- 2026-11-13: Decisions 2-4 above (no active trimming, all-or-nothing adds, small gross shortfall)
  are the owner's, recorded as deliberate.
