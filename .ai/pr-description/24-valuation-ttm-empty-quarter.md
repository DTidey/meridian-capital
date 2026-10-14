## Summary

After packet 23, BAC and GS have a newest quarter (2026-06-30) that Yahoo lists but hasn't
filled in, so the Ticker page valuation showed "—" for P/E, EV/EBIT and the yields. Trailing
figures now sum the four latest quarters that report each figure, still requiring them to fall
within a year (300 days) and at most one quarter old, so a gap in the middle or a stale year
(e.g. a stopped dividend) still shows "—".

Live: BAC P/E 12.1x and GS 14.5x (previously "—"); other tickers unchanged.

## Spec

- Spec: `docs/specs/24-valuation-ttm-empty-quarter.md`
- Test plan: `docs/test-plans/24-valuation-ttm-empty-quarter.md`
- PR draft path: `.ai/pr-description/24-valuation-ttm-empty-quarter.md`

## Acceptance Criteria

- [x] AC1: TTM over the four latest quarters reporting each field, within 300 days and at
  most one quarter skipped.
- [x] AC2: BAC-style empty newest quarter skipped.
- [x] AC3: A missing middle quarter still gives "—".
- [x] AC4: Caption says "last four reported quarters".

## Security Review

- [x] Security considerations reviewed in `docs/specs/24-valuation-ttm-empty-quarter.md` (no
  meaningful impact)

## Validation

- Tests written first: 2 failed on the old rule
- `make lint`: pass
- `make test`: 841 passed
- Live: BAC and GS P/E now shown; JPM and AAPL unchanged

Reviewer pass (fresh subagent): no blockers; the missing recency limit (stale figures from
skipped quarters) is fixed and tested.
