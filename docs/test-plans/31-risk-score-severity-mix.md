# Test Plan: risk-score-severity-mix

Path: `docs/test-plans/31-risk-score-severity-mix.md`

## What changed
- `analysis/risk_analyzer.py`: `risk_score` is `10 - 2 * mean` of the per-risk severities in
  `material_risks`, or `None` when there are none; the unused label-to-score map is removed.
- `tests/test_combined_score.py`: two composite tests now pass risk results in the
  `material_risks` shape (their expected composites are unchanged).

## Acceptance criteria coverage
- AC1: `tests/test_risk_score.py::TestRiskScore::test_mean_of_severities`,
  `test_all_low_is_ten`, `test_all_critical_is_four`
- AC2: `TestRiskScore::test_invalid_severities_ignored`
- AC3: `TestRiskScore::test_none_result`, `test_empty_list`, `test_missing_list`,
  `test_not_a_list`, `test_no_valid_entries`, `test_label_not_used_as_fallback`
- AC4: `TestCompositeWithoutRisk::test_empty_risks_excluded_from_composite`
- AC5: `TestCacheKey::test_artifact_id_unchanged`

## Edge cases
- From spec: all entries invalid, a single entry, string entries, label disagreeing with the
  entries.
- Additional adversarial cases:
  - Entries of mixed types (strings, ints, lists) alongside one valid dict
  - A severity that is a list (unhashable): skipped, not a `TypeError`
  - 51 entries stay within the 4-10 range

## Notes
- Live check (2026-10-15, read-only against the 191 cached `_v2` results and the 2026-10-14
  scores): see the spec's decision log.

## Commands
```bash
make lint
make test
make security
```
