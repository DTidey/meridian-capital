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

## Edge cases
- From spec:
  - A venv already broken by pip 26.2 with pip-tools 7.5.3: `make sync` repairs it (verified on
    the project venv)
- Additional adversarial cases:
  - Not applicable: Makefile and pin change only. The 2026-10-10 Reviewer pass noted that
    `make sync` installs the latest pip-tools and `pip-sync` skips it, so the pin is not enforced
    locally. Accepted in the spec's Assumptions.

## Notes
- Flaky risks: a future pip or pip-tools release can break `make sync` again.
- Determinism considerations: none.

## Commands

```bash
make sync
make compile && git diff --stat requirements.txt requirements-dev.txt
make security
make lint
make test
```
