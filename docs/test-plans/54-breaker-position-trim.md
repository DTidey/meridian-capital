# Test Plan: breaker-position-trim

Path: `docs/test-plans/54-breaker-position-trim.md`

## What changed
- `config.yaml`: `risk.circuit_breakers.max_single_position_pct` 0.03 -> 0.06.
- `risk/circuit_breakers.py`: FORCE_CLOSE trims to `portfolio.max_position_pct` of NAV, rejects
  every other APPROVED trade for the ticker that night; `_load_config` raises when the breaker
  cap is below the sizing cap.
- `tests/test_risk_circuit_breakers.py`: `TestCircuitBreakerForceClose` rewritten; whatif trim
  test added; `_base_config` carries `portfolio.max_position_pct`.

## Acceptance criteria coverage
- AC1: `TestCircuitBreakerForceClose::test_force_close_trims_long`
- AC2: `TestCircuitBreakerForceClose::test_force_close_trims_short`
- AC3: `TestCircuitBreakerForceClose::test_trim_rejects_other_trades_for_ticker`
- AC4: `TestCircuitBreakerForceClose::test_cap_below_sizing_cap_raises`
- AC5: `TestCircuitBreakerForceClose::test_production_config_caps_consistent`
- AC6: `TestCircuitBreakerForceClose::test_position_at_cap_not_trimmed`,
  `TestCircuitBreakerWhatIf::test_whatif_trim_no_db_changes`

## Edge cases
- From spec: one share above the sizing cap (full close), position exactly at the cap, short
  sign, non-APPROVED rows.
- Additional adversarial cases:
  - Rebalance COVER and SHORT rows for the same ticker both rejected (the 2026-10-28 CLX
    duplicate) while another ticker's BUY stays APPROVED
    (`test_trim_rejects_other_trades_for_ticker`)
  - A PENDING row for the ticker keeps its status (`test_non_approved_rows_left_alone`)
  - One share worth 7% of NAV gives target 0, delta -1 (`test_share_above_sizing_cap_closes_fully`)
  - Misconfigured cap raises before any approval row is written
    (`test_cap_below_sizing_cap_raises`)

## Notes
- AC1-AC5 tests fail on `main` (full close / opening-only reject / no config check / cap 0.03).

## Commands
```bash
make lint
make test
make security
```
