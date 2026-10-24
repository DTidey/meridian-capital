# Changelog 2026-10-24

## Packet 47: filelock advisories

- Upgraded across a major version (3.29.4 -> 4.2.4) rather than ignoring: `pip-compile -P
  filelock` resolved with no other pin moving, so no parent caps it below 4.
- Checked the consumers directly, since the test suite never imports filelock: pip-audit ran
  clean and `make precommit` rebuilt every hook environment through virtualenv from an empty
  `PRE_COMMIT_HOME`.
- Numbered 47: the data audit plan still reserves 43 and 44.

## Packet 43: combined score

- AI reorders, it does not select: the owner chose this over an AI veto (demote a candidate
  when the blend crosses 50) and over keeping the sector re-rank. Any re-rank across all 503
  names keeps a fixed share at each extreme, so a candidate AI marks down is always replaced by
  an unreviewed name.
- Ranks within the side, not quant composite + AI percentile: inside the LONG candidates the
  quant composite spans only 80-100 while an AI percentile spans 0-100, so the nominal 60/40
  blend would let AI drive about three quarters of the order (2026-10-23: 7 of the top 20
  longs matched quant's top 20, against 10 with within-side ranks).
- Mid-rank `(rank - 0.5) / n`: ranks average exactly 50, the neutral rank given to a candidate
  with no AI result, and a lone candidate ranks 50.
- Found while scoping: the short book's conviction tilt rewards the weakest shorts. Backlog
  item; own packet.
