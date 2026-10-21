## Summary

The insider analyzer called the AI for nearly the same tickers every night: its cache key was
`{ticker}_{score_date}`, so it never hit on a new day. Run logs 2026-10-15 to 2026-10-20 show
136-140 insider calls a night, 122-131 for the same tickers as the night before (140 of 169
calls on 2026-10-20).

- Key is now `{ticker}_{sha256(transaction text)[:16]}_v2`, hashing the exact transaction and
  cluster text sent to the model. A new call happens only when a Form 4 transaction or cluster
  flag enters or leaves the 90-day window (or the model changes, or the 30-day TTL expires).
- Prompt window line no longer carries the dates (they changed daily); each transaction keeps
  its date.
- Same-date transactions are ordered by `id`, so the hash does not depend on DB row order.
- Sector analysis unchanged and still runs nightly (owner's call).

## Spec

- Spec: `docs/specs/37-insider-cache-key.md`
- Test plan: `docs/test-plans/37-insider-cache-key.md`
- PR draft path: `.ai/pr-description/37-insider-cache-key.md`

## Acceptance Criteria

- [x] AC1: `artifact_id_insider` returns `{ticker}_{16 hex}_v2`, stable for the same text.
- [x] AC2: Unchanged inputs on a later score date hit the cache; no API call.
- [x] AC3: New transaction, aged-out transaction, or new cluster flag calls the API.
- [x] AC4: Prompt contains neither the score date nor the window start.
- [x] AC5: Same-date transactions fetched in a fixed order (date, then id, newest first).
- [x] AC6: `make lint`, `make test`, `make security` pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/37-insider-cache-key.md` (no meaningful
  impact: hash of text already sent to the model, used as a bound SQL parameter; fewer external
  calls)

## Validation

- Six new tests fail against `main`, reproducing the bug.
- Read-only check on production data for the 140 tickers analysed on 2026-10-20: new keys
  unchanged for 135/140, 136/140 and 138/140 across the last three night pairs, so about 2-5
  insider calls a night instead of about 137.
- `make lint`: pass
- `make security`: pass
- `make test`: 1069 passed

Reviewer pass (fresh subagent): no blockers; exact-id pin and tie-order comment applied,
dashboard date effect noted below.

## Risks and follow-ups

- First night after merge: every insider key is new, so one full run of calls (about 140) as
  before; savings start the night after.
- Old date-keyed insider rows are no longer read; they expire through the 30-day TTL.
- Dashboard Ticker page shows the newest insider row by `created_at`, so on a cache hit it shows
  the assessment from when the inputs last changed (up to 30 days old), dated then. Intended:
  the inputs have not changed since.
