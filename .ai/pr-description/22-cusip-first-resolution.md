## Summary

13F holdings are now matched to tickers by CUSIP (via OpenFIGI) first, with issuer-name matching
as the fallback. Funds file both Alphabet share classes as "ALPHABET INC", which name-matched
GOOGL, so class C holdings were booked as GOOGL (and GOOGL share counts overstated).

- One OpenFIGI call per filing covers all CUSIPs, using the per-run cache from packet 19.
- If OpenFIGI doesn't resolve a CUSIP, fails, or returns a ticker outside the S&P 500 universe,
  name matching is used as before.
- Without `OPENFIGI_API_KEY`, behaviour is unchanged.

## Spec

- Spec: `docs/specs/22-cusip-first-resolution.md`
- Test plan: `docs/test-plans/22-cusip-first-resolution.md`
- PR draft path: `.ai/pr-description/22-cusip-first-resolution.md`

## Acceptance Criteria

- [x] AC1: CUSIPs looked up via OpenFIGI first, in one call.
- [x] AC2: Unresolved or non-universe results fall back to name matching.
- [x] AC3: Unchanged without an API key.
- [x] AC4: GOOG and GOOGL resolved separately.

## Security Review

- [x] Security considerations reviewed in `docs/specs/22-cusip-first-resolution.md` (no
  meaningful impact)

## Validation

- Tests written first: 4 failed on the old order
- `make lint`: pass
- `make security`: pass
- `make test`: 807 passed (rebased on #56)
- Live dry run: only Alphabet changes (33 GOOG fund-quarters added, 12 moved from GOOGL); all
  other holdings identical

Reviewer pass (fresh subagent): no blockers; test plan and PR draft added, an empty OpenFIGI call
skipped, and failed-batch and adversarial tests added.

## After merge

Run `python run_data.py --no-filings --refetch-13f` once.
