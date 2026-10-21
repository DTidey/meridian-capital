## Summary

The 2026-10-21 data audit found three SEC ingestion defects that move insider scores by 25
points or more for about 40 tickers. This packet fixes all three and adds a rebuild that
corrects the stored data from the Form 4 XML already in `sec_filings`.

- **Issuer check.** A company's submissions feed also lists Form 4s where the company is the
  reporting owner (its trades in other issuers). These were booked as the ticker's insider
  trades (BRK-B's 100 came from Berkshire buying Lennar). Transactions are now stored only when
  the filing's `issuerCik` equals the ticker's CIK; the filing row is still stored, so it is
  not fetched again. The run logs the number of skipped filings at INFO.
- **Unique key.** `(ticker, accession_no, insider_name, date, shares)` dropped the second of
  two same-size lines in one filing (an M exercise and the S sale of the same shares; two S
  lots). The key is now `(ticker, accession_no, txn_index)`, `txn_index` being the line's
  0-based position among the filing's non-derivative transactions.
- **CIK lookup.** `browse-edgar` returned no CIK for 29 universe tickers and wrong ones for
  BLK and BNY, and a CIK seen once in a stored URL was reused forever (XOM). CIKs now come from
  SEC's `company_tickers.json`, loaded once per run; if it fails, an ERROR is logged and nothing
  is stored that run. A ticker missing from the map is skipped with a WARNING; a CIK that
  differs from the newest stored filing URL logs a WARNING naming both.
- **Migration** (in `initialise_schema`, idempotent): adds `txn_index`, drops the old 5-column
  key (found in `pg_constraint` by its columns; Postgres named it
  `insider_transactions_ticker_accession_no_insider_name_date__key`), adds
  `insider_transactions_ticker_accession_no_txn_index_key` if absent.
- **Rebuild.** `run_data.py --rebuild-insider` loads the universe and runs only
  `rebuild_insider_transactions`: one transaction that deletes all insider rows and cluster
  flags, re-parses every stored Form 4, inserts its transactions under each universe ticker
  whose CIK is the issuer's (so GOOG and GOOGL both get Alphabet's, and BK filings reach BNY),
  and recomputes cluster flags. Any failure rolls back. It makes one SEC request
  (`company_tickers.json`). It refuses `--tickers`, since it deletes every insider row.
- **Cron support.** `run_all.py --forms FORM [FORM ...]` passes the form types to
  `run_data.py`.

## Spec

- Spec: `docs/specs/39-insider-issuer-integrity.md`
- Test plan: `docs/test-plans/39-insider-issuer-integrity.md`
- PR draft path: `.ai/pr-description/39-insider-issuer-integrity.md`

## Acceptance Criteria

- [x] AC1: `_load_cik_map` returns ticker to CIK from `company_tickers.json`, zeros stripped;
  raises on unexpected JSON.
- [x] AC2: `update_sec_data` uses the mapped CIK over stored URLs and logs a WARNING naming both.
- [x] AC3: Ticker missing from the map skipped with a WARNING; nothing stored when the map
  cannot be loaded; no `browse-edgar` call.
- [x] AC4: `_parse_form4_xml` returns the issuer CIK and gives each non-derivative transaction
  its 0-based `txn_index`.
- [x] AC5: A Form 4 for another issuer stores its filing row but no insider rows.
- [x] AC6: Two same-insider, same-date, same-shares lines in one filing (M and S of 43,778) are
  both stored.
- [x] AC7: Migration replaces the 5-column key with `(ticker, accession_no, txn_index)`;
  running it twice succeeds and changes nothing.
- [x] AC8: Rebuild replaces all insider rows and cluster flags from stored XML: other issuers
  excluded, GOOG and GOOGL both filled, stale flags removed.
- [x] AC9: A failed rebuild leaves `insider_transactions` and `insider_cluster_flags` unchanged.
- [x] AC10: `run_data.py --rebuild-insider` runs only the rebuild; `run_all.py --forms 4` passes
  `--forms 4` to `run_data.py`.
- [x] AC11: `make lint`, `make test`, `make security` pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/39-insider-issuer-integrity.md`.
  Disposition: low impact. `company_tickers.json` CIKs are accepted only as 1-10 ASCII digits
  (else the whole map is rejected and nothing is fetched), and are used only in URL paths and
  as bound parameters; XML issuer CIKs pass the same check. XML parsing unchanged. The rebuild
  deletes and re-inserts in a single transaction with rollback (AC9 tests). Migration DDL uses
  fixed identifiers plus a catalog-sourced constraint name, quoted. One extra SEC request per
  run. `make security` (bandit, pip-audit) passes.

## Validation

- New tests failed before the code change (missing `_load_cik_map`,
  `rebuild_insider_transactions`, `_migrate_insider_unique_key`, `--rebuild-insider`,
  `--forms`).
- `make lint`: pass
- `make security`: pass
- `make test`: 1128 passed (1069 before; 59 new)
- Nothing was run against the production database; the PostgreSQL migration is tested against
  a simulated catalog and its SQL reviewed against the production constraint name.

## Post-merge production steps (owner confirms each)

1. Deploy (pull `main` into the live working copy). The first entry point to start runs the
   migration in `initialise_schema` (brief exclusive lock on `insider_transactions`).
2. Only with the owner's confirmation, outside the cron window:
   `python run_data.py --rebuild-insider`. Expect about 31,980 filings parsed, about 237 with
   no universe issuer, roughly 60,000 rows for about 474 tickers.
3. Only with the owner's confirmation: rescore (`python run_scoring.py`) so insider scores use
   the rebuilt rows.
4. Proposed crontab change (owner edits): Mon-Thu run
   `run_all.py --whatif --no-13f --forms 4` (adds `--forms 4`, drops `--no-filings`), so new
   Form 4s are fetched nightly.

## Risks and follow-ups

- The rebuild deletes every insider row and flag in one transaction; run it outside the cron
  window, as a concurrent nightly insert can wait on its locks. It drops rows for tickers no
  longer in the universe (BK, EA and others), which scoring does not use.
- Until the rebuild runs, old rows keep their wrong-issuer and missing-line defects, and old
  rows have `txn_index` NULL; they cannot collide with new rows because seen accessions are
  skipped.
- First nightly run after merge: one extra request (`company_tickers.json`); if SEC is down,
  that run stores no SEC data (by design, ERROR logged).
- Insider analysis cache keys (packet 37) hash the transaction text, ordered by date then id.
  The rebuild changes the rows of the affected tickers and gives every row a new id, so many
  keys may change and the next analysis run makes more insider calls than usual (about one
  full run, as on the first night after packet 37).
- GOOG/GOOGL still re-download each other's filings every run (backlog: per-CIK fetch).
- Deferred to `docs/backlog.md`: 10b5-1 flag, CEO/CFO title matching, cluster-flag window
  bookkeeping and `flagged_at`, Form 4/A and transaction date sanity, per-CIK fetch.
