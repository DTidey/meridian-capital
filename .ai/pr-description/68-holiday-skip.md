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
- Fail open: any calendar error prints `WARNING: market calendar check failed for <date>
  (<error>): running the pipeline` and runs; the SPY guard still catches a real holiday.
- Score date comes from one function, `factors.score_date.resolve_score_date`, used by both
  `run_all.py` and `run_scoring.py`, so the check and the guard cannot disagree.
- `--ignore-market-calendar` forces a run. Half-days run normally.

## Acceptance Criteria

- [x] AC1: Holiday: skip line, no layer, exit 0.
- [x] AC2: Weekend: skip line, no layer, exit 0, no Alpaca client.
- [x] AC3: Normal trading day runs as before.
- [x] AC4: Early-close half-day runs as before.
- [x] AC5: Calendar lookup failure prints a WARNING and runs; never skips.
- [x] AC6: `--ignore-market-calendar` runs on a closed day without calling the calendar.
- [x] AC7: `run_all.py` and `run_scoring.py` share `resolve_score_date`.
- [x] AC8: Layer 2 SPY freshness guard unchanged.
- [x] AC9: `docs/run_all.md` documents the skip and the flag.

## Security Review

- [x] Security considerations reviewed in `docs/specs/68-holiday-skip.md`: read-only Alpaca
  calendar call with the existing keys; `run_all.py` now loads `.env` like the layer scripts;
  fail-open on every error; no new dependency.

## Validation

- `make lint`: pass
- `make test`: 1843 passed
- `make security`: bandit clean; pip-audit fails on a pre-existing advisory unrelated to this
  change: virtualenv 21.14.12 (dev only), PYSEC-2026-4402, fixed in 21.14.13. Needs its own
  dependency packet; requirements files are not touched here.

## Risks and follow-ups

- One extra Alpaca `GET /v2/calendar` per weekday run. No new env var.
- If Alpaca's calendar wrongly returned no entry for a trading day, that day would be skipped;
  accepted, it is the broker's own calendar.
- The morning check can treat a log containing `Market closed on` as "market closed", not a
  failure.

Generated with [Claude Code](https://claude.com/claude-code)
