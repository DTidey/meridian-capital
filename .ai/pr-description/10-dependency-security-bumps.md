## Summary

- Fixes the Security step that has failed CI on `main` since 2026-09-24. pip-audit flagged
  advisories in seven indirect dependencies; because the step fails, the Test step never runs.
- Raises only the affected pins to fixed versions and leaves every other pin unchanged:
  anyio 4.14.2, gitpython 3.2.0, msgpack 1.2.3, pillow 12.3.0, soupsieve 2.9, urllib3 2.8.0, and
  in dev only, virtualenv 21.14.5 (with python-discovery 1.6.2).

## Spec

- Spec: `docs/specs/10-dependency-security-bumps.md`
- Test plan: `docs/test-plans/10-dependency-security-bumps.md`
- PR draft path: `.ai/pr-description/10-dependency-security-bumps.md`

## Acceptance Criteria

- [x] AC1: pip-audit reports no known vulnerabilities across both lock files.
- [x] AC2: Only the listed pins change; no `.in` changes.
- [x] AC3: Full test suite, `make lint` and `make security` pass with the new versions installed.

## Security Review

- [x] Security considerations reviewed in `docs/specs/10-dependency-security-bumps.md`
- [x] Change only removes known advisories; no new dependencies

## Validation

- `make security`: pass (no known vulnerabilities)
- `make lint`: pass
- `make test`: 544 passed with the new versions installed
