# Full daily run (after market close)
python run_all.py --no-filings --no-13f

# Full run including SEC filings (weekly)
python run_all.py

# Preview everything without committing or trading
python run_all.py --whatif --no-filings --no-13f

# Preview run that also fetches new Form 4 insider filings (Layer 1 --forms passthrough)
python run_all.py --whatif --no-13f --forms 4

# Fast test on a few tickers, skip execution and reporting
python run_all.py --tickers AAPL MSFT NVDA --no-execution --no-reporting

# Full run with stress tests, dry-run on Alpaca
python run_all.py --no-filings --no-13f --stress --dry-run

# Market holidays (packet 68): before Layer 1, run_all checks the score date (the date Layer 2
# scores: scoring.score_date in config.yaml, else today). Weekends are closed; weekdays are
# checked against Alpaca's trading calendar (one call, existing ALPACA_* keys). On a closed day
# it prints one line and exits 0 without running any layer:
#   Market closed on 2026-11-26 (holiday): skipping the nightly run
# Early-close half-days are trading days and run normally. If the calendar lookup fails it
# prints "WARNING: market calendar check failed for <date> (<error>): running the pipeline"
# and runs; the Layer 2 SPY freshness guard still stops a real holiday. The calendar call has
# socket timeouts of 5 s (connect) / 10 s (read); they do not bound DNS lookup or a response
# that keeps trickling.
# The score date is resolved once and passed with --date to Layers 2-6 and Book sync, so a run
# that passes midnight scores the date that was checked.

# Today only: if scoring.score_date in config.yaml is not today's date, run_all stops before any
# layer (ERROR, "Pipeline failed at: Score date check", exit 1), with any flags, including
# --no-execution and --whatif. Its layers write live state that is not keyed by the score
# date (Layer 5: cache/halt.lock and cache/risk_state.json; Layer 4: PENDING approvals from
# today's book; Book sync / Layer 6: the broker book, NAV and orders). Keep
# scoring.score_date null. There is no supported historical run_all (see docs/backlog.md).

# Force a run on a day the calendar says is closed
python run_all.py --no-filings --no-13f --ignore-market-calendar
