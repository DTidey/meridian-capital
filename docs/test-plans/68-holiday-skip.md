# Test Plan: holiday-skip

Path: `docs/test-plans/68-holiday-skip.md`

## What changed
- `execution/market_calendar.py` (new): `closed_reason(day)` returns weekend / holiday / None
  (trading day) and raises `CalendarUnavailable` on any lookup failure. Review fix: the
  lookup's own client gets a `requests.Session` with (5 s, 10 s) timeouts.
- `factors/score_date.py` (new): `resolve_score_date`, now used by `run_scoring.py` and
  `run_all.py`.
- `run_all.py`: market-day check before Layer 1, `--ignore-market-calendar`, loads `.env`.
  Review fix: resolves the score date once and passes it with `--date` to Layers 2-6 and
  Book sync.
- `docs/run_all.md`: skip behaviour and the flag.
- Tests: `tests/test_market_calendar.py`, `tests/test_score_date.py`,
  `tests/test_run_all_market_calendar.py`; `tests/test_run_all.py` gets an autouse fixture
  that keeps the check offline (every day open).

All tests use fake Alpaca clients or a real `TradingClient` pointed at a localhost socket;
none touches an external network or a real database.

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
  `test_missing_keys_is_unavailable`; stalled calendar end to end:
  `test_run_all_market_calendar.py::test_stalled_calendar_warns_and_runs` (WARNING with
  `Timeout`, Layer 1 runs, exit 0)
- AC6: `test_run_all_market_calendar.py::test_override_runs_on_holiday` (no calendar request)
- AC7: `test_score_date.py` (precedence CLI > config > today;
  `test_run_all_and_layer2_share_the_function`; `test_layer2_scores_the_resolved_date`);
  `test_run_all_market_calendar.py::test_checks_the_layer2_score_date`
- AC8: `test_scoring_freshness.py` unchanged and passing (`test_stale_spy_stops_scoring` uses
  the 2026-11-25 / 2026-11-26 case)
- AC9: `test_run_all_market_calendar.py::test_run_all_doc_mentions_override`
- AC10: `test_market_calendar.py`:
  - `test_timeout_is_finite_and_short`: connect <= 10 s, read <= 30 s, both > 0
  - `test_dedicated_session_carries_the_timeout`: the lookup's client ends with a
    `_TimeoutSession` holding `CALENDAR_TIMEOUT`
  - `test_session_passes_timeout_to_requests`: the session hands `timeout` to requests
  - `test_real_sdk_client_uses_the_timeout`: a real alpaca-py `TradingClient` sends the
    calendar GET with `timeout=CALENDAR_TIMEOUT` (proves the SDK path uses `_session`)
  - `test_stalled_calendar_times_out`: real client against a localhost socket that never
    answers raises `CalendarUnavailable` (`ReadTimeout`) in under 5 s with a 0.5 s timeout
  - `test_client_without_session_is_unavailable`: a client we cannot bound is never called
- AC11: `test_run_all_market_calendar.py`:
  - `test_midnight_crossing_layer2_gets_checked_date`: `date.today()` returns 11-26 then
    11-27; the calendar checks 11-26, Layer 2 gets `--date 2026-11-26`, the clock is read once
  - `test_every_dated_layer_gets_the_one_score_date`: Layers 2-6 and Book sync get the same
    `--date`; Layers 1 and 7 get none
  - `test_whatif_status_gets_the_score_date`, `test_override_passes_the_same_date`

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
- Review fixes checked the same way: against commit ac79ffd the 11 new AC10/AC11 tests fail;
  with the fix they pass. Separately, an unbounded alpaca-py `get_calendar` against a silent
  localhost socket was still blocked after 8 s.
- The stalled-socket tests rely on the listen backlog accepting the TCP connection without an
  `accept()`; the 0.5 s timeout keeps them fast.

## Commands
```bash
make lint
make test
make security
```
