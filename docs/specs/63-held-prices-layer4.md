# Layer 4 Prices for Held Names Outside the Candidate List

**Spec file:** `docs/specs/63-held-prices-layer4.md`
**Spec slug:** held-prices-layer4
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-10

## Problem statement
`run_portfolio.py` passes only `candidates["ticker"]` (tonight's LONG/SHORT names) to
`_load_prices`. `portfolio/rebalance.py` `generate_trades` iterates every ticker in
`current | target` and falls back to price 0.0 when a ticker has no entry in `prices`. A held
name that dropped out of the candidate list (now NEUTRAL in `combined_scores`) therefore shows
price 0.00 and estimated cost $0.00 in the trade list, has `trade_value` 0 in
`_apply_turnover_budget`, and is left out of the current-gross side of the build-up gap in
`_budget_values`.

Seen on the nightly run of 2026-11-09 (`logs/run_all_2026-11-09.log`): VICI (SELL 107), YUM
(COVER 18) and CRL (COVER 7) all showed price 0.00 and $0.00, about $8k of the book ignored by
the turnover summary and the build-up gap. Layer 1 had stored their closes. First raised
2026-11-06 (AVY, backlog entry "Held names outside the candidate list valued at $0 in
Layer 4"). Orders are unaffected: the executor prices from `daily_prices` itself.

## Scope
In scope:
- `run_portfolio.py`: load positions before prices and call `_load_prices` with the candidate
  tickers plus the currently held tickers (`load_positions`). The same price frames feed the
  trade list's price and the ADV / high-low inputs of `estimate_cost`, so both are fixed by the
  one change.
- `portfolio/rebalance.py` `generate_trades`: log one WARNING listing every trade-list ticker
  that still has no usable price (no frame, empty frame, or a last close that is missing, NaN
  or not positive). The 0.0 fallback is kept so the row is still emitted.
- Remove the backlog entry.

Out of scope / non-goals:
- Optimiser universe: unchanged. Held non-candidates are not passed to the optimiser as
  candidates and cannot become targets. Extra keys in `prices` are harmless because the
  optimisers look prices up by candidate ticker (`prices.get(ticker)`) and never iterate the
  dict.
- Betas (`compute_betas`): unchanged, candidates only. Betas feed the optimiser and the target
  portfolio summary; neither involves held non-candidates (their target is 0).
- Factor exposures (`_load_factor_scores` / `compute_exposures`): unchanged, candidates only;
  they describe the target portfolio.
- Rebalance-schedule warnings (`check_events`): unchanged, candidates only. A held
  non-candidate is being closed tonight; a schedule warning would not change that, and adding
  it would change the printed warning list for every run. Minimal change preferred.
- Executor pricing, the no-stored-close case (`no_close`, packet 60) and the
  "no candidates at all" early exit: unchanged.

## Assumptions
- `portfolio_positions` is the broker book synced by Layer 6 (`execution/book_sync.py`), so
  `load_positions` is the right source for "currently held".
- Layer 1 stores daily prices for the whole S&P 500 universe, so a held name that left the
  candidate list normally still has a close on or before the score date.

## Proposed behavior / API
```python
# run_portfolio.py
current = load_positions(conn)
prices = _load_prices(conn, _price_tickers(candidates, current), score_date, lookback=...)
# betas, check_events, factor scores and the optimiser still use candidates["ticker"]

# portfolio/rebalance.py, after building rows
# WARNING portfolio.rebalance - No price for 1 trade-list ticker(s), valued at $0: XYZ
```
`_price_tickers(candidates, current) -> list[str]`: candidate tickers in their order, then held
tickers not already listed, sorted, no duplicates. `generate_trades` signature and return
columns unchanged.

## Acceptance criteria
- AC1: A held ticker absent from the candidate set gets its latest stored close (on or before
  the score date) as `price` in the Layer 4 trade list.
- AC2: That ticker's trade gets a non-zero `estimated_cost_usd` from `estimate_cost`, using its
  own stored high/low/volume history for the spread and ADV inputs.
- AC3: That ticker's trade counts at `|delta| x price` in `trade_value`, in the turnover
  summary (`needed_usd`, `kept_usd`), and on the current-gross side of the build-up gap in
  `_budget_values`.
- AC4: The optimiser, `compute_betas`, `check_events` and `_load_factor_scores` receive the
  candidate tickers only; a held non-candidate is never a target (target 0, closed as SELL or
  COVER).
- AC5: When no ticker is held outside the candidate list, the tickers loaded and the trade list
  are unchanged from before this packet.
- AC6: `generate_trades` logs one WARNING naming every trade-list ticker without a usable price
  (missing frame, empty frame, NaN or non-positive last close), and logs no such warning when
  every ticker is priced.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; held tickers come from the `portfolio_positions`
  table and are passed to SQLAlchemy as bound parameters in an `IN` clause.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none; the warning logs ticker symbols only.
- File system access impact: none.
- Network or external service impact: none; no order sizes or limits change (the executor
  prices separately).
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: Layer 4 still reads only; `--whatif` writes nothing.

## Edge cases
- Held ticker that is also a candidate: listed once (no duplicate in the `IN` list).
- Held ticker with no stored close at all (left the data universe): still emitted with price
  0 and cost 0, and the AC6 warning names it; closures are never trimmed (packet 57).
- Last stored close is NaN (half-written row): treated as missing, warned; `trade_value` stays
  0 via the existing `fillna(0)`.
- Empty book (`load_positions` returns no rows): only candidate tickers loaded (AC5).
- Held ticker whose latest close is older than the score date: that close is used, as for
  candidates (same `_load_prices` query).

## Rollout
1. No schema or config change. Takes effect on the next Layer 4 run.
2. Revert is the one-line ticker-list change in `run_portfolio.py` plus the warning helper.

## Verify in paper trading
- Next nightly with a held name outside the candidate list: its trade-list row shows a real
  price and a non-zero Est. Cost, and the turnover summary's "needed" includes it.
- No "No price for" WARNING in the Layer 4 log unless a held name has no stored close.

## Test guidance
- AC1 -> `tests/test_held_prices_layer4.py::TestRunWiring::test_held_non_candidate_priced`,
  `TestPriceTickers::test_held_non_candidate_added`
- AC2 -> `TestRunWiring::test_held_non_candidate_has_cost`
- AC3 -> `TestTurnover::test_held_closure_counts_in_trade_value_and_summary`,
  `TestTurnover::test_held_long_counts_in_build_up_gap`
- AC4 -> `TestRunWiring::test_optimiser_universe_unchanged`
- AC5 -> `TestPriceTickers::test_candidates_only_unchanged`,
  `TestRunWiring::test_no_held_non_candidates_same_tickers`
- AC6 -> `TestMissingPriceWarning::test_warns_for_unpriced_ticker`,
  `test_no_warning_when_all_priced`, `test_nan_and_zero_close_warned`

## Decision log
- 2026-11-10: Widen the price ticker list in `run_portfolio.py` rather than have
  `generate_trades` query prices itself: one query, the same lookback for every ticker, and
  `generate_trades` stays free of DB reads for pricing.
- 2026-11-10: Keep the 0.0 fallback and add a WARNING instead of raising: a missing price must
  not block a closure (the executor prices it independently), but it must be visible.
- 2026-11-10: Betas, factor exposures and schedule warnings stay candidates-only (see
  non-goals); only the trade list consumes held non-candidates.
