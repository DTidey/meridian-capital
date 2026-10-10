# pip-tools Compatibility

**Spec file:** `docs/specs/11-pip-tools-compat.md`
**Spec slug:** pip-tools-compat
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- `make sync` upgrades pip to the latest release but leaves pip-tools at its pin. pip-tools
  7.5.3 fails to import under pip 26.2, which breaks `make sync` in every local venv.
- CI only works because `make compile` runs first and installs the latest pip-tools.

## Scope
In scope:
- The pip-tools pin.
- The `make sync` install line.

Out of scope / non-goals:
- Pinning pip itself.
- Changing CI.
- Changing any other dependency.

## Assumptions
- pip-tools 7.6.2 supports pip 26.2.1, verified in a fresh venv.
- `pip-sync` ignores pip-tools itself when syncing, so upgrading it in `sync` doesn't fight the
  pin.

## Proposed behavior / API
### Public interface
- `Makefile`: the `sync` target.
- `requirements-dev.txt`: the pip-tools pin.
- CLI entry points: none.

### Inputs / outputs
- Inputs: a venv in any state.
- Outputs: a synced venv.
- Error handling: unchanged. Make stops on the first failing command.

### Examples
```bash
make sync      # pip, setuptools, wheel and pip-tools upgraded, then pip-sync
```

## Acceptance criteria

- AC1: `requirements-dev.txt` pins pip-tools 7.6.2, which works with pip 26.2.1. No other pin
  changes.

- AC2: `make sync` upgrades pip-tools together with pip, setuptools and wheel, matching
  `make compile`, so a pip upgrade can't leave an incompatible pip-tools behind.

- AC3: On a venv with pip 26.2.1, `make sync` and `make compile` both complete. `make compile`
  produces no lock-file changes, and `make lint`, `make security` and `make test` pass.

## Files changed

| File | Change |
|---|---|
| `requirements-dev.txt` | pip-tools 7.5.3 -> 7.6.2 |
| `Makefile` | `sync` target also upgrades pip-tools |

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: None.
- Secrets or credential handling: None.
- Data exposure or privacy impact: None.
- File system access impact: None.
- Network or external service impact: None.
- Dependency or supply-chain impact: Developer tooling only (pip-tools). Runtime dependencies are
  unchanged.
- Security notes for reviewers/testers: None.

## Edge cases
- A venv already broken by pip 26.2 with pip-tools 7.5.3: `make sync` repairs it, because
  pip-tools is upgraded before `pip-sync` runs.
- A future pip release: pip-tools moves with it in `sync`.

## Test guidance
- AC1 -> `git diff main -- requirements-dev.txt`; fresh-venv `pip-sync --dry-run` and
  `pip-compile --dry-run`
- AC2 -> `Makefile` review
- AC3 -> `make sync`, `make compile` (no diff), `make security`, `make lint`, `make test`

## Decision log
- 2026-10-10: `make sync` now upgrades pip-tools alongside pip, not just the pin, so the two
  can't drift apart again.
