## Summary

The 2026-10-21 data audit found `daily_prices` wrong in ways that change trades: splits after
the initial load never reached older rows (APH's 2:1 split reads as a 50% loss; APH, CVNA, KLAC
and CRWD scored SHORT on it), `adj_close` was dividend-adjusted only up to the load date, 4,194
all-null rows shifted lookbacks, the nightly run never stored that day's close (yfinance `end`
is exclusive), and `volume` was 32-bit.

- **Empty rows:** a row with no open, high, low or close is never stored (any backend).
- **Day's close:** download `end` is tomorrow from 16:30 New York time, so the 22:00 UTC run
  stores the day's bar; before 16:30 the in-progress session is excluded.
- **Adjustment check:** each incremental download already overlaps the last stored date; if the
  provider's close or adj_close there differs by more than 0.01%, the ticker's full history is
  re-downloaded (about 10 tickers a night expected, from dividends).
- **Gap check:** a ticker with fewer rows than trading-calendar dates (dates where at least half
  of stored tickers have a close) between its first and last bar is re-downloaded.
- **Full re-download:** delete and insert in one transaction (rollback on error); an empty fetch
  keeps the existing rows with a WARNING. Rows older than the 3-year lookback are removed.
- **`run_data.py --refresh-prices`:** re-downloads every ticker.
- **Volume:** `BigInteger`; `initialise_schema` runs `ALTER ... TYPE BIGINT` on PostgreSQL only
  when `information_schema` says `integer`.
- **Freshness gate:** `run_scoring.py` exits 1 before loading or writing anything unless SPY
  has a non-null close dated `score_date` (latest bar on or before `score_date`). On a market
  holiday this stops scoring and `run_all.py` stops, as the spec intends.
- **Logging:** `Prices: N full re-downloads (a adjustment, g gap), d empty rows dropped`, plus
  one line per re-downloaded ticker naming the reason.

## Spec

- Spec: `docs/specs/38-price-history-integrity.md`
- Test plan: `docs/test-plans/38-price-history-integrity.md`
- PR draft path: `.ai/pr-description/38-price-history-integrity.md`

## Acceptance Criteria

- [x] AC1: `_upsert_prices` stores no all-null OHLC row and returns the number stored.
- [x] AC2: Download end is today + 1 at or after 16:30 New York time, today before.
- [x] AC3: Close or adj_close change above 0.01% on the last stored date re-downloads the full
  history, replacing old rows (including rows absent from the new download).
- [x] AC4: A change of 0.01% or less does not re-download.
- [x] AC5: A ticker missing a calendar date between its first and last rows is re-downloaded; a
  complete one is not.
- [x] AC6: An empty full re-download leaves the ticker's rows unchanged.
- [x] AC7: `update_prices(..., refresh=True)` and `run_data.py --refresh-prices` re-download
  every ticker.
- [x] AC8: `volume` stores values above 2^31 - 1; the PostgreSQL migration is a no-op on
  `bigint`.
- [x] AC9: `run_scoring.py` exits non-zero without writing scores when SPY has no non-null bar
  dated `score_date`, and runs normally when it does.
- [x] AC10: `make lint`, `make test`, `make security` pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/38-price-history-integrity.md` (no
  meaningful impact: fixed-text DDL with no input; delete and insert share one transaction and
  an empty fetch never deletes, both tested; a few more yfinance calls a night)

## Validation

- New tests fail against `main` (import of `_download_end`, four freshness-gate tests, both
  flag tests).
- yfinance replaced in tests by an in-memory fake with yfinance's exclusive `end`; New York and
  UTC clocks patched; SQLite only.
- `make lint`: pass
- `make security`: pass (bandit clean; pip-audit no known vulnerabilities, 8 pillow ignored)
- `make test`: 1113 passed (1069 before)

## Post-merge production step (owner's confirmation required)

Run once, after merge, only with the owner's go-ahead:

```bash
python run_data.py --no-filings --no-13f --refresh-prices   # rebuild every ticker's history
python run_scoring.py                                        # rescore on corrected prices
```

The first command also widens `volume` to `BIGINT` (via `initialise_schema`) if no other entry
point has done so first, and removes the 4,194 pre-packet all-null rows.

## Risks and follow-ups

- **Volume migration locks the table.** `ALTER COLUMN TYPE BIGINT` rewrites about 460k rows
  under an exclusive lock; the first entry point to start after deploy (including the
  dashboard) runs it. Expected to take seconds; readers wait meanwhile.
- **Refresh is a delete-and-replace per ticker on production.** A ticker whose download fails
  keeps its old rows (WARNING in the log); check the log for these after the backfill.
- **Holiday runs stop at Layer 2.** `run_all.py` reports a Layer 2 failure on market holidays
  and runs no later layers that night. Intended (spec), but it will look like a failure.
- **Gap check counts rows.** A ticker with a stray row on a non-calendar date and a missing
  calendar date nets out and is not flagged; unlikely once all-null rows are gone.
- **Calendar denominator is every stored ticker,** including any no longer in the universe. If
  many stale tickers are stored, fewer dates reach the half threshold and gaps are missed.
- **Polygon backend** (not in use) gets the same checks, but stores adjusted close as both close
  and adj_close and treats `end` as inclusive; logged in `docs/backlog.md`.
