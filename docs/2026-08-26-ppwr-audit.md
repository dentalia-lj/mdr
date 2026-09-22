# PPWR audit — GC's packaging declarations against the device corpus

**Date:** 2026-08-26 · **Status:** analysis complete; **ruled out of scope 2026-08-26** (§7); no code changed · **Cost:** three T1 calls (`claude-haiku-4-5`, §6.1); everything else is T0 and text, free

Measured over the 980 PPWR declarations GC published on 2026-08-05 (archived
a local scratch directory) and all 149 GC PDFs in the SFTP corpus, both
run through the same `t0_extract` the pipeline uses.

---

## 1. What was asked, and the short answer

Whether GC's new PPWR declarations are a threat to the registry, where they
differ from the device declarations we already hold, and what — if anything —
should be built.

**They can reach production, and the 2026-08-25 alarm stands — though not for
the reason first given.**

The audit ran in two halves and they disagree, so read both. On T0 evidence alone
(§5) these documents are safe: no REF list is extracted, so they route to the one
GATE path that *is* guarded, and land at `staged`. That was the finding as of the
first pass, and it corrects the 2026-08-25 claim about the mechanism.

Then the protection was measured rather than reasoned about (§6.1). It does not
survive T1. Asked the exact question the pipeline asks, T1 answered
`coverage_scope = group` on 3 of 3 documents, which puts `ref_list` back in the
T2 target list and reopens the REF branch — the branch with no device check on
it. And the T0 safety could never have held anyway: `regulation` is in
`_ESCALATE_ALWAYS`, so the T1 call is guaranteed, on every document, always.

Behind that there is no second line. The numbering mismatch that was expected to contain
the blast radius does not exist (§6.2).

Nothing is on fire — the files are outside `imports/` and nothing ingests them
until someone moves them. But the fix at §7 is no longer contingent on anything.

---

## 2. What a PPWR declaration is

Two pages, one article, identical template across all 980.

```
GC Europe Head Office, Interleuvenlaan 33, 3001 Leuven

Declaration of Conformity in accordance with Article 39 of Regulation (EU)
2025/40 on packaging and packaging waste (PPWR)

We, GC EUROPE N.V. … in our role as manufacturer according to article 3(13)
of the Regulation, ensure and declare under our sole responsibility that the
package:

    Fuji Plus powder 15g
    Article: 10000084

…is in conformity with:  Regulation (EU) 2025/40 (PPWR)
                         Regulation (EC) 1907/2006 (ReaCh)

Packaging description
This package is a multi-material package meant to protect the medical device
contained in it…

  Item No.   Designation                    Material            Weight (g)
  30000592   Barcode Label 32x60mm          Paper (label)       4
  30000597   Cardboard box w/ lid           Paper/cardboard     750
  30001234   BOTTLE BROWN FUJI PLUS         Plastic (bottle)    34
  …

Substances of concern (Art. 5 PPWR)  …lead, cadmium, mercury, hexavalent
chromium below 100 mg/kg… PFAS provisions do not apply.

Leuven, 29/07/2026     Mario Minale
                       General Manager Regulatory, EHS and Sustainability
                       Version: 1
```

The declared object is the **box**: label, carton, bottle, cap, tape, tray
insert, each with a gram weight. Note the trap in the middle — the document
says the words *"medical device"*, because it is describing what the box
protects. Any guard keyed on whether a document mentions a medical device
fails on all 980.

---

## 3. Where they differ from the device corpus

Both populations scanned in full. Counts are documents containing the marker.

| Marker | PPWR (n=980) | GC corpus (n=149) |
|---|---|---|
| `2025/40` (PPWR) | **980** | **0** |
| "Article 39" | **980** | **0** |
| "packaging and packaging waste" | **980** | **0** |
| `1907/2006` (REACH) | 980 | 6 |
| `2017/745` (MDR) | **0** | **130** |
| `93/42` (MDD) | 0 | 2 |
| Basic UDI-DI | **0** | **124** |
| ISO 13485 | **0** | **123** |
| "declaration of conformity" | 980 | 124 |

**Zero documents in either population cite both regimes.** There is no combined
MDR+PPWR declaration to worry about — the separation is total, in both
directions, on four independent signals.

Structurally, a GC *device* declaration carries what a PPWR one has none of:
manufacturer SRN (`BE-MF-000001608`), Basic UDI-DI (`++J022MD0105JZ`), risk
class and rule (`IIa`, rule 7), notified body (BSI, 2797), a certificate number
(`MDR 778483`), harmonised standards (ISO 4823:2015), and an article-list table.
Same company, same signatory name, different job title, different document.

---

## 4. What our extractor makes of them

All 980, no variance whatsoever:

| Field | Value | Confidence |
|---|---|---|
| `doc_class` | `compliance-doc` | — |
| `type` | `DoC` | **1.00** |
| `manufacturer` | `GC EUROPE N.V.` | **0.97** |
| `coverage_scope` | `manufacturer` | 0.60 |
| `validity_from` | `2026-07-29` | 0.95 |
| `regulation` | *absent* | — |
| `ref_list` | *empty* | — |
| pages | 2 | — |

`regulation` is absent because T0 only recognises MDR and MDD markers
(`extract_regulation`, `app/extract/t0_templates.py:276`). It is in
`_ESCALATE_ALWAYS`, so T1 is asked — and T1's schema admits only
`MDR | MDD | n.a.`, where `n.a.` is deliberately "a REAL value, not an absence"
(`app/extract/llm.py:120`). So every one of these lands as **`DoC / n.a.` with a
resolved GC manufacturer**: a well-formed device declaration to every stage
downstream.

---

## 5. Which path they take — correcting the 2026-08-25 reading

The 2026-08-25 reading had these taking GATE's REF branch to `production`,
because `_is_md_document` guards only the manufacturer-binding path. **That is
wrong for these documents**, and the reason matters.

`t0_extract` cannot find a REF list, so `derive_coverage_scope` falls back to
`manufacturer` at 0.6. And `needs_escalation` suppresses the item-identifier
fields on the *value*, not the confidence:

```python
cov = (fields.get("coverage_scope") or {}).get("value")
if cov != "manufacturer":   # unknown scope still pursues an id (conservative)
    missing += [f for f in _ESCALATE_ITEM_ID if _below(fields, f, threshold)]
```
`app/extract/tiers.py:131-133`

Confirmed by running it: `needs_escalation` returns
`['regulation', 'validity_to', 'coverage_scope', 'cert_number']` — **`ref_list`
is not chased**, at T1 or T2. No REF list ever exists, so the REF branch is
never reached. They route to `_handle_mfr_binding`, where two independent
conditions both refuse:

- `scope_stated` requires `coverage_scope` confidence ≥ `cfg.gate.high` = **0.92**. It is **0.60**.
- `_is_md_document` requires `regulation ∈ (MDR, MDD)` or `type ∈ (ISO,)`. It is `n.a.` / `DoC`.

**Outcome: `staged`, plus one review task per document.** Not production. The
existing guard does its job — *on T0 evidence*. §6.1 measures what happens when
T1 is asked, which it always is, and the conclusion reverses.

---

## 6. Why that is not good enough

**6.1 The protection is one LLM answer wide.** `coverage_scope` *is* escalated,
and `needs_escalation` is recomputed after T1 (`app/extract/tiers.py:304`). The
document says *"the package: Fuji Plus powder 15g / Article: 10000084"* — one
named article. `group` is the natural reading, and the T1 enum offers exactly
`group | manufacturer | null`. If T1 answers `group`, `ref_list` re-enters the
T2 target list, vision reads the article number, and the REF branch opens — the
branch with no `_is_md_document` check on it.

**Measured 2026-08-26, three documents, real T1 (`claude-haiku-4-5`), the exact
escalation list the pipeline computes.** T1 answered `coverage_scope = group` on
**3 of 3**, at confidence 0.95 / 0.85 / 0.95, citing the article line as its
verbatim ("Fuji II Cap box of 50 caps assorted, Article: 10000020"). Recomputing
`needs_escalation` on the merged fields returns `ref_list` every time.

**The REF branch reopens on all three.** The protection described in §5 does not
survive first contact with T1 — it holds only for as long as no LLM looks at the
document, which is never, because `regulation` is in `_ESCALATE_ALWAYS` and
guarantees the call happens.

What T1 returned for the other three fields is worth recording, because it is the
whole argument for §7.5:

| field asked | T1 answer | its own verbatim |
|---|---|---|
| `regulation` | `n.a.` | "Regulation (EU) 2025/40 (PPWR) and Regulation (EC) 1907/2006" |
| `validity_to` | *none* | "No expiry date stated in the document" |
| `cert_number` | *none* | "This is a Declaration of Conformity, not a certificate" |
| `coverage_scope` | **`group`** | "…Article: 10000020" |

Three of the four fields come back empty. The single field the document does
answer is the one that opens the hazard.

**6.2 If it opens, numbering does not save us.** The 2026-08-25 reading had most
missing because PPWR keys on GC's 8-digit Material number while our GC matches are
6-character. Measured against the documents: **115 of 124** GC MDR declarations
print *both* the 6-digit legacy code and the 8-digit new code (e.g. `000286` and
`10000101` for Caviton Jar White 30g, in adjacent columns of the same table). GC
is mid-migration and publishes both, so the catalogue is matchable in both
spaces. Treat the exposure as all 980, not 91.

**6.3 The safe outcome is still expensive.** 980 documents at the measured GC
rate of $0.0420/doc ≈ **$41** of extraction, against a €10–40/month steady-state
target — for documents Art. 19 does not require a distributor to hold. And 980
review tasks enter a queue whose purpose is exceptions a human can act on. For
scale, the entire registry is a few hundred documents.

**6.4 Nothing here is GC-specific.** Every supplier is under the same obligation
from 2026-08-12. GC is simply first. Whatever is decided should be decided for
the class, not for GC.


**6.5 And they answer none of the questions the registry asks.** This is the
plainest argument against ingesting them, and it comes straight out of the T1
probe rather than from reasoning. Asked for the four fields the pipeline chases,
T1 filled exactly one — and it was the dangerous one:

- `validity_to` → nothing. *"No expiry date stated in the document."* A PPWR
  declaration never expires, so the renewal chase and the `/expiry` board have
  nothing to work with.
- `cert_number` → nothing. *"This is a Declaration of Conformity, not a
  certificate."* No notified body was involved and none is named.
- `regulation` → `n.a.` There is no device regulation to record.
- `coverage_scope` → `group`. The one field it answers, and the one that opens
  the REF branch.

Nor does the document carry a Basic UDI-DI, an SRN, a risk class, or a
harmonised standard — 0 of 980 on every count (§3). Everything it *does* carry
that we would want, we already have: the manufacturer is in the playbook, the
article number is in the catalogue. What is left is a component list of labels,
cartons and caps with gram weights.

So the registry gains nothing and inherits a 980-row review queue, ~$41 of
extraction, and a live path to a false production write. The cost/benefit is
not close.

---

## 7. Ruling and recommendation

> **RULED 2026-08-26 by Denis: PPWR declarations do not go into this registry.**
> They are dangerous and they add nothing. This stands unless Dentalia changes
> the directive — at which point the work starts with a PRD change, not code.

The guard below still ships. The ruling is a decision; the guard is what stops a
future backfill from quietly overriding it when nobody remembers this document.

In order:

1. ~~Measure the T1 coverage_scope answer.~~ **Done 2026-08-26 — it answers
   `group`, 3 of 3 (§6.1). The REF branch is reachable.** What follows is no
   longer contingent.

2. **Give the guard a front door.** Today's protection is a fallback confidence
   of 0.6 and a suppression keyed on a value — both incidental. The document
   *says* which regime it is under, on all 980, in four independent ways. Make
   VALIDATE read that: a compliance document citing a regulation outside
   `MD_REGULATIONS`, whose type is not in `QMS_TYPES_WITHOUT_REGULATION`, gets a
   capping flag. That covers the PPWR case and the ~13 cosmetics / LVD / EMC /
   machinery / RoHS documents `_is_md_document`'s docstring already names, and it
   is regime-based rather than dependent on whether a REF list happened to parse.

3. **Extend `_is_md_document` to GATE's REF branch** (`app/handlers/gate.py:814`)
   regardless. It is one condition, it closes the asymmetry the docstring already
   admits to, and it does not depend on the scope ruling.

4. **Do not let the files in until 1–3 are done.** They are outside `imports/`
   deliberately. `backfill.scan` would take all 980 on the next scan.

5. ~~Then rule on scope.~~ **Ruled 2026-08-26: out of scope.** Reopen only on a
   client directive, and start with the PRD — regulation vocabulary, what
   `type=DoC` means when the subject is a package, per-article coverage.

---

## 8. What was not verified

- ~~The T1 `coverage_scope` answer~~ — **measured 2026-08-26, §6.1: `group`,
  3 of 3.** Still unmeasured downstream of it: whether T2 vision actually returns
  `10000020` as the `ref_list` value (the article line) or the packaging component
  numbers (`30000592`…). Either populates a REF list; only the first can match the
  catalogue. One T2 vision call settles it.
- **The catalogue's actual GC `mfr_ref` values.** §6.2 infers from GC's documents
  because there is no live registry on this machine (every `$PGDATA_HOST/*`
  was empty on 2026-08-25 and 2026-08-26). One query settles it directly.
- **The primary PPWR text.** Art. 19 distributor duties (§7.5 rationale, recorded
  in `[ppwr-in-scope]`) come from three agreeing secondary sources; EUR-Lex
  returned an empty body on five attempts across two tools. Confirm before acting
  on it commercially.
- **Other suppliers' PPWR formats.** Only GC's have been seen. §6.4 assumes the
  class behaves like the sample.

---

## 9. Reproducing this

```
<scratch>/gc-ppwr-2026-08-25/
  PPWR_GC_EUROPE.zip     184 MB, sha256 a83959ebddb86bc8597ef7b6f7c4e5f7…
  PPWR GC EUROPE/        980 PDFs
  ppwr_index.csv         980 rows joined to GC's "Devices by Class" sheet
```

Start from `ppwr_index.csv` rather than the code. Filter `also_an_art_code=YES`
for the 91 whose article number is also a legacy code. Device-class spread of
the 980: 868 class IIa, 50 class I, 42 non-medical, 20 absent from GC's sheet.

Related: `[gc-ppwr-archive]`, `[non-md-doc-reaches-production-via-ref]` and
`[ppwr-in-scope]` in `tasks/followups.md`; `playbooks/gc.json` rev 2 for the
portal and the archive URL.
