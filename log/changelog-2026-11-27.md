# Changelog 2026-11-27

## Packet 69: virtualenv PYSEC-2026-4402

- Pinned with `pip-compile --upgrade-package virtualenv==21.14.13` on `requirements-dev.in`
  (the Makefile `compile` flags: no hashes, default resolver, no constraints), so only the
  virtualenv line moved. `requirements.txt` does not pin virtualenv.
- Compiled and tested under Python 3.12 (the lock header's version), in a throwaway venv under
  `/tmp`, not `.venv`, because `.venv` is a symlink to the shared venv.
- Numbered 69: 68 is in use on the open `hermes/holiday-skip` branch (PR #152).
