# soupsieve Advisories

**Spec file:** `docs/specs/67-soupsieve-4.md`
**Spec slug:** soupsieve-4
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-13

## Problem statement
`make security` fails for every PR: pip-audit reports two soupsieve 2.9 advisories,
CVE-2026-126192 (fixed in 2.10) and CVE-2026-126190 (fixed in 4.0). soupsieve is an indirect
dependency, pulled in only by beautifulsoup4. Bandit is clean.

## Scope
In scope:
- `requirements.txt`, `requirements-dev.txt`: soupsieve 2.9 -> 4.0
  (`pip-compile -P soupsieve==4.0`).
- `tests/test_audit_ignores.py`: tests that keep soupsieve at the fixed version in both files.

Out of scope / non-goals:
- Upgrading any other dependency, including beautifulsoup4.
- Ignoring the advisories instead (packet 45 removed all ignores).

## Assumptions
- 4.0 is the lowest release that clears both advisories: pip-audit reports CVE-2026-126190
  against 2.10 and 3.0.4, and nothing against 4.0.
- beautifulsoup4 4.15.0 (current pin) declares `soupsieve>=1.6.1`, so it needs no bump.
- soupsieve 4.0 declares `Requires-Python >=3.11.5`; the project and CI run Python 3.12.
- Our code never calls CSS selectors (`select`, `select_one`, `css`); `data/edgar_docs.py` uses
  only `find` and `find_all`. bs4 still imports soupsieve on import.

## Proposed behavior / API
### Public interface
- `make security`: passes again; no other command changes.

### Inputs / outputs
- No runtime behavior change. The live venv picks up the pin with `make sync`; the dashboard
  imports bs4 through the filing viewer, so restart it after syncing.

## Acceptance criteria
- AC1: `make security` passes with no ignored advisories.
- AC2: soupsieve is pinned at 4.0 or later in both `requirements.txt` and
  `requirements-dev.txt`, with the same version in each.
- AC3: Only the soupsieve pin changes in the requirements files; `make lint` and `make test`
  pass on the new pin.

## Security considerations
- Dependency or supply-chain impact: clears two soupsieve advisories by upgrading; nothing is
  ignored. soupsieve is a runtime dependency (via beautifulsoup4, used for EDGAR filing HTML).
- Input handling, auth/authz, secrets, data exposure, file system, network: no change.
- Security notes for reviewers/testers: a two-major-version jump (2.9 -> 4.0) of a transitive
  dependency; no code path uses CSS selectors, and the EDGAR parsing tests pass on 4.0.

## Edge cases
- A later parent caps `soupsieve<4`: `make compile` cannot resolve, so the pin cannot silently
  fall back.
- The two requirements files drift apart: the pins-agree test fails.
- 2.10 sorts after 2.9 only numerically; a two-part version such as `4.0` must meet the floor.

## Test guidance
- AC1 -> `make security`; `tests/test_audit_ignores.py::TestNoAuditIgnores`
- AC2 -> `TestPins::test_soupsieve_at_fixed_version`, `TestPins::test_soupsieve_pins_agree`
- AC3 -> `git diff origin/main -- requirements*.txt` review; `make lint`; `make test`

## Decision log
- 2026-11-13: Upgrade rather than ignore, as packet 45 settled.
- 2026-11-13: 4.0, not 2.10: 2.10 still carries CVE-2026-126190, and 3.x does too.
- 2026-11-13: Packet number 67, because 66 is in use on the open pretrade-sector-gross branch.
