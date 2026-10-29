# Test Plan: yfinance-inplace-warnings

Path: `docs/test-plans/55-yfinance-inplace-warnings.md`

## What changed
- `data/market_data.py`: `ignore_yfinance_inplace_warnings()`, called on import after
  `import yfinance`.
- `docs/backlog.md`: "pandas 4 and yfinance inplace".
- `tests/test_market_data.py`: `TestYfinanceInplaceWarnings`.

## Acceptance criteria coverage
- AC1: `TestYfinanceInplaceWarnings::test_yfinance_inplace_hidden`
- AC2: `TestYfinanceInplaceWarnings::test_own_code_inplace_shown`
- AC3: `TestYfinanceInplaceWarnings::test_other_yfinance_pandas4_shown`
- AC4: manual, backlog entry present

## Edge cases
- From spec: yfinance imported before `data.market_data`.
- Additional adversarial cases:
  - Module whose name merely contains "yfinance" (`notyfinance.utils`) is not filtered
    (`test_module_named_like_yfinance_shown`)
  - Both yfinance modules seen in the log (`yfinance.utils`, `yfinance.multi`) are filtered
    (`test_yfinance_inplace_hidden`)
  - Tests run under an `always` filter, as pytest sets, so the filter is checked against the
    strictest setting (`_shown`)
  - Live check: a real `yf.download` printed 13 `Pandas4Warning` lines with only
    `import yfinance`, 0 after `import data.market_data`

## Commands
```bash
make lint
make test
make security
```
