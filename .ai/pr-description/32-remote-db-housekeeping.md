## Summary

Catches the repository up with the move of the database to a shared server, and removes the
database credentials from this public repository.

- **Credentials out of `config.yaml`.** `DATABASE_URL` in the git-ignored `.env` is now the only
  source of the connection URL. `data.db.database_url()` reads it for all nine entry points and
  stops with a clear error if it is missing. The silent fallback to a local SQLite file in three
  entry points is gone.
- **No passwords in logs.** `run_data.py` and `run_scoring.py` logged the full URL; they now
  mask the password (`redacted_url`).
- **`run_execution.py`** reads `.env` from the repository root via python-dotenv, like the other
  entry points (it used the current directory and its own parser).
- **Docker removed.** `docker-compose.yml` deleted; README, CLAUDE.md, `.env.example` and the
  living docs describe the remote server and `DATABASE_URL`.
- **`datetime.utcnow()` replaced** (backlog item) by `data._utils.utc_now()`, which returns the
  same naive UTC value, so stored timestamps keep their format.

## Spec

- Spec: `docs/specs/32-remote-db-housekeeping.md`
- Test plan: `docs/test-plans/32-remote-db-housekeeping.md`
- PR draft path: `.ai/pr-description/32-remote-db-housekeeping.md`

## Acceptance Criteria

- [x] AC1: `database_url()` reads `DATABASE_URL` only and raises when it is unset or blank.
- [x] AC2: All nine entry points use `database_url()`.
- [x] AC3: `config.yaml` has no `database` section or connection URL.
- [x] AC4: Logged URLs mask the password.
- [x] AC5: `run_execution.py` loads `.env` from the repository root.
- [x] AC6: `docker-compose.yml` deleted; living docs describe the remote server.
- [x] AC7: No `datetime.utcnow()` left in `data/` or `tests/`; `utc_now()` is naive UTC.
- [x] AC8: The `utcnow()` backlog item removed.

## Security Review

- [x] Security considerations reviewed in `docs/specs/32-remote-db-housekeeping.md`: removes
  credentials from a tracked file and from logs, and fails closed when the URL is missing. The
  old password remains in git history and is rotated after merge.

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 1010 passed; DeprecationWarnings down from 135 to 1 (third-party)

Reviewer pass (fresh subagent): no blockers. Important items fixed: the guard tests now match
either quote style and flag logging `engine.url`; `run_execution.py` uses python-dotenv, whose
quote handling its own parser lacked.

## Before pulling this onto the machine that runs the pipeline

The nightly `run_all.py` cron job and the dashboard run from that working copy. Add
`DATABASE_URL` to its `.env` first, or they stop with "DATABASE_URL is not set".

## Risks and follow-ups

- Rotate the database password, update `.env`, and restrict server access (`pg_hba.conf`,
  firewall) to trusted hosts.
