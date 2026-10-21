# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Risk factors still not found for six 10-Ks

- **Raised:** 2026-10-15, packet 33 universe check.
- **Problem:** C, GE, NEM and SYF use layouts the extractor does not recognise (no Item 1A
  heading, or "Risk Factors Relating to ..." sub-sections only). USB and WFC incorporate their
  risk factors by reference from the annual report exhibit (EX-13), which the analyzer does
  not fetch. All six fall back to the stored 10-K text.
- **Fix:** for USB/WFC, fetch the EX-13 exhibit when Item 1A points to it; for the others,
  inspect their layouts individually.

## Remove the pillow pip-audit ignores

- **Raised:** 2026-10-21, packet 36.
- **Problem:** eight pillow advisories are fixed only in 13.0.0, and streamlit requires
  `pillow<13` (1.65.0 and 1.66.0), so `make security` ignores them via `PILLOW_AUDIT_IGNORES`.
- **Fix:** when a streamlit release allows pillow 13, upgrade both, pin pillow at 13 or later
  and delete `PILLOW_AUDIT_IGNORES` from the Makefile (`tests/test_audit_ignores.py` fails until
  then).
