## Summary

Fixes two input bugs found by the GPT-6 shadow test. They affect production AI scores whatever
the model.

- **Filing analyzer, mislabelled columns (since the initial commit).** Columns were selected in
  one order and named in another, so 7 of 14 reached the model mislabelled (e.g. CFO labelled
  "total assets", total assets labelled "equity"). Now named in selection order.
- **Risk analyzer, missing risk factors.** It sent the first 80,000 characters of the stored
  10-K, which often ended before Item 1A: 12% of stored risk analyses said they saw no risk
  content (and scored "low risk"). It now locates the Item 1A section in the EDGAR original
  (packet 20 converter), falling back to the old behaviour if that fails or finds under 2,000
  characters. Found for 67 of 72 real 10-Ks (shortest section about 24,000 characters).
- Filing and risk cache keys get a `_v2` suffix, so the next run recomputes them instead of
  reusing results from the broken inputs (nothing deleted; old rows expire normally).

## Spec

- Spec: `docs/specs/30-analyzer-input-fixes.md`
- Test plan: `docs/test-plans/30-analyzer-input-fixes.md`
- PR draft path: `.ai/pr-description/30-analyzer-input-fixes.md`

## Acceptance Criteria

- [x] AC1: Filing DataFrame columns hold their own values.
- [x] AC2: Item 1A section extracted from heading to Item 1B/1C/2.
- [x] AC3: Risk analyzer sends that section, with a logged stored-text fallback.
- [x] AC4: Filing and risk artifact ids versioned.

## Security Review

- [x] Security considerations reviewed in `docs/specs/30-analyzer-input-fixes.md` (adds EDGAR
  fetches on risk cache misses, via packet 20's validated URLs)

## Validation

- Tests written first: the new test module failed to import before the fix
- `make lint`: pass
- `make security`: pass
- `make test`: 935 passed
- Live: Item 1A found in 67/72 10-Ks; FIS columns correct

Reviewer pass (fresh subagent): no blockers. Fixed the Important case (a short contents entry
could pass as the section) with a minimum length and source logging; added edge-case tests.

## Trading impact (measured)

Shadow simulation, current models, 50 sampled tickers swapped in and the universe re-ranked:
filing scores average +2.09, risk -1.88, AI composite +0.05 on average; 16 of 503 tickers change
direction, no LONG/SHORT flips.

## Risks and follow-ups

- The next analysis run recomputes filing and risk for every candidate (risk on gpt-4o: roughly
  $10, plus about 4 minutes of EDGAR fetches), within the $25 per-run ceiling.
- With the real risk-factors text, gpt-4o rates every sampled company HIGH severity, so the
  three-level risk score barely differentiates. Its old variation came mostly from this bug.
  The risk score's design needs a follow-up.
