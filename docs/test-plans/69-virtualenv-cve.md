# Test Plan: virtualenv-cve

Path: `docs/test-plans/69-virtualenv-cve.md`

## What changed
- `requirements-dev.txt`: virtualenv 21.14.12 -> 21.14.13.
- `tests/test_audit_ignores.py`: virtualenv floor raised to 21.14.13; version-comparison and
  adversarial cases.

## Acceptance criteria coverage
- AC1: `make security` ("No known vulnerabilities found");
  `TestNoAuditIgnores::test_security_target_has_no_ignores`
- AC2: `TestPins::test_virtualenv_at_fixed_version`
- AC3: `git diff origin/main -- requirements*.txt` shows only virtualenv; `make lint`;
  `make test`

## Edge cases
- From spec: numeric version comparison (`TestVersionParsing::test_versions_compare_numerically`).
- Additional adversarial cases:
  - 21.14.12 (the vulnerable pin) and 21.14.9 fail the 21.14.13 floor; 21.14.13 and 21.15.0 pass
    it (`TestVersionParsing::test_versions_compare_numerically`)
  - A lookalike package (`virtualenv-clone==21.14.13`) does not satisfy the virtualenv pin
    (`TestAdversarial::test_virtualenv_lookalike_does_not_count`)
  - A `virtualenv==21.14.13` inside a pip-compile `# via` comment does not count
    (`TestAdversarial::test_virtualenv_pin_in_via_comment_does_not_count`)

## Notes
- Checked against `main`'s pins: `test_virtualenv_at_fixed_version` fails there (21.14.12).
- pip-audit results depend on the advisory database on the day; a new advisory fails CI, which
  is intended.

## Commands
```bash
make lint
make test
make security
```
