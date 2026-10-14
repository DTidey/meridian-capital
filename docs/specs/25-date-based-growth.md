# Date-Based Growth Comparisons

**Spec file:** `docs/specs/25-date-based-growth.md`
**Spec slug:** date-based-growth
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- Year-on-year and quarter-on-quarter comparisons pick the earlier period by row position:
  - `factors/growth.py` `_yoy` and `_acceleration`: latest row vs 4 (and 8) rows back.
  - `factors/quality.py` `_gm_trend`: latest gross margin vs 4 rows back.
  - `data/fundamentals.py` stored `revenue_growth_qoq/yoy` and `earnings_growth_qoq/yoy`:
    the next column and 4 columns back (1 for annual).
- On 2026-10-14, 10 tickers' quarterly rows had a gap (BBY, INTU, KR, LULU, MRVL, HONA, FERG,
  SKYD and two others), so "4 rows back" was 15 months back.
- Since packet 23, BAC's and GS's newest quarter is an empty row, so the growth factor's latest
  values are empty and their growth score defaults to neutral.
- The user asked for this fix on 2026-10-14.

## Scope
In scope:
- Date-based comparisons in the growth factor, quality's margin trend and ingestion's stored
  growth ratios.

Out of scope / non-goals:
- Other factors; the ratio formulas themselves.

## Assumptions
- `period_end` is a month-end since packet 23 (test fixtures use month starts); matching on
  calendar year and month covers both.

## Proposed behavior / API
### Public interface
- New `factors._utils.value_and_prior(df, col, months) -> (latest, prior)`.
- `factors.growth._yoy`, `_acceleration`, and the R&D-intensity row; `factors.quality._gm_trend`.
- `data.fundamentals._prior_period(period_data, ends, i, months)`.

## Acceptance criteria
- AC1: `value_and_prior` returns the column's value in the latest row where it is present, and
  its value in the row whose period is exactly `months` calendar months earlier (same year and
  month); the prior is None if that row is missing or empty.
- AC2: Growth YoY (revenue, net income, FCF) compares the latest reported value with the value 12
  months earlier; acceleration compares that with the YoY ending 12 months earlier (needing 24
  months back). A missing period gives no value, never a different period's.
- AC3: R&D intensity uses the latest row with revenue present.
- AC4: Quality's gross-margin trend compares the latest reported gross margin with the one 12
  months earlier.
- AC5: Stored QoQ ratios compare with the period 3 months earlier, YoY with 12 months earlier
  (annual rows: both 12 months), chosen by date within the fetched statements, in both the
  yfinance and FMP paths.

## Security considerations
- No meaningful security impact: calculation changes only.

## Edge cases
- Empty newest quarter (BAC/GS): the latest reported quarter is used.
- Quarter missing 12 months back: YoY empty (neutral after ranking), not 15 months back.
- Annual rows: QoQ and YoY both compare with the prior year, as before for contiguous years.

## Test guidance
- AC1 -> `tests/test_growth.py::TestValueAndPrior`
- AC2, AC3 -> `tests/test_growth.py::TestDateBased`
- AC4 -> `tests/test_quality.py::TestGmTrendDates`
- AC5 -> `tests/test_fundamentals.py::TestGrowthRatiosByDate`

## Decision log
- 2026-10-14: Exact calendar-month match rather than "nearest row about a year back", so a
  missing quarter gives an honest blank instead of a 9- or 15-month comparison.
- 2026-10-14 (Reviewer pass): no blockers. Applied the date rule to the FMP path too, made
  `value_and_prior` independent of index labels, and added annual, leap-year and
  24-months-back tests.
