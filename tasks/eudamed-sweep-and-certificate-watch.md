# EUDAMED — sweep and certificate watch

**Pick-up note for a fresh session. Written 2026-08-25 after a day of live
measurement. Everything below was measured against the real API, not reasoned.
Do not re-probe to confirm; re-probe only if you suspect it has changed.**

**Status: brainstormed 2026-08-26 with Denis; spec revised, plan needs
regeneration. No code written.** Seven decisions were taken and are recorded in
the spec's preamble and §10. The re-measurement below corrected several figures
in this file — corrected in place, as this file's own §7.3 instructs.

> **This does not depend on `[playbooks-into-the-database]`.** It is implementable
> against the playbook files exactly as they are today. The two are independent
> workstreams and neither blocks the other. Nothing in the EUDAMED design reads
> or writes a playbook.

---

## 1. Why bother — the one-paragraph case

EUDAMED is **not a document source and never can be**. What it is: a registry of
what manufacturers registered. That makes it good for exactly two things —
telling us *which* declaration to ask a manufacturer for, and telling us when a
certificate we hold has been renewed or superseded. Both feed the project's
first end-state output (item_ref → evidenced documents) by making the chase
targeted instead of blind.

## 2. What is already built and committed

| | |
|---|---|
| `app/handlers/eudamed.py` | `eudamed.sync` — one Basic UDI-DI, pages to the end, upserts `eudamed_mirror`. Live. |
| `migrations/038`, `039` | `eudamed_mirror.reference` (catalogue number), `.trade_name`. |
| `web/app.py` + `document_detail.html` | "Look up in EUDAMED" button on a document page, results table. Live at `/documents/{id}`. |
| `tests/test_eudamed_handler.py` | 29 tests. |
| **Spec** | `docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md` |
| **Plan** | `docs/superpowers/plans/2026-08-25-eudamed-enhancement.md` — 11 TDD tasks |

The spec and plan cover the two proposed job types. **Read the spec first; this
file is the context around it, not a replacement for it.**

Ruling in force (Denis, 2026-08-25, PHASES decision log): stage 1 only — fetch,
store, display. `ref-eudamed` capped at `staged`. EUDAMED is an **enhancement,
never a main path**: nothing in the pipeline may come to depend on it being
reachable.

## 3. Measured facts — do not re-derive these

### The register holds no declarations of conformity

~~Certificate types across the whole register, counted 2026-08-25: 260
quality-management-system / 92 technical-documentation / 48
quality-assurance.~~ **Wrong — re-measured 2026-08-26.** `totalElements` is
**4,608**, and the register carries IVDR certificates the first count missed
entirely:

```
3272  mdr  quality-management-system        200  ivdr quality-management-system
 582  mdr  technical-documentation          157  ivdr technical-documentation
 388  mdr  quality-assurance                  1  ivdr production-quality-assurance
   6  mdr  product-verification               2  mdr  type-examination
```

3,196 distinct actors, 4,508 distinct certificate numbers. All notified-body
types. A DoC is the manufacturer's own document, issued by no
notified body, so **it is not in EUDAMED in any form — not as a file, not as a
record.** MDR Art. 14(2)(a) asks a distributor to verify the DoC was drawn up;
EUDAMED cannot help with that specific duty. This bounds everything.

Device records carry **no certificate field**. Certificates are reachable only
per manufacturer, via `actorSrn` on `/certificates/search/`.

### The targeting result (this is the payoff)

| | |
|---|---:|
| Carl Martin devices registered in EUDAMED | 3,594 |
| Our Carl Martin device articles | 2,567 |
| Found in EUDAMED by article number | **2,389 (93%)** |
| Distinct Basic UDI-DIs behind them | **70** |
| Documents we hold carrying any of those 70 | **0** |
| Unresolved, cause unknown | 178 |

The registry's largest gap — 2,569 reusable instruments with no declaration —
is a request list of **70 documents**, and EUDAMED names every one.

### The monitoring result

| Source | Certificate | Revision | Valid to |
|---|---|---|---|
| Our 96 declarations (51 production) | G15 043306 0282 | Rev. 00 | 2026-05-04 |
| Our doc 255 (EC, production) | G15 043306 0282 | Rev. 01 | 2031-05-04 |
| EUDAMED today | G15 043306 0282 | **Rev. 02**, status `supplemented` | 2031-05-04, issued 2026-06-12, NB 0123 |

Carl Martin the same shape: `HZ 1594091-1`, old revision expiring 2026-06-29, a
renewal already issued to 2031-06-29 — and **no document we hold cites it**, so
it is a certificate gap rather than drift. GC returned **zero** certificates.

**Re-measured against the whole register, 2026-08-26.** Our 58 distinct
`document.cert_number` values matched **7**, covering **107 of 391**
certificate-carrying documents. Two findings were new: Straumann
`G10 020326 0062` ours Rev. 06 vs EUDAMED **Rev. 07** `reissued`; Gebr.
Brasseler (KOMET) `HZ 1470094-1` at **Rev. 5** `supplemented` with an expiry of
**2026-02-28**, six months past. The 51 misses are mostly ours, not EUDAMED's:
extraction defects (`0482`, `CE-0197`, `NB1639`, `Quality Management System`),
format variants, and genuinely absent MDD-era numbers.

### Coverage ceiling — why this is four sweeps, not 4,265 button presses

| Manufacturer | Device articles |
|---|---:|
| CARL MARTIN | 2,567 |
| IVOCLAR | 1,069 |
| GC EUROPE N.V. | 356 |
| KOMET | 258 |
| RENFERT + 4 others | 15 |

Nine canonical manufacturers cover the whole device catalogue; four cover 99.6%.

### Article-number reach

`item_mirror`, medical devices only: 4,265 total, **1,246 carry `mfr_ref`**, and
of those **1,110 have `mfr_ref` identical to `item_ref`** (89%). So `item_ref`
is a usable EUDAMED key where `mfr_ref` is blank.

Sample of 30 random device articles queried by article number: **25 matched** on
exact reference *and* correct manufacturer, zero false positives after gating.

### Yield of article-level linking — measured and rejected

Across six mirrored Basic UDI-DI groups, EUDAMED reached **116** catalogue
items; **110 already had article-level production evidence**. Six would be new.
That is why `ref-eudamed` linking is rejected for now and the cap stays
unexercised.

## 4. API traps — each of these cost real time

**`reference=` is a SUBSTRING match, not exact.** `reference=64` returns
**62,918** records. An ungated sample returned Promedics Orthopaedics, PAUL
HARTMANN and Cerascreen records for Dentalia article numbers. Every match must
be gated on exact reference **plus** manufacturer. `srn=` does not have this
problem — it cannot return another company's devices.

**Unknown parameters are silently ignored** and return the unfiltered
3,196,505-record set rather than erroring. A renamed parameter reads as a
spectacular hit. Confirmed non-filtering: `manufacturerName`, `manufacturerSrn`
(on the certificates endpoint), `deviceName`, `nomenclatureCode`. Confirmed
filtering: `basicUdi`, `srn`, `reference`, `primaryDi`, `tradeName`,
`riskClassCode`, `actorSrn`.

**`actorName` on the certificates endpoint DOES filter, and is a substring
match** (measured 2026-08-26; not tested on 08-25). `Ivo` returns 4, `Ivoclar`
returns 1. Same gating requirement as `reference`.

**There is no actor-search endpoint.** `actors/search`,
`economicOperators/search`, `actors`, `search/actors`, `actors/search-eo` and
`economic-operators/search` all 302 to a not-found screen (2026-08-26). SRNs are
read off device or certificate records, never looked up by name.

**Max page size is 300, not 100** (2026-08-26). Asking for 1,000 returns 300.
Confirmed on both endpoints, so the whole certificate register is **16 calls**
and Ivoclar's device sweep is **~38 pages, not 114**.

**`certificateStatus` is a nine-valued enum** nobody had looked at (2026-08-26):
issued 2,770 / supplemented 1,007 / amended 422 / reissued 197 / withdrawn 87 /
cancelled 68 / restricted 31 / suspended 18 / reinstated 8. `latestVersion` is
`true` on all 4,608 — the endpoint pre-filters, so it is not a discriminator.

**`document.cert_number` embeds the revision; EUDAMED separates it.** We store
`G15 043306 0282 Rev. 00`; EUDAMED stores the number bare with `revisionNumber`
in its own field. Any join must split ours — which is a ruling, not a detail.
See the spec §5.1.

**Responses are paged, default size 20.** Spring envelope: `content`, `number`,
`size`, `totalElements`, `totalPages`, `last`. Reading one response silently
returns the first 20 of any larger group. Both `page=` (0-based) and `size=`
work; `size=100` confirmed. **This shipped as a bug and was fixed the same day
— see the two fix commits.**

**Group sizes are far larger than the 2026-08-20 research suggested.** That
research said 41 at its largest; Ivoclar's `76152082APROS004VZ` is a genuine
group of **7,586** devices in 76 pages, every record carrying that exact
`basicUdi` and one manufacturer name. A size threshold is worthless as a
filter-integrity guard — check the group each record belongs to instead.

**`deviceName` is null on every record measured** (121 of 121 across two
unrelated manufacturers). The name is in **`tradeName`** — 544 of 591 populated.
Also shipped as a bug and fixed.

**The per-device detail endpoint 400s** with both `uuid` and `ulid` taken from
the list response. The 2026-08-20 research recorded it as working; either the
path shape changed or the ids differ. Not chased.

**`deviceStatusType` is on every record and nobody has looked at what values it
takes.** An article withdrawn from the market changes what must be held for it.
Deliberately unstored — inventing a retention rule for a withdrawn device is
Dentalia's call.

## 5. Standing rulings this work must respect

**Do not widen `_RCODE_SUFFIX`.** `app/handlers/validate.py:711-742` strips a
trailing R-code (` R000`, ` R7`) from certificate numbers and **deliberately
leaves `Rev. NN` alone** — Denis, 2026-08-19: *"`Rev. 00` and `Rev. 01` may be a
supersession rather than a spelling, and resolving one to the other would assert
that silently; 96 declarations sit in that shape and all carry their own expiry,
so refusing costs nothing. Widening this regex is a ruling, not a tidy-up."*
Those are the same 96 Ivoclar declarations this work is about. The proposed
drift view **reports** the revision difference and resolves nothing.

**Seed `manufacturer` from the playbooks + `manufacturer_alias`** — Denis,
2026-08-20, `[manufacturer-contacts-empty]`. The table has 0 rows and has never
had a writer; it already carries an unused `eudamed_srn` column. Boundary rule
from the same ruling: *a playbook never contains an email address, the database
never contains a crawl URL.*

**The SRN belongs in a table, not a playbook file** —
`docs/superpowers/specs/2026-07-29-manufacturer-playbooks-design.md:153`:
mirrored source data lands in a table, authored config in the file. An SRN is
read from an API response at runtime; playbooks are hand-authored. This holds
whether or not playbooks ever move to the database.

**One canonical manufacturer may be several EUDAMED entities.**
`manufacturer_alias` already maps BC codes **001 and 005 both to `IVOCLAR`**, so
our canonical name is a Dentalia-side grouping, not a legal entity. One SRN per
canonical name would report articles as unregistered when a sibling entity holds
them — and that false alarm becomes a wrong e-mail to a supplier. The spec
proposes a `manufacturer_srn` table for this.

## 6. Open, needs Denis — mostly answered 2026-08-26

Items 1 and 3 were ruled on: **no sweep runs unattended**, the scheduler only
marks a manufacturer due and a person releases each run, restoring the binding
2026-08-20 ruling that the first spec draft had contradicted. Item 2 is
deferred by ruling. What remains open is in the spec's §10, chiefly the
certificate-number **normalisation ruling** (reach is 107 documents; two further
rules take it to 145 and each asserts that two differently-printed strings name
one certificate).

### Superseded — the original three

1. **Sweep cadence.** Monthly per manufacturer is the assumption in the plan —
   nine certificate calls plus ~150 device pages for the two large catalogues.
   It is a config key defaulting off, so it is changeable after the fact.
2. **Request drafts from the gap list.** Deferred in the plan. It reaches into
   `app/handlers/email_request.py` (S2.4), which builds its draft body from
   *documents currently expiring*; a "these declarations do not exist at all"
   draft is different copy on the same manufacturer-keyed trigger. Worth doing
   after the gap view has been looked at with real data.
3. **A live sweep is a bigger outbound footprint** than the ~40 calls made so
   far: ~114 pages for Ivoclar, ~36 for Carl Martin, holding the `ec.europa.eu`
   politeness lease for minutes. Denis authorised the button's calls; a full
   sweep should be asked for separately.

## 7. Where to start tomorrow

1. Read `docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md` — §5
   (bad paths) and §6 (rejected options) are the parts that carry the reasoning.
2. Brainstorm the shape before touching the plan. The plan's Task 1 is the enum
   migration and is deliberately the smallest possible commit.
3. Nothing needs re-measuring. If a figure here looks wrong, re-measure that one
   and correct this file rather than assuming the file is right.
