# Backfill matching: why documents will not link, and what to fix

Status: **implemented and merged 2026-08-11**. Written 2026-08-10 after
the corpus-first cold start (full LJ ingest, 15.958 items, 5.648 groups,
`DISCOVER_HOLD=true`) and before the first paid extraction run. All four tasks
landed: Task 0 per-run test database, Task 1 VALIDATE canonicalization
(extended after a live case-sensitivity bug), Task 2
document-facing aliases (corrected afterwards — Maillefer and Sirona
are their own BC brands, not DENTSPLY aliases), Task 3 per-manufacturer
`ref_normalize`. What still blocks a backfill document from linking is
upstream of everything here: nothing produces the `manufacturer` field this rule
set consumes — see `2026-08-11-ext-manufacturer.md`.

## The general idea

The tier ladder (T0 deterministic -> T1 text LLM -> T2 vision -> T3 human) is a
fallback ladder for **reading** a document. It has nothing to say about
**matching** the values it read to the catalogue. Those are two different axes,
and the system currently has depth on the first and none on the second.

Concretely: T2 can read "Ivoclar Vivadent AG" off a scanned DoC with perfect
fidelity, and the document will still fail to link, because the join compares
that string to the literal `'IVOCLAR'` we derived from BC vendor code 001. The
ladder cannot rescue a join failure — escalating a tier re-reads a value that
was already correct.

So the matching axis needs its own tolerance layer, and it needs it in the
database rather than in code, for the same reason the extraction templates are
data: **we do not know in advance how a manufacturer will write its own name or
number a product, and we cannot ship a code change per manufacturer.**

Two tolerance layers are missing:

1. **Name tolerance** — `manufacturer_alias`, which exists and is populated from
   the wrong direction only.
2. **Number tolerance** — per-manufacturer REF normalization, which does not
   exist at all.

Both are per-manufacturer data, and the playbook is already the per-manufacturer
data file. That is where they belong.

---

## Blocker 1 — the extracted manufacturer is never canonicalized

### What the code does

`backfill.scan` emits `extract.doc` with `group_id: None`
(`app/handlers/backfill.py:86`) — a backfill document has no requesting group, it
self-identifies from its own content. VALIDATE therefore takes the
`group_id is None` branch (`app/handlers/validate.py:302`):

```python
ext_manufacturer = _val(fields, "manufacturer")
if ext_manufacturer:
    group, match_count = _resolve_group_scoped(conn, ext_manufacturer, ref_list, basic_udi_di)
```

and `_resolve_group_scoped` (`validate.py:139`) does:

```sql
SELECT group_id, canonical_manufacturer, basic_udi_di
FROM item_group WHERE canonical_manufacturer = %s ORDER BY group_id
```

An **exact string equality** against the raw text extracted from the PDF. There
is no `manufacturer_alias` lookup anywhere in `validate.py` — verified by grep;
the only `canonical` references are column names in these queries.

### Why it cannot match

`item_group.canonical_manufacturer` is written by RESOLVE from
`manufacturer_alias`, whose `raw_name` column holds **BC vendor codes**:

| raw_name | canonical_name | source |
|---|---|---|
| 001 | IVOCLAR | playbook |
| 005 | IVOCLAR | playbook |
| 275 | IVOCLAR | playbook |
| 077 | KOMET | playbook |
| 041 | VOCO | playbook |
| 002 | INSTITUT STRAUMANN AG | vendor-master |

All 194 Ivoclar groups therefore carry `canonical_manufacturer = 'IVOCLAR'`. A
document says "Ivoclar Vivadent AG". `'Ivoclar Vivadent AG' = 'IVOCLAR'` is
false, so `_resolve_group_scoped` returns `(None, 0)`, no links are produced, and
the document lands in the manual queue regardless of how well it was read.

### Why the alias candidates do not exist

They were designed and never authored. Three separate observations:

* `manufacturer_alias` is populated from exactly two directions today — the
  S1.8 `vendor_master` import (BC code -> canonical) and RESOLVE's
  `_alias_lookup` self-seed, which inserts `raw -> raw` on a miss
  (`resolve.py:76`). **Nothing has ever written a document-facing name into it.**
* The playbook schema has a dedicated `aliases` field for this. It is `[]` for
  Ivoclar, Komet and VOCO; GC has `["GC"]` and Dentsply has `["DENSTPLY"]` —
  both BC-side spellings, not document-side ones.
* The data partly exists already, in the wrong field: playbook `match.anchors`
  carries `"voco gmbh"`, `"gc europe"`, `"dentsply"` — document-facing strings,
  used only for template matching, never for identity.

Per CLAUDE.md, playbook onboarding authoring is Phase 2 (S2.1). So this is
"designed, deferred, and now load-bearing" rather than an oversight — but it is
load-bearing *now*, because backfill is the only producer that resolves by
manufacturer name.

### Fix

Canonicalize `ext_manufacturer` through `manufacturer_alias` before the group
lookup, and populate the alias table with document-facing names.

Two constraints on the lookup:

* **Read-only.** RESOLVE's `_alias_lookup` inserts `raw -> raw` on a miss, which
  is right for a BC vendor code (a new supplier code is a real new manufacturer)
  and wrong for extracted document text (a garbled OCR name must not mint a
  manufacturer). VALIDATE must look up without inserting.
* **Never silent.** An unresolvable manufacturer is the single most likely reason
  a backfill document fails to link. It must be counted and surfaced on the job
  result, not inferred from an empty link list.

Matching should be case- and whitespace-insensitive; the alias table stores
`raw_name` as written, so normalize on both sides of the comparison rather than
mutating stored data.

---

## Blocker 2 — REF suffixes are market codes the catalogue does not carry

### What a "REF suffix" is

Measured over all 174 Ivoclar corpus PDFs with T0 only (deterministic, free):
3.878 distinct REFs extracted, of which

| shape | count |
|---|---|
| digits only | 2.553 |
| digits + 1-3 trailing letters | 1.171 |
| other | 154 |

The trailing letters are **market / region codes**, not part of the article
number. The proof is that one base number carries many of them at once — 124
base numbers appear with more than one suffix:

```
645986 -> DC DS EA EG ES EU FC FS IS JJ KS PB PP PS RS SS   (16 variants)
628518 -> DC DS EA EG ES EU FC FS IS JJ PP PS RS SS SU      (15 variants)
698703 -> WW XA XG XP XS XU YC YS                           (8 variants)
```

and the frequency ranking reads like a market list: `AN` 463, `WW` 324 (world
wide), `CN` 71, `US` 57, `BU` 19, `BE` 19, `IN` 16, `AA` 16, `AG` 13, `BG` 11.

A manufacturer's Declaration of Conformity legitimately enumerates every market
variant of a product, because the declaration covers all of them. Dentalia buys
one variant and BC records the base article number.

### Why BC does not carry them

It mostly does not, and this is the granularity difference, not a data error.
Of 8.876 non-null `item_mirror.mfr_ref` values:

| shape | count |
|---|---|
| digits only | 3.848 |
| digits + trailing letters | 135 |
| contains a space | 937 |

The 135 that do have trailing letters are a different species — `000470256D`,
`0032RA`, `027CRQ`, `02TOC` — not Ivoclar market codes.

So: the document identifies the SKU at **market granularity**, the catalogue at
**base-article granularity**. Neither is wrong; they are different keys, and the
REF gate compares them raw.

### What it costs us

Measured overlap between the 174 Ivoclar documents' extracted REFs and the 340
`mfr_ref` values on Ivoclar group members:

| normalization | documents with >=1 hit | catalogue REFs matched |
|---|---|---|
| exact (ships today) | **19 / 174** | 181 / 340 |
| strip leading zeros | 20 / 174 | 183 / 340 |
| **strip 1-3 trailing letters** | **54 / 174** | 248 / 340 |
| both | 54 / 174 | 250 / 340 |

Suffix tolerance alone takes linkable documents from 19 to 54 — a 2,8x
improvement — and catalogue coverage from 53% to 73% of Ivoclar REFs.

### Why a blanket strip would be a bug

937 BC refs contain a space, and Komet's ISO bur numbers are the documented
example (`104 H251EF 060`, cited in `app/handlers/ingest.py`'s `mfr_ref_prose`
comment as the reason only `"!"` is a safe prose marker). In those codes letters
are load-bearing. Stripping trailing letters globally would corrupt Komet
matching to fix Ivoclar matching.

### Fix

Per-manufacturer REF normalization, authored in the playbook, applied to **both
sides** of the REF gate comparison at match time. Never mutate the stored
`mfr_ref` or the extracted `ref_list` — normalization is a comparison rule, and
the raw values remain the evidence.

Note the playbook already has a `ref_strategy` field, but it names a *parsing*
strategy (`"stitched-table"`, `"text-column"`, `"table"`) — how to find the REF
list on the page. Normalization is a distinct concern and needs its own key.

---

## Ceiling, so nobody expects too much

Even fully fixed, Ivoclar is 644 of 15.958 items in processed scope and 479 in
strict scope: **4,0% maximum coverage** from this one folder. And 7.082 of the
15.958 ingested items (44%) carry no `mfr_ref` at all, so REF-list matching can
never reach them — only Basic UDI-DI or a human can. That figure is the real
AC1 ceiling, now measured rather than estimated.

---

## Tasks

### Task 0 — per-run test database (do first, alone)

`tests/conftest.py:67` hardcodes `TEST_DB = "dentalia_test"` and the session
fixture opens with `DROP DATABASE IF EXISTS dentalia_test WITH (FORCE)` +
`CREATE DATABASE`. `DENTALIA_TEST_ADMIN_URL` / `DENTALIA_TEST_URL` are
env-overridable but the DROP/CREATE uses the constant, so **no environment
variable can separate two concurrent runs**. The second run to start forcibly
kills the first's connections and recreates the database underneath it mid-run.

Observed 2026-08-10: three consecutive full-suite runs gave clean / `ERROR at
setup` / mid-run failure, purely from a concurrent run in another session. The
failures are indistinguishable from a real regression, which is what makes this
worth fixing before anything else.

Fix: derive the database name per run (pid, or a `DENTALIA_TEST_DB` env var that
the DROP/CREATE also honours).

**This task gates the parallel plan below** — until it lands, two agents running
pytest corrupt each other's results.

Files: `tests/conftest.py`, `docs/runbook.md` (Tests section).

### Task 1 — canonicalize the extracted manufacturer in VALIDATE

Resolve `ext_manufacturer` through `manufacturer_alias` before
`_resolve_group_scoped`'s lookup. Read-only (no self-seed). Case- and
whitespace-insensitive. Count and report unresolved manufacturers on the job
result.

Files: `app/handlers/validate.py`, `tests/test_validate_handler.py`.

### Task 2 — author document-facing aliases

Populate playbook `aliases` with the names manufacturers use on their own
documents, and make sure `cli playbooks sync` writes them into
`manufacturer_alias` with `source='playbook'`. Start from the strings already
sitting in `match.anchors`.

Files: `playbooks/*.json`, `app/cli.py` (`sync_aliases`) if the sync path does
not already carry `aliases`, `tests/test_playbooks_sync.py`.

### Task 3 — per-manufacturer REF normalization

Add a normalization rule to the playbook, apply it to both sides of the REF gate
comparison. Do not mutate stored values. Must leave space-containing codes
(Komet) untouched.

Files: `app/handlers/validate.py`, `playbooks/*.json`, tests.

---

## Sequencing

Task 1 and Task 3 both edit `app/handlers/validate.py`, so they cannot run
concurrently in one tree, and running them in two worktrees buys a merge
conflict in the same function. Task 0 and Task 2 touch disjoint files.

```
Task 0  (conftest)         -- solo, gates everything else
   |
   +-- Task 2 (playbooks)  -- parallel, disjoint files
   |
   +-- Task 1 (validate)   -- then Task 3 (validate), sequential
```

Task 3 depends on Task 1 in practice as well as in file terms: there is no point
tuning REF tolerance while every document still fails to find its group.
