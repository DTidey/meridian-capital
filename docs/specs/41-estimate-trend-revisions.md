# Estimate Trend Revisions

**Spec file:** `docs/specs/41-estimate-trend-revisions.md`
**Spec slug:** estimate-trend-revisions
**Status:** Draft
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
The 2026-10-21 data audit (`docs/data-audit-2026-10-21.md`, items C5, C6 and M2) found three
faults in the estimate revisions factor and the forward earnings yield.

- **One-day glitches feed straight into scores.**
  - PGR's stored `forwardEps` was 1012 instead of about 16.3 on 24, 25 and 28 September.
  - That made `val_fwd_earn_yield`, `rev_30d` and `revisions_score` 100 and flipped PGR's
    direction.
  - The spike passes through `rev_30d`, then `rev_60d`, then `rev_90d` until late December.
  - CRM showed -1.90 for two days.
  - Neither the ingestion (`data/estimates.py`) nor the factors validate the value.
- **A fiscal-year roll-over is scored as a revision.**
  - `forwardEps` is Yahoo's next-fiscal-year estimate, and it steps to the following year
    when a company reports its fourth quarter.
  - `factors/revisions.py` compares snapshots across that step, so MU and COST scored 100 on all
    three windows on 2026-10-20.
  - About 400 calendar-year companies roll over in January and February.
- **Revisions are in dollars, not percent.** High-EPS names dominate their sector rank: MU's
  +$50.55 (+32%) outranks NKE's -$0.69 (-30%).

Yahoo's `earningsTrend` data (checked 2026-10-21) avoids all three:
- For each fiscal period (`0q`, `+1q`, `0y`, `+1y`) it gives the current consensus EPS and the
  consensus 7, 30, 60 and 90 days ago for that same period.
- PGR's `+1y` trend shows no glitch (16.28 thirty days ago, 16.30 now).
- MU's `+1y` trend (171.36 thirty days ago, 206.89 now) compares the same fiscal year across
  its roll-over.
- `+1y` current equals `.info` `forwardEps` (7 of 7 tickers).
- One `earningsTrend` request also returns the quarterly estimate. Its `0q` average equals
  `.calendar`'s "Earnings Average" (PGR 4.11543 for both).
- `.info` `earningsTimestampStart` equals the `.calendar` date (7 of 7 tickers).

So `earningsTrend` can replace the `.calendar` request, with no change in request count.

Goal:
- revisions and forward earnings yield come from Yahoo's period-consistent EPS trend;
- revisions are percent changes and are guarded against single-snapshot glitches;
- the earnings calendar is built from `.info` and `earningsTrend`;
- Yahoo requests stay at about 1,006 a night.

## Scope
In scope:
- A shared, memoised `earningsTrend` fetch per ticker per run, using packet 40's retry, second
  pass, circuit breaker and coverage logging.
- A new table, `estimate_trends`: one row per ticker, snapshot date and fiscal period (`0y`,
  `+1y`), holding the current consensus and the 7/30/60/90-days-ago values.
- The analyst estimates step stores a trend snapshot alongside the existing
  `analyst_estimates` row.
- The earnings calendar no longer requests `.calendar`.
  - The date comes from `.info` `earningsTimestampStart` (and `earningsTimestampEnd` when it
    differs).
  - `eps_estimate` comes from the `0q` average in `earningsTrend`.
- `factors/revisions.py`: `rev_30d`, `rev_60d` and `rev_90d` become the percent change of the
  `+1y` consensus against its value 30, 60 and 90 days ago, from the latest trend snapshot.
- A glitch guard on the `+1y` current value, used by both revisions and the forward earnings
  yield.
- `factors/value.py`: `val_fwd_earn_yield` uses the guarded `+1y` consensus instead of the
  stored `forwardEps`.

Out of scope / non-goals:
- `analyst_estimates` and its consumers: the dashboard price-target and EPS charts, and the
  ticker page. The table is still written each night as now.
- A blended next-twelve-months EPS for the forward yield (audit M5).
- Backfilling `estimate_trends` for past dates. The first nightly run after deploy writes the
  first snapshot, and the factors use it the same night.
- Rescoring historical `factor_scores`.
- Short interest (audit M1).

## Assumptions
- Yahoo's `earningsTrend` 30/60/90-days-ago values refer to the same fiscal period as `current`.
  Checked on MU after its 2026-10-01 roll-over: the `+1y` history is continuous for the new
  year.
- `+1y` is the right horizon. It is what `forwardEps` measures today, so the forward yield's
  meaning is unchanged.
- A consensus that moves more than 50% from Yahoo's own value 7 days earlier, for the same
  fiscal period, is far more likely a data glitch than a real revision. Large genuine
  revisions (MU +20% in 30 days, NKE -29% in 30 days) stay under 50% over 7 days. A genuine
  jump that persists is accepted once Yahoo's 7-days-ago value catches up, after at most a
  week.
- A denominator floor of $0.10 keeps percent changes finite for near-zero estimates (DOC 0.14,
  SKYD 0.20). Ranks are percentiles within sector, so the size of an extreme value does not
  matter beyond its rank.
- A trend snapshot older than 5 calendar days before the score date is stale and is not used.
  Snapshots are weekday-only.

## Proposed behavior / API
### Public interface
- `data/yahoo_info.py`:
  - `YahooTrend`: memoised `earningsTrend` fetches for one run, mirroring `YahooInfo`.
  - `get(ticker) -> dict | None` returns the trend by period, or `None` when every attempt
    failed.
  - The fetch goes through yfinance `Ticker.eps_trend` and `Ticker.earnings_estimate`, which
    share one request.
- `data/db.py`: new table `estimate_trends` with columns `ticker`, `date`, `period`, `current`,
  `days_7`, `days_30`, `days_60`, `days_90` and `fetched_at`. Primary key
  `(ticker, date, period)`.
- `update_estimates(conn, tickers, info=None, trend=None)` also writes `estimate_trends` rows
  for `0y` and `+1y`.
- `update_earnings_calendar(conn, tickers, config, info=None, trend=None)` takes dates from
  `info` and `eps_estimate` from `trend`, and never calls `.calendar`.
- `run_data.py` creates one `YahooInfo` and one `YahooTrend` per run and passes them to short
  interest, estimates and calendar.
- `factors/loader.py`:
  - loads `estimate_trends` for `period = '+1y'`, from 5 days before `score_date` to
    `score_date`, as `data["estimate_trends"]`;
  - `data["estimates"]` is still loaded.
- A guard function, used by both `factors/revisions.py` and `factors/value.py`:
  `guarded_current(row) -> float | NaN`.
  - Returns `current`, unless `days_7` is present and
    `|current - days_7| > 0.5 * max(|days_7|, 0.10)`.
  - In that case it returns `days_7`.

### Inputs / outputs
- **Revisions.** For each ticker, the latest `+1y` trend snapshot dated within 5 days before
  `score_date`:
  - `rev_Nd = (g - days_N) / max(|days_N|, 0.10)`, where `g` is the guarded current, for N
    in 30, 60, 90;
  - NaN when `days_N` or the snapshot is missing.
  - Sector ranking as now; NaN still scores neutral 50.
- **Forward yield.** `val_fwd_earn_yield = g / price`, or NaN when there is no fresh snapshot.
- **Calendar.**
  - Success = `.info` succeeded. Dates are `earningsTimestampStart`, plus
    `earningsTimestampEnd` when it is a different date.
  - Missing timestamps mean a success with no dates (`[]`).
  - `eps_estimate` is the `0q` average when the trend is available, else NULL.
  - Superseded-date removal, coverage and the second pass behave as in packet 40.
- **Requests.**
  - At most one `.info` and one `earningsTrend` request per ticker per run, plus retries.
  - No `.calendar` requests.
- **Logging.**
  - Coverage lines for the trend fetch, under the analyst estimates step.
  - An INFO count of tickers whose current value the glitch guard replaced.

### Examples
```text
INFO  Analyst estimates: 503/503 tickers fetched (503 from the shared .info); trends 501/503
INFO  Revisions: glitch guard used the 7-day-ago consensus for 1 ticker (PGR)
INFO  Earnings calendar: 503/503 tickers fetched, 498 upcoming events stored, 3 superseded removed
```

## Acceptance criteria
- AC1: `YahooTrend.get` fetches `earningsTrend` at most once per ticker per run, retrying as in
  packet 40. The estimates step stores `estimate_trends` rows for `0y` and `+1y` with current
  and 7/30/60/90-days-ago values.
- AC2: A full `run_data.py` snapshot run makes at most one `.info` request and one
  `earningsTrend` request per ticker (retries aside), and no `.calendar` request.
- AC3: The earnings calendar stores `earningsTimestampStart` (and a different
  `earningsTimestampEnd`), with `eps_estimate` from the `0q` average. A failed `.info` counts as
  a failed fetch. Packet 40's superseded-date rules still hold.
- AC4: `rev_30d`, `rev_60d` and `rev_90d` equal `(g - days_N) / max(|days_N|, 0.10)` from the
  latest `+1y` snapshot within 5 days of the score date, and are NaN when it is missing or stale.
- AC5: The glitch guard returns `days_7` when the current value differs from it by more than
  50% (with the $0.10 floor), and `current` otherwise. A PGR-style snapshot (current 1012,
  `days_7` 16.37) produces revisions and a forward yield computed from 16.37.
- AC6: A fiscal-year roll-over does not create a revision. With `forwardEps` snapshots that step
  from 161.5 to 205.3 and a `+1y` trend that is flat, the revision sub-factors reflect the flat
  trend.
- AC7: `val_fwd_earn_yield` uses the guarded `+1y` current and is NaN without a fresh trend
  snapshot. It no longer reads `analyst_estimates.eps_estimate_fwd`.
- AC8: The schema creates `estimate_trends` on a fresh database and on the existing PostgreSQL
  database (`create_all`, idempotent).
- AC9: `make lint`, `make test` and `make security` pass.
- AC10: When fewer than 95% of the tickers whose trend fetch succeeded get a `+1y` row stored,
  the estimates step logs an ERROR naming the count. An empty trend response counts as missing,
  even though it is not a failed fetch. Failed fetches are reported by the fetch coverage
  ERROR only, so one cause gives one ERROR.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new. Yahoo values are stored through bound-parameter
  inserts.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact:
  - The same number of Yahoo requests a night: the `.calendar` request is replaced by
    `earningsTrend`.
  - Retries and the circuit breaker are as in packet 40.
- Dependency or supply-chain impact: none. The yfinance 1.7.0 accessors are already used.
- Security notes for reviewers/testers: none beyond the new table's bound inserts.

## Edge cases
- **No analyst coverage (no `+1y` row):** no trend row is stored; revisions and the forward
  yield are NaN (neutral).
- **`days_7` missing:** the guard cannot judge and returns `current`.
- **`days_N` missing for one window:** only that sub-factor is NaN.
- **Negative estimates:** the formula uses absolute values in the denominator, so a move from
  -1.00 to -0.50 is +50%.
- **`.info` succeeds but `earningsTrend` fails:** the calendar date is stored with a NULL
  `eps_estimate`; that ticker has no trend snapshot that night.
- **First night after deploy:** trend snapshots are written in Layer 1 before scoring, so
  revisions work immediately. The old `analyst_estimates` history is no longer used by the
  factors.
- **Daytime manual run:** a trend snapshot dated today is overwritten (upsert) by the nightly
  run.

## Test guidance
- AC1 -> `tests/test_yahoo_info.py::TestSharedTrend`; `tests/test_estimates.py::TestTrendRows`.
- AC2 -> `tests/test_run_data_args.py::test_two_requests_per_ticker`.
- AC3 -> `tests/test_earnings_calendar.py::TestInfoDates`, `::TestSupersededDates` (kept).
- AC4 -> `tests/test_revisions.py::TestTrendRevisions`.
- AC5 -> `tests/test_revisions.py::TestGlitchGuard`; `tests/test_value.py::TestGlitchGuard`.
- AC6 -> `tests/test_revisions.py::TestRollOver`.
- AC7 -> `tests/test_value.py::TestFwdYieldFromTrend`.
- AC8 -> `tests/test_db.py` (table present).
- AC9 -> `make lint`; `make test`; `make security`.
- AC10 -> `tests/test_estimates.py::TestTrendRowCoverage`.

## Decision log
- 2026-10-21: Use Yahoo's `earningsTrend` instead of our own `forwardEps` snapshots (owner's
  choice). It is period-consistent across fiscal-year roll-overs and needs no 30-day warm-up.
  Labelling our own snapshots would blank each roll-over ticker's revisions for up to 90 days,
  about 400 tickers every January and February.
- 2026-10-21: Replace `.calendar` with `.info` dates plus the `earningsTrend` `0q` average. Both
  matched `.calendar` exactly, and the swap keeps requests at about 1,006 a night.
- 2026-10-21: A second coverage check counts stored `+1y` rows, added in review. yfinance hides
  non-throttling HTTP errors on `earningsTrend` as an empty table. Without this check, a broad
  outage of that kind would store nothing and leave every revision score neutral without an
  ERROR. Empty responses are not retried, because a ticker with no analyst coverage also
  returns one.
- 2026-10-21: Guard against glitches with Yahoo's own 7-days-ago value for the same period,
  with no history of our own. PGR's glitch was absent from Yahoo's trend history.
- 2026-10-21: Percent changes with a $0.10 denominator floor, replacing dollar differences.
