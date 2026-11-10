# Changelog 2026-11-10

## Packet 63: held prices in Layer 4

- **Widen the price ticker list in `run_portfolio.py`, not a second query in
  `generate_trades`.** One `daily_prices` query with one lookback for every ticker; the same
  frames feed the trade-list price and `estimate_cost`'s spread/ADV inputs, so both are fixed
  together. Extra keys in `prices` are safe: both optimisers look up `prices.get(ticker)` per
  candidate and never iterate the dict (checked `portfolio/optimizer.py`,
  `portfolio/mvo_optimizer.py`).
- **Betas, factor exposures and `check_events` stay candidates-only.** They describe or build
  the target book; a held non-candidate has target 0 and is closed regardless. Adding it to
  schedule warnings would change the printed warning list every night for no decision.
- **Warn, do not raise, on a missing price.** The executor prices closures from
  `daily_prices` itself, so a $0 Layer 4 valuation must not block a closure; it only needs to
  be visible. "Missing" is `not price > 0`, which catches no frame, empty frame, NaN and 0.
- **Consequence:** on nights with held non-candidates the turnover summary's needed value and
  the build-up allowance change (now correct). 2026-11-09 had about $8k of such book.
