# Changelog 2026-10-27

## Packet 53: Alpaca null day-trade count

- Removed `day_trade_count` from `get_account` instead of mapping null to 0: nothing reads it,
  and a 0 would be a made-up figure that looks real.
- Equity and buying power stay strict (`float(None)` raises): they size orders and NAV, so a
  missing value must stop the run.
- Found by the first nightly run of packet 44 (2026-10-26). Alpaca also returns
  `pattern_day_trader` as null; no code reads it.
