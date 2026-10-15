# Test Plan: remote-db-housekeeping

Path: `docs/test-plans/32-remote-db-housekeeping.md`

## What changed
- `data/db.py`: `database_url()` and `redacted_url()`.
- `data/_utils.py`: `utc_now()`.
- Nine entry points (`run_*.py`, `dashboard/app.py`) use `database_url()`; `run_scoring.py`
  and `run_data.py` log the URL through `redacted_url()`; `run_execution.py` loads `.env` from
  its own directory, via python-dotenv like the others.
- `config.yaml`: `database` section removed. `docker-compose.yml` deleted.
- `data/` and `tests/`: `datetime.utcnow()` replaced by `utc_now()`.
- Docs: `README.md`, `CLAUDE.md`, `.env.example`, `docs/specification.md`,
  `docs/architecture.md`, `docs/run_dashboard.md`, `docs/alpaca_paper_trading.md`,
  `docs/backlog.md`.

## Acceptance criteria coverage
- AC1: `tests/test_db_config.py::TestDatabaseUrl` (4 tests)
- AC2: `TestEntryPoints::test_entry_points_use_helper` (9 entry points)
- AC3: `TestConfigFile` (2 tests)
- AC4: `TestRedactedUrl` (3 tests), `TestEntryPoints::test_logged_urls_redacted`
- AC5: `TestExecutionDotenv::test_loads_env_from_script_dir_not_cwd`, `test_quoted_value_unquoted`
- AC6: `TestDocs::test_compose_file_deleted`, `test_no_docker`, `test_no_url_with_password`,
  `test_database_url_documented` (7 docs each; the Docker and password checks also cover
  `docs/erd.md` and `docs/run_all.md`)
- AC7: `TestUtcNow` (naive and current, no offset in ISO strings, none left in `data/` or
  `tests/`)
- AC8: `TestDocs::test_backlog_item_removed`

## Edge cases
- From spec: blank `DATABASE_URL`, URL without a password.
- Additional adversarial cases:
  - Surrounding whitespace and a trailing newline in `DATABASE_URL` are stripped
  - Percent-encoded password (`p%40ss%3Aw0rd`) is masked in both encoded and decoded form
  - A `.env` in the current directory is not read in place of the repository's
  - Placeholder URLs (`<user>:<password>@`) in docs are allowed; literal passwords are not
  - A quoted `.env` value reaches `run_execution.py` without its quotes (python-dotenv)
  - Entry-point source checks match either quote style and flag logging `engine.url`

## Notes
- The existing suites for the data modules exercise `utc_now()` through the replaced call
  sites (timestamps, cache freshness in `universe` and `fundamentals`). DeprecationWarnings in
  `make test` dropped from 135 to 1 (the remaining one is from a third-party library).

## Commands
```bash
make lint
make test
make security
```
