## Summary

Makes the growth factor's revenue-acceleration sub-factor usable and lets fundamentals history
build up.

- **History preserved.** A refresh no longer overwrites stored figures with blanks (Yahoo keeps
  listing aging quarters after blanking them, e.g. WMT 2025-01-31). Raw figures merge field by
  field; ratios are kept only when the whole fetch is blank, so a ratio never outlives the
  figures it came from.
- **Acceleration redefined.** It was "latest YoY minus YoY a year earlier", which needs 24 months
  of quarters; no ticker had them (0 of 519). It is now the trend (least-squares slope per
  quarter) of YoY revenue growth over the latest four quarters: at least 2 points (4 values), at
  most 4 (8 values). YoY points avoid seasonality; the slope keeps 2-, 3- and 4-point tickers on
  one scale.
- Live: 203 of 503 tickers now have a value; coverage grows as history accumulates.

## Spec

- Spec: `docs/specs/26-acceleration-trend.md`
- Test plan: `docs/test-plans/26-acceleration-trend.md`
- PR draft path: `.ai/pr-description/26-acceleration-trend.md`

## Acceptance Criteria

- [x] AC1: Blanks don't overwrite stored figures; ratios only kept for fully blank fetches.
- [x] AC2: Acceleration is the slope of up to four YoY points.
- [x] AC3: Fewer than 2 points gives NaN; at most 4 points used.
- [x] AC4: Points matched by calendar month; a missing quarter only drops the points needing it.

## Security Review

- [x] Security considerations reviewed in `docs/specs/26-acceleration-trend.md` (no meaningful
  impact)

## Validation

- Tests written first: 11 failed on the old code
- `make lint`: pass
- `make security`: pass
- `make test`: 868 passed
- PostgreSQL upsert behaviour checked in a rolled-back transaction
- Live: acceleration for 203 of 503 tickers (was 0)

Reviewer pass (fresh subagent): no blockers. Fixed the stale-ratio risk from coalescing ratios
on partial fetches; added a blank-then-filled test; marked packet 02's definition superseded.

## Notes

- A refresh can no longer clear a stored figure to blank (by design).
- No data run needed: the factor change applies at the next scoring run, and history is
  protected from the next fundamentals refresh on.
