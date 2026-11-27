# Skip the Nightly Run on US Market Holidays

**Spec file:** `docs/specs/68-holiday-skip.md`
**Spec slug:** holiday-skip
**Status:** Implemented (2026-11-27; review fixes 1 and 2 2026-11-27)
**Owner:** DTidey
**Date:** 2026-11-27

## Problem statement
The nightly cron (22:00 UTC Mon-Fri, `python run_all.py --no-filings --no-13f`, Fridays full)
ran on Thu 2026-11-26, Thanksgiving, when NYSE was closed (`logs/run_all_2026-11-26.log`):

- Layer 1 ran as usual (119 s); that data work was wasted.
- Layer 2 stopped after 1 s with exit 1: `Latest SPY bar is 2026-11-25, not the score date
  2026-11-26: scoring stopped` (`run_scoring.py` `_prices_fresh`, packet 38).
- Layers 3-7 never ran and the run ended as FAILED.

The SPY freshness guard did its job, but a scheduled market holiday is reported as a pipeline
failure. The morning check cannot tell a holiday from a real data failure.

Goal: on a day the US equity market is closed, `run_all.py` detects it before Layer 1, prints
one clear line, and exits 0 without running any layer.

## Scope
In scope:
1. A market-day check at the start of `run_all.py`, before Layer 1, for the date Layer 2 will
   score.
2. One shared score-date function used by both `run_all.py` and `run_scoring.py`, and one
   score date per run: `run_all.py` resolves it once and passes it with `--date` to every
   layer that scores a date (Layers 2-6 and Book sync).
3. A `--ignore-market-calendar` flag on `run_all.py` that forces a run.
4. `docs/run_all.md`: the flag and the skip behaviour.

Out of scope / non-goals:
- The SPY freshness guard in `run_scoring.py`: unchanged. A missing SPY bar on an open day is
  still a failure.
- Market-day checks in the individual layer scripts (`run_data.py`, `run_scoring.py`, ...)
  when run on their own.
- Early-close handling: half-days (day after Thanksgiving, Christmas Eve) are trading days and
  run normally; the 22:00 UTC cron is after a 13:00 ET early close.
- The crontab itself, and how Layers 3-6 resolve their date when run on their own.
- Layer 1 (fetches data as of now, no score date) and Layer 7 (`--date` there is a report /
  letter date, not a score date): no `--date` passed.
- A new calendar dependency (`exchange_calendars`, `pandas_market_calendars`).

## Assumptions
- `run_all.py` passes no `--date` to `run_scoring.py`, so Layer 2's score date is
  `scoring.score_date` from `config.yaml` if set, else `date.today()` in the server's local
  time zone. On 2026-11-26 that gave 2026-11-26.
- Alpaca's trading calendar (`GET /v2/calendar`, alpaca-py `TradingClient.get_calendar`) lists
  every NYSE trading day, with open and close times, and omits weekends, full-day holidays and
  special closures. Half-days are listed with an early close. Paper and live endpoints serve
  the same calendar.
- `execution/broker.py` `get_client` builds the `TradingClient` from `ALPACA_API_KEY`,
  `ALPACA_SECRET_KEY` and `ALPACA_PAPER` (default paper), the same client Layer 6 and Book
  sync use; the keys come from `.env`.

## Proposed behavior / API
### Calendar source (decision)
Alpaca's calendar, through the existing `get_client`. alpaca-py is already a dependency and
the keys are already in `.env` for Book sync and Layer 6, so this adds no dependency, no key
and no new service. It is authoritative for the days the broker will trade, including special
closures that a rules-based holiday list would miss. A new package (`exchange_calendars`,
`pandas_market_calendars`) would avoid one network call but adds a dependency whose holiday
tables must be kept current; Alpaca is enough for "is date D a trading day".

Weekends are decided locally (Saturday, Sunday), with no Alpaca call.

### Timeout (decision, review fix)
alpaca-py 0.45.0 calls `requests.Session.request` without a timeout, so a connection that is
accepted but never answered blocks forever (reproduced against a silent localhost socket).
`closed_reason` gives the client it builds a dedicated `requests.Session` subclass that adds
`timeout=CALENDAR_TIMEOUT` = (5 s connect, 10 s read) to every request. The client comes from
`get_client()`, which builds a new `TradingClient` per call, so only this lookup's client is
changed: no global patch of the SDK or of `requests`, and Book sync / Layer 6 are untouched.
This uses the SDK's private `_session` attribute; if a future alpaca-py drops it, the check
raises `CalendarUnavailable` before any call (fail open with a WARNING every night, visible,
never a hang). Rejected: a thread with `join(timeout)` (leaves a blocked thread, no socket
timeout); patching `requests.Session.request` globally (affects every HTTP call in the
process).

These are requests' per-socket timeouts: connect is the TCP connect, read is the longest
wait between bytes. They do not bound DNS resolution or a response that keeps trickling bytes,
and a 429 rate limit makes the SDK retry 3 times, 3 s apart; so they stop the reproduced
failure (an accepted connection that never answers) but are not an overall deadline. An
overall deadline is out of scope for this packet.

### One score date per run (decision, review fix)
The check and the layers must use the same date even if the run crosses local midnight (the
Friday full run with filings and 13-F can run for hours). `run_all.py` calls
`resolve_score_date` once, before the check, and passes `--date <score date>` to Layer 2
(`run_scoring.py`), Layer 3 (`run_analysis.py`), Book sync and Layer 6
(`run_execution.py --sync/--execute/--status`), Layer 4 (`run_portfolio.py`) and Layer 5
(`run_risk_check.py`); each otherwise reads `date.today()` again when it starts. The
`--ignore-market-calendar` path passes the same date. Side effect: under `run_all.py`, a
per-layer `analysis.score_date` in `config.yaml` no longer overrides `scoring.score_date`
(both are null today); a layer run on its own still reads its own key.

### Never sync or execute a historical score date (decision, review fix 2)
`execution/book_sync.py` `sync_book(score_date)` replaces that date's `portfolio_history`
snapshot with today's broker positions and overwrites that date's `portfolio_nav` row, and
Book sync runs under `--whatif` too; Layer 6 trades against the date's approvals. Before this
packet `run_execution.py` always used `date.today()`; passing `--date` (AC11) would let a
backfill `scoring.score_date` (e.g. `2026-11-20`) reach both. So `run_all.py` reads the run
date once at start (`factors.score_date.run_date`) and resolves the score date from that same
read. If the score date differs from the run date (past or future):
- without `--no-execution` (including `--whatif`, `--dry-run`, `--ignore-market-calendar`):
  print `ERROR: score date <d> is not the run date <today> ...`, report the run as failed at
  "Score date check" and exit 1 before the calendar check and before any layer;
- with `--no-execution`: print `Score date <d> is not the run date <today>: historical run,
  Book sync and Layer 6 are not run (--no-execution)` and run the other layers for that date.
  Book sync and Layer 6 are also skipped by the date test itself, not only by the flag.

Why stop rather than silently drop sync/execution: a historical run is a deliberate,
hand-started backfill (`docs/run_all.md` has no other backfill story, and the cron never sets
`scoring.score_date`). A date left in `config.yaml` by mistake would otherwise turn every
nightly run into a quiet no-trade run that reports success; a hard stop is visible at the
06:30 check. `--no-execution` keeps the analysis-only backfill possible. The run date is a
single clock read, so a run that crosses midnight is never "historical".

### Fail open (decision)
The calendar must not become a new single point of failure. If the lookup fails for any
reason (missing keys, network, timeout, auth, unexpected response, unparseable score date),
the run is
NOT skipped: `run_all.py` prints a WARNING and runs normally. On a real holiday the existing
SPY guard then stops Layer 2, as today. A lookup error never skips a trading day.

### Public interface
- `factors/score_date.py` (new): `resolve_score_date(cli_date: str | None, scoring_cfg: dict)
  -> str`. Returns `cli_date`, else `scoring_cfg["score_date"]` if set, else
  `date.today().isoformat()`. `run_scoring.py` uses it instead of its inline expression.
- `execution/market_calendar.py` (new):
  - `CalendarUnavailable(Exception)`: the lookup failed; the caller must run normally.
  - `closed_reason(day: date, client_factory=None) -> str | None`: `"weekend"` on Saturday or
    Sunday (no client is built); `"holiday"` when Alpaca's calendar has no entry for `day`;
    `None` when `day` is a trading day (including half-days). `client_factory` defaults to
    `execution.broker.get_client`. Raises `CalendarUnavailable` when the client cannot be
    built or has no `requests.Session` to bound, the call raises or times out, the response is
    unusable, or it lists only other dates. The response shape is checked before emptiness is
    read: it must be a list or tuple whose every entry has a `date` that is a plain
    `datetime.date` (not a `datetime`, string or `None`). An empty list or tuple is a holiday;
    anything else empty (`{}`, `""`, a set, an iterator) is unusable.
  - `CALENDAR_TIMEOUT = (5.0, 10.0)`: (connect, read) seconds for the calendar request.
- `run_all.py`:
  - New flag `--ignore-market-calendar`: skip the check and run.
  - Before Layer 1: resolve the score date once with `resolve_score_date(None,
    config["scoring"])`, call `closed_reason` on it, and pass it as `--date` to Layers 2-6 and
    Book sync.
  - Loads `.env` (as every layer script does) so `get_client` finds the Alpaca keys.

### Inputs / outputs
- Closed day: prints `Market closed on 2026-11-26 (holiday): skipping the nightly run` (or
  `(weekend)`) and exits 0. No layer, no DB connection, no order.
- Open day: prints `Market open on 2026-11-27: running the pipeline` and runs as before.
- Lookup failure: prints `WARNING: market calendar check failed for 2026-11-27 (<error>):
  running the pipeline` and runs as before.
- `--ignore-market-calendar`: prints `Market calendar check skipped for <date>
  (--ignore-market-calendar): running the pipeline` and runs as before with the same
  `--date`; Alpaca is not called.
- Error handling: `CalendarUnavailable` and any other exception from the check are caught in
  `run_all.py`; the message is the exception text (no secrets: the client never puts keys in
  error messages, and only `str(exc)` is printed).

### Examples
```bash
# Thanksgiving, cron
python run_all.py --no-filings --no-13f
# -> Market closed on 2026-11-26 (holiday): skipping the nightly run   (exit 0)

# Force a run anyway
python run_all.py --no-filings --no-13f --ignore-market-calendar
```

## Acceptance criteria
- AC1: On a full-day market holiday (Alpaca calendar has no entry for the score date),
  `run_all.py` prints `Market closed on <score date> (holiday): skipping the nightly run`,
  runs no layer and exits 0.
- AC2: On a Saturday or Sunday score date, `run_all.py` prints `Market closed on <score date>
  (weekend): skipping the nightly run`, runs no layer, exits 0, and does not build an Alpaca
  client.
- AC3: On a normal trading day, `run_all.py` runs the pipeline as before.
- AC4: On an early-close half-day (calendar entry with a 13:00 ET close), `run_all.py` runs the
  pipeline as before.
- AC5: If the calendar lookup fails (client cannot be built, the call raises or hits a
  socket timeout, or the response is unusable: not a list or tuple, or any entry without a
  plain `datetime.date` `date`), `run_all.py` prints a line starting `WARNING: market calendar
  check failed` and runs the pipeline; it never skips. Only an empty list or tuple means
  holiday.
- AC6: `--ignore-market-calendar` runs the pipeline on a closed day without calling the
  calendar.
- AC7: `run_all.py` and `run_scoring.py` derive the score date with the same function,
  `factors.score_date.resolve_score_date`; the date checked by `run_all.py` is the date Layer 2
  scores (CLI `--date`, else `scoring.score_date`, else today).
- AC8: The Layer 2 SPY freshness guard is unchanged: on an open day with no SPY bar for the
  score date, Layer 2 still stops with exit 1.
- AC9: `docs/run_all.md` documents the skip and the `--ignore-market-calendar` flag.
- AC10: The calendar request has socket connect / read timeouts (`CALENDAR_TIMEOUT`, 5 s /
  10 s), applied through a session on the lookup's own client only; a calendar that accepts
  the connection and never answers raises `CalendarUnavailable` once the read timeout
  passes. (Not an overall deadline: DNS resolution and a trickling response are not bounded.)
- AC11: `run_all.py` resolves the score date once and passes that exact date with `--date` to
  Layers 2-6 and Book sync (also with `--whatif` and `--ignore-market-calendar`); if the run
  crosses local midnight after the check, Layer 2 still scores the checked date.
- AC12: If the score date differs from the run date (read once at start), `run_all.py` never
  runs Book sync or Layer 6 for it: without `--no-execution` (any other flags, including
  `--whatif`) it prints an `ERROR: score date ... is not the run date ...` line and exits 1
  before any layer; with `--no-execution` it runs the other layers for that date. A score date
  equal to the run date (null config, or a run that crosses midnight) syncs and executes as
  before.

## Security considerations
- Auth/authz impact: none. Uses the existing Alpaca keys through `get_client`; the calendar
  endpoint is read-only.
- Input handling or injection risk: the score date comes from `config.yaml` or the clock and
  is parsed with `date.fromisoformat`; a bad value fails open (AC5), not into a skip.
- Secrets or credential handling: keys stay in `.env`; `run_all.py` now calls `load_dotenv`
  like every layer script. Child processes already load the same `.env`, and `load_dotenv`
  does not override existing variables, so they see the same values. Only the exception text
  is printed.
- Data exposure or privacy impact: none; no DB access in the check.
- File system access impact: reads `config.yaml` and `.env`, as the layer scripts do.
- Network or external service impact: one extra `GET /v2/calendar` to Alpaca per weekday run
  (none on weekends or with the override). A failure or timeout falls back to running.
- Dependency or supply-chain impact: none; alpaca-py, PyYAML and python-dotenv are existing
  dependencies.
- Network or external service impact (review fix): the calendar request has socket connect /
  read timeouts (5 s / 10 s), so an Alpaca that accepts the connection and never answers
  cannot hold the nightly run. They do not bound DNS resolution or a trickling response.
- Data integrity (review fix 2): a historical or future score date never reaches Book sync
  (which rewrites `portfolio_history` and `portfolio_nav` for that date) or Layer 6.
- Security notes for reviewers/testers: the fail-open path is the safety property: confirm
  no exception from the check can lead to a skip; and that no path passes a date other than
  the run date to `run_execution.py`.

## Edge cases
- Special closures (e.g. national days of mourning): not in any fixed list; Alpaca omits them,
  so they are reported as `holiday`.
- `scoring.score_date` set in `config.yaml` (backfill): refused unless `--no-execution`
  (AC12); then the check uses that date, as Layer 2 does.
- `scoring.score_date` set to today's date: equals the run date, runs normally.
- Missing Alpaca keys on a weekday: fail open with a warning (Layer 6 would fail later anyway).
- Calendar response with entries for other dates only: unusable, fail open.
- Empty but not a list (`{}`, `""`, empty set or iterator): unusable, fail open; never a
  holiday (review 3).
- `--whatif` or `--tickers` runs on a closed day: skipped too (Layer 2 would fail anyway);
  `--ignore-market-calendar` forces them.
- Calendar accepts the connection and never answers: read timeout, fail open (AC10, AC5).
- Run crosses local midnight (Friday full run): Layers 2-6 still get the checked date (AC11).
- Malformed `scoring.score_date`: it differs from the run date, so it is refused (AC12)
  unless `--no-execution`; then the check fails open and the layers fail on it as before.
- `config.yaml` unreadable: `run_all.py` now stops before Layer 1 with a traceback (exit 1);
  every layer would fail on it anyway. Never a skip.

## Test guidance
- AC1 -> `test_run_all_market_calendar.py::test_holiday_skips_with_exit_0`,
  `test_market_calendar.py::test_no_entry_is_holiday`
- AC2 -> `test_run_all_market_calendar.py::test_weekend_skips_without_client`,
  `test_market_calendar.py::test_weekend_needs_no_client`
- AC3 -> `test_run_all_market_calendar.py::test_trading_day_runs`,
  `test_market_calendar.py::test_trading_day_is_open`
- AC4 -> `test_run_all_market_calendar.py::test_half_day_runs`,
  `test_market_calendar.py::test_half_day_is_open`
- AC5 -> `test_run_all_market_calendar.py::test_calendar_error_runs_with_warning`,
  `test_stalled_calendar_warns_and_runs`, `test_empty_dict_response_warns_and_runs`, and the
  `test_market_calendar.py` unavailable cases
  (`test_malformed_response_is_unavailable_not_holiday[...]`)
- AC6 -> `test_run_all_market_calendar.py::test_override_runs_on_holiday`
- AC7 -> `test_score_date.py`
- AC8 -> existing `test_scoring_freshness.py` (unchanged, still passing)
- AC9 -> `test_run_all_market_calendar.py::test_run_all_doc_mentions_override`
- AC10 -> `test_market_calendar.py::test_timeout_is_finite_and_short`,
  `test_dedicated_session_carries_the_timeout`, `test_session_passes_timeout_to_requests`,
  `test_real_sdk_client_uses_the_timeout`, `test_stalled_calendar_times_out`,
  `test_client_without_session_is_unavailable`
- AC12 -> `test_run_all_historical_date.py` (all tests)
- AC11 -> `test_run_all_market_calendar.py::test_midnight_crossing_layer2_gets_checked_date`,
  `test_every_dated_layer_gets_the_one_score_date`, `test_whatif_status_gets_the_score_date`,
  `test_override_passes_the_same_date`

## Decision log
- 2026-11-27: Alpaca calendar over a new dependency: already a dependency with keys in `.env`,
  authoritative for broker trading days; one call per weekday run.
- 2026-11-27: Fail open on any lookup error: a wrong skip loses a trading day silently; a
  wrong run on a holiday is caught by the SPY guard, as on 2026-11-26.
- 2026-11-27: Weekends decided locally: no network on manual weekend runs.
- 2026-11-27: Check in `run_all.py` only: the goal is the nightly run; layer scripts run alone
  are manual and keep their current behaviour.
- 2026-11-27: Packet 68: 66 and 67 are merged on main; no open branch uses 68.
- 2026-11-27 (review): timeout via a dedicated `requests.Session` subclass on the lookup's own
  client, not a global patch or a watchdog thread; (5, 10) s is ample for one small GET.
- 2026-11-27 (review): one score date per run, passed with `--date` to every dated layer, so
  the check and the scoring can never disagree across midnight.
- 2026-11-27 (review 2): a score date other than the run date stops the run unless
  `--no-execution`; Book sync and Layer 6 must never see a historical date. Stop, not skip,
  so a forgotten backfill date fails visibly instead of silently stopping trading.
- 2026-11-27 (review 2): timeouts described as socket connect / read timeouts, not an overall
  bound; no overall deadline in this packet.
- 2026-11-27 (review 3): validate the response shape before reading emptiness: an empty
  non-list (`{}`, `""`) iterated to no dates and was read as a holiday. The current SDK
  validates responses, so production could not hit it, but AC5 promises any unusable
  response runs. A `datetime` entry is rejected too: it never equals a `date`.
