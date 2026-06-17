# Test Plan: reporting-dashboard

Path: `docs/test-plans/07-reporting-dashboard.md`

## What changed
- Initial implementation of Layer 7 Reporting & Dashboard. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_reporting_nav.py` — `build_nav_series` idempotency: calling the function twice on the same fixture produces no duplicate rows in `portfolio_nav` (upsert on PK); empty `portfolio_history` returns empty DataFrame without crashing. Also covered by `tests/test_reporting_attribution.py` for `pnl_attribution.run()` idempotency, and the equivalent upsert path in `position_attribution.build_trades()`.
- AC2: `tests/test_reporting_attribution.py` — `test_beta_pnl_formula`: asserts `beta_pnl = net_beta * spy_return` to 6 decimal places; `test_alpha_residual_sums_to_total`: asserts `beta_pnl + sector_pnl + factor_pnl + alpha_pnl == portfolio_return` within floating-point tolerance of 1e-9; `test_brinson_zero_when_weights_match_benchmark`: allocation effect = 0 when portfolio sector weights equal benchmark weights.
- AC3: `tests/test_reporting_fifo.py` — `test_fifo_full_exit`: 100 shares entered then fully exited produces one closed trade with correct `realized_pnl`, `entry_date`, `exit_date`, and `holding_days`; `test_fifo_partial_exit`: 100 shares entered, 60 exited produces one closed 60-share trade and one open 40-share trade; `test_fifo_direction_flip`: long-to-short transition is treated as a close of the long followed by entry of a new short.
- AC4: Manual dashboard smoke test — page routing verified by navigating to each of the six pages (Portfolio, Research, Risk, Performance, Execution, Letter) and confirming `render()` returns without error; auto-refresh thread initialisation verified by checking `st.session_state.refresh_thread` is set on first load.
- AC5: `tests/test_reporting_commentary.py` — weekday gate: if today is not the configured weekday, `generate_if_due` returns None without calling the OpenAI API; cache hit: if `weekly_commentary` already has a row for the current week, the cached content is returned without a new API call; mock API call used on cache miss.
- AC6: `tests/test_reporting_lp_letter.py` — cache hit: second call on same date returns cached `lp_letters.content` without an API call; `force=True` calls the API and overwrites the cached row; `render_full` structure test: output string contains letterhead block, CONFIDENTIAL stamp, `Dear Limited Partners,`, JARVIS signature, and compliance footer.
- AC7: `tests/test_reporting_tearsheet.py` — section presence: output file contains all required section headings (performance vs SPY, monthly returns grid, equity curve, factor exposures, sector exposures, turnover, recent slippage); Sharpe formula: `_sharpe` helper returns `annualised_mean / annualised_std` for a known returns series; sparkline length: ASCII equity curve is exactly 80 characters.
- AC8: `tests/test_reporting_dashboard.py` or `tests/test_page_research.py` — amber warning banner fires when a held ticker has earnings within the blackout window; banner fires when an FOMC meeting is within 3 days; banner fires when monthly OpEx Friday is within 3 days; no banner shown when none of the three conditions are true.
- AC9: `tests/test_reporting_win_loss.py` or `tests/test_reporting_turnover.py` — FIFO tax bucketing: position held 300 days taxed at short-term rate 37%; position held 400 days taxed at long-term rate 20%; `tax_estimate_usd` equals `short_term_gains * 0.37 + long_term_gains * 0.20` to floating-point tolerance.
- AC10: `tests/test_reporting_win_loss.py` — `test_win_rate_all_winners`: 5 winning trades return `win_rate=1.0`; `test_win_rate_no_trades`: empty `position_trades` table returns zeroed dicts without raising a KeyError or ZeroDivisionError; `test_holding_period_bucketing`: trades at 2d, 10d, and 50d fall into the correct holding-period buckets.

## Edge cases
- From spec:
  - FIFO lot matching: direction flip (long to short) closes the long lot before opening the short.
  - FOMC meeting dates are hardcoded in `FOMC_DATES` constant; no external data source needed.
  - Monthly OpEx Friday is the third Friday of the month, computed programmatically.
  - JARVIS commentary is generated only on the configured weekday (default Friday); all other days `generate_if_due` returns None.
  - LP letter Regenerate button calls `lp_letter.generate(engine, cfg, force=True)` and overwrites the cached row for today.
  - `OPENAI_API_KEY` absence raises at first AI call, not at import time; all other reporting functions run without it.
  - Auto-refresh thread is started only once per session (guarded by `"refresh_thread" not in st.session_state`).
  - `build_nav_series` on empty `portfolio_history` returns an empty DataFrame and does not write to `portfolio_nav`.
- Additional adversarial cases:
  - `pnl_attribution.run()` called when `portfolio_nav` has rows but `daily_prices` is missing SPY data for that date: the function skips the affected date gracefully rather than raising a KeyError.
  - `win_loss.compute()` called with a `position_trades` table containing only open trades (no `exit_date`): returns zeroed overall dict without raising.
  - `slippage_stats()` called within `tear_sheet.write()` when `execution_orders` has no FILLED rows: the tear sheet section renders with zeros/N/A rather than raising.
  - `build_trades()` called when a ticker appears in `portfolio_history` with a quantity of exactly 0 (phantom row): treated as an exit, not an entry, so no open trade is created.

## Notes
- Flaky risks: OpenAI API calls (commentary, LP letter, JARVIS chat) are mocked in tests; no network dependency. Streamlit page tests that require a running Streamlit server are marked as manual smoke tests.
- Determinism considerations: All tests use deterministic fixtures with fixed snapshot dates and known NAV values; sparkline length is asserted by character count, not by content.
