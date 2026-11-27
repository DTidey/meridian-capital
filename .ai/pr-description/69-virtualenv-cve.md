## Spec

- Spec: `docs/specs/69-virtualenv-cve.md`
- Test plan: `docs/test-plans/69-virtualenv-cve.md`
- PR draft path: `.ai/pr-description/69-virtualenv-cve.md`

## Summary

Unblocks CI, which fails `make security` on `main` and every PR: pip-audit reports
PYSEC-2026-4402 against virtualenv 21.14.12, fixed in 21.14.13.

- virtualenv 21.14.12 -> 21.14.13 in `requirements-dev.txt` (dev-only, via pre-commit), pinned
  with `pip-compile --upgrade-package virtualenv==21.14.13`. No other pin moves; none of
  virtualenv's own dependencies needed a bump.
- `requirements.txt` does not pin virtualenv and is unchanged.
- The pin test floor rises to 21.14.13.

## Acceptance Criteria

- [x] AC1: `make security` passes with no ignored advisories.
- [x] AC2: virtualenv >= 21.14.13 in `requirements-dev.txt`.
- [x] AC3: Only the virtualenv pin changes; lint and tests pass on the new pin.

## Security Review

- [x] Security considerations reviewed in `docs/specs/69-virtualenv-cve.md` (PYSEC-2026-4402
  fixed by upgrade; dev-only dependency; no ignores)

## Validation

Fresh Python 3.12 venv at `/tmp/hedge-virtualenv-cve-py312`, synced from these pins with
`pip-sync`, running the Makefile recipe commands:
- `ruff check .` and `ruff format --check .`: pass
- bandit: clean; pip-audit: "No known vulnerabilities found" (before: PYSEC-2026-4402 on
  virtualenv 21.14.12)
- `pytest`: 1814 passed
- The raised pin test fails against `main`'s requirements

## Risks and follow-ups

- Patch-level bump of a dev-only dependency; no runtime impact.
- After merge, `make sync` in the live venv. PR #152 (packet 68) should rebase on this; both add
  `log/changelog-2026-11-27.md` and a CHANGELOG line.
