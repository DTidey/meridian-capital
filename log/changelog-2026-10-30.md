# Changelog 2026-10-30

## Packet 56: resize trade direction

- Found by the 2026-10-29 nightly: Layer 5 resizes left BA, COIN and AVY with an action
  opposite to their delta, and the executor sent them the wrong way (AVY also on 10-28). The
  three orders were cancelled at Alpaca before the 10-30 open (owner's call).
- Fixed at the source (the resizes re-derive the action with the rebalance mapping) so
  `position_approvals`, the dashboard and the executor agree on what each row does.
- The executor refuses a mismatch instead of taking the side from the delta sign: with Layer 5
  fixed, a mismatch is a new bug and should be visible, not silently traded.
- Targets rounded in `generate_trades`, not the optimisers: approvals become orders, while
  `target_portfolio` weights stay exact for reporting and risk.
- Breaker SIZE_DOWN had the same flaw (not yet triggered in paper trading) and is fixed the
  same way.
- A blackout HOLD is not cut again by reduce-gross or size-down: it is no longer an opening
  trade, and both have only ever cut opening trades.
