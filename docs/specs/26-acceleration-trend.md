# Revenue Acceleration From Accumulated History

**Spec file:** `docs/specs/26-acceleration-trend.md`
**Spec slug:** acceleration-trend
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The growth factor's `grw_rev_accel` (latest YoY revenue growth minus the YoY ending 12 months
  earlier) needs quarters 12 and 24 months back. Yahoo supplies about 5-7 quarters and the oldest
  stored quarter is 2024-09-30, so on 2026-10-14 no ticker (0 of 519) had it; the sub-factor is
  neutral for everyone.
- History should build up in the database, but a refresh overwrites stored values with blanks
  when Yahoo still lists an aging quarter without its figures (WMT and BBY 2025-01-31 revenue on
  2026-10-14).
- The user asked (2026-10-14) to let values build up and compute the metric from at least 4 and
  up to 8 stored values.

## Scope
In scope:
- Fundamentals upsert keeps existing values over blanks.
- New `grw_rev_accel` definition.

Out of scope / non-goals:
- Other growth sub-factors; backfilling history from other sources.

## Assumptions
- Year-on-year comparisons avoid seasonality (e.g. retail Q4); a per-quarter slope keeps tickers
  with different numbers of points on one scale, which matters because the sub-factor is
  sector-ranked.

## Proposed behavior / API
### Public interface
- `data.fundamentals._upsert_period`: coalescing upsert.
- `factors.growth._acceleration(df, col)`: new definition.

## Acceptance criteria
- AC1: Saving a period that already exists keeps each stored raw figure where the new fetch has
  none (NULL/NaN) and takes the new value where it has one. Ratio columns are kept only when the
  fetch has no raw figures at all; otherwise they come from the fetch (even if blank), so a ratio
  never outlives the figures it was computed from. `updated_at` is always refreshed.
- AC2: `_acceleration` computes YoY growth for each of the latest four quarters q = t, t-3, t-6,
  t-9 months (t = latest quarter with the column) for which both q and q-12 months are stored, and
  returns the least-squares slope of those growth rates per quarter (positive = accelerating).
- AC3: With fewer than 2 YoY points (fewer than 4 usable values) it returns NaN (neutral after
  ranking). At most 4 points (8 values) are used.
- AC4: Points are matched by calendar month, as in packet 25; a missing quarter only removes the
  points that need it.

## Security considerations
- No meaningful security impact: calculation and upsert semantics only.

## Edge cases
- A genuine restatement to a different value still overwrites; only blanks are ignored.
- A value can no longer be cleared to NULL by a refresh (by design); a raw figure Yahoo stops
  reporting keeps its last value.
- Partial fetch (some figures blank): raw figures merge; ratios that need a blank figure become
  blank rather than keeping a value from older figures.
- Zero revenue a year earlier: that point is skipped (no growth rate).
- Points need not be contiguous (e.g. t and t-9 only): the slope uses their actual positions.

## Test guidance
- AC1 -> `tests/test_fundamentals.py::TestUpsertKeepsHistory`
- AC2-AC4 -> `tests/test_growth.py::TestAccelerationTrend`

## Decision log
- 2026-10-14: Slope of YoY growth (user's 4-to-8 values) instead of YoY minus YoY a year earlier,
  which needs 24 months of history. Coverage on 2026-10-14: 206 of 519 tickers (143 with 2 points,
  63 with 3), versus 0.
- 2026-10-14: Coalescing upsert rather than a separate history table, so every reader of
  `fundamentals` benefits and nothing new needs keeping in sync.
- 2026-10-14 (Reviewer pass): no blockers. Important: coalescing ratios too could keep a ratio
  computed from figures that had since changed (new net income, blank revenue, old margin kept).
  Ratios are now kept only for fully blank fetches. Also: test for a blank-then-filled period;
  packet 02's acceleration definition marked as superseded.
