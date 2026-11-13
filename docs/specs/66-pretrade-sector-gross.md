# Pre-Trade Sector Check Per Trade, and Layer 4 Targets Capped at the Position Limit

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
2. CF, DVA and AIZ were rejected by check 4 (`POSITION_SIZE: x% > max 5.0%`). Layer 4 produces
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

The nightly logs and the database for 2026-11-12 were not available to this packet; the causes
above are read from the code, and the figures (26.9%, the tickers) are from the run report.

## Scope
In scope:
- `risk/pre_trade.py` check 5 (sector concentration):
  - sums gross over the trade's own sector only;
  - a trade that does not increase its own absolute weight (|target weight| <= |current
    weight|, both at the latest close over NAV) passes check 5 whatever its sector's gross;
  - a trade that increases its own absolute weight is rejected when its sector's pro-forma gross
    is above `risk.pre_trade.max_sector_pct` (unchanged at 0.25).
- `portfolio/rebalance.py` `generate_trades`: after rounding, cap every target at
  `floor(cap x NAV / price)` whole shares (sign kept), where cap = the smaller of
  `portfolio.max_position_pct` and `risk.pre_trade.max_position_pct` (both 0.05). Action, delta
  and cost are derived from the capped target. Rows with no usable price are not capped.
- Pre-trade check 4 stays as the backstop, unchanged.
- Spec 05 check 5 wording; backlog: remove the rounding entry, add the optimiser sector
  definition gap (below).

Out of scope / non-goals:
- No limit value changes; no limit loosened.
- Active trimming of an over-limit sector back under 25%: not done (open question).
- The optimisers' own sector constraint (`portfolio.max_sector_pct` caps each side of a sector
  at 25%, net at 5%, so a sector can be planned at up to ~55% gross while pre-trade caps gross at
  25%): not changed here; backlogged. With this packet only the adds in such a sector are
  rejected.
- The weight renormalisation in the conviction-tilt optimiser: unchanged. The share cap is the
  last sizing step, after rounding, so it covers both optimisers and rounding in one place.
- Checks 6-8 still use the whole pro-forma book (packet 44), unchanged.
- Closing trades (SELL / COVER / target 0) still skip checks 2-8, unchanged.

## Assumptions
- `position_approvals.current_shares` is signed (shorts negative), as written by
  `generate_trades` from the broker book.
- Layer 4 and Layer 5 see the same NAV (`get_nav` + `with_nav` in both runners) and the same
  latest close on or before the score date, so a target at or below the cap in Layer 4 is at or
  below it in Layer 5.

## Proposed behavior / API
```python
# risk/pre_trade.py
_check_sector_concentration(proforma_weights, sector_map, ticker, current_weight, max_sector_pct)
# -> (False, "") when the ticker has no sector or |proforma_weights[ticker]| <= |current_weight|
# -> (True, "SECTOR_CONCENTRATION: <own sector> pro-forma gross x% > max 25.0%") when the
#    trade adds gross and its own sector's pro-forma gross > max

# portfolio/rebalance.py
_position_cap(config) -> float          # min(portfolio.max_position_pct, pre_trade.max_position_pct)
_cap_shares(shares, price, nav, cap) -> float  # sign(shares) * floor(cap * nav / price), never above
```

## Acceptance criteria
- AC1: With one sector's pro-forma gross over `max_sector_pct`, an opening trade in another
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
- AC7: Targets at or below the cap, and rows with no usable price, are unchanged.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; no new queries or inputs.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: none directly. More trades are approved for execution
  than before; every approved trade still passes the same limits (position 5%, sector 25% for
  adds, gross, net, beta, correlation), and Layer 4 orders can only get smaller.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: no limit value changed in `config.yaml`.

## Edge cases
- Trade ticker with no sector in `factor_scores`: passes check 5 (unchanged).
- Long-to-short flip with a larger absolute size in an over-limit sector: increases gross,
  rejected.
- Held position already above the cap with a target above the cap: capped target gives a SELL /
  COVER trim to the cap (closing action, approved).
- `floor(cap x NAV / price)` shares landing a hair above the cap from float rounding: one share
  fewer.
- Price 0 / missing: target not capped (no value known), as before.
- Share price above cap x NAV: target capped to 0 shares (a closure), as the breaker trim does.

## Rollout
1. No schema or config change. Takes effect on the next nightly run.
2. Revert is the two code changes; no data migration.

## Verify in paper trading
- Next nightly: no `POSITION_SIZE` rejections; `SECTOR_CONCENTRATION` rejections only for adds
  in the sector named in the reason.
- Trade list shows no target above 5% of NAV at the latest close.

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

## Decision log
- 2026-11-13: Judge "reduces gross" by the trade's own absolute weight (target vs current) rather
  than the sector total with and without the trade: in the whole-book pro-forma the two are the
  same for a single trade, and the per-trade form needs no second pass over the book.
- 2026-11-13: Cap in `generate_trades`, not in the optimisers: whole-share rounding happens
  there and is the last step that can push a target over, and it covers both optimisers. The
  pre-trade check stays as the backstop.
- 2026-11-13: Cap at the smaller of the two configured limits so the layers cannot disagree if
  one is changed without the other.
