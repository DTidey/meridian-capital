# Resize Trade Direction

**Spec file:** `docs/specs/56-resize-trade-direction.md`
**Spec slug:** resize-trade-direction
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-30

## Problem statement
The 2026-10-29 nightly sent three orders the wrong way and two orders for zero shares.
Layer 5 shrinks opening trades twice: the earnings blackout in `risk/pre_trade.py` halves
`target_shares`, then tail-risk `REDUCE_GROSS_20` in `risk/tail_risk.py` scales it by 0.8.
Circuit-breaker `SIZE_DOWN_30` (`risk/circuit_breakers.py`) does the same when it fires. All
recompute `delta_shares` but keep the original `action`. When the smaller target sits
below current holdings the delta changes sign, and the executor takes the side from `action`
and the size from `abs(delta)`:

| Ticker | Book | Target after Layer 5 | Should send | Sent |
|---|---|---|---|---|
| BA | -14 | -11 | COVER 3 | SHORT 3 |
| COIN | -5 | -2 | COVER 3 | SHORT 3 |
| AVY | 7 | 6 | SELL 1 | BUY 1 |
| ZTS | -51 | -51 | nothing | SHORT 0 (rejected) |
| TXN | 13 | 13 | nothing | BUY 0 (rejected) |

AVY on 2026-10-28 was the same case (BUY 1 instead of SELL 1). BA, COIN and AVY were cancelled
at Alpaca before the 2026-10-30 open. The zero orders reached Alpaca because
`appr.delta_shares or (...)` in `execution/executor.py` treats a stored 0.0 as missing.

Separately, the optimisers size positions as `weight * nav / price`, so `target_shares` is
fractional (CLX -19.95, AKAM -25.80). Alpaca only takes whole-share limit orders, so the
executor rounds at submission while `position_approvals` and `execution_orders` keep the
fraction.

## Scope
In scope:
- `portfolio/rebalance.py`: round `target_shares` to whole shares before the delta and action
  are computed; make the action mapping public (`map_action`) so Layer 5 can reuse it.
- `risk/pre_trade.py`: the blackout resize re-derives `action` from the new target.
- `risk/tail_risk.py`: `_apply_reduce_gross` re-derives `action` from the new target.
- `risk/circuit_breakers.py`: `_apply_size_down` re-derives `action`; HOLD rows are not
  opening trades, so SIZE_DOWN and CLOSE_ALL leave them alone.
- `execution/executor.py`: skip HOLD and zero-share rows; refuse a row whose action
  disagrees with the sign of its delta; read a stored 0.0 delta as 0.

Out of scope / non-goals:
- Rounding inside the optimisers (`target_portfolio` weights and shares stay fractional for
  reporting and risk).
- A single order that crosses zero (long to short); unchanged.
- The blackout resize skipping the later pre-trade checks; unchanged.

## Assumptions
- The rebalance action table (`_map_action`) is the one definition of BUY / SELL / SHORT /
  COVER / HOLD; Layer 5 should agree with it rather than keep its own.
- A resize that turns an opening trade into a reduction is wanted: both resizes exist to cut
  exposure, so trimming an existing position is in the spirit of the rule.
- HOLD rows stay `APPROVED` in `position_approvals` so the dashboard shows what Layer 5 did.

## Proposed behavior / API
### Public interface
- `portfolio.rebalance.map_action(cur, tgt, delta) -> str` (renamed from `_map_action`, same
  logic).
- `generate_trades`: `target_shares` is `round(target)`; `delta_shares` and `action` follow.
- Blackout resize, reduce-gross and size-down write `action = map_action(current, new_target, new_delta)`
  alongside `target_shares` and `delta_shares`.
- `execute_approvals` result for a skipped row: `status="SKIPPED"`, `reason` one of
  `no_change` (HOLD, or a delta that rounds to 0 shares) or `action_mismatch` (BUY/COVER
  with delta <= 0, or SELL/SHORT with delta >= 0). `action_mismatch` logs at ERROR.

### Examples
```python
# Blackout: BUY 7 -> 10 halves to 5, below the 7 held
# before: action=BUY  target=5 delta=-2  -> executor sent BUY 2
# after:  action=SELL target=5 delta=-2  -> executor sends SELL 2
```

## Acceptance criteria
- AC1: `generate_trades` rounds `target_shares` to whole shares and computes `delta_shares`
  and `action` from the rounded value.
- AC2: When the blackout resize leaves a target on the other side of current holdings, the
  row's `action` is re-derived (BUY 7 -> 10 becomes SELL to 5).
- AC3: When the blackout resize leaves the target equal to current holdings, the row's
  `action` is HOLD and `delta_shares` is 0.
- AC4: When reduce-gross leaves a target on the other side of current holdings, the row's
  `action` is re-derived (BUY 10 -> 11 at 20% becomes SELL to 9), and a row it leaves at
  current holdings becomes HOLD (SHORT -5 -> -6 at 20% stays at -5).
- AC5: The executor submits nothing for a HOLD row or a row whose delta rounds to 0 shares,
  including a stored delta of exactly 0, and returns `SKIPPED` / `no_change`.
- AC6: The executor submits nothing for a row whose action disagrees with the sign of its
  delta, returns `SKIPPED` / `action_mismatch`, and logs an error.
- AC7: Circuit-breaker SIZE_DOWN re-derives the action the same way (BUY 10 -> 12 at 70%
  becomes SELL to 8) and leaves HOLD rows unchanged.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; values come from our own tables.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: fewer Alpaca orders (no zero-share or wrong-side
  orders); order side logic unchanged.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: the change only removes orders; it never adds or
  enlarges one.

## Edge cases
- Resize of a new position (current 0): target stays on the same side, action unchanged.
- Reduce-gross or size-down after a blackout relabel to SELL/COVER or HOLD: skipped, as closing and HOLD
  rows are not opening trades (no double cut of a reduction).
- Fractional current holdings: `map_action` thresholds unchanged (|delta| < 1 is HOLD).
- Rounding .5 targets: Python `round` (half to even), as the resizes already use.

## Test guidance
- AC1 -> `tests/test_rebalance.py::TestWholeShareTargets`
- AC2 -> `tests/test_risk_pre_trade.py::TestBlackoutResizeAction::test_resize_below_holding_sells`
- AC3 -> `TestBlackoutResizeAction::test_resize_to_holding_is_hold`
- AC4 -> `tests/test_risk_tail_risk.py::TestReduceGrossAction`
- AC5 -> `tests/test_execution_executor.py::TestTradeDirectionGuard::test_hold_skipped`,
  `test_zero_delta_skipped`
- AC6 -> `TestTradeDirectionGuard::test_action_mismatch_refused`
- AC7 -> `tests/test_risk_circuit_breakers.py::TestSizeDownAction`

## Decision log
- 2026-10-30: Refuse a mismatched row in the executor rather than take the side from the delta
  sign: Layer 5 now keeps them in step, so a mismatch is a new upstream bug and must show,
  not be silently traded.
- 2026-10-30: Round in `generate_trades`, not the optimisers: approvals are what become
  orders; target weights stay exact for reporting and risk.
