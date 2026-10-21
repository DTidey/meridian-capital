## Summary

The 2026-10-21 data audit (C5, C6, M2) found three faults in the estimate revisions factor and
the forward earnings yield:
- One-day `forwardEps` glitches go straight into scores. PGR's stored value was 1012 instead
  of about 16.3 on 24-28 September.
- A fiscal-year roll-over is scored as an upgrade. MU and COST were at 100 on all three windows
  on 2026-10-20.
- Revisions are in dollars, so high-EPS names dominate.

This packet moves both factors onto Yahoo's `earningsTrend`, which gives the consensus for a
fiscal period now and 7/30/60/90 days ago for that same period. It also takes the earnings
calendar off `.calendar`, so Yahoo requests stay at about 1,006 a night.

- **Shared `earningsTrend`.**
  - `YahooTrend` (`data/yahoo_info.py`) caches each ticker's `earningsTrend` for the run,
    caching only successes, like `YahooInfo`.
  - `eps_trend` and `earnings_estimate` are read from one `yf.Ticker`: one request.
  - It uses packet 40's retry, second pass, circuit breaker and coverage logging.
- **New `estimate_trends` table.**
  - Primary key `(ticker, date, period)`, holding `0y` and `+1y` rows: `current`, `days_7`,
    `days_30`, `days_60`, `days_90`.
  - The estimates step writes it next to `analyst_estimates` (unchanged, still used by the
    dashboard).
  - A period without a current consensus is skipped.
  - The trend pass covers every ticker, so a same-day re-run overwrites the snapshot.
- **Earnings calendar from `.info`.**
  - The date is `earningsTimestampStart`, plus `earningsTimestampEnd` when it is a different
    UTC date.
  - `eps_estimate` is the `earningsTrend` `0q` average, or NULL without a trend.
  - Success means `.info` succeeded. Packet 40's superseded-date rules are unchanged.
  - No `.calendar` request. `run_data.py` shares one `YahooInfo` and one `YahooTrend` across
    steps 4-6.
- **Revisions.**
  - `rev_Nd = (g - days_N) / max(|days_N|, 0.10)`, from the latest `+1y` snapshot no older
    than 5 days before the score date. `g` is the guarded current value.
  - NaN (neutral 50) when the snapshot or the window is missing.
  - The snapshot-history lookup, the 30-day warm-up and packet 40's prior-tolerance logic are
    removed.
- **Glitch guard.** `guarded_current` (`factors/_utils.py`) returns Yahoo's 7-days-ago value
  when the current one differs from it by more than `0.5 * max(|days_7|, 0.10)`. Revisions and
  the forward yield both use it, and revisions logs how many tickers it replaced.
- **Forward yield.** `val_fwd_earn_yield = g / price`. It no longer reads
  `analyst_estimates.eps_estimate_fwd`.

Example log lines:

```text
INFO  Analyst estimates: 503/503 tickers fetched (503 from the shared .info); trends 501/503
INFO  Revisions: glitch guard used the 7-day-ago consensus for 1 ticker (PGR)
INFO  Earnings calendar: 503/503 tickers fetched, 498 upcoming events stored, 3 superseded removed
```

## Spec

- Spec: `docs/specs/41-estimate-trend-revisions.md`
- Test plan: `docs/test-plans/41-estimate-trend-revisions.md`
- PR draft path: `.ai/pr-description/41-estimate-trend-revisions.md`

## Acceptance Criteria

- [x] AC1: `YahooTrend.get` fetches `earningsTrend` at most once per ticker per run, with packet
  40's retry. The estimates step stores `estimate_trends` rows for `0y` and `+1y` with current
  and 7/30/60/90-days-ago values.
- [x] AC2: A full `run_data.py` snapshot run makes at most one `.info` and one `earningsTrend`
  request per ticker (retries aside), and no `.calendar` request.
- [x] AC3: The calendar stores `earningsTimestampStart` (and a different `earningsTimestampEnd`)
  with `eps_estimate` from the `0q` average. A failed `.info` is a failed fetch. Packet 40's
  superseded-date rules hold.
- [x] AC4: `rev_30d`/`rev_60d`/`rev_90d` equal `(g - days_N) / max(|days_N|, 0.10)` from the
  latest `+1y` snapshot within 5 days of the score date, and are NaN when it is missing or stale.
- [x] AC5: The glitch guard returns `days_7` for a move of more than 50% (with the $0.10 floor),
  else `current`. A PGR-style snapshot (1012 vs 16.37) gives revisions and a forward yield from
  16.37.
- [x] AC6: A fiscal-year roll-over (`forwardEps` 161.5 to 205.3, flat `+1y` trend) creates no
  revision.
- [x] AC7: `val_fwd_earn_yield` uses the guarded `+1y` current, is NaN without a fresh snapshot,
  and no longer reads `analyst_estimates.eps_estimate_fwd`.
- [x] AC8: The schema creates `estimate_trends` on a fresh database and on an existing one
  (`create_all`, idempotent).
- [x] AC9: `make lint`, `make test` and `make security` pass.
- [x] AC10: Fewer than 95% of fetched tickers with a stored `+1y` row logs an ERROR (empty
  trend responses count as missing; failed fetches only raise the fetch ERROR).

## Behaviour change

- Revision scores change for every ticker on the first night:
  - they are now percent, same-fiscal-year changes;
  - they are live immediately, with no 30-day warm-up;
  - MU and COST lose their roll-over 100s;
  - PGR's glitch no longer passes through `rev_30d`/`rev_60d`/`rev_90d`.
- `val_fwd_earn_yield` moves only where Yahoo's `+1y` consensus differs from the old stored
  `forwardEps` (normally equal) or the guard fires.
- Tickers without a fresh `+1y` trend snapshot score neutral on revisions and the forward
  yield.
- Calendar `eps_estimate` values are the same figure as before (`0q` avg = `.calendar`'s
  "Earnings Average").

## Security Review

- [x] Security considerations reviewed in `docs/specs/41-estimate-trend-revisions.md`.
  Disposition: low impact.
  - No auth, secrets, file system or dependency changes.
  - Yahoo values go into the new table through SQLAlchemy bound-parameter upserts; the period
    is one of the fixed strings `0y` and `+1y`.
  - Request volume is unchanged: `.calendar` is replaced by `earningsTrend`, one per ticker
    per run (AC2 test), with packet 40's backoff and circuit breaker.
  - `make security` (bandit, pip-audit) passes.

## Validation

- Tests came first, in a commit before the code (`95e34bf`). There:
  - five test modules failed to import (`YahooTrend` and `estimate_trends` missing);
  - 25 tests failed in `test_value.py`, `test_snapshot_coverage.py`, `test_loader.py` and
    `test_db.py`.
- `make lint`: pass
- `make security`: pass
- `make test`: 1278 passed (1225 on main)
- Nothing was run against Yahoo or the production database. yfinance is faked in all tests
  (`tests/yahoo_fake.py` now scripts `earningsTrend`).

## Post-merge verification

1. Deploy is a `git pull` of `main` into the live working copy. There is no migration step:
   `initialise_schema` creates `estimate_trends` on the first start. No backfill is needed.
2. Check the next nightly log for:
   - `Analyst estimates: N/M tickers fetched (... from the shared .info); trends T/M`, with T
     close to M and no `Analyst estimate trends` ERROR;
   - the `Revisions: glitch guard ...` count;
   - the `Earnings calendar` coverage line;
   - no `.calendar` requests (the calendar step should take seconds, because both responses
     are cached).
3. `estimate_trends` should hold about 500 tickers x 2 periods for the run date:
   `select period, count(*) from estimate_trends where date = '<run date>' group by period`.
4. PGR's `rev_30d` on 2026-10-26 is not extreme. The audit expected the old glitch to reach
   `rev_30d` from 2026-10-24.
5. MU and COST revision sub-scores are no longer 100 because of the roll-over.

## Risks and follow-ups

- **First night is the first snapshot.** If the trend fetch fails broadly on deploy night,
  revisions and the forward yield are neutral for the affected tickers until the next
  successful night. Each later night has up to 5 days of fallback.
- **yfinance hides some errors.** With yfinance's default `hide_exceptions`, a non-throttling
  HTTP error on `earningsTrend` returns an empty frame, which is not retried. AC10 (added in
  review) makes it visible: below 95% of fetched tickers with a `+1y` row, the step logs an
  ERROR. Revisions for the affected tickers are neutral that night; the next good night
  restores them.
- **The estimates step can take longer in an outage.** The trend pass has its own second pass
  and breaker, so the worst case is about 8 more minutes.
- **Calendar dates depend on `.info`.** `earningsTimestampStart` matched `.calendar` for 7 of 7
  tickers when checked. An end timestamp without a start is not stored.
- `data["estimates"]` is still loaded, but no factor reads it now. It was left in place to keep
  the change in scope; a later packet can remove the load and `_EST_LOOKBACK_DAYS`.
- Out of scope, unchanged:
  - a blended next-twelve-months EPS (M5);
  - backfilling `estimate_trends`;
  - rescoring historical `factor_scores`;
  - short interest (M1).
