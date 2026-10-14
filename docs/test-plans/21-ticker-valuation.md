# Test Plan: ticker-valuation

Path: `docs/test-plans/21-ticker-valuation.md`

## What changed
- `dashboard/ticker_valuation.py` (new): valuation metrics and the VALUATION section.
- `dashboard/page_ticker.py`: calls it after the price section.

## Acceptance criteria coverage
- AC1: `tests/test_ticker_valuation.py::TestValuation::test_aapl`, `test_missing_debt_or_cash_no_ev`
- AC2: `test_fewer_than_four_quarters_no_ttm`, `test_gap_in_quarters_no_ttm`,
  `test_single_missing_quarter_no_ttm`, `test_52_53_week_quarter_ends_still_ttm`,
  `test_missing_value_in_ttm`
- AC3: `test_aapl`, `test_negative_denominators_are_nm`, `test_negative_ev_multiple_is_nm`
- AC4: `test_aapl`, `test_negative_fcf_yield_kept`, `test_bank_has_no_ebit_or_fcf`,
  `test_dividend_signs_do_not_cancel`
- AC5: `TestFormatting` (negatives, below $1M, unit rounding); market cap card asserted in
  `tests/test_page_ticker.py::TestRenderSmoke::test_renders_with_all_sections`
- AC6: `test_no_price_or_shares`; `TestRenderSmoke::test_renders_with_only_universe`

## Edge cases
- From spec: banks, gaps, negative equity, missing debt or cash (tests above).
- Additional adversarial cases:
  - Zero shares: `test_zero_shares`
  - NaN fundamentals: `test_nan_values_treated_as_missing`
  - Quarters spanning more than 13 months: `test_gap_in_quarters_no_ttm`

## Notes
- Live check (2026-10-14, production data): AAPL market cap $5.01T, EV $5.06T, P/E 38.9x,
  EV/EBIT 32.7x, P/B 46.6x, FCF yield 2.7%, dividend yield 0.3%. JPM: EV/EBIT and FCF yield
  "—" (no EBIT or FCF for banks). DELL (negative equity): P/B "n/m".

## Commands
```bash
make lint
make test
pytest tests/test_ticker_valuation.py tests/test_page_ticker.py -q
```
