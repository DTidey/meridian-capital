# Test Plan: credit-spread-lookback

Path: `docs/test-plans/59-credit-spread-lookback.md`
Spec: `docs/specs/59-credit-spread-lookback.md`
Status: planned (phase 1). The threshold in AC5 is the owner's D1 value (2.0 recommended).

## What changes (planned)
- `risk/tail_risk.py` `_get_credit_spread_z_fred`: observation-based start date, short-window
  WARNING, INFO summary line.
- `config.yaml` `credit_spread_sigma` per D1.
- Spec 05 AC5 text.

## Acceptance criteria coverage
- AC1: `tests/test_risk_tail_risk.py::TestCreditLookback::test_uses_exact_lookback_obs`
  (mock FRED returns 300 business days with a known last-252 mean/std; z equals the value
  computed from exactly the last 252)
- AC2: `TestCreditLookback::test_observation_start_covers_business_days` (captures the
  `observation_start` param; >= ceil(252 x 7 / 5) + 30 days before score_date)
- AC3: `TestCreditLookback::test_short_window_warns_and_computes` (200 obs -> z computed,
  WARNING with "200")
- AC4: `TestCreditLookback::test_missing_values_not_counted` (`"."` rows interleaved; 252
  numeric observations used)
- AC5: `tests/test_risk_tail_risk.py::TestTailRiskCaution::test_credit_threshold_boundary`
  (z = sigma -> CAUTION; z = sigma - 0.01 -> NORMAL with VIX below caution)
- AC6: `TestCreditLookback::test_info_line_without_api_key` (caplog: INFO contains count,
  mean, std, latest, z; no record contains the key string)
- AC7: documentation review of the spec 05 diff

## Edge cases
- From spec:
  - Fewer rows than requested (AC3 test)
  - `score_date` on a weekend: last observation before it is the latest
    (`TestCreditLookback::test_weekend_score_date`)
  - Latest value `"."`: previous numeric value is latest (`test_latest_missing_uses_previous`)
  - std near 0 returns 0.0 (existing behaviour; `test_flat_series_zero`)
- Additional adversarial cases:
  - Observations dated after `score_date` (FRED revision or a future-dated row) are excluded
    even when they are numeric (`test_future_observations_excluded`)
  - Unsorted FRED response (dates out of order): window still the last 252 by date
    (`test_unsorted_response_sorted`)
  - Exactly 10 numeric observations: computes; 9 raises and falls back to HYG
    (`test_minimum_ten_boundary`)
  - `credit_lookback_days` changed to 60 in config: start date and window follow the config,
    not a hard-coded 252 (`test_lookback_from_config`)

## Notes
- Flaky risks: none; `requests.get` patched, cache written under `tmp_path`.
- Determinism considerations: synthetic series with fixed values.

## Commands
```bash
make lint
make test
make security
```
