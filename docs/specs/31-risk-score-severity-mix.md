# Risk Score from Severity Mix

**Spec file:** `docs/specs/31-risk-score-severity-mix.md`
**Spec slug:** risk-score-severity-mix
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-15

## Problem statement
The risk analyzer's score comes from a single overall label (`risk_severity`: LOW, MEDIUM,
HIGH, CRITICAL mapped to 10, 8, 6, 4). Since packet 30 gave the model the real Item 1A
section, almost every company is labelled HIGH: 182 of 191 cached results (2026-10-15). The
risk score is one of four equally weighted inputs to the AI composite (40% of the combined
score), so a near-constant input adds no ranking information and pulls every AI composite
towards 6. Its rank correlation with one-year realised volatility is -0.01.

The same responses already list each material risk with its own severity, and that mix does
vary: 96 distinct mixes across 191 tickers, and its average has a rank correlation of +0.12
with realised volatility (+0.16 within sector). Scoring from the mix uses data already
cached, so no API calls are needed.

## Scope
In scope:
- `risk_score` computed from the average severity of `material_risks`.
- No score when the response lists no material risks (the model did not see a usable risk
  section).

Out of scope / non-goals:
- Prompt or schema changes (option 2, only if this score proves too weak).
- Using `boilerplate_percentage`, risk counts, or `new_risks` in the score: none showed a
  relationship with realised volatility (rank correlations -0.08 to +0.10).
- Rescaling or stretching the score's range, or changing the AI composite weights.
- Fixing Item 1A extraction for tickers where it fails (CLX, EIX, MS); backlog item.
- Using the risk analysis as a trade veto.

## Assumptions
- `material_risks` severities are the model's per-risk judgement and are comparable across
  companies, at least as well as the overall label. Checked on 191 cached `_v2` results
  (2026-10-15): severities LOW 21, MEDIUM 846, HIGH 993, CRITICAL 80; none outside the four
  labels.
- A result with an empty `material_risks` list means the model saw no usable risk factors.
  All three such cached results (CLX, EIX, MS) say the section was missing or entirely
  boilerplate.

## Proposed behavior / API
### Public interface
- `analysis.risk_analyzer.risk_score(result: dict | None) -> float | None`: same signature,
  new calculation.
- Cache keys (`AnalysisCache.artifact_id_risk`) unchanged: stored results are rescored on the
  next `run_analysis.py` run without new API calls.

### Inputs / outputs
- Input: a validated risk analysis result, as stored.
- Output: `10 - 2 * mean(severity)` over the `material_risks` entries, where LOW=0, MEDIUM=1,
  HIGH=2, CRITICAL=3. Range 4 (all CRITICAL) to 10 (all LOW), the same scale as before.
- `None` when the result is `None`, or when no `material_risks` entry has a valid severity.
- Error handling: a malformed `material_risks` (not a list, entries that are not dicts) counts
  as no valid entries; no exception.

### Examples
```python
risk_score({"material_risks": [
    {"risk": "a", "severity": "HIGH"},
    {"risk": "b", "severity": "MEDIUM"},
], "risk_severity": "HIGH"})                    # 10 - 2 * 1.5 = 7.0
risk_score({"material_risks": [], "risk_severity": "LOW"})   # None
```

## Acceptance criteria
- AC1: `risk_score` returns `10 - 2 * m`, where `m` is the mean severity value (LOW=0,
  MEDIUM=1, HIGH=2, CRITICAL=3) of the `material_risks` entries with a valid severity.
- AC2: Entries whose severity is missing or not one of the four labels are left out of the
  mean (not defaulted to MEDIUM).
- AC3: `risk_score` returns `None` when the result is `None`, when `material_risks` is
  missing, empty, or not a list, or when no entry has a valid severity. The overall
  `risk_severity` label is not used as a fallback.
- AC4: When `risk_score` is `None` for a ticker that has a risk result, the AI composite
  averages the remaining analyzers and `analyzers_used` excludes risk (existing behaviour for
  `None` scores, now reachable for this case).
- AC5: Risk artifact ids are unchanged (`<ticker>_<accession>_v2`), so cached results are
  reused and rescored.

## Security considerations
- No meaningful security impact: a pure calculation change over data already stored. No new
  inputs, network calls, secrets, writes, or dependencies. Malformed model output is handled
  without exceptions (AC3).

## Edge cases
- All entries invalid: `None` (AC3).
- One valid entry: its own severity value.
- `material_risks` entries that are strings rather than dicts: ignored.
- Overall label CRITICAL but entries mostly MEDIUM: the entries decide (the label is not used).

## Test guidance
- AC1 -> `tests/test_risk_score.py::TestRiskScore::test_mean_of_severities`,
  `test_all_low_is_ten`, `test_all_critical_is_four`
- AC2 -> `tests/test_risk_score.py::TestRiskScore::test_invalid_severities_ignored`
- AC3 -> `tests/test_risk_score.py::TestRiskScore::test_none_result`, `test_empty_list`,
  `test_missing_list`, `test_not_a_list`, `test_no_valid_entries`,
  `test_label_not_used_as_fallback`
- AC4 -> `tests/test_risk_score.py::TestCompositeWithoutRisk`
- AC5 -> `tests/test_risk_score.py::TestCacheKey::test_artifact_id_unchanged`

## Decision log
- 2026-10-15: Average per-risk severity chosen over counts and shares. On 191 cached results:
  average severity gives 53 distinct scores (rank correlation with realised volatility +0.12,
  +0.16 within sector); share of HIGH/CRITICAL +0.08; count of HIGH/CRITICAL -0.02; total count
  -0.08 (counts track filing length, not risk). The overall label gives 4 values and -0.01.
- 2026-10-15: Same 4-10 scale as the old mapping, so the score reads as before and the
  composite weights are untouched. The new spread is narrower (standard deviation about 0.4,
  against 0.75 for earnings and 1.1 for filing), so risk carries less weight in the composite
  than the others. Stretching is left until the score has a track record.
- 2026-10-15: No score when no material risks are listed, rather than falling back to the
  label. Those results mean the model never saw the risk factors; scoring CLX and EIX 10
  (lowest risk) was the packet 30 failure mode again.
- 2026-10-15: Measured effect on the 2026-10-14 scores (read-only re-rank; rebuilding with the
  old risk scores reproduces all 503 stored directions): risk score mean 6.09 to 6.80,
  standard deviation 0.55 to 0.41; AI composite +0.18 on average (largest change 1.42);
  CLX, EIX and MS lose their risk score; 14 of 503 tickers change direction, no LONG/SHORT
  flips.
- 2026-10-15 (Implementer): severities that are not strings (e.g. a list) are skipped; found
  by an adversarial test, where a list raised `TypeError` (unhashable) on the map lookup.
