## Summary

- Adds Page VII — TICKER to the Streamlit dashboard: a searchable ticker dropdown that loads all
  stored information for the selected ticker from the existing database tables.
- Renders a full-history `adj_close` area chart with five KPI cards (latest close, 52W high/low,
  30-day average volume, YTD return).
- Displays a current position card (direction, shares, entry price, unrealized P&L, weight) only
  when the ticker is held in `portfolio_positions`.
- Shows factor scores (composite + all seven dimensions) as metric cards and a horizontal bar chart,
  AI scores, annual fundamentals, analyst estimates, short interest, and up to 12 recent insider
  transactions — all sourced from existing tables with no schema changes.
- Wires the new page into `dashboard/app.py` (`PAGES` dict and import/route branch).

## Spec

- Spec: `docs/specs/08-ticker-page.md`
- Test plan: `docs/test-plans/08-ticker-page.md`
- PR draft path: `.ai/pr-description/08-ticker-page.md`

## Acceptance Criteria

- [x] AC1: Page VII renders without error when `sp500_universe` is populated; dropdown lists all
  tickers alphabetically as `TICKER  —  Company Name`.
- [x] AC2: Selecting a ticker with price data renders a Plotly adj_close area chart; selecting one
  with no price data renders a caption rather than raising.
- [x] AC3: Five KPI cards show latest close, 52W high (green), 52W low (red), 30-day avg volume,
  and YTD return (green/red); YTD falls back to 0.0 when fewer than two YTD rows exist.
- [x] AC4: A LONG/SHORT badge appears in the header and a position section renders when the ticker
  is held in `portfolio_positions`; neither appears when the ticker is not held.
- [x] AC5: Factor scores section shows composite + seven dimension cards (green ≥ 60, red ≤ 40,
  neutral otherwise) plus a horizontal bar chart with dashed midline; a caption appears when no
  scores exist.
- [x] AC6: AI scores section shows five score cards when rows exist in `ai_scores`; section is
  omitted entirely when no rows exist.
- [x] AC7: Fundamentals section shows eight metric cards from the most recent annual row; a caption
  appears when no annual fundamentals exist.
- [x] AC8: Analyst estimates and short interest sub-sections each render data cards or a "No data"
  caption as appropriate.
- [x] AC9: Insider transactions section renders up to 12 rows in a dataframe ordered by date
  descending; shows a caption when no rows exist.
- [x] AC10: Empty `sp500_universe` renders an info message and returns without raising; earnings
  date caption appears when a row exists in `earnings_calendar`.

## Security Review

- [x] Security considerations reviewed in `docs/specs/08-ticker-page.md`
- [x] No new external API calls, no new write paths, no user-controlled SQL input

## Validation

- [x] `make lint`
- [x] `make test`
- [x] `make security`

## GitHub Checks

- Required checks for `main`:
  - `CI / test`
  - `CodeQL / analyze`

## Changelog

- [x] Add to `CHANGELOG.md` under `## Unreleased`

## Open Risks

- `adj_close` values entirely null for a ticker would cause an unguarded `iloc[-1]` error; this
  cannot occur with Layer 1 ingestion behaviour but is noted for awareness.
