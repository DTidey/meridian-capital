# Changelog 2026-10-24

## Packet 47: filelock advisories

- Upgraded across a major version (3.29.4 -> 4.2.4) rather than ignoring: `pip-compile -P
  filelock` resolved with no other pin moving, so no parent caps it below 4.
- Checked the consumers directly, since the test suite never imports filelock: pip-audit ran
  clean and `make precommit` rebuilt every hook environment through virtualenv from an empty
  `PRE_COMMIT_HOME`.
- Numbered 47: the data audit plan still reserves 43 and 44.
