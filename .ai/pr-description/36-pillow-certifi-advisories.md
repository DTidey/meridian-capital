## Summary

Unblocks CI, which fails `make security` on every PR (including Dependabot #83-#88) since new
pip-audit advisories appeared on 2026-10-21.

- certifi 2026.6.17 -> 2026.10.19 (fixes its advisory).
- Eight pillow 12.3.0 advisories are fixed only in 13.0.0, which streamlit blocks (`pillow<13`
  in 1.65.0 and the latest 1.66.0). They are ignored by name in `make security`
  (`PILLOW_AUDIT_IGNORES`). All need an attacker-supplied image; no source imports PIL, and a
  test keeps it that way.
- A test fails once pillow is pinned at 13 or later, so the ignores get removed then. Backlog
  item added.

## Spec

- Spec: `docs/specs/36-pillow-certifi-advisories.md`
- Test plan: `docs/test-plans/36-pillow-certifi-advisories.md`
- PR draft path: `.ai/pr-description/36-pillow-certifi-advisories.md`

## Acceptance Criteria

- [x] AC1: `make security` passes; certifi at 2026.10.19 in both requirements files.
- [x] AC2: Only the named pillow GHSA advisories are ignored.
- [x] AC3: A test fails once pillow 13 is pinned and the ignores remain.
- [x] AC4: No source file imports PIL.
- [x] AC5: Only the certifi pins change; lint and tests pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/36-pillow-certifi-advisories.md` (eight
  pillow advisories accepted as unreachable until streamlit allows pillow 13; certifi updated)

## Validation

- New tests fail against `main`'s Makefile and requirements, and the time-box test fails with
  pillow set to 13.0.0
- `make lint`: pass
- `make security`: pass ("No known vulnerabilities found, 8 ignored")
- `make test`: 1055 passed

Reviewer pass (fresh subagent): no blockers or Important items; suggestions applied (ignore
list tied to the spec's IDs, PIL scan cannot pass on zero files, matplotlib wording corrected).

## Risks and follow-ups

- The pillow advisories stay open until streamlit drops its cap; tracked in `docs/backlog.md`.
- Dependabot PRs #83-#88 need a rebase after merge to pick up the fix.
