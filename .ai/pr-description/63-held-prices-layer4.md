Spec: `docs/specs/63-held-prices-layer4.md`

## Acceptance Criteria

- [x] AC1: A held ticker outside the candidate list gets its latest stored close as trade-list price.
- [x] AC2: Its trade gets a non-zero cost estimate from its own high/low/volume history.
- [x] AC3: It counts in `trade_value`, the turnover summary and the build-up gap's current gross.
- [x] AC4: Optimiser, betas, schedule warnings and factor scores still get candidates only.
- [x] AC5: With no held non-candidates, tickers loaded and the trade list are unchanged.
- [x] AC6: One WARNING names every trade-list ticker without a usable price; none when all priced.

## Summary

Layer 4 loaded prices for tonight's LONG/SHORT candidates only, so a held name that left the
candidate list got price 0.00 and Est. Cost $0.00 (2026-11-09: VICI SELL 107, YUM COVER 18,
CRL COVER 7; 2026-11-06: AVY). Those trades counted as $0 in the turnover budget and were left
out of the current gross in the build-up gap. Orders were unaffected (the executor prices
separately).

- `run_portfolio.py`: `_load_prices` gets candidates plus held tickers (`_price_tickers`);
  `load_positions` moved up. The optimiser universe, betas, schedule warnings and factor
  exposures stay candidates-only.
- `portfolio/rebalance.py`: `generate_trades` logs a WARNING listing any trade-list ticker still
  valued at $0 for want of a price (kept the 0 fallback so closures are never blocked).
- Backlog entry removed; CHANGELOG under Unreleased.

## Spec

- Spec: `docs/specs/63-held-prices-layer4.md`
- Test plan: `docs/test-plans/63-held-prices-layer4.md`
- PR draft path: `.ai/pr-description/63-held-prices-layer4.md`

## Security Review

- [x] Security considerations reviewed in `docs/specs/63-held-prices-layer4.md`
- No auth, secrets, network or order-size change; held tickers come from the DB and are bound
  parameters.

## Validation

- 6 of 11 new tests failed before the fix; all pass after.
- `make lint`, `make test`: pass.

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- The turnover summary and build-up allowance now include held non-candidates, so on nights
  with such closures the rotation budget numbers shift slightly versus before (correctly).
- A held name with no stored close is still priced at 0 (now warned); see backlog "A held name
  with no stored close cannot be closed by the pipeline".

Generated with [Claude Code](https://claude.com/claude-code)
