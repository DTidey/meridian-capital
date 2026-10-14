# Test Plan: ticker-page-additions

Path: `docs/test-plans/18-ticker-page-additions.md`

## What changed
- `dashboard/page_ticker.py`: calls the new sections; Institutional score card; helpers moved.
- `dashboard/ticker_common.py`: `score_colour`, `chart_layout` (moved, unchanged).
- `dashboard/ticker_institutional.py`: institutional holdings cards and per-fund table.
- `dashboard/ticker_signals.py`: signal cards, sub-factor table, trend charts, cluster flags.
- `dashboard/ticker_research.py`: AI analysis expanders, SEC filings, earnings release.

## Acceptance criteria coverage
- AC1: `tests/test_ticker_institutional.py::TestNetChangeDisplay` (5 tests); "2 / 2" and net
  change cards asserted in the smoke test.
- AC2: `tests/test_ticker_institutional.py::TestFundHoldings` (10 tests);
  `TestRenderSmoke::test_renders_with_only_universe` (empty-state caption).
- AC3: `tests/test_ticker_signals.py::TestFormatting` (NaN AI composite, direction colours);
  `TestRenderSmoke::test_renders_with_all_sections` (seeded NaN `ai_composite`).
- AC4: `tests/test_ticker_signals.py::TestSubfactors` (3 tests; all 36 columns mapped);
  "Sub-factor scores" expander asserted in the smoke test.
- AC5: `tests/test_ticker_research.py::TestMdEscape`, `TestAnalysisMarkdown`,
  `TestParseResult`; insider expander asserted in the smoke test (seeded unreadable `risk` row).
- AC6: `tests/test_ticker_research.py::TestSafeSecUrl`; Form 4 seeded and excluded in the smoke
  test.
- AC7: `tests/test_ticker_research.py::TestReleaseTitle`; expander label asserted in the smoke
  test.
- AC8: `tests/test_ticker_signals.py::TestTrendFigure` (3 tests); the smoke test asserts 4
  plotly charts (price, factor bars and both trends).
- AC9: `tests/test_ticker_signals.py::TestClusterLines`; cluster caption asserted in the smoke
  test.
- AC10: `tests/test_page_ticker.py::TestRenderSmoke` (2 tests, Streamlit `AppTest`).

## Edge cases
- From spec:
  - Full exit at the latest quarter: `test_exited_fund_not_listed`
  - Unreadable `result_json`: `test_invalid_json_is_none`, seeded in the smoke test
  - Dimension without a score: `test_dimension_without_score`
  - Non-SEC URL: `test_other_hosts_rejected`
- Additional adversarial cases:
  - Look-alike host `www.sec.gov.evil.com` and `javascript:` URLs: `test_lookalike_host_rejected`
  - Markdown link injected in model output renders literally: `test_llm_text_is_escaped`
  - A literal backslash can't cancel an escape: `test_backslash_escaped_first`
  - JSON that is a list or string: `test_non_object_is_none`
  - Markdown in model-supplied JSON keys: `test_model_keys_escaped`
  - Boolean score: `test_bool_score_not_rendered_as_number`
  - Two rows on one date don't make a chart: `test_duplicate_dates_count_once`
  - Holdings after the as-of quarter don't leak in: `test_later_periods_ignored`
  - Null market value sorts last: `test_null_value_sorts_last`
  - NaN sub-factor scores shown blank: `test_missing_and_nan_are_none`

## Notes
- Live render (2026-10-14) with `AppTest` against production PostgreSQL for AAPL, CB and SPG,
  with the institutional section pointed at the packet 17 dry-run data: no exceptions; all ten
  section headers present; AAPL shows 3 / 9 funds, +1.88M net (Citadel +2,082,206, Bridgewater
  -199,115, Berkshire 0), which reconciles with the summary.
- Flaky risks: `AppTest` timeout set to 30 s.

## Commands
```bash
make lint
make security
make test
pytest tests/test_page_ticker.py tests/test_ticker_*.py -q
```
