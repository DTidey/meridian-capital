# Test Plan: filelock-4

Path: `docs/test-plans/47-filelock-4.md`

## What changed
- `requirements-dev.txt`: filelock 3.29.4 -> 4.2.4.
- `tests/test_audit_ignores.py`: filelock pin test and version-comparison cases.

## Acceptance criteria coverage
- AC1: `make security` ("No known vulnerabilities found");
  `TestNoAuditIgnores::test_security_target_has_no_ignores`
- AC2: `TestPins::test_filelock_at_fixed_version`
- AC3: `git diff origin/main -- requirements*.txt` shows only filelock; `make lint`;
  `make test`; `make precommit` with an empty `PRE_COMMIT_HOME`, so virtualenv builds the hook
  environments on filelock 4

## Edge cases
- From spec: filelock pinned in `requirements.txt` later (`test_filelock_at_fixed_version`
  checks both files); numeric version comparison (`TestVersionParsing`).
- Additional adversarial cases:
  - 4.10.0 passes the 4.2.1 floor although it sorts before "4.2.1" as a string
    (`TestVersionParsing::test_versions_compare_numerically`)
  - 4.2.0, one patch short of the fix, and the old 3.29.4 fail the floor (same test)
  - A filelock pin only in a `# via` comment does not count
    (`TestAdversarial::test_commented_pin_does_not_count` covers the shared parser)

## Notes
- Checked against `main`'s pins: `test_filelock_at_fixed_version` fails there (3.29.4).
- pip-audit results depend on the advisory database on the day; a new advisory fails CI, which
  is intended.

## Commands
```bash
make lint
make test
make security
make precommit
```
