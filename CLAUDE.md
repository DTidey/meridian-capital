# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup
make venv       # create .venv
make compile    # pin requirements via pip-tools
make sync       # install all dependencies into .venv

# Daily workflow
make lint       # ruff check + ruff format --check
make test       # pytest (tests/ directory)
make security   # bandit on source dirs + pip-audit on requirements files
make precommit  # run all pre-commit hooks against every file
```

Run a single test:
```bash
. .venv/bin/activate && pytest tests/test_foo.py::test_bar -q
```

Start the dashboard:
```bash
. .venv/bin/activate && streamlit run dashboard/app.py --server.port 8502
```

Run the full pipeline:
```bash
. .venv/bin/activate && python run_all.py
```

## Architecture

Meridian Capital Partners is a Python 3.12 quantitative long/short equity fund pipeline. It ingests
market, fundamental, and alternative data for the S&P 500 universe, scores securities across factor
dimensions, applies AI analysis, constructs a market-neutral portfolio, manages risk, and routes
orders through Alpaca. A Streamlit dashboard provides daily reporting.

The database is PostgreSQL, served via `docker-compose.yml`. All tables are defined in `data/db.py`
and registered on a single SQLAlchemy `MetaData` object.

### Seven-layer pipeline

| Layer | Entry point | Purpose |
|---|---|---|
| 1 | `run_data.py` | Data ingestion (prices, fundamentals, SEC filings, transcripts) |
| 2 | `run_scoring.py` | Factor scoring (momentum, quality, value, revisions, insider, growth, short interest, institutional) |
| Transcript | `run_transcripts.py` | Earnings transcript ingestion (between Layers 2 and 3) |
| 3 | `run_analysis.py` | AI analysis via OpenAI (earnings, filings, risk, insider) |
| 4 | `run_portfolio.py` | Portfolio construction (MVO optimizer, conviction weighting) |
| 5 | `run_risk_check.py` | Risk management (pre-trade veto, circuit breakers, tail risk) |
| 6 | `run_execution.py` | Order execution via Alpaca |
| 7 | `run_reporting.py` + `dashboard/app.py` | Reporting, tear sheet, LP letter, Streamlit dashboard |

Run all layers in sequence with `python run_all.py`.

### Numbered packet system

Every change is tracked as a numbered "packet" with a shared two-digit prefix, e.g. `08`:

| Artifact | Path |
|---|---|
| Spec | `docs/specs/08-my-change.md` |
| Test plan | `docs/test-plans/08-my-change.md` |
| PR draft | `.ai/pr-description/08-my-change.md` |

The prefix is assigned sequentially and never renumbered. Specs `01`-`07` document the initial
seven layers. Use `08` onwards for new changes.

### Five-role workflow

Roles are defined in `.ai/roles/` and must be executed in order:

1. **Spec Writer** (`00_spec_writer.md`) — writes `docs/specs/<nn>-<slug>.md`. No implementation code.
2. **Orchestrator** (`01_orchestrator.md`) — breaks the spec into a task checklist and commit plan.
3. **Implementer** (`02_implementer.md`) — writes minimal code strictly against acceptance criteria.
4. **Tester** (`03_tester.md`) — writes `docs/test-plans/<nn>-<slug>.md` and pytest tests.
5. **Reviewer** (`04_reviewer.md`) — Blockers / Important / Suggestions with file+line citations.

### Acceptance criteria conventions

- Labelled `AC1`, `AC2`, `AC3`, ... in the spec.
- Every AC must be testable and mapped to a named test in the test plan.
- PR body and PR draft must check every AC with `- [x] AC1 ...` syntax.

### CI enforcement

`.github/scripts/validate_pr.py` runs on every PR and enforces spec linkage, AC coverage, and
companion test plan / PR draft presence. Required status checks before merging: `CI / test` and
`CodeQL / analyze`.

### Blocking and handoff format

```
Blocked on: <question>
Affected AC: <AC id(s) or "missing">
Proposed default: <optional>
```

Reviewer blockers additionally:
```
File: <path:line>
AC: <AC id or "N/A">
Why this blocks merge: <one sentence>
```

### Security

Every spec that changes code must include a `Security considerations` section. `make security`
runs Bandit across the source directories and pip-audit against both requirements files.

### Tooling

- **Python 3.12**, line length 100, ruff rule sets: `E F I W UP B C4 SIM`.
- Pre-commit hooks auto-fix with `ruff check --fix` and `ruff format` on every commit.
- Dependencies managed via `pip-tools`: edit `requirements.in` / `requirements-dev.in`, then
  `make compile && make sync`.
- Versions in `CHANGELOG.md` use `MAJOR.MINOR.PATCH`; accumulate under `## Unreleased` until a
  release is explicitly requested.
- Functions <= 50 lines, nesting <= 4 levels; split by feature/domain when a file mixes concerns.
- Record non-obvious decision reasoning in `log/changelog-YYYY-MM-DD.md`.
- Never use emojis.

### Key environment variables

```
DATABASE_URL          # PostgreSQL connection string
OPENAI_API_KEY        # OpenAI (Layer 3 AI analysis + JARVIS)
FMP_API_KEY           # Financial Modeling Prep (optional — falls back to SEC 8-K mining)
ALPACA_API_KEY        # Alpaca brokerage
ALPACA_SECRET_KEY     # Alpaca brokerage
ALPACA_PAPER          # true/false (default true)
SEC_USER_AGENT        # SEC EDGAR user agent string
SEC_USER_EMAIL        # SEC EDGAR contact email
FRED_API_KEY          # FRED macro data (optional — tail risk monitor)
```

Copy `.env.example` to `.env` and fill in your keys before running any layer.
