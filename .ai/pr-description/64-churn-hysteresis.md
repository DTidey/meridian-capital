Spec: `docs/specs/64-churn-hysteresis.md`

- [x] AC1 `select_book` keeps held same-side candidates with side rank <= `incumbent_exit_rank` (best first, capped at `num_*`), then fills with the best remaining candidates; side size is `min(num_*, candidates)`
- [x] AC2 held names beyond the exit rank, out of band, or flipped are not incumbents and `generate_trades` closes them in full (SELL / COVER)
- [x] AC3 exit rank = `num_*` (or key absent) gives the top-N book, weights and trades; an empty book gives the top N
- [x] AC4 side ranks deterministic: ties break by ticker; independent of row order
- [x] AC5 both optimisers select through `select_book`; downstream steps unchanged; books sum to gross
- [x] AC6 scoring weights are the linear VIX-band blend on the mean of the last `vix_smoothing_days` closes, band edges from config, sum to 1, equal the regime weights at full edges and base weights in the neutral zone
- [x] AC7 `regime_state` stores `vix_smoothed`, `high_vol_share`, `low_vol_share`; label by share >= 0.5; no VIX gives base weights, NORMAL and a warning; `regime_conditional_weights: false` gives base weights
- [x] AC8 `python -m portfolio.churn_replay` runs read-only and prints exits, exits by reason, round trips, turnover, mean side rank, top-10 left out and composite Spearman per variant
- [x] AC9 invalid `incumbent_exit_rank`, band edges or `vix_smoothing_days` raise ValueError naming the key before any DB work

## Summary

Reduces Layer 4 churn (12 names opened on 11-06 closed in full on 11-09) with two dampers,
tuned together from a read-only replay of the stored score history:

- **Incumbent buffer** (`portfolio/book_selection.py`): new names enter at the top 20 per
  side; a held name stays while it is a candidate on the same side with side rank <= 35. Book
  size is fixed at 20/20, so sizing, tilt, bounds, sector and beta steps are unchanged. NEUTRAL
  and flipped names close as before. Used by both the conviction and MVO optimisers; the Layer
  4 log prints buffered names and closure reasons per side.
- **Regime weight blend** (`factors/regime_weights.py`): weights blend linearly between base
  and regime weights across VIX bands (low_vol 13-17, high_vol 20-30) on the mean of the last
  5 closes, instead of switching on one close at 15/25. Stateless (pure function of stored
  closes). `regime_state` gains `vix_smoothed`, `high_vol_share`, `low_vol_share`. The unused
  `vix_below` / `vix_above` keys are replaced by validated band edges.
- **Replay** (`portfolio/churn_replay.py`, `portfolio/churn_sim.py`): read-only
  (`SET TRANSACTION READ ONLY`), rescoring stored factor and AI scores with the current Layer
  2/3 code; band exits are counted apart from rank exits for the deferred band-exit decision.

Out of this packet (David, 2026-11-10): rotation credit in the turnover budget and a buffer
for band exits; both are backlog items with the replay evidence. The 11-04 VIX print (16.61)
is real.

## Replay results

Read-only against production, 2026-11-10. Idealised 20/20 book, equal weight, full fills.

Short window, 2026-10-27 to 11-09 (9 nights):

| Scoring | Exit rank | Exits | Rank / band exits | Round trips 1 / 2 nights | Turnover / night | Mean side rank | Top-10 left out |
|---|---|---|---|---|---|---|---|
| stored (today) | 20 | 92 | 80 / 12 | 38 / 61 | 38.5% | 10.5 | 0.00 |
| stored | 35 | 41 | 34 / 7 | 14 / 18 | 17.2% | 12.0 | 0.15 |
| blend 5d low 13-17 | 20 | 87 | 83 / 4 | 42 / 60 | 36.8% | 10.5 | 0.00 |
| **blend 5d low 13-17** | **35** | **32** | **29 / 3** | **11 / 14** | **13.5%** | **12.1** | **0.15** |
| blend 5d low 13-17 | 40 | 26 | 23 / 3 | 7 / 9 | 11.1% | 13.2 | 0.40 |

Long window, 2026-05-05 to 11-09 (120 nights; baseline is today's switch recomputed, because
stored combined scores before late October follow pre-packet-43 rules):

| Scoring | Exit rank | Exits | Rank / band exits | Round trips 1 / 2 nights | Turnover / night | Mean side rank | Top-10 left out |
|---|---|---|---|---|---|---|---|
| switch (today) | 20 | 1043 | 971 / 72 | 484 / 645 | 32.7% | 10.5 | 0.00 |
| blend 5d low 13-17 | 25 | 659 | 598 / 61 | 195 / 285 | 20.7% | 10.9 | 0.03 |
| blend 5d low 13-17 | 30 | 471 | 411 / 60 | 95 / 141 | 14.8% | 11.6 | 0.20 |
| **blend 5d low 13-17** | **35** | **358** | **300 / 58** | **47 / 77** | **11.2%** | **12.4** | **0.31** |
| blend 5d low 13-17 | 40 | 304 | 239 / 65 | 40 / 59 | 9.5% | 13.2 | 0.47 |
| blend 5d low 12-18 | 35 | 365 | 310 / 55 | 54 / 84 | 11.4% | 12.4 | 0.29 |
| blend 3d low 13-17 | 35 | 360 | 301 / 59 | 49 / 76 | 11.3% | 12.4 | 0.32 |
| blend 10d low 13-17 | 35 | 376 | 322 / 54 | 49 / 86 | 11.8% | 12.3 | 0.28 |

Composite day-over-day Spearman, short window (mean / min): stored 0.947 / 0.802; blend 5d
0.963 / 0.879. Largest one-night high_vol share change: 1.0 today, 0.12 with 5 days.
Rescoring check: for the short window the recomputed switch equals the stored results on
every metric. Full grid and stability table in the spec (Replay results).

## Chosen settings

- `portfolio.incumbent_exit_rank: 35`: elbow of the curve; 30 -> 35 removes 64 round trips,
  35 -> 40 only 18 while top-10 names left out rise 0.31 -> 0.47 per side-night. Round trips
  do not reach zero at any tested rank.
- `scoring.regime_weights.vix_smoothing_days: 5`: churn equal to 3 and 10 days; largest
  one-night share change 0.12 (3 days 0.19); 10 days hardly reacts to the 11-05 spike.
- low_vol band 13-17 (spec draft had 12-18): churn equal across bands; 12-18 would keep the
  low_vol tilt partly on for 99 of 120 nights because VIX sat at 15-17 all summer; 13-17
  keeps the 15 centre and has the smallest one-night change.
- high_vol band 20-30 unchanged from the spec (one high-VIX episode in the history).

## First-night effect

- Already-stored scores are not rewritten; the change starts with the next nightly run.
- Composite values shift once: on the 11-09 data the 5-day mean VIX is 22.24, high_vol share
  0.22, label NORMAL (momentum 0.177, quality 0.215, value 0.163); composite Spearman vs the
  stored 11-09 composite 0.995; 9 names leave the candidate lists and 10 enter. The share
  then moves by at most about 0.12 a night and returns to 0 once the 11-05/11-06 closes leave
  the window (11-13 score date) unless VIX stays above 20.
- `regime_state` gains three nullable columns on the first run.
- Layer 4: with the 11-09 scores and the current broker book, today's rule closes 12 held
  names; with the buffer and blend, 9 (7 beyond rank 35, 2 out of band), keeping 4 at side
  rank 21-35.

## Validation

- `make lint`: pass.
- `make test`: 1770 passed (4m37s).
- `make security`: pass (bandit no issues; pip-audit no known vulnerabilities).
- Replay run read-only against production (`SET TRANSACTION READ ONLY`, SELECT only); no DB
  writes, no broker calls.

## Security

Reviewed: no auth, secret or network change. The replay opens a read-only transaction
(refuses dialects without a read-only mode) and prints aggregates only. Config values are
validated at start. Schema change is three nullable `ADD COLUMN`s on `regime_state`.

## Open risks

- The replay book is idealised (equal weights, full fills, no turnover budget), so live
  turnover will differ; watch `position_approvals` round trips for two weeks.
- The buffer holds names at rank 21-35 that the old rule would close; they get the bottom
  weight and no conviction tilt. Layer 5 and the circuit breaker can still close them.
- Rotation credit is not in this packet, so a high-churn night can still trim replacements.
