## Summary

Growth comparisons now pick the earlier period by date instead of row position.

- The growth factor's YoY (revenue, earnings, FCF) and acceleration, and the quality factor's
  gross-margin trend, compare the latest reported quarter with the quarter exactly 12 (or 24)
  months earlier. A missing quarter gives no value instead of a 15-month comparison (10 tickers
  had gaps), and an empty newest quarter is skipped.
- BAC and GS get real growth scores again (36.8 and 68.4); since packet 23 their empty newest
  quarter had left them at the neutral default.
- Stored QoQ/YoY growth ratios are computed against dated periods at ingestion.

## Spec

- Spec: `docs/specs/25-date-based-growth.md`
- Test plan: `docs/test-plans/25-date-based-growth.md`
- PR draft path: `.ai/pr-description/25-date-based-growth.md`

## Acceptance Criteria

- [x] AC1: `value_and_prior` matches by calendar month.
- [x] AC2: Growth YoY and acceleration by date.
- [x] AC3: R&D intensity from the latest row with revenue.
- [x] AC4: Quality's gross-margin trend by date.
- [x] AC5: Stored QoQ/YoY ratios by date.

## Security Review

- [x] Security considerations reviewed in `docs/specs/25-date-based-growth.md` (no meaningful
  impact)

## Validation

- Tests written first: they failed to import the missing helper, and the gap tests encode the
  date rule
- `make lint`: pass
- `make security`: pass
- `make test`: 855 passed
- Live: BAC and GS growth scores restored; other tickers shift slightly

Reviewer pass (fresh subagent): no blockers. The FMP path now uses the same date rule; the
lookup no longer depends on index labels; annual, leap-year and 24-months-back tests added.

## Known, not changed

- Revenue acceleration needs 24 months of quarters; Yahoo supplies about 5-7, so it is neutral
  for every ticker (before and after).

## After merge

Run `python run_data.py --no-filings --no-13f --refresh-fundamentals` once to rewrite the stored
growth ratios. The factor changes take effect at the next scoring run.
