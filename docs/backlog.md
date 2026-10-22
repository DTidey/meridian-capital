# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Risk factors still not found for six 10-Ks

- **Raised:** 2026-10-15, packet 33 universe check.
- **Problem:** C, GE, NEM and SYF use layouts the extractor does not recognise (no Item 1A
  heading, or "Risk Factors Relating to ..." sub-sections only). USB and WFC incorporate their
  risk factors by reference from the annual report exhibit (EX-13), which the analyzer does
  not fetch. All six fall back to the stored 10-K text.
- **Fix:** for USB/WFC, fetch the EX-13 exhibit when Item 1A points to it; for the others,
  inspect their layouts individually.

## Polygon price backend: adjusted close and date index

- **Raised:** 2026-10-21, packet 38.
- **Problem:** `_fetch_polygon` in `data/market_data.py` requests `adjusted=true` and stores that
  adjusted close as both `close` and `adj_close`, so `close` is not the raw close yfinance
  stores. The `dates` line sits inside the `for bar` loop (rebuilt per bar; harmless but
  wrong). Polygon's range end is inclusive, unlike yfinance's, so the packet-38 end date can
  include a partial bar before 16:30 New York. Backend not in use (no `POLYGON_API_KEY`).
- **Fix:** before enabling Polygon, fetch unadjusted and adjusted bars (or compute adj_close),
  move `dates` out of the loop and pass an inclusive end date.

## Insider factor: 10b5-1 plan flag

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** Form 4s mark trades made under a Rule 10b5-1 plan (pre-scheduled, so weak
  signal), but the flag is not parsed or stored, and the insider factor weights planned and
  discretionary sales alike.
- **Fix:** parse and store the 10b5-1 flag on `insider_transactions`; down-weight planned
  trades in the insider factor and analyzer.

## Insider factor: CEO/CFO title matching

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** `_is_ceo_cfo` substring-matches `officerTitle`, so it misses "President and
  Principal Executive Officer" style titles and can match unrelated titles that contain "ceo"
  or "cfo"; it ignores the Form 4 `isOfficer`/`isDirector` relationship flags.
- **Fix:** match on whole words plus the principal executive/financial officer wording; check
  against a sample of stored titles.

## Insider cluster flags: window bookkeeping

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** `_flag_cluster_buys` upserts one flag per purchase date and never removes flags
  whose purchases left the lookback or were deleted (the packet 39 rebuild clears them once);
  overlapping windows count the same cluster several times; `flagged_at` is never set, so it
  is always NULL.
- **Fix:** recompute a ticker's flags from scratch each run (delete then insert), merge
  overlapping windows, and set `flagged_at`.

## Form 4/A amendments and transaction date sanity

- **Raised:** 2026-10-21, packet 39 (out of scope there).
- **Problem:** Form 4/A amendments are not fetched, so corrected filings never replace the
  original; transactions carry no filed date, and a mistyped transaction date (years before or
  after the filing) is stored as is and can enter or leave the scoring window wrongly.
- **Fix:** fetch 4/A and replace the amended accession's rows; store the filed date and flag
  transaction dates far from it.

## Per-CIK fetch for share-class siblings

- **Raised:** 2026-10-21, packet 39.
- **Problem:** `sec_filings.accession_no` is globally unique, so one share class (GOOG or
  GOOGL) owns each filing row. The other class never sees those accessions as already fetched
  and re-downloads the same Form 4s (and 10-K/10-Q/8-K text) every run; the inserts are no-ops.
- **Fix:** fetch submissions once per CIK and record seen accessions per CIK, writing insider
  rows for every ticker of the issuer.
