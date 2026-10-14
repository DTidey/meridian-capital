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

# 2026-10-14: Ticker page additions (packet 18)

- **Sections in three modules by domain.** `page_ticker.render` was already about 380 lines;
  the new sections are separate functions it calls. Shared helpers moved to `ticker_common.py`
  to avoid a circular import, keeping their old names in `page_ticker` for existing tests.
- **Form 4 left out of the filings list.** It is 83% of `sec_filings` and would push out every
  10-K/10-Q/8-K; insider trades already have their own section.
- **Model output escaped, release text in a text area.** LLM text derived from filings is
  outside input. Escaping also stops `$` amounts rendering as LaTeX, which `st.markdown` does.
- **Generic renderer for analysis JSON.** The four analyzers' shapes differ; one renderer for
  dict/list/scalar values avoids four bespoke layouts and survives prompt changes.
- **Follow-up, not fixed: issuer-name ticker matching.** It misses some quarters (Berkshire's
  "CHUBB LTD SWITZ"), about 3% of holdings, which creates false "New" positions in the table
  and the factor. Options: set `OPENFIGI_API_KEY`, or store CUSIPs and reuse a mapping once
  resolved.

# 2026-10-14: OpenFIGI resolution (packet 19)

- **Prompted by the user adding an OpenFIGI key.** A dry run showed the existing fallback made
  things worse (46k rows, 26 flags) and still missed Chubb, so it was fixed before the key was
  used on the real database.
- **`ID_CINS` by first character.** CUSIPs for US/Canadian issuers start with a digit; CINS
  start with a country letter. OpenFIGI doesn't treat them as interchangeable.
- **No first-hit fallback.** Foreign listings come first for some issuers (Chubb: `AEX`), and
  a wrong ticker is worse than an unresolved one.
- **Non-universe holdings dropped at ingestion.** They were 86% of rows, unused, and drove most
  of the "3+ funds opening" flags.
- **Remaining one-quarter gaps are real trading.** All are at the three high-turnover funds;
  six sampled Bridgewater Q1 2026 gaps are absent from its actual filing.

# 2026-10-14: Filing viewer (packet 20)

- **The squashed text is an ingestion artefact.** `sec_data.py` and `transcripts.py` strip tags
  and collapse all whitespace, so line breaks and table structure are gone before any display.
- **Hybrid chosen by the user.** Exact EDGAR conversion for reading (numbers exact, any length,
  free) and AI only for summaries. AI reformatting of stored text was rejected: a 10-K exceeds
  gpt-4o-mini's output limit, and rebuilding squashed tables risks altered figures.
- **Fetch on demand, not re-ingest.** Changing ingestion would alter the AI analysts' inputs and
  require re-downloading 38k filings.
- **Columns merged where no row has real content in both.** The first prototype merged `$`
  cells per row and misaligned AAPL's 10-Q; EDGAR uses `colspan`, so the grid is expanded first.
- **Summaries kept 10 years in `analysis_results`.** Filings don't change; the 30-day analysis
  TTL would make the dashboard pay again for the same summary.
- **Figure check is advisory.** It flags numbers not found verbatim (after removing thousands
  separators), so a correct but derived figure is also flagged; the prompt asks for exact
  quotes to keep that rare.

# 2026-10-14: Ticker valuation (packet 21)

- **EV/EBIT, not EV/EBITDA.** `fundamentals` has no depreciation and amortisation; the value
  factor's `val_ev_ebitda_inv` already uses EBIT, so the dashboard labels it honestly.
- **TTM from quarterly rows.** Annual rows have 12 near-duplicate pairs from two sources with
  different debt and share counts; quarterly rows have none and are more current.
- **300-day span check.** Four rows aren't a trailing year if a quarter is missing (JPM's
  2025-03-31 row is empty). Contiguous quarter ends are about 9 months apart and one gap makes
  about 12, so the limit is 300 days; the first draft's 13 months let a gap through (Reviewer).

# 2026-10-14: CUSIP-first resolution (packet 22)

- **CUSIP before name.** A CUSIP names the exact security; issuer names can't tell share classes
  apart (both Alphabet classes are filed as "ALPHABET INC").
- **Name match kept as fallback** for no API key, OpenFIGI outages, and tickers OpenFIGI spells
  differently from the universe.

# 2026-10-14: Fundamentals integrity (packet 23)

- **Started as 12 duplicate annual rows; the share count was the bigger problem.** "Share
  Issued" includes treasury stock, overstating shares for banks and buyback-heavy companies
  (JPM by 54%), which skews market cap, the value factor's yields and the quality factor's
  dilution measure.
- **Positional pairing of statements.** Found by sampling 20 tickers: 7 of 40 statement sets
  had different periods, and BAC/GS were off by a whole quarter for every row.
- **Month-end keys.** Yahoo has used both fiscal ends and month-ends over time; month-end is
  the stable key and also what quarterly rows mostly already use.
- **Newest row wins in a merge**, never a mix of fields, because the two fetches define items
  differently (AAPL FY2025 debt 112.4B vs 98.7B).
- **Valuation follow-on.** After realignment, the newest BAC/GS quarter has income but no
  balance sheet, so valuation now takes balance items from the latest quarter that has them.

# 2026-10-14: Valuation TTM with an empty newest quarter (packet 24)

- **Per-field quarter selection.** Yahoo lists BAC's and GS's 2026-06-30 quarter with no figures;
  packet 23 stopped filling it from a neighbouring quarter, which exposed the strict "four latest,
  all present" rule. Skipping unreported quarters per field, with the 300-day span check, keeps
  gaps rejected.

# 2026-10-14: Date-based growth comparisons (packet 25)

- **Exact calendar-month match.** "4 rows back" became 15 months back for 10 tickers with a
  missing quarter, and the empty newest BAC/GS row (packet 23) neutralised their growth score.
  A missing period now gives a blank, which ranks as neutral, rather than a wrong comparison.
- **Acceleration is effectively always neutral**, before and after: it needs 24 months of
  quarters and Yahoo supplies about 5-7. Left as a known limitation.

# 2026-10-14: Acceleration trend and history preservation (packet 26)

- **Slope of YoY points**, the user's 4-to-8-values idea. YoY rather than QoQ so seasonality
  cancels; a per-quarter slope so tickers with 2, 3 or 4 points rank on one scale.
- **History preserved in the upsert**, not a separate table, so every reader benefits. Ratios
  only coalesce for a fully blank fetch; a partial fetch replaces them, so they never mix old
  and new figures (Reviewer).
- **Coverage 203 of 503 today**, rising each quarter as history accumulates.

# 2026-10-14: Reference docs refresh (packet 27)

- **Historical packet docs left alone.** They record each change as made; rewriting them would
  lose that. `docs/README.md` now says which docs are living and which are records.
- **ERD generated from the schema** so drift can be checked mechanically (5 tables and many
  columns had been missed by hand edits).
- **Test counts removed** from docs; every hard-coded count had gone stale.

# 2026-10-14: Reasoning model support (packet 28)

- **Stage 1 of the GPT-6 move, no model switched.** The user is considering GPT-6 Luna; GPT-4o
  remains available in the API (no retirement date found), so the move is staged: code ready
  (this), low-risk uses next, scoring analyzers only after a shadow test.
- **Prefix-based detection from config**, so the next reasoning family needs no code change.
- **Reasoning headroom added on top of the visible limit**, not replacing it, so a 600-token
  JARVIS reply isn't eaten by reasoning.
- **Cache mismatch is a miss**, then overwritten on the same key: no schema or key migration.

# 2026-10-14: GPT-6 for non-trading uses (packet 29)

- **Sector analysis moves too.** It reads `openai_model` but only feeds the research reports,
  not combined scores, so it counts as non-trading.
- **Pinning test for `analyzer_models`**, so the scoring move can't happen by a casual config
  edit before the shadow test.

# 2026-10-14: Analyzer input fixes (packet 30)

- **Found by the GPT-6 shadow test.** GPT-6 Luna refused to trust the filing table ("assets
  $494M vs receivables $16B"); gpt-4o-mini had scored such tables for months. The model
  comparison is only meaningful once inputs are right, so this comes before any switch.
- **Item 1A from the EDGAR original**, where headings and contents rows are distinguishable,
  rather than the whitespace-collapsed, 500k-capped stored text.
- **Versioned cache keys instead of deleting rows**: reversible, and old rows age out.
- **Risk score design is weak**: once the model reads real risk factors, nearly everything is
  HIGH. Left for a separate decision.
