# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Replace deprecated `datetime.utcnow()`

- **Raised:** 2026-10-15, surfaced by DeprecationWarnings in `make test`
  after the Dependabot bumps (#66-#77).
- **Problem:** `datetime.utcnow()` is deprecated and scheduled for removal
  in a future Python version. It returns a naive datetime.
- **Scope:** 41 call sites in 13 files.
  - `data/`: `earnings_calendar.py`, `estimates.py`, `fundamentals.py`,
    `institutional.py`, `market_data.py`, `sec_data.py`,
    `short_interest.py`, `transcripts.py`, `universe.py`
  - `tests/`: `test_earnings_calendar.py`, `test_fundamentals.py`,
    `test_sec_data.py`, `test_universe.py`
- **Fix:** switch to `datetime.now(UTC)`. About half the call sites
  (19 of 41) feed `.isoformat(...)` into stored timestamps, where an aware
  datetime appends `+00:00`. Most of the rest use `.strftime("%Y-%m-%d")` and are
  unaffected. A few do arithmetic against datetimes parsed from stored
  strings (e.g. `datetime.utcnow() - last` in a cache-freshness check),
  which raises `TypeError` if only one side is timezone-aware. Either strip the tzinfo
  (`.replace(tzinfo=None)`) to keep the stored format unchanged, or confirm
  that every reader and comparison of those strings handles the new suffix.
- **Done when:** `make test` emits no `utcnow` DeprecationWarnings and
  cache freshness checks behave as before.

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
