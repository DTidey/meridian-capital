# Circuit Breaker Position Trim

**Spec file:** `docs/specs/54-breaker-position-trim.md`
**Spec slug:** breaker-position-trim
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-29

## Problem statement
The 2026-10-28 nightly run (the first with real paper positions) force-closed AIZ (3.99% of
NAV), AKAM (3.08%) and CLX (3.74%). The circuit breaker cap
`risk.circuit_breakers.max_single_position_pct` is 0.03, but portfolio construction and the
pre-trade check both size names up to `max_position_pct` 0.05. Any position sized between 3%
and 5% is therefore closed the night after it fills, and Layer 4 buys it back the night after
that. Two further faults:
- FORCE_CLOSE closes the whole position instead of bringing it back within the limit.
- It rejects only that night's opening trades (BUY/SHORT) for the ticker. A closing trade from
  the rebalance stays APPROVED, so CLX got a second COVER that Alpaca rejected
  (`insufficient qty available`, all 43 shares held by the force-close order).

The three force-close orders were cancelled at Alpaca on 2026-10-29 before the open (owner's
call).

## Scope
In scope:
- `config.yaml`: `risk.circuit_breakers.max_single_position_pct` 0.03 -> 0.06.
- `risk/circuit_breakers.py`:
  - FORCE_CLOSE trims an oversized position to `portfolio.max_position_pct` of NAV;
  - the trim replaces every other APPROVED trade for that ticker that night;
  - the run refuses a breaker cap below `portfolio.max_position_pct`.
- Tests in `tests/test_risk_circuit_breakers.py`.

Out of scope / non-goals:
- Pre-trade or Layer 4 sizing rules.
- Tail risk: it scales only opening trades, so a trim (SELL/COVER) is never reduced.
- Executor handling of more than one order per ticker.

## Assumptions
- `portfolio_positions` is the Alpaca book (packet 44): signed shares, signed market value.
- The trim is whole shares: Alpaca orders are whole shares, and rounding down keeps the trimmed
  position within the limit.
- The breaker event name stays `FORCE_CLOSE` so the dashboard and risk_events history keep
  working; the logged detail gains the target shares.

## Proposed behavior / API
### Public interface
- `run_circuit_breakers(...)` keeps its signature. It raises `ValueError` when
  `risk.circuit_breakers.max_single_position_pct` < `portfolio.max_position_pct`.

### Inputs / outputs
- For each position with |market value| / NAV > `max_single_position_pct`:
  - target shares = sign x floor(`portfolio.max_position_pct` x NAV / price), where price =
    |market value| / |shares|;
  - every APPROVED trade for that ticker and date becomes REJECTED;
  - one APPROVED SELL (long) or COVER (short) row is inserted with that target and
    delta = target - current.
- `whatif=True` still writes nothing.

## Acceptance criteria
- AC1: An oversized long is trimmed with a SELL to floor(max_position_pct x NAV / price) shares,
  not closed.
- AC2: An oversized short is trimmed with a COVER to the same size, with negative target shares.
- AC3: Every other APPROVED trade for a trimmed ticker that night, opening or closing, is
  REJECTED; trades for other tickers are untouched.
- AC4: A breaker cap below `portfolio.max_position_pct` raises `ValueError` before any change.
- AC5: Production config has a breaker cap (0.06) above `portfolio.max_position_pct` (0.05).
- AC6: A position at or under the breaker cap is not trimmed, and `whatif=True` writes nothing.

## Security considerations
- Input handling: reads existing config and DB values only.
- Auth/authz, secrets, data exposure, file system, network: no change.
- Dependency impact: none.
- Security notes for reviewers/testers: the change reduces order size versus a full close;
  a misconfigured cap now fails the risk step instead of churning the book.

## Edge cases
- One share is worth more than the sizing cap: target is 0, so a full close (as before).
- Position exactly at the cap: no trim (strict `>`).
- Short position: shares and market value are negative; target keeps the sign.
- A REJECTED or PENDING row for the ticker is left as it is.

## Test guidance
- AC1 -> `TestCircuitBreakerForceClose::test_force_close_trims_long`
- AC2 -> `TestCircuitBreakerForceClose::test_force_close_trims_short`
- AC3 -> `TestCircuitBreakerForceClose::test_trim_rejects_other_trades_for_ticker`
- AC4 -> `TestCircuitBreakerForceClose::test_cap_below_sizing_cap_raises`
- AC5 -> `TestCircuitBreakerForceClose::test_production_config_caps_consistent`
- AC6 -> `TestCircuitBreakerForceClose::test_position_at_cap_not_trimmed`,
  `TestCircuitBreakerWhatIf::test_whatif_trim_no_db_changes`

## Decision log
- 2026-10-29: Breaker cap 6%, trim to the 5% sizing cap (owner's choice over lowering sizing to
  3%): the breaker catches drift above what Layer 4 can buy, and a trim keeps the view.
- 2026-10-29: The trim replaces all of that night's trades for the ticker, not only opening
  ones, so the broker never gets two orders that share the same held shares.
