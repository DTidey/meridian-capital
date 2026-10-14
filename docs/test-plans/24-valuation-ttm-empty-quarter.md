# Test Plan: valuation-ttm-empty-quarter

Path: `docs/test-plans/24-valuation-ttm-empty-quarter.md`

## What changed
- `dashboard/ticker_valuation.py`: TTM sums the four latest quarters that report each field
  (still within 300 days); eight quarters loaded; caption wording.

## Acceptance criteria coverage
- AC1: `tests/test_ticker_valuation.py::TestTtmSelection::test_fields_use_their_own_latest_quarters`,
  `test_fewer_than_four_with_field`
- AC2: `TestTtmSelection::test_empty_newest_quarter_skipped`
- AC3: `TestTtmSelection::test_middle_gap_still_rejected`; existing `test_gap_in_quarters_no_ttm`,
  `test_single_missing_quarter_no_ttm`
- AC4: `tests/test_page_ticker.py::TestRenderSmoke::test_renders_with_all_sections`

- AC1 recency: `TestTtmRecency` (stopped dividend, two skipped quarters, zero dividend counted)

## Edge cases
- Adversarial: a missing middle quarter skipped would span about 365 days and is rejected
  (`test_middle_gap_still_rejected`); per-field quarter choice (`test_fields_use_their_own_latest_quarters`).

## Notes
- Live (2026-10-14, production data): BAC P/E 12.1x, GS 14.5x (previously "—"); JPM 13.5x and
  AAPL 38.9x unchanged.

## Commands
```bash
make lint
make test
```
