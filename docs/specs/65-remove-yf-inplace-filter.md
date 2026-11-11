# Remove the yfinance inplace Warning Filter

**Spec file:** `docs/specs/65-remove-yf-inplace-filter.md`
**Spec slug:** remove-yf-inplace-filter
**Status:** Implemented (2026-11-11)
**Owner:** DTidey
**Date:** 2026-11-11

## Problem statement
Packet 55 added `ignore_yfinance_inplace_warnings()` to `data/market_data.py` to hide the
pandas 3.1 `Pandas4Warning`s raised by yfinance 1.7.0, which called `sort_index`, `rename` and
`set_index` with `inplace=True`. Backlog item "pandas 4 and yfinance inplace" said to drop the
filter once a yfinance release stopped using `inplace`.

yfinance 1.8.0 (Dependabot, commit `20b92c2`, upstream PR #3020) removed those calls. Checked
2026-11-11 in `.venv` (yfinance 1.8.0, pandas 3.1.1): no call in the installed yfinance source
passes `inplace`, and an earlier live download raised no `Pandas4Warning`. The filter now
matches nothing. It is dead code, and it would hide a regression if a later yfinance release
brought `inplace` back. That warning should be visible before a pandas 4 bump.

## Scope
In scope:
- `data/market_data.py`: delete `ignore_yfinance_inplace_warnings`, its module-level call, and
  the imports only it used (`warnings`, `Pandas4Warning`).
- `tests/test_market_data.py`: delete `TestYfinanceInplaceWarnings` and its helpers and
  imports, which tested only the filter.
- New `tests/test_yfinance_inplace.py`: shows the root cause is fixed (AC2-AC4).
- `docs/backlog.md`: remove "pandas 4 and yfinance inplace" (picked up).

Out of scope / non-goals:
- Bumping pandas to 4.x (none released yet; Dependabot handles it).
- yfinance's own import-time filter (`default` for `DeprecationWarning` from `^yfinance`).
- Other warning filters (`data/edgar_docs.py` `XMLParsedAsHTMLWarning`).
- `requirements.in`: it already has `yfinance>=1.8.0` (Dependabot, `20b92c2`); no edit.

## Assumptions
- The filter matched only one category (`Pandas4Warning`), one message prefix ("The inplace
  keyword") and one package (`yfinance(\.|$)`), so removing it shows no other warning.
- The yfinance chart JSON shape (`chart.result[0]` with `meta`, `timestamp`, `events`,
  `indicators.quote`, `indicators.adjclose`) is stable enough to stub in tests.

## Proposed behavior / API
### Public interface
- Removed: `data.market_data.ignore_yfinance_inplace_warnings`. No other caller (repo-wide
  grep: only `tests/test_market_data.py`).
- No CLI or config change.

### Inputs / outputs
- Layer 1 output is unchanged with yfinance 1.8.0, which raises none of these warnings. If a
  later yfinance raises one again, it now appears in the Layer 1 log instead of being hidden.

### Error handling
- None added or changed.

## Acceptance criteria
- AC1: `data/market_data.py` no longer defines or calls `ignore_yfinance_inplace_warnings`.
  Importing it in a fresh interpreter adds no `ignore` warnings filter that matches
  `Pandas4Warning` and is scoped to a yfinance module.
- AC2: The Layer 1 yfinance fetchers (`_fetch_yfinance_batch` for several tickers,
  `_fetch_yfinance_single`) run real yfinance and pandas code against stubbed Yahoo HTTP
  responses (no network). Every warning is recorded with no filter applied: no
  `Pandas4Warning`, `FutureWarning` or `DeprecationWarning` is raised, and the stubbed prices
  are returned.
- AC3: No call in the installed yfinance package source passes the `inplace` keyword, directly
  or through a `**{...}` dict literal. The installed yfinance version is >= 1.8.
- AC4: The `yfinance` requirement in `requirements.in` rules out every version below 1.8.0 and
  allows 1.8.0.
- AC5: `docs/backlog.md` no longer contains the "pandas 4 and yfinance inplace" item.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none. The tests parse the installed yfinance source with
  `ast.parse` and never execute it. One test runs `sys.executable -c` with a fixed snippet and
  no shell.
- Secrets or credential handling: none; the tests make no network calls and need no keys.
- Data exposure or privacy impact: none. Only which warnings are logged changes.
- File system access impact: tests read the installed yfinance package; the yfinance timezone
  cache is replaced by its in-memory dummy so nothing is written to the user cache.
- Network or external service impact: none. Production downloads are unchanged.
- Dependency or supply-chain impact: none. The yfinance 1.8.0 pin already landed; AC4 only
  checks it.
- Security notes for reviewers/testers: no meaningful security impact. Removing a warnings
  filter can only make more output visible.

## Edge cases
- yfinance downloads in worker threads (`threads=True`) and swallows per-ticker exceptions.
  The test records warnings process-wide (Python 3.12 filters are global), and it also checks
  the returned prices, so a swallowed failure cannot pass silently.
- yfinance's own `default` filter for its `DeprecationWarning`s stays in place. A future
  `inplace` regression would print once per call site in the nightly log, which is the wanted
  early signal before pandas 4.
- A yfinance downgrade below 1.8 is blocked by AC3 (installed version) and AC4 (pin).
- The source scan must not pass vacuously: it requires more than 10 files and its own scanner
  tests show it flags literal, variable and `**{...}` forms. Comments, strings and plain
  variables named `inplace` are not flagged.
- pandas 4: this backlog item no longer blocks the bump. AC2 shows the yfinance path is clean
  under pandas 3.1, but pandas 4 may bring other breaking changes, so its Dependabot PR still
  needs the normal CI run.

## Test guidance
- AC1 -> `tests/test_yfinance_inplace.py::test_filter_function_removed`,
  `test_import_installs_no_yfinance_pandas4_filter`
- AC2 -> `test_batch_download_raises_no_deprecations`,
  `test_single_download_raises_no_deprecations`,
  `test_harness_catches_pandas4_warning_inside_yfinance` (adversarial: harness sees a warning
  raised inside a yfinance worker thread)
- AC3 -> `test_installed_yfinance_has_no_inplace_calls`, `test_installed_yfinance_at_least_1_8`,
  `test_scanner_flags_literal_inplace`, `test_scanner_flags_indirect_inplace`,
  `test_scanner_ignores_non_call_mentions` (adversarial scanner checks)
- AC4 -> `test_requirements_pin_rules_out_pre_1_8`
- AC5 -> manual: `grep -n "yfinance inplace" docs/backlog.md` returns nothing

## Decision log
- 2026-11-11: Stub at the yfinance HTTP layer (`YfData.get` / `cache_get`), not at our
  `_fetch_yfinance_*` functions. Stubbing our functions would skip the yfinance and pandas code
  where `inplace` was used, so the test would prove nothing.
- 2026-11-11: Keep a static source scan as well as the runtime test. The runtime test covers
  only the code paths the stub reaches; the scan covers the whole package.
- 2026-11-11: Remove the packet 55 tests rather than adapt them. They tested only the filter's
  matching rules, which no longer exist.
