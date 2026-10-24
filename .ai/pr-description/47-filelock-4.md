## Summary

Unblocks CI, which fails `make security` on every PR since 2026-10-24 (first seen on #101):
pip-audit reports three new filelock 3.29.4 advisories, fixed in 4.2.1.

- filelock 3.29.4 -> 4.2.4 in `requirements-dev.txt` (dev only: via virtualenv, cachecontrol
  and python-discovery). No other pin moves.
- A pin test keeps filelock at the fixed version.

## Spec

- Spec: `docs/specs/47-filelock-4.md`
- Test plan: `docs/test-plans/47-filelock-4.md`
- PR draft path: `.ai/pr-description/47-filelock-4.md`

## Acceptance Criteria

- [x] AC1: `make security` passes with no ignored advisories.
- [x] AC2: filelock >= 4.2.1 in `requirements-dev.txt` (and in `requirements.txt` if present).
- [x] AC3: Only the filelock pin changes; lint, tests and pre-commit pass on the new pin.

## Security Review

- [x] Security considerations reviewed in `docs/specs/47-filelock-4.md` (three filelock
  advisories fixed by upgrade; no ignores)

## Validation

Fresh venv in a worktree, synced from these pins:
- `make lint`: pass
- `make security`: pass ("No known vulnerabilities found")
- `make test`: 1376 passed
- `make precommit` with an empty `PRE_COMMIT_HOME`: pass (virtualenv built the hook
  environments on filelock 4)
- The new pin test fails against `main`'s requirements

## Risks and follow-ups

- Major-version bump of a transitive tool dependency; its consumers were run directly.
- After merge, `make sync` in the live venv. The dashboard does not use filelock, so no restart.
- #101 (docs) passes once rebased on this change.
