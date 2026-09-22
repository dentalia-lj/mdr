# Linking backfill documents: REF-first, manufacturer second

Status: **Phase 1 implemented 2026-08-11.** Phase 2 (`ext.manufacturer`) still
open. Written 2026-08-11, revised the same day after measuring the premise
instead of trusting it.

## Phase 1 outcome (measured, not projected)

Migration 021 (`ref-catalogue` barred from production), `cfg.validate.
min_unscoped_ref_len`, and the guarded unscoped path in `validate.py`. Suite
970 passed / 1 skipped (from 962/1 — the 8 new tests). Every guard
mutation-tested: removing any one of the six turns its own test red, and
deleting `ref-catalogue` from the migration's CHECK turns the DB-level test red.

Verification step 1 (free) settled the two open questions:

| | raw | normalized (global strip) |
|---|---|---|
| distinct REFs | 5.511 | 5.498 |
| genuine cross-manufacturer collisions | **12** | **18 (+50%)** |
| extra documents matched, over 61 real attempts | — | **0** |

So **edge case 4 is closed by measurement: normalization is NOT applied when
inferring the manufacturer.** It costs 6 extra collisions and buys nothing. It
still applies afterwards, inside `_ref_gate_for_group`, once the manufacturer is
pinned — that is the ordinary scoped case.

Ten collisions survive the `>=6` + prose guards and are exactly what the
ambiguity check exists for. One is live: **`878115` is held by both
`GC EUROPE N.V.` and `VOCO`.** Others are `10033600` (ADEC | PLANMECA),
`110 0608` (KOMET | SCHLUMBOHM), `7117-033-00` (DUERR DENTAL | EMS). Several are
not real conflicts at all — `205363` is 3M UNITEK/SOLVENTUM after a spinoff, and
the HENRY SCHEIN pairs are a distributor rather than a second manufacturer.

Verification step 4 (free, in a rolled-back transaction over the 61 persisted
extractions) matched the projection exactly: **5 documents linked**, all at
`ref-catalogue`, all reaching `GC EUROPE N.V.`, zero ambiguous. Unplanned
dividend: `no-ref-overlap` fires on **21** candidates that until now staged with
no links and no stated reason.

## What changed in this plan, and why

The first draft made `ext.manufacturer` the blocker: a backfill document cannot
find its `item_group` without a manufacturer to scope on, so extract the
manufacturer and everything follows. That framing came from PRD C1:

> The matching key is always the **pair `(canonical_manufacturer, mfr_ref)`** —
> bare article numbers collide across manufacturers.

True in principle. Measured against this catalogue, barely true in practice:

| | |
|---|---|
| distinct REFs across all groups | 5.511 |
| unique to exactly one manufacturer | 5.487 |
| colliding across manufacturers | **24 (0.44%)** |
| — of those, prose already flagged `mfr_ref_prose` (`NI VEČ DOBAVLJIVO!`, `UKINJENO!`) | 12 |
| — **genuine article numbers** | **12 (0.22%)** |

And some of those 12 are not real collisions: `205363` maps to
`3M UNITEK | SOLVENTUM`, one company after a spinoff.

So requiring a manufacturer before *any* link can form is charging a full
extraction field to defend against a 0.22% risk. **A REF match alone is enough
to propose a link.** It is not enough to auto-write one — see the edge cases,
which is where the real design lives.

## Evidence from the live run

Against the 61 real GC extractions already in the database:

```
27 of 61 documents extracted a ref_list
 8 distinct REFs match a catalogue mfr_ref
 5 documents carry at least one match
 1 distinct manufacturer hit  -- GC EUROPE N.V., no ambiguity
```

Those 5 documents would link **today** with no extraction change. Adding the
manufacturer field takes the same 5 from 0 links to 5 links — it unlocks nothing
extra. The binding constraint is REF overlap (27/61 have refs at all, 5 match),
not identity.

---

## Edge cases — this is the substance

Unscoped matching is cheap but not free. Each hazard below was measured, not
imagined.

### 1. Short REFs are not distinctive (measured: 85 refs of 1-3 chars)

Length distribution of catalogue REFs: 1 char × 1, 2 × 3, 3 × 81, 4 × 254,
5 × 428, then 6 × 1.335, 7 × 1.156, 8 × 958.

The short ones are bare numbers: `100` -> INTERDENT, `102` -> SANOLABOR,
`116` -> INTERDENT, `136` -> CARL MARTIN, `158` -> ULTRADENT, `195` -> KERRHAWE.
Each is "unambiguous" in the collision sense, and each is worthless as identity:
a GC declaration listing REF `100` would link to an INTERDENT item.

**Guard: minimum REF length.** At >= 6 characters, 4.744 of 5.511 REFs (86%)
remain eligible, and all 8 of the real GC matches survive (every one is 6 chars).
The threshold is config, not a literal.

### 2. Prose masquerading as a REF (measured: 49 distinct containing `!`)

`NI VEČ DOBAVLJIVO!`, `NI VEČ NA VOLJO!`, `UKINJENO!`, `OPERA!`. INGEST already
detects these (`mfr_ref_prose`, the deliberate `"!"`-only rule from the
vendor-master analysis) but still stores them, so they sit in
`item_group_member.mfr_ref` and are matchable. The collision guard catches the
ones spanning several manufacturers; it does NOT catch a prose value unique to
one. **Guard: exclude any REF matching the `mfr_ref_prose` rule.**

### 3. Catalogue-observed uniqueness is not global uniqueness

The deepest one, and the reason this plan does not simply auto-link. Our
collision measurement covers only what Dentalia stocks. REF `698946` mapping to
one manufacturer *here* says nothing about whether another manufacturer uses
`698946` for a product Dentalia does not carry. CLAUDE.md states the rule
directly: absence of evidence is not evidence of absence.

**Guard: an unscoped match links at a capped `match_basis` and can never reach
`production` without a human.** This is an established pattern, not a new
mechanism — `item_document_trusted_basis_ck` already bars `name-family` and
`fetch-context` from `production` at the DB level, with `TRUSTED_BASES` in
`gate.py:37` as the code-side twin. A new basis (proposed name `ref-catalogue`,
NOT `ref-unscoped` — that string is already a flag name and reusing it would be
confusing) joins the barred set.

Consequence: unscoped links appear in staging for review, count toward nothing
until approved, and cannot silently attach one company's certificate to another's
product. That is the whole safety argument.

### 4. Normalization multiplies collisions — measure before combining

`ref_normalize` strips market suffixes, so `698946WW` -> `698946`. Two
manufacturers whose raw REFs differ may collide once normalized. The collision
set MUST be computed over normalized values whenever normalization is applied to
the unscoped path. **Unmeasured today** — measure before enabling the two
together, and if the normalized collision rate is materially worse, keep
normalization on the scoped path only.

### 5. A document spanning several manufacturers

A distributor's combined declaration can carry REFs that unambiguously match two
or more different manufacturers. Picking one would be arbitrary. **Flag and
stage, never choose** — same shape as the existing `multi-group-match`, which
already exists for the scoped equivalent.

### 6. Catalogue drift

"Unambiguous" is evaluated at match time. A later ingest can introduce a
collision that retroactively makes a past link wrong. Mitigated by (3) — the link
is staged, so a human sees it before it counts — but worth stating rather than
discovering.

### 7. Case, whitespace, and standards numbers

Fold both sides consistently, reusing the existing rules rather than forking
them. Note that a 6-digit numeric REF can coincide with a standards reference
(EN/ISO numbers) appearing in body text; the >= 6 guard reduces this but does not
eliminate it. REFs come from `extract_ref_list` (table/column parsing), not free
text, which is the real protection — but say so in the code, because it is load
bearing.

---

## Phase 1 — guarded unscoped REF linking (no extraction change)

`_resolve_group_unscoped` already exists in `validate.py` and is deliberately
distrusted: it is used only to raise the `ref-unscoped` flag, never to link.
Promote it, with the guards above.

For a `group_id`-null document whose manufacturer is unknown:

1. Take extracted REFs that pass the guards (length, not prose, normalized
   consistently).
2. Look up which `canonical_manufacturer`(s) hold them.
3. Exactly one manufacturer, and the REFs are unambiguous -> link the matching
   members at `match_basis = 'ref-catalogue'`, capped at staged.
4. More than one manufacturer -> flag, no auto-link.
5. No match -> flag `no-ref-overlap` (see below), no links.

Changes: `app/handlers/validate.py`, a migration extending
`item_document_trusted_basis_ck`, `gate.py` (leave `TRUSTED_BASES` alone — the
point is that it is NOT trusted), PRD C1/C5 + handbook link-basis tables.

## Phase 2 — `ext.manufacturer` (still worth it, no longer the blocker)

With Phase 1 in place the manufacturer field earns a narrower, clearer role:

* **Upgrades a link from capped to trusted.** A scoped `(manufacturer, ref)`
  match is `ref-list` basis, which CAN reach production. That is the difference
  between "a human must confirm every corpus link" and "the confident ones flow".
* **Disambiguates** the 0.22% and the multi-manufacturer documents.
* **Covers documents with no usable REF** — 34 of 61 GC documents extracted no
  REF at all. Identity is the only handle they will ever have.

Design as previously specified, and the specifics still hold:

* **T0 producer** matching playbook **aliases** (legal names read from the corpus
  — `Gebr. Brasseler GmbH & Co. KG`), never `match.anchors`. The anchors are
  document-management artifacts and nomenclature strings (`otcs`, `umdns code`)
  that would resolve a Straumann DoC to KOMET, and never the folder name, which
  is the supplier not the manufacturer (the DENSTPLY folder holds Maillefer,
  Sirona and VDW documents — codes 022, 012, 010).
* **T1/T2** via `TARGET` + `_FIELD_VALUE`; the generated `SCHEMA` (llm.py:57)
  keeps both tiers in step automatically.
* Costs no extra LLM call: measured on the GC trial, **T1 ran on 60 of 61**
  documents already, because T0's `coverage_scope` confidence (0.6/0.9 on a DoC)
  trips `_ESCALATE_ALWAYS` on its own.
* Prompt must demand the legal entity as printed, with legal form, `null` rather
  than a guess, and explicitly NOT the authorised representative (`Dentsply IH
  Limited`, 66 corpus files, always the representative).

## Tier/field parity as a contract

* PRD §5 owns the field list; handbook §5 restates it. Becomes ten in Phase 2.
* **T1 <-> T2 parity is already structural** — one generated `SCHEMA`, one
  `output_config` call site. Nothing to add.
* **T0 parity is neither enforced nor true**: `t0_extract` produces 8 of 9 and
  silently skips `referenced_docs`. Add a parity test (every `TARGET` name is
  either produced by T0 or listed in a named `T0_EXEMPT` constant with its
  reason) so a tenth field cannot be half-wired.

## The silent state, fixed in Phase 1

```python
if group is not None: ...
elif groups_found == 0: flags.append("manufacturer-unresolved")
```

`group is None AND groups_found > 0` — manufacturer known, groups found, no REF
overlap — currently falls through with no flag and no count. On the Ivoclar
measurement that is roughly 120 of 174 documents: staged, zero links, no stated
reason. Add non-blocking flag `no-ref-overlap`. It changes no disposition
(`ref_gate` is already False, so production is unreachable via gate.py:368) but
it is the channel staging renders, and it separates "we do not know whose this
is" from "we know whose it is and it covers nothing you stock".

## Verification

1. **Free, first:** compute the collision set over raw AND normalized REFs across
   the whole catalogue; report how many documents Phase 1 would link per corpus
   folder, and every multi-manufacturer document. No API calls.
2. Unit tests with **mutation testing** — break it, confirm red, restore. Prove
   at minimum: a short REF does not link; a prose REF does not link; an ambiguous
   REF does not link; a `ref-catalogue` link cannot be written as `production`
   (the DB CHECK must reject it, not just the code); a multi-manufacturer
   document flags instead of choosing.
3. Full suite (baseline `962 passed, 1 skipped`).
4. Re-run VALIDATE over the 61 existing GC extraction attempts — **this is free**,
   validate.doc makes no LLM calls, and the attempts are already persisted.
   Expect 5 documents to link. That is the end-to-end proof, at zero cost.
5. Only then consider a paid re-extraction for Phase 2.

## What neither phase fixes

7.082 of 15.958 items (44%) carry no `mfr_ref` at all, so REF matching can never
reach them. Basic UDI-DI or a human is the only path. This plan makes the ceiling
visible; it does not raise it.
