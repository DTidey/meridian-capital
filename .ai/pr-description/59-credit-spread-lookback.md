## Summary

Tail-risk CAUTION (REDUCE_GROSS_20) fired on every run since go-live, always on the HY credit
spread z-score. Two causes: the FRED request covered 282 calendar days, so the "252-day"
window held only ~205 observations; and a 1-sigma threshold on a calm year fires on a ~20bp
widening.

- `risk/tail_risk.py` `_get_credit_spread_z_fred`: requests `ceil(lookback x 7 / 5) + 30`
  calendar days and keeps exactly `credit_lookback_days` observations on or before the score
  date; WARNING when fewer are available; INFO line with count, mean, std, latest and z
  (never the API key).
- `config.yaml`: `risk.tail_risk.credit_spread_sigma` 1.0 -> 2.0 (owner decision).
- Spec 05 AC5 and credit-spread section restated.

## Spec

- Spec: `docs/specs/59-credit-spread-lookback.md`
- Test plan: `docs/test-plans/59-credit-spread-lookback.md`
- PR draft path: `.ai/pr-description/59-credit-spread-lookback.md`

## Acceptance Criteria

- [x] AC1: z computed from exactly `credit_lookback_days` observations on or before the score date.
- [x] AC2: `observation_start` at least `ceil(lookback x 7 / 5) + 30` calendar days back.
- [x] AC3: Fewer observations than the lookback (at least 10): z computed, WARNING names the count.
- [x] AC4: Non-numeric FRED values skipped and not counted.
- [x] AC5: CAUTION by credit only at `z >= credit_spread_sigma` (2.0).
- [x] AC6: INFO line with count, mean, std, latest, z; API key never logged.
- [x] AC7: Spec 05 states the observation window and threshold.

## Security Review

- [x] Security considerations reviewed in `docs/specs/59-credit-spread-lookback.md`
- [x] Secrets or credential handling: test asserts `FRED_API_KEY` never appears in log records
- Reviewer focus: the new INFO line logs values only, not the request URL or params.

## Validation

- 11 new tests failed before the fix, all pass after (`tests/test_risk_tail_risk.py`
  `TestCreditLookback`, `TestCreditThreshold`).
- Evidence for 2.0: corrected 252-observation z for the last 30 nights (table in the spec).
  CAUTION nights: 25 at 1.0, 18 at 1.5, 13 at 2.0. The old window reproduces the logged z
  values (11-04 2.42, 10-29 1.32).
- `make lint`: pass. `make test`: pass (counts in the final phase-2 summary).

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- CAUTION still fires at 2.0 on 13 of the last 30 nights, including 11-02 to 11-04 (OAS up
  ~70bp since late September). With packet 58 that means a 120% gross book, not churn.
- One FRED request per run is unchanged; packet 58 adds a second one in Layer 4.
