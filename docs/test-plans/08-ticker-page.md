# Test Plan: ticker-page

Path: `docs/test-plans/08-ticker-page.md`

## What changed

- Added `dashboard/page_ticker.py` — a new Streamlit page (VII — TICKER) with a ticker dropdown,
  closing price chart, and sections for factor scores, AI scores, fundamentals, analyst estimates,
  short interest, and insider transactions.
- Modified `dashboard/app.py` to register the page in `PAGES` and route to it.

## Acceptance criteria coverage

- AC1: `tests/test_page_ticker.py::test_ticker_list_query` — inserts two `sp500_universe` rows and
  asserts the query returns them in ascending ticker order with the expected ticker and company_name
  values.

- AC2: `tests/test_page_ticker.py::test_price_chart_no_data_does_not_raise` — calls the DB query
  for a ticker absent from `daily_prices` and asserts an empty DataFrame is returned without raising.
  The Plotly chart rendering itself is a manual smoke test: navigate to page VII, select a ticker
  with prices, and confirm the area chart is visible.

- AC3: `tests/test_page_ticker.py::test_price_kpi_52w` — builds a 260-row price fixture and
  asserts `tail(252).max()` and `tail(252).min()` match the known high and low.
  `test_ytd_return_positive` — inserts prices spanning two years and asserts the YTD return formula
  yields a positive value when the latest price exceeds the year-start price.
  `test_ytd_return_negative` — asserts a negative YTD return when the latest price is below the
  year-start price.
  `test_ytd_return_single_row` — asserts YTD return falls back to 0.0 when only one row exists in
  the current calendar year.

- AC4: Manual dashboard smoke test — select a ticker present in `portfolio_positions` and confirm
  the LONG/SHORT badge appears in the header and the "CURRENT POSITION" section renders five metric
  cards. Select a ticker not in `portfolio_positions` and confirm neither the badge nor the section
  appears.

- AC5: `tests/test_page_ticker.py::test_score_colour_green` — `_score_colour(75)` returns
  `LONG_COL`. `test_score_colour_red` — `_score_colour(25)` returns `SHORT_COL`.
  `test_score_colour_neutral` — `_score_colour(50)` returns `NEUTRAL`.
  `test_score_colour_none` — `_score_colour(None)` returns `NEUTRAL` without raising.
  Manual smoke test: select a ticker with factor scores and confirm the bar chart renders with a
  dashed midline at 50.

- AC6: Manual smoke test — select a ticker absent from `ai_scores` and confirm the AI SCORES
  section does not appear. Select a ticker with an AI scores row and confirm five metric cards are
  rendered.

- AC7: Manual smoke test — select a ticker with annual fundamentals and confirm all eight metric
  cards are present (revenue, gross margin, operating margin, ROE, debt/equity, FCF, revenue growth
  YoY, current ratio). Select a ticker with no fundamentals and confirm the caption appears.

- AC8: Manual smoke test — select a ticker absent from `analyst_estimates` and confirm the
  "No analyst data." caption appears. Select a ticker absent from `short_interest` and confirm
  "No short interest data." caption appears.

- AC9: `tests/test_page_ticker.py::test_insider_query_limit` — inserts 15 `insider_transactions`
  rows for one ticker and asserts the query returns exactly 12 rows ordered by date descending.

- AC10: `tests/test_page_ticker.py::test_empty_universe_returns_early` — calls the universe query
  against an empty `sp500_universe` table and asserts an empty list is returned; the render guard
  condition (`if not universe_rows: return`) is exercised without raising.

## Edge cases

- Ticker with `adj_close` values all null: `price_df["adj_close"].iloc[-1]` would raise; the
  current implementation does not guard against this — acceptable because Layer 1 ingestion always
  populates `adj_close` for any row it inserts. Flagged for awareness.
- `volume` column entirely null for a ticker: guarded by `vol_series = price_df["volume"].dropna()`;
  `avg_vol_30d` returns 0.
- YTD with fewer than two rows in the current year: guarded by `len(ytd_df) > 1`; falls back to
  0.0.
- Score values at the exact boundary (40 or 60): `_score_colour` uses `>=` and `<=` so 60 is green
  and 40 is red.
- `next_earnings` row with null `eps_estimate`: the `if eps_est is not None` guard omits the EPS
  clause from the caption.
- Ticker present in `portfolio_positions` with a null `direction`: `pos_badge` remains empty; no
  badge is rendered.

## Notes

- Streamlit widget calls (`st.selectbox`, `st.plotly_chart`, etc.) cannot be exercised in pytest
  without a running Streamlit server; all rendering ACs are therefore manual smoke tests.
- The `_score_colour` and KPI calculation logic is pure Python and fully unit-testable.
- DB query correctness (ordering, filtering, limit) is unit-tested via the `tmp_engine` SQLite
  fixture from `conftest.py`.
- No OpenAI API calls are made by this page; no mocking of external services is required.
