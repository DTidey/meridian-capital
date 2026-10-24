## Summary

Fixes data audit C9: names that AI never reviewed were becoming LONG or SHORT and displacing
AI-reviewed candidates (68 of 205 on 2026-10-23, including 8 of the 20 strongest shorts).

- Direction now comes from Layer 2, so only the LONG/SHORT candidates that Layer 3 reviews can
  be LONG or SHORT. AI reorders candidates; it cannot promote or veto.
- Within each side, quant and AI are ranked separately (mid-rank percentiles), blended 60/40,
  and mapped onto the side's band: longs 80-100, shorts 0-20. Ranking AI fixes the bunched
  1-10 scale; ranking quant within the side keeps 60/40 meaning 60/40.
- A candidate with no AI result gets the neutral AI rank 50. NEUTRAL names keep their quant
  composite. The sector re-rank is removed.
- Spec 03 AC8 marked superseded; audit C9 fix line updated; backlog item for the short-book
  conviction tilt bug found while scoping (own packet).

## Spec

- Spec: `docs/specs/43-combined-score-candidates.md`
- Test plan: `docs/test-plans/43-combined-score-candidates.md`
- PR draft path: `.ai/pr-description/43-combined-score-candidates.md`

## Acceptance Criteria

- [x] AC1: Combined direction equals Layer 2 direction; no promotion or demotion by AI.
- [x] AC2: LONG scores in [80, 100], SHORT in [0, 20]; NEUTRAL keeps its quant composite.
- [x] AC3: Within-side order follows the 60/40 blend of quant and AI strength ranks.
- [x] AC4: A candidate without an AI result keeps its direction with AI rank 50; single-candidate
  sides and no-AI days are handled.
- [x] AC5: Rows written to `combined_scores` as before.

## Security Review

- [x] Security considerations reviewed in `docs/specs/43-combined-score-candidates.md` (no
  new inputs, secrets or network use; changes which names reach the paper book)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- 11 of the new tests fail against `main`'s implementation

Reviewer pass (fresh subagent): no blockers. Important item applied (SHORT-side quant order
was untested: two SHORT tests added, checked by mutation); suggestion applied
(`compute_combined_scores` split to stay under 50 lines). Not taken: dropping NULL-composite
candidates (Layer 2 never writes them), removing the pre-existing unused `_insert_universe`
helper, and the stale `ai_composite` description in spec 03 (out of scope).
- Production dry run on 2026-10-23 data, writes stubbed out: direction equals Layer 2 for all
  503; unreviewed LONG/SHORT 68 -> 0; LONG 81.6-99.1, SHORT 1.3-18.6; PLD NEUTRAL 26.7 (was
  SHORT 13.3); NEE SHORT 14.3 (was NEUTRAL 29.0)

## Risks and follow-ups

- The nightly book changes: 5 of the top 20 longs and 8 of the top 20 shorts differ from
  last night's. Runs are `--whatif`, so nothing trades.
- Audit C9 is signed off after the first nightly run on this code (Verify: no unreviewed
  LONG/SHORT; recheck PLD and NEE).
- Short-book conviction tilt (backlog) is the next small Layer 4 packet.
