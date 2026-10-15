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
