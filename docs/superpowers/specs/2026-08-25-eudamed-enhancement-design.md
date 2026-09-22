# EUDAMED as an enhancement path — design

**Status:** proposed 2026-08-25; **revised 2026-08-26** after a live
re-measurement that corrected several figures in the first draft and added two
findings it did not have. Supersedes nothing; extends the stage-1 ruling of
2026-08-25.

**Scope ruling it builds on** (Denis, 2026-08-25, PHASES decision log): EUDAMED
stage 1 is *fetch, store, display*. `ref-eudamed` is capped at `staged`. This
document proposes stage 2 as an **enhancement path, never a main path** (Denis,
2026-08-25): nothing in the pipeline may come to depend on EUDAMED being
reachable, and no EUDAMED-derived value may become production evidence.

**Decisions taken in the 2026-08-26 brainstorm** (Denis, all recorded in §10):
scheduler proposes and a human releases every sweep; certificates are acquired
as one whole-register mirror rather than per manufacturer; actor attribution is
exact-auto with a fuzzy confirm queue; `manufacturer` identity is seeded for all
384 canonical names and all 384 are probe-eligible; the sweep delta is expressed
in Basic UDI-DI groups and device-status transitions; certificate drift enriches
the existing renewal e-mail while the declaration gap ships as a view only.

---

## 1. What EUDAMED actually is, measured

Figures below were measured live against `https://ec.europa.eu/tools/eudamed/api`.
Those marked **(08-26)** replace first-draft figures that did not survive
re-measurement; the rest were measured 2026-08-25 and re-confirmed.

**It is a registry of what manufacturers registered. It is not a document
source, and it never can be.**

### 1.1 The certificate register **(08-26)**

`totalElements` on `/certificates/search/` is **4,608**, not the 400 the first
draft recorded. The first draft's type table was low by roughly 11x and omitted
IVDR entirely.

| Certificate type | count |
|---|---:|
| `certificate-mdr-type.quality-management-system` | 3,272 |
| `certificate-mdr-type.technical-documentation` | 582 |
| `certificate-mdr-type.quality-assurance` | 388 |
| `certificate-ivdr-type.quality-management-system` | 200 |
| `certificate-ivdr-type.technical-documentation` | 157 |
| `certificate-mdr-type.product-verification` | 6 |
| `certificate-mdr-type.type-examination` | 2 |
| `certificate-ivdr-type.production-quality-assurance` | 1 |

3,196 distinct actors, 4,508 distinct certificate numbers.

**The first draft's conclusion survives the correction intact.** Every type is a
notified-body type. A declaration of conformity is the manufacturer's own
document, issued by no notified body, so **no DoC exists in EUDAMED in any form
— not as a file, not as a record**. The document MDR Art. 14(2)(a) obliges a
distributor to verify is precisely the one EUDAMED does not hold. This is the
single most important fact in this design and it bounds everything below.

Device records carry no certificate field at all.

### 1.2 `certificateStatus` — a whole finding the first draft missed **(08-26)**

Every certificate carries a nine-valued status:

| status | count |
|---|---:|
| `issued` | 2,770 |
| `supplemented` | 1,007 |
| `amended` | 422 |
| `reissued` | 197 |
| **`withdrawn`** | 87 |
| **`cancelled`** | 68 |
| **`restricted`** | 31 |
| **`suspended`** | 18 |
| `reinstated` | 8 |

A suspended, withdrawn, cancelled or restricted certificate under one of our
manufacturers is a sharper compliance event than a revision bump, and it is not
a document request — it is news. It becomes its own finding (§4.3).

### 1.3 Record shape and keys **(08-26)**

- `revisionNumber` is **null on 481 of 4,608** records, and where present takes
  at least six live formats: `Rev. 02`, `Rev.0`, `Rev. 5`, `Rev. 07`, `1`, `2`.
  Stored raw; normalised only for comparison.
- `versionNumber` is never null. `latestVersion` is `true` on **all 4,608** — the
  search endpoint already filters to current records, so it is not a
  discriminator and must not be used as one.
- 98 `(actorSrn, certificateNumber)` pairs carry more than one record, up to 3.
- **The proposed primary key `(certificate_number, revision_number, actor_srn)`
  was tested against all 4,608 rows and collides zero times.** It stands.
  `(actor, number, versionNumber)` collides 50 times and must not be used.

### 1.4 What it gives us

Per article (`reference=<article number>`): `primaryDi`, `basicUdi`, `riskClass`,
`manufacturerName`, `manufacturerSrn`, `tradeName`, `deviceStatusType`,
`applicableLegislation`.

Per manufacturer (`srn=<SRN>`): that manufacturer's entire registered catalogue,
paged. Confirmed exact: `srn=DE-MF-000005066` returns exactly 3,594 devices,
matching the independently-measured Carl Martin figure.

Per certificate record: `certificateNumber`, `revisionNumber`, `certificateType`,
`certificateStatus`, `issueDate`, `startingValidityDate`, `expiryDate`,
`notifiedBodySrn`, **and `actorSrn` + `actorName`**.

That last point is load-bearing: **the SRN arrives on every certificate record**,
so SRN discovery is a by-product of the certificate pull, not a prerequisite for
it. The first draft ordered these the other way round.

### 1.5 The targeting result

| | |
|---|---:|
| Carl Martin devices registered in EUDAMED | 3,594 |
| Our Carl Martin device articles | 2,567 |
| Found in EUDAMED by article number | **2,389 (93%)** |
| Distinct Basic UDI-DIs behind them | **70** |
| Documents we hold carrying any of those 70 | **0** |
| Unresolved, cause unknown | 178 |

The registry's largest single gap — 2,569 reusable instruments with no
declaration — is a request list of 70 documents, and EUDAMED names every one.

### 1.6 The monitoring result, re-measured against the full register **(08-26)**

Our corpus holds 769 documents, 391 carrying a `cert_number`, across 58 distinct
numbers. Joined against all 4,608 register rows: **7 numbers match, covering 107
of the 391 documents.** That is a raw certificate-number join across every
document type, not the drift view's reach — **correction, 2026-08-27**: 96 of
those 107 are Ivoclar declarations carrying `G15 043306 0282 Rev. 00` (see the
table below), and `certificate_drift`/`held_certificate` have always been scoped
to `EC`/`ISO` documents only (§4.1) — a DoC citing a certificate number does not
hold it. Scoped to what the views actually cover, `held_certificate` reaches
**9** of the **43** EC/ISO documents that carry a `cert_number`; the other **34**
have no production `item_document` link and so are `unattributable`, invisible
to every finding built on it (measured 2026-08-26, Task 6/7 fix rounds).

| Our `cert_number` | docs | EUDAMED | Actor |
|---|---:|---|---|
| `G15 043306 0282 Rev. 00` | 96 | **Rev. 02**, `supplemented`, exp 2031-05-04 | Ivoclar Vivadent AG |
| `G15 043306 0282 Rev. 01` | 1 | as above | Ivoclar Vivadent AG |
| `G10 020326 0062 Rev. 06` | 1 | **Rev. 07**, `reissued`, exp 2030-06-15 | Institut Straumann AG |
| `HZ 1470094-1` | 1 | **Rev. 5**, `supplemented`, **exp 2026-02-28** | Gebr. Brasseler (KOMET) |
| `HZ 2020214-1` | 4 | `Rev.0`, `issued`, exp 2030-05-01 | Sure Dent |
| `HZ 2158053-1` | 3 | `1`, `issued`, exp 2028-07-30 | Guilin Woodpecker |
| `C528661-PA-NoMA-BRA` | 1 | `2`, `issued`, exp 2028-08-09 | ANGELUS |

Straumann and Brasseler are new since the first draft. The Brasseler certificate
carries an expiry six months in the past.

Carl Martin's `HZ 1594091-1` — two register rows, `Rev. 1` expiring 2026-06-29
and `Rev. 2` running to 2031-06-29 — matches **no** document we hold. That is a
certificate gap (§4.4), not drift.

### 1.7 Why reach is 27%, and whose fault that is **(08-26)**

The 51 unmatched numbers split three ways, and only the last is EUDAMED's doing:

1. **Extraction defects.** `0482`, `CE-0197`, `NB1639`, `Quality Management
   System`, `Medical Device Class 1` are not certificate numbers. This is a
   data-quality finding independent of this work and is reported, not fixed here.
2. **Format variants.** `HZ 1470094-1 G` (36 docs) matches Brasseler if a
   trailing single letter is stripped; `SX 1594091-1` and `SX 1470094-1` match
   if an `SX` prefix is reconciled to `HZ`. Each is a normalisation **ruling** —
   see §5.1 and §10.1. None is applied. Note that `MDR 778483` (80 docs) is
   *not* in this bucket: it is absent from the register outright, so no
   normalisation reaches it.
3. **Genuinely absent.** `0675GB448260109` (92 docs) and similar MDD-era or
   UK numbers that predate or sit outside the register.

The ceiling on drift coverage is our extraction quality, not EUDAMED's contents.

### 1.8 Coverage ceiling

Nine canonical manufacturers cover the entire device catalogue; four cover
99.6%. `item_mirror` holds 4,265 medical-device articles across 11 distinct raw
manufacturer strings.

| Manufacturer | Device articles |
|---|---:|
| CARL MARTIN | 2,567 |
| IVOCLAR | 1,069 |
| GC EUROPE N.V. | 356 |
| KOMET | 258 |
| RENFERT + 4 others | 15 |

GC holds **zero** certificates in the register (`actorName=GC Europe` → 0), so
its SRN cannot be discovered from the certificate pull. That is what keeps the
article-probe fallback in the design.

### 1.9 Article-number reach

`item_mirror`, medical devices only: 4,265 total, **1,246 carry `mfr_ref`**, and
of those **1,110 have `mfr_ref` identical to `item_ref`** (89%). So `item_ref` is
a usable EUDAMED key where `mfr_ref` is blank. A sample of 30 random device
articles: 25 matched on exact reference *and* correct manufacturer, zero false
positives after gating.

---

## 2. Goals and non-goals

**Goals**

1. Detect certificate revision drift against certificates we hold.
2. Detect adverse certificate status (suspended, withdrawn, cancelled,
   restricted) under our manufacturers. **(08-26)**
3. Detect certificates EUDAMED lists for our manufacturers that we hold no copy
   of. **(08-26)**
4. Produce, per manufacturer, the list of articles EUDAMED knows about for which
   we hold no article-level declaration, grouped by Basic UDI-DI.
5. Report what changed since the last sweep, in Basic UDI-DI groups and device
   status transitions. **(08-26)**
6. Surface all of it in the review UI where the work already lives.
7. Carry drift into the e-mail the renewal path already composes.

**Non-goals**

- Writing any `document`, `item_document` or `evidence` row. Invariant 1 is
  untouched: these are producers.
- Creating `ref-eudamed` links. The 2026-08-25 cap stands and nothing here
  creates one.
- Resolving `Rev. NN` differences into a supersession chain. See §5.1.
- Fixing the extraction defects §1.7 exposes. They are reported here and owned
  elsewhere.
- Lighting DISCOVER's `eudamed` rung. It reads `cert_refs`; EUDAMED publishes no
  document URLs; it stays dark and a test pins that.
- Autonomous document discovery from a Basic UDI-DI. See §6.3.
- A drafted e-mail for the declaration gap. Deferred by ruling — §10.2.

---

## 3. Data model

### 3.1 `manufacturer` gets its first writer

`manufacturer` was created by migration 006 as a *declared, not built* Phase 2
table. Nothing writes it and it holds **0 rows, re-verified 2026-08-26**. It
already carries `eudamed_srn`, and `email.request` reads it for contacts — which
is why [`email_request.py`](../../app/handlers/email_request.py) `_contacts()`
returns nothing today and the renewal mail has no recipients. This work is its
first writer, which makes it shared infrastructure rather than an EUDAMED detail.

Rows are seeded from all **384** distinct `manufacturer_alias.canonical_name`
values (445 alias rows), per Denis 2026-08-26.

**Already ruled, not decided here.** Denis, 2026-08-20
(`[manufacturer-contacts-empty]`): "Two jobs: seed `manufacturer` identity from
the playbooks + `manufacturer_alias`, and ask the client for the regulatory
contact per supplier." This does the first.

One new column: **`srn_probed_at timestamptz`**. Without it, "probed, no SRN
exists" and "never probed" are the same null, and the article-probe fallback
re-runs against every unregistered supplier forever. **(08-26)**

The SRN belongs in a table rather than a playbook file by the boundary the
playbook design already sets
(`2026-07-29-manufacturer-playbooks-design.md:153`): mirrored source data lands
in a table, authored config in the file. An SRN is read from an API response at
runtime; playbooks are hand-authored in git. Playbooks stay files (Denis,
2026-08-25).

### 3.2 `manufacturer_srn` — one canonical name, many EUDAMED entities

`eudamed_srn text` on `manufacturer` is one-to-one and that is wrong here.
`manufacturer_alias` already maps BC manufacturer codes **001 and 005 both to
`IVOCLAR`**: our canonical name is a Dentalia-side grouping, not a legal entity,
and a multinational registers per legal entity. Storing one SRN and sweeping it
would report articles as unregistered when they are registered under a sibling
entity — and that false alarm becomes a wrong e-mail to a supplier.

```sql
CREATE TABLE manufacturer_srn (
  canonical_name text NOT NULL REFERENCES manufacturer(canonical_name),
  srn            text NOT NULL,
  actor_name     text,          -- EUDAMED's own name for the entity
  -- 'register-exact' | 'register-fuzzy' | 'article-probe'   (08-26)
  discovered_via text NOT NULL,
  -- 'auto' | 'pending' | 'confirmed' | 'rejected'           (08-26)
  status         text NOT NULL DEFAULT 'pending',
  match_score    real,          -- rapidfuzz score for a fuzzy candidate
  probe_ref      text,          -- the article number, when discovered_via='article-probe'
  first_seen     timestamptz NOT NULL DEFAULT now(),
  decided_at     timestamptz,
  decided_by     text,
  PRIMARY KEY (canonical_name, srn)
);
```

`status` drives the confirm queue (§4.5). Only `auto` and `confirmed` rows are
swept. `manufacturer.eudamed_srn` is left alone — untouched, not repurposed, not
dropped. A column no code has ever written is not evidence of an intended shape.

### 3.3 `eudamed_certificate`

`eudamed_mirror` is device-shaped and cannot hold these.

```sql
CREATE TABLE eudamed_certificate (
  certificate_number text NOT NULL,
  revision_number    text NOT NULL DEFAULT '',
  actor_srn          text NOT NULL,
  actor_name         text,
  certificate_type   text,
  certificate_status text,          -- the nine-valued enum, §1.2   (08-26)
  issue_date         date,
  starting_validity  date,
  expiry_date        date,
  notified_body_srn  text,
  version_number     integer,                                    -- (08-26)
  first_seen         timestamptz NOT NULL DEFAULT now(),         -- (08-26)
  synced_at          timestamptz NOT NULL,
  PRIMARY KEY (certificate_number, revision_number, actor_srn)
);
```

The key is proven against the whole register (§1.3). `first_seen` is **never
touched by `ON CONFLICT`** — it is what makes "a renewal appeared" detectable,
and since a new revision is a new row, the certificate watch falls out of it for
free.

The whole register is mirrored, not only our manufacturers' rows: 4,608 rows is
nothing, matching happens locally against `pg_trgm`/rapidfuzz rather than through
a substring query, and "did a supplier register their first certificate" becomes
answerable without a fresh fetch (Denis, 2026-08-26).

### 3.4 `eudamed_mirror` gains a delta **(08-26)**

Today the upsert sets `synced_at = now()` on every conflict, so after one sweep
a device registered last week and one registered in 2021 are indistinguishable
and **no delta is derivable**. Two columns fix it:

- `first_seen timestamptz NOT NULL DEFAULT now()` — never touched by `ON CONFLICT`.
- `device_status_type text` — read for transitions, acted on nowhere (§5.7).

### 3.5 Sweep state **(08-26)**

The scheduler proposes and a human releases (§4.6), which needs somewhere to
live: per canonical manufacturer, `last_swept_at`, `due_at`, `released_at`,
`released_by`. This is also the due-list the UI renders.

### 3.6 Views, no stored findings

All findings are derivable from the registry plus the mirror. Storing them would
be a second source of truth that goes stale. See §4.2–§4.5.

---

## 4. Job types and findings

Two new tags. Job types are a closed enum (Invariant 7): PRD change and
migration first, never a runtime string.

```
eudamed.certregister {}            -- (08-26) replaces the first draft's
                                   --   eudamed.certcheck {manufacturer}
  -> GET /certificates/search/?size=300&page=N  to `last`   (16 calls)
  -> upsert eudamed_certificate (all 4,608)
  -> derive actor_name -> actor_srn candidates into manufacturer_srn

eudamed.sweep {manufacturer}       -- human-released, never self-emitting
  -> for each auto/confirmed SRN
  -> GET /devices/udiDiData?srn=<srn>&size=300&page=N  to `last`
  -> upsert eudamed_mirror, preserving first_seen
```

`size=300` is the server's cap — asking for 1,000 returns 300 — and is confirmed
on **both** endpoints. Ivoclar is therefore ~38 pages, not the 114 the first
draft assumed. **(08-26)**

`ALTER TYPE job_type ADD VALUE` cannot be used in the same transaction that uses
the new value (PG16), and the migration runner applies each file in one
transaction. Migration 013 hit this and documents it. The enum migration
therefore adds the two values and nothing else.

### 4.1 `certificate_drift`

Held `EC`/`ISO` documents joined to `eudamed_certificate` on the **baseline**
number only — `Rev. NN` split off, the ` R000` R-code already stripped on our
side by `validate.py`'s existing `_RCODE_SUFFIX`. Both revisions are exposed as
columns and neither is resolved into the other. See §5.1 — this view **reports**
a difference the registry is forbidden to assert.

**Near-misses are shown, not joined** (Denis, 2026-08-26). A held certificate
whose number matches a register row only under a looser normalisation appears in
a separate `possible_match` column, flagged with the rule that would have matched
it, for a human to confirm. Same shape as the SRN confirm queue (§4.5): the
system surfaces the candidate and refuses to assert it. **Correction,
2026-08-27:** "145" and "107" here are the raw certificate-number string-match
counts across every document type, not this view's own reach — `held_certificate`
is scoped to `EC`/`ISO` only (this section's opening sentence), which takes the
baseline join from 107 documents down to **9**. The near-miss rules (trailing
letter, `SX`→`HZ` prefix) have not been separately re-measured against that
EC/ISO-only population; §5.1's table below states the raw, unscoped figures and
says so.

### 4.2 `certificate_status_alert` **(08-26)**

Certificates under a matched manufacturer whose `certificate_status` is
`withdrawn`, `cancelled`, `suspended` or `restricted`. Not a document request —
news, and the most urgent thing this work produces.

### 4.3 `certificate_gap` **(08-26)**

Certificates EUDAMED lists for a matched manufacturer that no document we hold
cites. Carl Martin's `HZ 1594091-1` is the worked example. The cheapest ask of
the four: a named number and issuing notified body, one line in an e-mail.

The reverse case — we hold a certificate EUDAMED does not list — is expected for
MDD-era paperwork predating the register and is **not** a finding.

### 4.4 `eudamed_declaration_gap`

Our medical-device articles joined to `eudamed_mirror` on exact `reference`,
restricted to articles with no article-level production evidence, grouped by
`basic_udi_di`. Ships as a view only; the e-mail draft is deferred (§10.2).

### 4.5 The SRN confirm queue **(08-26)**

Attribution of an EUDAMED actor to one of our canonical manufacturers is a fuzzy
name match, and a wrong attribution produces a wrong drift alert, which becomes a
wrong e-mail to a supplier — §5.2's failure mode in a new place.

A normalised exact match (case, legal suffixes, punctuation) stores the SRN with
`status='auto'`. Anything below that is written `status='pending'` with its
score, and a person clears it once per manufacturer, forever. Only `auto` and
`confirmed` are swept. This mirrors the staging-review pattern already in the UI
and gives `manufacturer_srn` real provenance.

### 4.6 Scheduler: proposes, never releases **(08-26)**

Denis, 2026-08-20, decision log, marked binding: *"**No sweep ever runs
unattended.** Serial where no rate limit is published; prefer a bulk download to
an API; an operator releases every run."* The first draft's monthly self-emitting
tick contradicted that ruling; re-ruled 2026-08-26 in favour of the ruling.

The tick computes `due_at` and marks manufacturers due. It emits nothing. A
person releases a sweep from the due list, and that click enqueues
`eudamed.sweep`. The due list is itself a view worth having.

**`eudamed.certregister` is carved out** (Denis, 2026-08-26). The 2026-08-20
ruling forbids unattended sweeps, but in the same sentence says *"prefer a bulk
download to an API"* — a 16-call whole-register pull is that bulk download, not a
per-manufacturer sweep. The scheduler runs it directly. Device sweeps still wait
for a click.

**Cadence** (Denis, 2026-08-26): certificates **monthly**, device sweeps
**quarterly**. Certificates carry five-year validity and drift slowly, but drift
matters most near expiry and 16 calls is cheap insurance. Device catalogues move
more slowly than the ~38 pages it costs to re-read one. Since the device tick
only proposes, a wrong cadence costs a stale badge rather than traffic.

---

## 5. Bad paths

The failure modes, worst first. Each names its mitigation and each mitigation is
testable.

### 5.1 Silently asserting a supersession — the one that would corrupt the registry

`validate.py:711-745` strips a trailing R-code from certificate numbers and
**deliberately leaves `Rev. NN` alone**. The comment carries Denis's ruling of
2026-08-19: *"`Rev. 00` and `Rev. 01` may be a supersession rather than a
spelling, and resolving one to the other would assert that silently; 96
declarations sit in that shape and all carry their own expiry, so refusing costs
nothing. Widening this regex is a ruling, not a tidy-up."*

Those are the same 96 Ivoclar declarations this design is about.

**The first draft understated this.** It assumed `document.cert_number` held a
bare number. It does not: we store `G15 043306 0282 Rev. 00`, while EUDAMED
stores `certificateNumber: "G15 043306 0282"` with the revision in its own
field. **So the drift view cannot join at all without splitting our column** —
performing, for its own purposes, the very operation VALIDATE is forbidden to
perform. **(08-26)**

**Mitigation:** the split happens **only inside the view**, is read-only, and
produces two columns — our revision and EUDAMED's — which are displayed side by
side and never reconciled. Nothing writes `superseded_by`, `cert_doc_id`, or any
registry column. `_RCODE_SUFFIX` is not touched. Pinned by a test asserting the
view emits a drift row while every registry column is unchanged.

The looser normalisations §1.7 lists are **not applied to the join** (ruled,
Denis 2026-08-26): they surface as `possible_match` candidates for a human
(§4.1). Their measured value is far smaller than it looks:

| normalisation | new numbers | new docs | cumulative |
|---|---:|---:|---:|
| baseline (` R000` / `Rev. NN` split only) | 7 | 107 | 107 |
| + strip a trailing single letter | 1 | 36 | 143 |
| + reconcile an `SX` prefix to `HZ` | 2 | 2 | 145 |

145 of 391, and one rule carries all of it. Stripping ` R000` gains **nothing**:
`MDR 778483 R000` normalises to `MDR 778483`, which is itself absent from the
register.

**These are raw string-match counts across every document type in the 391,
declarations included — correction, 2026-08-27.** `held_certificate`,
`certificate_drift` and `certificate_drift_candidate` have always been scoped to
`EC`/`ISO` documents (§4.1), and 96 of the 107 baseline matches are Ivoclar
declarations, never in scope. The views' real baseline reach is **9** of the
**43** EC/ISO documents carrying a `cert_number` (**34** unattributable — no
production `item_document` link, measured 2026-08-26, Task 6/7). This table's
cumulative column is left as measured — it is a legitimate statement about
extraction/join quality across the whole corpus — but it is not what a person
querying the certificate views actually sees.

### 5.2 Wrong attribution — the one that would produce a wrong e-mail

Two routes now, both gated.

`reference=` is a **substring** match: `reference=64` returns **62,918** records,
and an ungated sample returned Promedics Orthopaedics, PAUL HARTMANN and
Cerascreen records for Dentalia article numbers. `actorName=` on the certificates
endpoint is **also** a substring match — `Ivo` returns 4, `Ivoclar` returns 1
**(08-26)**. `srn=` has neither problem: it cannot return another company's
devices.

**Mitigation, four layers:**
1. Article probing requires exact `reference` equality *and* manufacturer-name
   corroboration before an SRN is stored.
2. Actor attribution never uses a substring query — the register is mirrored and
   matched locally, exact-auto with everything else queued for a human (§4.5).
3. Every record returned by a sweep must carry the requested `srn`; one that does
   not raises, in the shape `_assert_filtered` already uses for `basicUdi`.
4. `discovered_via` and `probe_ref` record which route and which article produced
   an SRN, so a bad attribution is auditable after the fact.

### 5.3 False gaps

An article can lack article-level evidence legitimately: it is covered by a
manufacturer-scope document that genuinely covers it, its declaration is `staged`
awaiting review, or its link was retracted for cause. A gap list that counts
those sends Nataša chasing documents she has.

**Mitigation:** the gap view excludes `retracted` links on the JOIN (not in
`WHERE` — a `WHERE` turns the LEFT join inner and drops every article with no
link at all, a bug already made and fixed once in `web/registry.py`), counts
`staged` separately from absent, and reports manufacturer-scope cover as its own
column rather than folding it into "missing".

### 5.4 The API changes or is withdrawn

It is unofficial and undocumented. Endpoints were read out of the public UI
bundle. Unknown parameters are silently ignored and return the unfiltered
3.25M-record set rather than erroring, so a renamed parameter reads as a
spectacular hit. Confirmed filtering: `basicUdi`, `srn`, `reference`, `primaryDi`,
`tradeName`, `riskClassCode`, `actorSrn`, **`actorName` (08-26)**. Confirmed
non-filtering: `manufacturerName`, `manufacturerSrn`, `deviceName`,
`nomenclatureCode`. There is **no actor-search endpoint**: `actors/search`,
`economicOperators/search`, `actors`, `search/actors`, `actors/search-eo` and
`economic-operators/search` all 302 to a not-found screen **(08-26)**.

**Mitigation:** enhancement, never main path. Every consumer degrades to "no
EUDAMED data" rather than to a wrong answer; the handlers raise on anything
unexpected so the failure lands on the dead-jobs board; the UI states when the
mirror was last synced so stale data is visibly stale.

### 5.5 Politeness lease starvation

Ivoclar is ~38 pages at `size=300`, Carl Martin 12. The domain lease is per-host
and shared, so a sweep holds `ec.europa.eu` for minutes and the per-document
button waits behind it.

**Mitigation:** sweeps enqueue at `sweep` priority, the button stays
`interactive`. Since a human now releases each sweep (§4.6), the lease is held at
a moment somebody chose.

### 5.6 Articles EUDAMED does not know

178 of 2,567 Carl Martin articles did not resolve, and 5 of 30 in the mixed
sample. Unexplained. If the gap list quietly treats "not in EUDAMED" as "no gap",
the report overstates coverage.

**Mitigation:** the view reports `not_in_eudamed` as its own bucket, never folded
into either "covered" or "missing". CLAUDE.md's rule stands: skipped rows are
counted and reported, never silent.

### 5.7 `deviceStatusType` is read but not acted on

Stored and diffed for transitions (§3.4), acted on nowhere. A withdrawn device
does **not** leave the declaration gap list: MDR retention runs ten years past
the last device placed, so the DoC is still owed. Inventing any other retention
rule is Dentalia's call.

### 5.8 Reach is 27% and looks like coverage **(08-26)**

**Correction, 2026-08-27:** the raw certificate-number join reaches 107 of 391
certificate-carrying documents, but that count spans every document type and 96
of the 107 are Ivoclar declarations — `certificate_drift` has always been
scoped to `EC`/`ISO` documents only (§4.1). Scoped to what the views actually
cover, the reach is **9** of the **43** EC/ISO documents carrying a
`cert_number` (**34** unattributable, no production `item_document` link).
Presented without either denominator it reads as far more coverage than either
number represents.

**Mitigation:** every certificate view states matched/unmatched/absent counts,
plus a fourth `unattributable` bucket for the 34 (never folded into either), and
the unmatched bucket links to §1.7's three causes so the extraction defects are
visible as defects rather than as EUDAMED gaps.

---

## 6. Rejected options

### 6.1 Article-level `ref-eudamed` links

Measured before rejecting: across six mirrored Basic UDI-DI groups, EUDAMED
reached 116 catalogue items, **110 of which already had article-level production
evidence**. Six would have been new. The measured yield does not justify a new
link basis, a constraint amendment and a review burden. The cap ruling stands and
stays unexercised.

### 6.2 Storing the drift and gap findings in tables

Rejected in favour of views. The queue is regenerable state and the registry is
truth; a findings table is a third thing that can disagree with both.

### 6.3 Autonomous document discovery from a Basic UDI-DI

Feeding a Basic UDI-DI or trade name into DISCOVER's web rung as a search term is
plausible and is **not** in this design. DISCOVER's `eudamed` rung reads
`cert_refs`, which EUDAMED does not publish, so it stays dark regardless; and the
web rung has produced **1 document** across the project's life against 862 from
the archive.

### 6.4 Widening `_RCODE_SUFFIX` to swallow `Rev. NN`

See §5.1. It is a ruling, not a tidy-up, and it belongs to VALIDATE, not here.

### 6.5 Per-manufacturer certificate queries **(08-26)**

Rejected in favour of the whole-register mirror. 384 gated `actorName` queries
against a substring matcher, versus 16 unfiltered pages matched locally with the
project's existing `pg_trgm` → rapidfuzz path. The query route also silently
misses any manufacturer whose registered legal name does not contain our
canonical string — `GC EUROPE N.V.` being the obvious risk.

### 6.6 `latestVersion` as a currency signal **(08-26)**

It is `true` on all 4,608 records. The endpoint filters to current before we see
it. Treating it as a discriminator would be a no-op that reads as a check.

---

## 7. Invariant compliance

| Invariant | How this holds |
|---|---|
| 1 — only GATE writes the registry | Both handlers write `eudamed_mirror`, `eudamed_certificate`, `manufacturer`, `manufacturer_srn`. No `document`, `item_document` or `evidence`. Asserted by test, not assumed. |
| 3 — REF gate | No link is created, so the gate is not reached. `ref-eudamed` stays capped and unwritten. |
| 4 — never downgrade | No supersession is asserted. §5.1. |
| 7 — closed job-type enum | Two values added by migration, PRD updated in the same slice. |
| 11 — adapter opacity | All fetching through the `Fetcher` adapter, never raw httpx. |
| 12 — LLM use | None anywhere in this work. Name matching is rapidfuzz, not a model. |

---

## 8. Slices

Each produces working, testable software and can be rejected without blocking
its neighbour.

- **A1** — enum values; `manufacturer` seeded (384) + `srn_probed_at`;
  `manufacturer_srn`; `eudamed_certificate`; the `eudamed.certregister` handler;
  local exact/fuzzy actor matching.
- **A2** — the SRN confirm queue UI.
- **A3** — `certificate_drift`, `certificate_status_alert`, `certificate_gap`;
  the `/expiry` section and the manufacturer-page panel.
- **A4** — drift lines inside the renewal e-mail `email_request.compose()`
  already builds.
- **B1** — `first_seen` + `device_status_type` on `eudamed_mirror`; sweep state;
  the `eudamed.sweep` handler; the due list and release button; the article-probe
  SRN fallback (it needs a swept manufacturer to probe from).
- **B2** — `eudamed_declaration_gap` and the sweep-delta panel.

A1 is the only slice the others need. A3 depends on A1; A4 on A3; B2 on B1.

---

## 9. What this does not do

It never gets you a document. Every finding terminates in a fetch or an e-mail a
person sends. EUDAMED tells you *which* declaration to ask for and *when* a
certificate you hold went stale — it does not hold the paperwork, and the first
end-state output still depends on obtaining it from the manufacturer.

---

## 10. Rulings taken, and what is left

All four items the 2026-08-26 draft left open were ruled the same day. Recorded
here because a decision that lives only in a chat is not a decision.

1. **Normalisation.** *Join on baseline, report near-misses.* The drift view
   joins only on the safe split; looser candidates surface as `possible_match`
   for a human to confirm (§4.1). Raw string-match join reach (every document
   type) stays 107 of 391 documents; near-miss visibility would reach 145.
   **Correction, 2026-08-27:** those are not the view's own reach — scoped to the
   `EC`/`ISO` documents `certificate_drift`/`held_certificate` actually cover,
   the join reaches 9 of 43 EC/ISO documents carrying a `cert_number` (34
   unattributable). The 2026-08-19 `_RCODE_SUFFIX` ruling is untouched and the
   system asserts nothing new.
2. **`eudamed.certregister` release.** *Carved out.* The scheduler runs it —
   16 calls against a public register is the "bulk download" the 2026-08-20
   ruling prefers, not the per-manufacturer sweep it forbids. Device sweeps still
   require a human release (§4.6).
3. **Cadence.** *Certificates monthly, device sweeps quarterly* (§4.6).
4. **The declaration-gap e-mail (B3).** *Deferred.* The gap list ships as a view
   and gets looked at with real data before anyone writes copy for it. A
   decision, not an omission.

### Still open, owned elsewhere

- **Regulatory contacts.** `manufacturer.contact_emails` stays empty after this
  work: seeding identity is the first half of the 2026-08-20
  `[manufacturer-contacts-empty]` ruling, and the second half is asking the
  client per supplier. The renewal e-mail has no recipients until that happens.
- **The extraction defects §1.7 exposes.** `0482`, `CE-0197`, `NB1639`,
  `Quality Management System` and `Medical Device Class 1` are sitting in
  `document.cert_number` and are not certificate numbers. Reported by this work,
  fixed by whoever owns EXTRACT.
- **`deviceStatusType` semantics.** Stored and diffed here, acted on nowhere.
  A retention rule for a withdrawn device is Dentalia's call (§5.7).
