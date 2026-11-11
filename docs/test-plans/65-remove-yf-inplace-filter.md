# Test Plan: remove-yf-inplace-filter

Path: `docs/test-plans/65-remove-yf-inplace-filter.md`
Spec: `docs/specs/65-remove-yf-inplace-filter.md`
Status: Implemented (2026-11-11).

## What changed
- `data/market_data.py`: `ignore_yfinance_inplace_warnings()` and its import-time call removed,
  with the `warnings` and `Pandas4Warning` imports only it used.
- `tests/test_market_data.py`: `TestYfinanceInplaceWarnings` (packet 55) and its helpers
  removed.
- New `tests/test_yfinance_inplace.py`.
- `docs/backlog.md`: "pandas 4 and yfinance inplace" removed.

## Acceptance criteria coverage
- AC1: `tests/test_yfinance_inplace.py::test_filter_function_removed`,
  `test_import_installs_no_yfinance_pandas4_filter` (fresh interpreter, so pytest's own
  per-test warning filters cannot mask a module-level filter)
- AC2: `test_batch_download_raises_no_deprecations`,
  `test_single_download_raises_no_deprecations`,
  `test_harness_catches_pandas4_warning_inside_yfinance`
- AC3: `test_installed_yfinance_has_no_inplace_calls`, `test_installed_yfinance_at_least_1_8`,
  `test_scanner_flags_literal_inplace`, `test_scanner_flags_indirect_inplace`,
  `test_scanner_ignores_non_call_mentions`
- AC4: `test_requirements_pin_rules_out_pre_1_8`
- AC5: manual, `grep -n "yfinance inplace" docs/backlog.md` returns nothing

## How the runtime test works (AC2)
- `YfData.get` and `YfData.cache_get` are monkeypatched to return canned Yahoo chart JSON (3
  daily bars, a dividend and a split event, adjclose). Real `yf.download` (threaded, two
  tickers) and `Ticker.history` parse it with real pandas.
- The yfinance timezone cache is swapped for its in-memory `_TzCacheDummy`, so nothing is
  written under `~/.cache/py-yfinance`.
- Warnings are recorded under `simplefilter("always")`. Any `Pandas4Warning`,
  `FutureWarning` or `DeprecationWarning` fails the test with its message. The returned prices
  are also checked, because yfinance swallows per-ticker exceptions.

## Adversarial cases (beyond the spec edge cases)
1. `test_harness_catches_pandas4_warning_inside_yfinance`: a `Pandas4Warning` raised inside the
   stubbed response, on a yfinance download worker thread, must be reported. This shows the
   AC2 green result is not a harness that cannot see warnings.
2. `test_scanner_flags_indirect_inplace`: `inplace=flag` and `**{'inplace': True}` are flagged,
   not only the literal `inplace=True` a text grep would find.
3. `test_scanner_ignores_non_call_mentions`: comments, strings, a variable named `inplace` and
   `f(x=inplace)` are not flagged, so the scan has no false positives.
4. `test_installed_yfinance_has_no_inplace_calls` requires more than 10 scanned files, so an
   empty or wrong package path cannot pass vacuously.
5. `test_requirements_pin_rules_out_pre_1_8` checks 1.7.0, 1.7.9, 1.0.0 and 0.2.66 are
   excluded and 1.8.0 is allowed.

## Commands
```bash
. .venv/bin/activate && pytest tests/test_yfinance_inplace.py tests/test_market_data.py -q
make lint
make test
make security
```

## Results (2026-11-11)
- Before the code change: 2 failed (`test_filter_function_removed`,
  `test_import_installs_no_yfinance_pandas4_filter`), 8 passed.
- After: see the PR draft validation section.
