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
