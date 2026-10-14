# Valuation TTM With an Empty Newest Quarter

**Spec file:** `docs/specs/24-valuation-ttm-empty-quarter.md`
**Spec slug:** valuation-ttm-empty-quarter
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- After packet 23 aligned statements by period, BAC and GS have a 2026-06-30 quarterly row that
  is empty on every statement (Yahoo lists the period but has no figures yet). The Ticker page
  valuation (packet 21 AC2) needs the four latest quarters all present, so P/E, EV/EBIT and the
  yields show "—" for them. Before packet 23 the row held a neighbouring quarter's figures.
- The user asked for this fix on 2026-10-14.

## Scope
In scope:
- TTM selection in `dashboard/ticker_valuation.py`.

Out of scope / non-goals:
- Ingestion, and the factors (value takes the latest non-empty value per field; quality skips
  quarters without a balance sheet since packet 23).

## Assumptions
- Four contiguous quarter-ends span about 273 days; skipping a quarter in the middle makes about
  365, so packet 21's 300-day limit still rejects gaps.

## Proposed behavior / API
### Public interface
- `dashboard.ticker_valuation._ttm(quarters, field)`: new selection rule; `render_valuation`
  loads the eight latest quarters.

## Acceptance criteria
- AC1: A TTM figure sums, per field, the four latest quarters that have that field, provided
  those four span at most 300 days (first to last `period_end`) and the newest of them is within
  100 days of the newest stored quarter (at most one quarter skipped); otherwise "—". This
  replaces packet 21 AC2's "four latest quarters, all present".
- AC2: With BAC's layout (newest quarter empty, the four before it complete), P/E, EV/EBIT and
  the yields are computed from those four earlier quarters.
- AC3: A missing quarter in the middle of the series still gives "—".
- AC4: The caption says TTM figures use the last four reported quarters.

## Security considerations
- No meaningful security impact: read-only display logic.

## Edge cases
- Different fields can use different quarters (e.g. income published, cash flow not): each is
  the latest four reported for that field.
- Fewer than four quarters with the field: "—".

## Test guidance
- AC1-AC3 -> `tests/test_ticker_valuation.py::TestTtmSelection`, `TestTtmRecency`
- AC4 -> `tests/test_page_ticker.py::TestRenderSmoke`

## Decision log
- 2026-10-14: Skip empty quarters per field instead of dropping empty rows at ingestion, so the
  stored data still shows the period Yahoo lists, and the factors' own handling is unchanged.
- 2026-10-14 (Reviewer pass): no blockers. Important: without a recency limit, up to four
  skipped quarters could show a year-old figure unlabelled (e.g. a stopped dividend as last
  year's yield). Added the 100-day limit and tests, including that a zero dividend counts.
