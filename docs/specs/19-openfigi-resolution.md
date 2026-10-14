# OpenFIGI Ticker Resolution

**Spec file:** `docs/specs/19-openfigi-resolution.md`
**Spec slug:** openfigi-resolution
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- 13F holdings are matched to tickers by issuer name, with an OpenFIGI CUSIP lookup as a
  fallback when `OPENFIGI_API_KEY` is set. Name matching misses some quarters (Berkshire's Q1
  2026 filing names Chubb "CHUBB LTD SWITZ"), so about 3% of holdings vanish for a quarter.
  That creates false "new positions" in the factor and on the Ticker page (packet 18).
- The user added an OpenFIGI key on 2026-10-14. A dry run with the key showed the fallback
  doesn't fix this, and makes other things worse:
  1. Companies incorporated outside the US (Chubb, Accenture, Medtronic, Aon) have CINS
     identifiers (first character a letter). The code queries every identifier as `ID_CUSIP`,
     and OpenFIGI answers "No identifier found." for CINS. Chubb stayed missing in Q1 2026.
  2. With no US-listed common stock hit, the code takes the first hit's ticker. For Chubb as
     `ID_CINS` that is `AEX` (a German listing), so a wrong ticker could be stored.
  3. Every resolved holding is stored, including about 40,000 rows for stocks outside the S&P
     500 (46,129 rows against 6,622 in the universe). Nothing uses them, and they flood the
     "3+ funds opening" flag count (9 tickers became 26, mostly non-S&P names).
  4. The same identifier is looked up again for every fund and quarter; the dry run took
     7 minutes instead of about 25 seconds.
  5. Failed OpenFIGI batches (rate limits, outages) are logged at DEBUG only.
- Already-stored filings are never re-fetched, so fixing resolution doesn't repair stored
  data without a re-fetch.

## Scope
In scope:
- `data/institutional.py` OpenFIGI lookup and filtering of resolved holdings.
- A `run_data.py --refetch-13f` flag that deletes stored 13F holdings and re-fetches them.

Out of scope / non-goals:
- Issuer-name matching rules (`_norm_name`), and preferring CUSIP over name when both resolve.
- Storing CUSIPs.
- Alphabet: funds file both classes under "ALPHABET INC", which name-matches GOOGL, so GOOG
  (class C) holdings are booked as GOOGL. Fixing it means preferring CUSIP over name.
- Non-S&P 500 holdings (none of the pipeline uses them).

## Assumptions
- OpenFIGI v3 mapping accepts `ID_CINS` for CINS identifiers. Checked live on 2026-10-14:
  `H1467J104` as `ID_CUSIP` gave "No identifier found."; as `ID_CINS` it gave `CB` on exchange
  `US`, preceded by `AEX` on German exchanges.
- A CUSIP's first character is a digit for US/Canadian issuers; a CINS starts with a letter
  (country code).
- `sp500_universe` is the set of tickers the pipeline scores and the dashboard lists.

## Proposed behavior / API
### Public interface
- `data.institutional._cusip_to_tickers_openfigi(cusips, cache=None)`: `ID_CINS` for
  identifiers starting with a letter; US-listed common stock only; uses and fills `cache`.
- `data.institutional._fetch_13f_infotable(..., cusip_cache=None)`: drops holdings whose
  ticker isn't in `sp500_universe`.
- `data.institutional.update_institutional(conn, config, refetch=False)`.
- CLI: `run_data.py --refetch-13f`.

### Inputs / outputs
- Inputs: 13F infotables; OpenFIGI mapping responses.
- Outputs: `institutional_holdings` rows for S&P 500 tickers only.
- Error handling:
  - A failed OpenFIGI batch is logged at WARNING with the error, and its identifiers stay
    unresolved for this run (not cached).

## Acceptance criteria
- AC1: Identifiers whose first character is a letter are sent to OpenFIGI as `ID_CINS`; all
  others as `ID_CUSIP`.
- AC2: A ticker is taken only from a hit with `securityType` "Common Stock" on a US exchange
  code (`US`, `UN`, `UA`, `UQ`, `UR`, `UT`). With no such hit the identifier stays unresolved;
  there is no first-hit fallback.
- AC3: `_fetch_13f_infotable` returns only holdings whose ticker is in `sp500_universe` (read
  from the table, not the name map). Before the check, `.` and `/` in tickers become `-`, the
  universe spelling (`BRK/B` and `BRK.B` become `BRK-B`).
- AC4: Within one `update_institutional` run, each identifier is sent to OpenFIGI at most once,
  including identifiers that came back unresolved. Identifiers in a failed batch are not
  cached.
- AC5: A failed OpenFIGI batch is logged at WARNING.
- AC6: With `update_institutional(..., refetch=True)`, each recent filing fetched (up to 6 per
  tracked fund) replaces that fund's stored rows for its period, only after the new fetch
  returns holdings, logging the count replaced. Periods outside that window and funds no longer
  tracked are kept. `run_data.py --refetch-13f` passes `refetch=True`; it has no effect with
  `--no-13f`.
- AC7: Issuer names that normalise to the same name for several universe tickers (share
  classes: FOX/FOXA, NWS/NWSA) are left out of the name map, so those holdings resolve by CUSIP.
- AC8: An OpenFIGI response that isn't a JSON list is a failed batch (AC5). Identifiers missing
  from a short response are cached as unresolved (AC4).

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: None new; identifiers are sent as JSON values.
- Secrets or credential handling: `OPENFIGI_API_KEY` read from the environment as before, sent
  only to `api.openfigi.com`.
- Data exposure or privacy impact: None; 13F data is public.
- File system access impact: None.
- Network or external service impact: Fewer OpenFIGI requests (AC4).
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: AC6 replaces a period's rows only after its re-fetch
  succeeds, and leaves everything outside the fetched window alone.

## Edge cases
- No API key: lookup skipped, as before; universe filter still applies.
- Identifier with an empty `data` list or a `warning`: unresolved, cached as unresolved.
- Lower-case letters in an identifier: treated as CINS (first character is a letter).
- OpenFIGI returns fewer results than identifiers sent: missing ones stay unresolved.

## Test guidance
- AC1, AC2 -> `tests/test_institutional.py::TestOpenFigi`
- AC3 -> `TestUniverseFilter`
- AC4, AC5 -> `TestOpenFigi`
- AC6 -> `TestRefetch`
- AC3 (share classes), AC7 -> `TestShareClassTickers`
- AC8 -> `TestOpenFigi::test_malformed_response_is_failed_batch`,
  `test_short_response_leaves_rest_unresolved`

## Decision log
- 2026-10-14: Non-S&P 500 holdings dropped at ingestion rather than filtered downstream. No
  consumer uses them, and they were 86% of rows and most of the flag count.
- 2026-10-14: No first-hit fallback. A foreign listing's ticker can collide with an unrelated
  US ticker; an unresolved holding is safer than a wrong one.
- 2026-10-14: Re-fetch is an explicit flag, not automatic, because it re-downloads every
  filing. Use it after changing resolution rules or adding the API key.
- 2026-10-14 (Reviewer pass): no blockers. Fixed: universe read from `sp500_universe` (the
  name map merged FOX/FOXA and NWS/NWSA); share-class ticker spelling normalised; re-fetch
  replaces per period after a successful fetch instead of deleting everything first, so
  history beyond the last 6 filings survives; non-list responses and short responses handled;
  `update_institutional` and `_fetch_13f_infotable` split under 50 lines. A new test then showed
  name matching booking Fox class B as FOXA, fixed by AC7.
