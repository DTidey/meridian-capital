# Betas: Pairwise, Shrunk, and a Beta Adjustment That Works

**Spec file:** `docs/specs/52-betas.md`
**Spec slug:** betas
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit item H3 (`docs/data-audit-2026-10-21.md`): beta and covariance estimates depend on
which other tickers are in the call, betas are not shrunk, and the optimiser's beta adjustment
does nothing.

1. `portfolio/beta.py` `compute_betas` pivots every ticker's returns into one table and calls
   `.dropna()`, which drops any day on which any ticker is missing. A universe-wide call used 12
   of 60 days; JPM's beta was 0.55 alone and -0.80 in the universe call. `compute_betas` feeds
   the optimisers, the pre-trade net-beta veto and the book sync.
2. Raw 60-day OLS betas range from about -0.4 to 4.4.
3. `portfolio/optimizer.py` `_adjust_beta` scales the whole short book by one factor and then
   renormalises it to the short gross target, which undoes the scaling: net beta never moves.
4. `portfolio/mvo_optimizer.py` covariance has the same `.dropna()` as (1).

On the 2026-10-23 target book (packet 51 targets 0.80 / 0.70), net beta is -0.33: the shorts
average beta 1.35, the longs 0.77. With shrinkage it is -0.19, still past the optimiser limit
(`portfolio.max_beta` 0.15) and the pre-trade veto (`risk.pre_trade.max_net_beta` 0.20).

Owner's decisions (2026-10-24): neutralise by reweighting within each book (no SPY hedge, limits
unchanged); shrink betas with the Blume adjustment.

## Scope
In scope:
- `portfolio/beta.py` `compute_betas`: pairwise against SPY over the last `lookback_days` SPY
  returns; at least 20 shared returns or beta 1.0; Blume adjustment `0.67 * raw + 0.33`.
- `portfolio/optimizer.py` `_adjust_beta`: tilt weights within each book, keeping both gross
  targets and the position bounds, until net beta is within `max_beta`.
- `portfolio/mvo_optimizer.py` `_build_cov`: keep days with any data; missing returns per
  ticker are filled as today (ticker mean), not dropped for everyone.
- Docs: spec 04 note, audit H3 fix line, CHANGELOG, decision log.

Out of scope / non-goals:
- The beta lookback (60 days) and the limits themselves.
- A beta hedge instrument.
- Sector neutrality after the beta tilt: the tilt runs after the sector step and may move
  sector nets slightly; pre-trade still checks sector concentration.

## Assumptions
- Shrinkage applies wherever `compute_betas` is used (optimisers, pre-trade, book sync), so the
  optimiser and the pre-trade veto agree.
- SPY's own beta stays 1.0 under Blume (0.67 + 0.33).
- Within a book, weights are positive; net beta = sum(long w * beta) - sum(short w * beta).

## Proposed behavior / API
### Public interface
- `compute_betas(conn, tickers, score_date, lookback_days=60) -> Series`: same signature.
- `_adjust_beta(combined, betas, max_beta, long_gross, short_gross, min_pos, max_pos)`.

### Inputs / outputs
- Beta adjustment, when |net beta| > `max_beta`:
  - tilt strength k: long weights `*= exp(-k * beta)`, short weights `*= exp(+k * beta)`, then
    each book is clamped to [`min_pos`, `max_pos`] and renormalised to its gross target;
  - k's sign follows the excess (positive net beta: k > 0, which lowers long beta and raises
    short beta); its size is the smallest found by bisection that brings |net beta| within
    `max_beta`, searching up to |k| = 20;
  - if the limit cannot be reached, the strongest tilt is used and a WARNING logs the remaining
    net beta (the pre-trade veto still applies).
- Within the limit: weights unchanged.

## Acceptance criteria
- AC1: A ticker's beta is the same whether computed alone or with other tickers, including when
  another ticker has missing days.
- AC2: Betas are Blume-adjusted (`0.67 * raw + 0.33`); fewer than 20 shared returns with SPY, or
  no SPY data, gives 1.0.
- AC3: When net beta exceeds `max_beta`, `_adjust_beta` brings |net beta| within `max_beta`
  where reachable (never overshooting past the limit on the other side), keeping each book's
  gross target and every weight within the position bounds; a book that cannot hold its gross
  within the bounds is left untouched with a warning.
- AC4: When the limit cannot be reached, the closest book is returned and a WARNING is logged;
  within the limit, the book is unchanged.
- AC5: MVO's covariance keeps days on which some tickers are missing.

## Security considerations
- No meaningful security impact: numerical changes to risk estimates and weights. It makes the
  beta limit and the pre-trade beta veto effective.

## Edge cases
- Every beta equal: the tilt cannot change net beta; returned unchanged with a warning.
- One name per book: weights cannot shift within the book; warning.
- A book that cannot hold its gross within the position bounds (fewer than gross / max_pos
  names): the tilt is skipped with a warning rather than shrinking the book.
- A peer's row on a date SPY lacks: returns are taken on SPY's trading days, so it cannot break
  SPY's next return.
- Reach: under the 5% position cap, reweighting moves net beta by roughly 0.08-0.10 for books
  with little beta spread inside each book; beyond that the warning fires and the pre-trade veto
  (0.20) decides.
- A ticker with no prices: beta 1.0 (shrinkage of 1.0 is 1.0).
- Negative raw beta: shrunk towards 1 like any other.

## Test guidance
- AC1 -> `tests/test_beta.py::TestPairwise::test_beta_same_alone_or_with_gappy_peer`,
  `test_lookback_counts_spy_days`, `test_peer_with_off_calendar_date`
- AC2 -> `tests/test_beta.py::TestShrinkage::*`, existing default tests
- AC3 -> `tests/test_optimizer.py::TestAdjustBeta::test_net_beta_brought_within_limit`,
  `test_gross_and_bounds_kept`, `test_positive_excess_tilts_the_other_way`,
  `test_production_bounds_twenty_names`, `test_book_too_small_for_gross_is_skipped`,
  `test_single_short_cannot_flip_net_beta`
- AC4 -> `TestAdjustBeta::test_within_limit_unchanged`, `test_unreachable_warns`,
  `test_old_scaling_no_op_is_gone`
- AC5 -> `tests/test_mvo_optimizer.py::test_cov_keeps_days_with_gaps`

## Decision log
- 2026-10-24: Reweight within books (owner's choice over a SPY hedge or wider limits): no new
  instrument, gross targets and names unchanged.
- 2026-10-24: Blume shrinkage (owner's choice): standard adjusted beta; on 2026-10-23 it moves
  the book's net beta from -0.33 to -0.19 before any reweighting.
- 2026-10-24: Exponential tilt found by bisection, rather than an optimiser: one parameter,
  monotone in practice, and it keeps every weight positive within the bounds.
- 2026-10-24: The tilt uses bounded scaling (names at a bound fixed, the rest share the
  remaining gross) rather than `_clamp_and_renorm`, which collapses a strong tilt back to equal
  weights.
