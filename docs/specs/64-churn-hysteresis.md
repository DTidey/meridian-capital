# Layer 4 Churn: Incumbent Buffer and Regime Weight Smoothing

**Spec file:** `docs/specs/64-churn-hysteresis.md`
**Spec slug:** churn-hysteresis
**Status:** Implemented (2026-11-10)
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
regime weights; AC8 adds the full replay (see Replay results).

## Scope
In scope:
1. Incumbent buffer in Layer 4 book selection (both optimisers): enter at the top `num_longs`
   / `num_shorts`, keep a held name while it is still a candidate on the same side and its
   side rank is within `portfolio.incumbent_exit_rank`.
2. Regime weight smoothing in Layer 2: blend base and regime weights linearly across a VIX
   band, driven by a trailing mean of VIX closes, for both high_vol and low_vol; read the
   band edges from config; persist the smoothed VIX and blend shares.
3. A read-only replay tool that measures exits, round trips and composite stability for the
   current rules and each change, from the stored score history.
4. Remove the backlog item and point to this spec.

Out of scope / non-goals:
- Rotation credit in the turnover budget (Design 3): a follow-up packet after the buffer and
  smoothing land (Decision 2); backlog "Rotation credit in the turnover budget".
- Minimum holding period (see Design 4: rejected for now).
- Buffering names that leave the candidate band (NEUTRAL). They are closed as today; see
  Design 1 and Decision 3. The replay counts band exits separately so the follow-up can be
  decided from data; backlog "Buffer for band exits".
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
  score date, looked up within max(14, 2 x days) calendar days (holidays and gaps). Fewer
  closes than `vix_smoothing_days` but at least one: mean of those, with a warning. None:
  NORMAL (both shares 0), with a warning, as today.
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

### 3. Turnover budget and closures (deferred: follow-up packet, Decision 2)
Not implemented in this packet. The analysis below is kept as the starting point for the
follow-up; `portfolio/rebalance.py` is unchanged here.

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
Not added. If the AC8 replay at the chosen exit rank still shows round trips within 2 nights,
a follow-up packet can add `min_hold_nights` that blocks rank exits only (never band exits,
flips, Layer 5 or circuit-breaker closures).

How the three measures fit together, with no double dampening:
- Regime smoothing acts on the score input: fewer rank shocks.
- The buffer acts on book membership: absorbs the rank noise that remains.
- The rotation credit (follow-up packet) would act on trade sizing: it lets the replacements
  for real exits through, so it loosens rather than adds damping.
- The exit rank is chosen with smoothing in place (AC8 replay), so the two dampers are tuned
  together rather than stacked. No exit rank gets 2-night round trips to zero (40 still has
  59 over 120 nights), so the rank is set at the elbow of the curve (Replay results).

## Config keys
```yaml
scoring:
  regime_weights:
    vix_smoothing_days: 5     # trailing VIX closes averaged before blending
    low_vol:
      vix_full:  13           # smoothed VIX <= 13: full low_vol weights
      vix_none:  17           # smoothed VIX >= 17: base weights
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
- `low_vol.vix_below` and `high_vol.vix_above` are removed (never read before this packet).
- Validation at load (ValueError naming the key): `vix_smoothing_days` an integer >= 1;
  `low_vol.vix_full < low_vol.vix_none <= high_vol.vix_none < high_vol.vix_full`;
  `incumbent_exit_rank` an integer >= max(`num_longs`, `num_shorts`).
- Missing keys: `vix_smoothing_days` 5, band edges low 12/18 and high 20/30 (code defaults,
  the spec's provisional values); `incumbent_exit_rank` defaults to max(`num_longs`,
  `num_shorts`) (no buffer), so an old config keeps the top-N selection.
- `config.yaml` sets 35, 5 and the low_vol band 13-17, chosen from the replay (Replay
  results, Decision 4). The high_vol band 20-30 is not tuned here (one episode in the
  history), it keeps the spec value.
- VIX lookup window: the last `vix_smoothing_days` closes within max(14, 2 x days) calendar
  days of the score date (14 days at the chosen 5).

## Proposed behavior / API
### Public interface
- `factors/regime_weights.py`:
  - `smoothed_vix(vix_df, days, score_date) -> float | None` (closes after `score_date`
    ignored)
  - `regime_shares(vix_smoothed, regime_config) -> tuple[float, float]` (high, low)
  - `blend_weights(base, regime_config, high_share, low_share) -> dict[str, float]`
  - `regime_label(high_share, low_share) -> str`
  - `resolve(vix_df, base, scoring_cfg, score_date) -> dict` (weights, regime, vix_close,
    vix_smoothed, high_vol_share, low_vol_share); `validate(regime_config)`.
  - `adjust_weights` kept for the per-regime weights; the hard-coded `resolve_regime` is
    removed.
- `factors/loader.py` `_load_vix`: look back max(14, 2 x `vix_smoothing_days`) calendar days
  (was 5).
- `run_scoring.py`: validates the regime config at start, uses the blend; writes
  `vix_smoothed`, `high_vol_share`, `low_vol_share` to `regime_state`, added with `data/db.py`
  `_add_column_if_missing` (FLOAT, nullable).
- `portfolio/book_selection.py` (new): `select_book(candidates, current, config) ->
  DataFrame` with the selected LONG and SHORT rows plus `side_rank` and `incumbent` (bool);
  `classify_exits(...)` gives each held name that is not kept its reason (`beyond_exit_rank`,
  `capped`, `out_of_band`, `flipped`); `exit_rank(config)` validates the key. Used by
  `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py` in place of `.head(num)`.
- `optimise(...)` (both optimisers) gains `current: pd.DataFrame | None = None` (broker
  positions); `run_portfolio.py` passes the positions it already loads before pricing
  (packet 63) and validates `incumbent_exit_rank` at start.
- `portfolio/churn_replay.py` (new, CLI and read-only loading) and `portfolio/churn_sim.py`
  (new, pure rescoring and book simulation): `python -m portfolio.churn_replay --since
  2026-10-27 [--until 2026-11-09] [--exit-ranks 20,25,30,35,40] [--smoothing-days 1,3,5,10]
  [--low-bands 12-18,13-17,14-16]`.

### Inputs / outputs
- Layer 4 logs per side: names kept by the buffer (`incumbent` and side rank > `num_*`),
  held names closed by reason (beyond exit rank, capped, out of band, flipped).
- `regime_state` gains three nullable FLOAT columns; existing columns unchanged.
- Error handling: invalid config raises ValueError at start (scoring or portfolio). No silent
  fallback for a bad band; missing VIX logs a warning and uses base weights (as today).

### Examples
```python
# exit rank 35, num_longs 20; held long ATO at side rank 85 -> closed (beyond exit rank)
# held long APA at side rank 21 -> kept (incumbent), book still 20 names
# held long VICI now NEUTRAL -> closed (out of band)
# held long XYZ now a SHORT candidate -> closed on the long side
regime_shares(22.24, cfg)   # 2026-11-09 5-day mean: (0.224, 0.0), 78% base, 22% high_vol
regime_label(0.224, 0.0)    # "NORMAL"
```

## Acceptance criteria
- AC1: Per side, `select_book` keeps every held name that is a candidate on the same side
  with side rank <= `incumbent_exit_rank` (best rank first, capped at `num_longs` /
  `num_shorts`), then fills the remaining slots with the best ranked remaining candidates; the
  side has `min(num_*, candidates on that side)` names.
- AC2: A held name with side rank > `incumbent_exit_rank`, a held name that is no longer a
  LONG/SHORT candidate, and a held name that is now a candidate on the other side are not
  incumbents; each gets target 0 on its old side and `generate_trades` closes it (SELL or
  COVER, full quantity).
- AC3: With `incumbent_exit_rank` equal to `num_*` (or the key absent), the selected book,
  weights and trades equal the top-N output for the same inputs; with an empty book the
  selection is the top N.
- AC4: Side ranks are deterministic: ties in `combined_score` break by ticker ascending, and
  the result does not depend on row order in `candidates` or `current`.
- AC5: Both `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py` select through
  `select_book`; the conviction tilt, earnings haircut, liquidity cap, bounds, sector and beta
  steps then run on the selected book unchanged, and each book still sums to its gross target.
- AC6: Scoring weights are the linear blend of base, high_vol and low_vol weights by shares
  from the mean of the last `vix_smoothing_days` VIX closes (on or before the score date,
  within max(14, 2 x days) calendar days), with the band edges read from config; the weights
  sum to 1; at or beyond a band's full edge they equal the regime's weights, and inside the
  neutral zone they equal the base weights.
- AC7: `regime_state` stores `vix_smoothed`, `high_vol_share` and `low_vol_share`, and the
  regime label is HIGH_VOL / LOW_VOL when that share is >= 0.5, else NORMAL; no VIX close in
  the window gives base weights, shares 0, label NORMAL and a warning;
  `regime_conditional_weights: false` gives base weights.
- AC8: `python -m portfolio.churn_replay` runs read-only (`SET TRANSACTION READ ONLY` on
  PostgreSQL, `PRAGMA query_only` on SQLite, SELECT only, no broker calls) over the stored
  history and prints, per scoring variant (stored scores; the hard switch recomputed; the
  blend for each smoothing-days and low_vol band setting) and per exit rank: name exits
  (total and per night), exits by reason (rank, band, flip), round trips within 1 and 2
  nights, idealised one-way turnover per night, mean side rank of the held book and top-10
  names left out per side-night; and per scoring variant the mean and minimum day-over-day
  Spearman of the composite and the blend shares' largest one-night change.
- AC9: Invalid config raises ValueError naming the key, before any database work:
  `incumbent_exit_rank` below `num_*` or not an integer; band edges out of order or
  overlapping; `vix_smoothing_days` not an integer >= 1.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new; config values are validated (AC9); DB access
  uses SQLAlchemy expressions, no string-built SQL.
- Secrets or credential handling: the replay reads `DATABASE_URL` from `.env` like every
  layer; nothing is logged or printed from it.
- Data exposure or privacy impact: none; the replay prints aggregates only.
- File system access impact: none beyond existing cache and log paths.
- Network or external service impact: no new calls; the replay never calls Alpaca. Live
  effect: fewer closing and opening orders per night.
- Schema: three nullable FLOAT columns on `regime_state`, added by `initialise_schema` on the
  next run (idempotent `ADD COLUMN`); no existing row is rewritten.
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
  closure is valued at its stored close (packet 63). With no stored close, packet 63 logs a
  WARNING and the price is 0, so it charges 0 budget.
- VIX gap (holiday, missed ingest): mean of the closes found in the window; one close only is
  allowed with a warning; none gives NORMAL.
- Backfill or rerun of a score date scored after this packet: same weights as the first run
  (stateless, AC6). A rerun of a date scored before this packet gives the blend, not the
  stored hard-switch weights: already stored scores are not rescored by this packet, and
  rerunning old dates is not part of the nightly run.
- `--whatif`: same selection (positions read, nothing written).
- Score date with no positions (fresh book): top N, as today (AC3).

## Replay results
`python -m portfolio.churn_replay` against production (read-only), 2026-11-10. Idealised
20/20 book, equal weight, full fills. "Stored" uses the stored `combined_scores`; "switch"
recomputes Layer 2 and the Layer 3 side bands from the stored factor and AI scores with the
stored regime's weights; "blend Nd low a-b" uses the blend (high_vol band 20-30).

Check: over 2026-10-27 to 2026-11-09 the recomputed switch reproduces the stored results
exactly (every metric equal), so the rescoring matches Layers 2 and 3. Before late October
the stored `combined_scores` follow the pre-packet-43 rules (1655 exits and 387 band exits
over 120 nights, against 1043 and 72 recomputed), so for the long window the recomputed
switch is the baseline.

Short window, 2026-10-27 to 2026-11-09 (9 nights; VIX 16.6 to 28.2):

| Scoring | Exit rank | Exits | Rank / band exits | Round trips 1 / 2 nights | Turnover / night | Mean side rank | Top-10 left out |
|---|---|---|---|---|---|---|---|
| stored (today) | 20 | 92 | 80 / 12 | 38 / 61 | 38.5% | 10.5 | 0.00 |
| stored | 35 | 41 | 34 / 7 | 14 / 18 | 17.2% | 12.0 | 0.15 |
| blend 5d low 13-17 | 20 | 87 | 83 / 4 | 42 / 60 | 36.8% | 10.5 | 0.00 |
| blend 5d low 13-17 | 30 | 44 | 41 / 3 | 15 / 22 | 18.4% | 11.4 | 0.05 |
| **blend 5d low 13-17** | **35** | **32** | **29 / 3** | **11 / 14** | **13.5%** | **12.1** | **0.15** |
| blend 5d low 13-17 | 40 | 26 | 23 / 3 | 7 / 9 | 11.1% | 13.2 | 0.40 |
| blend 3d low 13-17 | 35 | 34 | 30 / 4 | 11 / 15 | 14.3% | 12.1 | 0.15 |
| blend 10d low 13-17 | 35 | 33 | 30 / 3 | 10 / 15 | 13.8% | 12.1 | 0.15 |

Long window, 2026-05-05 to 2026-11-09 (120 nights; 5-day mean VIX 14.8 to 22.5):

| Scoring | Exit rank | Exits | Rank / band exits | Round trips 1 / 2 nights | Turnover / night | Mean side rank | Top-10 left out |
|---|---|---|---|---|---|---|---|
| switch (today's rules) | 20 | 1043 | 971 / 72 | 484 / 645 | 32.7% | 10.5 | 0.00 |
| switch | 35 | 373 | 314 / 59 | 58 / 88 | 11.7% | 12.4 | 0.34 |
| blend 5d low 13-17 | 20 | 1022 | 954 / 68 | 466 / 628 | 32.0% | 10.5 | 0.00 |
| blend 5d low 13-17 | 25 | 659 | 598 / 61 | 195 / 285 | 20.7% | 10.9 | 0.03 |
| blend 5d low 13-17 | 30 | 471 | 411 / 60 | 95 / 141 | 14.8% | 11.6 | 0.20 |
| **blend 5d low 13-17** | **35** | **358** | **300 / 58** | **47 / 77** | **11.2%** | **12.4** | **0.31** |
| blend 5d low 13-17 | 40 | 304 | 239 / 65 | 40 / 59 | 9.5% | 13.2 | 0.47 |
| blend 5d low 12-18 | 35 | 365 | 310 / 55 | 54 / 84 | 11.4% | 12.4 | 0.29 |
| blend 5d low 14-16 | 35 | 360 | 302 / 58 | 52 / 80 | 11.3% | 12.4 | 0.32 |
| blend 3d low 13-17 | 35 | 360 | 301 / 59 | 49 / 76 | 11.3% | 12.4 | 0.32 |
| blend 10d low 13-17 | 35 | 376 | 322 / 54 | 49 / 86 | 11.8% | 12.3 | 0.28 |

No flip exits in any variant. Composite stability and blend shares:

| Scoring | Spearman mean / min, short window | Spearman mean / min, long window | Largest one-night share change (high / low), long | Nights with low share > 0 (of 120), mean low share |
|---|---|---|---|---|
| stored / switch | 0.947 / 0.802 | 0.967 / 0.802 | 1.0 / 1.0 (hard switch) | 14 LOW_VOL nights |
| blend 1d low 13-17 | 0.954 / 0.822 | 0.970 / 0.815 | 0.82 / 0.49 | 79, 0.21 |
| blend 3d low 13-17 | 0.963 / 0.876 | 0.971 / 0.815 | 0.19 / 0.32 | 74, 0.20 |
| blend 5d low 13-17 | 0.963 / 0.879 | 0.971 / 0.815 | 0.12 / 0.29 | 69, 0.19 |
| blend 10d low 13-17 | 0.963 / 0.882 | 0.971 / 0.815 | 0.02 / 0.16 | 67, 0.17 |
| blend 5d low 12-18 | 0.963 / 0.879 | 0.971 / 0.817 | 0.12 / 0.36 | 99, 0.24 |
| blend 5d low 14-16 | 0.963 / 0.879 | 0.971 / 0.815 | 0.12 / 0.35 | 45, 0.13 |

The long-window minimum (0.815, 2026-05-09) is a factor-data night with base weights on
both days, not a regime switch; the blend
removes the 0.802 regime night. Share changes in the long window include a 7-day gap in the
score history (2026-05-14 to 05-21) and nights with no `combined_scores` row.

### Chosen settings
- `incumbent_exit_rank: 35`. The buffer does most of the work: with the blend in place,
  2-night round trips fall 628 -> 285 -> 141 -> 77 -> 59 for exit ranks 20/25/30/35/40 over
  120 nights. 35 is the elbow: 30 -> 35 removes 64 round trips and 113 exits, 35 -> 40 only
  18 and 54, while the top-10 names left out rise from 0.31 to 0.47 per side-night and the
  mean held rank from 12.4 to 13.2. Short window: exits 92 -> 32, round trips 61 -> 14.
  Round trips do not reach zero at any tested rank, so this is the elbow rather than the
  spec's "near zero" target.
- `vix_smoothing_days: 5`. Churn is the same for 3, 5 and 10 days (within noise at every
  exit rank). 5 days keeps the largest one-night high_vol share change at 0.12 (3 days:
  0.19; 1 day: 0.82, about as jumpy as today). 10 days barely reacts: over the 11-05 spike
  its mean high_vol share is 0.005, so it would in effect switch the high_vol tilt off.
- low_vol band 13-17 (not 12-18). Churn is within noise across 12-18, 13-17 and 14-16
  (round trips 84 / 77 / 80 at exit 35). The band changes how often the low_vol tilt is on:
  VIX sat at 15-17 for most of the summer, so with 12-18 the tilt is partly on 99 of 120
  nights (mean share 0.24), a standing change to the strategy's calm-market weights rather
  than smoothing. 13-17 keeps today's 15 boundary as its centre, is on 69 nights (mean
  0.19) and has the smallest largest one-night change (0.29); 14-16 is steeper (0.35).
- Band exits: about one in six exits at the chosen settings (58 of 358 over 120 nights; 3 of
  32 in the short window), against 12 of 92 today in the short window. The blend removes most
  of the regime-driven band exits (short window, exit 20: 12 -> 4). Decision 3's follow-up
  can use these counts.

## First night after merge
- Scoring for already-stored dates is not rewritten; the change starts with the next nightly
  run after merge.
- Layer 2: regime weights blend instead of switching. If the next run were scored on the
  2026-11-09 data, the 5-day mean is 22.24, high_vol share 0.22, label NORMAL: momentum 0.177
  (base 0.20), quality 0.215, value 0.163, the rest scaled by 0.99. Composite Spearman
  against the stored 11-09 composite (base weights) is 0.995; 9 names leave the LONG/SHORT
  candidate lists and 10 enter. So composite values shift once, by a small amount, on the
  first night; the share then moves by at most about 0.12 a night. The 11-05 and 11-06
  closes (28.2, 25.6) leave the 5-day window on the 11-13 score date; after that the share
  is 0 unless the mean of the new closes is above 20.
- `regime_state` gains three columns on the first run (old rows NULL).
- Layer 4: the buffer applies to the broker book from the first run. With the 11-09 scores
  and the current broker book (35 names), today's rule closes 12 held names; the buffer at
  35 closes 10 with the stored scores and 9 with the blend (7 beyond rank 35, 2 out of
  band), keeping 4 names at side rank 21-35. Fewer closures mean fewer replacement opens,
  so the turnover budget trims less.

## Rollout
1. Commits in order: buffer (AC1-AC5, AC9 part); regime blend (AC6, AC7, AC9 part); replay
   tool (AC8); chosen config values and packet docs. The replay reuses `select_book` and the
   blend functions, so it lands after them (the spec's order put it first; the numbers were
   produced before the config values were committed).
2. `incumbent_exit_rank`, `vix_smoothing_days` and the low_vol band are set from the replay
   (Replay results); reasoning in `log/changelog-2026-11-10.md`.
3. Schema: three nullable columns on `regime_state`; no backfill (old rows NULL).
4. Revert: set `incumbent_exit_rank` to 20 for the buffer; the regime blend reverts by commit
   (or `regime_conditional_weights: false` for base weights only).
5. Follow-ups (backlog): rotation credit in the turnover budget; buffer for band exits.

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
  `test_missing_key_matches_top_n`, `test_empty_book_top_n`,
  `tests/test_optimizer.py::test_no_current_matches_top_n`
- AC4 -> `TestSelectBook::test_tie_break_by_ticker`, `test_row_order_independent`
- AC5 -> `tests/test_optimizer.py::test_select_book_used_and_gross_preserved`,
  `tests/test_mvo_optimizer.py::test_select_book_used`
- AC6 -> `tests/test_regime_weights.py::TestBlend::test_neutral_zone_base_weights`,
  `test_full_edge_equals_regime_weights`, `test_midpoint_blend`, `test_weights_sum_to_one`,
  `test_smoothed_vix_mean_of_last_n`, `test_window_gap_uses_available_closes`
- AC7 -> `tests/test_regime_weights.py::TestLabel::test_label_thresholds`,
  `test_no_vix_normal_with_warning`, `test_conditional_weights_off`,
  `tests/test_regime_state.py::test_regime_state_columns`
- AC8 -> `tests/test_churn_replay.py::test_read_only_transaction`,
  `test_metrics_on_fixture_history`, `test_baseline_matches_top_n`
- AC9 -> `tests/test_book_selection.py::TestExitRankConfig::test_exit_rank_below_n_raises`,
  `test_exit_rank_not_integer_raises`,
  `tests/test_regime_weights.py::TestRegimeConfig::test_band_order_validated`,
  `test_smoothing_days_validated`,
  `tests/test_regime_state.py::test_bad_band_stops_scoring_before_db`,
  `tests/test_mvo_optimizer.py::test_bad_exit_rank_raises_before_fallback`

## Decisions
David, 2026-11-10, on the open questions of the merged spec (#132):
1. The VIX close of 16.61 on 2026-11-04 is real: FRED VIXCLS and CBOE history agree, and VIX
   ran 16.6 -> 28.2 on 11-05. No data fix.
2. Rotation credit (turnover budget exemption for opens and adds up to the book's closure
   value; former Scope item 3, Design 3 and AC8) is out of this packet: a separate follow-up
   packet after the buffer and smoothing land. The ACs were renumbered AC1-AC9 (old AC9
   replay -> AC8, old AC10 config -> AC9). Backlog: "Rotation credit in the turnover budget".
3. No buffer for names that leave the candidate band (turn NEUTRAL) for now: deferred until
   the replay counts those exits. The replay reports band exits separately from rank exits
   (AC8; Replay results). Backlog: "Buffer for band exits".
4. The low_vol band, the incumbent exit rank and the number of VIX smoothing days are set
   from the replay, not from the provisional 12-18, 35 and 5: chosen 13-17, 35 and 5 (Replay
   results, Chosen settings), in `config.yaml`.

## Decision log
- 2026-11-10: Rank buffer with fixed book size over extra slots or score distance: no resize
  trades from buffering, and sizes behave as today.
- 2026-11-10: Linear blend on a 5-day mean VIX over enter/exit hysteresis: hysteresis would
  not have prevented the 11-05/11-09 jumps, needs stored state, and still jumps in full.
- 2026-11-10: Rotation credit over exempting or trimming closures: counts a rotation once and
  keeps closures never-trimmed (packets 57, 61). Deferred to a follow-up packet (Decision 2).
- 2026-11-10: No minimum holding period: overlaps with the buffer and ignores how far a name
  fell.
- 2026-11-10: Exit rank 35, 5 smoothing days, low_vol band 13-17, from the replay (Chosen
  settings).
- 2026-11-10: The replay recomputes combined scores with the current Layer 2/3 code from the
  stored factor and AI scores, so variants are compared under one set of rules; checked
  against the stored scores on 2026-10-27 to 11-09 (identical).
