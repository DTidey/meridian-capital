# Test Plan: layer5-target-cuts

Path: `docs/test-plans/58-layer5-target-cuts.md`
Spec: `docs/specs/58-layer5-target-cuts.md`
Status: implemented with the approved options (D1-A gross scale in Layer 4, D2-A Layer 4 owns
earnings, D3 packet 56's assumption superseded).

## What changed
- `risk/tail_risk.py`: `evaluate_tail_risk` (read-only); `_apply_reduce_gross` removed.
- `run_portfolio.py` `_tail_risk_config`: regime evaluated before optimising; gross targets
  scaled with `portfolio.book_targets.scale_book_targets`.
- `risk/pre_trade.py`: check 2 no longer resizes; forward-window `EARNINGS_WINDOW` tag.
- Specs 04, 05, 56 and `docs/backlog.md` text.

## Acceptance criteria coverage
- AC1: `tests/test_risk_tail_risk.py::TestEvaluateTailRisk::test_matches_run_tail_risk`
  (parametrised: NORMAL, CAUTION by VIX, CAUTION by credit z, STRESS);
  `test_writes_nothing` (row counts of `position_approvals` and event tables unchanged)
- AC2: `tests/test_run_portfolio.py::TestTailRiskGrossScale::test_normal_full_gross`,
  `test_caution_scales_book` (sum |w| = 1.20, long 0.64, short 0.56),
  `test_stress_scales_book` (0.75; 0.40 / 0.35), `test_layer4_uses_regime` (wiring and printed
  line), `test_input_config_not_mutated`
- AC3: `tests/test_risk_tail_risk.py::TestNoApprovalEdits::test_regime_leaves_rows` (CAUTION
  and STRESS; the regime event is still recorded). Rewritten:
  `TestTailRiskCaution::test_vix_caution_flags_20pct`,
  `TestTailRiskStress::test_vix_stress_flags_50pct`. Removed: `TestReduceGrossAction` (pinned
  packet 56's flips). `TestTailRiskClosingTrades` kept (still true).
- AC4: `TestTailRiskGrossScale::test_second_night_no_churn` (night 1 approvals applied to the
  book; night 2 on identical inputs -> all HOLD)
- AC5: `tests/test_layer5_regression_2026_11_04.py::test_planned_actions_survive_layer5`
  (the 11-04 rows for NKE, CLX, AKAM, CF, AIZ, AXON, COHR with their earnings dates under
  CAUTION: final action and delta equal Layer 4's)
- AC6: `tests/test_risk_pre_trade.py::TestEarningsWindow::test_no_resize_reason_tagged`,
  `test_window_name_still_size_checked` (a blackout name above `max_position_pct` is now
  REJECTED, previously approved by the early return),
  `test_reported_yesterday_not_tagged`, `test_earnings_after_window_not_tagged`,
  `test_add_to_held_not_flipped`, `test_short_add_not_flipped`. Removed:
  `TestPreTradeEarningsBlackout`, `TestBlackoutResizeAction`
- AC7: existing `tests/test_optimizer.py` earnings haircut tests unchanged
- AC8: `TestTailRiskGrossScale::test_whatif_no_writes`
- AC9: documentation review of specs 04/05/56 and backlog diffs

## Edge cases
- From spec:
  - STRESS gross target (`TestTailRiskGrossScale::test_stress_scales_book`; the reductions
    themselves are `generate_trades` diffs against the smaller target)
  - Closing trade for a name in the earnings window: approved, untagged checks skipped as
    before (`TestEarningsWindow::test_closing_trade_unaffected`)
  - Earnings on `score_date` itself is inside the window
    (`TestEarningsWindow::test_earnings_today_tagged`)
- Additional adversarial cases:
  - Scaled book must still pass `book_targets` validation: with `net_exposure_min: 0.06` and
    targets 0.80/0.70 (net 0.10), STRESS scales net to 0.05 and must raise a clear ValueError
    instead of silently building an out-of-bounds book
    (`TestTailRiskGrossScale::test_scaled_book_still_validated`)
  - Credit-z fetch raises: both entry points fall back to HYG the same way
    (`TestEvaluateTailRisk::test_fred_failure_falls_back`)
  - FRED_API_KEY value never appears in log output of the regime evaluation
    (`TestEvaluateTailRisk::test_api_key_not_logged`)
  - Scale outside (0, 1] refused (`TestTailRiskGrossScale::test_scale_out_of_range_refused`)
  - An APPROVED BUY written by Layer 4 with a delta that would round to 0 after any scaling:
    Layer 5 leaves it untouched (no 0-share row created)
    (`TestNoApprovalEdits::test_small_delta_untouched`)
  - Regime NORMAL in Layer 4 but CAUTION in Layer 5 (data changes between the two): Layer 5
    still edits nothing (`TestNoApprovalEdits::test_layer5_regime_differs_no_edit`)

## Notes
- Flaky risks: FRED is mocked (`requests.get` patched); no network.
- Determinism considerations: optimiser outputs are compared with `pytest.approx`.
- The 11-04 regression uses hand-built approval rows from the investigation table, not the
  production database.

## Commands
```bash
make lint
make test
make security
```
