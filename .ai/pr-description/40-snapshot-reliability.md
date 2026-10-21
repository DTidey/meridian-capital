## Summary

The 2026-10-21 data audit (C7, C8, H10) found that the nightly Yahoo snapshots fail silently and
that the 90-day revision is blank every Friday. On 2026-10-20 the earnings calendar made 503
requests in 11 seconds and all failed (Yahoo throttling after about 1,000 `.info` calls), and
estimates lost the last 60 tickers. This packet cuts the request count, retries throttled
requests, says loudly when coverage is short, keeps only each ticker's current earnings dates,
and fixes the Friday window.

- **Shared `.info`.** New `data/yahoo_info.py`. `run_data.py` creates one `YahooInfo` per run
  and passes it to short interest (step 4) and estimates (step 5), so each ticker's `.info` is
  requested at most once per run (about 503 fewer requests a night). Failed fetches are not
  memoised, so estimates can still recover a ticker short interest lost. Standalone callers get
  their own `YahooInfo` by default.
- **Retry and second pass.** Every Yahoo fetch (`.info`, `.calendar`) is tried up to 3 times,
  waiting 5 s then 15 s after any exception (including `YFRateLimitError`). After a step's loop,
  failed tickers get one more pass (same retry) after a 60-second pause.
- **Coverage.** Each step logs `<step>: N/M tickers fetched` (M excludes tickers already
  snapshotted today) and an ERROR naming the step and counts when under 95%. The run continues;
  the run summary numbers are unchanged.
- **Calendar failed vs empty.** `_fetch_earnings_date` returns `None` when the request fails
  (was `[]`), `[]` when it succeeds with no dates. Only `None` counts against coverage and goes
  to the second pass.
- **Superseded dates.** When a ticker's calendar fetch succeeds and stores at least one
  in-window date, its other rows with `earnings_date >= today` are deleted. Past rows, other
  tickers, and tickers whose fetch failed or returned no dates are untouched. The coverage line
  reports the removed count.
- **Friday `rev_90d`.** `_EST_LOOKBACK_DAYS` 90 to 105. Revisions use the latest snapshot on or
  before each window's cutoff and no more than 7 days before it; otherwise NaN (neutral 50).

Example log lines:

```text
INFO  Short interest: 503/503 tickers fetched
INFO  Analyst estimates: 503/503 tickers fetched (503 from the shared .info)
INFO  Earnings calendar: 497/503 tickers fetched, 512 upcoming events stored, 118 superseded removed
ERROR Earnings calendar: 0/503 tickers fetched after retries (below 95%)
```

## Spec

- Spec: `docs/specs/40-snapshot-reliability.md`
- Test plan: `docs/test-plans/40-snapshot-reliability.md`
- PR draft path: `.ai/pr-description/40-snapshot-reliability.md`

## Acceptance Criteria

- [x] AC1: A fetch that raises and then succeeds returns the data; waits are 5 s then 15 s;
  after 3 failed attempts the ticker counts as failed, not "no data".
- [x] AC2: In one `run_data.py` run, short interest and estimates together request `.info` once
  per ticker; estimates make no request for a ticker whose `.info` already succeeded.
- [x] AC3: Tickers failing the first pass are retried once in a second pass, and a second-pass
  success is stored (short interest, estimates, calendar).
- [x] AC4: The calendar fetch returns `None` on failure and `[]` on an empty success; only
  `None` counts against coverage and triggers the second pass.
- [x] AC5: A successful calendar fetch with dates deletes the ticker's other future rows; past
  rows, other tickers' rows, and rows of failed or empty fetches are unchanged.
- [x] AC6: Coverage below 95% logs an ERROR naming the step and counts, and the run continues;
  at 95% or above no ERROR.
- [x] AC7: With weekday snapshots, `rev_90d` is computed on a Friday; the loader returns at least
  105 days of estimates.
- [x] AC8: A prior snapshot more than 7 days before a window's cutoff is not used; that
  sub-factor is NaN.
- [x] AC9: `make lint`, `make test`, `make security` pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/40-snapshot-reliability.md`.
  Disposition: low impact. No auth, secrets, file system or dependency changes. Yahoo values are
  stored through the existing bound-parameter inserts. The only new delete is scoped to one
  ticker and `earnings_date >= today` with bound parameters, and runs only after a successful
  fetch that stored dates (AC5 tests). Net Yahoo requests fall by about 503 a night; retries
  happen only after failures, with backoff. `make security` (bandit, pip-audit) passes.

## Validation

- New tests failed before the code change (run against a copy of the branch head):
  `data.yahoo_info` missing for the retry, shared-info, second-pass, coverage and calendar
  tests; Friday window, 7-day tolerance and 105-day lookback tests failed.
- `make lint`: pass
- `make security`: pass
- `make test`: 1219 passed (1177 on main; 42 new)
- Nothing was run against Yahoo or the production database; yfinance is faked in all tests.

## Post-merge verification

1. Deploy (pull `main` into the live working copy). No migration, no backfill.
2. Watch the next nightly log for the three coverage lines (`Short interest: N/M tickers
   fetched`, `Analyst estimates: ...`, `Earnings calendar: ...`), any ERROR below 95%, and the
   calendar's `superseded removed` count (expect roughly 100-120 on the first night).
3. Then check `earnings_calendar`: `max(fetched_at)` is less than one day old, and no ticker has
   two future dates (`select ticker from earnings_calendar where earnings_date >= current_date
   group by ticker having count(*) > 1` returns only genuine double listings, if any).
4. Check that Friday 2026-10-23's `factor_scores` has a normal count of `rev_90d = 50` (in line
   with other weekdays, 8-78, not 503).

## Risks and follow-ups

- **Run time.** A step whose every request is throttled now takes much longer: three attempts
  with 20 s of waits per ticker, then a second pass after 60 s. For 503 tickers in a full outage
  that is about 503 x 20 s x 2, around 5.6 hours per step, not "a few minutes" as the spec
  estimates. Partial throttling costs far less. Watch the first nightly runs' elapsed time.
- If Yahoo is still throttling when the second pass starts, the 60-second pause may be too short;
  the ERROR line makes that visible.
- The first successful calendar run deletes superseded future dates for every ticker it fetches,
  including dates beyond the lookahead window that the response no longer lists. A ticker whose
  response lists one of two genuine dates loses the other (assumed rare; spec assumption).
- Forward EPS glitches, fiscal-period roll-overs and percent revisions (C5, C6, M2) remain for
  packet 41; PGR's glitch enters `rev_30d` on 2026-10-24.
- No calendar freshness check in the optimiser or pre-trade checks (out of scope).
