## Summary

Adds a VALUATION section to the Ticker page, after the price chart: market cap, enterprise
value, P/E (TTM), EV/EBIT (TTM), price/book, FCF yield and dividend yield.

- Market cap = latest close x latest quarterly shares outstanding; EV adds debt and subtracts
  cash from the latest quarter.
- Trailing figures sum the last four quarters, only when they form a trailing year with no
  missing values.
- EV/EBIT rather than EV/EBITDA: depreciation and amortisation isn't stored. (The value
  factor's "ev_ebitda" sub-factor also uses EBIT.)
- Read-only; no new tables.

## Spec

- Spec: `docs/specs/21-ticker-valuation.md`
- Test plan: `docs/test-plans/21-ticker-valuation.md`
- PR draft path: `.ai/pr-description/21-ticker-valuation.md`

## Acceptance Criteria

- [x] AC1: Market cap and EV from the latest close and quarter.
- [x] AC2: TTM only over four quarters spanning at most 300 days (no missing quarter), all present.
- [x] AC3: P/E, EV/EBIT, P/B; non-positive denominators shown as "n/m".
- [x] AC4: FCF yield and dividend yield.
- [x] AC5: Formatting and as-of caption.
- [x] AC6: "No valuation data." without price or shares; page still renders.

## Security Review

- [x] Security considerations reviewed in `docs/specs/21-ticker-valuation.md` (no meaningful
  impact: read-only, numbers only)

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 800 passed
- Live: AAPL $5.01T market cap, P/E 38.9x; JPM EV/EBIT and FCF yield "—"; DELL P/B "n/m"

Reviewer pass (fresh subagent): no blockers. The TTM span rule (13 months let one missing
quarter through) is tightened to 300 days; formatting and sign suggestions also taken.

## Risks

- Annual fundamentals contain 12 near-duplicate pairs (two sources, different debt and shares);
  not used here, noted for a follow-up.
- EV for banks is shown but isn't a meaningful measure for them.
