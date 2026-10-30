## Summary

The 2026-10-29 nightly sent BA SHORT 3, COIN SHORT 3 and AVY BUY 1 when the risk layer wanted
COVER 3, COVER 3 and SELL 1, and sent ZTS and TXN orders for 0 shares (rejected). AVY on
2026-10-28 was the same bug. The earnings-blackout resize (50%) and tail-risk reduce-gross
(20%) shrink an opening trade's target and recompute its delta but keep its action; once the
target falls below current holdings the delta flips sign, and the executor takes the side from
the action. BA, COIN and AVY were cancelled at Alpaca before the 2026-10-30 open.

- `risk/pre_trade.py`, `risk/tail_risk.py`, `risk/circuit_breakers.py`: the three resizes
  (blackout, reduce-gross, breaker size-down) re-derive the action with the rebalance
  mapping (`portfolio.rebalance.map_action`, made public); a resize back to current holdings is
  HOLD, and HOLD rows are not cut again.
- `execution/executor.py`: HOLD and sub-share rows are skipped (`no_change`); a row whose action
  disagrees with its delta sign is refused (`action_mismatch`, logged as an error) rather than
  traded; a stored 0.0 delta is no longer read as missing.
- `portfolio/rebalance.py`: approval targets are whole shares (the optimisers size fractional
  shares; Alpaca limit orders take whole shares).

## Spec

- Spec: `docs/specs/56-resize-trade-direction.md`
- Test plan: `docs/test-plans/56-resize-trade-direction.md`
- PR draft path: `.ai/pr-description/56-resize-trade-direction.md`

## Acceptance Criteria

- [x] AC1: `generate_trades` rounds targets to whole shares; delta and action follow.
- [x] AC2: A blackout resize below current holdings re-derives the action (BUY becomes SELL).
- [x] AC3: A blackout resize to current holdings becomes HOLD with delta 0.
- [x] AC4: Reduce-gross re-derives the action the same way, including HOLD.
- [x] AC5: The executor submits nothing for HOLD rows or deltas that round to 0 shares (`no_change`).
- [x] AC6: The executor refuses an action/delta mismatch (`action_mismatch`) and logs an error.
- [x] AC7: Breaker SIZE_DOWN re-derives the action and leaves HOLD rows alone.

## Security Review

- [x] Security considerations reviewed in `docs/specs/56-resize-trade-direction.md` (only
  removes orders; never adds or enlarges one)

## Validation

- 15 new tests fail on `main`, pass here
- Replay of the 2026-10-29 trade list through the new logic: BA HOLD, AVY HOLD, TXN HOLD,
  COIN COVER 1, ZTS COVER 1 (tail-risk cut); other trades unchanged
- `make lint`: pass
- `make test`: 1563 passed
- `make security`: pass ("No known vulnerabilities found")

## Risks and follow-ups

- Behaviour change: a blackout HOLD is not cut again by reduce-gross or size-down (BA would have stayed at
  -14 rather than covering to -11). Reduce-gross only touches opening trades, as before.
- Monday's log should show no `qty must be > 0` errors; any `action_mismatch` line is a new
  upstream bug.
