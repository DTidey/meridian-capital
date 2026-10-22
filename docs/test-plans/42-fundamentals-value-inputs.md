# Test Plan: fundamentals-value-inputs

Path: `docs/test-plans/42-fundamentals-value-inputs.md`

## What changed
- `data/db.py`: new `market_caps` table. `data/market_caps.py`: `update_market_caps` from the
  shared `.info`.
- `run_data.py`: market caps stored in step 4; `--recompute-ratios`.
- `data/fundamentals.py`: ROE = net income / equity and D/E = debt / equity, both `None` without
  positive equity; `recompute_ratios`.
- `factors/market_cap.py`: effective shares from snapshots with a 1.5x guard and a
  balance-sheet fallback. `factors/ttm.py`: TTM helper moved from the dashboard, zero for an
  omitted dividend or buyback line.
- `factors/loader.py`: `data["market_caps"]`.
- `factors/value.py`: shared market cap, TTM flows, one balance-sheet quarter, operating income.
- `factors/quality.py`: Altman Z from the shared market cap and operating income; ROE stability
  from raw figures; D/E rule for negative equity.
- `dashboard/ticker_valuation.py`: shared helpers, snapshot shares, operating income.

## Acceptance criteria coverage
- AC1: `tests/test_market_caps.py::TestUpdateMarketCaps` (stored, today's skipped, shared cache,
  bad values, failed fetch); `tests/test_run_data_args.py::test_one_info_fetch_per_ticker`,
  `test_low_coverage_run_continues`
- AC2: `tests/test_market_caps.py::TestSharesFromSnapshots`, `TestMarketCaps` (BRK-B, latest
  close, fallback, quarter without balance sheet, non-trading day, split, glitch), `TestLoader`
- AC3: `tests/test_value_inputs.py::TestMarketCapSource`
- AC4: `tests/test_value_inputs.py::TestTtm`
- AC5: `tests/test_value_inputs.py::TestShareholderYield`, `tests/test_ttm.py::TestZeroWhenOmitted`
- AC6: `tests/test_value_inputs.py::TestOperatingIncome`, `tests/test_quality.py::TestAltmanZ`,
  `tests/test_ticker_valuation.py` (fixtures now on `operating_income`)
- AC7: `tests/test_value_inputs.py::TestOneBalanceSheetQuarter`
- AC8: `tests/test_value_inputs.py::TestEquityRules` (incl. `test_omitted_debt_line_is_no_debt`,
  `test_piotroski_leverage_from_raw_figures`), `tests/test_recompute_ratios.py::TestEquityRatios`,
  `tests/test_value_inputs.py::TestEquityRules`, `tests/test_quality.py::TestRoeStability`
- AC9: `tests/test_recompute_ratios.py::TestRecomputeRatios`, `TestCli`
- AC10: `tests/test_ttm.py::TestDashboardShares`, `tests/test_ticker_valuation.py`
- AC11: `make lint`, `make test`, `make security`

## Edge cases
- From spec:
  - first snapshot used as is (`test_single_snapshot_used`);
  - no snapshot, balance-sheet fallback (`test_fallback_to_balance_sheet`);
  - snapshot on a non-trading day (`test_snapshot_on_non_trading_day_uses_earlier_close`);
  - split between snapshots (`test_split_between_snapshots_not_a_glitch`);
  - non-payer with a cash-flow statement (`test_neither_line_with_cash_flow_statement_is_zero`);
  - quarter without a cash-flow statement (`test_without_cash_flow_statement_missing`);
  - equity exactly 0 (`test_no_positive_equity_gives_none[0.0]`);
  - income-only newest quarter (`test_income_only_newest_quarter_uses_previous_balance_sheet`).
- Additional adversarial cases:
  - An unusable newest snapshot does not disable the guard (`test_unusable_snapshot_skipped`)
  - Guard boundary: exactly 1.5x accepted, just over rejected (`test_guard_boundary`)
  - NaN debt does not make EV NaN, the old `nan or 0.0` bug (`test_nan_debt_is_zero_debt`)
  - A missing cash figure is not filled from an older quarter
    (`test_missing_cash_not_taken_from_older_quarter`)
  - An omitted revenue line is missing, never zero (`test_only_cash_return_lines`)
  - A stopped dividend counts zero, not last year's (`test_stopped_dividend_counts_zero_not_last_year`)
  - An already-correct row is not rewritten, and missing figures clear the ratio
    (`test_correct_row_untouched_and_missing_figures_cleared`)
  - Glitched snapshot read by the dashboard from the database (`test_snapshot_shares_from_db_with_guard`)
  - The dashboard ignores a snapshot older than scoring's 30-day window (`test_stale_snapshot_ignored`)
  - `--recompute-ratios` with `--rebuild-insider` is rejected (`test_rejects_rebuild_insider`)

## Notes
- Existing tests changed only where the spec changes behaviour: Altman Z takes a market cap,
  ROE stability reads net income and equity, value fixtures have four quarters (TTM), EBIT
  fixtures use `operating_income`, and the low-coverage run expects the market caps step's line.
- Production dry run (read-only, 2026-10-21 data, live Yahoo `marketCap` for 18 tickers): see
  the PR description.

## Commands
```bash
make lint
make test
make security
```
