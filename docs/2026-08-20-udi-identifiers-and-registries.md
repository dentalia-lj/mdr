# UDI identifiers and device registries — measured, 2026-08-20

Second research pass, widening [2026-08-20-eudamed-reachability-research.md](2026-08-20-eudamed-reachability-research.md)
from "does EUDAMED work" to "what is UDI actually, which databases hold it,
what would we fetch, and do we depend on it".

**Recommendation: secondary source of truth, never primary.** The measurements
below say that is not a compromise — it is the only defensible reading, because
no single registry covers our catalogue and the two that exist disagree often
enough to be worth reconciling and rarely enough to be trustworthy.

---

## 1. Three identifiers, not one

Conflating these is the main way UDI work goes wrong.

| | what it identifies | our column | populated |
|---|---|---|---|
| **Basic UDI-DI** | the *device group* — same intended purpose, risk class, design | `document.basic_udi_di` | **466 / 740 documents** |
| **UDI-DI** (primary DI) | one *marketable model* | `item_document.udi`, `item_mirror.udi` | **0 / 5,979 and 0 / 15,958** |
| UDI-PI | the *production instance* — lot, serial, expiry | — | not modelled |

They are different strings in different namespaces. Straumann's group
`0763003170014QD` contains the model `07630031731326`; neither is derivable
from the other. **A document printing a UDI-DI is not printing a Basic UDI-DI.**

The consequence for us: **UDI is currently a document-side attribute only.**
We hold zero UDIs on the item side, so today there is no UDI join to make
in-house — which is exactly what a registry supplies.

## 2. Formats — verified against our own data

| agency | Basic UDI-DI | UDI-DI | in our documents |
|---|---|---|---|
| **GS1** | GMN: company prefix + model ref + **2 check chars**, ≤25 chars | GTIN-14 (all digits) | 126 |
| **HIBCC** | `++` + 4-char LIC (first alpha) + 1–17 model + **2 check chars** | LIC + product + check | 333 |
| **EUDAMED-assigned** | `B-` + the UDI-DI verbatim | (as issued) | 0 |
| junk / unparsed | — | — | 7 |

Worked examples from our rows: `0763003170002Q6` = GCP `7630031` + model + check pair `Q6`. `++E22100160100000008Z` = `++` + LIC `E221` + model + check `8Z`. VOCO's LIC is `E221`, GC's is `J022`.

The `B-` shape was found in EUDAMED (148 of VOCO's 865 devices), not in our
corpus: `B-E22124861` is literally `B-` prefixed onto UDI-DI `E22124861`,
issuing agency still HIBCC, registered 2026-07-06. It reads as a EUDAMED-side
fallback for devices whose manufacturer never obtained a real Basic UDI-DI.
**Observed, not read off a spec** — the EUDAMED help page that names
"Basic UDI-DI/EUDAMED DI" never defines the difference. Treat as a third shape
our parsers must tolerate; do not build a rule on the inferred meaning.

### Offline check-character validation — BUILT, 2026-08-20

`app/extract/udi.py`, wired into `t0_templates.extract_basic_udi`. Both
agencies close the identifier with a MOD 1021,32 check pair, so every Basic
UDI-DI we hold can be verified locally: no network, no key, no registry.

**How the algorithm was obtained.** Transcribed from HIBCC's *HIBC Basic
UDI-DI* specification — Table 1 (the 82-character data set and its values),
Table 2 (the depleted 32-character encode set) and the worked example. The
spec is a PDF; WebFetch could not read it, so it was read page by page as
images. Two details only the primary source gives: the `++` flag is part of
the weighted payload rather than a prefix to strip, and the weighting runs
**right to left** from the character adjacent to the check pair, by ascending
primes from 2.

GS1's equivalent PDF is served **403** to us. Rather than implement from a
secondary description, the GS1 half was pinned empirically: the same
implementation was run against Basic UDI-DIs GS1 subscribers had themselves
registered in EUDAMED. All 185 pass. GS1's GMN therefore uses the identical
scheme — measured, not assumed, and `tests/test_udi_validation.py` says so in
the docstring rather than pretending to a spec we could not read.

**How it was verified**, in ascending order of what each rules out:

1. The spec's own worked example — `++A999MODELIDENTIFIER11` → check value
   774 → pair `S8` — reproduces exactly. This pins the character table, the
   prime weighting and the encode in one assertion.
2. **306 of 306** codes published by EUDAMED are accepted (121 HIBCC + 185
   GS1). Zero false positives on manufacturer-registered ground truth, which
   is the property that matters: a validator that rejects good codes would
   send clean documents to a paid tier.
3. **3 of our own 352** extracted codes are rejected. Two are the known OCR
   corruptions. **The third — `++E221023102000000093` — was found only by
   running the validator**, has no visible tell, and would never have been
   caught by eye. It sat in the "unresolvable in EUDAMED" bucket, where a
   misread code and an unregistered manufacturer look identical.
4. Nine mutations of the module and five of the wiring, each applied in
   isolation, all killed by the tests.

**What it changed.** `extract_basic_udi` previously returned confidence `1.0`
on whatever the label regex caught, unconditionally. It now scores: verified
keeps 1.0, invalid and unverifiable both land under `needs_escalation`'s 0.95,
so the document escalates to a tier that can re-read it instead of feeding a
corrupt identifier to `validate.py`'s `basic-udi-di` link basis — which
Invariant 3 treats as production-capable. An invalid pair also raises the
`basic_udi_check_failed` anomaly, keyed to the offending value so `seen_count`
counts how many documents repeat the same misread string. The value itself is
never rewritten or dropped: a reviewer still needs to see what was printed.

Measured blast radius on the current corpus: 3 invalid and 7 unverifiable rows
out of 352, so roughly ten extra T1 calls (~$0.09) against ten identifiers
previously trusted at 1.0.

One thing this surfaced immediately: `7612147BDTPFEW`, used as a test fixture
and in the job-type handbook's payload example, is synthetic and fails its own
check pair. The fixture now uses a real registry code; **the handbook example
is still wrong** and is left for whoever owns that file next.

## 3. The registries

| registry | scope | access | catalogue number? | bulk? |
|---|---|---|---|---|
| **EUDAMED** | EU, 3.20M devices | undocumented public backend | `reference` — 100% populated | **no** |
| **FDA GUDID / AccessGUDID** | US, 5.18M DIs | documented API + **bulk files** | `catalogNumber` | **yes — 516 MB full + daily/weekly/monthly deltas** |
| GS1 GEPIR | GTIN prefix → company | redirects; `api.gs1.org` needs a key | no | not probed further |
| SwissDaMed, MHRA | CH / UK | not probed | — | — |

GUDID is the better-engineered of the two by a wide margin: `GET /api/v3/devices/lookup.json?di=` needs no key, and the bulk release removes the need to hammer anything. Its API has **no company search** — lookup is by DI only — so company-level work must go through the bulk file, which is what it is for.

## 4. Do the two registries agree? — the decisive measurement

240 UDI-DIs sampled from the four EUDAMED catalogues already swept (60 each),
looked up individually in GUDID, comparing EUDAMED `reference` against GUDID
`catalogNumber`:

| manufacturer | sampled | found in GUDID | catalogue number agrees | differs |
|---|---|---|---|---|
| Straumann | 60 | 56 | 56 | 0 |
| Neodent (JJGC) | 60 | 51 | 49 | 2 |
| VOCO | 60 | 49 | 45 | 4 |
| Ivoclar | 60 | 47 | 47 | 0 |
| **total** | **240** | **203 (85%)** | **197** | **6** |

Of the six differences, four are GUDID `catalogNumber: null` (VOCO left the
field empty) and one is punctuation (`106.252` vs `106252`). **Exactly one is a
real conflict:** DI `07899878061137`, EUDAMED `108.237` against GUDID
`108.337`. Agreement where both sides carry a value is **197/199 = 99.0%**.

Two legally mandated registries on two continents, filled independently by the
same manufacturers, agree on the catalogue number 99% of the time and
disagree in a way that points at a typo. That is the definition of a useful
cross-check, and it is the whole argument for the secondary-source design.

## 5. They are complementary, not redundant

The manufacturers EUDAMED cannot serve are not the same ones GUDID cannot serve.
**Gebr. Brasseler (Komet) resolves 0 of 16 Basic UDI-DIs in EUDAMED** — and has
**6,103 devices registered in GUDID** under that exact legal name, plus 11,123
under its US arm Peter Brasseler Holdings. One registry's blind spot is the
other's coverage.

(GC, our other EUDAMED blind spot at 2%, is *unconfirmed* in GUDID — the web
search UI is fuzzy full-text and returned unusable results. Settle it from the
bulk file, not from the site.)

## 6. What we would fetch

- **EUDAMED** — per-SRN sweeps of `GET /devices/udiDiData?srn=…`, monthly. No
  bulk export exists. Prerequisite: populate `manufacturer.eudamed_srn` (table
  has 0 rows); there is no actor search, so an SRN is read off any one resolved
  device.
- **GUDID** — `gudid_full_release_*.zip` once, then the daily/weekly deltas.
  Index locally on `companyName` + `catalogNumber` + `deviceId`. No key.
- **Neither for discovery.** Established in the EUDAMED pass: no document URLs
  exist in either. This is an identity and cross-validation source, full stop.

### Never sweep automatically — Denis's ruling, 2026-08-20

Binding on both registries and on anything built against them:

- **No sweep runs unattended.** A sweep is an operator action, not a cron tick.
  A scheduler may *propose* one (a manufacturer whose data is stale) but the
  request goes to the manual queue for a human to release; it never fires
  itself. This is the opposite of how `eudamed.sync` is described in PRD §3 and
  handbook §9, where it sits on its own cron.
- **Where an API does not publish a rate limit, assume one request at a time.**
  Neither EUDAMED nor AccessGUDID documents a limit, and an undocumented limit
  is not permission. The research runs used up to six concurrent workers to get
  an answer inside a session; production must not inherit that number. Serial
  by default, and any concurrency is a configured value with an operator behind
  it.
- **Prefer the path that needs no requests at all.** GUDID's bulk release is one
  download against thousands of lookups, so for GUDID the sweep question mostly
  disappears. Where a bulk path exists, using the API instead is a defect.
- **The offline validator is the model.** `app/extract/udi.py` answers a real
  question about every Basic UDI-DI we hold and makes zero requests. Work that
  can be moved off the network should be.

### Licence constraints — real, and they bite

AccessGUDID's terms prohibit "systematic access (electronic harvesting) or
extraction of content from **the website**, including the use of 'bots' or
'spiders'". The bulk files are the sanctioned path and are explicitly offered
for "third-party data aggregators" — so **download the release files; never
sweep the site**. Attribution is mandatory and has fixed wording (NLM
disclaimer). Separately: **GMDN content requires a commercial licence from
The GMDN Agency** — "no such activity is permitted without a licence" — so we
must not ingest GMDN terms from GUDID. EMDN from EUDAMED carries no such
restriction and is the nomenclature to use if we ever want one.

## 7. Rely on it, or secondary source?

**Secondary. Three independent reasons, each sufficient on its own:**

1. **Coverage is not ours to control.** EUDAMED reaches 47.7% of our Basic
   UDI-DIs and GUDID 85% of sampled DIs, but both fail *per manufacturer* and
   in different places. A pipeline that depends on a registry inherits a
   coverage hole it cannot close by working harder — only by waiting for a
   third party to register.
2. **The evidence model forbids it.** Invariant 2 wants evidence *from the
   document*. A registry lookup has no `(archive_url, page, verbatim, tier)`
   tuple and never will. Making a registry primary would mean weakening the
   invariant that makes the registry trustworthy in the first place.
3. **Its value is highest exactly where it is not authoritative.** The 99%
   agreement is worth having *because* we compute the answer independently and
   compare. A source used as primary can never contradict itself.

**What "secondary" means concretely:**

- **Confirm** — a document-derived link that a registry independently supports
  is stronger. Record the corroboration; do not upgrade the link's basis.
- **Contradict** — a mismatch like `108.237` / `108.337` is a `data_anomaly`
  and a review-queue item, never a silent overwrite of either side.
- **Fill holes, capped** — where we have no document-side answer at all, a
  registry-derived link is still useful and still capped. `ref-eudamed` should
  join `name-family` / `fetch-context` / `ref-catalogue` in
  `item_document_trusted_basis_ck`, so it can never reach `production` by
  accident. This is the opposite of the open question left at the end of the
  EUDAMED pass, and the cross-registry numbers here are why: a source that is
  1% wrong is a fine corroborator and a poor authority.
- **Validate offline first** — check characters cost nothing and catch our own
  OCR errors before any registry is consulted.

## 8. What is not answered

- GC's presence in GUDID (needs the bulk file, not the site).
- GEPIR / `api.gs1.org` — key required, not pursued; both registries already
  hand us `manufacturerName` / `companyName` directly, so prefix→company
  resolution is a nice-to-have at best.
- SwissDaMed and MHRA — not probed. Low priority while EU+US already
  complement each other.
- The `B-` prefix's normative definition — inferred from data, not from spec.

---

Sources: [GS1 GMN executive summary](https://www.gs1.org/sites/default/files/docs/idkeys/gs1_gmn_executive_summary.pdf) ·
[GMN check character pair calculation](https://www.gs1.org/sites/default/files/checkcharacterpaircalculation.pdf) ·
[HIBC Basic UDI-DI](https://www.hibcc.org/wp-content/uploads/HIBCC-Basic-UDI-DI.pdf) ·
[MDCG 2018-1 v3, Basic UDI-DI guidance](https://www.hibcc.org/wp-content/uploads/MDCG-2018-1-v3-Guidance-on-basic-UDI-DI-and-changes-to-UDI-DI-1.pdf) ·
[AccessGUDID device lookup API](https://accessgudid.nlm.nih.gov/resources/developers/device_lookup_api) ·
[AccessGUDID downloads](https://accessgudid.nlm.nih.gov/download) ·
[AccessGUDID terms of use](https://accessgudid.nlm.nih.gov/terms)
