# `docs/samples/` — captured payloads

## `bc-api-2026-09-02-*.json`

**Synthetic, with the real shape.** b-s.si sent the raw JSON on 2026-09-03
(`AllItems.txt`, `DataItems.txt`) after Denis asked. What is committed here is
that response with every value replaced: item numbers, descriptions, prices,
costs, margins, vendor numbers, GTIN, GUIDs, etags and the endpoint host are all
invented. **The structure is untouched** — same records, same property names,
same types, same empty-vs-populated pattern — because the structure is what the
profile guard and `tools/bc_api.py` read, and the values are Dentalia's cost and
margin data, which does not belong in a repository.

Two relationships are preserved deliberately, because findings depend on them:
`profit` is still `(unitPrice - unitCost) / unitPrice`, and `lastDateTimeModified`
is still years apart from `systemModifiedAt` on the first record — that gap is
why delta reads must use `systemModifiedAt`.

- **`dataitems`**: the complete 10-record response. Six properties per record:
  `@odata.etag`, `no`, `description`, and the three writable `pte*` fields.
- **`allitems`**: the **first 3 records of a 10-record `?$top=10` response**,
  complete, **242 properties each**. The other seven add no new property.

The real capture is kept out of the repo. Neither was fetched by us — the
endpoint is LAN-only. Re-capture with `curl` once it is reachable, scrub it the
same way, and re-run the check.

Analysis: [`../2026-09-03-bc-api-integration.md`](../2026-09-03-bc-api-integration.md).
Gap check: `python -m tools bc-api`.
