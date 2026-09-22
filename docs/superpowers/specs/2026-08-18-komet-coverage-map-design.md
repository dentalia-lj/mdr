# Komet coverage map — backfilling a manufacturer that indexes its own documents

Design document, 2026-08-18. **Nothing here is implemented.** Decision record
for review; §9 lists the one question still open.

Measured against the live corpus and database on 2026-08-18. Every number below
was read, not estimated; the command that produced it is named where it matters.

---

## 1. Why this exists

Komet is BC code `077`: 319 catalogue items, 92 groups, and — before this work —
**zero linked documents**. Four brand folders have been backfilled (GC, 3SHAPE,
IVOCLAR, STRAUMANN) and 1.698 items across five BC codes carry links today; not
one of them is Komet's.

The first attempt was to parse REF lists out of Komet's PDFs. It works, at a
price: every one of the 186 documents escalates past T0, 13 of them to vision,
and the run costs ~$11 to reach ~227 items. It also fails structurally on 81 of
Komet's 102 declarations, which carry no product list at all.

The corpus already contains the answer. Komet ships an index:

```
1-Where to find DoC (Medical Devices Only).xlsx     3.800 rows
    Article no. | Reference no. | UMNDS | Product family (Where to find DoC)
    000085K3    | H1.314.006    | 16-668| 532854

Artikli, kjer je originalni proizvajalec drug kot Komet/SPECIFIKACIJA.docx
    Article no (Komet) | Reference no. | Where to find DoC
    043690K0           | 9978.000.000  | 533221
```

That last column is the document key. It resolves two ways, both present in the
tree: as a filename prefix under `DOC/` (42 product families) and as a folder
name under `Artikli.../` (11 numbers).

Two counts appear below and they are not the same thing: the spreadsheet names
**42** product families, while **49** distinct family numbers can be read off
`DOC/` filenames. The extra seven are numbers Komet ships documents for without
listing them in the index — including the three written with a slash in the
spreadsheet (`100/008`, `101/349`, `103/119`) and a dash or underscore in the
filenames. Every one of the 42 has at least one file; the seven extras produce
documents with no map links, which is the same state as any unmapped document.

Matched against the catalogue — folding both sides through
`validate._fold_bur_code`, because Dentalia stores the reference in spaced form
(`000 GPFQ04 020`) and Komet prints it dotted (`GPFQ04.000.020`) — the two files
cover **246 of the 319 items**, versus ~227 for PDF parsing, with no parsing and
no model calls, and stated by the manufacturer rather than inferred by us.

## 2. Scope

**In:** the 42 `DOC/` product families (229 items), a one-shot tool, and the
document-selection rule that decides which of Komet's files become documents.

**Phase 2, sequenced after:** the 11 `Artikli/` folders (16 items), which need a
manufacturer re-binding first — §7.

**Out:** anything recurring. `backfill.scan` is not scheduled (`app/scheduler.py`
emits `ingest.monthly`, `ingest.run`, `email.request`, `report.weekly`,
`playbook.reonboard` and nothing else), and steady-state refresh is
`doc_sources[kind:"direct"]` → `fetch.url`, owned by the playbook-expansion
work in `2026-08-18-playbooks-across-stages-design.md` item 3. This design does
not touch that path and must not pre-empt it.

## 3. Decisions taken

| # | Decision | Denis, 2026-08-18 |
|---|---|---|
| D1 | A map-derived link is **production-capable**, under a new `match_basis` `map-supplier` | "Production-capable (map-supplier)" |
| D2 | The `Artikli/` tree is **in**, at production like the rest | "In, production like the rest" |
| D3 | Those 16 items get their **manufacturer re-bound** to the real issuer rather than invariant 3 gaining an exception | approach 1 |
| D4 | The map runs as a **CLI tool**, not as a stage in the live pipeline | "this only happens with komet… might kill the others" |
| D5 | **One document per `(family, regulation)`**; Komet's own merged file is canonical where it exists | "this should solve it" |

D1's rationale: invariant 3 permits auto-write on `(canonical_manufacturer,
catalogue article number)` overlap, and a map row is exactly that pair, asserted
by the manufacturer about its own article. That is stronger evidence than a
regex reading the same manufacturer's PDF, which is already production-capable
as `ref-list`.

D4's rationale: the map is a migration artifact. Putting it in VALIDATE would
make every document, forever, pay a guard for something that ran once. The CLI
already carries one-off registry work of exactly this shape — `cmd_repair_ref_list`
appends `extraction_attempt` rows and re-emits jobs at the new revision.

## 4. Which files become documents

Komet ships 186 PDFs in three trees. Classified against the rule below, 184 of
them key cleanly:

| files | class | disposition |
|---|---|---|
| 85 | `DOC/` declaration | document, when canonical for its `(family, regulation)` |
| 43 | `DOC/` annex (`_RA_812_Liste_`, `_Liste.pdf`) | archived, **not** a document |
| 26 | `DOC/` merged (`_List_SIGNED`, `_DoC_List_`) | **canonical** document where present |
| 24 | `Artikli/` | phase 2 |
| 6 | `CE/` | manufacturer-scope; today's `mfr-scope` basis already covers it, no family key needed |
| 2 | no family key | see below |

Grouping the 49 family numbers by which shapes they ship:

| families | shipped |
|---|---|
| 24 | annex + MDD declaration + MDR declaration + **merged MDR** |
| 11 | annex + MDD declaration + MDR declaration |
| 5 | declaration only |
| 4 | annex + declaration |
| 2 | MDR declaration only |
| 3 | one-offs |

So the dominant case is four files for **two** logical documents — one MDD DoC,
one MDR DoC — where the list is shared by both and the MDR one exists twice,
bare and merged.

**The rule: one document per `(family, regulation)`. Canonical bytes are the
merged file when Komet published one, otherwise the bare declaration. The annex
and the redundant bare form are archived and retrievable, but are not documents.**

Three consequences:

1. **No synthetic merged PDF is ever created.** For 24 families Komet already
   published one, and its code set is identical to the annex's — verified on
   `532797`: merged 2 pages / 10.188 chars / 22 codes, declaration 1 page /
   3.178 chars / 0 codes, annex 1 page / 6.956 chars / 22 codes, and
   `merged ∩ annex` differs by nothing in either direction. Where Komet did not
   publish one, the companion-annex mechanism already committed
   supplies the list. A merged artifact of our own making would be a document
   the manufacturer never issued, sitting in a compliance archive.
2. **The duplicate collision is what this rule prevents.** Left alone, the bare
   MDR declaration and the merged MDR file both become documents with identical
   `(coverage subject, type, regulation)` — invariant 5's supersession triple —
   and race to supersede each other. Picking one canonical form per pair is the
   same reasoning that makes the annex not-a-document.
3. **Every original file stays reachable.** Only *which file the document row
   points at* changes; all 186 remain archived by content hash. Siblings are
   recoverable without a migration, since every file's `source_url` carries the
   family number, so "the other three files for 532797" is a query. The document
   detail page needs an affordance to show them; that is the one UI addition.

The two unkeyed files: `26.05.2024_100-008_DoC_List_EU.pdf` carries its family
in the middle of the name rather than the front and is recoverable;
`Komet - GP - EU Declaration of Conformity (Rev.11, 2025.05.02).pdf` carries no
number anywhere and goes to the manual queue rather than being guessed at.

## 5. Coverage, and the residue

246 of 319 items are covered. Crossed against BC's own medical-device flag:

| `md_flag` | in the map | items | reading |
|---|---|---|---|
| true | yes | **229** | the working set |
| true | no | **29** | BC calls it a device, Komet's MD-only index omits it — a real hole |
| null | no | 44 | consistent with the file's title, *Medical Devices Only* |
| null | yes | **17** | Komet treats it as a device, BC's flag is empty |

So the map answers **229 of the 258 items BC flags as medical devices (89%)**,
plus 17 BC never flagged.

Residue handling, none of it silent:

- **29 uncovered devices** — counted on the job result. They are what DISCOVER
  is for, once Komet has `doc_sources`.
- **44 unflagged non-devices** — left alone. Claiming coverage for something
  that is not a device is worse than claiming none.
- **17 flag disagreements** — linked, and raised as a `data_anomaly` so the BC
  flag can be corrected at source.
- **1 unkeyed file** — manual queue.
- **`533231`** — named in `SPECIFIKACIJA.docx`, no folder and no file anywhere
  in the tree. Counted, not fatal.

## 6. Architecture

```
backfill.scan       archives every PDF                        (unchanged)
                    + archives the declared map files          (new, small)

extract.doc → validate.doc → gate.candidate → document         (unchanged)

app/cli.py komet-coverage                                      (new)
    reads the ARCHIVED map files
    resolves family → canonical document
    appends an extraction_attempt at rev+1 whose ref_list is the map's,
      carrying the map file's archive_url on that field
    emits validate.doc at the new rev
                    ↓
            existing C1 gate forms the links, GATE writes them
```

GATE remains the only writer of `document` / `item_document` / `evidence`
(invariant 1). No new job type, so the closed enum is untouched (invariant 7).
No payload field is removed (invariant 9). Nothing is deleted (invariant 4).

**Why `extraction_attempt` and not direct link insertion:** it is the mechanism
`repair_ref_list` already uses, it keeps VALIDATE's flag computation — date
sanity, supersession, the downgrade guard — applied to the document rather than
bypassed, and the resulting evidence row carries the map file as its
`archive_url`, which is what invariant 2 asks for. A 2026-08-18 change made a per-field
`archive_url` expressible for exactly this reason.

### Playbook schema

The rule is manufacturer knowledge, so it lives with the rest of it:

```json
"coverage_map": {
  "sources": [
    {"file": "1-Where to find DoC (Medical Devices Only).xlsx",
     "sheet": "Medical Devices Only - find DoC",
     "columns": {"article": "Article no.",
                 "reference": "Reference no.",
                 "key": "Product family (Where to find DoC)"}},
    {"file": "Artikli, kjer je originalni proizvajalec drug kot Komet/SPECIFIKACIJA.docx",
     "columns": {"article": "Article no (Komet)",
                 "reference": "Reference no.",
                 "key": "Where to find DoC"}}
  ],
  "key_resolves": [
    {"kind": "filename-prefix", "under": "DOC"},
    {"kind": "folder", "under": "Artikli, kjer je originalni proizvajalec drug kot Komet"}
  ],
  "canonical": {"prefer": ["_List_SIGNED", "_DoC_List_"], "annex": ["_RA_812_Liste", "_Liste.pdf"]}
}
```

Authored for Komet only, and a test holds it there — the same authoring guard
`ref_normalize` and `companion` already carry.

## 7. Phase 2 — the 16 cross-manufacturer items

`SPECIFIKACIJA.docx` exists to say *"articles whose original manufacturer is
other than Komet"*: its 39 rows point at documents issued by Shofu, MetaBiomed,
Becht, Stoddard and TÜV SÜD, while the items sit in groups whose
`canonical_manufacturer` is KOMET (all 319 are, in 92 groups, none ungrouped).

Linking them as they stand would assert coverage across a manufacturer boundary,
which is what invariant 3's overlap rule exists to prevent. Per D3 the data gets
corrected rather than the rule weakened: those items are regrouped under the
issuer their own documents name, read from T0's `manufacturer` field rather than
from the folder — consistent with the principle already in the code, *"the corpus
folder is the supplier, not the manufacturer"*. `item_group.canonical_manufacturer`
is free text and the `manufacturer` table is empty (0 rows, it holds contact and
EUDAMED metadata), so this is grouping work, not entity modelling.

Phase 2 is deliberately not detailed further here. It needs its own design once
phase 1 has run, because the regrouping interacts with RESOLVE.

## 8. Failure modes and testing

1. **Header shape is asserted, not assumed.** The playbook names the columns; a
   renamed, reordered or inserted column fails the job. A silently shifted
   column would re-point every article at once and look like success.
2. **A key with no file is counted, not fatal** — `533231` is that case today.
3. **Two canonical candidates for one `(family, regulation)` is a refusal**, not
   a guess. The same discipline as the annex ambiguity fixed the same day: the
   `533173` Klasse_I / Klasse_Is pair was silently resolved last-one-wins and
   handed a Class Is list to a Class I declaration.
4. **The map's `content_hash` is recorded on the job result.** A re-run against a
   changed map is a human decision, not a silent re-map. This is the only
   survivor of the update discussion, and it is one line rather than a
   versioning subsystem, because §2 establishes the tool is one-shot.
5. **Re-running is free.** GATE upserts, so a second run produces the same links
   and no second document.

Testing, against a real Postgres (CLAUDE.md forbids mocking it):

- The docx reader is split from the coverage logic, so the awkward half is
  tested on a crafted XML string rather than a committed binary; the xlsx
  fixture is built in-test with openpyxl.
- Table-driven: header renamed → job fails; key with no file → counted; two
  canonicals → refused; `md_flag` disagreement → anomaly; second run →
  idempotent; a map link reaches `production` while an ordinary one still faces
  its usual gate.
- A corpus floor test pinning the measured numbers — 229 of 258 flagged
  devices, 246 of 319 total — in the shape `t0_ref_min` already uses. It fails
  when the map or the catalogue moves, which is when somebody should look.
- Every test mutation-checked before it is kept.

## 9. Open question

**How does a link learn it came from the map?**

D4 puts the tool outside the live pipeline, and §6 routes its output through the
existing `validate.doc` → `gate.candidate` path so that VALIDATE's flags still
apply. But VALIDATE is what assigns `match_basis`, and as written it would call
these links `ref-list` / `ref-item` — losing the `map-supplier` distinction D1
asked for.

Two ways, and this needs a ruling:

**(a) A three-line, universal rule in VALIDATE.** The `ref_list` evidence carries
a marker (`"source": "coverage-map"`); when present, the basis is
`map-supplier`. Data-driven rather than manufacturer-gated, and inert for every
document in the registry today, since none carries the marker. Cost: it is a
change to the shared path, which D4 was chosen to avoid — though a universal
rule triggered by data is a much smaller risk than a per-manufacturer branch.

**(b) Drop the distinct basis.** The links land as `ref-list` / `ref-item` and
the provenance lives in the evidence row's `archive_url`, which already points
at the archived spreadsheet. Nothing on the live path changes at all. Cost: the
basis names a REF list that is not in the document, and querying "which links
came from the map" means joining through evidence.

Recommendation: **(a)**. `match_basis` is what the registry keeps as provenance,
and it should name the thing that actually matched.
