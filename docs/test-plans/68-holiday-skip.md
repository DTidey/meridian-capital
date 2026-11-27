# Test Plan: holiday-skip

Path: `docs/test-plans/68-holiday-skip.md`

## What changed
- `execution/market_calendar.py` (new): `closed_reason(day)` returns weekend / holiday / None
  (trading day) and raises `CalendarUnavailable` on any lookup failure.
- `factors/score_date.py` (new): `resolve_score_date`, now used by `run_scoring.py` and
  `run_all.py`.
- `run_all.py`: market-day check before Layer 1, `--ignore-market-calendar`, loads `.env`.
- `docs/run_all.md`: skip behaviour and the flag.
- Tests: `tests/test_market_calendar.py`, `tests/test_score_date.py`,
  `tests/test_run_all_market_calendar.py`; `tests/test_run_all.py` gets an autouse fixture
  that keeps the check offline (every day open).

All tests use fake Alpaca clients; none touches the network or a real database.

## Acceptance criteria coverage
- AC1: `test_run_all_market_calendar.py::test_holiday_skips_with_exit_0`;
  `test_market_calendar.py::test_no_entry_is_holiday`
- AC2: `test_run_all_market_calendar.py::test_weekend_skips_without_client`;
  `test_market_calendar.py::test_weekend_needs_no_client` (Saturday and Sunday)
- AC3: `test_run_all_market_calendar.py::test_trading_day_runs`;
  `test_market_calendar.py::test_trading_day_is_open`
- AC4: `test_run_all_market_calendar.py::test_half_day_runs`;
  `test_market_calendar.py::test_half_day_is_open`
- AC5: `test_run_all_market_calendar.py::test_calendar_error_runs_with_warning` (network error,
  missing keys); `test_market_calendar.py::test_call_error_is_unavailable`,
  `test_missing_keys_is_unavailable`
- AC6: `test_run_all_market_calendar.py::test_override_runs_on_holiday` (no calendar request)
- AC7: `test_score_date.py` (precedence CLI > config > today;
  `test_run_all_and_layer2_share_the_function`; `test_layer2_scores_the_resolved_date`);
  `test_run_all_market_calendar.py::test_checks_the_layer2_score_date`
- AC8: `test_scoring_freshness.py` unchanged and passing (`test_stale_spy_stops_scoring` uses
  the 2026-11-25 / 2026-11-26 case)
- AC9: `test_run_all_market_calendar.py::test_run_all_doc_mentions_override`

## Edge cases
- From spec:
  - `scoring.score_date` set (backfill): `test_checks_the_layer2_score_date`
  - Missing Alpaca keys: `test_calendar_error_runs_with_warning[...ALPACA_API_KEY...]`
  - Response listing only other dates: `test_only_other_dates_is_unavailable`,
    `test_response_for_other_dates_fails_open`
  - `--whatif` / `--tickers` on a holiday: `test_whatif_and_tickers_skipped_on_holiday`
- Additional adversarial cases:
  - Malformed `scoring.score_date` (`26/11/2026`) fails open, not into a skip:
    `test_bad_config_score_date_fails_open`
  - Unexpected exception type from the client (RuntimeError) fails open:
    `test_unexpected_exception_type_fails_open`
  - Raw dict response (client in raw-data mode), `None` response, entry without `date`: all
    `CalendarUnavailable`, never "holiday" (`test_raw_dict_response_is_unavailable`,
    `test_none_response_is_unavailable`, `test_entry_without_date_is_unavailable`)
  - Default client factory resolves `execution.broker.get_client` at call time:
    `test_default_factory_is_broker_get_client`

## Notes
- Flaky risks: none; dates are fixed through config or a fake `date` class.
- Determinism considerations: the existing `tests/test_run_all.py` would otherwise depend on
  the weekday the suite runs and on the network; its autouse fixture stubs the check.
- Checked before the fix: the three new test files fail at collection (modules missing).

## Commands
```bash
make lint
make test
make security
```
