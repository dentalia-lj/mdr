# Read API response contract (S1.6)

Normative sources (this spec adds only what they leave open — do not restate):
- CLAUDE.md invariant "Visibility rule for ALL consumers" (`migrations/005_registry.sql`, `item_document_production`).
- `migrations/027_effective_expiry.sql` — `document_effective_expiry`, the one definition of when a document lapses, and the `basis` vocabulary.
- `migrations/034_item_document_production_expiry.sql` — the view's `expires`/`expiry_basis` columns this API selects.
- `web/app.py` (`api_item_documents`, `api_document`, `api_kpi`) — the implementation.

**Consumer access** was ruled and built 2026-08-25 (`docs/superpowers/specs/2026-08-25-item-document-access-design.md`, followup `[serving-mechanism-and-doc-names]` closed): every `/api/*` route takes a staff Basic login or a service `X-API-Key`, never anonymous; `/documents/{id}/file` the same; `/item/*` also takes the BC link key `?k=`. The policy is `web/access.py`, default-deny. Response URLs are absolute when `web.public_base_url` is set (`https://api.cw.dentalia.si` on the client server, live 2026-09-30). The display name is derived (`web/item_docs.py`), not a stored title. The caller-facing version of all this is the app's own `/api-reference` page.

## 1. Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/items/{item_ref}/documents` | The current documents of one item, per type (`?include_superseded` returns every published document linked to it, with `current` per row) |
| GET | `/api/documents/{doc_id}` | One production or superseded document + its complete evidence |
| GET | `/api/kpi` | The KPI board (same numbers as `GET /`) |

All three are read-only, production-visibility only (Invariant 1: the web process holds zero write grants on `document`/`item_document`/`evidence`). None of these routes carries auth logic of its own: the global dependency in `web/access.py` decides. Since 2026-11-23 `/api/kpi` and `/api/documents/{doc_id}` are **staff only**; an API key reaches the item endpoint and the § 4b catalogue in the webstore view (§ 4c). The catalogue and batch endpoints are in § 4b, the download links in § 4c.

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

- **`doc_id`** — the registry's own id. Stable; the same document keeps this id across supersession (a NEW document gets a new id). `document.superseded_by` is written only by the auto-file fast path, on an older copy filed behind the current document; which document answers for an item is decided per item (migration 071).
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

### Which documents the default response returns (2026-10-02, migration 071)

The default response returns the **current** documents per type for the item:
one, or several when documents of that type carry the same issue date (all are
current, no tiebreak). Among the item's published documents of a type, MDR is
current over MDD whatever the dates, then the newest `validity_from`; undated
documents rank below dated ones, and `n.a.` never outranks MDR. **The default
response therefore shrinks for items that used to return several current
documents of one type**: the older ones are history for that item now, and are
reachable with `?include_superseded=true`.

### `?include_superseded` (2026-09-16, widened 2026-10-02)

`include_superseded=true` widens the ROW SET to **every published document
linked to the item**, with `current` per row, so the history stays retrievable:
the older and replaced paperwork a consumer reconciling older orders needs.
Off by default, so a caller that never sets it gets the current documents only.
Rows come newest first: `validity_from` descending, undated last, `doc_id`
descending on a tie. The default response keeps its `doc_id` order.

A separate boolean rather than a `view` value, for the reason `include_unlinked`
is one on `/api/manufacturers/{name}/documents` (§4b): `view` is the
**projection**, this is the **row set**, and one knob answers one question.

Flagged documents carry three extra fields, and only when the flag is on:

- **`current`** — `true` when this is a document that answers for the item
  (see above), `false` when it is history for this item. This, not `status`,
  says which rows are current.
- **`status`** — the document's own status, `production` or `superseded`. It is
  not currency: a document the old document-wide rule marked `superseded` can
  still be `current: true` for an item nothing newer covers.
- **`superseded_by`** — the `doc_id` the auto-file fast path or the old rule
  filed this document behind, `null` otherwise. `document.superseded_by` is the
  pointer GATE writes.

An unflagged response gains nothing. `status` would be the constant
`"production"`, `superseded_by` the constant `null` and `current` the constant
`true` for every row, and the batch endpoint would carry them up to a hundred
times a call while telling nobody anything.

**Refused in the customer view.** `view=customer&include_superseded=true`
returns `400`, not a silently dropped flag. A history row is by
definition paperwork that does not answer for the item; rendering it to a clinic as coverage is
the exact harm the customer projection exists to prevent. The rule lives in
`web/item_docs.py` (`CUSTOMER_HISTORY_REFUSAL`) and both routes translate its
`ValueError`, so the single-item and batch endpoints cannot answer differently.

**Not reachable from the BC card.** `GET /item/{item_ref}` shares this
implementation but never passes the flag: the card is paperwork for a person
handling one item today, and BC builds its links by string concatenation.

**Expired is not superseded, and never needed a flag.** Neither view filters on
expiry, so an expired current document has always been returned, carrying `valid_to` and
`expiry_basis` for the caller to act on. Measured 2026-09-16: 86 expired
production documents reaching 1 115 items through 1 184 production links, all
already visible.

### Visibility rule

Verbatim from `migrations/005_registry.sql`: **a document is visible for an item only when both the document and that item's link are production.** Since migration 071 the document half is the per-item currency rule: the item's published documents (`production` or `superseded`) on production links, narrowed to the current one per type. Encoded once, in `item_document_production`, and never reimplemented as an ad hoc join (K2's note in `docs/specs/kpi.md` explains why: reimplementing it is exactly the drift this invariant exists to prevent).

`?include_superseded` moves exactly one half of that rule, in one view
(`item_document_history`, migration 070, `current` added by 071), and the other
half not at all:

| | default | `?include_superseded=true` |
|---|---|---|
| documents | the current one(s) per type | every published document (`production`, `superseded`), `current` per row |
| link status | `production` | `production` |

`staged` documents stay invisible because they are ungated machine output;
`rejected` and `filed` because each is a recorded decision that this is **not**
coverage (C15). A non-production **link** is a human saying this document does
not cover this item, so no flag may override it in either direction — a
retracted link is unreachable through either view by construction.

`item_document_production` is the current rows of `item_document_history`
(migration 071), so the two cannot drift apart about which document answers:
`web/registry.py`, `web/catalogue.py` and the KPI board all read the production
view and get the current documents without editing a call site.
`046_eudamed_gap_view` does not read it: `eudamed_article_status` joins
`item_document` and `document` itself, and migration 072 aligned it, so a DoC
counts there when it is published (`production` or `superseded`) on a
production link, which is the same as the article having a current DoC.

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
| GET | `/api/manufacturers?q=` | Every manufacturer with its stable `id`, BC codes (staff), production item and document counts |
| GET | `/api/manufacturers/{id}` | One manufacturer's identity and production counts |
| GET | `/api/manufacturers/{id}/documents?view=` | Production documents across the line, **deduped by `doc_id`** |
| GET | `/api/manufacturers/{id}/items?page=&per_page=` | That manufacturer's items, paginated (**staff only** since 2026-11-23) |

**Addressed by `manufacturer.id`** since 2026-11-23: a name changes on a UI
rename and can be a bare BC code like `077`. The id resolves through every name
the manufacturer has had, so it keeps working between a rename and the next
`playbooks sync`. Staff keep the name forms under
`/api/manufacturers/by-name/{canonical_name}` (+ `/documents`, `/items`).
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

## 4c. The webstore key (2026-11-23)

Full design: `docs/superpowers/specs/2026-11-23-webshop-api-design.md`. What a
caller with an `X-API-Key` gets, which since that date is the webstore (Erpium)
and nothing else:

- **Routes:** `/api/manufacturers`, `/api/manufacturers/{id}`,
  `/api/manufacturers/{id}/documents`, `/api/items/{item_ref}/documents`,
  `/api/items/documents`, `/api/documents/{doc_id}/link`. Any other `/api/`
  route: `403` from the app, though on `api.cw.dentalia.si` Caddy answers first with
  plain `404 Not found`. `/item/*` and `/documents/{id}/file`: refused.
- **Always the customer view**; `view=full` is ignored. `include_unlinked` and
  `include_superseded`: `400`.
- **Rows:** a document past its **legal** expiry is dropped. Legal expiry is
  `LEAST(document.validity_to, the cited production/superseded certificate's
  validity_to)`, deliberately not `document_effective_expiry.expires`, which also
  folds in the five-year DoC review horizon (ruling 2026-09-11).
- **`valid_to` is the legal expiry**, so a class I DoC with no stated date shows
  `null` and its `name` carries no "valid to". The staff view is unchanged and
  keeps the effective expiry and its basis.
- **`url`** is `/api/documents/{doc_id}/link`, which the webstore's server calls
  on a customer's click. Response: `{"url": "<base>/d/<token>", "expires_at"}`.
  The token is signed (`WEB_DOWNLOAD_LINK_SECRET`) and valid 5 minutes. `/d/*`
  needs no credential, re-checks visibility, never redirects to storage. `410`
  expired or altered, `404` not visible.
- **Manufacturer list:** only manufacturers with at least one visible document,
  without `codes`.
- **`file_name`** on every document: the original file name (basename of
  `archive_url`, archive hash prefix removed). The derived `name` is the same for
  every IFU of one manufacturer, so this is what tells them apart.
- **Other documents** (spec § 5.11): manufacturer documents also return
  `other_documents`, documents bound to the manufacturer that are `filed` or
  published with no published link, not past legal expiry; the tree and the
  detail carry an `other_documents` count. Links may be issued for them.
- **Manufacturer documents** also carry `item_count`, `items` (`[{item_ref,
  item_name}]`, at most 50) and `items_truncated`: the manufacturer's items each
  document is current for. Not on the item routes (spec § 5.9).
- **Limits** (`web/limits.py`): `429` + `Retry-After` past 1,200 calls a minute
  per key, or past 30 distinct documents a minute / 100 a day per customer IP on
  `/d/*`. Per key and day, 200 distinct documents linked raises an alert, never a
  block.

**Search** (`GET /api/search?q=`, spec § 5.10, `web/search.py`): `q` 2–100
characters (else `400`), matched in order on `item_ref` and `mfr_ref` (exact,
case-insensitive), a document's `basic_udi_di` (spaces ignored), then item name
(`pg_trgm`). At most 20 items, each the item payload plus `matched_on`, and only
items with a document the caller may be shown. Matching manufacturer names come
back as `manufacturers` (tree entries, never their items). Key and staff.

`url` changing meaning in this view is the second deliberate exception to § 5's
additive-only rule, made for the same reason as the first: no external consumer
was live yet.

## 5. Evolution rule

Additive-only, same rule as job payloads (CLAUDE.md: "Payload fields additive-only once Phase 1 starts; consumers tolerate unknown fields, never missing ones"). A future field is appended, never inserted in a way that renumbers or removes an existing key; a field is never repurposed to mean something new (the `valid_to`-becomes-effective change in Task 2 was the one deliberate exception, made once, before any external consumer existed — "expose a read API from day one, wire nothing"). No external consumer exists yet by that same ruling, so this document is the contract for the first one.
