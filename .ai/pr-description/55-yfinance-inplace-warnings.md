## Summary

The 2026-10-28 nightly, the first on pandas 3.1.0, logged about 540 `Pandas4Warning` lines from
yfinance 1.7.0 (latest), which still calls pandas with `inplace=True`. Our code has no such
calls. The calls still work; pandas 4 is expected to remove the keyword.

- `data/market_data.py` ignores `Pandas4Warning` messages about `inplace` raised from yfinance
  modules, set after `import yfinance` (yfinance adds its own filter that shows them).
- Other warnings, including the same message from our code, still show.
- `docs/backlog.md`: do not merge a pandas 4.x bump until yfinance drops `inplace`.

## Spec

- Spec: `docs/specs/55-yfinance-inplace-warnings.md`
- Test plan: `docs/test-plans/55-yfinance-inplace-warnings.md`
- PR draft path: `.ai/pr-description/55-yfinance-inplace-warnings.md`

## Acceptance Criteria

- [x] AC1: A yfinance `inplace` `Pandas4Warning` is hidden after importing `data.market_data`.
- [x] AC2: The same warning from our own code is still shown.
- [x] AC3: Other `Pandas4Warning` messages from yfinance are still shown.
- [x] AC4: `docs/backlog.md` holds pandas 4.x until yfinance drops `inplace`.

## Security Review

- [x] Security considerations reviewed in `docs/specs/55-yfinance-inplace-warnings.md` (log
  output only; narrow filter)

## Validation

- `TestYfinanceInplaceWarnings`: fails on `main` (no filter), passes here
- Live `yf.download`: 13 warning lines with only `import yfinance`, 0 after
  `import data.market_data`
- `make lint`: pass
- `make test`: 1545 passed
- `make security`: pass ("No known vulnerabilities found")

## Risks and follow-ups

- Tonight's log should shrink back to about 50 KB.
- Remove the filter when a yfinance release drops `inplace`.
