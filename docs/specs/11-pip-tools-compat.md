# pip-tools Compatibility

**Spec file:** `docs/specs/11-pip-tools-compat.md`
**Status:** Done
**Date:** 2026-10-10

## Purpose

`make sync` upgrades pip to the latest release but leaves pip-tools at its pinned version.
With pip 26.2, pip-tools 7.5.3 fails on import (`cannot import name 'stdlib_pkgs' from
'pip._internal.utils.compat'`), so `make sync` breaks on any local venv. CI is unaffected only
because `make compile` runs first and installs the latest pip-tools.

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
- Secrets or credential handling: None.
- Network or external service impact: None. This is developer tooling only; runtime dependencies
  are unchanged.
