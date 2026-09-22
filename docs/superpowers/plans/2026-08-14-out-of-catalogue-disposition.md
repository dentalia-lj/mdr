# PRD amendment C15 — `filed` disposition for out-of-catalogue documents

**Status: PROPOSAL, awaiting Denis. No code written. No migration written.**

Ratifying this means editing `docs/dentalia-pipeline-contract-prd-v3.md` (§7, §9,
changelog row C15), `docs/dentalia-schema-sketch.md`, `docs/vocabulary.md`, and
adding migration `025_doc_status_filed.sql` — contract first, handlers after.

---

## The problem, measured

The IVOCLAR backfill (173 documents, 2026-08-14) left 207 documents `staged`.
Investigating `doc_id 1361` (`Cention Forte, Cention Primer.pdf`) showed why:

| | |
|---|---|
| staged documents carrying a REF list | 181 |
| REF codes they name, in total | 1.605 |
| of those codes Dentalia stocks | **6** |

`Cention` returns zero rows in `item_mirror` by code or by name, with and
without the `WW` market suffix. The documents are read correctly, attributed to
the right manufacturer, and carry correct dates. Dentalia simply does not sell
the products they cover: a manufacturer's document set is their whole line, and
Dentalia buys a slice of it.

`staged` means *"probably belongs in the registry, a human should confirm."* No
human can confirm coverage of a product the company does not sell, so all 181
would be opened, rejected, and regenerated on the next sweep. A queue whose
items can only ever be rejected is worse than no queue: it trains the reviewer
to skim, and the genuine staged documents are hidden among them.

This is not a matching bug. The REF gate is behaving exactly as invariant 3
requires. It is a missing **disposition**.

## Denis's rulings (2026-08-14, verbatim)

1. *"kept if it refers to an item and the item needs certificates"*
2. *"the certiifcates that are covering entire brand, or ufi or other documents
   relted to company also should be kept even if they dont mention items"*

Applied to the 207:

| bucket | count | disposition |
|---|---|---|
| names items, MDR/MDD | 157 | keep — ruling 1 |
| manufacturer/brand scope (ISO 13485, QMS, company-wide) | 8 | keep — ruling 2 |
| names items, regulation `n.a.` (e.g. `Bluephase Meter II.pdf`) | 29 | keep — still a device-adjacent product document |
| explicitly non-device (`DOC - niso MD artikli.pdf`) | 2 | out of scope here — `[extract-scope]` owns it |
| remainder (MDR DoC, empty ref_list) | 11 | keep — ruling 2 |

Net effect of the rulings: **nothing is dropped.** Invariant 4 already forbids
deletion; these rulings say the same about *retention effort*. So this
amendment is purely about where a kept document rests, not about discarding
anything.

## Proposal

### C15 — `document.status` gains `filed`

`doc_status` becomes `staged | production | filed | rejected | superseded`.

**`filed`** = read successfully, manufacturer resolved, evidence complete, and
**no linkable item exists in the catalogue**. Terminal until the catalogue
changes. Archived, evidenced, dated, searchable — and in nobody's queue.

It is not `production`: a production document is one the registry asserts covers
specific items, and this one covers none. It is not `rejected`: nothing is wrong
with it. It is not `staged`: there is no decision for a human to make.

### Entry condition (VALIDATE decides, GATE writes)

A candidate is `filed` when **all** hold:

1. `manufacturer-unresolved` is absent — we know whose document it is.
2. Evidence is complete (the existing invariant-2 check, unchanged).
3. `links[]` is empty **and** the reason is `no-ref-overlap` — the manufacturer
   resolved and no REF matched. Any other empty-links reason keeps today's
   behaviour.
4. No BLOCKING flag survives.

Condition 3 is the load-bearing one: `no-ref-overlap` already means exactly
"we know the manufacturer, and none of the codes are ours". Today it is
INFORMATIONAL, which is right — it stops being informational only in
combination with zero links.

### What re-links a filed document

`filed` is terminal but not permanent. When INGEST mirrors a new item whose
`item_ref`/`mfr_ref` matches a filed document's stored `ref_list`, the document
should re-enter at `validate.doc`. **This needs its own decision** — the
cheapest version is a periodic scan, the correct version is INGEST emitting on
new-item insert. Not specified here; flagged as the follow-on.

### Manufacturer-scope documents (ruling 2)

An ISO 13485 / QMS certificate names no item by design. It must NOT be caught by
condition 3 — `coverage_scope = 'manufacturer'` documents already have their own
path (C4: one-time human binding, then automatic link derivation at
`match_basis = 'mfr-scope'`). This amendment leaves C4 untouched. The 8
manufacturer-scope documents in the staged pile are waiting on a C4 binding,
which is a different queue and a real human decision.

**Consequence to accept:** ruling 2 is satisfied by C4, not by `filed`.

## What changes where

| file | change |
|---|---|
| `migrations/025_doc_status_filed.sql` | `ALTER TYPE doc_status ADD VALUE 'filed'` |
| `docs/dentalia-pipeline-contract-prd-v3.md` | changelog row C15; §7 disposition; §9 `document.status` enum |
| `docs/dentalia-schema-sketch.md` | status vocabulary + access matrix |
| `docs/vocabulary.md` | §3 doc statuses + the drift guard picks it up automatically |
| `app/handlers/validate.py` | emit the `filed` recommendation |
| `app/handlers/gate.py` | honour it; audit event `filed` |
| `web/app.py` | `/documents` filter; staging board excludes `filed` |

Seven files, so this is **three tasks**, not one (CLAUDE.md rule 4):
contract+migration · handlers+tests · UI.

## Risks

- **`ALTER TYPE ... ADD VALUE` cannot run inside a transaction** in older
  Postgres and is irreversible. The migration runner applies files in a
  transaction — this must be checked before writing it, not after.
- **Every read surface that filters `status='production'` is unaffected**
  (filed is not production), but every surface that lists *non*-production
  documents must be re-checked, or filed documents reappear in the queue we
  just emptied.
- **The 6 real overlaps.** 6 of 1.605 codes DO match. Nothing here may file a
  document that has even one linkable item.

## Open question for Denis

Re-linking (the "what happens when Dentalia starts stocking Cention" path) is
the part with real design weight, and it is deliberately unspecified above.
Cheap version: a nightly scan comparing filed documents' `ref_list` against new
`item_mirror` rows. Correct version: INGEST emits `validate.doc` for every filed
document matching a newly mirrored item. The cheap one is small and can miss a
window; the correct one touches the INGEST contract.

Which, or ship `filed` first and decide re-linking separately?
