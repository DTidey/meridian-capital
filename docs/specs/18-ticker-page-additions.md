# Ticker Page Additions

**Spec file:** `docs/specs/18-ticker-page-additions.md`
**Spec slug:** ticker-page-additions
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The Ticker page (page VII, packet 08) doesn't show institutional holdings. The user asked for
  them, and also asked what else the database holds per ticker that the page doesn't show.
- From that list, the user chose to add: the trading signal and direction, the AI analysis
  reasoning, SEC filings and earnings releases, and history and sub-factors.
- Depends on packet 17. Institutional data is only meaningful after its ingestion fixes.

## Scope
In scope (all read-only, for the selected ticker):
- Institutional holdings: summary cards and a per-fund table.
- Signal: latest `combined_scores` row and the `factor_scores` regime.
- An Institutional card among the factor score cards, plus a sub-factor table.
- AI analysis: the latest `analysis_results` write-up per analyzer.
- Recent 10-K / 10-Q / 8-K filings with EDGAR links, and the latest earnings release.
- History charts for analyst estimates and short interest; insider cluster flags.

Out of scope / non-goals:
- Position extras (entry date, beta), `portfolio_history`, approvals, trades, orders, risk log.
- Fundamentals beyond the current eight fields, and quarterly fundamentals.
- Form 4 filings in the filings list (already covered by insider transactions).
- Any writes, or changes to other pages.

## Assumptions
- `earnings_transcripts.content` holds the 8-K EX-99.1 earnings press release text (checked for
  AAPL on 2026-10-14), so it is labelled "earnings release".
- `combined_scores.ai_composite` can be NaN (seen for AAPL 2026-10-12).
- `analysis_results.result_json` shapes (checked 2026-10-14): `earnings` has six
  `{score, reasoning}` dimensions plus `bull_case`, `bear_case`, `key_quotes` and
  `one_line_summary`; `filing`, `insider` and `risk` mix scalars, string lists and (for `risk`)
  a list of `{risk, severity, category}` dicts. All have `one_line_summary`.
- `sec_filings.filing_url` is the EDGAR folder URL for the filing.

## Proposed behavior / API
### Public interface
- New `dashboard/ticker_common.py`: `score_colour` and `chart_layout`, moved from
  `page_ticker.py`. `page_ticker` keeps its `_score_colour` / `_chart_layout` names as imports.
- New `dashboard/ticker_institutional.py`: `render_institutional(engine, ticker, cfg)` and pure
  `fund_holdings(rows, fund_periods, as_of) -> list[dict]` and
  `net_change_display(value) -> (text, colour)`.
- New `dashboard/ticker_signals.py`: `render_signal(engine, ticker)`, `render_subfactors(fs)`,
  `render_estimate_trend(engine, ticker)`, `render_short_interest_trend(engine, ticker)`,
  `render_cluster_flags(engine, ticker)`, and pure `subfactor_rows(fs)`, `fmt_score(value)`,
  `direction_colour(direction)` and `SUBFACTORS`.
- New `dashboard/ticker_research.py`: `render_ai_analysis(engine, ticker)` and
  `render_filings(engine, ticker)` (which also renders the earnings release), and pure
  `md_escape`, `parse_result`, `analysis_markdown`, `safe_sec_url` and `release_title`.
- `dashboard/page_ticker.py`: calls the new sections; adds the Institutional card.
- CLI entry points: none.

### Inputs / outputs
- Inputs: existing tables only; `cfg["institutional"]["tracked_funds"]` for the fund count.
- Outputs: Streamlit elements only. No writes.
- Error handling:
  - Each new section shows a caption when it has no data, and never raises.
  - `result_json` that fails to parse shows a caption for that analyzer only.

## Acceptance criteria
- AC1: An "INSTITUTIONAL HOLDINGS" section uses the ticker's latest `institutional_summary`
  row. It shows the quarter, Funds Holding as "n / N" (N = tracked funds in config), Net Share
  Change (signed; in shares below 10,000, else millions; green > 0, red < 0, "—" when null)
  and New Positions.
- AC2: Below the cards, a table lists each fund whose latest filing for any quarter <= that
  period holds the ticker: Fund, Quarter, Filed, Shares, Value ($M), and Change vs the fund's
  previous filing. Change is signed shares ("0" when unchanged), "New" if the previous filing
  didn't hold the ticker, and "—" if the fund has no earlier filing. Rows are sorted by value, largest first. With no summary row
  the section shows "No institutional holdings among tracked funds." and no table.
- AC3: A "SIGNAL" section, placed after the price section, shows from the latest
  `combined_scores` row: Combined, Quant, AI (NaN or null shown as "—"), and Direction (LONG
  green, SHORT red, otherwise neutral). It also shows Regime from the latest `factor_scores`
  row, and the as-of date. Without a `combined_scores` row it shows a caption.
- AC4: The factor score cards include Institutional (`institutional_score`), nine cards in all.
  A collapsed "Sub-factor scores" expander shows a table (Factor, Sub-factor, Score) of the 36
  sub-factor columns in `factor_scores`. Null scores are shown as blank.
- AC5: Under "AI SCORES", one collapsed expander per analyzer (earnings, filing, insider, risk)
  shows that analyzer's most recent `analysis_results` row for the ticker by `created_at`. The
  label is the analyzer name and the escaped `one_line_summary`; the body is `analysis_markdown(result)`
  plus the model and date. Analyzers with no row are omitted. Text from the LLM is escaped
  with `md_escape`, so `$`, `*`, `_`, `` ` ``, `[`, `]`, `#`, `<`, `>` and `|` render
  literally.
- AC6: An "SEC FILINGS" section lists up to 15 of the most recent `sec_filings` rows of form
  10-K, 10-Q or 8-K: Filed, Form, and a link to `filing_url`. Only `https://www.sec.gov/` URLs
  are shown as links; anything else is shown blank.
- AC7: Under "SEC FILINGS", a collapsed expander titled "Earnings release - <quarter> <year>
  (<earnings_date>)" shows the latest `earnings_transcripts` row's content as plain text in a
  read-only text area. It is omitted when no row exists.
- AC8: Under "ANALYST ESTIMATES" and "SHORT INTEREST", line charts show the full stored history
  of price target and forward EPS, and of % float short and days to cover. Each chart is shown
  only when there are at least two dated rows.
- AC9: Under "RECENT INSIDER TRANSACTIONS", any `insider_cluster_flags` rows for the ticker are
  listed (window start to end, insider count, total shares), most recent first. Nothing is
  shown when there are none.
- AC10: With an empty database apart from one `sp500_universe` row, the page renders without an
  exception (Streamlit `AppTest`). With every new table populated for that ticker, it also
  renders without an exception and shows the new section headers.

## Security considerations
- Auth/authz impact: None; unchanged (dashboard on a trusted network).
- Input handling or injection risk: LLM output and filing text come from outside the system.
  LLM text is markdown-escaped before rendering (AC5). Release text goes into a read-only text
  area (AC7). No new values are put in `unsafe_allow_html` HTML; fund names and directions go
  through Streamlit's escaped elements; direction and regime are HTML-escaped before
  `metric_card`. Links are limited to the SEC host (AC6). Residual: `md_escape` doesn't stop
  bare `https://` URLs in model text being auto-linked.
- Secrets or credential handling: None.
- Data exposure or privacy impact: None new; the data is already in the database and largely
  public (SEC).
- File system access impact: None.
- Network or external service impact: None from the server. Filing links open in the user's
  browser.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: Check that no new `unsafe_allow_html=True` call
  interpolates database text.

## Edge cases
- Ticker with summary rows but held by no fund at the latest period (full exit): cards show
  0 / N, and the table shows "No tracked fund holds this ticker as of <quarter>."
- `result_json` not valid JSON, or not an object: caption for that analyzer.
- An earnings `{score, reasoning}` dimension missing `score`: shown without a score.
- A filing URL that is null or not on sec.gov: no link.
- Analyst or short-interest history with one row: no chart, as in AC8.

## Test guidance
- AC1, AC2 -> `tests/test_ticker_institutional.py`
- AC3, AC4, AC8, AC9 -> `tests/test_ticker_signals.py` (`TestTrendFigure`, `TestClusterLines`)
  and the smoke-test assertions
- AC5, AC6, AC7 -> `tests/test_ticker_research.py`
- AC10 -> `tests/test_page_ticker.py::TestRenderSmoke`

## Decision log
- 2026-10-14: New sections live in three modules by domain (institutional, signals, research)
  rather than in `page_ticker.py`, which is already 478 lines with a 380-line `render`.
  The shared helpers move to `ticker_common.py` to avoid a circular import.
- 2026-10-14: Form 4 excluded from the filings list. It is 83% of `sec_filings` and would
  crowd out the 10-K/10-Q/8-K filings; insider trades already have their own section.
- 2026-10-14: Fund table uses carry-forward (the same rule as packet 17 AC6), so funds that
  haven't filed the latest quarter yet still appear, with their quarter shown.
- 2026-10-14: Rendering against production data needs packet 17's `filed_date` column. The
  dashboard's `app.py` runs `initialise_schema` at startup, which adds it, so no extra guard.
  Until the next 13F run, the section shows the pre-packet-17 rows.
- 2026-10-14: Found while checking against production data, left for a follow-up: ticker
  matching by issuer name misses some quarters (Berkshire's Q1 2026 filing names Chubb
  "CHUBB LTD SWITZ"). About 3% of fund holdings disappear for one quarter, which shows as false
  "New" rows here and as false new positions in the factor. Setting `OPENFIGI_API_KEY` enables
  the existing CUSIP fallback.
- 2026-10-14 (Reviewer pass): two blockers fixed. Direction and regime are now HTML-escaped
  before `metric_card`, and AC8/AC9 have tests (`trend_figure` and `cluster_lines` were split
  out to make them testable). Also fixed: model JSON keys escaped, `width="stretch"` instead of
  the deprecated `use_container_width` in new code, smoke-test assertions for AC1/AC2/AC3/AC5/
  AC6/AC8/AC9, small net changes in shares, conditional expander labels, bool scores. Not taken:
  neutralising line-start markdown and bare-URL autolinks in model text (recorded as residual).
