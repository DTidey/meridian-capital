# Test Plan: risk-management

Path: `docs/test-plans/05-risk-management.md`

## What changed
- Initial implementation of Layer 5 Risk Management. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_risk_state.py` — full pipeline wiring via entry point; verifies that invoking `run_risk_check.py` with no flags runs all six stages (factor risk model, pre-trade veto, circuit breakers, factor monitor, correlation monitor, risk state save) end-to-end.
- AC2: `tests/test_risk_pre_trade.py` — each of the 8 checks evaluated in isolation; separate test asserts closing/covering trades skip checks 2-8 and are always APPROVED; verifies `position_approvals` rows are stamped APPROVED or REJECTED.
- AC3: `tests/test_risk_pre_trade.py` — earnings blackout size-reduction test: target_shares halved, status=APPROVED, reason=`BLACKOUT_REDUCED`; full rejection path is explicitly not taken.
- AC4: `tests/test_risk_circuit_breakers.py` — each trigger threshold tested individually (daily loss 1.5%, daily loss 2.5%, weekly loss 4%, drawdown 8%); KILL_SWITCH test asserts `cache/halt.lock` is created; SIZE_DOWN_30 test asserts target_shares modified to 70%; priority ordering (drawdown before daily loss) verified.
- AC5: `tests/test_risk_tail_risk.py` — VIX >= 35 applies 50% reduction; VIX >= 25 applies 20% reduction; VIX < 25 produces no action; HYG proxy z-score >= 1.0 sigma applies REDUCE_GROSS_20; simultaneous VIX >= 35 and spread >= 1 sigma does not double-reduce.
- AC6: `tests/test_risk_factor_risk.py` — variance decomposition test: `factor_var + specific_var == total_var` to floating-point tolerance; MCTR flagging: tickers where |MCTR_pct| > 1.5 * |weight_pct| appear in `mctr_flags`; `predicted_cov` shape is NxN for N portfolio positions.
- AC7: `tests/test_risk_correlation.py` — effective N bets formula (eigendecomposition of combined correlation matrix, entropy-based); high-correlation alert fires when `avg_long_corr > 0.60` or `avg_short_corr > 0.60`.
- AC8: `tests/test_risk_factor_risk.py` — HIGH priority alert logic: factor spread |z| > 1.5 AND active crowding flag from Layer 2 produces `priority="HIGH"` in alert dict.
- AC9: `tests/test_risk_state.py` — halt.lock creation via `set_halt()`; deletion via `clear_halt()`; `is_halted()` reads file existence (not JSON); JSON round-trip: `save_risk_state` then `load_risk_state` produces identical dict.
- AC10: `tests/test_risk_pre_trade.py`, `tests/test_risk_circuit_breakers.py` — `--whatif` flag: all checks and metrics computed, no rows written to DB; `--clear-halt`: deletes `cache/halt.lock` and exits without running other stages.

## Edge cases
- From spec:
  - Closing/covering trades (target_shares ~ 0) skip checks 2-8 and always pass check 1 unless halt.lock is present.
  - KILL_SWITCH overrides SIZE_DOWN — do not apply SIZE_DOWN if KILL_SWITCH fires.
  - Earnings blackout applies a 50% size reduction rather than rejection; reason recorded as `BLACKOUT_REDUCED`.
  - VIX >= 35 dominates credit-spread action even if both conditions fire simultaneously.
  - `is_halted()` checks `cache/halt.lock` file existence, not the JSON field; they can diverge transiently.
  - `portfolio_history` with no prior data: P&L is 0, drawdown is 0 (no crash on empty history).
  - Ticker missing from yfinance stress-test data defaults to sector-average return.
  - Stress test parquet cache is considered stale if older than 30 days (triggers re-fetch).
  - FRED API key absent: system falls back silently to HYG proxy without raising an error.
- Additional adversarial cases:
  - Simultaneous KILL_SWITCH and VIX >= 35: only KILL_SWITCH action is written; tail risk does not resize already-rejected trades.
  - Pre-trade veto with all 8 checks failing for the same ticker: exactly one REJECTED row is written (checks continue to be logged, but status is set once).
  - Factor risk model with fewer than 50 stocks having both returns and factor scores on a given day: that day is skipped gracefully in the rolling OLS without raising an exception.
  - `cache/risk_state.json` is missing on first run: `load_risk_state` returns an empty/default dict and subsequent `save_risk_state` creates the file.
  - Circuit breaker weekly-loss lookback when `portfolio_history` has fewer than 5 trading days: gracefully returns 0% weekly P&L.

## Notes
- Flaky risks: External API calls (FRED, yfinance) are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures with fixed random seeds where numpy is involved in covariance estimation.
