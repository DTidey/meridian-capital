## Summary

- Fixes local `make sync`. It upgrades pip to 26.2, and the pinned pip-tools 7.5.3 then fails
  on import. CI didn't notice because `make compile` runs first and installs the latest pip-tools.
- Raises the pip-tools pin to 7.6.2 and makes `make sync` upgrade pip-tools alongside pip, as
  `make compile` already does, so a future pip release can't break it the same way.

## Spec

- Spec: `docs/specs/11-pip-tools-compat.md`
- Test plan: `docs/test-plans/11-pip-tools-compat.md`
- PR draft path: `.ai/pr-description/11-pip-tools-compat.md`

## Acceptance Criteria

- [x] AC1: `requirements-dev.txt` pins pip-tools 7.6.2; no other pin changes.
- [x] AC2: `make sync` upgrades pip-tools together with pip, setuptools and wheel.
- [x] AC3: `make sync` and `make compile` complete on pip 26.2.1; compile produces no lock-file
  changes; lint, security and tests pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/11-pip-tools-compat.md`
- [x] Developer tooling only; runtime dependencies unchanged

## Validation

- `make sync`: pass (previously failed)
- `make compile`: pass, no lock-file diff
- `make security`: pass
- `make lint`: pass
- `make test`: 571 passed
