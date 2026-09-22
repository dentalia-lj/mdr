# EUDAMED reachability — measured, 2026-08-20

Closes the research question blocking `eudamed.sync` (PHASES.md S2.3, and the
"hard number for the regulatory-tailwind claim" audit item).

**Question asked:** does EUDAMED's public data resolve a Basic UDI-DI to its
constituent devices *and* their catalogue reference numbers?

**Answer: yes, exactly and completely — but it reaches only about half our
Basic UDI-DIs, and the half it reaches is decided per manufacturer, not per
document.** Two of the design's load-bearing assumptions about EUDAMED are
wrong and are corrected below.

Method: read-only unauthenticated GETs against the EUDAMED public website's
own backend. 354 distinct Basic UDI-DIs queried individually; four
manufacturer catalogues swept in full (16,502 device records); 53 certificate
numbers looked up; 27 device detail records sampled. All raw output kept in
the session scratchpad, not committed.

---

## 1. What the public endpoints actually give

Base `https://ec.europa.eu/tools/eudamed/api`. Endpoint list extracted from
the public UI bundle (`main.b4d604bfe0712ad8.js`), then probed.

| Endpoint | Use to us |
|---|---|
| `GET /devices/udiDiData` | The workhorse. Paged device list, 3,196,505 records. |
| `GET /devices/udiDiData/{uuid}` | Per-device detail (EMDN code, clinical sizes, storage, `additionalInformationUrl`). |
| `GET /certificates/search/` | 4,485 certificates: number, type, issue/expiry, NB, holder SRN. |
| `GET /actors/{uuid}/publicInformation` | Actor detail. No usable actor *search* by name. |

Working query parameters on `/devices/udiDiData` (found by probing; unknown
parameters are silently ignored and return the unfiltered 3.19M count, so
every one below was confirmed by an actual change in `totalElements`):

- **`basicUdi=<value>`** — exact match, returns every device in that group.
- **`srn=<manufacturer SRN>`** — **returns the manufacturer's entire
  registered catalogue.** This is the important one, and it is not in the
  unofficial docs.
- `primaryDi=`, `reference=`, `tradeName=`, `riskClassCode=` — all filter.
- `manufacturerName=`, `manufacturerSrn=`, `deviceName=`, `nomenclatureCode=`
  — **do not** filter. They look plausible and are ignored.

Every device record carries `reference` — EUDAMED's
"Reference/Catalogue number" field, an MDR Annex VI Part B data element. In
the 16,502 records swept, `reference` was populated on **100%**.

Worked example, `basicUdi=471070188CNQQ`: 41 devices, all sharing that exact
Basic UDI-DI, each with its own reference (`7031`, `7024`, … `601`). That is
precisely the group → member → catalogue-number resolution the question asked
about. (Re-measured 2026-08-25: **47** devices. These groups grow.)

**The list is paged and the default page is 20.** The envelope is a Spring page
— `content`, `number`, `size`, `totalElements`, `totalPages`, `last` — and both
`page=N` (0-based) and `size=` work, confirmed live 2026-08-25. Reading a single
response therefore returns the first 20 of any larger group with no error and no
signal, which is how `eudamed.sync` shipped storing 20 of those 47 on its first
live run. Any consumer of this endpoint must walk to `last`.

## 2. How much of *our* data it reaches

354 distinct well-formed Basic UDI-DIs from `document.basic_udi_di`, one
query each:

| | count | share |
|---|---|---|
| resolved to at least one device | **169** | **47.7%** |
| returned zero | 181 | 51.1% |
| errored after 3 retries | 4 | 1.1% |

The misses are not random. Split by issuing agency: GS1-style codes resolve
**63/68 (93%)**; HIBCC `++` codes resolve **106/280 (38%)**. Split by
manufacturer, using the documents we have actually linked to items:

| manufacturer | resolved | zero | rate |
|---|---|---|---|
| Institut Straumann AG | 7 | 0 | 100% |
| Ivoclar Vivadent (codes 001/005/275) | 49 | 5 | 91% |
| VOCO | 12 | 1 | 92% |
| GC | 1 | 50 | **2%** |
| Komet (Gebr. Brasseler) | 0 | 16 | **0%** |

**This is a manufacturer-registration gap, not a matching or data-quality
problem.** 120 of the 181 misses share the single labeler prefix `++J022`
(GC). A manufacturer has either registered its portfolio or it has not; there
is no middle. Only 2 of the 181 misses look like extraction errors on our side
(`++E221049001OOOOOOOFX` — letter `O` where the code needs `0`), so EUDAMED
does double as a cheap validator for extracted Basic UDI-DIs, but that is a
rounding error next to the registration gap.

## 3. What it would be worth — the number

Four manufacturer catalogues swept in full via `srn=`, then intersected with
`item_mirror.mfr_ref`:

| manufacturer | EUDAMED refs | our items | with `mfr_ref` | ref matched |
|---|---|---|---|---|
| Ivoclar Vivadent | 11,364 | 1,294 | 804 | 528 |
| Institut Straumann | 2,555 | 918 | 660 | 420 |
| Neodent (JJGC) | 1,715 | 796 | 536 | 125 |
| VOCO | 864 | 31 | 23 | 14 |
| **total** | **16,498** | **3,039** | **2,023** | **1,087** |

Exact string match and aggressively normalized match (case-folded,
punctuation-stripped, leading zeros dropped) both give **1,087** — identical.
The reference formats already agree; no normalization layer is needed.

Chaining that through to documents we actually hold — item → `mfr_ref` →
EUDAMED → Basic UDI-DI → our `document.basic_udi_di`:

| manufacturer | items EUDAMED resolves | we hold a matching doc | **items linked that are unlinked today** |
|---|---|---|---|
| Ivoclar | 528 | 516 | 0 |
| Straumann | 420 | 200 | **59** |
| Neodent | 125 | 42 | **42** |
| VOCO | 14 | 9 | 0 |
| **total** | **1,087** | **767** | **101** |

**101 catalogue items would gain a document link today**, on a Basic UDI-DI
basis — which Invariant 3 already names as production-capable. Ivoclar
contributes zero because it is already 781/860 linked; the entire gain is
Straumann and Neodent, the two manufacturers whose corpora we could not parse
REF lists out of.

The ceiling matters more than today's figure. Those 1,087 items point at only
85 distinct Basic UDI-DIs, and we hold a document for 27 of them. **The
binding constraint is our document coverage, not EUDAMED.** Every additional
document that carries a Basic UDI-DI links its whole EUDAMED group at once —
one Straumann document covers a group of up to 208 devices. The mirror is
leverage on the document corpus, not a substitute for it.

## 4. Two design assumptions that are wrong

**(a) EUDAMED does not supply document URLs.** PRD §3 and the handbook §3
pseudo-code have DISCOVER call `eudamed_mirror.cert_lookup(...)` and get back
`urls` to fetch. It cannot. The only URL field is
`additionalInformationUrl`, and it is a bare portal root: all 15 sampled
Straumann devices returned the identical `https://ifu.straumann.com/`. Across
a random cross-manufacturer sample of 12, only 2 had any URL at all, both
bare domains (`ifu.osstem.com`, `www.medster.com.tr`). Useful as a
**playbook-authoring seed** (it confirms a manufacturer's IFU portal domain);
useless as a fetch target. The `eudamed` rung in
`discovery.default_source_priority` cannot do what it was specified to do.

**(b) There is no bulk export.** The handbook's
`download_bulk_json(EUDAMED_EXPORT_URL)` has no counterpart in reality. The
EUDAMED technical-documentation page publishes data dictionaries, XSD
schemas, business rules and a Swagger file for the M2M/DTX services — all
scoped to authorized Competent Authorities, Economic Operators and Notified
Bodies uploading their own data. **No public REST API is documented at all.**
The `/tools/eudamed/api/*` endpoints used here are the public website's own
undocumented, unversioned backend. That is the single largest operational risk
in building on this: it can change shape without notice or deprecation.

The right shape is therefore **per-SRN sweeps, not a full mirror**. Sweeping
all 3.19M devices at 100/page would be ~32,000 requests; sweeping the four
manufacturers above took 16,502 records in roughly 20 minutes at six
concurrent workers. Thirty manufacturers is a couple of hours on a monthly
cron, and costs nothing — no LLM, no API fee, no key.

## 5. The certificate module — real, but too early

`GET /certificates/search/` is public and filters by `certificateNumber`,
`actorSrn` and `notifiedBodySrn`. It carries exactly the fields we store on
`document`: number, type, `issueDate`, `expiryDate`, status, revision.

Of our 53 distinct certificate numbers, **4 matched** (7.5%). The module only
became mandatory on 28 May 2026 and holds 4,485 certificates EU-wide, so
coverage is thin and will thicken.

The four that matched validated cleanly, and one filled a gap:

| cert | our `validity_to` | EUDAMED expiry | |
|---|---|---|---|
| HZ 2020214-1 | 2030-05-01 | 2030-05-01 | agrees |
| HZ 2158053-1 | 2028-07-30 | 2028-07-30 | agrees |
| C528661-PA-NoMA-BRA | *null* | 2028-08-09 | **fills a hole** |
| HZ 1470094-1 (doc 420) | 2031-02-28 | 2031-02-28 (Rev. 7) | agrees |

EUDAMED also keeps the revision chain: `HZ 1470094-1` exists twice, Rev. 5
(issued 2025-07-17, expires 2026-02-28) and Rev. 7 (issued 2026-04-24,
expires 2031-02-28). Our doc 420 matches Rev. 7. Note both rows are flagged
`latestVersion: true` with different `versionNumber`s — reading only the
first row of a certificate search will give you the wrong expiry date. Any
consumer must take the latest by `issueDate`, not by position.

## 6. Regulatory direction

Commission Decision (EU) 2025/2371 made four modules mandatory from
**28 May 2026** — Actor registration, **UDI/Device registration**, Notified
Bodies and Certificates, and Market Surveillance. Registration is now a legal
obligation, not a courtesy, which means today's 47.7% hit rate is a floor that
should climb on its own. That is the regulatory-tailwind claim, now with a
number under it. Post-Market Surveillance/Vigilance and Clinical
Investigations remain in development with no mandatory date.

## 7. What this changes in our schema and contracts

Three concrete gaps, each small:

1. **`eudamed_mirror` cannot store the useful field.** Its columns are
   `(udi_di, basic_udi_di, device_name, manufacturer_srn, cert_refs,
   synced_at)` — there is no `reference` column, and `reference` is the entire
   point. Migration needed.
2. **`manufacturer.eudamed_srn` already exists** (migration 006) and is
   exactly the anchor a per-SRN sweep needs. The table has **0 rows**, so
   populating it is a prerequisite. There is no actor-search endpoint, so an
   SRN is obtained by resolving one known Basic UDI-DI per manufacturer and
   reading `manufacturerSrn` off the device — which is how all four SRNs here
   were found.
3. **A new `match_basis` needs a ruling before it is written.** An
   EUDAMED-derived link's evidence chain is half document (our extracted
   `basic_udi_di`, with page/verbatim/tier) and half third-party attestation
   (EUDAMED's reference → Basic UDI-DI binding), and the second half has no
   Invariant 2 evidence tuple. The `item_document_trusted_basis_ck` constraint
   caps only `name-family`, `fetch-context`, `ref-catalogue`, so **adding
   `ref-eudamed` without touching that constraint silently makes it
   production-capable.** That must be a deliberate decision, not a default.

   The argument for allowing production: EUDAMED is a legally mandated
   registry in which the manufacturer itself declares the binding — arguably
   stronger than a REF list rasterized out of a PDF. The argument against:
   it is not evidence *from the document*, which is what Invariant 2 requires.
   Denis's call; it is a PRD change plus a migration either way.

   **Answered by the follow-up pass, 2026-08-20** —
   [2026-08-20-udi-identifiers-and-registries.md](2026-08-20-udi-identifiers-and-registries.md) §7.
   Cross-checking 240 devices against FDA GUDID put a number on how much a
   registry can be trusted: 99.0% agreement on the catalogue number where both
   carry a value, with one genuine conflict. That is a good corroborator and a
   poor authority, so `ref-eudamed` should be **added to**
   `item_document_trusted_basis_ck` (capped at `staged`), not left out of it.

## 8. Verdict

Build it, scoped down from what the PRD describes. It is not a discovery
source and not a bulk mirror; it is a **per-manufacturer catalogue-number
index that turns one document into many item links**, at zero marginal cost,
against a dataset the law is actively filling. Its value scales with the
document corpus rather than substituting for it, and it is blocked on one
ruling (§7.3) rather than on more research.

The honest caveat to carry forward: it runs on an undocumented endpoint, it
reaches 48% of our Basic UDI-DIs today, and two of our five measured
manufacturers (GC, Komet) are effectively absent from it.

---

Sources: [EUDAMED overview](https://health.ec.europa.eu/medical-devices-eudamed/overview_en) ·
[UDI-DI identification information](https://webgate.ec.europa.eu/eudamed-help/en/search-by-module/devices/udi-devices/manage/register-a-basic-udi-di-together-with-a-udi-di-of-a-regulation-device/udi-di-identification-information.html) ·
[EUDAMED technical documentation](https://webgate.ec.europa.eu/eudamed-help/en/documentation/technical-documentation.html) ·
[EUDAMED API reference (unofficial)](https://openregulatory.github.io/eudamed-api/) ·
[EUDAMED public site](https://ec.europa.eu/tools/eudamed/eudamed)
