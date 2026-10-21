# Pillow and certifi Advisories

**Spec file:** `docs/specs/36-pillow-certifi-advisories.md`
**Spec slug:** pillow-certifi-advisories
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
`make security` started failing on 2026-10-21 for every PR, including Dependabot PRs #83-#88:
pip-audit reports eight new pillow 12.3.0 advisories (fixed only in 13.0.0) and one certifi
2026.6.17 advisory (fixed in 2026.10.19). Both are indirect dependencies. certifi can move, but
pillow cannot: streamlit requires `pillow<13`, in the pinned 1.65.0 and the latest 1.66.0.
Until CI is green no PR can merge.

## Scope
In scope:
- certifi 2026.6.17 -> 2026.10.19 in `requirements.txt` and `requirements-dev.txt`.
- `Makefile`: `PILLOW_AUDIT_IGNORES` lists the eight pillow advisories by GHSA ID; the
  `security` target passes them to pip-audit as `--ignore-vuln`.
- A test that fails once pillow is pinned at 13 or later while the ignores remain.
- Backlog item to remove the ignores when streamlit allows pillow 13.

Out of scope / non-goals:
- Overriding streamlit's pillow cap.
- The Dependabot PRs themselves (#83-#88); they pass after a rebase on this change.

## Assumptions
- Exposure is nil: every advisory needs an attacker-supplied image opened or re-saved by Pillow
  (PDF append, GIF, PPM/PGM with `ImageMorph`, TIFF, EPS). No source file imports PIL; pillow is
  installed only as a dependency of matplotlib and streamlit, no code path decodes images (charts
  are plotly), and the dashboard has no image upload.
- pip-audit matches `--ignore-vuln` against advisory aliases, so GHSA IDs work although it
  prints CVE IDs (checked: "No known vulnerabilities found, 8 ignored").

## Proposed behavior / API
### Public interface
- `make security`: same checks, minus the eight named pillow advisories.

### Inputs / outputs
- Ignored advisories (pillow, fixed in 13.0.0): GHSA-p4wg-p73j-c528, GHSA-65qm-jvmh-6pqg,
  GHSA-58r4-6rv3-gfh8, GHSA-pchr-8j38-3x4p, GHSA-q45j-58j2-45rg, GHSA-462x-pgg6-55fp,
  GHSA-c6hw-5grf-ff96, GHSA-vx6w-vv9f-6gvg.
- Any other advisory, including a new pillow one, still fails `make security`.

## Acceptance criteria
- AC1: `make security` passes, and certifi is pinned at 2026.10.19 or later in both
  requirements files.
- AC2: pip-audit ignores only the advisories named in `PILLOW_AUDIT_IGNORES`, each one of the
  GHSA IDs listed in this spec, with no other `--ignore-vuln` in the `security` target.
- AC3: A test fails when pillow is pinned at 13 or later and `PILLOW_AUDIT_IGNORES` is not empty.
- AC4: No source file or `run_*.py` entry point imports PIL.
- AC5: Only the certifi pins change in the requirements files, and `make lint` and `make test`
  pass.

## Security considerations
- Dependency or supply-chain impact: certifi update adds the latest CA bundle. Eight pillow
  advisories are accepted until streamlit allows pillow 13.
- Input handling or injection risk: the accepted advisories need untrusted image input; the code
  opens no images (AC4 guards this), so they are not reachable.
- Auth/authz, secrets, data exposure, file system, network: no change.
- Security notes for reviewers/testers: the ignores are named, not blanket, and a test forces
  their removal once pillow 13 is pinned.

## Edge cases
- A new pillow advisory: not in the list, so CI fails as before.
- Pillow pinned at 13 later (by `make compile` or Dependabot): AC3 test fails until the ignores
  are removed.
- requirements.txt and requirements-dev.txt pin different pillow versions: test fails.

## Test guidance
- AC1 -> `make security`; `tests/test_audit_ignores.py::TestCertifi`
- AC2 -> `tests/test_audit_ignores.py::TestIgnoreList`
- AC3 -> `tests/test_audit_ignores.py::TestIgnoresAreTimeBoxed`
- AC4 -> `tests/test_audit_ignores.py::TestExposure`
- AC5 -> `git diff main -- requirements*.txt` review; `make lint`; `make test`

## Decision log
- 2026-10-21: Named, time-boxed ignores rather than overriding streamlit's cap (owner's call):
  the advisories are unreachable here, and running streamlit on a pillow it excludes is untested.
- 2026-10-21: GHSA IDs rather than the CVE IDs pip-audit prints; GHSA IDs are the advisories'
  own identifiers and the same in every database pip-audit can query.
- 2026-10-21: `pip-compile --upgrade-package certifi` rather than a floor in `requirements.in`,
  as packet 10 did for indirect pins; certifi is not a direct dependency.
