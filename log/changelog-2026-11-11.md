# Changelog 2026-11-11

## Packet 65: remove the yfinance inplace warning filter

- **Remove the filter rather than keep it as a harmless no-op.** With yfinance 1.8.0 it
  matches nothing. Keeping it would hide an `inplace` regression in a later yfinance, which is
  exactly the warning we want to see before merging a pandas 4 bump. The filter was narrow (one
  category, one message prefix, one package), so removing it shows nothing else.
- **Stub at the yfinance HTTP layer (`YfData.get` / `cache_get`), not at
  `_fetch_yfinance_*`.** The existing `yahoo` fixture in `tests/test_market_data.py` replaces
  our fetchers, which skips the yfinance and pandas code where `inplace` lived. Stubbing below
  yfinance keeps the test offline while running its real parsing. The timezone cache is swapped
  for yfinance's own `_TzCacheDummy` so the test writes nothing to `~/.cache/py-yfinance`.
- **Record warnings, not escalate them to errors.** yfinance downloads in worker threads and
  swallows per-ticker exceptions, so an escalated warning could become an empty result instead
  of a failure. Recording under `simplefilter("always")` reports the message, and the test also
  checks the returned prices. An adversarial test shows a warning raised on a worker thread is
  caught.
- **Check the filter in a subprocess.** pytest wraps collection and each test in
  `catch_warnings`, so a module-level filter added at import is not visible in
  `warnings.filters` during a test. A fresh interpreter is the reliable check.
- **AST scan as well as the runtime test.** The runtime test covers only the paths a simple
  daily response reaches; the scan covers the whole installed package and catches `inplace=var`
  and `**{"inplace": ...}`, which a grep for `inplace=True` would miss.
- **`requirements.in` not edited.** Dependabot's `20b92c2` already set `yfinance>=1.8.0`; AC4
  tests it so a downgrade cannot slip back in.
