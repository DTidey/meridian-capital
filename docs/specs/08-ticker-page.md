# Ticker Detail Page

**Spec file:** `docs/specs/08-ticker-page.md`
**Status:** Done
**Date:** 2026-06-19

## Purpose

Add a seventh page to the Streamlit dashboard (Page VII — TICKER) that allows the user to select
any ticker from the S&P 500 universe via a searchable dropdown and view all stored information for
that ticker in one place: a closing price chart, portfolio position (if held), factor scores,
AI scores, fundamentals, analyst estimates, short interest, and recent insider transactions.

The page is entirely read-only; it writes to no tables.

## Acceptance criteria

- AC1: Navigating to page VII "TICKER" in the dashboard renders without error when `sp500_universe`
  is populated; the dropdown lists every ticker in ascending alphabetical order in the format
  `TICKER  —  Company Name`.

- AC2: Selecting a ticker that has rows in `daily_prices` renders a Plotly area chart of `adj_close`
  over the full available history, using the indigo accent colour and a transparent fill. Selecting a
  ticker with no price rows renders a caption ("No price data loaded for this ticker.") rather than
  raising an exception.

- AC3: Five KPI cards below the chart display: latest `adj_close`, 52-week high (green), 52-week
  low (red), 30-day average volume, and YTD return (green when ≥ 0, red when < 0). YTD return is
  computed from the first trading day of the current calendar year; when fewer than two YTD rows
  exist the value is 0.0.

- AC4: When the selected ticker is present in `portfolio_positions`, a LONG or SHORT badge appears
  inline in the page header and a "CURRENT POSITION" section renders five metric cards: direction
  (coloured green for LONG, red for SHORT), shares, entry price, unrealized P&L (coloured
  green/red), and portfolio weight. When the ticker is not held neither the badge nor the section
  appears.

- AC5: The "FACTOR SCORES" section shows the most recent row from `factor_scores` for the selected
  ticker, displaying `composite_score` and the seven factor composite scores (momentum, value,
  quality, growth, revisions, short_interest, insider) as metric cards. Cards with a score ≥ 60
  are coloured green, ≤ 40 red, and between 40 and 60 neutral. A horizontal bar chart of all eight
  scores is rendered beneath the cards with a dashed vertical line at 50. When no factor scores
  exist the section shows a caption instead.

- AC6: The "AI SCORES" section shows the most recent row from `ai_scores` for the selected ticker
  (earnings, filing, risk, insider_ai, ai_composite), applying the same green/red/neutral colouring
  as AC5. The section is omitted entirely when no AI scores exist for the ticker.

- AC7: The "FUNDAMENTALS" section shows the most recent row where `period_type = 'annual'` from
  `fundamentals`, displaying revenue (billions), gross margin, operating margin, ROE, debt-to-equity,
  FCF (billions), revenue growth YoY, and current ratio. When no annual fundamentals exist the
  section shows a caption instead.

- AC8: The "ANALYST ESTIMATES" sub-section shows the most recent row from `analyst_estimates`
  (price target, forward EPS, analyst count). The "SHORT INTEREST" sub-section shows the most
  recent row from `short_interest` (% float short coloured red when > 10%, days to cover, shares
  short in millions). Each sub-section renders a caption when no rows exist.

- AC9: The "RECENT INSIDER TRANSACTIONS" section shows up to 12 of the most recent rows from
  `insider_transactions` for the selected ticker in a dataframe with columns: Date, Name, Title,
  Type, Shares, Price. When no rows exist the section shows a caption.

- AC10: When `sp500_universe` is empty the page renders an info message ("Universe not loaded. Run
  `python run_data.py` first.") and returns without raising an exception. The next/last earnings
  date from `earnings_calendar` is shown as a caption beneath the price KPIs when a row exists.

## Files changed

| File | Change |
|---|---|
| `dashboard/page_ticker.py` | New page module |
| `dashboard/app.py` | Add `"VII": "TICKER"` to `PAGES`; add import/route branch |

## Security considerations

- Auth/authz impact: None. The page has no authentication layer, consistent with all other
  dashboard pages. It must be run on a trusted local or VPN-restricted network.
- Secrets or credential handling: None. The page makes no API calls; it reads only from the
  existing PostgreSQL database using the shared engine.
- Network or external service impact: None. No outbound network calls.
- Input handling: The ticker selection is populated from a DB-backed dropdown; users cannot supply
  arbitrary ticker strings. All values rendered to the UI are derived from trusted DB reads. No
  SQL interpolation of user input occurs.
- No meaningful security impact beyond the above.

## Test guidance

- AC1 -> `tests/test_page_ticker.py::test_ticker_list_query`
- AC2 -> `tests/test_page_ticker.py::test_price_chart_no_data_does_not_raise` (manual smoke: chart renders)
- AC3 -> `tests/test_page_ticker.py::test_price_kpi_52w`, `test_ytd_return_positive`, `test_ytd_return_negative`, `test_ytd_return_single_row`
- AC4 -> manual dashboard smoke test (position badge and section conditional on `portfolio_positions` row)
- AC5 -> `tests/test_page_ticker.py::test_score_colour_green`, `test_score_colour_red`, `test_score_colour_neutral`, `test_score_colour_none`; manual smoke: factor bar chart renders
- AC6 -> manual dashboard smoke test (AI scores section absent when no rows)
- AC7 -> manual dashboard smoke test (fundamentals section with annual filter)
- AC8 -> manual dashboard smoke test (analyst/SI captions when empty)
- AC9 -> `tests/test_page_ticker.py::test_insider_query_limit`
- AC10 -> `tests/test_page_ticker.py::test_empty_universe_returns_early`
