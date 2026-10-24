# NAV From the Broker

**Spec file:** `docs/specs/50-nav-from-broker.md`
**Spec slug:** nav-from-broker
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit item C11 (`docs/data-audit-2026-10-21.md`): NAV is never marked to market, so the
circuit breakers cannot fire.

- `reporting/nav_series.py` `build_nav_series` writes `portfolio_nav` every night as the fixed
  config `portfolio.nav_usd` plus the sum of `portfolio_history.unrealized_pnl`. Realised P&L,
  cash and costs never enter it, and before packet 44 the history P&L was always 0.
- `risk/circuit_breakers.py` `_compute_pnl` rebuilds NAV the same way and divides daily and
  weekly P&L by the config `nav_usd`, not by the previous NAV.
- Sizing (optimisers, rebalance turnover budget), pre-trade checks and stress tests all use the
  fixed config `nav_usd`.
- `reporting/turnover.py` sums day-to-day changes in each ticker's `market_value` from
  `portfolio_history`; since packet 44 writes a snapshot every night, price drift now counts as
  turnover (backlog, packet 44 review).

Goal: one NAV series, Alpaca account equity recorded each night, and everything that needs NAV
reads it. Third of three packets agreed on 2026-10-24: 44 (book of record), 49 (orders), 50.

## Scope
In scope:
- `execution/book_sync.py` `sync_book`: also upserts `portfolio_nav` (`date = score_date`,
  `nav` = Alpaca equity) in the same transaction.
- `reporting/nav_series.py` `build_nav_series`: no longer computes NAV; fills `spy_close` and
  `drawdown_pct` (against the running peak) for the recorded rows.
- `risk/circuit_breakers.py`: daily and weekly P&L and drawdown from `portfolio_nav`.
- `portfolio/state.py` `get_nav`: latest recorded NAV on or before the score date, falling back
  to config `nav_usd`; `run_portfolio.py`, `run_risk_check.py` use it (sizing, pre-trade,
  circuit breakers, stress test).
- `reporting/turnover.py`: turnover from filled `execution_orders` value.
- `run_reporting.py`: `build_nav_series` call updated.
- Docs: spec 05/07 notes, audit C11 fix line, backlog (turnover item removed), CHANGELOG,
  decision log.

Out of scope / non-goals:
- Execution metrics that count only `FILLED` orders (backlog item; separate packet).
- FIFO position attribution with signed shorts (backlog).
- Deposits and withdrawals: equity changes from cash flows would read as P&L. Paper account
  only; noted as an assumption.
- Circuit-breaker thresholds and actions.

## Assumptions
- Alpaca `account.equity` is cash plus the market value of positions, so it includes realised
  and unrealised P&L and costs. No external cash flows on the paper account.
- The nightly book sync runs after the US close, so equity is the day's closing value.
- `portfolio_nav` keeps its columns (`date`, `nav`, `spy_close`, `drawdown_pct`,
  `computed_at`). The existing 2026-05-21 row (100,000) matches the paper account's equity and
  is kept.
- Turnover = traded value / 2 / average NAV over the window, as today, with traded value =
  `filled_shares * avg_fill_price` of every order with a fill, by `rebalance_date`.

## Proposed behavior / API
### Public interface
- `sync_book(conn, client, score_date, dry_run=False)`: unchanged signature; the summary also
  carries `"equity"`.
- `build_nav_series(engine) -> DataFrame` (the `nav_usd` argument is removed).
- `portfolio.state.get_nav(config, conn=None, score_date=None) -> float`.
- `portfolio.state.with_nav(config, nav) -> dict`: a copy of config with `portfolio.nav_usd`
  set, for code that reads NAV from config.
- `run_circuit_breakers(conn, score_date, nav_usd, ...)`: same signature; `nav_usd` is the
  current NAV used for position-size checks.

### Inputs / outputs
- Circuit breakers, for `score_date`:
  - today = `portfolio_nav.nav` on `score_date`; with no row, all P&L figures are 0, a WARNING is
    logged and no trigger fires;
  - daily P&L % = today / latest NAV before `score_date` - 1 (0 with no earlier row);
  - weekly P&L % = today / latest NAV on or before `score_date` - 7 days - 1 (the daily figure
    when no such row exists);
  - peak = highest recorded NAV on or before `score_date`; drawdown = (peak - today) / peak.
  - `risk_state` records `nav_usd` = today, `daily_pnl_usd` = today - previous.
- Error handling: unchanged elsewhere; a failed equity read already fails the sync.

## Acceptance criteria
- AC1: `sync_book` (not dry run) upserts `portfolio_nav` for `score_date` with `nav` = Alpaca
  account equity, in the same transaction as the positions; a re-sync replaces it; a dry run
  writes nothing.
- AC2: `build_nav_series` never changes `nav`; it sets `drawdown_pct` from the running peak of
  recorded NAVs and `spy_close` from `daily_prices`.
- AC3: Circuit breakers compute daily and weekly P&L as returns on the previous recorded NAVs and
  drawdown from the highest recorded NAV; a losing day moves drawdown, and a large enough loss
  fires the existing triggers. No row for `score_date` means no trigger and a warning.
- AC4: `get_nav` returns the latest recorded NAV on or before `score_date`, or config `nav_usd`
  when none; `run_portfolio.py` and `run_risk_check.py` size and check with it.
- AC5: Turnover is filled order value / 2 / average NAV over 30 and 90 days; price drift between
  snapshots does not count.

## Security considerations
- No new inputs, credentials or network calls: equity is already read by `sync_book`.
- Risk control: circuit breakers now see real P&L, so they can halt or cut trading as designed;
  with no NAV recorded for the day they fail open (no trigger) and log a warning, as before.
- Data integrity: the NAV row is written in the book-sync transaction, so positions and NAV
  never disagree about the date.

## Edge cases
- First recorded day: daily and weekly P&L 0, drawdown 0.
- A missing night (sync failed): the next day's daily P&L is measured against the last recorded
  NAV, so a two-day move counts as one day.
- Zero or negative equity: NAV recorded as is; the breakers use it (a wiped-out account shows
  100% drawdown and trips the kill switch); `get_nav` falls back to config when <= 0.
- A manual re-sync after reporting keeps that date's `spy_close` and `drawdown_pct`: only `nav`
  and `computed_at` are upserted.
- Partial fills on `CANCELLED` orders count toward turnover (filled value, not status).

## Test guidance
- AC1 -> `tests/test_book_sync.py::TestSyncNav::test_equity_recorded_as_nav`,
  `test_flat_book_still_records_nav`, `test_resync_replaces_nav`, `test_dry_run_records_no_nav`,
  `test_failed_commit_records_no_nav`, `test_resync_keeps_reporting_columns`
- AC2 -> `tests/test_reporting_nav.py::test_recorded_nav_never_changed`,
  `test_drawdown_calculation`, `test_spy_close_filled`, `test_no_rows_added`
- AC3 -> `tests/test_risk_circuit_breakers.py::TestNavFromBroker::test_losing_day_moves_drawdown`,
  `test_returns_on_previous_nav_not_config`, `test_weekly_against_nav_seven_days_back`,
  `test_weekly_falls_back_to_daily`, `test_first_recorded_day_is_flat`,
  `test_no_nav_today_warns_and_does_not_trigger`, `test_weekly_uses_nav_exactly_seven_days_back`,
  `test_wiped_out_account_trips_kill_switch`, and the trigger tests (size down, close all,
  kill switch, what-if) rewritten on `portfolio_nav`
- AC4 -> `tests/test_state.py::TestRecordedNav::test_latest_recorded_nav`,
  `test_on_or_before_score_date`, `test_falls_back_to_config`, `test_non_positive_nav_ignored`,
  `test_with_nav_copies_config`; runner wiring (`run_portfolio.py`, `run_risk_check.py`) by
  review and the production read-only check
- AC5 -> `tests/test_reporting_turnover.py::test_turnover_from_fills`,
  `test_partial_fill_on_cancelled_order_counts`, `test_unfilled_orders_do_not_count`,
  `test_price_drift_is_not_turnover`

## Decision log
- 2026-10-24: Alpaca equity is NAV, recorded by the nightly book sync (agreed in the 44/49/50
  split). It already includes realised P&L, costs and cash, which no history sum can.
- 2026-10-24: Returns on the previous NAV, not on the fixed config NAV, so the breakers scale
  with the fund.
- 2026-10-24: Sizing uses the recorded NAV too; config `nav_usd` stays as the fallback before
  the first sync (both are $100,000 today, so nothing moves on merge).
- 2026-10-24: Turnover from fills, because packet 44's nightly snapshots made the
  market-value-diff method count price drift.
