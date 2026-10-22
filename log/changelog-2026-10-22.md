# Changelog 2026-10-22

## Packet 45: pillow 13 and virtualenv advisories

- Upgraded instead of adding more pillow ignores: streamlit 1.66.1 lifts its `pillow<13` cap,
  which was the only reason for packet 36's ignores.
- packaging moved to 26.3 as well: `pip-compile -P virtualenv` alone kept virtualenv at 21.14.5,
  because pip-tools prefers existing pins and virtualenv 21.14.9+ needs `packaging>=26.3`.
- Numbered 45, not 42: the data audit plan (`docs/data-audit-2026-10-21.md`) already refers to
  packets 42-44 for fundamentals, combined score and positions.
