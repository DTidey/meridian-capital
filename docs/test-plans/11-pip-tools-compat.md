# Test Plan: pip-tools-compat

Path: `docs/test-plans/11-pip-tools-compat.md`

## What changed

- Raised the pip-tools pin to 7.6.2 and made `make sync` upgrade pip-tools alongside pip.

## Acceptance criteria coverage

- AC1: `git diff main -- requirements-dev.txt` shows only the pip-tools line changing.
  Check compatibility in a fresh venv with pip 26.2.1 and pip-tools 7.6.2:
  `pip-sync --dry-run` and `pip-compile --dry-run` both complete.

- AC2: Review the `Makefile` `sync` target; its install line includes `pip-tools`.

- AC3: In the project venv, which was previously broken with pip 26.2.1 and pip-tools 7.5.3, run
  `make sync` (completes; pip-tools 7.6.2 installed), then `make compile` (no lock-file diff),
  `make security`, `make lint` and `make test`. CI runs the same targets.

## Commands

```bash
make sync
make compile && git diff --stat requirements.txt requirements-dev.txt
make security
make lint
make test
```
