# CUSIP-First 13F Resolution

**Spec file:** `docs/specs/22-cusip-first-resolution.md`
**Spec slug:** cusip-first-resolution
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- 13F holdings are matched to tickers by issuer name first, then by CUSIP via OpenFIGI. Funds
  file both Alphabet share classes as "ALPHABET INC", which name-matches GOOGL, so GOOG
  (class C) holdings are booked as GOOGL: 48 GOOGL rows and no GOOG rows on 2026-10-14.
- A CUSIP identifies the exact security, so it should take precedence when available. The user
  asked for this fix on 2026-10-14, as its own packet.

## Scope
In scope:
- Resolution order in `data/institutional._resolve_tickers`.

Out of scope / non-goals:
- OpenFIGI query rules (packet 19), name normalisation, the universe filter.

## Assumptions
- With `OPENFIGI_API_KEY` set, OpenFIGI maps each class's CUSIP to its own ticker (Alphabet
  class A `02079K305` to GOOGL, class C `02079K107` to GOOG).
- The per-run identifier cache (packet 19) keeps the extra lookups cheap: universe holdings add
  roughly 500 identifiers per run.

## Proposed behavior / API
### Public interface
- `data.institutional._resolve_tickers(raw, conn, cusip_cache)`: same signature, new order.

### Inputs / outputs
- Unchanged.

## Acceptance criteria
- AC1: Holdings without an `issuerTicker` and with a CUSIP are looked up by OpenFIGI first, all
  in one call (via the run cache).
- AC2: A holding whose OpenFIGI ticker (after share-class spelling is normalised) is not in
  `sp500_universe`, or that OpenFIGI doesn't resolve, falls back to issuer-name matching.
- AC3: Without `OPENFIGI_API_KEY`, resolution is unchanged (issuer name only).
- AC4: Two holdings with the same issuer name and different CUSIPs resolve to their own share
  classes (GOOG and GOOGL).

## Security considerations
- No meaningful security impact: same external service and data as packet 19; only the order
  of resolution changes.

## Edge cases
- OpenFIGI batch failure: those holdings fall back to name matching (AC2).
- A holding with a ticker element in the XML: unchanged, no lookup.

## Test guidance
- AC1-AC4 -> `tests/test_institutional.py::TestCusipFirst`
- Existing `TestUniverseFilter` updated: name-matched holdings are now looked up too.

## Decision log
- 2026-10-14: Name match kept as a fallback rather than removed, so resolution still works with
  no API key, during OpenFIGI outages, and for tickers OpenFIGI reports under a non-universe
  spelling.
- 2026-10-14: Root of the old misbooking, left as is: `_norm_name` strips "CLASS A/B" but not
  "CLASS C", so universe names normalise to "ALPHABET" (GOOGL) and "ALPHABET CLASS C" (GOOG),
  and the filed "ALPHABET INC" matches GOOGL. Without an OpenFIGI key that fallback still applies.
- 2026-10-14 (Reviewer pass): no blockers. Added the test plan and PR draft, skipped the OpenFIGI
  call when no CUSIPs need it, and added failed-batch, empty-CUSIP and BRK/B tests.
