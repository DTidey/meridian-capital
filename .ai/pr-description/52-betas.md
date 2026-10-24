## Summary

Fixes data audit H3: beta estimates depended on which other tickers were in the call, were not
shrunk, and the optimiser's beta adjustment did nothing. On 2026-10-23 the target book's net
beta was -0.33 against a 0.15 limit and a 0.20 pre-trade veto.

- `compute_betas`: each ticker is regressed on SPY over the last 60 SPY returns, on the days
  they share (at least 20, else 1.0), so other tickers' gaps no longer drop days; then
  Blume-adjusted `0.67 * raw + 0.33` (owner's choice). Used by both optimisers, the pre-trade
  beta veto and the book sync.
- `_adjust_beta`: the old code scaled the short book and renormalised it back (a no-op). It now
  reweights within each book, longs by exp(-k beta) and shorts by exp(+k beta), keeping both
  gross targets and the position bounds, with k found by bisection (owner's choice over a SPY
  hedge); a warning if the limit cannot be reached.
- MVO covariance keeps days on which some tickers are missing.
- Spec 04 notes; audit H3 fix line.

## Spec

- Spec: `docs/specs/52-betas.md`
- Test plan: `docs/test-plans/52-betas.md`
- PR draft path: `.ai/pr-description/52-betas.md`

## Acceptance Criteria

- [x] AC1: A ticker's beta is the same alone or with gappy peers.
- [x] AC2: Blume-adjusted; fewer than 20 shared returns gives 1.0.
- [x] AC3: Net beta brought within the limit, gross and bounds kept.
- [x] AC4: Unreachable limit warns; within the limit, unchanged.
- [x] AC5: MVO covariance keeps days with gaps.

## Security Review

- [x] Security considerations reviewed in `docs/specs/52-betas.md` (no meaningful security
  impact; makes the beta limit and veto effective)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- 12 of the new tests fail against `main`
- Production dry run on 2026-10-23: gross 150.0%, net 10.0%, net beta -0.150 (was -0.326)
- Read-only pre-trade simulation of that whole target book: 24 pass, 16 resized for earnings
  blackout, none rejected (also audit H4's Verify)

Reviewer pass (fresh subagent): blocker fixed (a book with too few names to hold its gross
within the 5% cap was silently shrunk by the tilt, and a one-sided check could accept an
overshoot past the limit on the other side: such books now skip the tilt with a warning, and
|net beta| is checked on both sides). Important items fixed: returns on SPY's trading days (a
peer's off-calendar row could break SPY's next return); tests at production bounds; the MVO gap
test now exercises the per-ticker fill.

## Risks and follow-ups

- Reach: under the 5% position cap, reweighting moves net beta by roughly 0.08-0.10 when there
  is little beta spread inside each book. 2026-10-23's real book reached -0.150; a harsher book
  would log a warning and fall to the pre-trade veto (0.20).

- Shrunk betas change the pre-trade beta veto and the book sync's stored betas too (intended:
  optimiser and veto agree).
- The tilt runs after the sector step, so sector nets can move slightly; pre-trade still checks
  sector concentration.
