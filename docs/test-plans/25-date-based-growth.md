# Test Plan: date-based-growth

Path: `docs/test-plans/25-date-based-growth.md`

## What changed
- `factors/_utils.py`: `value_and_prior` (latest reported value and the value N months earlier).
- `factors/growth.py`: YoY, acceleration and the R&D-intensity row by date.
- `factors/quality.py`: gross-margin trend by date.
- `data/fundamentals.py`: stored QoQ/YoY growth ratios compare dated periods.

## Acceptance criteria coverage
- AC1: `tests/test_growth.py::TestValueAndPrior` (4 tests)
- AC2: `tests/test_growth.py::TestDateBased::test_gap_gives_nan_not_fifteen_months`,
  `test_gap_elsewhere_still_computes`, `test_acceleration_by_date`; existing `TestYoY`,
  `TestAcceleration`
- AC3: `TestDateBased::test_empty_newest_quarter_keeps_growth` (growth score and R&D intensity)
- AC4: `tests/test_quality.py::TestGmTrendDates` (2 tests)
- AC5: `tests/test_fundamentals.py::TestGrowthRatiosByDate`, `TestGrowthRatiosAnnualAndLeap`
  (annual rows, February leap-year ends, earnings ratio)
- Also: `tests/test_growth.py::TestDateBasedMore` (missing 24-months-back quarter, repeated
  index labels)

## Edge cases
- From spec: empty newest quarter, missing year-ago quarter (tests above).
- Additional adversarial cases:
  - Missing quarter 12 months back gives NaN, not a 15-month comparison
  - Unsorted rows: `test_unsorted_input`
  - Gap elsewhere in the series doesn't block YoY: `test_gap_elsewhere_still_computes`

## Notes
- Live check (2026-10-14, production data via the scoring loader): BAC and GS growth scores
  36.8 and 68.4 (previously the neutral default because of their empty newest quarter); other
  tickers shift slightly with the re-ranking.
- Found, not changed: revenue acceleration needs 24 months of quarters and Yahoo supplies about
  5-7, so it is neutral (50) for every ticker, before and after this packet.

## Commands
```bash
make lint
make test
python run_data.py --no-filings --no-13f --refresh-fundamentals   # once, after merge
```
