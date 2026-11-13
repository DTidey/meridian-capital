# Changelog 2026-11-13

## Packet 67: soupsieve advisories

- 4.0, not 2.10: pip-audit lists a different fix version per advisory (2.10 and 4.0). Auditing
  single-pin files showed 2.10 and 3.0.4 still carry CVE-2026-126190, so 4.0 is the lowest
  release that clears both.
- Pinned with `pip-compile -P soupsieve==4.0` on both `.in` files, so only the soupsieve line
  moved; beautifulsoup4 4.15.0 accepts any `soupsieve>=1.6.1`, so it stays.
- Tested in a throwaway venv under `/tmp`, not `.venv`, because `.venv` is shared with another
  running job.
- Numbered 67: 66 is in use on the open `hermes/fix-pretrade-sector-gross` branch.
