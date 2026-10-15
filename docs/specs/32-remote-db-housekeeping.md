# Remote Database Housekeeping

**Spec file:** `docs/specs/32-remote-db-housekeeping.md`
**Spec slug:** remote-db-housekeeping
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-15

## Problem statement
The database moved from a local Docker container to a shared server on 2026-09-24. Four things have not caught up:
1. **Credentials in a public repo.** `config.yaml` holds the full connection URL, user and
   password included, and the repository is public. The password will be rotated after this
   packet lands; the new one must live only in the git-ignored `.env`.
2. **Credentials in logs and a silent fallback.** `run_scoring.py` and `run_data.py` log the
   full URL with its password. Three entry points (`run_portfolio.py`, `run_risk_check.py`,
   `run_analysis.py`) fall back to a local SQLite file `meridian.db` when no URL is set, so a
   missing setting produces an empty database instead of an error.
3. **Stale docs.** `docker-compose.yml` and the living docs still describe a local Docker
   database.
4. **Deprecated `datetime.utcnow()`** (backlog item): 41 call sites in `data/` and `tests/`
   produce DeprecationWarnings and will break on a future Python version.

## Scope
In scope:
- One helper that reads `DATABASE_URL` from the environment, used by every entry point; the
  `database` section removed from `config.yaml`.
- Password redacted wherever the URL is logged.
- `run_execution.py` loads `.env` from the repository root, as the other entry points do.
- `docker-compose.yml` deleted; living docs and `.env.example` updated.
- `datetime.utcnow()` replaced in `data/` and `tests/`.
- The `utcnow()` item removed from `docs/backlog.md`.

Out of scope / non-goals:
- Rotating the database password and restricting server access (the owner does this after
  merge).
- Rewriting git history: the old password stays in history, which is why it is rotated.
- Historical docs (`docs/specs/`, `docs/test-plans/`, `docs/prompts/`, `log/`), per
  `docs/README.md`.
- Moving stored timestamps to timezone-aware strings (`+00:00`). Some tables already store
  aware strings from `datetime.now(UTC)`; unifying formats would be a data migration.

## Assumptions
- Every entry point loads `.env` before reading the URL (checked 2026-10-15: all nine call
  `load_dotenv` at import, except `run_execution.py`, which loads it in `main()` before reading
  the URL, but from the current directory).
- The test suite never reads `config.yaml`'s database URL (checked: tests build SQLite engines
  directly).
- Only two `utcnow()` sites compare datetimes (`data/fundamentals.py`, `data/universe.py`), and
  both compare against naive datetimes parsed from stored strings.

## Proposed behavior / API
### Public interface
- New `data.db.database_url() -> str`.
- New `data.db.redacted_url(url: str) -> str`.
- New `data._utils.utc_now() -> datetime`: the current UTC time as a naive datetime, the value
  `datetime.utcnow()` returned.
- Entry points: `run_data.py`, `run_scoring.py`, `run_transcripts.py`, `run_analysis.py`,
  `run_portfolio.py`, `run_risk_check.py`, `run_execution.py`, `run_reporting.py`,
  `dashboard/app.py`.

### Inputs / outputs
- `database_url()` returns `DATABASE_URL` from the environment. If it is unset or blank it
  raises `RuntimeError("DATABASE_URL is not set: add it to .env (see .env.example)")`.
- `redacted_url(url)` returns the URL with any password replaced by `***`.

### Examples
```python
os.environ["DATABASE_URL"] = "postgresql+psycopg2://meridian:s3cret@db.example:5432/meridian"
database_url()   # the URL above
redacted_url(database_url())   # "postgresql+psycopg2://meridian:***@db.example:5432/meridian"
```

## Acceptance criteria
- AC1: `database_url()` returns `DATABASE_URL` from the environment and raises `RuntimeError`
  naming `DATABASE_URL` and `.env` when it is unset or blank. There is no fallback to
  `config.yaml` or SQLite.
- AC2: All nine entry points get their URL from `database_url()`; none reads
  `config["database"]`, `DATABASE_URL` directly, or a SQLite default.
- AC3: `config.yaml` has no `database` section and contains no connection URL.
- AC4: `redacted_url` masks the password (and leaves a URL without one unchanged); entry points
  that log the database URL log it through `redacted_url`.
- AC5: `run_execution.py` loads `.env` from the repository root, whatever the current
  directory.
- AC6: `docker-compose.yml` is deleted. `README.md`, `CLAUDE.md`, `.env.example` and the living
  docs in `docs/` (`specification.md`, `architecture.md`, `run_dashboard.md`,
  `alpaca_paper_trading.md`) describe a remote PostgreSQL server configured by `DATABASE_URL`
  in `.env`, and none mentions Docker or contains a connection URL with a password.
- AC7: No `datetime.utcnow()` remains in `data/` or `tests/`; `utc_now()` returns a naive
  datetime within a second of `datetime.now(UTC)`, so stored timestamp strings keep their
  format.
- AC8: `docs/backlog.md` no longer lists the `utcnow()` item.

## Security considerations
- Removes the database credentials from a tracked file in a public repository (AC3) and from
  logs (AC4). The old password remains in git history; the owner rotates it after merge, and
  should restrict `pg_hba.conf` and the firewall to trusted hosts.
- Fails closed: a missing `DATABASE_URL` stops the run instead of writing to a stray SQLite
  file (AC1).
- `.env` is git-ignored (checked); `.env.example` carries a placeholder only.
- No new dependencies or network calls.

## Edge cases
- `DATABASE_URL` set but blank: treated as unset (AC1).
- URL without a password (e.g. SQLite, or peer auth): logged unchanged (AC4).
- Old `.env` files with `POSTGRES_PASSWORD`: harmless, no longer read by anything.
- Streamlit caches the engine (`st.cache_resource`): a missing URL raises on first page load
  with the same message.

## Test guidance
- AC1 -> `tests/test_db_config.py::TestDatabaseUrl`
- AC2 -> `tests/test_db_config.py::TestEntryPoints::test_entry_points_use_helper`
- AC3 -> `tests/test_db_config.py::TestConfigFile`
- AC4 -> `tests/test_db_config.py::TestRedactedUrl`,
  `TestEntryPoints::test_logged_urls_redacted`
- AC5 -> `tests/test_db_config.py::TestExecutionDotenv`
- AC6 -> `tests/test_db_config.py::TestDocs`
- AC7 -> `tests/test_db_config.py::TestUtcNow`
- AC8 -> `tests/test_db_config.py::TestDocs::test_backlog_item_removed`

## Decision log
- 2026-10-15: Credentials move bundled with the Docker docs change: both describe how the app
  reaches the remote database. Requested by the owner after the public-repo exposure was found.
- 2026-10-15: `DATABASE_URL` only, no config fallback: a URL in `config.yaml` is how the
  credentials got committed, and the silent SQLite fallback hides a missing setting.
- 2026-10-15: `utc_now()` returns a naive datetime, not an aware one: identical values to
  `utcnow()`, so stored strings and the two comparisons are unchanged. Aware timestamps
  everywhere would need a data migration (non-goal).
- 2026-10-15 (Implementer): `run_execution.py` reads `.env` from its own directory; it used
  the current directory, which only worked because the config fallback hid a missing URL.
- 2026-10-15: Operational note for the owner: the nightly `run_all.py` cron job and the
  dashboard run from this working copy, so `DATABASE_URL` must be in `.env` before the merge
  is pulled.
- 2026-10-15 (Reviewer pass): no blockers. Important: entry-point source checks widened (either
  quote style, `engine.url` logging); `run_execution.py` now uses python-dotenv instead of its
  own parser, which kept quotes around values. Hostname dropped from this spec.
