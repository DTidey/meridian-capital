# yfinance inplace Warnings

**Spec file:** `docs/specs/55-yfinance-inplace-warnings.md`
**Spec slug:** yfinance-inplace-warnings
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-29

## Problem statement
The 2026-10-28 nightly run, the first on pandas 3.1.0, logged about 540 `Pandas4Warning` lines
(log 250 KB against 45 KB the night before). pandas 3.1 deprecates the `inplace` keyword, and
yfinance 1.7.0 (latest) still calls `sort_index`, `rename` and `set_index` with
`inplace=True` (`yfinance/utils.py`, `yfinance/multi.py`). yfinance also adds a filter that
shows its own deprecation warnings, so they are printed on every price download. Our code has
no `inplace=True` calls. The calls still work; they break only when pandas removes the keyword
(pandas 4).

## Scope
In scope:
- `data/market_data.py`: ignore `Pandas4Warning` messages about the `inplace` keyword raised
  from yfinance modules (the Layer 1 process imports it before any yfinance call).
- `docs/backlog.md`: hold pandas 4.x until a yfinance release drops `inplace`.

Out of scope / non-goals:
- `risk/stress_test.py` (cached, rare download; no warnings in the 10-28 log).
- Pinning pandas below 4 in `requirements.in`.

## Assumptions
- yfinance registers its `default` filter on import, so ours must be added after
  `import yfinance` to take precedence (`warnings.filterwarnings` inserts at the front).

## Proposed behavior / API
### Public interface
- No API change. Importing `data.market_data` adds one warnings filter: action `ignore`,
  category `Pandas4Warning`, message starting "The inplace keyword", module matching
  `yfinance`.

### Inputs / outputs
- Layer 1 output no longer carries these lines; any other warning is unchanged.

## Acceptance criteria
- AC1: After importing `data.market_data`, a `Pandas4Warning` about `inplace` raised from a
  yfinance module is not shown.
- AC2: The same warning raised from our own code is still shown.
- AC3: Other `Pandas4Warning` messages from yfinance are still shown.
- AC4: `docs/backlog.md` records that a pandas 4.x bump waits for a yfinance release without
  `inplace`.

## Security considerations
- No input, auth, secrets, network or dependency change; log output only.
- Security notes for reviewers/testers: the filter is narrow (one category, one message, one
  package), so no other warning is hidden.

## Edge cases
- yfinance imported first by another module: the filter still applies because
  `data.market_data` imports yfinance itself before adding it.
- A future yfinance release without `inplace`: the filter matches nothing and can be removed.

## Test guidance
- AC1 -> `tests/test_market_data.py::TestYfinanceInplaceWarnings::test_yfinance_inplace_hidden`
- AC2 -> `TestYfinanceInplaceWarnings::test_own_code_inplace_shown`
- AC3 -> `TestYfinanceInplaceWarnings::test_other_yfinance_pandas4_shown`
- AC4 -> manual: backlog entry "pandas 4 and yfinance inplace"

## Decision log
- 2026-10-29: Filter rather than pin pandas (owner's choice): pandas 3.1 works, and the
  backlog note stops a 4.x Dependabot bump from being merged early.
