## Summary

The AI risk score now uses the severity mix of the risks the model lists, not its single overall
label. Since packet 30 the model sees the real risk-factors section and labels nearly every
company HIGH (182 of 191), so the old score was close to a constant in the AI composite.

- `risk_score` is `10 - 2 * mean` of the per-risk severities in `material_risks` (LOW 0, MEDIUM
  1, HIGH 2, CRITICAL 3). Same 4-10 scale as before.
- No score when no risks are listed: those responses mean the model never saw a usable risk
  section (CLX, EIX, MS), and two of them were scoring 10 (lowest risk). The AI composite
  averages the other analyzers for them.
- Cache keys unchanged: the next run rescores stored results with no API calls.

## Spec

- Spec: `docs/specs/31-risk-score-severity-mix.md`
- Test plan: `docs/test-plans/31-risk-score-severity-mix.md`
- PR draft path: `.ai/pr-description/31-risk-score-severity-mix.md`

## Acceptance Criteria

- [x] AC1: Score is `10 - 2 *` the mean valid per-risk severity.
- [x] AC2: Invalid or missing severities are left out of the mean.
- [x] AC3: `None` for no result, no list, an empty list or no valid entries; no label fallback.
- [x] AC4: A `None` risk score drops out of the AI composite and `analyzers_used`.
- [x] AC5: Risk artifact ids unchanged.

## Security Review

- [x] Security considerations reviewed in `docs/specs/31-risk-score-severity-mix.md` (no
  meaningful impact: a calculation over stored data; malformed model output handled without
  exceptions)

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 950 passed
- An adversarial test found a `TypeError` on a list-valued severity; fixed before review.

Reviewer pass (fresh subagent): approve, no blockers or Important items. Display suggestions
(the ticker page rounds the score to a whole number; the report still prints the overall label)
added to `docs/backlog.md`.

## Trading impact (measured)

Read-only re-rank of the 2026-10-14 scores (the rebuild with old risk scores reproduces all 503
stored directions): risk score mean 6.09 to 6.80, standard deviation 0.55 to 0.41; AI composite
+0.18 on average (largest 1.42); 14 of 503 tickers change direction, no LONG/SHORT flips.

## Risks and follow-ups

- The new score's link to realised volatility is weak (rank correlation +0.12). If it stays
  weak, the next step is a prompt asking for a relative 1-10 rating (option 2), which needs a
  full re-run and a shadow test.
- Its spread is narrower than the earnings and filing scores, so risk now counts for less in
  the composite than they do.
- Item 1A extraction still fails for a few tickers (CLX, EIX, MS); backlog item.
