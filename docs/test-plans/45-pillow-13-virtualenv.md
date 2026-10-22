# Test Plan: pillow-13-virtualenv

Path: `docs/test-plans/45-pillow-13-virtualenv.md`

## What changed
- `requirements.in`: `streamlit>=1.66.1`.
- `requirements.txt`, `requirements-dev.txt`: streamlit 1.66.1, pillow 13.0.0, packaging 26.3;
  virtualenv 21.14.12 (dev).
- `Makefile`: `PILLOW_AUDIT_IGNORES` and its `--ignore-vuln` arguments removed.
- `tests/test_audit_ignores.py`: packet 36 time-box tests replaced.

## Acceptance criteria coverage
- AC1: `make security` ("No known vulnerabilities found");
  `TestNoAuditIgnores::test_security_target_has_no_ignores`,
  `test_pillow_ignore_variable_removed`
- AC2: `TestPins::test_pillow_at_fixed_version`, `test_streamlit_allows_pillow_13`,
  `test_shared_pins_agree`
- AC3: `TestPins::test_virtualenv_at_fixed_version`
- AC4: `git diff origin/main -- requirements*.txt` shows only the four packages; `make lint`;
  `make test`; dashboard started on port 8599 and `/_stcore/health` returned `ok`; matplotlib
  saved a PNG with pillow 13

## Edge cases
- From spec: pins drift between files (`test_shared_pins_agree`); an ignore on a continuation
  line (`test_ignore_on_continuation_line_is_caught`).
- Additional adversarial cases:
  - An `--ignore-vuln` on a backslash continuation of the pip-audit line is still found
    (`TestAdversarial::test_ignore_on_continuation_line_is_caught`)
  - A similarly named package (`pillow-heif`) does not satisfy the pillow pin
    (`test_similarly_named_package_does_not_count`)
  - A pin inside a pip-compile `# via` comment does not count (`test_commented_pin_does_not_count`)
  - Versions compare numerically, so 21.14.12 is newer than 21.14.9
    (`TestVersionParsing::test_versions_compare_numerically`)

## Notes
- Checked against `main`'s Makefile: the pip-audit line still carries `--ignore-vuln`, so
  `test_security_target_has_no_ignores` fails there.
- pip-audit results depend on the advisory database on the day; a new advisory fails CI, which
  is intended.

## Commands
```bash
make lint
make test
make security
```
