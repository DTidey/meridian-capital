## Summary

Unblocks CI, which fails `make security` on every PR: pip-audit reports CVE-2026-126192 (fixed
in 2.10) and CVE-2026-126190 (fixed in 4.0) against soupsieve 2.9.

- soupsieve 2.9 -> 4.0 in both requirements files (indirect, via beautifulsoup4). 4.0 is the
  lowest release that clears both; 2.10 and 3.x still carry CVE-2026-126190.
- beautifulsoup4 stays at 4.15.0 (`soupsieve>=1.6.1`). No other pin moves.
- Pin tests keep soupsieve at the fixed version and the two files in step.

## Spec

- Spec: `docs/specs/67-soupsieve-4.md`
- Test plan: `docs/test-plans/67-soupsieve-4.md`
- PR draft path: `.ai/pr-description/67-soupsieve-4.md`

## Acceptance Criteria

- [x] AC1: `make security` passes with no ignored advisories.
- [x] AC2: soupsieve >= 4.0 in both requirements files, same version in each.
- [x] AC3: Only the soupsieve pin changes; lint and tests pass on the new pin.

## Security Review

- [x] Security considerations reviewed in `docs/specs/67-soupsieve-4.md` (two soupsieve
  advisories fixed by upgrade; no ignores)

## Validation

Fresh venv at `/tmp/hedge-soupsieve-venv`, synced from these pins with `pip-sync`:
- `ruff check .` and `ruff format --check .`: pass
- bandit: clean; pip-audit: "No known vulnerabilities found"
- `pytest`: 1781 passed
- bs4 smoke test on soupsieve 4.0: `find` and `select_one` both work
- The new pin test fails against `main`'s requirements

## Risks and follow-ups

- Two-major-version bump of a transitive dependency; no code calls CSS selectors, and the
  EDGAR parsing tests pass.
- After merge, `make sync` in the live venv and restart the dashboard (the ticker page filing
  viewer imports bs4).
