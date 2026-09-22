# Per-item currency: which declaration is current, for which article

**Status:** design, awaiting review. Nothing here is built.
**Closes:** AC8's third clause, F23. **Amends:** PRD C6, invariant 5.
**Ruled:** 2026-09-14 (per item, not per document) and 2026-09-15 (the three
clarifications in §2).

---

## 1. The defect, measured

A newer declaration retires an older one whenever the two share a catalogue
**group**. Sharing a group is not the same as covering the same devices: a group
is a bundle of similar articles, so two declarations can sit in one group and
describe entirely different products.

Measured 2026-09-14 over every pair of production documents sharing a group,
type and regulation — the pairs today's code treats as supersession candidates:

| Pairs | Article sets |
|---|---|
| 11 | identical |
| 1 | superset |
| 1 | partial overlap |
| **28** | **no shared article at all** |

The group is the wrong subject **68%** of the time. All four supersessions in
the live registry are of that kind, and **94 items** show no current declaration
while their paperwork sits in the archive:

| Retired | By | Articles retired | Articles the newer one names | Shared |
|---|---|---|---|---|
| 162 IVOCLAR | 815 | 78 | 1 | 1 |
| 375 KOMET | 415 | 11 | 18 | 0 |
| 379 KOMET | 415 | 6 | 18 | 0 |
| 415 KOMET | 377 | 18 | 15 | 0 |

`_current_production_doc` ([`app/handlers/validate.py`](../../../app/handlers/validate.py))
takes any production document sharing ONE group with the candidate as "the
current one"; `_apply_supersession`
([`app/handlers/gate.py`](../../../app/handlers/gate.py)) re-checks only type and
regulation and then supersedes the whole document.

**PRD C6 says "overlapping coverage subject" and invariant 5 says "identical".**
The code implements C6. They have disagreed since they were written, and this
spec settles it.

---

## 2. What was ruled

**2026-09-14 — per item, not per document.** A newer declaration replaces an
older one only for the articles it names, only when it is genuinely newer, and
only within the same type and regulation. Denis: *"we can't request a
manufacturer to always cover all the same devices — what if some go out of
production? The documents can't always cover all the same items."* A supplier
retiring an article is ordinary product lifecycle, so a rule demanding the newer
document name every article of the older would treat the normal case as an
anomaly and send it to a human.

**2026-09-15 — three clarifications, and they change the mechanism.**

1. **Half-current is correct and designed, not a defect.** *"The document is
   current while it covers some of the items. If a new document covers others,
   then this document is current for those."*
2. **The asking entity is the ITEM or the MANUFACTURER, never the document.**
   *"Document is an internal entity which needs even partial accuracy, but from
   item or mfr perspective we need clean relations and only the last document is
   current per each type."*
3. **An outdated document is kept and simply not returned**, unless a caller
   asks for history: *"they are retrieved by api, but only if they need all."*

---

## 3. The mechanism: latest-wins, computed

Because the question is always asked from the item's side, currency is a
**property of the answer, not a stored flag**. The view picks, per item and per
document type, the newest production document. Nothing writes a supersession
state at all.

### 3.1 The rule, stated once

> For an item, the current document of a given type is the production document
> linked to that item, of that type and regulation, with the newest
> `validity_from`. Every other production document linked to that item and type
> is history: kept, addressable, and not returned by default.

Ties (equal `validity_from`, which the registry already contains) resolve by:
article-level `match_basis` before manufacturer scope, then the higher
`doc_id` — the later arrival. Stated explicitly because the current code has no
tiebreak and two same-day declarations are a live scenario.

### 3.2 Why this rather than a stored link status

The rejected alternative was: `item_document` gains a link-level `superseded`
status and `superseded_by`, written at gate time.

| | Persisted link status | Latest-wins, computed |
|---|---|---|
| Migration | new enum value + column on `item_document` | one view |
| The four bad supersessions | need an audited repair | stop being consulted |
| A stored flag that can be wrong | yes, and it already is | does not exist |
| Retired article (nothing newer ever names it) | old document keeps a production link forever and never turns `superseded` | no state to be stuck in |
| "Which document is current?" | ambiguous per document | always answerable per item |
| Lost | — | the queryable `A was retired by B` pointer |

The loss is real and recoverable: the GATE audit log already records every
disposition, so "which document replaced which" remains reconstructible as
history. It stops being the thing the system *reads*.

**Cost check.** 9.008 links, 4.960 items, 891 documents. A per-item lateral pick
over that is free; this is not a performance trade.

### 3.3 What changes in code

1. **`item_document_production`** — the view every reader already uses — gains
   the per-item, per-type pick. Its column list does not change, so every
   consumer keeps working.
2. **A second view, `item_document_history`**, returns the same rows without the
   pick: every production link, current or not. This is what the API's "all"
   mode reads.
3. **`validate._current_production_doc`** stops resolving "the current document"
   by group. VALIDATE's never-downgrade check (invariant 4) compares against the
   documents covering *the candidate's own articles*.
4. **`gate._apply_supersession`** is deleted, along with `document.superseded_by`
   as a decision input. The column stays (append-only, 10-year retention) and
   becomes a historical record of what the old rule did.
5. **`document.status = 'superseded'`** stops being written. Existing rows keep
   the value; see §5.

### 3.4 What does NOT change

- Invariant 1: only GATE writes the registry. This spec removes a write; it adds
  none.
- Invariant 3: the REF gate is untouched. Currency is about *which* of several
  valid documents answers for an article, never about whether a link may be
  written at all.
- Invariant 4 (never downgrade) survives in its own terms: an older candidate
  still cannot become the current answer for an article that has a newer one.
- The capped bases (`name-family`, `fetch-context`, `ref-catalogue`) stay capped
  at `staged` and therefore never participate — a staged link is not production
  and cannot be anyone's current document.

---

## 4. The API's "all" mode

`GET /api/items/{item_ref}/documents` keeps returning **one current document per
type**. It gains `?all=1`, which returns every production document linked to the
item with a `current` boolean per row, newest first.

This is an addition, not a change: the default response shape is untouched, so
the webshop and Business Central see exactly what they see today.

---

## 5. The eight documents, and why the repair is smaller than it was

Under a stored flag, the four wrong supersessions had to be repaired or they
would keep lying. Under latest-wins nothing reads `document.status =
'superseded'` for currency, so they stop mattering **as decisions**. They still
show on `/documents` as superseded, which is the wrong word for five real
supplier documents.

Measured 2026-09-15:

| Doc | Status | Production links | What it is |
|---|---|---|---|
| 162 IVOCLAR | superseded by 815 | **78** | real; its 78 links are the bulk of the 94 items |
| 415 KOMET | superseded by 377 | **18** | real |
| 375, 379 KOMET | superseded by 415 | 0 | real, but nothing visible rides on them |
| 377 KOMET | production | 15 | real, current |
| 815, 832, 839 IVOCLAR | production | 1 each | **seeded test files** from `tools/seed_fake_mailbox.py`, archive path `/archive/unknown/unknown/ivoclar-doc-2026.pdf` |

**The three seeded files must be rejected whatever mechanism ships.** Latest-wins
does not save us here: 815 is dated 2026-08-01, so it is the *newest* document
for its item and would win the pick. A dev fixture would be the current
declaration for a real catalogue item. This is the one repair that is not
optional.

**The five real documents need one status correction**, not a mechanism: set 162,
375, 377, 379 and 415 back to `production`. Under latest-wins that is cosmetic
for coverage (the view no longer consults the flag) and honest for the documents
page.

If a production environment is ever rebuilt from BC and the corpus, the seeded
three will not exist there and the five will be recreated correctly **provided
this rule ships first** — a rebuild without the rule change reproduces the same
four wrong supersessions.

---

## 6. Blast radius: what moves the day this lands

`item_document_production` changing meaning moves every coverage number on the
board at once. Before merging, measure and record: AC1's article-level count,
the declarations headline, the Coverage gaps count, `eudamed_article_status`'s
`has_production`, and `bc_fields`' `pteHasDoC`. **94 items regain a declaration**
— that is the intended direction, and it must be stated in the same breath as
the new AC1 figure so nobody reads the jump as a measurement error.

Readers to check by name, each of which asks "is this document current?" today:
the compliance card (`app/compliance.py`), `/documents`, the item page,
`/staging`'s unpublished-links panel, `item_document_production`, `bc_fields`,
and the weekly report.

---

## 7. Testing

- **The 41 pairs as a table-driven test.** Each pair, its article sets, and which
  document should answer for which article. This is the fixture the defect was
  measured with and it becomes the regression suite.
- **Ordinary lifecycle:** doc A covers articles 1-10, doc B (newer) names 1-5.
  Items 1-5 answer B, items 6-10 answer A, and neither document changes status.
- **A retired article:** nothing newer ever names article 7; A stays its answer
  indefinitely and is never flagged.
- **A tie:** two documents, same `validity_from`, one article-level and one
  manufacturer-scope; the article-level one wins, deterministically.
- **The chain test** (`tests/test_chain.py`) gains a second document through the
  real queue, so the pick is exercised where the stages meet and not only in SQL.
- **`?all=1`** returns history including the non-current document; the default
  response is byte-identical to today's.

---

## 8. Open questions for review

1. **Does `document.status = 'superseded'` stay in the vocabulary?** It becomes
   write-only history. Keeping it costs a word on a screen that no longer means
   what it says; removing it is a migration plus every renderer.
2. **Should the five real documents be repaired at all**, given the flag stops
   being read? My recommendation is yes — the documents page is read by people,
   and "superseded" is false about all five.
3. **`?all=1` or `?history=1`** as the parameter name, and whether the webshop
   should be told about it now or when it asks.
