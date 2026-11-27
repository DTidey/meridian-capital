## Spec

- Spec: `docs/specs/68-holiday-skip.md`
- Test plan: `docs/test-plans/68-holiday-skip.md`
- PR draft path: `.ai/pr-description/68-holiday-skip.md`

## Summary

The 2026-11-26 (Thanksgiving) nightly run ran Layer 1 for 119 s, then Layer 2's SPY freshness
guard stopped it and the run was reported FAILED. A market holiday is now a clean skip. The
packet went through four Codex review rounds; the follow-ups are part of this PR.

- Holiday skip: `run_all.py` checks the score date before Layer 1: weekends locally, weekdays
  against Alpaca's trading calendar (`get_calendar`, existing client and keys). Closed: prints
  `Market closed on 2026-11-26 (holiday): skipping the nightly run` and exits 0, no layer
  runs. `--ignore-market-calendar` forces a run. Half-days run normally.
- Fail open: any calendar error prints `WARNING: market calendar check failed for <date>
  (<error>): running the pipeline` and runs; the SPY guard still catches a real holiday.
- Bounded calendar call (review 1): socket connect / read timeouts (5 s / 10 s), set on the
  lookup's own client through a `requests.Session` subclass (alpaca-py sets none, so a server
  that accepts and never answers blocked forever). No global patch of the SDK or `requests`.
  Not an overall deadline: DNS resolution and a trickling response are not bounded.
- Single score date per run (review 1): `run_all.py` reads the run date once, resolves the
  score date with `factors.score_date.resolve_score_date` (also used by `run_scoring.py`) and
  passes it with `--date` to Layers 2-6 and Book sync, so a run that crosses local midnight
  cannot check one day and score the next.
- Malformed calendar response treated as unavailable (review 3): the response must be a list
  or tuple of entries with a plain `datetime.date`; anything else (`{}`, `""`, a set, a
  string or `datetime` date) fails open with the WARNING instead of reading as a holiday.
  Only an empty list or tuple means holiday.
- Historical score dates refused with every flag (reviews 2 and 4): if `scoring.score_date`
  differs from the run date, `run_all.py` prints an ERROR, reports "Score date check" and
  exits 1 before the calendar check and any layer, including with `--no-execution` and
  `--whatif`. An audit (table in spec 68) found every step that takes the score date writes
  shared or current state: the halt lock and `risk_state.json` (Layer 5), PENDING approvals
  from today's book (Layer 4), the broker book, NAV and orders (Book sync, Layer 6), the
  "latest" CSV and the LLM cache (Layers 2-3).
- `docs/backlog.md`: two items: "Historical (backfill) runs" (the narrowed use case) and
  "Book sync against a past date from the CLI" (`run_execution.py --sync --date <past>` by
  hand is not guarded here).

## Acceptance Criteria

- [x] AC1: Holiday: skip line, no layer, exit 0.
- [x] AC2: Weekend: skip line, no layer, exit 0, no Alpaca client.
- [x] AC3: Normal trading day runs as before.
- [x] AC4: Early-close half-day runs as before.
- [x] AC5: Calendar lookup failure, socket timeout or malformed response (not a list/tuple of
  entries with a plain `date`) prints a WARNING and runs; never skips. Only an empty list or
  tuple means holiday.
- [x] AC6: `--ignore-market-calendar` runs on a closed day without calling the calendar.
- [x] AC7: `run_all.py` and `run_scoring.py` share `resolve_score_date`.
- [x] AC8: Layer 2 SPY freshness guard unchanged.
- [x] AC9: `docs/run_all.md` documents the skip, the flag and the today-only rule.
- [x] AC10: Bounded calendar call: socket connect / read timeouts on its own client; a
  calendar that never answers raises `CalendarUnavailable` after the read timeout.
- [x] AC11: Single score date per run, passed with `--date` to Layers 2-6 and Book sync;
  crossing midnight leaves Layer 2 on the checked date.
- [x] AC12: A score date other than the run date (past, future or malformed) stops
  `run_all.py` with exit 1 before the calendar check and any layer, with every flag; nothing
  writes the halt lock, risk state, approvals, book or NAV.

## Security Review

- [x] Security considerations reviewed in `docs/specs/68-holiday-skip.md`: read-only Alpaca
  calendar call with the existing keys; `run_all.py` now loads `.env` like the layer scripts;
  fail-open on every error; socket connect / read timeouts (5 s / 10 s); a historical score
  date never reaches any layer (no halt lock, `risk_state.json`, approvals, broker book,
  `portfolio_history` or `portfolio_nav` written for it); no new dependency.

## Validation

After rebasing onto `origin/main` (0f5725a, packet 69 virtualenv bump):
- `make lint`: pass (ruff check, ruff format --check)
- `make test`: 1887 passed
- `make security`: pass; bandit clean, pip-audit "No known vulnerabilities found" (main now
  pins virtualenv 21.14.13). No requirements file differs from `main`.
- `.github/scripts/validate_pr.py` on this draft: pass

Regression checks for each review round (new tests run against the previous commit):
- Review 1 (Codex on ac79ffd): the 11 new timeout / one-date tests fail on ac79ffd; an
  unbounded SDK calendar call against a silent localhost socket was still blocked after 8 s
- Review 2 (Codex on 8d6cf3d): 7 of the 10 new historical-date tests fail on 8d6cf3d (the
  fake `sync_book` got `2026-11-20`); the 3 normal-path ones pass on both
- Review 3 (Codex on 09a7d38): the 4 empty non-list response cases and the run_all-level
  empty-dict test fail on 09a7d38; the other 6 malformed shapes already raised
- Review 4 (Codex on 5caed0b): with `--no-execution` and a past date, the real Layer 5
  circuit-breaker sequence wrote `halt.lock` (and with `--whatif` overwrote `risk_state.json`)
  in a temp cache dir; those 2 tests and the 3 `--no-execution` refusal cases fail on 5caed0b
- Final finding: the malformed `scoring.score_date` test now pins a valid run date and checks
  the AC12 refusal (exit 1, no calendar check, no layer) instead of a fail-open run

## Risks and follow-ups

- One extra Alpaca `GET /v2/calendar` per weekday run, with socket connect / read timeouts
  (5 s / 10 s). These are not an overall deadline: DNS resolution and a response that keeps
  trickling bytes are not bounded, and the SDK retries a 429 three times, 3 s apart. No new
  env var.
- A historical `scoring.score_date` left in `config.yaml` now fails the nightly run at
  "Score date check" (exit 1) until it is set back to null.
- Narrowed use case: no historical (backfill) runs through `run_all.py`, not even
  `--no-execution`; review 2's documented analysis-only backfill is withdrawn (backlog item).
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
