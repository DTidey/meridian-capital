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

## Packet 64: incumbent buffer and regime weight smoothing

- **Rotation credit and the band-exit buffer are out** (David's decisions 2 and 3); the ACs
  were renumbered AC1-AC9 so the PR body checks exactly the ACs in the spec. Both are backlog
  items with the replay evidence.
- **The replay recomputes scores with today's Layer 2/3 code** from stored factor and AI
  scores, instead of trusting stored `combined_scores` for every date. Reason: before late
  October the stored combined scores follow the pre-packet-43 rules (1655 exits, 387 band
  exits over 120 nights vs 1043 / 72 recomputed), so variants would be compared under
  different rules. Check: for 2026-10-27 to 11-09 the recomputed hard switch equals the
  stored results on every metric. A name that becomes a candidate only under other weights
  has no AI score and gets the neutral AI rank, as Layer 3 does for a name with no analysis.
- **Settings from the replay, not the spec's provisional values.** Exit rank 35 is the elbow
  (2-night round trips 628/285/141/77/59 for 20/25/30/35/40 over 120 nights; 35 -> 40 saves
  18 more but raises top-10 names left out from 0.31 to 0.47 per side-night). Smoothing 5
  days: churn equal to 3 and 10 days; 3 days doubles the largest one-night share change, 10
  days hardly reacts to the 11-05 spike (mean high share 0.005). low_vol band 13-17 instead
  of 12-18: churn is the same, but VIX sat at 15-17 all summer, so 12-18 would leave the
  low_vol tilt partly on for 99 of 120 nights, a standing change to calm-market weights.
- **The replay lands after the buffer and blend commits**, not first as the spec's rollout
  said: it calls `select_book` and the blend functions so the replay measures the shipped
  code, not a copy. The numbers were produced before the config values were committed.
- **Smoothing window max(14, 2 x days) calendar days**, not a fixed 14: 14 calendar days hold
  only about 10 trading days, so a 10-day setting (part of the replay grid) would otherwise
  always be short and warn.
- **`incumbent_exit_rank` and the regime config are validated at the start** of
  `run_portfolio.py`, `run_scoring.py` and MVO `optimise`: MVO wraps its solver in a broad
  `except` and falls back to conviction tilt, which would have hidden a config error behind a
  "did not converge" warning.
- **`resolve_regime` removed** rather than kept with its hard-coded 15/25: nothing else
  called it, and keeping it would leave a second, different regime rule in the code.
- **Replay read-only on SQLite uses `PRAGMA query_only`**: `SET TRANSACTION READ ONLY` is
  PostgreSQL-only; any other dialect is refused rather than run without a guard.
