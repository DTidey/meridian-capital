# Changelog 2026-10-22

## Packet 45: pillow 13 and virtualenv advisories

- Upgraded instead of adding more pillow ignores: streamlit 1.66.1 lifts its `pillow<13` cap,
  which was the only reason for packet 36's ignores.
- packaging moved to 26.3 as well: `pip-compile -P virtualenv` alone kept virtualenv at 21.14.5,
  because pip-tools prefers existing pins and virtualenv 21.14.9+ needs `packaging>=26.3`.
- Numbered 45, not 42: the data audit plan (`docs/data-audit-2026-10-21.md`) already refers to
  packets 42-44 for fundamentals, combined score and positions.

## Packet 42: fundamentals and value inputs

- Market cap from Yahoo `marketCap`, not SEC cover pages: 80 universe tickers (multi-class
  filers such as BRK-B, GOOGL, CME, FOXA) have no single cover-page share count in XBRL.
- Market cap is stored, and shares derived as market cap / our close. Our closes are
  split-adjusted retroactively, so the derived count does not jump at a split and the 1.5x
  guard only catches glitches.
- The audit's MNST/APH "doubled shares" finding was wrong: both split 2:1 (2026-08-11,
  2026-09-03) after the SEC cover pages it compared against.
- Consumers read `operating_income` rather than rewriting the stored `ebit`, so no fundamentals
  re-fetch is needed.
- Quality computes ROE from raw figures, so ROE stability never mixes the old (minus one) and
  new stored formulas while `--recompute-ratios` is pending.
- Production dry run on 2026-10-21 data: BRK-B value score 93 to 42, CME 100 to 12, CMG gets a
  shareholder yield; neutral shareholder-yield scores fall from 150 to 10 tickers; LOW, MAR, YUM
  D/E rank 96/89/81 to 13. BRK-B's EV/EBIT and shareholder yield stay neutral because its
  2025-09-30 quarter is blank, so no contiguous trailing year exists (M10).
