# Layer 4 Churn: Incumbent Buffer and Regime Weight Smoothing

**Spec file:** `docs/specs/64-churn-hysteresis.md`
**Spec slug:** churn-hysteresis
**Status:** Draft
**Owner:** DTidey
**Date:** 2026-11-10

## Problem statement
The book churns: names opened one night are closed in full one or two nights later, and the
turnover budget then trims the replacements, so gross falls further below target (packets
57-60 context, `docs/investigations/2026-11-05-underinvested-book.md`).

Evidence, nightly run 2026-11-09 vs 2026-11-06 (log `logs/run_all_2026-11-09.log`; DB read
only, `combined_scores`, `factor_scores`, `regime_state`, `position_approvals`, 2026-11-10):

- 12 positions opened by the 11-06 orders were closed in full on 11-09 (all APPROVED, target 0):
  MOS, VICI, APA, ATO, YUM, COHR, WELL, AJG, CRL, TTWO, MSI, DE. The same night 13 opening or
  adding trades were TRIMMED by the turnover budget (BUY 4, SHORT 7, SELL 1, COVER 1).
- Side rank by `combined_score` (1 = strongest), 11-06 -> 11-09:

  | Ticker | Side | 11-06 | 11-09 | Composite 11-06 (stored) | 11-06 recomputed, NORMAL weights | 11-09 |
  |---|---|---|---|---|---|---|
  | DE | SHORT | 12 | 74 | 4.8 | 19.3 | 19.3 |
  | ATO | LONG | 18 | 85 | 96.8 | 83.9 | 80.6 |
  | COHR | SHORT | 15 | 71 | 5.3 | 1.3 | 18.4 |
  | TTWO | SHORT | 5 | 40 | 4.5 | 9.1 | 9.1 |
  | MOS | SHORT | 7 | 38 | 4.2 | 8.3 | 12.5 |
  | AJG | SHORT | 18 | 41 | 3.9 | 9.2 | 9.2 |
  | MSI | SHORT | 10 | 30 | 1.3 | 6.6 | 6.6 |
  | WELL | LONG | 16 | 42 | 93.3 | 100.0 | 86.7 |
  | APA | LONG | 5 | 21 | 100.0 | 100.0 | 95.2 |
  | VICI | LONG | 9 | NEUTRAL | 96.7 | 80.0 | 71.7 |
  | YUM | SHORT | 8 | NEUTRAL | 6.4 | 23.4 | 23.4 |
  | CRL | SHORT | 19 | NEUTRAL | 6.7 | 20.0 | 26.7 |

  Ranks use ties broken by order; the run log's ranks differ by one for APA and TTWO.

### Cause A: hard VIX regime switch
- `factors/regime_weights.py` `resolve_regime` switches weights on one day's VIX close:
  HIGH_VOL above 25 (quality 0.28, value 0.22, momentum 0.10), LOW_VOL below 15 (momentum
  0.28, value 0.10), otherwise the base weights (momentum 0.20, quality 0.20, value 0.15).
  The thresholds are hard-coded; `scoring.regime_weights.*.vix_above` / `vix_below` in
  `config.yaml` are never read.
- `regime_state`: 11-04 VIX 16.6 NORMAL, 11-05 28.2 HIGH_VOL, 11-06 25.6 HIGH_VOL, 11-09 19.9
  NORMAL. The composite is a sector rank of the weighted sum (`factors/composite.py`), so a
  reweighting reshuffles ranks even when factor scores are unchanged.
- Day-over-day Spearman of `composite_score`: about 0.98 on normal nights, 0.802 on 11-04 ->
  11-05 and 0.905 on 11-06 -> 11-09. Recomputed from the stored factor scores with NORMAL
  weights on every day, the same pairs are 0.882 and 0.971. On a single day, NORMAL vs
  HIGH_VOL weights give a Spearman of only 0.92-0.93.
- The table above shows the 11-09 move was mostly the 11-06 book being built on two days of
  HIGH_VOL weights: for DE, TTWO, AJG, MSI and YUM the 11-06 composite under NORMAL weights
  equals the 11-09 value. COHR, MOS and WELL also moved on factors (short interest, mid-month
  refresh, Spearman 0.90; revisions 0.93).
- The data audit already flagged it: `docs/data-audit-2026-10-21.md` M7, "Regime flips 16
  times in 115 days with no hysteresis". Since 2026-05-01 the switch flipped 18 times in 135
  sessions, mostly around the VIX 15 boundary.

### Cause B: no incumbency buffer in Layer 4
- `portfolio/optimizer.py` (and `portfolio/mvo_optimizer.py`) take a strict top `num_longs` /
  `num_shorts` (20) per side by `combined_score` every night. A held name at side rank 21 or
  worse, or one that leaves the LONG/SHORT candidate band, gets target 0 and is closed in full.
- `portfolio/rebalance.py` `_apply_turnover_budget`: full closures are never trimmed and are
  charged to the budget first (`budget_remaining = budget - closures`), so on a high-churn
  night the closures use up the rotation budget and the replacements are trimmed. In a book
  below target, the packet 57 build-up allowance covers opens only up to the pre-trade gap,
  so it does not cover the gross freed by that night's closures. Each rotated dollar is
  charged twice (closure plus replacement).
- Backlog item "Back-and-forth trading across consecutive nights" (raised 2026-11-04) is the
  same problem; this spec replaces it and the item is removed from `docs/backlog.md`. Its
  examples: CSGP +4 (10-30), +60 to 122 (11-02), sold to 0 (11-03); ALLE +3 (11-02), sold to 0
  (11-03); CLX covered (10-30, 11-02), shorted again (11-03).

### Size of the effect (rank-only replay, 2026-10-27 to 2026-11-09)
Idealised 20/20 book rebuilt from `combined_scores` each night, no costs, full fills:

| Exit rank | Name exits (9 nights) | Round trips (exit within 2 nights of entry) | Mean side rank held | Top-10 names left out per side-night |
|---|---|---|---|---|
| 20 (today) | 92 | 61 | 10.5 | 0.00 |
| 25 | 67 | 39 | 10.8 | 0.00 |
| 30 | 51 | 26 | 11.4 | 0.05 |
| 35 | 41 | 18 | 12.0 | 0.15 |
| 40 | 33 | 14 | 12.8 | 0.25 |

On 11-09 alone, exits fall from 14 to 7 at exit rank 35. This replay does not change the
regime weights; AC9 adds the full replay.

## Scope
In scope:
1. Incumbent buffer in Layer 4 book selection (both optimisers): enter at the top `num_longs`
   / `num_shorts`, keep a held name while it is still a candidate on the same side and its
   side rank is within `portfolio.incumbent_exit_rank`.
2. Regime weight smoothing in Layer 2: blend base and regime weights linearly across a VIX
   band, driven by a trailing mean of VIX closes, for both high_vol and low_vol; read the
   band edges from config; persist the smoothed VIX and blend shares.
3. Turnover budget: count a rotation once. Closures stay never-trimmed and charged; opens and
   adds in the same book are exempt up to the value those closures charged.
4. A read-only replay tool that measures turnover and round trips for the current rules and
   each change, from the stored history since 2026-10-27.
5. Remove the backlog item and point to this spec.

Out of scope / non-goals:
- Minimum holding period (see Design 4: rejected for now).
- Buffering names that leave the candidate band (NEUTRAL). They are closed as today; see
  Design 1 for why and for the follow-up option.
- Held-name prices outside the candidate list: done in packet 63 (merged, PR #131); held
  non-candidates now get prices in Layer 4.
- Smoothing factor scores or `combined_score` over time (EMA of scores): a wider change to
  every factor's meaning; revisit only if the replay shows the buffer and regime smoothing
  are not enough.
- Tail-risk gross scaling (`risk/tail_risk.py`, packet 58): it responds to the same day's VIX
  on purpose and is unchanged.
- One-order direction flips (backlog "Direction flip in one order").
- Changing `num_longs`, `num_shorts`, gross targets, the conviction tilt or the 30% budget.

## Assumptions
- The broker book synced into `portfolio_positions` (packets 50-51) is the source of truth for
  what is held; positive shares = held long, negative = held short.
- About 100 names per side are candidates each night (`combined_scores`: 108 LONG, 96-97
  SHORT), so side ranks up to about 100 exist and an exit rank of 35 is well inside the band.
- `combined_score` is on the side's band (LONG 80-100, SHORT 0-20,
  `analysis/combined_score.py`), so side rank by `combined_score` is the right measure.
- Layer 5 and the circuit breaker can still close or cut any position; the buffer only
  changes what Layer 4 proposes.

## Design

### 1. Incumbent buffer (recommended: rank buffer, fixed book size)
Options:
- (a) Rank buffer, fixed count (recommended). Per side: incumbents are held names that are
  candidates on the same side with side rank <= `incumbent_exit_rank`; keep them (best rank
  first, capped at `num_longs` / `num_shorts`), then fill the remaining slots with the best
  ranked non-incumbents. The book still has exactly `num_*` names when enough candidates
  exist, so gross targets, position bounds and per-name sizes behave as today.
- (b) Rank buffer with extra slots (book grows to N + k): changes per-name size every time an
  incumbent is buffered, which creates resize trades of its own. Rejected.
- (c) Score-distance buffer (keep while `combined_score` is within x points of the Nth name):
  harder to reason about because the band mapping compresses scores. Rejected.

Rules for (a):
- Side rank: 1-based rank within the side's candidates, LONG by `combined_score` descending,
  SHORT ascending; ties broken by ticker ascending so selection is deterministic.
- Leaves the candidate band (now NEUTRAL, or no `combined_scores` row): not an incumbent;
  target 0, closed as today. Reason: leaving the band already means a move from the top or
  bottom quintile of the sector to the middle, a rank of about 100 or worse on the side; and
  Layer 3 computes no side-band `combined_score` for NEUTRAL names, so there is no rank to
  compare. Under regime smoothing, the band exits caused by the regime switch (YUM, CRL)
  should mostly not happen. Follow-up option if the replay shows many band exits: a wider
  exit band on the composite (e.g. keep a long while composite >= 70); the prices it needs
  are in place since packet 63.
- Flips side (held long, now a SHORT candidate, or the reverse): not an incumbent on its old
  side; target 0 on that side. It can enter the other side only by the normal top-N rule.
- Buffered names are ordinary book members after selection. The equal-weight base, conviction
  tilt (top 5% and 10% of the selected book by `combined_score`), earnings haircut, liquidity
  cap, position bounds, sector caps and beta tilt run unchanged on the selected book. Sector
  and beta steps change weights, not membership, so they cannot evict an incumbent. A
  buffered name at rank 21-35 is at the bottom of the book and gets no tilt.
- No lower weight for buffered names: a weight that changes as a name moves in and out of the
  buffer would create resize trades every night.
- Held count above `num_*` on a side (legacy book): incumbents are capped at `num_*` by rank;
  the rest get target 0.
- Cost: a new top-10 name is left out about 0.15 times per side-night at exit rank 35 (replay
  above); it enters when an incumbent leaves.

### 2. Regime weights (recommended: linear blend on a 5-day mean VIX)
Options:
- (a) Separate enter/exit thresholds (hysteresis), state from the previous score date. Since
  2026-05-01, bands 13/17 and 23/27 cut flips from 18 to 2, but each flip is still a full
  weight jump, and this episode is not fixed: 11-05 (28.2) enters HIGH_VOL, 11-06 (25.6) stays,
  11-09 (19.9) leaves, the same two jumps as today. It also needs the previous night's state,
  which is wrong after a missed night or a backfill. Rejected.
- (b) Linear interpolation across a band on the day's VIX. Smooth in level but not in time:
  the high_vol share moves 0.00 -> 0.82 -> 0.56 -> 0.00 over 11-04 to 11-09 (band 20-30).
  Rejected on its own.
- (c) Linear interpolation across a band on a trailing mean of the last 5 VIX closes
  (recommended). The high_vol share over the same days is 0.00 -> 0.12 -> 0.25 -> 0.22; since
  2026-05-01 the largest one-day change is 0.12 (high) and 0.21 (low), against full 1.0 jumps
  today. Stateless (a pure function of stored closes, so backfills and missed nights give the
  same answer), and one bad VIX print moves it by a fifth. The lag is acceptable: the
  factor-weight tilt is a slow view, and the fast response to a VIX spike stays with tail
  risk (packet 58), which still reads the day's VIX.

Rule for (c):
- `vix_smoothed` = mean of the last `vix_smoothing_days` VIX closes dated on or before the
  score date, looked up within 14 calendar days (holidays and gaps). Fewer closes than
  `vix_smoothing_days` but at least one: mean of those, with a warning. None: NORMAL (both
  shares 0), with a warning, as today.
- `high_share` = clip((vix_smoothed - high_vol.vix_none) / (high_vol.vix_full -
  high_vol.vix_none), 0, 1); `low_share` = clip((low_vol.vix_none - vix_smoothed) /
  (low_vol.vix_none - low_vol.vix_full), 0, 1). The bands do not overlap, so at most one
  share is above 0.
- Weights = (1 - high_share - low_share) x base + high_share x high_vol weights + low_share x
  low_vol weights, where each regime's weights are today's `adjust_weights` output (overrides
  then renormalise); the result sums to 1.
- Label (for `regime_state.regime`, `factor_scores.regime`, dashboard and logs): HIGH_VOL if
  `high_share >= 0.5`, LOW_VOL if `low_share >= 0.5`, else NORMAL. The band centres (25 and
  15) keep today's thresholds as the label boundary.
- `regime_conditional_weights: false` still means base weights, shares 0.

### 3. Turnover budget and closures (recommended: count each rotation once)
Today closures are never trimmed and are charged to the budget first, so they push the
replacements out.
- (a) Keep as is. On a night like 11-09 the book stays below target. Rejected.
- (b) Exempt closures from the budget. A rotation then costs only the replacement leg, but a
  night where the signal collapses could close half the book with no limit on what follows,
  and a name that is not re-bought still frees no budget. Rejected.
- (c) Make closures trimmable. Leaves names Layer 4 no longer wants (beyond the buffer, out of
  band, or flipped) held to protect entries, and breaks packet 57/61 (closures never
  trimmed, Layer 5 counts on it). Rejected.
- (d) Rotation credit (recommended). Closures stay never-trimmed and charged at their
  `budget_value`, ordered first as today. Then, per book, opens and adds that are still charged
  after the packet 57 build-up allowance are exempt up to that book's charged closure value
  (highest conviction first, same order as the allowance). A pure rotation costs its one-way
  value, the usual definition of turnover; the 30% budget still caps the night's total.

With the buffer, a closure is now a real signal exit (beyond rank 35, out of band, or flipped),
so letting its replacement through is what the strategy wants.

### 4. Minimum holding period (recommended: not now)
A hold of k nights would block exits for k nights whatever the signal says. It would overlap
with the buffer (double dampening: a name kept by the buffer is also inside the hold; a name
outside both would still be held), it is time-based and so ignores how far the name fell
(DE: rank 12 -> 74), and it has to exempt risk and band exits, which adds special cases.
Not added. If the AC9 replay at the chosen exit rank still shows round trips within 2 nights,
a follow-up packet can add `min_hold_nights` that blocks rank exits only (never band exits,
flips, Layer 5 or circuit-breaker closures).

How the three measures fit together, with no double dampening:
- Regime smoothing acts on the score input: fewer rank shocks.
- The buffer acts on book membership: absorbs the rank noise that remains.
- The rotation credit acts on trade sizing: lets the replacements for real exits through, so
  it loosens rather than adds damping.
- The exit rank is chosen with smoothing in place (AC9 replay), so the two dampers are tuned
  together rather than stacked: pick the smallest exit rank that gets 2-night round trips
  near zero.

## Config keys
```yaml
scoring:
  regime_weights:
    vix_smoothing_days: 5     # trailing VIX closes averaged before blending
    low_vol:
      vix_full:  12           # smoothed VIX <= 12: full low_vol weights
      vix_none:  18           # smoothed VIX >= 18: base weights
      momentum:  0.28
      value:     0.10
    high_vol:
      vix_none:  20           # smoothed VIX <= 20: base weights
      vix_full:  30           # smoothed VIX >= 30: full high_vol weights
      quality:   0.28
      value:     0.22
      momentum:  0.10

portfolio:
  incumbent_exit_rank: 35     # keep a held name while its side rank <= this; = num_* is today
```
- `low_vol.vix_below` and `high_vol.vix_above` are removed (never read today).
- Validation at load (ValueError naming the key): `vix_smoothing_days >= 1`;
  `low_vol.vix_full < low_vol.vix_none <= high_vol.vix_none < high_vol.vix_full`;
  `incumbent_exit_rank` an integer >= max(`num_longs`, `num_shorts`).
- Missing keys: `vix_smoothing_days` 5 and the band edges above; `incumbent_exit_rank`
  defaults to max(`num_longs`, `num_shorts`) (no buffer), so an old config keeps today's
  behaviour. `config.yaml` sets 35.
- Defaults to confirm by the AC9 replay before the first live night: exit rank among 25, 30,
  35, 40; smoothing days among 3, 5, 10.

## Proposed behavior / API
### Public interface
- `factors/regime_weights.py`:
  - `smoothed_vix(vix_df, days) -> float | None`
  - `regime_shares(vix_smoothed, regime_config) -> tuple[float, float]` (high, low)
  - `blend_weights(base, regime_config, high_share, low_share) -> dict[str, float]`
  - `regime_label(high_share, low_share) -> str`
  - `resolve_regime` / `adjust_weights` kept for the per-regime weights (or folded in).
- `factors/loader.py` `_load_vix`: look back 14 calendar days (today 5).
- `run_scoring.py`: use the blend; write `vix_smoothed`, `high_vol_share`, `low_vol_share` to
  `regime_state`, added with `data/db.py` `_add_column_if_missing` (FLOAT, nullable).
- `portfolio/book_selection.py` (new): `select_book(candidates, current, config) ->
  DataFrame` with the selected LONG and SHORT rows plus `side_rank` and `incumbent` (bool).
  Used by `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py` in place of `.head(num)`.
- `optimise(...)` gains a `current: pd.DataFrame` argument (broker positions);
  `run_portfolio.py` already loads positions before prices and the optimiser (packet 63) and
  passes that frame through.
- `portfolio/rebalance.py` `_budget_values`: rotation credit per book.
- `portfolio/churn_replay.py` (new): `python -m portfolio.churn_replay --since 2026-10-27
  [--exit-rank 20,30,35,40] [--smoothing-days 5]`, read-only.

### Inputs / outputs
- Layer 4 summary prints per side: names kept by the buffer (`incumbent` and side rank >
  `num_*`), names closed by reason (beyond exit rank, out of band, flipped).
- `regime_state` gains three nullable FLOAT columns; existing columns unchanged.
- Error handling: invalid config raises ValueError at start (scoring or portfolio). No silent
  fallback for a bad band; missing VIX logs a warning and uses base weights (as today).

### Examples
```python
# exit rank 35, num_longs 20; held long ATO at side rank 85 -> closed (beyond exit rank)
# held long APA at side rank 21 -> kept (incumbent), book still 20 names
# held long VICI now NEUTRAL -> closed (out of band)
# held long XYZ now a SHORT candidate -> closed on the long side
regime_shares(22.48, cfg)   # (0.248, 0.0): weights 75% base, 25% high_vol
regime_label(0.248, 0.0)    # "NORMAL"
```

## Acceptance criteria
- AC1: Per side, `select_book` keeps every held name that is a candidate on the same side
  with side rank <= `incumbent_exit_rank` (best rank first, capped at `num_longs` /
  `num_shorts`), then fills the remaining slots with the best ranked non-held candidates; the
  side has `min(num_*, candidates on that side)` names.
- AC2: A held name with side rank > `incumbent_exit_rank`, a held name that is no longer a
  LONG/SHORT candidate, and a held name that is now a candidate on the other side are not
  incumbents; each gets target 0 on its old side and `generate_trades` closes it (SELL or
  COVER, full quantity).
- AC3: With `incumbent_exit_rank` equal to `num_*` (or the key absent), the selected book,
  weights and trades equal today's top-N output for the same inputs; with an empty book the
  selection is the top N.
- AC4: Side ranks are deterministic: ties in `combined_score` break by ticker ascending, and
  the result does not depend on row order in `candidates` or `current`.
- AC5: Both `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py` select through
  `select_book`; the conviction tilt, earnings haircut, liquidity cap, bounds, sector and beta
  steps then run on the selected book unchanged, and each book still sums to its gross target.
- AC6: Scoring weights are the linear blend of base, high_vol and low_vol weights by shares
  from the mean of the last `vix_smoothing_days` VIX closes (on or before the score date,
  within 14 calendar days), with the band edges read from config; the weights sum to 1; at
  or beyond a band's full edge they equal today's regime weights, and inside the neutral
  zone they equal the base weights.
- AC7: `regime_state` stores `vix_smoothed`, `high_vol_share` and `low_vol_share`, and the
  regime label is HIGH_VOL / LOW_VOL when that share is >= 0.5, else NORMAL; no VIX close in
  the window gives base weights, shares 0, label NORMAL and a warning;
  `regime_conditional_weights: false` gives base weights.
- AC8: In `_budget_values`, per book, opens and adds still charged after the build-up
  allowance are exempt up to that book's charged closure value (highest conviction first);
  closures are still never trimmed, still charged and still ordered first; when the night has
  no closures, budget values equal today's.
- AC9: `python -m portfolio.churn_replay` runs read-only (`SET TRANSACTION READ ONLY`, no
  writes, no broker calls) over the stored history and prints, per variant (today; buffer at
  each exit rank; regime smoothing; buffer plus smoothing; plus rotation credit), name exits
  per night, round trips within 1 and 2 nights, one-way turnover as a fraction of NAV,
  trimmed trade count, gross vs target, and mean side rank of the held book.
- AC10: Invalid config raises ValueError naming the key: `incumbent_exit_rank` below
  `num_*` or not an integer; band edges out of order or overlapping; `vix_smoothing_days`
  below 1.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new; config values are validated (AC10); DB access
  uses SQLAlchemy expressions, no string-built SQL.
- Secrets or credential handling: the replay reads `DATABASE_URL` from `.env` like every
  layer; nothing is logged or printed from it.
- Data exposure or privacy impact: none; the replay prints aggregates and tickers only.
- File system access impact: none beyond existing cache and log paths.
- Network or external service impact: no new calls; the replay never calls Alpaca. Live
  effect: fewer closing and opening orders per night.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: confirm the replay opens a read-only transaction and
  cannot write approvals or positions; confirm Layer 5 and the circuit breaker can still
  close a buffered name.

## Edge cases
- Held name with a tiny position (partial fill, e.g. DE -4 shares): an incumbent like any
  other; its target is the full weight, so Layer 4 adds to it.
- Name entered last night but the order did not fill: not held, not an incumbent; it competes
  on rank as a new name.
- Held count on a side above `num_*`: incumbents capped by rank, the rest closed (AC1).
- Fewer candidates on a side than `num_*`: all candidates selected, incumbents first.
- Incumbent in earnings blackout or capped by liquidity: kept, weight cut by the existing
  steps, as for any book member.
- Halt or circuit breaker active: Layer 5 decides as today; the buffer never blocks a risk
  closure.
- Name dropped from the universe (no `combined_scores` row): out of band, closed (AC2). Its
  closure is valued at its stored close (packet 63), so it charges the budget and earns
  rotation credit at that value. With no stored close, packet 63 logs a WARNING and the price
  is 0, so it charges 0 budget and gives 0 rotation credit.
- VIX gap (holiday, missed ingest): mean of the closes found in the window; one close only is
  allowed with a warning; none gives NORMAL.
- Backfill or rerun of an old score date: same weights as the first run (stateless, AC6).
- `--whatif`: same selection (positions read, nothing written).
- Score date with no positions (fresh book): top N, as today (AC3).

## Rollout
1. One commit per measure, in this order: replay tool (AC9) with today's rules as baseline;
   buffer (AC1-AC5, AC10 part); regime smoothing (AC6, AC7, AC10 part); rotation credit (AC8).
   Rerun the replay after each and record the numbers in the PR draft.
2. Choose `incumbent_exit_rank` and `vix_smoothing_days` from the replay; record the choice in
   `log/changelog-YYYY-MM-DD.md`.
3. Schema: three nullable columns on `regime_state`; no backfill needed (old rows NULL).
4. Revert: set `incumbent_exit_rank` to 20 for the buffer; the regime blend and rotation
   credit revert by commit.

## Verify in paper trading
- Layer 4 log shows buffered counts per side and closure reasons; on a quiet night, closures
  are mostly beyond-exit-rank or out-of-band, not names at rank 21-35.
- Read-only: `SELECT score_date, vix_close, vix_smoothed, high_vol_share, low_vol_share,
  regime FROM regime_state ORDER BY score_date DESC LIMIT 10` shows shares moving gradually.
- Read-only: count full closures of names opened in the previous two nights in
  `position_approvals` (round trips) for two weeks; compare with 2026-10-27 to 2026-11-09.

## Test guidance
- AC1 -> `tests/test_book_selection.py::TestSelectBook::test_incumbent_within_exit_rank_kept`,
  `test_fill_with_best_non_held`, `test_book_size_fixed`
- AC2 -> `TestSelectBook::test_beyond_exit_rank_closed`, `test_neutral_incumbent_closed`,
  `test_flipped_incumbent_closed_on_old_side`,
  `tests/test_rebalance.py::test_buffer_exit_generates_full_closure`
- AC3 -> `TestSelectBook::test_exit_rank_equal_n_matches_top_n`,
  `test_missing_key_matches_top_n`, `test_empty_book_top_n`
- AC4 -> `TestSelectBook::test_tie_break_by_ticker`, `test_row_order_independent`
- AC5 -> `tests/test_optimizer.py::test_select_book_used_and_gross_preserved`,
  `tests/test_mvo_optimizer.py::test_select_book_used`
- AC6 -> `tests/test_regime_weights.py::TestBlend::test_neutral_zone_base_weights`,
  `test_full_edge_equals_regime_weights`, `test_midpoint_blend`, `test_weights_sum_to_one`,
  `test_smoothed_vix_mean_of_last_n`, `test_window_gap_uses_available_closes`
- AC7 -> `tests/test_regime_weights.py::TestLabel::test_label_thresholds`,
  `test_no_vix_normal_with_warning`, `tests/test_regime_state.py::test_regime_state_columns`,
  `test_conditional_weights_off`
- AC8 -> `tests/test_rebalance.py::TestRotationCredit::test_replacement_exempt_up_to_closure`,
  `test_closure_never_trimmed`, `test_no_closures_unchanged`,
  `test_credit_per_book_not_shared`
- AC9 -> `tests/test_churn_replay.py::test_read_only_transaction`,
  `test_metrics_on_fixture_history`, `test_baseline_matches_top_n`
- AC10 -> `tests/test_book_selection.py::test_exit_rank_below_n_raises`,
  `tests/test_regime_weights.py::test_band_order_validated`,
  `test_smoothing_days_validated`

## Open questions
- VIX close for 2026-11-04 is 16.61 between 20.83 (11-03) and 28.18 (11-05). If that print is
  wrong, the 11-05 switch looks larger than it was; check against another source. The 5-day
  mean limits the damage either way.
- Should the buffer also cover band exits (keep a long while composite >= 70)? Deferred until
  the replay counts band exits under regime smoothing (held-name prices exist since packet 63).
- Should the low_vol band be narrower than 12-18? VIX sat at 15-16 for most of October; the
  replay should report low_vol share changes too.

## Decision log
- 2026-11-10: Rank buffer with fixed book size over extra slots or score distance: no resize
  trades from buffering, and sizes behave as today.
- 2026-11-10: Linear blend on a 5-day mean VIX over enter/exit hysteresis: hysteresis would
  not have prevented the 11-05/11-09 jumps, needs stored state, and still jumps in full.
- 2026-11-10: Rotation credit over exempting or trimming closures: counts a rotation once and
  keeps closures never-trimmed (packets 57, 61).
- 2026-11-10: No minimum holding period: overlaps with the buffer and ignores how far a name
  fell.
