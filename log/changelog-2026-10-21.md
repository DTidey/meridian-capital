# 2026-10-21: Pillow and certifi advisories (packet 36)

- **CI broke without a code change.** New pip-audit advisories against pillow 12.3.0 and certifi
  2026.6.17 failed `make security` on every PR, so the Dependabot batch (#83-#88) could not
  merge; `main` would fail the same way on its next run.
- **certifi fixed, pillow ignored by name.** Pillow's only fix is 13.0.0, and streamlit (pinned
  and latest) requires `pillow<13`. Owner chose named, time-boxed ignores over forcing pillow 13
  under streamlit.
- **Why the ignores are safe.** Every advisory needs Pillow to open or re-save an
  attacker-supplied image (PDF, GIF, PPM, TIFF, EPS). Our code never imports PIL or decodes
  images (charts are plotly); pillow is only a matplotlib and streamlit dependency. A test fails
  if a PIL import appears.
- **Time box is a test, not a reminder.** Once pillow 13 is pinned, a test fails until the
  ignores are removed, so they cannot outlive the cap.

# 2026-10-21: Insider analysis cache key (packet 37)

- **Insider re-ran nightly.** Its cache key was `{ticker}_{score_date}`, so it never hit on a
  new day: ~137 calls a night, ~90% for the same tickers as the night before. Filing, risk and
  earnings are keyed on their source document and were fine.
- **Key is a hash of the model's input.** The formatted transaction and cluster text is exactly
  what the model sees per ticker, so any change that matters changes the key, and nothing else
  does. Listing transaction ids would miss the cluster flag.
- **Window dates dropped from the prompt.** They changed daily for no informational gain; each
  transaction line keeps its date, and the 30-day cache TTL bounds reuse of one result.
- **Same-date ties ordered by id.** Without a tiebreak the database may return ties in any
  order, which would flip the hash.
- **Sector analysis left uncached** (owner's call): its inputs change with daily scores.

# 2026-10-21: Price history integrity (packet 38)

- **Freshness gate looks at SPY on or before the score date.** Checking only the latest SPY
  bar overall would fail every historical `--date` rerun; AC9 asks only whether a bar dated
  `score_date` exists, which "latest on or before" answers and still names both dates.
- **"Today" for the end date is the New York date.** At 22:00 UTC the New York and UTC dates
  agree; after UTC midnight the New York date is still the session that just closed.
- **Re-adjusted incremental bars are not stored before the full re-download.** Storing them
  would mix two adjustment bases if the full fetch then failed; AC6 keeps old rows unchanged.
- **One re-download path for both backends.** Fetching is split from the checks, so the
  Polygon path gets the same checks without its own copy; its adjusted-close issue is in
  the backlog.
- **Gap check is a count, as the spec says.** Per ticker: rows vs calendar dates between first
  and last row (one grouped query plus a bisect), not a per-date set comparison.
- **Delete and insert committed together, rollback on error, then re-raise.** `_upsert_prices`
  commits per call, so the replace path does its own single commit; a failure is not swallowed.

# 2026-10-21: Insider issuer integrity (packet 39)

- **Old unique key found by columns, not by name.** Postgres generated the 5-column key's name
  and cut it to 63 characters: `insider_transactions_ticker_accession_no_insider_name_date__key`
  (column part loses `shares`, keeps a trailing underscore). `DROP CONSTRAINT IF EXISTS` with
  the spelled-out 69-character name would itself be cut to `..._date_shar` and silently miss,
  so the migration looks the key up in `pg_constraint` by its column set. The new key is named
  explicitly (`insider_transactions_ticker_accession_no_txn_index_key`, Postgres's own style,
  under 63) so create_all and the migration agree.
- **SQLite does not migrate the old key.** SQLite cannot drop a constraint in place; it is only
  used in tests, where tables are created fresh with the new key. Production is PostgreSQL.
- **Old rows with NULL txn_index can coexist with the new key.** NULLs are distinct in a unique
  key, so adding the constraint cannot fail on existing data; the rebuild then removes them.
- **Cluster flags split from their commit.** `_flag_cluster_buys` committed inside, which would
  have committed the rebuild's delete halfway. `_write_cluster_flags` does the work without
  committing; `_flag_cluster_buys` calls it and commits, so the nightly path is unchanged.
- **CIK-change warning compares with the newest stored filing.** Stored URLs keep the old CIK
  forever, so comparing with any stored URL would warn every night. Comparing with the most
  recently filed one warns until a filing under the new CIK is stored, then stops.
- **CIK map loaded before anything is written.** On failure `update_sec_data` returns before
  even the `fetched_at` backfill, so "store nothing" is literal.
- **Rebuild reads stored XML in pages by id and inserts in batches.** About 32,000 Form 4 XML
  documents would not fit comfortably in memory at once, and one insert per row would be slow
  against the remote server. Pages are fully fetched before inserts, so no cursor is open while
  writing.
- **`--rebuild-insider` refuses `--tickers`.** The rebuild deletes every insider row; with a
  ticker subset it would re-insert only those, wiping the rest. Failing fast is safer than
  silently ignoring `--tickers`.
- **Fetch failures are not counted as issuer mismatches.** A Form 4 whose XML could not be
  downloaded has no issuer to compare, so the INFO total counts only parsed filings that name
  another issuer.
- Packet 39 review: a read-only production dry run showed the issuer-CIK-only rebuild
  would erase XOM's insider history (its stored Form 4s name predecessor CIK 34088; SEC now
  maps XOM to 2115436). A heuristic (issuer equals the feed CIK and the trading symbol equals
  the ticker) was rejected because a BlackRock municipal fund files as "BNY". Predecessor
  CIKs are listed explicitly in `sec.predecessor_ciks`; the CIK-change WARNING prompts new
  entries.

# 2026-10-21: Snapshot reliability (packet 40)

- **Split out of the estimates work.** Packet 40 was going to be fundamentals and 41 the estimates
  fixes; PGR's forward EPS glitch enters `rev_30d` on 2026-10-24, and the calendar has been empty
  since 10-14, so the snapshot reliability part ships first. The audit checklist renumbers later
  packets (fundamentals 42, combined score 43, positions/NAV/execution 44); C5/C6 stay on 41.
- **Retry inside `call_with_retry`, failure as an exception.** `.calendar` can legitimately
  return `None` (no dates), so a `None` sentinel could not tell a failed fetch from an empty one.
  The helper re-raises the last exception; callers turn that into `None` (calendar) or a `None`
  `.info` (`YahooInfo.get`).
- **Failed `.info` fetches are not memoised.** If short interest fails a ticker after both
  passes, estimates tries it again; only successful responses are shared. This keeps the one
  request per ticker rule for the normal case without letting one throttled burst blank both
  steps.
- **Calendar parse errors count as failures.** Only the request is retried; a response that
  cannot be parsed returns `None` (logged at DEBUG) rather than `[]`, so it is never read as
  "no dates" and never triggers the superseded delete.
- **Superseded delete keeps the in-window dates from the response, not only the ones inserted.**
  It runs only when at least one row was stored. A future date beyond the lookahead window that
  the response no longer lists is also removed, as the spec's rule ("rows with
  `earnings_date >= today` not in the new set") covers it.
- **Coverage counts a fetch, not a stored row.** `.info` without short interest or estimate
  fields is a successful fetch with nothing stored, so it does not trigger the second pass or
  the ERROR; the run summary keeps counting stored rows as before.
- **Coverage threshold is inclusive at 95%.** 19/20 logs no ERROR; with nothing to attempt
  (everything snapshotted today) there is no ERROR either.
- **Circuit breaker added in review (AC10).** The implementation showed the spec's "a few
  minutes" worst case was wrong: retries alone cost about 20 s per ticker per pass, about 5.6
  hours per step when Yahoo refuses everything. A pass now stops after 10 consecutive failed
  tickers (about 8 minutes per step at worst). Ten in a row is far beyond the scattered
  failures of partial throttling, so it only trips in an outage. Skipped tickers are
  pre-filled as failed in each step's summary so the run summary stays complete.

# 2026-10-21: Estimate trend revisions (packet 41)

- **Trends are fetched for every ticker, not only those the estimates step still needs.** The
  step skips tickers with today's `analyst_estimates` row. The nightly cron runs at 22:00 UTC, the
  same UTC date as a daytime manual run, so tying the trend fetch to that skip would leave a
  daytime trend snapshot in place. The spec's edge case says the nightly run overwrites it
  (upsert), so the trend pass covers all tickers. It costs requests only on a same-day re-run,
  and AC2's one request per ticker per run still holds.
- **The trend fetch is its own pass inside the estimates step.** It has its own name
  (`Analyst estimate trends`) for the second pass, the circuit breaker and the ERROR below 95%.
  The spec's example INFO line is kept: `...(N from the shared .info); trends T/M`. Making one
  ticker's success need both `.info` and the trend would let a trend outage trip the breaker and
  skip `.info` for the rest of the pass, losing price targets and the calendar's dates.
- **Calendar requests the trend only when `.info` has a date.** A failed `.info` is a failed
  fetch (AC3) with no trend request. A success with no dates needs no EPS estimate. In the
  nightly run both responses are already memoised, so this only saves requests when the step
  runs on its own.
- **`earningsTimestampEnd` without a start is not a date.** The spec defines the dates as the
  start "plus" the end when it differs. Yahoo has not been seen sending an end without a start.
- **Latest snapshot as a whole row.** `latest_trends` keeps each ticker's newest row
  (`drop_duplicates`), not `groupby().last()`. That would fill a missing `days_90` from an
  older day and mix snapshots. The same pattern is audit M14 in `factors/value.py`.
- **Staleness is enforced by the loader.** `compute(data, config)` has no score date, so the
  5-day window is the loader's query (`period = '+1y'`, `date >= score_date - 5`). The factors
  take each ticker's latest row from what they are given.
- **Guard count logged once.** Both factors call `guarded_current` (`factors/_utils.py`). Only
  revisions logs the INFO count, so the line appears once per scoring run.
- **No trend data at all: everything neutral.** As before for an empty `analyst_estimates`,
  revisions returns 50 for every column, including `revisions_score`. Ranking a column of 50s
  would give 75.
- **Packet 40's revision tests retired.** `TestFridayWindow` and `TestPriorTolerance` tested the
  snapshot-history lookup this packet removes. The 105-day `analyst_estimates` lookback stays,
  with its test, because the loader still loads `data["estimates"]`. No factor reads it now.
  It was left in place to keep the change in scope (`_EST_LOOKBACK_DAYS`'s comment is now
  historical).
- **yfinance swallows some trend errors.** With yfinance's default `hide_exceptions`, a
  non-throttling HTTP error on `earningsTrend` becomes an empty frame. We read that as "no
  coverage" (success, no rows), not as a failure to retry. `YFRateLimitError` still raises and is
  retried. Watch the `trends T/M` count: a sudden drop with no ERROR points here.
