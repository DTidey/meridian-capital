# Snapshot Reliability

**Spec file:** `docs/specs/40-snapshot-reliability.md`
**Spec slug:** snapshot-reliability
**Status:** Draft
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
The 2026-10-21 data audit (`docs/data-audit-2026-10-21.md`, items C7, C8 and H10) found that the
nightly Yahoo snapshots fail silently, and that one revision window is blank every Friday.

- **The earnings calendar has stored nothing since 2026-10-14.**
  - On 2026-10-20 the step made 503 `.calendar` requests in 11 seconds, and every one failed.
  - The run logged `0 upcoming events stored` at INFO; the exception is swallowed at DEBUG
    (`data/earnings_calendar.py:66-68`).
  - Earlier steps make about 1,006 `.info` requests (short interest and estimates each call
    `.info` for every ticker), and Yahoo is throttling by the time the calendar runs.
- **Estimates drop 60 to 73 tickers a night**, mostly late in the alphabet (443 of 503 on
  2026-10-20). XOM's latest estimate is from 2026-10-14.
- **Superseded earnings dates are never removed.** 122 tickers hold more than one future date
  (e.g. AAPL has 10-29 and 11-02). The stale ones create false earnings blackouts in the
  optimiser and pre-trade checks.
- **`rev_90d` is neutral for every ticker on Fridays.**
  - On 2026-10-09 and 2026-10-16, all 503 tickers had `rev_90d = 50`, against 8 to 78 on other
    days.
  - The loader keeps 90 days of estimates (`factors/loader.py:26`), the same as the longest
    window. So the prior snapshot must fall exactly on the cutoff date.
  - The cutoff for a Friday is always a Saturday, when no snapshot exists.

Goal:
- snapshot steps make fewer requests;
- they retry when throttled, and say loudly when coverage is still short;
- the calendar keeps only each ticker's current dates;
- the 90-day revision works on every weekday.

## Scope
In scope:
- A shared Yahoo `.info` fetch with retry and backoff, used by short interest and estimates
  within one run, so each ticker's `.info` is requested at most once per run.
- The earnings calendar fetch gets the same retry and backoff. A failed fetch is told apart from
  a successful fetch that returned no dates.
- Each snapshot step makes a second pass over the tickers whose fetch failed.
- Each step reports its coverage, and logs an ERROR when coverage is below 95%. The run
  continues.
- Each successful calendar fetch that returns dates removes that ticker's other future dates.
- `_EST_LOOKBACK_DAYS` gains a margin. Revisions take the latest snapshot on or before each
  cutoff, and no more than 7 days before it.

Out of scope / non-goals:
- Forward EPS glitch filtering, fiscal-period labels and percent-based revisions (audit C5, C6,
  M2): the next estimates packet.
- Short interest settlement dates (audit M1).
- Moving the calendar onto `.info`. The owner chose to keep `.calendar` so the quarterly EPS
  estimate stays.
- A calendar freshness check in the optimiser or pre-trade checks.
- Failing the nightly run on low coverage. The owner chose ERROR and continue.

## Assumptions
- Yahoo throttling surfaces as an exception from yfinance (`YFRateLimitError` in yfinance 1.7.0,
  or another exception for an HTTP 429 error). A throttled call is retried rather than treated
  as "no data".
- `.info` returns the same fields for short interest and estimates whichever step asks first,
  so one response per ticker per run can serve both.
- A successful `.calendar` response lists the company's current upcoming date(s). A date
  stored earlier but missing from it has been superseded.
- An empty successful response is not taken as proof that earlier dates were cancelled. Rows
  are kept in that case.
- Snapshots are taken on weekdays, so the latest snapshot at or before any cutoff is normally 1
  to 3 days old. 7 days of tolerance covers a missed night or a long weekend without reaching
  back into a different window.
- Retry delays (5 and 15 seconds between 3 attempts) and a 60-second pause before the second
  pass. These are module constants, patched to zero in tests.
- Retries alone do not bound the run time. With every request throttled, each ticker costs
  about 20 seconds per pass, so 503 tickers would take about 5.6 hours per step. That would
  delay scoring into the next trading day. A circuit breaker bounds it (below).

## Proposed behavior / API
### Public interface
- New `data/yahoo_info.py`:
  - `YahooInfo`: memoised `.info` fetches for one run.
  - `YahooInfo.get(ticker) -> dict | None`: the `.info` dict, or `None` when every attempt
    failed.
  - Repeated `get` calls for a ticker that succeeded make no new request.
- `update_short_interest(conn, tickers, info: YahooInfo | None = None)`.
- `update_estimates(conn, tickers, info: YahooInfo | None = None)`.
  - Both default to a fresh `YahooInfo`, so standalone callers keep working.
- `run_data.py` creates one `YahooInfo` per run and passes it to both steps.
- `_fetch_earnings_date(ticker) -> list[dict] | None`: `None` means the fetch failed; `[]`
  means it succeeded with no dates.
- `factors/loader.py`: `_EST_LOOKBACK_DAYS = 105`, which is the longest window plus 15 days.
- `factors/revisions.py`: the prior snapshot for each window is the latest one dated on or
  before `ref_date - window`, and no earlier than 7 days before that cutoff. Otherwise the
  sub-factor is NaN, as now.

### Inputs / outputs
- **Retry.**
  - A call that raises is retried up to 3 attempts in total, waiting 5 and then 15 seconds.
  - After the last attempt it counts as a failure, logged at DEBUG with the exception.
- **Second pass.** After a step's loop, if any ticker failed, wait 60 seconds and try each
  failed ticker once more (with the same retry).
- **Circuit breaker.**
  - Within a pass, once 10 tickers in a row have failed every attempt, the remaining tickers
    in that pass are not requested and count as failed.
  - A WARNING names the step and the number of tickers skipped.
  - Any success resets the count.
  - The second pass applies the same rule.
  - Worst case per step: about 10 x 20 s + 60 s + 10 x 20 s, roughly 8 minutes.
- **Coverage.**
  - Coverage = tickers fetched successfully / tickers attempted. Calendar success includes an
    empty result.
  - Each step logs `"<step>: N/M tickers fetched"` at INFO.
  - When coverage is below 0.95, the step also logs an ERROR naming the step and the count.
  - The run summary table shows the step's count as now.
- **Superseded dates.**
  - When a ticker's calendar fetch succeeds with one or more in-window dates, the ticker's
    rows with `earnings_date >= today` that are not in the new set are deleted.
  - Past rows are never deleted.
- Error handling: no new exceptions reach `run_data.py`. Failures are counted and logged as
  above.

### Examples
```text
INFO  Short interest: 503/503 tickers fetched
INFO  Analyst estimates: 503/503 tickers fetched (503 from the shared .info)
INFO  Earnings calendar: 497/503 tickers fetched, 512 upcoming events stored, 118 superseded removed
ERROR Earnings calendar: 0/503 tickers fetched after retries (below 95%)
```

## Acceptance criteria
- AC1: A fetch that raises and then succeeds returns the data. Retry waits increase (5 s, then
  15 s). After 3 failed attempts the ticker counts as failed, not as "no data".
- AC2: In one `run_data.py` run, short interest and estimates together request `.info` once per
  ticker. Estimates for a ticker whose `.info` already succeeded make no request.
- AC3: Tickers that fail in a step's first pass are retried once in a second pass, and a
  second-pass success is stored.
- AC4: The calendar fetch returns `None` when the request fails and `[]` when it succeeds with no
  dates. Only `None` counts against coverage and triggers the second pass.
- AC5: A successful calendar fetch with dates deletes the ticker's other future rows. Past rows,
  other tickers' rows, and rows of a ticker whose fetch failed or returned no dates are
  unchanged.
- AC6: A step with coverage below 95% logs an ERROR naming the step and the counts, and the run
  continues. At 95% or above, no ERROR is logged.
- AC7: With weekday snapshots, `rev_90d` is computed (not NaN) on a Friday score date. The
  loader returns estimates going back at least 105 days.
- AC8: A prior snapshot more than 7 days before a window's cutoff is not used; that sub-factor
  is NaN.
- AC9: `make lint`, `make test` and `make security` pass.
- AC10: After 10 consecutive failed tickers in a pass, the remaining tickers in that pass are
  not requested, count as failed, and a WARNING is logged. A success in between resets the
  count. With every request failing, a step makes at most 20 tickers' worth of requests
  across both passes.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new. Yahoo values are stored through the existing
  bound-parameter inserts.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact:
  - About 503 fewer Yahoo requests a night.
  - Retries add requests only after failures, with backoff, so they do not hammer a throttled
    endpoint.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers:
  - The superseded-date delete must be scoped to one ticker and to future dates only (AC5).

## Edge cases
- **Ticker already snapshotted today:** skipped as now, and does not count as attempted.
- **`.info` succeeds but lacks short interest or estimate fields:** a success with no row
  stored, as now (`_fetch_*` returns `None` for missing fields). Not a failure.
- **Calendar returns a date outside the lookahead window:** not stored, as now. It does not
  trigger deletion unless at least one in-window date is stored.
- **Scoped run with a few `--tickers`:** coverage is computed over the tickers attempted.
- **No snapshots yet for a ticker in the 7-day tolerance (new listing, long gap):** NaN for that
  window, so the sub-factor scores neutral 50, as now.
- **Thanksgiving week:** the Friday after a Thursday holiday still has a Wednesday snapshot
  within 7 days of its cutoff.

## Test guidance
- AC1 -> `tests/test_yahoo_info.py::TestRetry`.
- AC2 -> `tests/test_yahoo_info.py::TestSharedInfo`;
  `tests/test_run_data_args.py::test_one_info_fetch_per_ticker`.
- AC3 -> `tests/test_snapshot_coverage.py::TestSecondPass` (short interest, estimates,
  calendar).
- AC4 -> `tests/test_earnings_calendar.py::TestFetchResult`.
- AC5 -> `tests/test_earnings_calendar.py::TestSupersededDates`.
- AC6 -> `tests/test_snapshot_coverage.py::TestCoverageError`.
- AC7 -> `tests/test_revisions.py::TestFridayWindow`; `tests/test_loader.py` (lookback).
- AC8 -> `tests/test_revisions.py::TestPriorTolerance`.
- AC9 -> `make lint`; `make test`; `make security`.
- AC10 -> `tests/test_snapshot_coverage.py::TestCircuitBreaker`.

## Decision log
- 2026-10-21: Share `.info` between short interest and estimates, and keep `.calendar` (owner's
  choice). `.info`'s `earningsTimestampStart` matched `.calendar` on 7 of 7 tickers, but
  `.info` has no quarterly EPS estimate, which the daily report and ticker page show. This
  cuts requests from about 1,509 to about 1,006 a night.
- 2026-10-21: Low coverage logs an ERROR and the run continues (owner's choice). Stopping
  would skip a whole night's scoring for a Yahoo outage.
- 2026-10-21: Remove superseded dates only on a successful fetch that returned dates. An empty
  response is more likely a Yahoo gap than a cancelled report.
- 2026-10-21: Circuit breaker after 10 consecutive failures, added in review. The
  implementation showed the retries alone would take about 5.6 hours per step in a full
  Yahoo outage (the original assumption said a few minutes). Ten in a row is far more than
  the scattered failures of partial throttling, so it only trips when Yahoo is refusing
  everything.
- 2026-10-21: Prior-snapshot tolerance of 7 days rather than the exact cutoff date. Snapshots
  are weekday-only, so an exact-date match fails on every Friday.
