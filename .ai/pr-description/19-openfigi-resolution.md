## Summary

Makes the OpenFIGI fallback for 13F ticker resolution work. With `OPENFIGI_API_KEY` set, the
old code made the data worse: it stored about 40,000 non-S&P 500 holdings and still missed
non-US-incorporated companies like Chubb.

- Non-US issuers have CINS identifiers (starting with a letter), which OpenFIGI only finds as
  `ID_CINS`. These are now sent with that type.
- Only US-listed common stock is accepted. The first-hit fallback could return a foreign ticker
  (Chubb's first hit is `AEX` in Germany).
- Holdings outside the S&P 500 universe are dropped; nothing uses them.
- Each identifier is looked up once per run (7 min down to under 2 min).
- Failed OpenFIGI batches are logged at WARNING, not DEBUG.
- Share-class tickers match the universe spelling (`BRK/B` becomes `BRK-B`), and names shared by
  two classes (Fox, News Corp) resolve by CUSIP, so class B is no longer booked as class A.
- New `run_data.py --refetch-13f` re-fetches the recent filings, replacing each period only
  after its new fetch succeeds.

## Spec

- Spec: `docs/specs/19-openfigi-resolution.md`
- Test plan: `docs/test-plans/19-openfigi-resolution.md`
- PR draft path: `.ai/pr-description/19-openfigi-resolution.md`

## Acceptance Criteria

- [x] AC1: Identifiers starting with a letter sent as `ID_CINS`.
- [x] AC2: US common stock only; no first-hit fallback.
- [x] AC3: Only `sp500_universe` tickers returned.
- [x] AC4: Each identifier looked up at most once per run; failed batches not cached.
- [x] AC5: Failed batches logged at WARNING.
- [x] AC6: `refetch=True` / `--refetch-13f` replaces each fetched period after a successful
  fetch; older periods and untracked funds kept.
- [x] AC7: Names shared by several share classes left out of the name map.
- [x] AC8: Non-list and short OpenFIGI responses handled.

## Security Review

- [x] Security considerations reviewed in `docs/specs/19-openfigi-resolution.md`
- [x] `--refetch-13f` replaces a period only after its re-fetch succeeds

## Validation

- Tests written first: 10 failed on the old code
- `make lint`: pass
- `make security`: pass
- `make test`: 717 passed
- Live dry run with the key: 485 tickers (was 342), 4 "3+ new" flags (was 9; 26 with the key on
  the old code), Chubb present in all 6 quarters. Remaining gaps checked against Bridgewater's
  filing and are real exits.

Reviewer pass (fresh subagent): no blockers; the Important items (class-share universe
filter, ticker spelling, re-fetch deleting unrecoverable history, function length) are fixed.

## Risks

- GOOG (class C) holdings are still booked as GOOGL: funds file both classes as "ALPHABET INC".

## After merge

Run `python run_data.py --no-filings --refetch-13f` once to rebuild the stored 13F data.
