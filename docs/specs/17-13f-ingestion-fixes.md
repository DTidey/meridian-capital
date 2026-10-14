# 13F Ingestion Fixes

**Spec file:** `docs/specs/17-13f-ingestion-fixes.md`
**Spec slug:** 13f-ingestion-fixes
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The user asked for institutional holdings on the Ticker page (packet 18). Checking the stored
  data against live SEC EDGAR filings showed it is wrong, so it must be fixed before display.
- It also feeds the institutional factor (5% of the composite score), so scores are affected.
- Defects found on 2026-10-14 (AAPL rows compared with Point72's 13F for Q2 2026):
  1. `market_value` is 1,000x too large. `_parse_infotable` multiplies `<value>` by 1000, but SEC
     has reported it in whole dollars since 2023-01-03. Point72 AAPL is stored as $142.1B; the
     filing says $142.1M.
  2. Option positions are stored as holdings. `<putCall>` is ignored. Point72's AAPL row is a
     call option; the filing has no AAPL common stock at all.
  3. Only the first row per (fund, ticker, filing) is kept. A filing can list one issuer several
     times (calls, puts, several managers); `insert_or_ignore` drops the rest instead of summing.
  4. `report_date` is the filing date, not the quarter the filing covers. Amendments
     (`13F-HR/A`) are treated as extra quarters: Citadel's 2026-09-02 amendment makes AAPL look
     like it went from 3 funds to 1.
  5. `_summarise_holdings` compares all funds' totals between report dates. Funds file on
     different days, so a partly filed quarter reads as funds selling out.
  6. Infotables are fetched only as `infotable.xml` / `form13fInfoTable.xml`. Baupost
     (`BGLLCQ22026.xml`) never loads; Berkshire (`56757.xml`) loaded once.
  7. Two config CIKs are wrong: `0001040570` is GeoEye, Inc. (not Third Point) and `0001070154`
     is Sterling Bancorp (not Appaloosa). Neither fund has ever loaded.

## Scope
In scope:
- Parsing, filing selection, infotable discovery, summary logic and the two CIKs.
- A `filed_date` column on `institutional_holdings`.
- Removing rows stored under the old rules so they are re-fetched.

Out of scope / non-goals:
- Display (packet 18).
- `13F-HR/A` amendments, including "new holdings" amendments that add confidential positions.
- Ticker resolution (name map and OpenFIGI): unchanged.
- The institutional factor maths in `factors/institutional.py`: unchanged.
- Historical `factor_scores` rows: not recomputed. The next scoring run uses corrected data.
- A second original 13F-HR for a period already loaded is not stored; the first stays. This is
  rare, and an amendment is the normal correction route (also out of scope).
- A ticker every fund exited in an earlier quarter keeps that quarter as its latest summary row.

## Assumptions
- `<value>` is in whole dollars for every filing fetched. The fetcher reads at most the 6 most
  recent filings per fund, all filed after 2023-01-03.
- The submissions JSON `reportDate` is the quarter-end period the 13F covers (checked against
  Point72, Berkshire, Tiger Global, Third Point and Appaloosa on 2026-10-14).
- Each filing folder's `index.json` lists its files. The infotable is the `.xml` file that is not
  `primary_doc.xml` (checked against Point72, Baupost and Berkshire).
- Some filers still report `<value>` in $ thousands. Baupost does in every filing fetched on
  2026-10-14 (AMZN Q2 2026: 3,743,854 shares, value 892,310). No S&P 500 stock trades near $1,
  so a median implied price below $1 reliably marks such a filing.
- Correct CIKs, from EDGAR on 2026-10-14: Third Point LLC `0001040273`, Appaloosa LP
  `0001656456`. Appaloosa Management LP (`0001006438`) last filed a 13F in 2016.

## Proposed behavior / API
### Public interface
- `data.institutional._get_recent_13f_accessions` returns `(accession, filed_date, period)`
  tuples.
- `data.institutional._parse_infotable` returns common-stock rows only, value unscaled.
- New `data.institutional._aggregate_by_ticker(rows) -> list[dict]`.
- New `data.institutional._infotable_filenames(session, base, limiter) -> list[str]`.
- New `data.institutional._purge_legacy_rows(conn) -> int`.
- New `data.institutional._normalise_value_units(rows) -> bool` (True when it rescaled).
- `data.institutional._summarise_holdings`: same signature, new rules (AC6).
- New `data.institutional.summarise_rows(rows) -> list[dict]`: the AC6 rules as a pure function,
  shared by `_summarise_holdings` and the scoring loader.
- New `data.institutional._load_filing(...)`: one filing's fetch and store, split out of
  `update_institutional`.
- `factors.loader._load_institutional`: reads `institutional_holdings` instead of
  `institutional_summary` (AC10).
- `data.db.institutional_holdings`: new nullable `filed_date` column, added to existing
  databases by `initialise_schema`.
- CLI entry points: unchanged.

### Inputs / outputs
- Inputs: SEC EDGAR submissions JSON, filing `index.json`, infotable XML.
- Outputs: `institutional_holdings` rows with `report_date` = period, `filed_date` = filing date;
  rebuilt `institutional_summary`.
- Error handling:
  - A failed `index.json` fetch falls back to the two legacy filenames.
  - Network and parse failures are logged and skipped per filing, as before.

## Acceptance criteria
- AC1: `_parse_infotable` stores `<value>` unscaled (whole dollars) as `market_value`.
- AC2: `_parse_infotable` skips rows that have a non-empty `<putCall>`, and rows whose
  `<sshPrnamtType>` is `PRN`. Rows with no `<sshPrnamtType>` are kept.
- AC3: Holdings resolved to the same ticker in one filing are summed (shares and market value)
  into one row before storage. A `None` value counts as 0 when others are present.
- AC4: `_get_recent_13f_accessions` returns only `13F-HR` filings (not `13F-HR/A` or `13F-NT`),
  as `(accession, filed_date, period)` where `period` is the submissions `reportDate`. When two
  `13F-HR` filings cover one period, only the most recently filed is returned (filings are
  sorted by filing date, not taken in the order SEC lists them). Up to
  `max_filings` distinct periods are returned.
- AC5: Holdings are stored with `report_date` = period and `filed_date` = filing date.
  `update_institutional` first deletes every `institutional_holdings` row with a null
  `filed_date` (rows from before this packet), logging the count at WARNING.
- AC6: `_summarise_holdings` builds one row per (ticker, period) using these rules:
  - `funds_holding`: number of funds whose latest filing with period <= P includes the ticker.
    Funds that have not yet filed for P count with their previous filing.
  - `net_share_change`: sum, over funds that filed for P and have an earlier filing, of shares
    at P minus shares at that fund's previous filing (absent = 0). `None` when no fund that
    filed for P has an earlier filing.
  - `new_positions`: number of funds that filed for P, have an earlier filing, and hold the
    ticker at P but not at their previous filing.
  - A row is written for every ticker that is in any fund's as-of filing at P (including
    filings carried forward from funds yet to file), or in the previous filing of a fund that
    filed at P, so full exits in P appear with `funds_holding` 0.
- AC7: The infotable is located from the filing's `index.json`: every file whose whole name
  matches `[\w.-]+\.xml` (case-insensitive), other than `primary_doc.xml`, tried in listed
  order. If `index.json` can't be fetched or lists none, the
  legacy names `infotable.xml` and `form13fInfoTable.xml` are tried.
- AC8: `config.yaml` uses CIK `0001040273` for Third Point and `0001656456` for Appaloosa.
- AC9: A filing whose median implied price (`market_value / shares` over rows with both
  positive) is below $1 is treated as reported in $ thousands: its `market_value`s are
  multiplied by 1000 before storage, logged at INFO. Other filings are stored unchanged.
- AC10: The scoring loader (`factors/loader._load_institutional`) builds its summary with the
  AC6 rules from holdings whose `filed_date` <= the score date, so scoring a past date never
  sees a 13F filed after it. Its output columns are unchanged.

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: Filenames from `index.json` are only used if the whole name
  matches `[\w.-]+\.xml`, so a crafted index can't redirect requests outside the filing
  folder. XML parsing is unchanged (`xml.etree`, already marked for bandit).
- Secrets or credential handling: None.
- Data exposure or privacy impact: None; 13F data is public.
- File system access impact: None.
- Network or external service impact: One extra SEC request per new filing (`index.json`),
  inside the existing 8 req/s limit. The one-off re-fetch is about 54 filings.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: AC5 deletes rows. It only touches
  `institutional_holdings` rows with null `filed_date`, which no code path writes after this
  packet.

## Edge cases
- A fund's only loaded filing: it counts toward `funds_holding` but adds nothing to
  `net_share_change` or `new_positions` (no baseline to compare with).
- A fund skipped a quarter: compared with its latest earlier filing, whatever its period.
- A filing with only option rows for a ticker: that ticker is absent for the fund.
- `13F-NT` (notice of no holdings, filed by a parent): ignored, as before.
- A filing with no rows that have both shares and value: units left as filed.
- `index.json` lists a `.xml` that isn't an infotable: the parser returns no rows and the next
  file is tried.

## Test guidance
- AC1 -> `tests/test_institutional.py::TestParseInfotableRules::test_value_is_whole_dollars`
- AC2 -> `TestParseInfotableRules::test_skips_options`, `test_skips_principal_amounts`
- AC3 -> `TestAggregateByTicker`
- AC4 -> `TestRecentAccessions`
- AC5 -> `TestStorage`, `TestPurgeLegacyRows`
- AC6 -> `TestSummariseHoldings`
- AC7 -> `TestInfotableFilenames`
- AC8 -> `TestConfigCiks`
- AC9 -> `TestNormaliseValueUnits`, `TestStorage::test_thousands_filing_rescaled_and_logged`
- AC10 -> `TestLoaderPointInTime`

## Decision log
- 2026-10-14: Amendments skipped rather than merged. Telling "restatement" from "new holdings"
  needs `primary_doc.xml` parsing; originals give a consistent quarterly series.
- 2026-10-14: Per-fund comparison with carry-forward replaces whole-quarter totals, so the
  latest quarter is usable while funds are still filing.
- 2026-10-14: A fund's first loaded filing produces no new positions. Previously everything in
  the oldest loaded quarter counted as new, which could raise false "simultaneous open" flags.
- 2026-10-14: Units detected per filing from the median implied price, not per row, so one
  odd row can't flip a filing. Found in the dry run, after the first draft of this spec.
- 2026-10-14: Legacy rows removed automatically by the null `filed_date` marker rather than by a
  manual flag, so no operator step is needed. The user approved delete-and-re-fetch.
- 2026-10-14 (Reviewer pass): no blockers. Important: the summary is dated by quarter end, so
  scoring a past date would see later filings. Fixed with AC10: the loader summarises holdings
  filed by the cutoff. Also taken: sort filings by date, a stricter filename pattern, rescale
  and purge log tests, `_load_filing` split out, and corrected test names. Not taken: writing
  zero-holder rows for tickers exited in earlier quarters. That would rank "exited" below
  "never held" (neutral 50), which is a factor-design change.
