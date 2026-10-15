# 2026-10-15: Risk score from severity mix (packet 31)

- **Follow-up from packet 30.** With the real Item 1A section the model labels 182 of 191
  companies HIGH overall, so the old label-based score was close to a constant inside the AI
  composite.
- **Average per-risk severity, not counts.** Counts of risks track how long the filing is
  (rank correlation with realised volatility -0.08); the average severity is the only candidate
  that both spreads scores out (53 distinct values) and leans the right way (+0.12, +0.16 within
  sector). The link is weak, which is why the prompt is not touched yet (option 2 if needed).
- **Score from cached data.** Cache keys are unchanged, so the next run rescores stored results
  at no API cost.
- **No score instead of the label when no risks are listed.** All three such cases (CLX, EIX,
  MS) say the risk section was missing or boilerplate; falling back to the label would score two
  of them 10 (lowest risk), repeating the packet 30 failure. Fixing their extraction is a
  backlog item.
- **Same 4-10 scale, no stretching.** Keeps the composite weights as they are. The new spread is
  narrower than earnings or filing, so risk counts for less in the composite; revisit once there
  is a track record.
- **Validated the re-rank before trusting it.** Rebuilding the combined scores with the old risk
  scores reproduced all 503 stored directions, so the 14 direction changes come from the new
  score alone.

# 2026-10-15: Remote database housekeeping (packet 32)

- **Credentials found in a public repo while scoping the Docker docs change.** `config.yaml`
  has carried the shared server's URL and password since 2026-09-24. Fixed by reading
  `DATABASE_URL` from `.env` only; the owner rotates the password after merge, since the old
  one stays in git history.
- **No fallback at all.** A config fallback is how the credentials got committed, and three
  entry points silently fell back to a local SQLite file, which would turn a missing setting
  into an empty database rather than an error.
- **Two entry points logged the full URL with its password.** Logs are git-ignored, so the
  exposure was local; now masked with `redacted_url`.
- **`utc_now()` stays naive.** Same values as `utcnow()`, so stored strings and the two
  datetime comparisons are untouched; moving to aware timestamps is a data migration.
- **Tests guard the docs.** Living docs are checked for Docker mentions and URLs with literal
  passwords, so the credentials can't quietly come back through a doc example.

# 2026-10-15: Risk factors extraction and risk score display (packet 33)

- **Measured on the whole universe, not just the three known failures.** Downloading every
  latest 10-K and running old and new extractors side by side found 12 more misses than the
  three in the backlog, and showed which heading forms matter (`Item 1.A`, `Item 1(a)`,
  "Part I." prefix, bare "Risk Factors").
- **Bare headings only as a fallback, and only with a real end.** A bare "Risk Factors" block is
  often a running page header; requiring a later item's title as the end, and trying it only
  when Item 1A fails, keeps all working filings' text the same.
- **Three working filings changed by a page-header block** (MLM, RMD, STZ) because the "Part I"
  prefix now matches a running header. Accepted: the risk text is identical.
- **Cached results not cleared.** They are keyed by filing, so the 15 newly found tickers update
  on expiry, a new 10-K, or the packet 34 model switch. Clearing them earlier is left to the
  owner.
- **AI card colours were always "short".** 1-10 scores were compared with the 0-100 thresholds;
  fixed while changing the number format, since it is the same line.

# 2026-10-15: GPT-6 for the scoring analyzers (packet 34)

- **Shadow test on final inputs.** Run after packets 30, 31 and 33 fixed the analyzer inputs and
  the risk score, so the comparison reflects the models, not input bugs. Production analyzer
  code with a stand-in cache: both model sets ran fresh, nothing was written.
- **Insider decided it.** gpt-4o-mini called most selling STRONG_SELL (score 1); Luna separates
  routine from meaningful selling, as the prompt asks. Earnings on Sol is more spread out;
  filing is similar.
- **Risk switched too, despite poor agreement.** Owner's call after seeing the numbers; Sol's
  risk score is the only one leaning the right way against realised volatility, on a small
  sample.
- **No cost-ceiling change.** About +10% per ticker; a full run is estimated at $12-13 of $25.
- **First run after merge is slow** (about twice as long; every analysis recomputed). `run_all.py`
  has no timeout, so the nightly job just takes longer once.
