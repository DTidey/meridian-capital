## Summary

Unblocks CI, which fails `make security` on every PR since 2026-10-22 (first seen on #97):
pip-audit reports 16 new pillow 12.3.0 advisories and two virtualenv 21.14.5 advisories.

- streamlit 1.66.1 now allows pillow 13, so pillow moves 12.3.0 -> 13.0.0 and streamlit
  1.66.0 -> 1.66.1 (`requirements.in` floor raised to match).
- virtualenv 21.14.5 -> 21.14.12 (dev only, via pre-commit); packaging 26.2 -> 26.3, which
  virtualenv 21.14.9+ requires.
- The packet 36 pillow ignores are removed: `make security` now runs pip-audit with no ignores.
  Their tests are replaced by tests that keep it that way. Backlog item closed.

## Spec

- Spec: `docs/specs/45-pillow-13-virtualenv.md`
- Test plan: `docs/test-plans/45-pillow-13-virtualenv.md`
- PR draft path: `.ai/pr-description/45-pillow-13-virtualenv.md`

## Acceptance Criteria

- [x] AC1: `make security` passes with no `--ignore-vuln` and no `PILLOW_AUDIT_IGNORES`.
- [x] AC2: pillow >= 13.0.0 and streamlit >= 1.66.1 in both requirements files; shared pins match.
- [x] AC3: virtualenv >= 21.14.9 in `requirements-dev.txt`.
- [x] AC4: Only the four packages' pins change; lint, tests and a dashboard start pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/45-pillow-13-virtualenv.md` (all pillow
  and virtualenv advisories fixed by upgrade; no ignores remain)

## Validation

- `make lint`: pass
- `make security`: pass ("No known vulnerabilities found")
- `make test`: 1278 passed
- Dashboard started from a fresh venv on these pins: `/_stcore/health` returned `ok`;
  matplotlib saved a PNG with pillow 13
- The no-ignores test fails against `main`'s Makefile

Reviewer pass (fresh subagent): no blockers or Important items; suggestions applied (ignores
via a Makefile variable also caught, comment on virtualenv being dev-only, test count updated).

## Risks and follow-ups

- After merge, the live venv needs `make sync` and
  `systemctl --user restart meridian-dashboard.service`.
- #97 (docs) passes once rebased on this change.
