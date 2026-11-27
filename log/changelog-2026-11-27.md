# Changelog 2026-11-27

## Packet 68: skip the nightly run on US market holidays

- **Alpaca calendar, no new dependency.** alpaca-py and the keys are already used by Book sync
  and Layer 6; the broker's calendar includes special closures a rules-based holiday list
  misses. `exchange_calendars` would save one HTTP call but adds holiday tables to keep current.
- **Fail open.** A wrong skip silently loses a trading day; a wrong run on a holiday is caught by
  the Layer 2 SPY guard, exactly as on 2026-11-26. So every error in the check (missing keys,
  network, odd response, unparseable `scoring.score_date`) prints a WARNING and runs.
- **Response validation.** An empty list means holiday; a list containing the day means open; a
  list with only other dates, or anything without `.date`, is treated as a failure, so a
  malformed answer can never be read as "closed".
- **Weekends locally.** No network for manual weekend runs, and the cron never hits them.
- **Score date shared, not duplicated.** `resolve_score_date` moved out of `run_scoring.py` into
  `factors/score_date.py`; `run_all.py` passes `cli_date=None` because it never forwards `--date`
  to Layer 2. Layers 3-6 still use their own `date.today()`; they already agree when
  `scoring.score_date` is null, and changing them is out of scope.
- **`load_dotenv` in `run_all.py`.** Needed for `get_client`. Child layers load the same `.env`
  and `load_dotenv` does not override, so their environment is unchanged.
- **`make security` red on main, not fixed here.** pip-audit reports virtualenv 21.14.12
  (PYSEC-2026-4402, fix 21.14.13, dev only). Unrelated; left for a dependency packet.

## Packet 69: virtualenv PYSEC-2026-4402

- Pinned with `pip-compile --upgrade-package virtualenv==21.14.13` on `requirements-dev.in`
  (the Makefile `compile` flags: no hashes, default resolver, no constraints), so only the
  virtualenv line moved. `requirements.txt` does not pin virtualenv.
- Compiled and tested under Python 3.12 (the lock header's version), in a throwaway venv under
  `/tmp`, not `.venv`, because `.venv` is a symlink to the shared venv.
- Numbered 69: 68 is in use on the open `hermes/holiday-skip` branch (PR #152).
