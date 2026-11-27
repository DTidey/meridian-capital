# virtualenv Advisory PYSEC-2026-4402

**Spec file:** `docs/specs/69-virtualenv-cve.md`
**Spec slug:** virtualenv-cve
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-27

## Problem statement
`make security` and CI's `test` job fail on `main` and every PR: pip-audit reports
PYSEC-2026-4402 against virtualenv 21.14.12, fixed in 21.14.13. virtualenv is a dev-only
indirect dependency (via pre-commit), pinned only in `requirements-dev.txt`. Bandit is clean.

## Scope
In scope:
- `requirements-dev.txt`: virtualenv 21.14.12 -> 21.14.13
  (`pip-compile --upgrade-package virtualenv==21.14.13`).
- `tests/test_audit_ignores.py`: raise the virtualenv floor to 21.14.13.

Out of scope / non-goals:
- Upgrading any other dependency, including pre-commit.
- Ignoring the advisory instead (packet 45 removed all ignores).
- `requirements.txt`: it does not pin virtualenv.

## Assumptions
- 21.14.13 is the fix version pip-audit reports, and the lowest release after the vulnerable pin.
- 21.14.13 resolves against the current pins of its own dependencies (distlib, filelock,
  platformdirs, python-discovery); pip-compile moved no other line.

## Proposed behavior / API
### Public interface
- `make security`: passes again; no other command changes.

### Inputs / outputs
- No runtime behavior change; virtualenv is used only by pre-commit to build hook environments.
  The live venv picks up the pin with `make sync`.

## Acceptance criteria
- AC1: `make security` passes with no ignored advisories.
- AC2: virtualenv is pinned at 21.14.13 or later in `requirements-dev.txt`.
- AC3: Only the virtualenv pin changes in the requirements files; `make lint` and `make test`
  pass on the new pin.

## Security considerations
- Dependency or supply-chain impact: clears PYSEC-2026-4402 by upgrading; nothing is ignored.
  virtualenv is dev-only (pre-commit) and is not installed in the runtime requirements.
- Input handling, auth/authz, secrets, data exposure, file system, network: no change.
- Security notes for reviewers/testers: a patch-level bump of a dev-only transitive dependency.

## Edge cases
- A later parent caps `virtualenv<21.14.13`: `make compile` cannot resolve, so the pin cannot
  silently fall back.
- `21.14.9` sorts after `21.14.12` as a string; the floor test compares numerically.

## Test guidance
- AC1 -> `make security`; `tests/test_audit_ignores.py::TestNoAuditIgnores`
- AC2 -> `TestPins::test_virtualenv_at_fixed_version`
- AC3 -> `git diff origin/main -- requirements*.txt` review; `make lint`; `make test`

## Decision log
- 2026-11-27: Upgrade rather than ignore, as packet 45 settled.
- 2026-11-27: Packet number 69, because 68 is in use on the open `hermes/holiday-skip` branch
  (PR #152).
