# Bind a document to its manufacturer — design

**Status:** proposed, 2026-08-31. Denis ruled the three decisions inline the
same day (scope, item links, read authority); the storage shape was chosen
after a scaling check.

**Origin.** A reviewer opened doc 2 (ISO, `MD 583446`, printed manufacturer
*3Shape Poland Sp. z o.o.*) and could not select 3Shape in the binding picker.
The question "why can't I select 3shape here" turned out not to be a picker
bug. There is no `document -> manufacturer` edge in the schema at all.

**Related, do not re-litigate:** `[mfr-bind-empty-class]` (Denis 2026-08-27, a
blank BC device class means UNKNOWN, not "not a device") stands unchanged and
this design preserves it. `[gate-bind-zero-links]` is corrected here, not
reversed. `[filed-relink]` is adjacent but does NOT cover this case — it
matches on `ref_list`, and these documents have none.

---

## 0. The failure this fixes

Neither document type reaches a manufacturer directly. Both fan out to
catalogue *items* first, and the manufacturer is read back off those items:

| Scope | Path to a manufacturer |
|---|---|
| `group` (DoC) | `document` -> `item_document` (`ref-list`/`ref-item`/`map-supplier`) -> `item_mirror` -> `item_group_member` -> `item_group.canonical_manufacturer` |
| `manufacturer` (ISO/EC) | `document` -> `item_document` (`mfr-scope`, one row per MD item) -> `item_mirror.manufacturer_raw` -> `manufacturer_alias` -> `canonical_name` |

Both run through `item_document`. So a document with **no item links has no
manufacturer at all** — nothing records what it covers.

Measured on the live registry 2026-08-31, documents with zero
`item_document` rows:

| status | scope | docs | of which carry a `manufacturer` evidence row |
|---|---|---|---|
| filed | group | 429 | 428 |
| filed | manufacturer | 14 | 14 |
| staged | group | 74 | 68 |
| staged | manufacturer | 19 | 17 |
| production | group | 2 | 2 |
| rejected | group | 2 | 2 |
| **total** | | **540** | **531** |

Every filed document — all 443 — is in this state *by definition*: `filed`
means we stock nothing it covers, and the manufacturer path runs through what
we stock.

What those 531 have is an `evidence` row with `field = 'manufacturer'`: the
string EXTRACT read off the page, in the document's own spelling, never
resolved to a catalogue entity. In the filed set it holds `VOCO` (228) and
`VOCO GmbH` (8) as separate values for one company, and `3Shape TRIOS A/S`
and `3Shape A/S` likewise. That is a record of what the document *claims*.
It is not a binding, and `SELECT ... WHERE manufacturer = 'VOCO'` does not
return those 228 documents.

**Why the 3Shape picker was the symptom.** `_mfr_binding_options`
(`web/app.py:852`) offers only entities holding an item with
`md_flag IS TRUE`. All 197 3Shape items (BC vendor codes `10004`, `10005`)
are `md_flag NULL`, so 3Shape is not offered — 9 of 366 entities are
offerable, 357 are not. The picker restriction was correct given the schema:
binding 3Shape would have written zero rows and recorded the decision nowhere.

---

## 1. What already exists (verified 2026-08-31)

Checked against the live database and the tree, not assumed:

* **`manufacturer.canonical_name` is UNIQUE** (`manufacturer_canonical_name_key`),
  so it is a legal FK target.
* **All 366 bindable entities exist in `manufacturer`.** The bind path cannot
  hit a foreign-key violation today.
* **One group manufacturer does not**: `3SHAPE MEDICAL A/S`, 3 groups. Already
  filed as `[seed-orphan-report-masked-by-042]`.
* **`dentalia_api` holds `SELECT` only on `document`.** Invariant 1 needs no
  grant change; the UI stays a producer.
* **GATE already resolves the manufacturer on every route.**
  `_handle_mfr_binding` does `_val(fields, "manufacturer")` ->
  `resolve_canonicals` (`app/handlers/gate.py:799-800`). The group-scope path
  has the same `fields` and simply never asks.
* **`_upsert_document` already has a sticky-column class.** `supersedes`,
  `referenced_doc_id`, `cert_doc_id` and `source_url` are `COALESCE`d to the
  stored value, documented in-code as "additive facts, not status".
* **`document` is written outside `gate.py` by repair tools**:
  `app/repair_archive_urls.py:116` runs `UPDATE document`. So a backfill tool
  is an established shape, not a new precedent.
* **`_derive_mfr_scope_links` raises only on `if not codes`** — unresolvable
  *names*. `3SHAPE TRIOS A/S` resolves to `['10004', '10005']`, so it would
  NOT raise; it would promote the document and link zero. Two code comments
  (`web/app.py:284-286`) and one test docstring
  (`tests/test_web.py:743-745`) state it "refuses it outright". They are
  wrong. See §5.

---

## 2. Rulings (Denis, 2026-08-31)

| Question | Ruling |
|---|---|
| Who gets a binding? | **Every document**, not only manufacturer-scope. The filed set becoming queryable is the larger prize. |
| Blank-class items — link them? | **No links.** Binding only. `[mfr-bind-empty-class]` stands: no MDR coverage asserted over items with no recorded device class. |
| Which source answers a manufacturer query? | **Column wins; link derivation fills gaps** where the column is null. |
| Storage shape | Text column FK'd to `manufacturer(canonical_name)` with `ON UPDATE CASCADE`. |

**Why the text FK rather than a surrogate `manufacturer_id`.** Every other
manufacturer attribute in this schema keys by name —
`item_group.canonical_manufacturer`, `manufacturer_srn.canonical_name`,
`eudamed_sweep_state.canonical_name`. Migration 052 built the
`ON UPDATE CASCADE` pattern for exactly this shape, and its argument transfers
verbatim: *"A rename does not invalidate an SRN attribution or a sweep
schedule; it renames the thing they describe."* A surrogate key would be the
only manufacturer reference in the schema keyed by id, adding a join to every
read and a second convention to the codebase, to solve a problem 052 already
solved. (An earlier draft of this design recommended the surrogate id; that
was wrong and is recorded here so the reasoning is not re-derived.)

---

## 3. Design

### 3.1 Data model

```sql
-- migration 053
ALTER TABLE document
  ADD COLUMN canonical_manufacturer text
    REFERENCES manufacturer(canonical_name) ON UPDATE CASCADE;

CREATE INDEX document_manufacturer_idx
  ON document (canonical_manufacturer)
  WHERE canonical_manufacturer IS NOT NULL;
```

Nullable and additive: no backfill is required for the migration to be
correct, and every existing reader keeps working untouched. `ON DELETE` stays
at `NO ACTION` for 052's stated reason — deleting a manufacturer that
something still references should be refused, not silently cascaded.

**This column is not `evidence.manufacturer`.** PRD §201's `manufacturer` is
"the legal entity that manufactures the devices, **as printed**" — an
extracted value with a confidence and a verbatim. This column is what a human
or C16 **confirmed**. The two must never be conflated: the printed name is a
reading, the column is a decision. The evidence row remains the provenance for
the decision.

### 3.2 GATE write points

`_upsert_document` gains one sticky column, in the class the file already
defines:

```sql
canonical_manufacturer = COALESCE(EXCLUDED.canonical_manufacturer,
                                  document.canonical_manufacturer)
```

A re-delivery that cannot recompute the binding must not erase it; a new
non-null binding still wins, so re-binding stays the correction path and
`_EDITABLE` needs no new entry.

Three writers:

1. **`gate.apply` `bind-manufacturer`** (human) — writes the picked canonical.
2. **`_handle_mfr_binding`** C16 (machine) — writes `bound` on every
   disposition it reaches, including `filed` and the `md-class-unknown`
   staging branch.
3. **The group-scope path** — resolves `_val(fields, "manufacturer")` through
   `resolve_canonicals` and writes it when exactly one canonical comes back.
   Ambiguous or unresolvable leaves it null; this path never guesses.

**No new job type, no new `match_basis`, no payload change.** The binding is
derived at write time from data GATE already holds, which keeps the closed
enums closed (invariant 7) and the payloads additive-only.

### 3.3 The picker

* `_mfr_binding_options` offers **all** catalogue entities with their MD-item
  count, zero included. The count stays on every option: "approve for all
  IVOCLAR products" and "…1069 items" are the same decision at two very
  different volumes.
* Approve no longer requires a non-zero item count.
* `MFR_BINDING_UNKNOWN_CLASS_REASON` is rewritten. It currently ends *"Fix the
  class in BC and re-run, or leave this until the export is populated"* —
  advice this ruling reverses. It becomes an explanation that the binding will
  be recorded and item links will follow when BC populates the class.
* `_mfr_unbindable_entities` and the "which leaves 357 out" prose are deleted:
  nothing is unbindable any more.

### 3.4 Read path

`web/catalogue.py::manufacturer_documents` reads the column, falling back to
the existing link join where it is null:

```sql
WHERE d.canonical_manufacturer = %s
   OR (d.canonical_manufacturer IS NULL AND <existing item join>)
```

Ruled "column wins, links fill gaps".

**Completeness is the larger half of this, not speed.** `manufacturer_documents`
reads `item_document_production`, so a document with no production link is
structurally invisible to it. Measured 2026-08-31, documents held against
documents the endpoint returns:

| manufacturer (evidence string) | held | returned today | invisible |
|---|---|---|---|
| VOCO | 270 | 32 | **238** |
| Ivoclar Vivadent AG | 171 | 70 | 101 |
| GC EUROPE N.V. | 131 | 55 | 76 |
| KOMET | 85 | 14 | 71 |
| Institut Straumann AG | 12 | 8 | 4 |
| VOCO GmbH | 12 | 0 | 12 |
| Alfred Becht GmbH | 4 | 0 | 4 |
| SHOFU INC. | 4 | 0 | 4 |

`/api/manufacturers/VOCO/documents` returns 32 of 270. Separately, `VOCO` and
`VOCO GmbH` are one company split across two evidence strings; the §3.5
backfill merges them through `resolve_canonicals` to 282 under one name.

**Endpoint semantics — decided, and reversible.** Making the column
authoritative brings `filed` and `staged` documents within reach of an endpoint
that is production-only by construction. A filed document is real, held and
evidenced, but it is explicitly *not* coverage — returning it unannounced would
change what an existing consumer's payload means.

So: **`/api/manufacturers/{name}/documents` stays production-only by default**,
and the wider set is reached through the `view` parameter the function already
takes. This preserves the current contract for anything already consuming it
and uses the knob that exists rather than inventing one. Chosen on
recommendation rather than by client ruling — if a consumer turns out to want
filed documents by default, this flips with a one-line default change and no
schema consequence.

**Speed is the smaller half, and it is about 100k, not today.** The current
query seq-scans `item_mirror` (no index on `manufacturer_raw` exists — see
§11), removing 15 936 rows to find 22:

```
Seq Scan on item_mirror  (rows=22)  Rows Removed by Filter: 15936
Planning Time: 14.966 ms   Execution Time: 13.167 ms
```

13 ms is not a problem now; the same scan at ~100k items is roughly six times
worse. On the indexed column it stops scaling with item count at all.

### 3.5 Backfill

A repair tool (`app/repair_document_manufacturer.py`), **not** a migration —
following `repair_archive_urls.py` and `repair_mfr_overbind.py`. Dry-run
first, one audit entry per row, a second run plans zero.

Measured 2026-08-31 over the 531 evidence strings, through the same
`resolve_canonicals` the bind path uses:

```
531 evidence strings
  -> 481  resolve to exactly one canonical   (90.6%)
  ->   0  ambiguous (>1 canonical)
  ->  50  resolve to none
```

The 50 are real manufacturers Dentalia holds documents for but stocks no items
from — `SHOFU INC.`, `Alfred Becht GmbH`, `Sure Dent Corporation`,
`Inter-Med, Inc.`, `META BIOMED CO., LTD.` among them. **They stay null.**
Inventing a `manufacturer` row for them would mint entities from OCR, which is
precisely what VALIDATE's read-only alias lookup exists to prevent (PRD §219:
"VALIDATE never self-seeds the way RESOLVE does, so a garbled OCR name cannot
mint a manufacturer"). The count is reported on the tool's result, never
silent.

Zero ambiguity in the measurement is a fact about today's data, not a
guarantee. The tool must still skip a string resolving to more than one
canonical and count it.

---

## 4. Contract delta

* **PRD v3 §data model** — `document` gains `canonical_manufacturer`.
* **C4** already reads *"one-time human binding approval, then automatic link
  derivation"*. This design stores the input to that derivation, which C4
  described and never had a home for. No change to C4's meaning; the schema
  sketch's `document` DDL and the tag->table matrix need the column added.
* **C15** (`filed`) is unaffected in its entry conditions, but a filed
  document now records the manufacturer it was filed under. That is what makes
  the 443 queryable.
* **Invariants:** 1 unaffected (verified: `dentalia_api` is SELECT-only on
  `document`). 2 unaffected — the binding is a decision, not a production
  *value*; its provenance is the audit entry plus the existing `manufacturer`
  evidence row. 7 unaffected — no new job type, no new `match_basis`.

---

## 5. Corrections carried in this work

Two defects found while designing, both small and both wrong-as-documented:

1. **The zero-link guard does not guard what three places say it does.**
   `_derive_mfr_scope_links` raises on `if not codes` — an unresolvable *name*.
   A name that resolves to BC codes holding no MD item does not raise; it
   promotes the document and links nothing. `web/app.py:284-286` and
   `tests/test_web.py:743-745` claim it "refuses it outright". The raise is
   **kept** (unresolvable names are still a real bug); the three statements are
   corrected to say what it actually does.

2. **The 17 open `mfr-binding` tasks carry no `reason`.** They predate the
   2026-08-27 build, so none renders
   `MFR_BINDING_UNKNOWN_CLASS_REASON` — doc 2 shows the default sentence
   ("Approving it links it to every medical-device product from that
   manufacturer"), the exact sentence that build was written to replace for
   this case. Backfill `reason` onto the existing tasks.

---

## 6. Consequence the checks caught: `rename_impact`

`app/regroup.py::rename_impact` reports the blast radius of a manufacturer
rename before the UI writes it, and its docstring states its two numbers are
"the ones a rename decision turns on". With `ON UPDATE CASCADE` on the new
column, a rename now also updates N `document` rows — a third number the
report does not have. It gains a document count, and the test pinning
`rename_impact` as a subset of `scope()` is updated with it.

Without this the rename screen silently understates what it is about to do,
which is the failure mode migration 052 was written to close.

---

## 7. Testing

Rewritten (assert behaviour this design changes):

| Test | File |
|---|---|
| `test_picker_offers_only_manufacturers_a_binding_would_link` | `test_web.py` |
| `test_picker_reports_a_resolvable_manufacturer_that_would_link_nothing` | `test_web.py` |
| `test_an_unknown_class_binding_does_not_promise_links_it_cannot_make` | `test_web.py` |
| `test_staging_shows_the_manufacturer_the_document_printed_when_it_has_no_links` | `test_web.py` |
| `test_bind_manufacturer_refuses_a_name_that_links_nothing` | `test_gate_apply_handler.py` |
| `test_mfr_binding_files_a_known_manufacturer_we_stock_no_devices_from` | `test_gate_candidate_handler.py` |
| `test_an_all_unclassified_manufacturer_stages_rather_than_files` | `test_gate_candidate_handler.py` |

New:

* Binding a manufacturer with zero MD items records the column and writes zero
  links (the 3Shape case, end to end).
* The binding survives a re-delivered `gate.candidate` that cannot recompute it
  (the sticky-COALESCE property).
* A group-scope document records its resolved manufacturer.
* An ambiguous extracted name leaves the column null rather than guessing.
* `manufacturer_documents` returns a filed document via the column — but only
  under the widening `view`, never in the production-only default (§3.4).
* The default `view` payload is unchanged for a manufacturer that has both
  production-linked and filed documents: the existing contract holds.
* `manufacturer_documents` still returns a link-only document with a null
  column (the fallback half).
* A rename cascades to `document` and `rename_impact` reports the count.
* The backfill tool is idempotent: a second run plans zero.

**Full suite required** — `migrations/` is in CLAUDE.md's full-suite selection
rule, and a `.sql` file is invisible to any import-graph selector. 2231 tests,
measured ~154s at `-n 4 --dist loadfile`.

---

## 8. Files, by slice

**Slice 1 — the edge exists** (nothing user-visible changes)
`migrations/053_document_manufacturer.sql` · `app/handlers/gate.py`
(`_upsert_document` + three write points) · `docs/dentalia-schema-sketch.md` ·
`docs/dentalia-pipeline-contract-prd-v3.md` · tests.

**Slice 2 — the reviewer can act**
`web/app.py` (`_mfr_binding_options`, `_mfr_binding_suggestion`,
`_decorate_review_row`, `MFR_BINDING_UNKNOWN_CLASS_REASON`, delete
`_mfr_unbindable_entities`) · `web/templates/_staging_doc_detail.html` · tests.

**Slice 3 — the filed set becomes queryable**
`web/catalogue.py` (`manufacturer_documents` reads the column; `view` gains the
wider set while the default stays production-only) · `web/registry.py` ·
`app/regroup.py` (`rename_impact`) · tests.

**Slice 4 — backfill and corrections**
`app/repair_document_manufacturer.py` · the two §5 corrections · tests.

Slices 1-3 each ship alone and leave the tree green. Slice 4 depends on 1.

---

## 9. Non-goals

* **Item links for blank-class items.** Ruled out. Revisit only if
  `[mfr-bind-empty-class]` is itself revisited.
* **Batching `_derive_mfr_scope_links`.** It writes one round-trip per item in
  a Python loop (`gate.py:1191-1192`). Measured floor on this machine: 0.503
  ms/round-trip, so doc 816's 2 567 items cost >=1.29s today and the same
  manufacturer at ~100k items would cost >=7.58s, inside the runner's
  transaction. Real and pre-existing; this design neither causes nor worsens
  it. Own followup, `INSERT ... SELECT` is the fix.
* **Auto re-derivation of links when BC populates `md_class`.** A bound
  document does not gain links by itself. Sized separately as its own slice; deliberately
  not in this design because the binding is useful without it.
* **Minting `manufacturer` rows for the 50 unresolved names.** See §3.5.

---

## 10. Open questions

None blocking. One to watch: if `[filed-relink]`'s scheduled scan is built,
it and the deferred link re-derivation above are the same scan visiting
documents by two different keys (`ref_list` vs manufacturer). Build whichever
lands first with the other in mind rather than as one job.

---

## 11. Findings surfaced while writing this

Recorded because each was measured, and none is fixed here:

* **No index exists on `item_mirror.manufacturer_raw`.** The table carries only
  `item_mirror_pkey` and the `name` trigram index, yet every
  manufacturer -> documents *and* manufacturer -> items query filters on that
  column — hence the seq scan in §3.4. This is a one-line index that helps
  immediately and is **independent of this design**: it should be filed and
  shipped on its own rather than bundled here, since it improves the read path
  whether or not the binding lands.
* `3SHAPE MEDICAL A/S` carries 3 groups and has no `manufacturer` row. The new
  FK will refuse it on backfill, correctly and loudly. Already
  `[seed-orphan-report-masked-by-042]`.
* An untracked `scan.py` (805 B, imports `tools.corpus`) appeared in the repo
  root during this session and was not written by this work. Flagged, not
  touched.
* Two evidence values in the filed set — `Zip Sender GmbH` (4) and
  `Oversize AG` (3) — do not read like dental manufacturers and resolve to
  nothing. Possibly seeded or test data that reached the registry. Worth a
  look independently of this design.
