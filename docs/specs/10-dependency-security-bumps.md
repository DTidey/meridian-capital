# Dependency Security Bumps

**Spec file:** `docs/specs/10-dependency-security-bumps.md`
**Status:** Done
**Date:** 2026-10-10

## Purpose

CI's Security step (`make security`, which runs pip-audit) has failed on `main` since 2026-09-24
because of published advisories against seven indirect dependencies in the lock files. That
failure stops the job before the Test step, so no PR currently gets its tests run in CI. This
change raises only the affected packages to their first fixed versions and leaves every other pin
unchanged.

## Acceptance criteria

- AC1: `pip-audit --no-deps --disable-pip -r requirements.txt -r requirements-dev.txt` reports no
  known vulnerabilities.

- AC2: Only these pins change: anyio 4.14.0 -> 4.14.2, gitpython 3.1.50 -> 3.2.0, msgpack
  1.2.0 -> 1.2.3, pillow 12.2.0 -> 12.3.0, soupsieve 2.8.4 -> 2.9, urllib3 2.7.0 -> 2.8.0
  (both lock files), plus virtualenv 21.5.1 -> 21.14.5 and its dependency python-discovery
  1.4.2 -> 1.6.2 (dev lock file only). No `.in` file changes.

- AC3: The full test suite passes with the new versions installed, and `make lint` and
  `make security` pass.

## Files changed

| File | Change |
|---|---|
| `requirements.txt` | Six indirect-dependency pins raised |
| `requirements-dev.txt` | Same six, plus virtualenv and python-discovery |

## Security considerations

- Auth/authz impact: None.
- Secrets or credential handling: None.
- Network or external service impact: None. This change fixes known advisories. gitpython goes
  to 3.2.0 rather than 3.1.51, because 3.1.51 still has advisories that are only fixed in
  3.1.54-3.1.57. soupsieve is held at 2.9, not 3.x, to avoid a major-version change to HTML
  parsing.
