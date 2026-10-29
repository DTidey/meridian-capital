## Summary

Fixes the force-close churn found in the 2026-10-28 nightly run, the first with real paper
positions. The circuit breaker capped single names at 3% of NAV while Layer 4 and pre-trade
size up to 5%, so AIZ (3.99%), AKAM (3.08%) and CLX (3.74%) were force-closed the night after
they filled. The three close-out orders were cancelled at Alpaca before the 2026-10-29 open.

- `risk.circuit_breakers.max_single_position_pct` 0.03 -> 0.06, above the 5% sizing cap.
- FORCE_CLOSE trims an oversized position to `portfolio.max_position_pct` of NAV (whole
  shares, sign kept) instead of closing it; a full close only when one share is worth more.
- The trim rejects every APPROVED trade for that ticker that night, opening or closing. On
  10-28 a rebalance COVER for CLX stayed live beside the force-close and Alpaca rejected it.
- A breaker cap below `portfolio.max_position_pct` now raises `ValueError` in the risk step.

## Spec

- Spec: `docs/specs/54-breaker-position-trim.md`
- Test plan: `docs/test-plans/54-breaker-position-trim.md`
- PR draft path: `.ai/pr-description/54-breaker-position-trim.md`

## Acceptance Criteria

- [x] AC1: An oversized long is trimmed with a SELL to the sizing cap, not closed.
- [x] AC2: An oversized short is trimmed with a COVER, target shares negative.
- [x] AC3: Every other APPROVED trade for a trimmed ticker that night is REJECTED; other tickers untouched.
- [x] AC4: A breaker cap below `portfolio.max_position_pct` raises `ValueError` before any change.
- [x] AC5: Production config cap 0.06 above `portfolio.max_position_pct` 0.05.
- [x] AC6: A position at or under the cap is not trimmed; `whatif=True` writes nothing.

## Security Review

- [x] Security considerations reviewed in `docs/specs/54-breaker-position-trim.md` (reads
  existing config and DB values only; no input, secrets or network change)

## Validation

- New force-close tests: AC1-AC5 fail on `main`, all pass here
- `make lint`: pass
- `make test`: 1541 passed
- `make security`: pass ("No known vulnerabilities found")

## Risks and follow-ups

- After merge, pull into the live copy before the 22:00 run. With the current book (largest
  AIZ 3.99%) nothing fires tonight.
- The 10-28 DB rows for the cancelled orders (AIZ, AKAM, CLX) are recorded as CANCELLED by
  tonight's order sync.
