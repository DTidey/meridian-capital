# Skip the Nightly Run on US Market Holidays

**Spec file:** `docs/specs/68-holiday-skip.md`
**Spec slug:** holiday-skip
**Status:** Implemented (2026-11-27)
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
2. One shared score-date function used by both `run_all.py` and `run_scoring.py`.
3. A `--ignore-market-calendar` flag on `run_all.py` that forces a run.
4. `docs/run_all.md`: the flag and the skip behaviour.

Out of scope / non-goals:
- The SPY freshness guard in `run_scoring.py`: unchanged. A missing SPY bar on an open day is
  still a failure.
- Market-day checks in the individual layer scripts (`run_data.py`, `run_scoring.py`, ...)
  when run on their own.
- Early-close handling: half-days (day after Thanksgiving, Christmas Eve) are trading days and
  run normally; the 22:00 UTC cron is after a 13:00 ET early close.
- The crontab itself, and the score date used by Layers 3-6 (`date.today()` in their own
  scripts); they already agree with Layer 2 when `scoring.score_date` is null.
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

### Fail open (decision)
The calendar must not become a new single point of failure. If the lookup fails for any
reason (missing keys, network, auth, unexpected response, unparseable score date), the run is
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
    built, the call raises, the response is not a list of entries with a `date`, or it lists
    only other dates.
- `run_all.py`:
  - New flag `--ignore-market-calendar`: skip the check and run.
  - Before Layer 1: resolve the score date with `resolve_score_date(None, config["scoring"])`
    and call `closed_reason` on it.
  - Loads `.env` (as every layer script does) so `get_client` finds the Alpaca keys.

### Inputs / outputs
- Closed day: prints `Market closed on 2026-11-26 (holiday): skipping the nightly run` (or
  `(weekend)`) and exits 0. No layer, no DB connection, no order.
- Open day: prints `Market open on 2026-11-27: running the pipeline` and runs as before.
- Lookup failure: prints `WARNING: market calendar check failed for 2026-11-27 (<error>):
  running the pipeline` and runs as before.
- `--ignore-market-calendar`: prints `Market calendar check skipped
  (--ignore-market-calendar): running the pipeline` and runs as before; Alpaca is not called.
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
- AC5: If the calendar lookup fails (client cannot be built, the call raises, or the response
  is unusable), `run_all.py` prints a line starting `WARNING: market calendar check failed`
  and runs the pipeline; it never skips.
- AC6: `--ignore-market-calendar` runs the pipeline on a closed day without calling the
  calendar.
- AC7: `run_all.py` and `run_scoring.py` derive the score date with the same function,
  `factors.score_date.resolve_score_date`; the date checked by `run_all.py` is the date Layer 2
  scores (CLI `--date`, else `scoring.score_date`, else today).
- AC8: The Layer 2 SPY freshness guard is unchanged: on an open day with no SPY bar for the
  score date, Layer 2 still stops with exit 1.
- AC9: `docs/run_all.md` documents the skip and the `--ignore-market-calendar` flag.

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
- Security notes for reviewers/testers: the fail-open path is the safety property: confirm
  no exception from the check can lead to a skip.

## Edge cases
- Special closures (e.g. national days of mourning): not in any fixed list; Alpaca omits them,
  so they are reported as `holiday`.
- `scoring.score_date` set in `config.yaml` (backfill): the check uses that date, as Layer 2
  does.
- Missing Alpaca keys on a weekday: fail open with a warning (Layer 6 would fail later anyway).
- Calendar response with entries for other dates only: unusable, fail open.
- `--whatif` or `--tickers` runs on a closed day: skipped too (Layer 2 would fail anyway);
  `--ignore-market-calendar` forces them.

## Test guidance
- AC1 -> `test_run_all_market_calendar.py::test_holiday_skips_with_exit_0`,
  `test_market_calendar.py::test_no_entry_is_holiday`
- AC2 -> `test_run_all_market_calendar.py::test_weekend_skips_without_client`,
  `test_market_calendar.py::test_weekend_needs_no_client`
- AC3 -> `test_run_all_market_calendar.py::test_trading_day_runs`,
  `test_market_calendar.py::test_trading_day_is_open`
- AC4 -> `test_run_all_market_calendar.py::test_half_day_runs`,
  `test_market_calendar.py::test_half_day_is_open`
- AC5 -> `test_run_all_market_calendar.py::test_calendar_error_runs_with_warning` and the
  `test_market_calendar.py` unavailable cases
- AC6 -> `test_run_all_market_calendar.py::test_override_runs_on_holiday`
- AC7 -> `test_score_date.py`
- AC8 -> existing `test_scoring_freshness.py` (unchanged, still passing)
- AC9 -> `test_run_all_market_calendar.py::test_run_all_doc_mentions_override`

## Decision log
- 2026-11-27: Alpaca calendar over a new dependency: already a dependency with keys in `.env`,
  authoritative for broker trading days; one call per weekday run.
- 2026-11-27: Fail open on any lookup error: a wrong skip loses a trading day silently; a
  wrong run on a holiday is caught by the SPY guard, as on 2026-11-26.
- 2026-11-27: Weekends decided locally: no network on manual weekend runs.
- 2026-11-27: Check in `run_all.py` only: the goal is the nightly run; layer scripts run alone
  are manual and keep their current behaviour.
- 2026-11-27: Packet 68: 66 and 67 are merged on main; no open branch uses 68.
