# Changelog 2026-10-29

## Packet 54: circuit breaker position trim

- Found by the 2026-10-28 nightly run, the first with real paper positions: the breaker cap
  (3%) sat below the Layer 4 and pre-trade sizing cap (5%), so AIZ, AKAM and CLX were
  force-closed the night after they filled. The three close-out orders were cancelled at Alpaca
  before the 2026-10-29 open (owner's call).
- Cap 6% and trim to 5% (owner's choice over sizing everything at 3%): the breaker is for drift
  above what Layer 4 can buy, and trimming keeps the position's view instead of churning it.
- The trim rejects every APPROVED trade for the ticker, not only opening ones: on 10-28 a
  rebalance COVER for CLX stayed live next to the force-close COVER and Alpaca rejected it
  (all shares held for the first order).
- A breaker cap below the sizing cap now raises: a silent mismatch churns the book nightly.
- Event name stays FORCE_CLOSE so risk_events history and the dashboard keep working.
