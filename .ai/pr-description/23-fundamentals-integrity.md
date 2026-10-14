## Summary

Fixes three defects in yfinance fundamentals ingestion, found while looking at duplicate annual
rows.

- **Share counts included treasury stock.** "Share Issued" was read first. Now "Ordinary Shares
  Number" (outstanding) is used, then issued minus treasury. JPM was stored at 4.11B shares
  (2.66B outstanding), so its market cap showed $1.36T instead of about $0.88T. 41 tickers'
  latest counts change, mostly banks and heavy buyback companies.
- **Statements were paired by position.** When Yahoo omits a period from one statement, every
  quarter shifted: BAC and GS stored each quarter with the previous quarter's balance sheet and
  cash flow. Statements are now matched by period.
- **Duplicate periods.** Yahoo's date convention changed (fiscal end vs month-end), and the date
  is part of the key, so re-fetches added rows (12 annual pairs). Dates are stored as month-ends
  and existing duplicates are merged, keeping the newest.
- The Ticker page valuation now takes balance-sheet figures from the latest quarter that has
  them, since the newest quarter can now be income-only.
- The quality factor likewise scores from quarters with a balance sheet, so the lag doesn't
  penalise those companies.
- New `run_data.py --refresh-fundamentals` to re-fetch every ticker.

## Spec

- Spec: `docs/specs/23-fundamentals-integrity.md`
- Test plan: `docs/test-plans/23-fundamentals-integrity.md`
- PR draft path: `.ai/pr-description/23-fundamentals-integrity.md`

## Acceptance Criteria

- [x] AC1: Outstanding shares, not issued.
- [x] AC2: Statements aligned by period; a missing period gives empty values.
- [x] AC3: `period_end` stored as the nearest month-end.
- [x] AC4: Near-duplicate rows merged, newest kept, idempotent.
- [x] AC5: `--refresh-fundamentals` re-fetches every ticker.
- [x] AC6: Valuation uses the latest quarter with a balance sheet.
- [x] AC7: Quality factor scores from quarters with a balance sheet.

## Security Review

- [x] Security considerations reviewed in `docs/specs/23-fundamentals-integrity.md` (no
  meaningful impact; the merge deletes only rows duplicating a newer row's period)

## Validation

- Tests written first: 19 failed on the old code
- `make lint`: pass
- `make security`: pass
- `make test`: 834 passed
- Dry run on a copy of production with a full refresh: 12 duplicates merged; BAC/GS realigned;
  41 share counts fell by more than 3%, none rose

Reviewer pass (fresh subagent): no blockers. Fixed the quality-factor penalty from income-only
quarters (AC7), added an ingestion-level alignment test, and made column and merge tie-breaks
deterministic. Known limitations are listed in the spec.

## After merge

Run `python run_data.py --no-filings --no-13f --refresh-fundamentals` once (about 10 minutes).
The nightly run then rescores with corrected fundamentals.
