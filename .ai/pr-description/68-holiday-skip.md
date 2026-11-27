## Spec

- Spec: `docs/specs/68-holiday-skip.md`
- Test plan: `docs/test-plans/68-holiday-skip.md`
- PR draft path: `.ai/pr-description/68-holiday-skip.md`

## Summary

The 2026-11-26 (Thanksgiving) nightly run ran Layer 1 for 119 s, then Layer 2's SPY freshness
guard stopped it and the run was reported FAILED. A market holiday is now a clean skip.

- `run_all.py` checks the score date before Layer 1: weekends locally, weekdays against
  Alpaca's trading calendar (`get_calendar`, existing client and keys). Closed: prints
  `Market closed on 2026-11-26 (holiday): skipping the nightly run` and exits 0, no layer runs.
- Timeouts: the calendar request has socket connect / read timeouts (5 s / 10 s), set on the
  lookup's own client through a `requests.Session` subclass (alpaca-py sets none, so a server
  that accepts and never answers blocked forever). No global patch of the SDK or `requests`.
  Not an overall deadline: DNS resolution and a trickling response are not bounded.
- Fail open: any calendar error or timeout prints `WARNING: market calendar check failed for <date>
  (<error>): running the pipeline` and runs; the SPY guard still catches a real holiday.
- One score date per run: `run_all.py` resolves it once with
  `factors.score_date.resolve_score_date` (also used by `run_scoring.py`) and passes it with
  `--date` to Layers 2-6 and Book sync, so a run that crosses local midnight cannot check one
  day and score the next.
- Historical score date: if `scoring.score_date` differs from the run date (read once at
  start), `run_all.py` stops with an ERROR and exit 1 before any layer, unless
  `--no-execution`, which runs the other layers for that date. Book sync (which rewrites that
  date's `portfolio_history` and `portfolio_nav`) and Layer 6 never see a historical date.
- `--ignore-market-calendar` forces a run. Half-days run normally.

## Acceptance Criteria

- [x] AC1: Holiday: skip line, no layer, exit 0.
- [x] AC2: Weekend: skip line, no layer, exit 0, no Alpaca client.
- [x] AC3: Normal trading day runs as before.
- [x] AC4: Early-close half-day runs as before.
- [x] AC5: Calendar lookup failure, socket timeout or unusable response (not a list/tuple of
  entries with a plain `date`) prints a WARNING and runs; never skips. Only an empty list or
  tuple means holiday.
- [x] AC6: `--ignore-market-calendar` runs on a closed day without calling the calendar.
- [x] AC7: `run_all.py` and `run_scoring.py` share `resolve_score_date`.
- [x] AC8: Layer 2 SPY freshness guard unchanged.
- [x] AC9: `docs/run_all.md` documents the skip and the flag.
- [x] AC10: The calendar request has socket connect / read timeouts on its own client; a
  calendar that never answers raises `CalendarUnavailable` after the read timeout.
- [x] AC11: The score date is resolved once and passed with `--date` to Layers 2-6 and Book
  sync; crossing midnight leaves Layer 2 on the checked date.
- [x] AC12: A score date other than the run date never reaches Book sync or Layer 6: exit 1
  before any layer, or other layers only with `--no-execution`.

## Security Review

- [x] Security considerations reviewed in `docs/specs/68-holiday-skip.md`: read-only Alpaca
  calendar call with the existing keys; `run_all.py` now loads `.env` like the layer scripts;
  fail-open on every error; socket connect / read timeouts (5 s / 10 s); a historical score
  date never reaches Book sync or Layer 6 (data integrity of `portfolio_history` /
  `portfolio_nav`); no new dependency.

## Validation

- `make lint`: pass
- `make test`: 1878 passed
- Review fixes (Codex on ac79ffd): the 11 new timeout / one-date tests fail on ac79ffd and
  pass now; an unbounded SDK calendar call against a silent localhost socket was still
  blocked after 8 s
- Review fix 2 (Codex on 8d6cf3d): 7 of the 10 new historical-date tests fail on 8d6cf3d (the
  fake `sync_book` got `2026-11-20`); the 3 normal-path ones pass on both
- Review fix 3 (Codex on 09a7d38): an empty non-list calendar response (`{}`, `""`, empty
  set or iterator) was read as "holiday"; those 4 cases and the run_all-level empty-dict test
  fail on 09a7d38 and pass now. The other 6 malformed shapes already raised and pass on both
- `make security`: bandit clean; pip-audit fails on a pre-existing advisory unrelated to this
  change: virtualenv 21.14.12 (dev only), PYSEC-2026-4402, fixed in 21.14.13. Needs its own
  dependency packet; requirements files are not touched here.

## Risks and follow-ups

- One extra Alpaca `GET /v2/calendar` per weekday run, with socket connect / read timeouts
  (5 s / 10 s). These are not an overall deadline: DNS resolution and a response that keeps
  trickling bytes are not bounded, and the SDK retries a 429 three times, 3 s apart. No new
  env var.
- A historical `scoring.score_date` left in `config.yaml` now fails the nightly run at
  "Score date check" (exit 1) until it is set back to null.
- `run_execution.py --sync --date <past>` run by hand can still rewrite a past snapshot;
  only `run_all.py` is guarded here (backlog item added).
- The timeout uses alpaca-py's private `client._session`; if an upgrade removes it, every
  run logs the calendar WARNING and runs (never hangs, never skips).
- Under `run_all.py`, `--date` now overrides a per-layer `analysis.score_date` in
  `config.yaml` (null today); layers run alone are unchanged.
- If Alpaca's calendar wrongly returned no entry for a trading day, that day would be skipped;
  accepted, it is the broker's own calendar.
- The morning check can treat a log containing `Market closed on` as "market closed", not a
  failure.

Generated with [Claude Code](https://claude.com/claude-code)
