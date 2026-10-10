# Dependency Security Bumps

**Spec file:** `docs/specs/10-dependency-security-bumps.md`
**Spec slug:** dependency-security-bumps
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- pip-audit (CI's Security step) has failed on `main` since 2026-09-24 because of advisories
  against seven indirect dependencies.
- The failure stops the job before the Test step, so no PR has had its tests run in CI.

## Scope
In scope:
- Raising the affected lock-file pins to fixed versions.

Out of scope / non-goals:
- Upgrading any other dependency, or any direct dependency.
- Changing `.in` files or the Python version.
- Clearing the open Dependabot PRs.

## Assumptions
- The pip-audit advisory database as of 2026-10-10.
- The packages are only used indirectly, so raising their minor or patch versions doesn't change
  behaviour the test suite would miss.

## Proposed behavior / API
### Public interface
- None. Only the lock files `requirements.txt` and `requirements-dev.txt` change.
- CLI entry points: none.

### Inputs / outputs
- Inputs: the existing lock files.
- Outputs: the lock files with raised pins.
- Error handling: not applicable.

### Examples
```bash
pip-compile -P anyio -P "gitpython>=3.1.57" -P msgpack -P pillow -P soupsieve==2.9.0 \
    -P urllib3 requirements.in -o requirements.txt
```

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
- Input handling or injection risk: None.
- Secrets or credential handling: None.
- Data exposure or privacy impact: None.
- File system access impact: None.
- Network or external service impact: None.
- Dependency or supply-chain impact: Removes known advisories. No new packages; versions come from
  PyPI via pip-compile.
- Security notes for reviewers/testers: Re-run pip-audit on both lock files, and check that the
  diff touches only the listed pins.

## Edge cases
- The first fixed version still has later advisories (gitpython 3.1.51): move to a version
  that clears all of them.
- A package can't move without a dependency moving too (virtualenv needs python-discovery).
- An unconstrained upgrade would cross a major version (soupsieve 3.x): pin to the smallest fix.

## Test guidance
- AC1 -> `make security` (pip-audit: no known vulnerabilities)
- AC2 -> `git diff main -- requirements*.txt` review
- AC3 -> `make test` with the new versions installed; BeautifulSoup selector and dashboard import
  checks

## Decision log
- 2026-10-10: gitpython goes to 3.2.0, not 3.1.51; the advisories against 3.1.51 are only
  fixed in 3.1.54 to 3.1.57.
- 2026-10-10: soupsieve is held at 2.9 to avoid a major-version change to HTML parsing.
- 2026-10-10: virtualenv went to 21.14.5 with python-discovery 1.6.2. It's dev-only, so a larger
  jump is acceptable.
- 2026-10-10: Kept as a separate PR from packet 09, because it fixes `main` independently.
