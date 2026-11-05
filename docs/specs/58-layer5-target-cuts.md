# Layer 5 Target Cuts: Tail Risk as a Book Gross Scale, One Owner for Earnings

**Spec file:** `docs/specs/58-layer5-target-cuts.md`
**Spec slug:** layer5-target-cuts
**Status:** Draft (phase 1: awaiting owner decisions D1-D3 before implementation)
**Owner:** DTidey
**Date:** 2026-11-05

## Problem statement
The investigation (`docs/investigations/2026-11-05-underinvested-book.md`, PR #126, findings
2, 4 and 5) shows that Layer 5 shrinks the trades that survive the turnover budget, and does so
on the **target position** of only the names that trade that night:

- Earnings blackout: `risk/pre_trade.py` check 2 sets `target = round(target x 0.5)` for any
  opening trade (BUY/SHORT) with earnings within +/-5 calendar days, then returns early, so the
  liquidity, size, sector, gross/net, beta and correlation checks are skipped for that row.
  It returns `APPROVED`, so `run_risk_check.py` never counts it as `REDUCED`.
- Tail risk: `risk/tail_risk.py` `_apply_reduce_gross` runs after pre-trade
  (`run_risk_check.py`) and sets `target = round(target x 0.8)` for every APPROVED BUY/SHORT
  row in CAUTION (`REDUCE_GROSS_20`). Blackout names end at 0.4x the Layer 4 target.
- CAUTION fired on every run from 10-26 to 11-04, always on credit z (1.32-2.70 against
  `credit_spread_sigma: 1.0`), with VIX 16.6-21.2 (below 25). Packet 59 covers the z-score.
- Because the cut applies to the whole target of a traded name, an add can turn into a
  reduction: 11-04 NKE cur -41, Layer 4 target -50 (SHORT 9) became target -40 (COVER 1);
  CLX, AKAM and CF also flipped (4 of 16 trades). Untraded positions keep 100% of their size,
  so REDUCE_GROSS_20 does not cut book gross by 20%; it shrinks whichever names trade, and
  Layer 4 plans the full target again the next night (CLX: SHORT 43, COVER, COVER, COVER,
  SHORT 7, COVER 3 over six nights).
- On 11-04 this cut the submitted value from $28.5k to $16.1k (-44%).
- Earnings are cut three times with two windows: the Layer 4 optimiser halves the weight of
  names reporting in `[score_date, score_date + 5d]` and renormalises the book
  (`portfolio/optimizer.py` `_earnings_haircut`, spec 04 AC3); pre-trade halves the target
  again over `+/-5d` (spec 05 AC3); then reduce-gross takes 0.8x. AXON 11-04: optimiser weight
  1.7% (-4 shares), pre-trade -2, tail -2.

Spec drift: spec 05 contradicts itself (check table: "full position blocked"; note below it:
"delta_shares halved"; AC3: "resizes the trade to 50% of target shares").

Goal: a tail-risk regime reduces the whole book's gross consistently and stably; earnings
risk is handled once, with one window; Layer 5 never turns a planned add into a reduction.

## Design choices (owner decision needed)

### D1. How tail-risk REDUCE_GROSS is applied
- **Option A (recommended): book-level gross scale in Layer 4.** Layer 4 evaluates the
  tail-risk regime (read-only, same thresholds and data as today) and scales
  `target_long_gross` and `target_short_gross` by `1 - reduction` (CAUTION 0.8: 150% -> 120%,
  net 10% -> 8%; STRESS 0.5: 75%). The optimiser builds the whole book at the reduced gross,
  so every position, traded or not, honours the cut, and the next night plans the same reduced
  target (no churn). Layer 5 tail risk keeps evaluating and logging the regime but no longer
  edits approvals. Trade-off: the regime is evaluated before Layer 5 runs; entering CAUTION
  with a full book trims every position by 20% (that is the intent of the control).
- Option B: scale the opening **delta** in Layer 5 (`new_delta = sign(delta) x
  floor(|delta| x 0.8)`, never past current holdings). Smallest change and never flips, but
  REDUCE_GROSS then only slows the build: the book still converges to 150% over a few nights,
  so the control no longer limits gross at all.
- Option C: keep Layer 5 as the owner but make it act on the whole book (append reduction rows
  for untraded positions). Correct gross, but duplicates Layer 4 sizing inside Layer 5 and
  still fights Layer 4 every night unless Layer 4 also knows the regime.

### D2. Who owns the earnings haircut, and which window
- **Option A (recommended): Layer 4 only, forward window.** Keep the optimiser haircut (weight
  x0.5 for earnings in `[score_date, score_date + earnings_blackout_days]`, surplus
  redistributed within the book; spec 04 AC3 unchanged). Pre-trade check 2 no longer resizes:
  it approves the row only if checks 3-8 pass and records `EARNINGS_WINDOW` in the reason for
  visibility. The window is forward-only because the risk is the announcement; once reported,
  the news is in the price (CLX was still cut on 11-04 after reporting on 11-02).
- Option B: pre-trade only. Remove the optimiser haircut; pre-trade blocks *new* opens in the
  window and freezes adds to held names (delta 0); existing positions untouched. Keeps the
  control in the risk layer, but leaves gross below target (no renormalisation) and blocks
  rather than halves.
- Option C: keep both, but pre-trade scales only the opening delta (never flips) and returns
  `REDUCED`. Smallest code change; still a double haircut and two windows.

### D3. Packet 56's assumption that a resize may turn an add into a reduction
Packet 56 (`docs/specs/56-resize-trade-direction.md`, Assumptions) states "a resize that turns
an opening trade into a reduction is wanted", and
`tests/test_risk_tail_risk.py::TestReduceGrossAction::test_cut_below_holding_sells` pins it.
Under D1-A and D2-A no Layer 5 stage resizes an approval any more, so the assumption has no
remaining user in pre-trade or tail risk; the tests that pin it are removed with
`_apply_reduce_gross`. A Layer 4 target that is lower than the holding (for example NKE at -40
under CAUTION) is a plain target change and still produces a reduction: the book is meant to
be at 120% gross. **Recommended:** mark packet 56's assumption superseded for pre-trade and
tail risk; keep packet 56's `map_action` re-derivation (still used by circuit-breaker
SIZE_DOWN) and its executor guards unchanged.

## Scope
In scope:
- `risk/tail_risk.py`: split the regime evaluation out of `run_tail_risk` into
  `evaluate_tail_risk(conn, score_date, config, cache_dir) -> dict` (no database writes);
  `run_tail_risk` calls it, logs the event as today, and no longer calls
  `_apply_reduce_gross` (removed).
- `run_portfolio.py`: before optimising, call `evaluate_tail_risk` and pass the optimiser a
  config whose `target_long_gross`, `target_short_gross` and `gross_exposure` are scaled by
  `1 - reduction`; print the regime and the gross target used.
- `risk/pre_trade.py` check 2: no resize; the row continues through checks 3-8; an approved
  row in the window carries `EARNINGS_WINDOW` in its reason.
- `risk/pre_trade.py` `_load_blackout_tickers`: forward window to match the optimiser (only
  needed for the reason tag).
- Docs: spec 05 AC3, AC5 and the check-2 text restated; spec 04 gains a note on the gross
  scale; packet 56 Assumptions gains a "superseded by 58" line; `docs/backlog.md` item
  "Earnings blackout halves the whole target, and REDUCED is never counted" removed.

Out of scope / non-goals:
- Credit z-score lookback and threshold (packet 59).
- Circuit-breaker SIZE_DOWN_30 (`risk/circuit_breakers.py` `_apply_size_down`): it has the
  same "scale only tonight's targets" shape but is a loss response, has not fired in paper
  trading, and is a separate decision. Added to `docs/backlog.md`.
- Pre-trade `POSITION_SIZE` rejects caused by whole-share rounding just above 5% (MU, ARE, BA
  between 10-30 and 11-03): backlog.
- The advisory 2-day earnings warnings in `portfolio/rebalance_schedule.py` (spec 04 AC9).
- Fail-open behaviour when VIX or credit data is missing (state NORMAL, unchanged).

## Assumptions
- VIX and the credit series are available when Layer 4 runs: Layer 1 has already loaded
  `^VIX`/HYG prices, and the FRED call is the same one Layer 5 makes minutes later.
- Scaling long and short gross by the same factor keeps net within every configured bound
  (net 0.10 x 0.8 = 0.08 and x 0.5 = 0.05, inside `net_exposure_min/max` and pre-trade
  `net_min/max`). `book_targets` validation runs on the scaled values.
- The earnings haircut and the gross scale are independent and multiply on the weight (a
  blackout name in CAUTION gets 0.5 of its weight in a book at 0.8 gross). This is consistent
  across the whole book and stable night to night, unlike today's per-trade compounding.

## Proposed behavior / API
### Public interface
- `risk.tail_risk.evaluate_tail_risk(conn, score_date, config, cache_dir) -> dict` with keys
  `tail_risk_state`, `action`, `reduction_pct`, `vix`, `credit_spread_z`.
- `run_tail_risk(...)` return value unchanged except `actions` no longer reports modified
  approval rows.
- `run_portfolio.py` output: `Tail risk: CAUTION (credit z 2.42) - gross target 120% (L 64% /
  S 56%)`.

### Examples
```python
# 11-04, CAUTION. Layer 4 target book at 120% gross.
# NKE cur -41: Layer 4 target -40 -> COVER 1 tonight, HOLD on later nights (no re-short).
# AXON (earnings 11-05): optimiser weight halved once; pre-trade approves as planned.
# Layer 5 leaves every APPROVED row as Layer 4 wrote it.
```

## Acceptance criteria
- AC1: `evaluate_tail_risk` returns the same state, action and reduction as `run_tail_risk`
  for the same inputs (NORMAL / CAUTION by VIX / CAUTION by credit z / STRESS) and writes
  nothing to the database.
- AC2: Layer 4 scales the long and short gross targets by `1 - reduction_pct`: the target book
  sums to 150% in NORMAL, 120% in CAUTION (64% / 56%) and 75% in STRESS (40% / 35%).
- AC3: Layer 5 tail risk modifies no `position_approvals` row in any state; it still records
  the regime event and prints the state.
- AC4: Under CAUTION, with Layer 4's approvals fully filled, a second run on unchanged inputs
  produces no non-HOLD trades (no night-to-night churn from the regime).
- AC5: No Layer 5 stage (pre-trade, tail risk) changes the action or size of an APPROVED
  opening row; 11-04 regression: NKE, CLX, AKAM and CF keep the actions Layer 4 planned.
- AC6: Pre-trade check 2 no longer resizes; an opening trade for a ticker with earnings in
  `[score_date, score_date + earnings_blackout_days]` runs checks 3-8 and, if approved,
  carries `EARNINGS_WINDOW` in its reason. A ticker that reported before `score_date` is not
  tagged.
- AC7: The optimiser earnings haircut is unchanged (spec 04 AC3 tests still pass).
- AC8: In `--whatif`, Layer 4 evaluates the regime, prints it, and writes nothing.
- AC9: Spec 05 AC3/AC5 and check-2 text, spec 04 and packet 56's Assumptions describe the
  shipped behaviour; the backlog item is removed and the SIZE_DOWN and POSITION_SIZE items
  are added.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; FRED/VIX inputs are numeric and already parsed by
  the existing code.
- Secrets or credential handling: `FRED_API_KEY` is now also read in Layer 4 (same code path,
  same environment variable); it must not appear in logs (the existing call passes it only as
  a request parameter and logs no URL).
- Data exposure or privacy impact: none.
- File system access impact: the FRED cache parquet is written once more per night (same path).
- Network or external service impact: one extra FRED request per night (Layer 4); Layer 5's
  request is unchanged. Alpaca orders: fewer Layer 5 edits; the book-level cut can produce
  reduction orders for every held name when the regime tightens (bounded by the regime's
  scale, never more than the position).
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: removing the pre-trade resize means blackout names now
  pass through checks 3-8 (stricter, not looser). Confirm `book_targets` still rejects a
  scaled book outside the pre-trade gross/net limits.

## Edge cases
- Regime changes between Layer 4 and Layer 5 (FRED call fails in one and falls back to HYG):
  Layer 5 logs a different state but changes nothing; Layer 4's book stands. Logged at
  WARNING when Layer 5's state differs from the one Layer 4 printed is out of scope (no
  shared record); visible by comparing the two log lines.
- STRESS with a full book: reductions of 50% across all names; with packet 57 the reductions
  are not trimmed by the rotation budget (de-risking allowance).
- A blackout name that is a closure or reduction: unaffected (closing trades already skip
  checks).
- Earnings on `score_date` itself: inside the forward window (inclusive), as the optimiser
  does today.

## Rollout
1. Land after packet 59 (so the regime is computed on the corrected window) and before or
   with packet 57.
2. First night: run `python run_portfolio.py --whatif` by hand; check the regime line and that
   the target book sums to the expected gross.
3. Revert is a code revert; no schema or config change.

## Verify in paper trading
- Layer 4 log prints the regime and gross target; `target_portfolio` weights sum to it.
- Layer 5 log: `tail_risk: ... modified N trades` no longer appears; pre-trade `REJECTED`
  counts may rise slightly for earnings names (now fully checked).
- `position_approvals` APPROVED rows match Layer 4's planned action and size for every opening
  trade (read-only query comparing `created_at` values with `reviewed_at` edits).
- Over a week: no name alternates add/reduce on consecutive nights because of the regime
  (compare `execution_orders` actions per ticker).

## Test guidance
- AC1 -> `tests/test_risk_tail_risk.py::TestEvaluateTailRisk` (parametrised over the four
  regimes; asserts no approval rows change)
- AC2 -> `tests/test_run_portfolio.py::TestTailRiskGrossScale::test_caution_scales_book`,
  `test_stress_scales_book`, `test_normal_full_gross`
- AC3 -> `tests/test_risk_tail_risk.py::TestNoApprovalEdits::test_caution_leaves_rows`,
  `test_stress_leaves_rows` (replaces `TestReduceGrossAction`, `test_vix_caution_reduces_20pct`,
  `test_vix_stress_reduces_50pct`)
- AC4 -> `tests/test_run_portfolio.py::TestTailRiskGrossScale::test_second_night_no_churn`
- AC5 -> `tests/test_layer5_regression_2026_11_04.py::test_planned_actions_survive_layer5`
- AC6 -> `tests/test_risk_pre_trade.py::TestEarningsWindow::test_no_resize_reason_tagged`,
  `test_window_name_still_size_checked`, `test_reported_yesterday_not_tagged`
  (replaces `TestPreTradeEarningsBlackout`, `TestBlackoutResizeAction`)
- AC7 -> existing `tests/test_optimizer.py` earnings haircut tests
- AC8 -> `TestTailRiskGrossScale::test_whatif_no_writes`
- AC9 -> review of the spec and backlog diffs (documentation AC)

## Decision log
- 2026-11-05: Spec written from the 2026-11-05 investigation. The double cut was first
  described in session 284a3bba (2026-10-30T07:34Z). The "reversal is correct" framing came
  from the assistant's report; the owner's reply was "Please cancel the orders and proceed
  with packet 56" (07:57Z), so the flip assumption was approved implicitly, not stated. In the
  2026-11-04 review (f1142635, 11:56-11:59Z) the assistant proposed "halve only the new part of
  the trade, never reverse its direction, and count resized trades as REDUCED", and the owner
  asked for it to be backlogged (PR #125). This spec still asks the owner to supersede packet
  56's assumption explicitly (D3) rather than doing so silently.
- 2026-11-05: Gross scale keeps the owner's long/short split (80/70, chosen in 630ff079
  2026-10-24T17:47Z as "Near-neutral, 80/70"); under CAUTION net falls from +10% to +8%,
  still inside the chosen bounds. 120% gross was never discussed with the owner; it is what
  today's REDUCE_GROSS_20 already intends for the whole book.
