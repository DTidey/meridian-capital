# Ticker Valuation

**Spec file:** `docs/specs/21-ticker-valuation.md`
**Spec slug:** ticker-valuation
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The dashboard doesn't show a company's valuation. Market cap and EV are computed inside the
  value factor (`factors/value.py`) but never stored or shown.
- The user chose (2026-10-14) a core set on the Ticker page: market cap, enterprise value,
  trailing P/E, EV/EBITDA, price/book, FCF yield and dividend yield.
- EBITDA can't be computed: `fundamentals` has no depreciation and amortisation. The value
  factor's "ev_ebitda" sub-factor actually uses EBIT. The page shows EV/EBIT, labelled as such.

## Scope
In scope:
- A "VALUATION" section on the Ticker page, computed from stored prices and quarterly
  fundamentals.

Out of scope / non-goals:
- Sector comparisons; other pages; storing valuation.
- Fundamentals ingestion, including duplicate annual rows (12 near-duplicate annual pairs,
  e.g. AAPL FY2025 at 2025-09-27 and 2025-09-30 with different debt and shares).
- Renaming the value factor's `val_ev_ebitda_inv`.

## Assumptions
- Quarterly `fundamentals` rows hold single-quarter flow figures (checked: AAPL's Dec 2025
  quarter revenue is 143.8B, consistent with a single quarter) and there are no duplicate
  quarters (checked 2026-10-14).
- `dividends_paid` is negative (cash outflow); its absolute value is the amount paid.
- `daily_prices.close` is the unadjusted close, as used by the value factor for market cap.

## Proposed behavior / API
### Public interface
- New `dashboard/ticker_valuation.py`: `render_valuation(engine, ticker)` and pure
  `valuation(close, quarters) -> dict`, `fmt_money(value)`, `fmt_multiple(value)`,
  `fmt_pct(value)`.
- `dashboard/page_ticker.py`: calls `render_valuation` after the price section.

### Inputs / outputs
- Inputs: latest `daily_prices.close`; the four latest quarterly `fundamentals` rows.
- Outputs: metric cards and an as-of caption. No writes.

## Acceptance criteria
- AC1: Market cap = latest close × latest quarterly `shares_outstanding`. Enterprise value =
  market cap + `total_debt` − `cash` from the latest quarter, shown only when both are present.
- AC2: Trailing (TTM) figures sum the four latest quarterly rows, and only when those four span
  at most 300 days (first to last `period_end`; contiguous quarters are about 9 months apart, so
  one missing quarter fails) and every value used is present; otherwise "—".
- AC3: P/E = market cap / TTM `net_income`; EV/EBIT = EV / TTM `ebit`; P/B = market cap /
  latest `total_equity`. A zero or negative numerator or denominator shows "n/m".
- AC4: FCF yield = TTM `fcf` / market cap (may be negative); dividend yield = TTM
  |`dividends_paid`| / market cap, summing each quarter's absolute value.
- AC5: Money is shown as $T, $B or $M with 2, 1 and 0 decimals respectively (e.g. $5.01T,
  $84.3B, $950M; negatives as -$5.0B; below $1M in whole dollars; rounding never shows 1000 of
  a unit); multiples to 1 decimal with "x"; yields as percentages to 1 decimal. A
  caption gives the price date and the latest quarter end used.
- AC6: Without a price or a quarterly row with shares, the section shows "No valuation data."
  and no cards. The page renders with an empty database apart from the universe (packet 18
  smoke test still passes).

## Security considerations
- No meaningful security impact: read-only, existing tables, values are numbers formatted by
  the code; no new HTML or external input.

## Edge cases
- Banks (no EBIT, no FCF): EV/EBIT and FCF yield show "—".
- Fewer than four quarters, or a gap (span over 300 days): TTM metrics "—".
- Negative EV (cash above market cap plus debt): EV/EBIT "n/m".
- Negative equity (buyback-heavy companies): P/B "n/m".
- Missing debt or cash: EV and EV/EBIT "—".

## Test guidance
- AC1-AC5 -> `tests/test_ticker_valuation.py`
- AC6 -> `tests/test_ticker_valuation.py`, `tests/test_page_ticker.py::TestRenderSmoke`

## Decision log
- 2026-10-14: EV/EBIT instead of EV/EBITDA; D&A isn't stored, and EBIT is what the value factor
  already uses.
- 2026-10-14: TTM from quarterly rows, not the latest annual row, so figures are current and the
  duplicate annual rows are avoided.
- 2026-10-14 (Reviewer pass): no blockers. Fixed: TTM span limit was 13 months, which let one
  missing quarter through; now 300 days. Also: negative money formatting, unit rounding,
  negative EV multiples as "n/m", dividends summed per quarter as absolute values.
