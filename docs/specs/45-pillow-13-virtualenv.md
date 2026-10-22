# Pillow 13 and virtualenv Advisories

**Spec file:** `docs/specs/45-pillow-13-virtualenv.md`
**Spec slug:** pillow-13-virtualenv
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-22

## Problem statement
`make security` started failing on 2026-10-22 for every PR (first seen on #97, docs only):
pip-audit reports 16 new pillow 12.3.0 advisories (fixed in 13.0.0) and two virtualenv 21.14.5
advisories (fixed in 21.14.7 and 21.14.9). Packet 36 ignored eight earlier pillow advisories
because streamlit required `pillow<13`. streamlit 1.66.1 now allows `pillow<14`, so pillow can
move to 13 and the packet 36 ignores can go, as `docs/backlog.md` planned.

## Scope
In scope:
- `requirements.in`: `streamlit>=1.66.1` (the first release that allows pillow 13).
- Pins in `requirements.txt` and `requirements-dev.txt`: streamlit 1.66.0 -> 1.66.1, pillow
  12.3.0 -> 13.0.0, packaging 26.2 -> 26.3; virtualenv 21.14.5 -> 21.14.12 (dev only).
  packaging moves because virtualenv 21.14.9 and later require `packaging>=26.3`.
- `Makefile`: delete `PILLOW_AUDIT_IGNORES` and the `--ignore-vuln` arguments.
- `tests/test_audit_ignores.py`: replace the packet 36 time-box tests with tests that keep
  `make security` free of ignores and the fixed versions pinned.
- Remove the backlog item "Remove the pillow pip-audit ignores".

Out of scope / non-goals:
- Upgrading any other dependency.
- Rewriting packet 36's spec; it stays as history.

## Assumptions
- streamlit 1.66.1 declares `pillow<14,>=9.2.0` (checked in its wheel metadata).
- matplotlib accepts pillow 13 (`pillow>=8`); no source file imports PIL, so pillow 13's API
  changes cannot reach our code directly.

## Proposed behavior / API
### Public interface
- `make security`: bandit plus pip-audit with no ignored advisories.

### Inputs / outputs
- No runtime behavior change. After merge the live venv needs `make sync` and a dashboard
  restart to pick up the new versions.

## Acceptance criteria
- AC1: `make security` passes with no `--ignore-vuln` in the `security` target and no
  `PILLOW_AUDIT_IGNORES` in the Makefile.
- AC2: pillow is pinned at 13.0.0 or later and streamlit at 1.66.1 or later in both
  requirements files, with matching pins for the packages they share.
- AC3: virtualenv is pinned at 21.14.9 or later in `requirements-dev.txt`.
- AC4: Only the streamlit, pillow, packaging and virtualenv pins change in the requirements
  files; `make lint` and `make test` pass and the dashboard starts on the new versions.

## Security considerations
- Dependency or supply-chain impact: clears 16 new and 8 previously ignored pillow advisories
  and two virtualenv advisories by upgrading; nothing is ignored any more. virtualenv is a
  dev-only dependency (via pre-commit).
- Input handling, auth/authz, secrets, data exposure, file system, network: no change.
- Security notes for reviewers/testers: any future advisory fails `make security` again; a
  new ignore would need its own spec.

## Edge cases
- A later streamlit re-caps pillow below 13: `make compile` cannot resolve, so the pins cannot
  silently fall back.
- An ignore reintroduced on a continuation line of the pip-audit recipe: still detected.
- Pins drift between the two requirements files: test fails.

## Test guidance
- AC1 -> `make security`; `tests/test_audit_ignores.py::TestNoAuditIgnores`
- AC2 -> `TestPins::test_pillow_at_fixed_version`, `test_streamlit_allows_pillow_13`,
  `test_shared_pins_agree`
- AC3 -> `TestPins::test_virtualenv_at_fixed_version`
- AC4 -> `git diff origin/main -- requirements*.txt` review; `make lint`; `make test`; dashboard
  health check (`/_stcore/health`) on the new versions

## Decision log
- 2026-10-22: Upgrade rather than extend packet 36's ignores, now that streamlit 1.66.1 allows
  pillow 13.
- 2026-10-22: Packet number 45, because the data audit plan already reserves 42-44.
