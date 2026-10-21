# Changelog

This file records intentionally published project milestones.

Release policy:
- Development packet numbers like `01-data-ingestion` are not release numbers.
- Release versions use `MAJOR.MINOR.PATCH` formatting such as `0.1.0`.
- A new release section is created only when explicitly requested.
- Changes can accumulate under `Unreleased` until a release is intentionally cut.

## Unreleased

### Added
- Reasoning-model support for LLM calls (GPT-5/GPT-6, o-series): no temperature, extra output
  headroom, configurable effort; model-aware analysis cache; GPT-6 prices (packet
  `28-reasoning-model-support`).
- Ticker page valuation (packet `21-ticker-valuation`): market cap, enterprise value, P/E,
  EV/EBIT, price/book, FCF yield and dividend yield from the latest price and trailing four
  quarters.
- Ticker page filing viewer (packet `20-filing-viewer`): read any recent 10-K, 10-Q or 8-K,
  converted from the EDGAR original with real paragraphs and tables, with an optional gpt-4o-mini
  summary that is saved, costed and checked for figures not in the filing.
- Seven-layer pipeline: data ingestion, factor scoring, AI analysis, portfolio
  construction, risk management, order execution, reporting and dashboard.
- Initial spec-first workflow integration: specs `01`–`07` document all seven layers
  with acceptance criteria, companion test plans, and PR drafts.
- Ticker page shows institutional holdings, the combined signal and direction, factor
  sub-scores, AI analysis write-ups, SEC filings and the latest earnings release, plus estimate
  and short-interest history and insider cluster flags (packet `18-ticker-page-additions`).
- Selectable LLM provider (`llm.provider` in `config.yaml`): OpenAI direct or OpenRouter.
  All LLM call sites share one client factory; cost tracking uses OpenRouter's reported cost
  when present (packet `09-openrouter-provider`).
- `LLM_PROVIDER` env var overrides `llm.provider`, so a machine can switch provider via `.env`
  without editing `config.yaml` (packet `12-llm-provider-env`).

### Changed
- Code fallbacks for LLM models now default to GPT-6 via `analysis.llm.DEFAULT_MODEL` /
  `DEFAULT_MODEL_CHEAP`, kept equal to `config.yaml` by a test (packet `35-model-defaults`).
- Scoring analyzers moved to GPT-6 after a 50-ticker shadow test: earnings and risk on gpt-6-sol,
  filing and insider on gpt-6-luna (packet `34-gpt6-scoring-analyzers`).
- Database credentials moved out of `config.yaml` into `.env`: `DATABASE_URL` is now required by
  every entry point (no config or SQLite fallback), logged URLs mask the password,
  `docker-compose.yml` is removed and the docs describe the remote server; deprecated
  `datetime.utcnow()` replaced (packet `32-remote-db-housekeeping`).
- AI risk score now comes from the average severity of the listed material risks instead of the
  single overall label (nearly always HIGH); no score when no risks are listed (packet
  `31-risk-score-severity-mix`).
- JARVIS, LP letter, weekly commentary and sector reports use gpt-6-sol; filing summaries use
  gpt-6-luna. Scoring analyzers unchanged (packet `29-gpt6-non-trading`).
- Reference docs, README and `.env.example` brought up to date; `docs/README.md` separates
  living references from historical records (packet `27-reference-docs-refresh`).
- Growth factor revenue acceleration is now the trend of up to four YoY growth points (needs 4
  to 8 stored quarters), and fundamentals refreshes no longer blank stored figures (packet
  `26-acceleration-trend`).
- `CLAUDE.md` imports `AGENTS.md` and `10Rules.md`; specs and test plans 09-12 brought up to the
  `.ai/templates` structure after a Reviewer pass (packet `13-agent-guidance-compliance`).
- `10Rules.md` merged into `AGENTS.md` and deleted; `AGENTS.md` is now the single source of
  agent rules (packet `14-merge-10rules-into-agents`).

### Security
- certifi raised to 2026.10.19; eight pillow 12.3.0 advisories ignored by name in
  `make security` until streamlit allows pillow 13 (none reachable: no code opens images), with
  a test that forces their removal then (packet `36-pillow-certifi-advisories`).
- Raised indirect-dependency pins (anyio, gitpython, msgpack, pillow, soupsieve, urllib3,
  virtualenv) to clear pip-audit advisories (packet `10-dependency-security-bumps`).

### Fixed
- Yahoo snapshots (packet `40-snapshot-reliability`): short interest and estimates share one
  `.info` request per ticker per run; every Yahoo fetch retries with backoff (5 s, 15 s) and
  failed tickers get a second pass; each step logs its coverage and an ERROR below 95%; a failed
  earnings calendar fetch is no longer stored as "no dates"; a successful calendar fetch removes
  the ticker's superseded future dates; the 90-day estimate revision now works on Fridays
  (105-day estimates lookback; prior snapshot up to 7 days before each window's cutoff).
- Price history (packet `38-price-history-integrity`): a ticker's full history is re-downloaded
  when a split or dividend re-adjusts it or it has a missing date; the nightly run stores the
  day's close; all-null rows are no longer stored; `volume` is 64-bit; scoring stops when SPY
  has no bar for the score date; new `run_data.py --refresh-prices`.
- Insider transactions are stored only for the issuer they concern (a company's own trades in
  other issuers, e.g. Berkshire buying Lennar, no longer count as BRK-B insider trades); every
  transaction in a Form 4 is kept (unique key `(ticker, accession_no, txn_index)`, so an
  exercise and a sale of the same share count are both stored); CIKs come from SEC's
  `company_tickers.json` once per run instead of `browse-edgar` and stale stored URLs.
  `run_data.py --rebuild-insider` rebuilds insider rows and cluster flags from stored Form 4
  XML; `run_all.py --forms` passes form types to Layer 1 (packet `39-insider-issuer-integrity`).
- Insider analysis no longer re-runs nightly for unchanged Form 4 activity: cached on a hash
  of its transaction input instead of the score date (packet `37-insider-cache-key`).
- Risk factors found in 15 more 10-Ks (`Item 1.A`, `Item 1(a)`, "Part I" prefixes and bare "Risk
  Factors" headings); Ticker page AI scores shown to one decimal in the right colours; the report
  shows the risk score and marks the model's overall label as not scored (packet
  `33-risk-extraction-display`).
- AI analyzer inputs (packet `30-analyzer-input-fixes`): filing analyzer columns were
  mislabelled; risk analyzer now reads the 10-K's Item 1A section; affected cached results
  recomputed.
- Growth comparisons (growth factor, quality's margin trend, stored growth ratios) pick the
  earlier period by date, not row position (packet `25-date-based-growth`).
- Ticker valuation skips a newest quarter Yahoo hasn't filled in yet, so BAC and GS show
  P/E again (packet `24-valuation-ttm-empty-quarter`).
- Fundamentals (packet `23-fundamentals-integrity`): shares outstanding exclude treasury
  stock; statements matched by period, not position; periods stored as month-ends with
  duplicates merged; new `run_data.py --refresh-fundamentals`.
- 13F holdings matched by CUSIP before issuer name, so Alphabet's GOOG and GOOGL are booked
  separately (packet `22-cusip-first-resolution`).
- OpenFIGI 13F ticker resolution (packet `19-openfigi-resolution`): non-US issuers found via
  `ID_CINS`; US common stock only; non-S&P 500 holdings dropped; one lookup per identifier per
  run; new `run_data.py --refetch-13f`.
- 13F institutional holdings (packet `17-13f-ingestion-fixes`): values no longer 1000x too large;
  options and bonds excluded; rows per ticker summed; stored by quarter, not filing date, with
  amendments skipped; each fund compared with its own previous filing; infotables found via
  `index.json`, so Baupost and Berkshire load; Third Point and Appaloosa CIKs corrected. Rows
  stored under the old rules are removed and re-fetched on the next 13F run.
- LLM layer (packet `16-llm-review-findings`): permanent API errors are no longer retried; config
  overrides can't send a key to another provider's host; JARVIS streaming tolerates chunks with
  empty `choices`; BYOK upstream cost honours `is_byok`; provider values are case-insensitive.
- Lint passes on ruff 0.16.10: Markdown excluded from ruff, since 0.16 formats Markdown code
  blocks by default (packet `15-ruff-0-16`).
- `make sync` works again with pip 26.2: pip-tools raised to 7.6.2 and upgraded alongside
  pip (packet `11-pip-tools-compat`).
