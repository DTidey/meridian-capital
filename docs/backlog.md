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
