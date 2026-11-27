# Changelog 2026-11-27

## Packet 68: skip the nightly run on US market holidays

- **Alpaca calendar, no new dependency.** alpaca-py and the keys are already used by Book sync
  and Layer 6; the broker's calendar includes special closures a rules-based holiday list
  misses. `exchange_calendars` would save one HTTP call but adds holiday tables to keep current.
- **Fail open.** A wrong skip silently loses a trading day; a wrong run on a holiday is caught by
  the Layer 2 SPY guard, exactly as on 2026-11-26. So every error in the check (missing keys,
  network, odd response, unparseable `scoring.score_date`) prints a WARNING and runs.
- **Response validation.** An empty list means holiday; a list containing the day means open; a
  list with only other dates, or anything without `.date`, is treated as a failure, so a
  malformed answer can never be read as "closed".
- **Weekends locally.** No network for manual weekend runs, and the cron never hits them.
- **Score date shared, not duplicated.** `resolve_score_date` moved out of `run_scoring.py` into
  `factors/score_date.py`. (Superseded by the review fixes below: `run_all.py` now resolves the
  date once and forwards it with `--date` to Layers 2-6 and Book sync.)
- **`load_dotenv` in `run_all.py`.** Needed for `get_client`. Child layers load the same `.env`
  and `load_dotenv` does not override, so their environment is unchanged.
- **`make security` red on main, not fixed here.** pip-audit reports virtualenv 21.14.12
  (PYSEC-2026-4402, fix 21.14.13, dev only). Unrelated; left for a dependency packet.

## Packet 68 review fixes (Codex on ac79ffd)

- **Calendar timeout.** alpaca-py 0.45.0 calls `requests.Session.request` with no timeout;
  against a localhost socket that never answers, `get_calendar` was still blocked after 8 s.
  Fix: `closed_reason` swaps the `_session` of the client it just built (`get_client()` makes a
  new `TradingClient` each call) for a `requests.Session` subclass that defaults
  `timeout=(5, 10)`. Chosen over a global patch of `requests` / the SDK (would change Book
  sync, Layer 6 and every other HTTP call in the process) and over a watchdog thread (no real
  socket timeout, leaves a blocked thread). Cost: depends on the private `_session`; if it
  disappears the check raises `CalendarUnavailable` before calling, so the run warns and goes
  on. `test_real_sdk_client_uses_the_timeout` would catch an SDK change that bypasses it.
- **One score date per run.** The check resolved the date, then each layer read
  `date.today()` again; a Friday full run that passes midnight would check D and score D+1.
  `run_all.py` now resolves once and passes `--date` to Layers 2-6 and Book sync. Layer 1 has
  no score date; Layer 7's `--date` is a report/letter date, so neither gets it. Side effect:
  under `run_all.py`, `analysis.score_date` (a separate config key, null today) no longer
  overrides `scoring.score_date` for Layer 3; one date for the whole run is the point.
- **Config read moved out of the fail-open try.** The date is now needed by every layer, not
  only the check, so an unreadable `config.yaml` stops `run_all.py` before Layer 1 (exit 1)
  instead of warning and then failing in every layer. Never a skip.

## Packet 68 review fixes 2 (Codex on 8d6cf3d)

- **Historical score date reached Book sync.** Passing `--date` to every dated layer (review
  fix 1) also passed a backfill `scoring.score_date` to `run_execution.py --sync`, and
  `sync_book` replaces that date's `portfolio_history` snapshot with today's broker book and
  overwrites its `portfolio_nav`; Book sync runs under `--whatif` too, and Layer 6 would trade
  against that date. Before the packet `run_execution.py` always used `date.today()`.
- **Stop, not skip.** A score date different from the run date exits 1 before any layer unless
  `--no-execution`. Silently dropping sync/execution would let a date forgotten in
  `config.yaml` turn every nightly run into a no-trade run reported as success; a failure at
  "Score date check" shows up at the 06:30 check. `--no-execution` keeps the analysis-only
  backfill. Future dates are refused the same way.
- **One clock read.** `factors.score_date.run_date()` is read once and passed to
  `resolve_score_date(today=...)`, so with a null config the score date equals the run date
  by construction, and a run that crosses midnight is never seen as historical.
- **Defence in depth.** Book sync and Layer 6 are skipped whenever the dates differ, not only
  through `--no-execution`.
- **Not covered:** running `run_execution.py --sync --date <past>` by hand; added to
  `docs/backlog.md`.
- **Timeout wording.** requests' connect/read timeouts are per socket operation: they do not
  bound DNS resolution or a trickling response. Docs now say "socket connect/read timeouts
  (5 s / 10 s)" with that caveat; no overall deadline in this packet.

## Packet 69: virtualenv PYSEC-2026-4402

- Pinned with `pip-compile --upgrade-package virtualenv==21.14.13` on `requirements-dev.in`
  (the Makefile `compile` flags: no hashes, default resolver, no constraints), so only the
  virtualenv line moved. `requirements.txt` does not pin virtualenv.
- Compiled and tested under Python 3.12 (the lock header's version), in a throwaway venv under
  `/tmp`, not `.venv`, because `.venv` is a symlink to the shared venv.
- Numbered 69: 68 is in use on the open `hermes/holiday-skip` branch (PR #152).
