## Summary

Fixes the fundamentals inputs behind the value and quality factors (data audit C2, H6, H7, M12,
M14).

- **Market cap** comes from Yahoo `.info` `marketCap`, stored nightly in a new `market_caps`
  table from the `.info` response already fetched for short interest. It counts every share
  class, so BRK-B ($1.15T, not 1.43M A-equivalent shares) and CME (no 1000x glitch) are right.
  Shares are market cap / our close, with a guard that keeps the previous snapshot when the
  latest moves more than 1.5x. Without a snapshot, balance-sheet shares are used.
- **Value factor:** trailing-twelve-month revenue, operating income, FCF, dividends and buybacks
  over four contiguous quarters. Balance-sheet items all come from one quarter. An omitted
  dividend or buyback line counts as zero when the cash-flow statement exists.
- **EBIT is operating income** in the value factor, Altman Z and the Ticker page (GOOGL's EBIT
  row includes $99B of non-operating gains).
- **ROE** is net income / equity (it was stored as the true value minus one). ROE and D/E are
  empty without positive equity; negative equity with debt ranks as most leveraged.
  `run_data.py --recompute-ratios` rewrites the stored values.
- **Audit correction:** MNST and APH share counts are not doubled; both split 2:1 after the SEC
  cover pages the audit compared against.

## Spec

- Spec: `docs/specs/42-fundamentals-value-inputs.md`
- Test plan: `docs/test-plans/42-fundamentals-value-inputs.md`
- PR draft path: `.ai/pr-description/42-fundamentals-value-inputs.md`

## Acceptance Criteria

- [x] AC1: Market caps step stores `marketCap` per ticker per day, skips today's, adds no request, drops bad values.
- [x] AC2: Effective shares: latest within 1.5x, previous otherwise, single as is, else balance sheet.
- [x] AC3: Value and Altman Z use that market cap (BRK-B from $1.15T).
- [x] AC4: Value flows are TTM over four contiguous quarters, NaN otherwise.
- [x] AC5: Omitted dividend or buyback line is zero with a cash-flow statement; no NaN from `x or 0.0`.
- [x] AC6: EV/EBIT, Altman Z and the dashboard use operating income.
- [x] AC7: Balance-sheet inputs come from one quarter.
- [x] AC8: ROE and D/E formulas; quality ROE from raw figures; negative equity with debt ranks lowest.
- [x] AC9: `--recompute-ratios` rewrites roe and debt_to_equity, idempotent, rejects `--tickers`.
- [x] AC10: Dashboard valuation uses the shared helpers.
- [x] AC11: lint, tests and security pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/42-fundamentals-value-inputs.md` (no new
  requests or dependencies; `--recompute-ratios` writes two derived columns and runs only with
  the owner's go-ahead)

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 1349 passed
- Read-only dry run on production data for 2026-10-21, with live Yahoo `marketCap` for 18
  tickers, compared with the stored scores:

| Ticker | Value score | Notes |
|---|---|---|
| BRK-B | 93.4 -> 42.1 | book/price rank 100 -> 68 |
| CME | 100 -> 11.8 | book/price rank 99 -> 26 |
| GOOGL | 18.2 -> 9.1 | EV/EBIT rank 81 -> 20 (operating income) |
| CMG | 42.6 -> 40.4 | shareholder yield 50 (neutral) -> 78.7 |
| LOW, MAR, YUM | - | D/E rank 96/89/81 -> 13 (negative equity) |

- Universe: 43 of 503 value scores move more than 20 points; neutral shareholder-yield scores
  fall from 150 to 10.

Reviewer pass (fresh subagent): no blockers. Applied: Piotroski's D/E signal and `qual_de_inv`
read raw figures (omitted debt line = no debt), `--recompute-ratios` moved to a helper and
rejects `--rebuild-insider`, dashboard uses scoring's 30-day snapshot window. Its point that
quality reads a newest quarter without a balance sheet does not hold: quality already keeps
only quarters with `total_assets`, the rule value now uses.

## Risks and follow-ups

- Deployment needs the owner's go-ahead: `run_data.py --recompute-ratios`, then the first
  nightly `market_caps` snapshot, then a rescore. Until the first snapshot, market cap falls
  back to balance-sheet shares (today's behaviour).
- BRK-B's EV/EBIT and shareholder yield stay neutral: its 2025-09-30 quarter is blank, so it has
  no contiguous trailing year (audit M10).
- Balance-sheet share history is unchanged and still feeds Piotroski's dilution signal; added
  to `docs/backlog.md`.
