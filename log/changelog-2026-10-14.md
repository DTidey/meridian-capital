# 2026-10-14: 13F ingestion fixes (packet 17)

- **Found while starting packet 18 (institutional holdings on the Ticker page).** Stored AAPL
  rows were checked against Point72's actual Q2 2026 13F before building a display on them. They
  didn't match, so the data was fixed first; the display follows in packet 18.
- **Amendments skipped, not merged.** A 13F-HR/A can restate the whole filing or add only
  confidential positions; telling them apart needs `primary_doc.xml`. Originals give one
  consistent filing per fund per quarter. Cost: positions disclosed only by amendment are missed.
- **Each fund compared with itself, with carry-forward.** Funds file over about six weeks. The
  old whole-quarter totals made an early-filed quarter look like the late filers had sold out,
  and the factor reads only the latest quarter, so this mattered most.
- **First loaded filing gives no "new positions".** Previously every holding in the oldest
  loaded quarter counted as new, which could raise false simultaneous-open flags.
- **$ thousands detected per filing by median implied price < $1.** Found in the dry run:
  Baupost still files in thousands. Median, not per row, so one odd row can't flip a filing;
  $1 works because no S&P 500 stock trades near it.
- **Legacy rows purged automatically by null `filed_date`** rather than a manual flag, so the
  next normal 13F run repairs the database with no operator step.
- **Fund name "Appaloosa Management" kept** although the filer is Appaloosa LP, so existing
  config and any references by name stay valid.
- **Scoring loader summarises from holdings, gated by `filed_date`.** Dating rows by quarter
  end made the stored summary leak filings into past-date scoring runs (found by the Reviewer).
  The summary rules are a pure function, so the loader reuses them on the filtered rows rather
  than storing a second as-of table.
