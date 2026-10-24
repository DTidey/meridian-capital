# Combined Score: AI Reorders the Quant Candidates

**Spec file:** `docs/specs/43-combined-score-candidates.md`
**Spec slug:** combined-score-candidates
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit item C9 (`docs/data-audit-2026-10-21.md`): the combined score lets names that AI
never reviewed become LONG or SHORT, and it mixes two scales.

Layer 3 reviews only Layer 2's LONG and SHORT candidates. `compute_combined_scores`
(`analysis/combined_score.py`) then:

1. blends 0.6 x quant composite + 0.4 x AI (1-10 rescaled to 0-100) for reviewed names only,
   and keeps the raw quant composite for every other name;
2. re-ranks all 503 names within sector and re-applies the 80/20 thresholds.

AI composites are bunched (2026-10-23: mean 5.98, standard deviation 0.61), so the blend pulls
every reviewed candidate toward the middle. Unreviewed names just inside the thresholds keep
their raw score and overtake them in the re-rank. On 2026-10-23:

- 28 of 108 combined LONGs and 40 of 97 combined SHORTs were never reviewed by AI;
- 8 of the 20 strongest combined SHORTs, the ones Layer 4 picks first, were unreviewed;
- PLD (quant 26.7, NEUTRAL, no AI) became SHORT at 13.3, and NEE (quant SHORT 19.4, reviewed)
  was pushed to NEUTRAL.

Layer 4 uses the score's size as well as its order: the MVO optimiser maps it to expected
returns and rebalance measures conviction as distance from 50. So the fix must keep LONGs above
50 and SHORTs below.

Goal: only reviewed candidates can be LONG or SHORT, AI reorders them within their side, and
the 60/40 weighting holds in practice.

## Scope
In scope:
- `analysis/combined_score.py` `compute_combined_scores`:
  - direction is the Layer 2 direction;
  - within each side, quant and AI are ranked separately, blended 60/40 and mapped onto the
    side's band;
  - NEUTRAL names keep their quant composite and stay NEUTRAL;
  - the sector re-rank is removed.
- `docs/specs/03-ai-analysis.md`: mark AC8 and the `combined_score` column description as
  superseded by this packet.
- `docs/data-audit-2026-10-21.md` C9: fix line.
- `docs/backlog.md`: new item for the short-book conviction tilt (found while scoping this
  packet; separate packet).
- `tests/test_combined_score.py`.

Out of scope / non-goals:
- Which names Layer 3 reviews, the analyzers, and `compute_ai_composite`.
- AI-only demotion of a candidate to NEUTRAL (a veto). Considered and declined: AI reorders
  but does not select.
- Layer 4: `_apply_conviction_tilt` sorts the short book descending, so the weakest shorts get
  the top multipliers. Backlog item, its own packet.
- L1 (`ai_composite` written as NaN, not NULL).

## Assumptions
- Layer 2's `factor_scores.direction` is LONG at composite >= `long_quintile_threshold` and
  SHORT at <= `short_quintile_threshold` (both from `scoring` in `config.yaml`), so the bands
  below line up with the quant thresholds.
- A candidate with no AI result (no `ai_scores` row, or `analyzers_used == 0`) is still a
  candidate: Layer 3 failed or was skipped for it, which says nothing about the stock. It gets
  the neutral AI rank, 50.
- AI views are directional within a side: for SHORT candidates a lower AI composite is a
  stronger short, as a lower quant composite is.
- Every downstream reader (Layer 4, rebalance, dashboard, JARVIS, reports, attribution) reads
  `combined_scores.combined_score` and `direction` with unchanged meaning: 0-100, LONG high,
  SHORT low.

## Proposed behavior / API
### Public interface
- `analysis.combined_score.compute_combined_scores(conn, score_date, config)`: same signature,
  same returned columns (`ticker`, `sector`, `composite_score`, `ai_composite`,
  `analyzers_used`, `combined_score`, `direction`, plus working columns), same table written.

### Inputs / outputs
- Inputs: `factor_scores` (`composite_score`, `direction`, `sector`) and `ai_scores` for
  `score_date`; `analysis.combined_score.quant_weight` / `ai_weight` and
  `scoring.long_quintile_threshold` / `short_quintile_threshold` from config.
- Strength rank within a side: `(rank - 0.5) / n * 100` with average ranks for ties, where
  `n` is the number of values ranked and 100 is the strongest. For LONG candidates higher
  values are stronger; for SHORT candidates lower values are.
  - `quant_rank`: rank of `composite_score` among that side's candidates.
  - `ai_rank`: rank of `ai_composite` among that side's candidates with an AI result; 50 for
    the rest.
- `blend = quant_weight * quant_rank + ai_weight * ai_rank` (weights normalised to sum to 1).
- Outputs, per ticker:
  - LONG candidate: `combined_score = long_thresh + (100 - long_thresh) * blend / 100`,
    direction LONG.
  - SHORT candidate: `combined_score = short_thresh - short_thresh * blend / 100`, direction
    SHORT.
  - NEUTRAL: `combined_score = composite_score`, direction NEUTRAL.
- Error handling: unchanged. No quant scores for the date logs a warning and returns an empty
  DataFrame.

### Examples
```python
# 2026-10-23 production data, 0.6/0.4 weights, thresholds 80/20
# LONG combined range 81.6-99.1; SHORT 1.3-18.6
# NEE: quant SHORT 19.4 -> combined SHORT 14.3 (was NEUTRAL 29.0)
# PLD: quant NEUTRAL 26.7, no AI -> combined NEUTRAL 26.7 (was SHORT 13.3)
```

## Acceptance criteria
- AC1: A ticker's combined direction equals its Layer 2 direction. No NEUTRAL quant name
  becomes LONG or SHORT, and no LONG or SHORT candidate becomes NEUTRAL, whatever the AI scores.
- AC2: LONG combined scores lie in `[long_thresh, 100]` and SHORT scores in
  `[0, short_thresh]`. NEUTRAL names keep `combined_score == composite_score`.
- AC3: Within a side, the order follows the 60/40 blend of within-side quant and AI strength
  ranks. Better AI moves a long up and a short down. With `ai_weight = 0` the order is the
  quant order, and with `quant_weight = 0` it is the AI order.
- AC4: A candidate without an AI result (no `ai_scores` row, or `analyzers_used == 0`) keeps its
  direction and gets AI rank 50. A side with a single candidate, or with no AI results at all,
  is handled without error.
- AC5: Results are written to `combined_scores` as before (one row per scored ticker, raw
  `ai_composite`, `combined_score`, `direction`).

## Security considerations
- Auth/authz, input handling, secrets, data exposure, file system, network, dependencies: no
  change. Reads and writes the same tables with SQLAlchemy expressions; no new inputs.
- Security notes for reviewers/testers: the change alters which names reach the paper-trading
  book. Nightly runs use `--whatif`, so nothing trades until a real rebalance.

## Edge cases
- Ties in quant or AI within a side: average ranks, so tied names get the same rank.
- One candidate on a side: its ranks are 50, so its score is the middle of the band.
- No AI results anywhere (Layer 3 outage, audit M21): every `ai_rank` is 50 and the order is
  the quant order.
- Weights that do not sum to 1 (e.g. 0.6/0.6): normalised, so scores stay in the band.
- A candidate whose AI row has `analyzers_used == 0` but a stale `ai_composite`: treated as no
  AI result.
- Missing sector: sector no longer affects the combined score.

## Test guidance
- AC1 -> `TestDirection::test_direction_follows_quant`,
  `test_neutral_name_never_promoted_by_bad_ai_on_candidates`,
  `test_candidate_never_demoted_by_bad_ai`
- AC2 -> `TestBands::test_scores_within_side_bands`, `test_neutral_keeps_quant_score`,
  `test_unnormalised_weights_stay_in_band`
- AC3 -> `TestOrdering::test_better_ai_ranks_long_higher`, `test_better_ai_ranks_short_lower`,
  `test_zero_ai_weight_is_quant_order`, `test_zero_ai_weight_is_quant_order_for_shorts`,
  `test_zero_quant_weight_is_ai_order`, `test_blend_matches_formula`,
  `test_blend_matches_formula_for_shorts`
- AC4 -> `TestMissingAi::test_candidate_without_ai_row_gets_neutral_rank`,
  `test_zero_analyzers_treated_as_missing`, `test_single_candidate_side`,
  `test_no_ai_results_at_all`
- AC5 -> `TestPersistence::test_rows_written`, `test_empty_quant_returns_empty`

## Decision log
- 2026-10-24: Direction comes from Layer 2; AI reorders within the candidates and cannot
  promote or veto (owner's choice over an AI veto at 50 and over keeping the sector re-rank,
  which still lets unreviewed names in).
- 2026-10-24: Both inputs ranked within the side before blending (owner's choice). Blending the
  quant composite (80-100 within LONGs) with an AI rank (0-100) would let AI drive about three
  quarters of the ordering; on 2026-10-23 only 7 of the top 20 longs matched quant's top 20,
  against 10 with within-side ranks.
- 2026-10-24: Mid-rank `(rank - 0.5) / n` so the ranks average exactly 50 and a lone value
  ranks 50, the same as the neutral rank given to a missing AI result.
- 2026-10-24: Bands from the existing Layer 2 thresholds, so the scale and the MVO expected
  return range stay as they are for quant candidates today.
