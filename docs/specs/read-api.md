# Read API response contract (S1.6)

Normative sources (this spec adds only what they leave open — do not restate):
- CLAUDE.md invariant "Visibility rule for ALL consumers" (`migrations/005_registry.sql`, `item_document_production`).
- `migrations/027_effective_expiry.sql` — `document_effective_expiry`, the one definition of when a document lapses, and the `basis` vocabulary.
- `migrations/034_item_document_production_expiry.sql` — the view's `expires`/`expiry_basis` columns this API selects.
- `web/app.py` (`api_item_documents`, `api_document`, `api_kpi`) — the implementation.

**Deliberately out of scope** (blocked on the `[serving-mechanism-and-doc-names]` ruling): consumer auth beyond the Caddy ingress, absolute/durable archive URLs, any stored title column. This spec documents the shape that survives that ruling either way.

## 1. Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/items/{item_ref}/documents` | Production documents linked to one item (`?include_superseded` adds the chain) |
| GET | `/api/documents/{doc_id}` | One production or superseded document + its complete evidence |
| GET | `/api/kpi` | The KPI board (same numbers as `GET /`) |

All three are read-only, production-visibility only (Invariant 1: the web process holds zero write grants on `document`/`item_document`/`evidence`). Auth is the Caddy reverse proxy in front of the whole app (G3 v0) — none of these routes carry auth logic of their own.

## 2. `GET /api/items/{item_ref}/documents`

`item_ref` uses the `:path` converter (item refs contain `/`), matching the HTML `/items/{item_ref}` route so the two never disagree about which items exist. Unknown `item_ref` returns `200` with an empty `documents` array — a JSON API's normal empty state, not a `404` (an item is a BC/webshop identifier this system does not own; "no documents yet" and "no such item" are not this endpoint's distinction to make).

**Response:**

```json
{
  "item_ref": "12345",
  "item_name": "Grandio Flow A2 caps",
  "manufacturer": "VOCO GmbH",
  "manufacturer_code": "077",
  "mfr_ref": "2691",
  "documents": [
    {
      "doc_id": 42,
      "name": "VOCO GmbH DoC (MDR), valid to 2027-05-01",
      "type": "DoC",
      "regulation": "MDR",
      "match_basis": "ref-list",
      "valid_from": "2023-04-01",
      "valid_to": "2027-05-01",
      "expiry_basis": "inherited",
      "url": "/documents/42/file"
    }
  ]
}
```

### Field semantics

**Item identity** (top level, added 2026-08-25). `item_ref` alone does not let a consumer confirm it asked about the article it meant, so the item's own identifying fields travel with its documents:

- **`item_name`** — `item_mirror.name`, the catalogue description as BC holds it.
- **`manufacturer`** — the canonical manufacturer name, `COALESCE(manufacturer_alias.canonical_name, item_mirror.manufacturer_raw)`. The same resolution `/items/{item_ref}` performs, reusing `manufacturer_alias` rather than defining a second mapping. Falls back to the raw BC code when no alias row exists yet — never null for a mirrored item.
- **`manufacturer_code`** — `item_mirror.manufacturer_raw`, the BC vendor code itself. Carried alongside the name because the code is what BC joins on and the name is what a human reads; the two are not interchangeable.
- **`mfr_ref`** — `item_mirror.mfr_ref`, the manufacturer's own article number (C1). Nullable in the catalogue and therefore here: null is a known data state, not an error (ground truth §7 trap 2; Denis, 2026-08-14 — `mfr_ref` was a check, never something the system pivots on).

All four are `null` when `item_ref` is not in `item_mirror`. That is the one signal separating "no such item" from "no documents yet" — the distinction §2 above deliberately declines to make with a `404`, now observable without changing the status code.

- **`doc_id`** — the registry's own id. Stable; the same document keeps this id across supersession (a NEW document gets a new id; `document.superseded_by` chains them).
- **`name`** — derived, not stored. `"{manufacturer} {type} ({regulation}), valid to {valid_to}"`, built from:
  - the **latest-revision** `manufacturer` evidence (`evidence.field = 'manufacturer'`, highest `extract_rev` — a re-extraction must not leave the name stuck on a stale or wrong earlier revision);
  - `type` (`DoC|EC|IFU|ISO|other`);
  - `regulation` in parens, omitted when `n.a.` (non-device paper — an ISO 13485 certificate has no MDR/MDD regulation to name);
  - `, valid to {valid_to}`, omitted when `valid_to` is null.

  Degrades gracefully at every step: no manufacturer evidence → name starts with the type; no regulation → no parens; no expiry → no "valid to" clause. Never a `KeyError`, never a literal `"None DoC"`.
- **`type`**, **`regulation`** — `document.type` / `document.regulation`, unchanged.
- **`match_basis`** — `item_document.match_basis` (`ref-list|udi|basic-udi-di|name-family|fetch-context|mfr-scope|manual|ref-catalogue|ref-item`). Because this endpoint only ever shows `production` links, `match_basis` here can never be `name-family`, `fetch-context`, or `ref-catalogue` (`item_document_trusted_basis_ck`, migration 005, widened by 021) — those three bases are structurally capped at `staged`; `ref-item` (023) is not barred, since its manufacturer is established before the number comparison the same way `ref-list`'s is.
- **`valid_from`** — `document.validity_from`, unchanged (a document's own stated issue/coverage-start date).
- **`valid_to`** — the **effective** expiry, `document_effective_expiry.expires` (migration 027), NOT `document.validity_to` raw. This is the one behavioral change a consumer can observe versus the pre-034 response: a DoC that cites a certificate now reports the certificate's date here even when the DoC's own `validity_to` is NULL. `document.validity_to`'s raw meaning is unchanged everywhere else — only this API's `valid_to` key means "effective."
- **`expiry_basis`** — which rule produced `valid_to`, quoted from migration 027's vocabulary:
  - `stated` — the document's own `validity_to`.
  - `inherited` — the production/superseded certificate it cites (`cert_doc_id`).
  - `staleness` — a DoC with no expiry of its own and no usable certificate: five years after `validity_from`, a REVIEW HORIZON Dentalia chose, **not a legal expiry** (MDR Annex IV requires no expiry on a DoC; Article 19(1) requires only that it be kept current).
  - `null` — nothing to chase (no stated date, no citable certificate, not a DoC eligible for the staleness rule — e.g. a Class I item's declaration with no `validity_from` either).
- **`url`** — `/documents/{doc_id}/file`, **constructed, never read from the database** (2026-08-24 correction). `document.archive_url` is a storage handle, not a link — backfill rows written before the archive fix hold `/imports/...` corpus paths nothing serves, and even repaired handles are an internal layout detail. The `document_file` route resolves the handle server-side against the configured roots, so this `url` works for every production document regardless of where its bytes live, and stays stable if the archive moves. **Relative**, not absolute or durable — served under this app's own origin. Absolute/durable URLs and any auth scheme beyond the Caddy proxy await the `[serving-mechanism-and-doc-names]` ruling (direct exposure vs. relay through webshop/BC).

### `?view=customer`

`view=customer` narrows the response to what the webshop may render to a clinic.
It omits `manufacturer_code` at the top level and `match_basis` / `expiry_basis`
from every document. Additive per §5: the default (`view=full`) is byte-identical
to the response documented above.

`expiry_basis` is withheld rather than hidden for tidiness. `staleness` means a
five-year review horizon Dentalia chose, not a legal expiry (§2 above), and
printed beside a date on a customer-facing page it reads as an expiry we
invented.

The same parameter applies to `GET /item/{item_ref}` (the BC item-card link),
which shares this endpoint's implementation — `web/item_docs.py`.

### `?include_superseded` (2026-09-16)

`include_superseded=true` widens the ROW SET to the documents this item's
current paperwork **replaced** — the supersession chain a consumer reconciling
older orders needs, and the one thing the registry knows about an item that a
consumer previously could not reach at all. Off by default, so every existing
response is unchanged.

A separate boolean rather than a `view` value, for the reason `include_unlinked`
is one on `/api/manufacturers/{name}/documents` (§4b): `view` is the
**projection**, this is the **row set**, and one knob answers one question.

Flagged documents carry two extra fields, and only when the flag is on:

- **`status`** — `production` or `superseded`. Without it the caller cannot
  tell which row is current.
- **`superseded_by`** — the `doc_id` that replaced this one, `null` on a
  current document. Chains are walkable: `document.superseded_by` is the same
  pointer GATE writes (Invariant 5).

An unflagged response gains nothing. `status` would be the constant
`"production"` and `superseded_by` the constant `null` for every row, and the
batch endpoint would carry both up to a hundred times a call while telling
nobody anything.

**Refused in the customer view.** `view=customer&include_superseded=true`
returns `400`, not a silently dropped flag. A superseded document is by
definition the paperwork we replaced; rendering it to a clinic as coverage is
the exact harm the customer projection exists to prevent. The rule lives in
`web/item_docs.py` (`CUSTOMER_HISTORY_REFUSAL`) and both routes translate its
`ValueError`, so the single-item and batch endpoints cannot answer differently.

**Not reachable from the BC card.** `GET /item/{item_ref}` shares this
implementation but never passes the flag: the card is paperwork for a person
handling one item today, and BC builds its links by string concatenation.

**Expired is not superseded, and never needed a flag.** Neither view filters on
expiry — `item_document_production` restricts on status alone — so an expired
production document has always been returned, carrying `valid_to` and
`expiry_basis` for the caller to act on. Measured 2026-09-16: 86 expired
production documents reaching 1 115 items through 1 184 production links, all
already visible.

### Visibility rule

Verbatim from `migrations/005_registry.sql`: **a document is visible for an item only when both the document and that item's link are production.** Concretely, `item_document.status = 'production' AND document.status = 'production'` — encoded once, in `item_document_production`, and never reimplemented as an ad hoc join (K2's note in `docs/specs/kpi.md` explains why: reimplementing it is exactly the drift this invariant exists to prevent).

`?include_superseded` moves exactly one half of that rule, in one view
(`item_document_history`, migration 070), and the other half not at all:

| | default | `?include_superseded=true` |
|---|---|---|
| document status | `production` | `production`, `superseded` |
| link status | `production` | `production` |

`staged` documents stay invisible because they are ungated machine output;
`rejected` and `filed` because each is a recorded decision that this is **not**
coverage (C15). A non-production **link** is a human saying this document does
not cover this item, so no flag may override it in either direction — a
retracted link is unreachable through either view by construction.

`item_document_history` is a second view rather than a widening of
`item_document_production`, deliberately: `web/registry.py`, `web/catalogue.py`,
the KPI board and `046_eudamed_gap_view` all read the production view, and
relaxing its status filter would change what all four mean without a single
call site being edited.

## 3. `GET /api/documents/{doc_id}`

```json
{
  "document": { "doc_id": 42, "type": "DoC", "regulation": "MDR", "...": "every document column" },
  "evidence": [
    { "field": "validity_to", "value": "2027-05-01", "tier": "T1", "model_id": "claude-haiku-4-5",
      "confidence": 0.92, "verbatim": "valid until 05/2027", "page": 3,
      "archive_url": "/archive/ab/cd/abcd....pdf", "extracted_at": "2026-08-01T10:00:00Z" }
  ]
}
```

`404` when `doc_id` does not exist or is not `status IN ('production', 'superseded')` — staged/rejected/filed documents are not this endpoint's to show (same visibility rule, applied at the document level since there is no item in this request). Superseded documents ARE served here as of 2026-09-16: `?include_superseded` hands a consumer their `doc_id`s, and a payload citing ids that then `404` is worse than no history at all. The `detail` string still reads `production document {id} not found`, unchanged, so an existing caller matching on it is unaffected. `document` is every column on the `document` row as-is (no derived fields here — `name`/effective-expiry derivation is specific to the item-scoped endpoint above). `evidence` lists every field's evidence, all revisions, ordered by `field` — Invariant 2's full evidentiary record `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)`, `page` legitimately null for T0/T3 evidence ([evidence-page] ruling, 2026-07-31).

## 4. `GET /api/kpi`

Returns the same `_kpi_board()` dict the HTML KPI board renders — one implementation, two renderings (`docs/specs/kpi.md` §4). Top-level keys: `coverage`, `match_basis`, `missing_mfr_ref`, `staging`, `manual_by_kind`, `dead_jobs`, `jobs_by_type_status`, `spend`. See `docs/specs/kpi.md` for the per-metric shape and interpretation; this spec does not restate it.

## 4b. Catalogue and batch endpoints (slice 2, 2026-08-25)

Service-visibility endpoints for the webshop backend. All under `/api/`, so
`web/access.py`'s policy covers them unchanged: `service` + `staff`, never
`bc`, never anonymous. **Nothing here is public, and that is the design, not an
oversight** -- a manufacturer-to-items listing is Dentalia's supplier map. The
webshop fetches server-side and renders into its own pages under its own login.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/manufacturers?q=` | Every manufacturer, BC codes, production item and document counts |
| GET | `/api/manufacturers/{canonical_name}` | One manufacturer's identity and production counts |
| GET | `/api/manufacturers/{canonical_name}/documents?view=` | Production documents across the line, **deduped by `doc_id`** |
| GET | `/api/manufacturers/{canonical_name}/items?page=&per_page=` | That manufacturer's items, paginated |
| GET | `/api/items/documents?ref=&ref=&view=` | Several items in one call, for an order page (`?include_superseded` applies to every entry) |

**Deduplication** on `/documents` is the reason the endpoint exists: one ISO
13485 certificate can cover a whole product line, so a per-item listing returns
it once per item and buries the handful that differ. `DISTINCT ON (doc_id)`.

**Pagination** on `/items` is mandatory, not optional -- ~16k items mirrored
today, ~100k at target. `per_page` defaults to 100 and is **capped at 500**;
asking for more silently yields the cap, so a consumer must read `per_page` back
out of the response rather than assume it got what it asked for. A `page` past
the end returns an empty `items` with the true `total`, never an error.
Response carries `page`, `per_page`, `total`, `pages`, `items`.

**Batch** is a `GET` with a repeated `ref` parameter, deliberately not a `POST`:
the web process is a job producer only, and a POST that merely reads muddies a
boundary that is otherwise enforced structurally. Capped at 100 refs (`400`
above that, and `400` with none). Entries return **in the order requested**, and
a ref this system has never mirrored is kept as a null-identity entry rather
than dropped -- the caller renders a row per order line and needs to know
*which* line has nothing, which a shorter array cannot convey.

`include_superseded` (§2) is honoured here too and applies to **every** entry:
reconciling a page of old paperwork is one question, not one per line. The
customer-view refusal is not restated in this endpoint -- it is `item_docs`'
rule, checked per entry, and the first `ValueError` answers for the whole call,
which is right because the flag is per-call.

**Production-only, and not by reusing the UI's query.**
`registry.manufacturer_detail` deliberately surfaces staged and filed documents
because the UI's question is "why is this not published yet". A consumer's
question is "what may I hand a customer". These endpoints therefore have their
own production-scoped queries in `web/catalogue.py`; the document projection
itself is shared with the item endpoints through
`web/item_docs.py::decorate_documents`, so the two can never disagree about what
a document looks like.

`?view=customer` applies to `/documents` and to the batch exactly as in §2.

## 5. Evolution rule

Additive-only, same rule as job payloads (CLAUDE.md: "Payload fields additive-only once Phase 1 starts; consumers tolerate unknown fields, never missing ones"). A future field is appended, never inserted in a way that renumbers or removes an existing key; a field is never repurposed to mean something new (the `valid_to`-becomes-effective change in Task 2 was the one deliberate exception, made once, before any external consumer existed — "expose a read API from day one, wire nothing"). No external consumer exists yet by that same ruling, so this document is the contract for the first one.
