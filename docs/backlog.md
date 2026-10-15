# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Item 1A not reaching the risk analyzer for some tickers

- **Raised:** 2026-10-15, packet 31.
- **Problem:** CLX, EIX and MS risk analyses list no material risks; the model says the
  section was missing or entirely boilerplate. Since packet 31 they get no risk score.
- **Fix:** check why `extract_risk_factors` misses their Item 1A heading (packet 30 noted
  heading styles it does not match) and widen the match.

## Risk score display after packet 31

- **Raised:** 2026-10-15, packet 31 Reviewer pass.
- **Problem:** the new risk score is fractional with a narrow spread (standard deviation about
  0.4), but `dashboard/page_ticker.py` shows it with `:.0f`, so 6.5-7.4 all read as 7. The
  report generator (`analysis/report_generator.py`) still prints "Overall severity" from the
  label, which no longer drives the score.
- **Fix:** show one decimal place; label the overall severity as the model's summary, or show
  the severity mix instead.
