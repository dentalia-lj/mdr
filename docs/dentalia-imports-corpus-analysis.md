# Dentalia Imports Corpus — Analysis & Extraction Playbook

2026-07-06 · Empirical characterization of `imports/dentalia-sftp/` — the manually collected seed corpus. Ground-truth companion to the PRD v3 §5 (EXTRACT) normative contract and the job type handbook §5 (`extract.doc` pseudo-code).

v2, 2026-07-15 · Verified claim-by-claim against the files: deterministic page-1 text sweep of all 1,307 PDFs (per-brand scanned counts in §1 reproduced exactly), content sampling of ~65 PDFs across all 12 brands, all support spreadsheets parsed. Corrections are marked **[corrected 2026-07-15]**; §10 open questions answered inline; verification log in §11. New-findings followups: `tasks/followups.md` [corpus-verify].

---

## 1. Corpus Inventory

`imports/dentalia-sftp/` — 12 brand directories, 1,307 PDFs + 9 supporting files (xlsx, docx, csv).

| Brand | PDFs | Compliance | Business docs | Scanned | Other | Non-PDF |
|-------|------|-----------|--------------|---------|-------|---------|
| DENSTPLY | 405 | 169 | 0 | 204 | 32 | 1 docx |
| VOCO | 292 | 264 | 0 | 15 | 13 | 1 xlsx, 1 csv |
| KOMET | 186 | 122 | 0 | 13 | 51 | 1 xlsx, 1 docx |
| IVOCLAR | 174 | 146 | 0 | 27 | 1 | 2 xlsx |
| GC | 149 | 128 | 0 | 6 | 15 | 1 xlsx |
| DENTAURUM | 37 | 1 | 0 | 33 | 3 | — |
| **NEODENT** | **34** | **5** | **28** | **0** | **1** | — |
| STRAUMANN | 13 | 9 | 0 | 0 | 4 | — |
| PLANMECA | 8 | 7 | 0 | 1 | 0 | — |
| 3SHAPE | 7 | 6 | 0 | 1 | 0 | — |
| LUMIWHITE | 1 | 1 | 0 | 0 | 0 | 1 xlsx |
| BREDENT | 1 | 0 | 0 | 0 | 1 | — |
| **TOTAL** | **1,307** | **858** | **28** | **300** | **121** | **9** |

*[corrected 2026-07-15] NEODENT compliance count 1→5, Other 5→1 (see §2.3); totals adjusted. All per-brand PDF and Scanned counts verified exact by the full-corpus sweep.*

**Key numbers:**
- **858 (66%)** text-based compliance documents — extractable via T0+T1
- **300 (23%)** scanned/image PDFs — require T2 vision
- **28 (2%)** business documents (NEODENT) — NOT compliance documents; invoices/offers
- **125 (10%)** "Other" — mostly MSDS safety sheets, IFU instructions, supplier info forms, technical data sheets, plus some compliance docs missed by keyword classification due to OCR typos in the extracted text

### Subdirectory conventions (inconsistent)

Most brands use a recognizable `DOC/` + `CE/` + sometimes `MSDS/` pattern, but naming and granularity vary:

| Convention | Brands |
|---|---|
| `DOC/` + `CE/` + `MSDS/` | DENSTPLY, DENTAURUM, GC, KOMET, PLANMECA |
| `DOC/` + `EC/` | 3SHAPE |
| `MDR - Declaration of Conformity/` + `MDD - Declaration of Conformity/` + `Certificates/` | VOCO |
| `IVAG Clinical/` + `IVAG Technical/` + `IVAG Cosmetics/` | IVOCLAR |
| `CE in DOC/` (everything flat) | STRAUMANN |
| `neodent CE/` + `neodent DOC/` (lowercase) | NEODENT |
| Flat (no subdirs) | LUMIWHITE, BREDENT |
| Numbered subdirs by internal tech-doc ID | KOMET (`Artikli, kjer je originalni proizvajalec drug kot Komet/533157/`) |

**T0 can exploit these:** a file under `MDR - Declaration of Conformity/` is almost certainly `type=DoC, regulation=MDR`. A file under `Certificates/` is likely `type=EC Certificate` or `type=ISO`. This is probability, not certainty — the text is authoritative.

---

## 2. Document Types Present

### 2.1 Compliance documents (858 PDFs, plus some in "Other")

| Type | Typical filename markers | Extractable fields | REF list? |
|------|------------------------|-------------------|-----------|
| **DoC** (Declaration of Conformity) | `DoC`, `Declaration`, `Konformitätserklärung`, `izjava o skladnosti` | type, regulation, manufacturer, validity_from, ref_list, basic_udi_di, cert_number? | **Yes** — product family REF lists, 4–200+ items |
| **Declaration of Compatibility (Art. 22)** [added 2026-07-15] | header `DECLARATION OF COMPATIBILITY FOR SYSTEMS/PROCEDURE PACKS`; DENSTPLY END kit files, VOCO `Declaration Art 22_*` | type, regulation (MDR Art. 22), manufacturer, validity_from, kit components | **Yes** — kit/component list. Distinct type from DoC; 17+ in DENSTPLY alone. Not yet in PRD/handbook/prompts — see [corpus-verify] followup |
| **EC Certificate** (Notified Body) | `CE`, `Certifikat`, `Certificate`, `Zertifikat` | type, regulation, manufacturer, validity_from, validity_to, cert_number | No — device categories or "see attached schedule" |
| **ISO 13485 / QMS Certificate** | `ISO`, `13485`, `Quality Management` | type, manufacturer, validity_from, validity_to, cert_number | **No** — manufacturer-scope, no item-level identifiers |
| **IFU** (Instructions for Use) | `navodila za uporabo`, `Instructions for Use`, `Gebrauchsanweisung`, `IFU` | type, manufacturer, referenced_docs | No |

### 2.2 Non-compliance documents (identify and skip)

| Type | Count | Detection | Action |
|------|-------|-----------|--------|
| **Business docs** (invoices, offers, delivery notes) | 28 | Keywords: `dobavnica`, `račun`, `ponudba` | Flag, skip extraction, report in job result |
| **MSDS** (Material Safety Data Sheets) | ~60–80 | Keywords: `Safety data sheet`, `1907/2006/EC`, `MSDS` | Skip — out of pipeline scope |
| **Supplier info forms** | ~10 | VOCO `General Supplier Information/` | Skip |
| **Technical data sheets** | ~15 | Product composition, alloy specs | Skip |
| **Supporting spreadsheets** | 9 | `.xlsx`, `.docx`, `.csv` | Skip — reference data only |

### 2.3 The NEODENT contamination

The `neodent CE/` directory contains **28 business documents** (offers `PONUDBA`, invoices `RAČUN - DOBAVNICA`, delivery notes `Dobavnica`). These are Dentalia's own operational documents, not manufacturer certificates. The directory name is misleading.

**[corrected 2026-07-15]** NEODENT holds **5 genuine compliance/IFU files (4 distinct documents), not 1**: `neodent CE/` also carries `neodent certifikat.pdf` and `neodent vsadki, izjava o skladnosti.pdf` (plus 1 Straumann catalog = the "Other" file), and `neodent DOC/` holds 3 genuine files — IFU `navodila za uporabo neodent.pdf`, DoC `neodent izjava o skaldnosti za T-baze.pdf`, and a second copy of the `vsadki` DoC under the same filename as the CE one (hash-dedupe case). 28 + 5 + 1 = 34.

**Guardrail:** T0 must classify document type before extraction. A PDF whose first-page text contains `dobavnica`, `račun`, or `ponudba` is a business document — flag it, report it, skip extraction. The `neodent CE/` directory name is not evidence.

---

## 3. T0 Pattern Catalog

### 3.1 Filename signals (supplementary — text is authoritative)

These patterns appear in filenames and can seed T0 extraction fields at confidence < 1.0 (text confirmation required for confidence 1.0):

**Document type from filename:**
- `DoC`, `Declaration of Conformity`, `Konformitätserklärung`, `izjava o skladnosti` → `type=DoC`
- `CE cert`, `Certificate`, `Certifikat`, `Zertifikat` → `type=EC Certificate`
- `ISO`, `13485` → `type=ISO`
- `navodila za uporabo`, `IFU`, `Instructions for Use` → `type=IFU`
- `MSDS`, `Safety Data Sheet`, `varnostni list` → NOT a compliance doc

**Regulation from filename:**
- `MDR`, `(EU) 2017/745` → `regulation=MDR`
- `MDD`, `RL 93/42`, `93/42/EEC` → `regulation=MDD`

**Dates from filename:**
- `exp 07072027` → `validity_to=2027-07-07`
- `Stand_25.05.2021` → `validity_from=2021-05-25` (German "Stand" = status/as-of date)
- `12022026` (GC convention: DDMMYYYY) → some date, type TBD by text
- `2026-1-signed` (VOCO convention: YYYY-V) → version/issue marker
- ISO date fragments: `2021-05-25`, `2025-10-06`

### 3.2 Text keyword patterns (T0 regex — confidence 1.0 when exact match)

**Document type — unambiguous first-page text:**
```
English:  "EC-Declaration of Conformity", "EU Declaration of Conformity",
          "Declaration of Conformity", "EU Quality Management System Certificate",
          "EC Certificate", "ISO 13485"
German:   "Konformitätserklärung", "EU-Konformitätserklärung",
          "EG-Konformitätserklärung"
Slovenian:"Izjava o skladnosti", "Izjava EU o skladnosti",
          "EU izjava o skladnosti"
French:   "Déclaration UE de Conformité"
Italian:  "Dichiarazione di Conformita' UE"
```

**Regulation — unambiguous text:**
```
MDR:  "(EU) 2017/745", "Regulation (EU) 2017/745", "MDR 737603"
MDD:  "93/42/EEC", "RL 93/42", "Council Directive 93/42/EEC",
      "Anhang II der Richtlinie 93/42/EWG"
```

**Manufacturer — explicit field labels (capture value after label):**
```
"Name of manufacturer:", "Manufacturer:", "Legal Manufacturer:",
"Hersteller:", "We, <Company> as the manufacturer",
"Wir, <Company> als der Hersteller"
```

### 3.3 Table formats for REF list extraction (pdfplumber)

Each manufacturer's DoC has a distinct table format. T0 can use per-manufacturer templates:

**VOCO — simple 2-column:**
```
REF #    Description
1217     QuickMix Syringe 10 g universal, ...
1218     QuickMix Syringe 10 g universal
```

**IVOCLAR — 4-column with article number:**
```
Article No.  Description                              Classification  Rule
596797       IPS e.max CAD for inLab MO 0 C14/5 pcs   IIa             8
```

**KOMET — 4-column with internal ID + REF + packing variant:**
```
ID          Packing group  REF / Product name
007506K0    K0             9553.204.060
007506K1    K1             9553.204.060
```

**STRAUMANN — 4-column with REF + Description + Class + Rule:**
```
REF        Description                                    Class  Rule
033.602S   Standard Implant, Ø 4.8mm WN, SLActive® 10mm   IIb    8
```

**GC — "Article List / According to the Attachment":** **[corrected 2026-07-15]**
The "attachment" is EMBEDDED in the same PDF, not a separate file: article-list tables sit on page 2 of the 2-page (older, English-only) DoCs and on pages 4+ of the 5–7-page multilingual ones ("Produktverzeichnis", carrying BOTH the legacy Art code and the Material ID). The DoC page-1 text still says "Artikelliste / Article List / Gemäß Anhang / According to the Attachment" — T0 must parse later pages (multi-page table stitching, see [extract-t0] followup), NOT flag `external-attachment`. All ~128 GC compliance DoCs follow this layout.

**DENSTPLY — mixed formats:**
Some DoCs have inline REF lists; others reference external technical documentation numbers (`DEC-00084482`). Pattern varies by product category (IMP, END, RES, EI, LAB prefixes).

### 3.4 T0 yield estimate

Based on the 30-PDF stratified sample:

| Field | T0 yield | Method |
|-------|---------|--------|
| `type` | ~75% | Keyword match on first page + filename fallback |
| `regulation` | ~60% | Keyword match on first page + filename fallback |
| `manufacturer` | ~60% | Label-value regex on first page |
| `validity_from` | ~30% | Date near "Issue Date"/"Stand" labels |
| `validity_to` | ~35% | "Expiry Date"/"Gültigkeitsdatum"/"Valid until" |
| `ref_list` | ~40% | pdfplumber table extraction (text PDFs only) |
| `basic_udi_di` | ~25% | "Basic UDI-DI:" label + UDI-DI regex pattern |
| `cert_number` | ~30% | "Certificate No." / "Zertifikatsnr." label |

These are per-field yields for T0 alone. T1 raises most fields to 85–95% for text-based PDFs.

---

## 4. Per-Field Extractability Assessment

### `type` — HIGH

Almost every compliance document self-identifies in the first 200 characters of page 1. QMS certs say "Quality Management System Certificate". DoCs say "Declaration of Conformity". EC certs say "EC Certificate" or carry a notified body header.

**Edge cases:**
- Declaration of Compatibility (MDR Article 22, systems/procedure packs) — not a DoC, distinct document type; **17+ in DENSTPLY** (END kit files, header `DECLARATION OF COMPATIBILITY FOR SYSTEMS/PROCEDURE PACKS`) plus VOCO `Declaration Art 22_*` **[verified 2026-07-15]**
- Some KOMET "Liste" docs are just REF tables without declaration headers — they're attachments to DoCs

### `regulation` — HIGH for text PDFs

Explicit legal references: "(EU) 2017/745" for MDR, "93/42/EEC" or "RL 93/42" for MDD. Usually on page 1.

**Edge case:** Some documents reference BOTH regulations (transition-period docs). The active regulation is what the declaration is issued under — the most recent reference. If ambiguous, flag for human.

### `manufacturer` — HIGH for text PDFs, MEDIUM for scans

Explicit, labeled field in every compliance document. Includes full legal entity name and address. SRN (Single Registration Number) is a secondary confirmatory signal.

**Edge cases:**
- QMS certs: the manufacturer is the certificate SUBJECT, not the issuer (BSI is the issuer, 3Shape is the manufacturer)
- EU Authorized Representative (e.g., Straumann → Etkon GmbH) — separate from legal manufacturer
- Multi-entity: "GC EUROPE N.V." is the manufacturer, but the DoC also references "GC Corporation" as parent

### `validity_from` / `validity_to` — MEDIUM

Dates appear in consistent label patterns: "First Issue Date", "Current Issue Date", "Starting Validity Date", "Expiry Date", "Declaration valid until", "Gültigkeitsdatum". Formats vary: `2022-07-08`, `January 21, 2031`, `07.07.2027`, `25.05.2021`.

**Edge cases:**
- QMS certs have multiple dates (first issue, current issue, start validity, expiry). The relevant ones for pipeline: `validity_from = current issue date`, `validity_to = expiry date`
- DoCs often have only an issue date (signature date), no expiry — that's correct, not a missing field
- MDD docs may have no dates at all (pre-MDR era, less structured)

### `ref_list` — HIGH when present, ABSENT from QMS certs

The field that varies most by document type:
- **Product DoC:** Always has a REF list (4–200+ items). Format is manufacturer-specific but tabular and regular.
- **QMS/ISO cert:** Never has a REF list. Coverage is manufacturer-scope.
- **EC Certificate:** May have a "Device Schedule" attachment with product categories (not individual REFs).

**Critical guardrail:** Do not hallucinate REFs from a QMS cert. If the document type is QMS/ISO, `ref_list` must be empty/null and `coverage_scope` must be `manufacturer`.

### `basic_udi_di` — MEDIUM

Format: `++E2265331554B` or `76152082ACERA001EQ` (varies by issuing entity). Usually near the product name on page 1 of a DoC. Not always present — MDD-era docs won't have one.

### `referenced_docs` — LOW

Documents that reference other documents (e.g., a DoC referencing its underlying EC certificate by number). Useful for supersession chaining but hard to extract reliably. Low T0/T1 priority.

### `cert_number` — MEDIUM

Present on EC and ISO certificates: "Certificate No.: 0675GB448260109", "MDR 737603 R000", "Zertifikatsnr.: HZ 1470094-1". Not present on DoCs (they may reference a cert number but don't have one themselves).

---

## 5. Tier Accuracy Estimates

Based on the 30-PDF stratified random sample (scanned detection via `pdftotext` page-1 length test):

### 5.1 Per-tier coverage

| Tier | Input | Corpus share | Mechanism | Cost profile |
|------|-------|-------------|-----------|-------------|
| **T0** | All 1,307 PDFs | 100% | Regex, filename parsing, pdfplumber tables | Free |
| **T1** | Fields with confidence < threshold from T0; text PDFs only | ~65–70% of PDFs (text-based); per-field, not per-doc | Haiku 4.5 via Batch API; text extraction prompt | ~$0.001/doc (Haiku batch) |
| **T2** | Fields still < threshold after T1; ALL scanned PDFs (300) | ~30% of PDFs (scans) + T1 failures (~5%) | Sonnet 5 via Batch API; vision extraction prompt | ~$0.02/doc (Sonnet batch, ~4 pages avg) |
| **T3** | Fields still < threshold after T2 | ~3–7% of PDFs | Human review UI (manual queue) | Human time |

### 5.2 Per-field accuracy by tier (estimated)

| Field | T0 alone | After T1 | After T2 | Notes |
|-------|---------|----------|----------|-------|
| `type` | 75% | 98% | 99%+ | T1 handles OCR typos ("Konjormitätserklärung"), T2 handles scans |
| `regulation` | 60% | 95% | 98% | Explicit legal refs, well-handled by LLM |
| `manufacturer` | 60% | 97% | 99% | Labeled field, prominent on page 1 |
| `validity_from` | 30% | 90% | 95% | Date format diversity; some docs lack dates |
| `validity_to` | 35% | 90% | 95% | Not always present (DoCs often have none) |
| `ref_list` | 40% | 88% | 92% | Table format diversity; QMS certs correctly empty |
| `basic_udi_di` | 25% | 85% | 90% | Not always present (MDD docs); format varies |
| `cert_number` | 30% | 90% | 93% | Only on certificates, not DoCs |

### 5.3 Combined estimate

- **~93–97%** of documents achieve all applicable fields above confidence threshold after T0→T1→T2
- **~3–7%** need T3 human review (mostly dense scanned tables, badly rotated scans, non-standard formats)
- **~23%** of the corpus (300 scanned PDFs) goes directly to T2 — T0/T1 can't extract text from images

---

## 6. Guardrails

### G1: Document type classification BEFORE extraction
T0 must classify every PDF as one of: `compliance-doc`, `business-doc`, `msds`, `supplier-info`, `technical-sheet`, `unknown`. Only `compliance-doc` proceeds to field extraction. Business docs, MSDS, and supplier info are reported in job metrics and skipped.

**Detection:** First-200-chars keyword match. Slovenian: `dobavnica`, `račun`, `ponudba`. English: `invoice`, `delivery note`. MSDS: `Safety data sheet`, `1907/2006/EC`.

### G2: QMS cert → no REF list, manufacturer scope
If `type ∈ {ISO, QMS Certificate, EC Certificate}` and the certificate body text says "quality system" or "Annex IX Chapter I and III", the document is manufacturer-scope. `ref_list` must be null/empty. `coverage_scope = manufacturer`. The validator's C4 pre-rule routes these to the `mfr-binding` flow.

### G3: "See attachment" / external article list detection
Text patterns: "see item list", "see attached Device Schedule", "According to the Attachment", "Gemäß Anhang", "Artikelliste", "Article List". When detected: flag `ref_list_source: "external-attachment"`, set REF list confidence low. The attachment may be a separate PDF, a missing file, or a scanned page later in the same PDF. **[corrected 2026-07-15]** GC verified: its "attachment" is always embedded later pages of the same PDF (§3.3) — parse later pages first; reserve the external-attachment flag for documents whose later pages genuinely lack the list.

### G4: Per-field confidence, per-field escalation
Never escalate the whole document. If T0 extracts `type=DoC` (conf 1.0) and `regulation=MDR` (conf 1.0) but `ref_list` (conf 0.3), only the REF list goes to T1. The handler pseudo-code already encodes this: `missing = low_confidence(fields, TARGET)`.

### G5: REF codes verbatim — no normalization in EXTRACT
REF codes are manufacturer-specific strings. Do not strip dots, dashes, spaces, or leading zeros. The REF gate in VALIDATE does fuzzy matching (`pg_trgm + rapidfuzz`). Normalization in EXTRACT would lose information needed for match provenance.

### G6: Multi-language keyword coverage
T0 regex must cover English, German, Slovenian. French/Italian/Portuguese appear in GC multilingual DoCs. The keyword lists in §3.2 cover all languages observed in the corpus.

### G7: OCR typos in text PDFs
Some text-based PDFs have garbled extracted text due to font encoding issues: "Konjormitätserklärung" (should be Konformitätserklärung), "Dedoration oj conformity", "ec Certifieate". T0 regex with fuzzy matching or substring matching avoids false negatives. T1 LLM handles these naturally.

### G8: Content-hash dedupe before extraction
`extract:{content_hash}` dedupe key means identical PDFs are extracted once. But different scans of the same certificate (different hashes) will be extracted separately. The `cert_number` field enables cross-PDF dedup in VALIDATE — if two extractions produce the same `(type, manufacturer, cert_number)` tuple, they're the same certificate.

---

## 7. Manufacturer-Level Observations

### Well-structured (high T0 yield expected)
- **VOCO:** Consistent template, English, explicit REF list per product DoC, clear regulation subdirectories. **[added 2026-07-15]** `Certificates/` subfolder is photocopier scans only (T2 territory); one file has uppercase `.PDF`; Basic UDI-DI on every DoC page 1
- **GC:** Consistent multilingual template, clear structure, regulation and type explicit on page 1
- **3SHAPE:** Clean, few files, well-named, explicit manufacturer and dates

### Moderately structured
- **IVOCLAR:** Internal OTCS document system with consistent template (TEFO-EN-000145); article numbers are 6-digit codes, sometimes with a suffix (e.g. `769570AN`) **[corrected 2026-07-15]**; product-level DoCs well-organized by clinical/technical/cosmetic
- **KOMET:** Consistent bilingual German/English template; dense REF tables with internal ID + REF + packing variant; some subdirs for third-party products
- **STRAUMANN:** Polarion-generated DoCs with internal revision metadata; Slovenian directory names but English/German document content; clear REF tables

### Problematic (low T0 yield, T2-heavy)
- **DENSTPLY:** 204 of 405 PDFs are scanned images (50%). Text-based ones have inconsistent templates across product divisions (IMP, END, RES, EI, LAB). A loose `DOWNLOADS DENTSPLY SIRONA.docx` lists URLs instead of containing actual certificates.
- **DENTAURUM:** 33 of 37 PDFs are scanned images (89%). Text-based ones have German content. The single compliance PDF with extractable text has OCR artifacts ("ec Certifieate").

### Contaminated
- **NEODENT:** 28 of 34 PDFs are business documents (invoices, offers, delivery notes). The `neodent CE/` directory name is misleading. **[corrected 2026-07-15]** 5 genuine compliance/IFU files, 4 distinct documents (see §2.3); `neodent DOC/` holds 3 files (2 DoCs + 1 IFU, template marker FRM-000922), all real.

### Non-MD
- **LUMIWHITE:** Explicitly marked "ni MD - KOZMETIKA" (not a medical device). 1 cosmetic product DoC + 1 CPNP spreadsheet. Out of pipeline scope.
- **BREDENT:** 1 MSDS only. No compliance documents.

---

## 8. Non-PDF Files

9 files, all supporting data — not extraction targets:

| File | Content |
|------|---------|
| `DENSTPLY/DOWNLOADS DENTSPLY SIRONA.docx` | **[corrected 2026-07-15]** ONE filtered download-center portal URL (dentsplysirona.com, `asset-type=declaration-of-conformity`) — a DISCOVER/playbook seed, not a per-product link list |
| `KOMET/1-Where to find DoC (Medical Devices Only).xlsx` | **[verified 2026-07-15]** 3,801 rows: Article no. → Reference no. → DoC folder id; sheet 2 "SETs": 5,875 rows SET no. → contained article nos. (kit explosion) |
| `IVOCLAR/IVOCLAR MD or NOT.xlsx` | **[verified 2026-07-15]** 8,353 rows: Material (9-digit) + Koda (6-digit) → MDR class + DoC product-family name (family, not filename) |
| `IVOCLAR/Copy of Dentalia_UDI.xlsx` | **[verified 2026-07-15]** 5,064 rows: Ivoclar material no. → UDI-DI (derivable pattern `DVIV` + material + `1`); keys on Ivoclar numbers, which for plain-style items equal BC `item_ref` (findings-phase0 §G4) |
| `GC/Devices by Class_22042026.xlsx` | **[verified 2026-07-15]** 3,098 rows: Material ID + legacy Art code (dual numbering; both appear in DoC tables) → class, NB, legal manufacturer |
| `VOCO/Regulatory Product Data/20260305_voco_en.xlsx` | **[verified 2026-07-15]** 921 rows × 191 cols: exact REF ("Exact manufacturer item number") → UDI-DI, Basic UDI-DI, MDR class, notified body |
| `VOCO/Regulatory Product Data/20260305_voco_en.csv` | Same data; semicolon-delimited, UTF-8 with BOM |
| `LUMIWHITE-ni MD - KOZMETIKA/CPNP (1).xlsx` | Cosmetic Products Notification Portal entries (13 rows) — out of scope |
| `KOMET/Artikli.../SPECIFIKACIJA.docx` | **[corrected 2026-07-15]** Mapping table: Komet article no. → REF → third-party-manufacturer DoC folder id |

**[corrected 2026-07-15]** These are more than operator reference data: four of the five biggest corpus brands ship a deterministic item→doc/UDI/class seed. Promoted to a concrete S1.2 RESOLVE input — see `tasks/followups.md` [corpus-verify] 2026-07-15.

---

## 9. Extraction Priority Order

For the initial implementation, prioritize by brand structure quality (best T0 yield first):

1. **VOCO** (292 PDFs, 90% text-based, consistent templates) — best T0/T1 test case
2. **GC** (149 PDFs, 96% text-based, multilingual but consistent)
3. **IVOCLAR** (174 PDFs, 84% text-based, internal template system)
4. **KOMET** (186 PDFs, 93% text-based, dense REF tables)
5. **3SHAPE + PLANMECA + STRAUMANN** (28 PDFs combined, mostly text-based)
6. **DENSTPLY** (405 PDFs, 50% scanned) — split: text PDFs through T0/T1, scans through T2
7. **DENTAURUM** (37 PDFs, 89% scanned) — mostly T2
8. **NEODENT** — after decontamination (separate business docs from compliance docs)
9. **LUMIWHITE, BREDENT** — skip

---

## 10. Open Questions

1. **DENSTPLY `DOWNLOADS DENTSPLY SIRONA.docx`** — this file contains URLs for downloading certificates. Are these URLs already fetched, or does this represent a discovery backlog? If the URLs are live, they should enter DISCOVER → FETCH → EXTRACT, not be treated as already-imported documents.
   **ANSWERED 2026-07-15:** it holds a single filtered download-center portal URL, not enumerated per-product links — a DISCOVER/playbook seed for the Dentsply onboarding, not a fetch backlog.

2. **GC "Article List / According to the Attachment"** — are the attachments present elsewhere in the corpus (e.g., as separate PDFs in the same directory) or are they genuinely missing? Sample check needed.
   **ANSWERED 2026-07-15:** present — embedded as later pages of the same PDFs (§3.3). Nothing is missing.

3. **IVOCLAR internal article numbers** — the 6-digit codes in IVOCLAR DoCs (e.g., 596797) are Ivoclar's internal article numbers. Do they match BC `mfr_ref` values? This is the Phase 0 `mfr_ref` source identification check.
   **ANSWERED (findings-phase0 §G4):** they match BC `item_ref` (not `mfr_ref`) for the ~86% plain-style items; the two IVOCLAR trackers joined 1,052 unique BC rows.

4. **NEODENT `neodent DOC/`** — 5 PDFs classified as "Other". Are these actual DoCs/IFUs that the keyword classifier missed (Portuguese template, `FRM-000922`)? Manual check needed — they may be the only real compliance docs for NEODENT.
   **ANSWERED 2026-07-15:** yes — the dir holds 3 files (not 5): 2 DoCs + 1 IFU, template marker FRM-000922, all genuine (§2.3).

5. **KOMET third-party subdirectory** — `Artikli, kjer je originalni proizvajalec drug kot Komet/` ("Articles where the original manufacturer is not Komet"). These contain certificates from other manufacturers (e.g., MetaBiomed). Should they be extracted under KOMET's manufacturer scope or the original manufacturer's?
   **PARTIALLY ANSWERED 2026-07-15:** the PDFs are authored by the original manufacturers with their own SRNs (Stoddard Manufacturing GB-MF-000004681, META BIOMED KR-MF-000009681, Alfred Becht DE-MF-000007020, SHOFU), and `SPECIFIKACIJA.docx` + the numbered dirs carry the Komet-code→manufacturer-DoC mapping. Extraction should record the document-stated manufacturer; the coverage-subject/binding decision is still open.

---

## 11. Verification log

2026-07-15 · Method: deterministic page-1 text sweep over all 1,307 PDFs (`pdftotext`, <100 non-whitespace chars = scanned) reproduced §1 exactly — 300 scanned total and every per-brand count (DENSTPLY 204, DENTAURUM 33, IVOCLAR 27, VOCO 15, KOMET 13, GC 6, PLANMECA 1, 3SHAPE 1, others 0). A fanned-out sampling pass read ~65 PDFs across all 12 brands and parsed all support spreadsheets; ~30 claims from this doc checked, all confirmed except the **[corrected 2026-07-15]** items. One sampling verdict ("no Declaration of Compatibility exists in DENSTPLY") was itself wrong and overruled by a full-text grep over all DENSTPLY text PDFs: 17 Article 22 declarations exist.
