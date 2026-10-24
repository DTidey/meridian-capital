# filelock Advisories

**Spec file:** `docs/specs/47-filelock-4.md`
**Spec slug:** filelock-4
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
`make security` started failing on 2026-10-24 for every PR (first seen on #101, docs only):
pip-audit reports three new filelock 3.29.4 advisories (CVE-2026-112907, CVE-2026-112913,
CVE-2026-112940), all fixed in 4.2.1. `main` passed on 2026-10-23, so the advisories are new.

## Scope
In scope:
- `requirements-dev.txt`: filelock 3.29.4 -> 4.2.4 (`pip-compile --upgrade-package filelock`).
- `tests/test_audit_ignores.py`: a test that keeps filelock at the fixed version.

Out of scope / non-goals:
- Upgrading any other dependency.
- Ignoring the advisories instead (packet 45 removed all ignores).

## Assumptions
- filelock is dev-only: required by virtualenv (pre-commit), cachecontrol (pip-audit) and
  python-discovery; no source file imports it, and it is not in `requirements.txt`.
- Those three accept filelock 4: pip-compile resolves with no other pin changing.

## Proposed behavior / API
### Public interface
- `make security`: passes again; no other command changes.

### Inputs / outputs
- No runtime behavior change. The live venv picks up the pin with `make sync`; the dashboard
  does not use filelock, so it needs no restart.

## Acceptance criteria
- AC1: `make security` passes with no ignored advisories.
- AC2: filelock is pinned at 4.2.1 or later in `requirements-dev.txt`, and in
  `requirements.txt` if it ever appears there.
- AC3: Only the filelock pin changes in the requirements files; `make lint`, `make test` and
  `make precommit` (which builds hook environments through virtualenv) pass on the new pin.

## Security considerations
- Dependency or supply-chain impact: clears three filelock advisories by upgrading; nothing is
  ignored. filelock is a dev and CI tool dependency, not shipped in the runtime requirements.
- Input handling, auth/authz, secrets, data exposure, file system, network: no change.
- Security notes for reviewers/testers: a major-version bump (3 -> 4) of a transitive
  dependency; its consumers were exercised directly (pip-audit, pre-commit with a fresh cache).

## Edge cases
- A later parent caps `filelock<4`: `make compile` cannot resolve, so the pin cannot silently
  fall back.
- filelock becomes a runtime dependency: the test checks that pin too.
- A later filelock such as 4.10.0 sorts before 4.2.1 as a string; versions compare
  numerically.

## Test guidance
- AC1 -> `make security`; `tests/test_audit_ignores.py::TestNoAuditIgnores`
- AC2 -> `TestPins::test_filelock_at_fixed_version`
- AC3 -> `git diff origin/main -- requirements*.txt` review; `make lint`; `make test`;
  `make precommit` with an empty `PRE_COMMIT_HOME`

## Decision log
- 2026-10-24: Upgrade rather than ignore, as packet 45 settled.
- 2026-10-24: Packet number 47, because the data audit plan already reserves 43 and 44.
