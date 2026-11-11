Spec: `docs/specs/65-remove-yf-inplace-filter.md`

## Acceptance Criteria

- [x] AC1: `data/market_data.py` no longer defines or calls `ignore_yfinance_inplace_warnings`; importing it adds no yfinance-scoped `ignore` filter for `Pandas4Warning`.
- [x] AC2: The Layer 1 yfinance fetchers (batch and single) run real yfinance and pandas on stubbed HTTP responses, raise no `Pandas4Warning`, `FutureWarning` or `DeprecationWarning`, and return the stubbed prices.
- [x] AC3: No call in the installed yfinance source passes `inplace` (directly or via `**{...}`); installed yfinance >= 1.8.
- [x] AC4: The `yfinance` requirement in `requirements.in` excludes every version below 1.8.0 and allows 1.8.0.
- [x] AC5: `docs/backlog.md` no longer has the "pandas 4 and yfinance inplace" item.

## Summary

Packet 55 hid yfinance 1.7.0's pandas `inplace` deprecation warnings in Layer 1. yfinance 1.8.0
(Dependabot, `20b92c2`; upstream PR #3020) removed those calls, so the filter matched nothing.
It also would have hidden a regression before a pandas 4 bump.

- `data/market_data.py`: filter function, its import-time call and its two imports removed.
- `tests/test_market_data.py`: the packet 55 filter tests removed.
- New `tests/test_yfinance_inplace.py`: real `yf.download` / `Ticker.history` against stubbed
  Yahoo chart JSON (no network, no disk cache) with every warning recorded; an AST scan of the
  installed yfinance source for `inplace` calls; version and requirement-pin checks; a
  fresh-interpreter check that no yfinance warning filter is installed.
- `requirements.in` already has `yfinance>=1.8.0`; not edited.
- Backlog item removed; CHANGELOG under Unreleased; reasoning in `log/changelog-2026-11-11.md`.

Behaviour change: none with yfinance 1.8.0. If a later yfinance brings `inplace` back, its
warnings appear in the Layer 1 log instead of being hidden.

## Spec

- Spec: `docs/specs/65-remove-yf-inplace-filter.md`
- Test plan: `docs/test-plans/65-remove-yf-inplace-filter.md`
- PR draft path: `.ai/pr-description/65-remove-yf-inplace-filter.md`

## Security Review

- [x] Security considerations reviewed in `docs/specs/65-remove-yf-inplace-filter.md`
- No meaningful security impact: removing a warnings filter only makes more output visible.
  The tests make no network calls, parse (never execute) yfinance source, and run one fixed
  `sys.executable -c` snippet without a shell.

## Validation

- Before the code change: 2 of 10 new tests failed (filter still present); 8 passed.
- After: `make lint` pass; `make test` 1777 passed; `make security` pass (Bandit clean,
  pip-audit "No known vulnerabilities found").

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- The stubbed runtime test covers the code paths a 3-bar daily response reaches (download,
  history parse, dividend and split events, adjclose). Other yfinance paths (price repair,
  intraday) are covered only by the static scan.
- The stub depends on yfinance internals (`YfData.get` / `cache_get`, `_TzCacheManager`). A
  yfinance refactor may break the test (loudly, not silently) and need the stub updated.
- pandas 4 is no longer blocked by this item, but may bring other breaking changes; its
  Dependabot PR still needs the normal CI run.

Generated with [Claude Code](https://claude.com/claude-code)
