# 2026-10-21: Pillow and certifi advisories (packet 36)

- **CI broke without a code change.** New pip-audit advisories against pillow 12.3.0 and certifi
  2026.6.17 failed `make security` on every PR, so the Dependabot batch (#83-#88) could not
  merge; `main` would fail the same way on its next run.
- **certifi fixed, pillow ignored by name.** Pillow's only fix is 13.0.0, and streamlit (pinned
  and latest) requires `pillow<13`. Owner chose named, time-boxed ignores over forcing pillow 13
  under streamlit.
- **Why the ignores are safe.** Every advisory needs Pillow to open or re-save an
  attacker-supplied image (PDF, GIF, PPM, TIFF, EPS). Our code never imports PIL or decodes
  images (charts are plotly); pillow is only a matplotlib and streamlit dependency. A test fails
  if a PIL import appears.
- **Time box is a test, not a reminder.** Once pillow 13 is pinned, a test fails until the
  ignores are removed, so they cannot outlive the cap.

# 2026-10-21: Insider analysis cache key (packet 37)

- **Insider re-ran nightly.** Its cache key was `{ticker}_{score_date}`, so it never hit on a
  new day: ~137 calls a night, ~90% for the same tickers as the night before. Filing, risk and
  earnings are keyed on their source document and were fine.
- **Key is a hash of the model's input.** The formatted transaction and cluster text is exactly
  what the model sees per ticker, so any change that matters changes the key, and nothing else
  does. Listing transaction ids would miss the cluster flag.
- **Window dates dropped from the prompt.** They changed daily for no informational gain; each
  transaction line keeps its date, and the 30-day cache TTL bounds reuse of one result.
- **Same-date ties ordered by id.** Without a tiebreak the database may return ties in any
  order, which would flip the hash.
- **Sector analysis left uncached** (owner's call): its inputs change with daily scores.

# 2026-10-21: Price history integrity (packet 38)

- **Freshness gate looks at SPY on or before the score date.** Checking only the latest SPY
  bar overall would fail every historical `--date` rerun; AC9 asks only whether a bar dated
  `score_date` exists, which "latest on or before" answers and still names both dates.
- **"Today" for the end date is the New York date.** At 22:00 UTC the New York and UTC dates
  agree; after UTC midnight the New York date is still the session that just closed.
- **Re-adjusted incremental bars are not stored before the full re-download.** Storing them
  would mix two adjustment bases if the full fetch then failed; AC6 keeps old rows unchanged.
- **One re-download path for both backends.** Fetching is split from the checks, so the
  Polygon path gets the same checks without its own copy; its adjusted-close issue is in
  the backlog.
- **Gap check is a count, as the spec says.** Per ticker: rows vs calendar dates between first
  and last row (one grouped query plus a bisect), not a per-date set comparison.
- **Delete and insert committed together, rollback on error, then re-raise.** `_upsert_prices`
  commits per call, so the replace path does its own single commit; a failure is not swallowed.

