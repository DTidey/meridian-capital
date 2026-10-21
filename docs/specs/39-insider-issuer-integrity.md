# Insider Issuer Integrity

**Spec file:** `docs/specs/39-insider-issuer-integrity.md`
**Spec slug:** insider-issuer-integrity
**Status:** Draft
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
The 2026-10-21 data audit found three defects in SEC ingestion that move insider scores by 25
points or more for about 40 tickers.

- **Trades in other companies are booked as the ticker's insider trades.** A company's EDGAR
  submissions feed also lists Form 4s where the company itself is the reporting owner (its
  holdings in other issuers). `update_sec_data` stores every Form 4 in the feed under the ticker
  and never checks the issuer. Examples: BRK-B's `insider_score` of 100 comes entirely from
  Berkshire buying Lennar, and the corrected score is 15.8. UBER's 1.2 comes from an Aurora sale,
  and the corrected score is 98.8. In total, 237 filings and 556 transaction rows across about 35
  tickers are affected.
- **The unique key drops real transactions.** The key is
  `(ticker, accession_no, insider_name, date, shares)`. Two transactions in one filing with the
  same share count collapse into one row, and the second is silently ignored. The most common case
  is an option exercise (M) followed by a sale (S) of the same number of shares: TT keeps the M
  and loses the S. 2,390 open-market rows ($5.4B of sales) are missing.
- **The company (CIK) lookup is wrong or missing.** The legacy `browse-edgar` lookup returns no
  CIK for 29 universe tickers (GEV, KVUE, VLTO, XYZ and others). It returns a dead CIK for BLK
  and a BlackRock municipal fund for BNY. A CIK found once in a stored URL is reused forever, so
  XOM stays on its old CIK and misses filings under its current one. SEC's own
  `company_tickers.json` maps all 503 universe tickers, BRK-B included.

Goal: insider transactions are stored only for the issuer they concern, every transaction in a
filing is kept, every universe ticker maps to its current CIK, and existing data is rebuilt to
match.

## Scope
In scope:
- `data/sec_data.py`:
  - CIK lookup from `company_tickers.json`, once per run, replacing `browse-edgar` and the
    stored-URL CIK reuse.
  - The Form 4 parser returns the issuer CIK and each transaction's position in the filing.
  - Transactions are stored only when the issuer CIK matches the ticker's CIK.
  - A rebuild function that re-creates `insider_transactions` and `insider_cluster_flags` from
    the Form 4 XML already stored in `sec_filings`.
- `data/db.py`:
  - New `insider_transactions.txn_index` column.
  - The unique key becomes `(ticker, accession_no, txn_index)`.
  - An idempotent migration for existing databases.
- `run_data.py`: `--rebuild-insider` flag.
- `run_all.py`: forwards `--forms` to `run_data.py`, so cron can fetch Form 4s nightly.

Out of scope / non-goals:
- 13F resolution (REITs, GOOGL depositary shares): a later packet.
- Insider factor logic: 10b5-1 handling, dollar normalisation, cluster-flag scoring, CEO/CFO
  title matching.
- Form 4/A amendments, and filed-date storage on transactions.
- `sec_filings.accession_no` stays globally unique, so one share-class ticker owns each filing
  row. Insider rows are still written for every share-class ticker of the issuer, as now.
- Rows for tickers no longer in the universe (BK, EA and others) are not rebuilt.
- Editing the crontab. That is the owner's change after merge: Mon-Thu adds `--forms 4` and drops
  `--no-filings`.
- Running the production rebuild. That is a separate, confirmed step after merge.

## Assumptions
- `https://www.sec.gov/files/company_tickers.json` maps tickers in the universe's spelling
  (BRK-B, BF-B). Share classes share a CIK (GOOG and GOOGL both map to 1652044). Checked
  2026-10-21: all 503 universe tickers are present.
- Every stored Form 4 `content_text` is an `ownershipDocument` with an `issuerCik` element.
  Checked 2026-10-21: 31,980 of 31,980. Every current `insider_transactions` row has its Form 4
  XML stored, so the rebuild needs no EDGAR document downloads.
- Matching on the mapped issuer CIK alone is not enough for the rebuild. Checked 2026-10-21:
  - 31,415 stored Form 4s name the ticker's mapped CIK as issuer.
  - 237 name another issuer; these are the defect.
  - Renamed tickers (BK to BNY, EQR to VMRK) reach the current ticker through the issuer CIK.
  - XOM's 36 stored Form 4s name its predecessor CIK (34088, Exxon Mobil Corp), while
    `company_tickers.json` now maps XOM to its new holding company (2115436). A dry run of the
    issuer-CIK-only rebuild cut XOM from 8 open-market rows to 0.
- A predecessor CIK cannot be recognised reliably from the filing alone. The BlackRock New York
  Municipal Income Trust (CIK 1137390) files under the trading symbol "BNY", which is why the
  legacy lookup mapped BNY to it. Neither 34088 nor 1137390 appears in `company_tickers.json`
  today. Predecessor CIKs are therefore listed explicitly in config; XOM is the only universe
  ticker that needs one (production dry run, 2026-10-21).
- The order of `nonDerivativeTransaction` elements in a filing never changes, so their 0-based
  position identifies a transaction within the filing.
- Insider data is used only for the last 90 days (scoring) and 180 days (cluster flags). Dropping
  rows for tickers outside the universe loses nothing that is used.

## Proposed behavior / API
### Public interface
- `_load_cik_map(session, limiter) -> dict[str, str]`: ticker to CIK (no leading zeros), from
  `company_tickers.json`. Raises on a network or format failure.
- `_parse_form4_xml(xml_text) -> tuple[str | None, list[dict]]`: the issuer CIK (no leading
  zeros) and the transactions. Each transaction dict gains `txn_index`.
- `update_sec_data(conn, tickers, config, forms=None)`: same signature. Uses the CIK map.
  Inserts Form 4 transactions only when the issuer CIK equals the ticker's CIK.
- `rebuild_insider_transactions(conn, tickers, config) -> dict[str, int]`: rebuilds from stored
  XML and returns rows per ticker.
- `config.yaml` `sec.predecessor_ciks`: ticker to a list of earlier CIKs whose Form 4s belong to
  that ticker (initially `XOM: ["34088"]`). Read only by the rebuild.
- `run_data.py --rebuild-insider`: loads the universe, runs the rebuild, and skips all other
  ingestion steps.
- `run_all.py --forms FORM [FORM ...]`: forwarded to `run_data.py`.

### Inputs / outputs
- **CIK map.**
  - Fetched once at the start of `update_sec_data`. If that fetch fails, log an ERROR and store
    nothing that run, without falling back to old CIKs.
  - A ticker missing from the map is logged at WARNING and skipped.
  - When a ticker's mapped CIK differs from the CIK in its stored filing URLs, log a WARNING
    once (e.g. XOM 34088 to 2115436).
- **Issuer check.**
  - The Form 4 filing row is stored as now, which marks it as seen.
  - Its transactions are inserted only when the issuer CIK equals the ticker's CIK.
  - A skipped filing is counted and logged at DEBUG. The run summary logs the total at INFO.
- **Unique key.** `(ticker, accession_no, txn_index)`. The PostgreSQL migration in
  `initialise_schema` does three things:
  - adds the column if missing;
  - drops the old 5-column unique constraint if present;
  - adds the new constraint if absent.
  Re-running the migration changes nothing.
- **Rebuild.** One transaction that:
  1. deletes all rows from `insider_transactions` and `insider_cluster_flags`;
  2. for every `sec_filings` row with `form_type = '4'` and stored content, parses the XML;
  3. inserts the transactions once for each universe ticker whose CIK, or one of whose
     configured predecessor CIKs (`sec.predecessor_ciks` in `config.yaml`, e.g.
     `XOM: ["34088"]`), equals the issuer CIK;
  4. recomputes cluster flags for every universe ticker.

  Any exception rolls the whole transaction back, leaving the old data in place.
- **Logging.** The rebuild logs filings parsed, filings with no universe issuer, rows inserted
  and flags written.

### Examples
```text
$ python run_data.py --rebuild-insider
INFO  Insider rebuild: 31980 Form 4 filings parsed, 237 with no universe issuer skipped,
      60xxx rows inserted for 474 tickers, NN cluster flags
$ python run_all.py --whatif --no-13f --forms 4      # proposed Mon-Thu cron
```

## Acceptance criteria
- AC1: `_load_cik_map` returns the ticker-to-CIK map from `company_tickers.json` with leading
  zeros stripped, and raises when the response is not the expected JSON.
- AC2: `update_sec_data` uses the mapped CIK even when stored filing URLs carry a different CIK,
  and logs a WARNING naming both CIKs.
- AC3: `update_sec_data` skips a ticker missing from the map with a WARNING, and stores nothing
  when the map cannot be loaded. It does not call `browse-edgar`.
- AC4: `_parse_form4_xml` returns the issuer CIK and gives each non-derivative transaction its
  0-based position as `txn_index`.
- AC5: A Form 4 whose issuer CIK differs from the ticker's CIK stores its filing row but no
  `insider_transactions` rows.
- AC6: Two transactions in one filing with the same insider, date and share count (e.g. M and S
  of 43,778 shares) are both stored.
- AC7: The schema migration replaces the 5-column unique constraint with
  `(ticker, accession_no, txn_index)`, and running it twice succeeds and changes nothing.
- AC8: `rebuild_insider_transactions` replaces all insider rows and cluster flags from stored
  XML:
  - other-issuer filings are excluded;
  - share-class tickers (GOOG and GOOGL) each receive the issuer's transactions;
  - stale cluster flags (e.g. one built from another issuer's trades) are removed;
  - a filing whose issuer is a configured predecessor CIK (XOM's 34088) is kept for that
    ticker;
  - a filing whose issuer is an unconfigured CIK is excluded, even when its trading symbol
    equals a universe ticker (the BlackRock fund filing as "BNY").
- AC9: If the rebuild fails partway, `insider_transactions` and `insider_cluster_flags` are
  unchanged.
- AC10: `run_data.py --rebuild-insider` runs only the rebuild. `run_all.py --forms 4` passes
  `--forms 4` to `run_data.py`.
- AC11: `make lint`, `make test` and `make security` pass.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk:
  - XML parsing is unchanged (`xml.etree` on SEC-supplied text, existing nosec notes).
  - `company_tickers.json` values are used only as bound parameters and in URL paths, after
    being checked as digits.
- Secrets or credential handling: none (the SEC user agent comes from `.env` as now).
- Data exposure or privacy impact: none. The data is public SEC filings.
- File system access impact: none.
- Network or external service impact:
  - One extra SEC request per run (`company_tickers.json`).
  - Nightly Form 4 fetches if the owner changes cron, within the existing 8 req/s limiter.
  - The rebuild fetches only `company_tickers.json`.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers:
  - The rebuild deletes and re-inserts in a single transaction. Verify the rollback path (AC9).

## Edge cases
- **CIK map entries for tickers not in the universe:** ignored.
- **Ticker present in the universe but not in the map:** warned and skipped (AC3).
- **Form 4 with no `issuerCik`:** treated as a mismatch, so no transactions are stored.
- **Form 4 with only derivative transactions:** no rows, as now.
- **Issuer CIK with leading zeros in the XML (e.g. `0001067983`):** compared with zeros stripped.
- **A share-class sibling's filing row is owned by the other class:** the rebuild still inserts
  that sibling's transactions, because it maps by issuer CIK, not by filing-row ticker.
- **Same accession processed again in a later run:** the unique key makes the insert a no-op.
- **Old rows with null `txn_index` after migration:** removed by the rebuild. Until then they
  cannot collide with new rows, because filings already seen are skipped by accession.

## Test guidance
- AC1 -> `tests/test_sec_data.py::TestCikMap`.
- AC2, AC3 -> `tests/test_sec_data.py::TestUpdateSecDataCik` (mocked session).
- AC4 -> `tests/test_sec_data.py::TestParseForm4Xml::test_issuer_cik_and_txn_index`.
- AC5 -> `tests/test_sec_data.py::TestUpdateSecDataIssuer::test_other_issuer_not_stored`.
- AC6 -> `tests/test_sec_data.py::TestUpdateSecDataIssuer::test_same_shares_both_stored`.
- AC7 -> `tests/test_db.py::TestInsiderUniqueMigration` (SQLite schema; PostgreSQL DDL verified
  against the production constraint name in the review).
- AC8, AC9 -> `tests/test_sec_data.py::TestRebuildInsider`.
- AC10 -> `tests/test_run_data_args.py`; `tests/test_run_all.py`.
- AC11 -> `make lint`; `make test`; `make security`.

## Decision log
- 2026-10-21: Rebuild from stored XML instead of re-downloading. Every affected filing is
  already stored with its XML, so a rebuild takes minutes, makes no EDGAR load, and is exactly
  repeatable.
- 2026-10-21: Key on position within the filing, not on the transaction code. Two lines can share
  every field, including the code (two S lots of 300,000 shares at different prices, as in NVDA).
- 2026-10-21: The rebuild also accepts explicitly configured predecessor CIKs. A production
  dry run found the issuer-CIK-only rule would erase XOM's insider history after its
  holding-company reorganisation. A heuristic (issuer = feed CIK plus matching trading symbol)
  was tried and rejected: it admitted the BlackRock fund that trades as "BNY". The AC2 CIK-change
  WARNING is the cue to add an entry. The nightly fetch needs no such rule: it reads only the
  current CIK's feed.
- 2026-10-21: No fallback to stored CIKs when the map fails to load. Falling back would quietly
  reuse the wrong CIKs (XOM, BNY) that this packet fixes. One skipped SEC run is visible and
  harmless.
