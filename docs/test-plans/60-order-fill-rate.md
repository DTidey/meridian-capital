# Test Plan: order-fill-rate

Path: `docs/test-plans/60-order-fill-rate.md`
Spec: `docs/specs/60-order-fill-rate.md`
Status: implemented with the approved options D1-A (DAY limits, 1.5% band) and D2-A (no
explicit roll-forward).

## What changed
- `config.yaml` `execution.limit_slippage_pct: 0.015` and comment.
- `execution/executor.py`: `_limit_band` (range check), `_limit_price` default 1.5%; the
  check runs first in `execute_approvals`.
- Spec 06 AC2 and packet 49 note.

## Acceptance criteria coverage
- AC1: `tests/test_execution_executor.py::TestLimitBand::test_buy_cover_above_close`,
  `test_sell_short_below_close` (close 100.00 -> 101.50 / 98.50; KIM 24.99 -> 25.36),
  `test_config_band_is_one_and_a_half_pct`
- AC2: `TestLimitBand::test_band_read_from_config` (0.005 reproduces the old 25.11 for KIM,
  0.015 gives 25.36; DAY time in force); existing
  `TestSubmission::test_limit_from_close_for_score_date`
- AC3: existing no-close skip test in `tests/test_execution_executor.py` (unchanged)
- AC4: documentation review of config and spec diffs
- AC5: `TestLimitBand::test_band_out_of_range_refused` (0, 0.0, -0.01, 0.051, 0.15, "0.015",
  None -> ValueError naming `limit_slippage_pct`; no `submit_order` call)

## Edge cases
- From spec:
  - Gap larger than the band: no code path; covered by paper-trading verification
  - Partial fill then expiry: existing order-sync tests unchanged
- Additional adversarial cases:
  - Band at the upper bound 0.05 is accepted (boundary inclusive)
    (`TestLimitBand::test_band_upper_bound_accepted`)
  - Band given as a string in YAML (`"0.015"`) or null: refused with the same error rather
    than compared lexically or crashing mid-run (cases of `test_band_out_of_range_refused`)
  - Low-priced name where rounding to cents matters: close 4.99 x 1.015 = 5.06485 -> 5.06,
    never below close for a BUY or above close for a SELL
    (`TestLimitBand::test_rounding_never_crosses_close`)
  - Validation runs before the open-order listing and before any approval is read, so a bad
    band fails without touching Alpaca (`TestLimitBand::test_validation_before_alpaca_calls`)

## Notes
- Flaky risks: none; Alpaca client mocked.
- Determinism considerations: limits rounded with Python `round(x, 2)`; tests choose values
  away from half-cent ties except the explicit rounding case.
- The fill-rate effect can only be verified in paper trading (spec "Verify in paper trading").

## Commands
```bash
make lint
make test
make security
```
