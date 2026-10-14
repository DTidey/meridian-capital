## Summary

Fixes 13F institutional holdings ingestion. The stored data was wrong in ways that affect the
institutional factor score, and it needs to be right before packet 18 shows it on the Ticker
page.

- Values were 1000x too large (SEC moved to whole dollars in 2023). A per-filing check now
  rescales filers that still report in $ thousands (Baupost).
- Options (calls and puts) and bonds were stored as stock holdings. They are now skipped.
- Only the first row per ticker in a filing was kept. Rows are now summed.
- Rows were dated by filing date, and amendments counted as extra quarters. Rows are now dated by
  the quarter the filing covers, with `filed_date` stored alongside. Amendments are skipped.
- The summary compared all funds' totals between dates, so a partly filed quarter looked like
  selling. Each fund is now compared with its own previous filing, and funds yet to file carry
  forward.
- Infotables are found from the filing's `index.json`, so Baupost and Berkshire load.
- Third Point and Appaloosa CIKs pointed at GeoEye and Sterling Bancorp. Both are corrected.
- The scoring loader now summarises holdings filed by the score date, so scoring a past date
  can't see later filings (rows are now dated by quarter end).
- Rows stored under the old rules are removed on the next 13F run and re-fetched.

## Spec

- Spec: `docs/specs/17-13f-ingestion-fixes.md`
- Test plan: `docs/test-plans/17-13f-ingestion-fixes.md`
- PR draft path: `.ai/pr-description/17-13f-ingestion-fixes.md`

## Acceptance Criteria

- [x] AC1: `market_value` stored unscaled.
- [x] AC2: Option and PRN rows skipped; rows without an amount type kept.
- [x] AC3: Rows for one ticker in one filing summed before storage.
- [x] AC4: Original 13F-HR only, one per period (most recent), with the submissions `reportDate`.
- [x] AC5: Stored with `report_date` = period and `filed_date`; legacy rows purged first.
- [x] AC6: Summary compares each fund with its own previous filing, with carry-forward.
- [x] AC7: Infotable located via `index.json`, with legacy-name fallback.
- [x] AC8: Third Point and Appaloosa CIKs corrected.
- [x] AC9: Filings reported in $ thousands detected by median implied price and rescaled.
- [x] AC10: The scoring loader only uses 13Fs filed by the score date.

## Security Review

- [x] Security considerations reviewed in `docs/specs/17-13f-ingestion-fixes.md`
- [x] Filenames from `index.json` limited to `.xml` names with no `/`
- [x] The deletion only touches `institutional_holdings` rows with null `filed_date`

## Validation

- Tests written first: 34 failed on the old code; 55 pass now
- `make lint`: pass
- `make security`: pass
- `make test`: 646 passed
- Live dry run into a throwaway SQLite database: all 9 funds load 6 quarters; Berkshire AAPL
  Q2 2026 = 227.9M shares, $65.95B; Point72 has no AAPL row (calls and puts only)

## Risks

- Historical `factor_scores` rows keep their old institutional scores; the next scoring run uses
  corrected data.
- Positions disclosed only in 13F-HR/A amendments are not captured.
- The loader no longer reads `institutional_summary`, which is now used only for logging and
  the dashboard.

Reviewer pass (fresh subagent): no blockers. The Important point (look-ahead when scoring a
past date) is fixed as AC10. Five suggestions taken; zero-holder rows for exited tickers not
taken (factor-design change), see the spec's decision log.
