"""Labeled fixture manifest for EXTRACT-tier tests (real corpus subset).

22 representative PDFs live under ``tests/fixtures/corpus/<MANU>/`` (committed, so
tier tests run in CI). Each entry separates three things:

* ``t0``   — what the DETERMINISTIC tier currently extracts, VERIFIED against the
             real file (asserted exactly in test_t0_corpus). Each value carries
             tier=='T0'. This is the regression baseline. Scalar fields only;
             list fields (ref_list) are asserted by floor via ``t0_ref_min``.
* ``ground_truth`` — the full 8-field human-read truth. Superset of ``t0``; the
             backbone for the (deferred) T1/T2 accuracy harness. ``None`` = the
             field is genuinely absent from the document. ``PENDING`` = present
             but only readable by vision (scanned doc) — to be filled from T2.
* ``notes`` — why this file is in the set / what it exercises.

Tests pass the FULL fixture path as the extractor's ``filename``. That arg is
dual-purpose in t0_extract: filename-signal fallbacks read it, and pdfplumber
re-opens the PDF from it for REF extraction. Fixtures are flattened to
``corpus/<MANU>/`` (manufacturer dirs carry no type/reg tokens), so the full path
both avoids the folder-leak that the original SFTP paths caused (e.g. an "MDR -
Declaration of Conformity" folder forcing regulation=MDR onto a battery DoC) AND
lets REF extraction open the file. The handler's real signal source (hash-addressed
archive vs original filename) is an open S0.2 design point — see followups.md.

Labels proposed from document content on 2026-07-06; flagged for Denis review.

The 8 target fields: type, regulation, validity_from, validity_to,
coverage_scope, ref_list, basic_udi_di, cert_number.
"""

from __future__ import annotations

import pathlib

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"

PENDING = "PENDING"  # present in doc but requires T2/vision (scanned) to read
EXTERNAL = "external"  # DoC references an external/attached article list, not enumerable at T0
TEXTLIST = "text-list"  # REF codes present as a text column (not a pdfplumber table)


# name is the exact committed basename under corpus/<manu>/
FIXTURES: list[dict] = [
    # ---- clean text DoCs (T0 does well) ------------------------------------ #
    {
        "manu": "VOCO",
        "name": "VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDR",
            "cert_number": "0675GB448260109",
            "basic_udi_di": "++E2210236000000000CN",
            "coverage_scope": "group",
        },
        "t0_ref_min": 2,
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": "2031-01-21",
            "coverage_scope": "group", "ref_list": "2 codes ('REF #' table, page 2)",
            "basic_udi_di": "++E2210236000000000CN",
            "cert_number": "0675GB448260109",
        },
        "notes": "MDR DoC. T0 gets type/reg/cert/udi. Misses validity_to "
                 "('Declaration valid until: January 21, 2031' — month-name date). "
                 "Page 2 carries a genuine 'REF #' table (1106/1107) alongside a "
                 "'see item list' phrase that previously (wrongly) suppressed "
                 "extraction entirely — S1.5 gate removal now finds it (closed T0 "
                 "gap, same fix class as the GC embedded-attachment cases). T1 "
                 "targets validity_to.",
    },
    {
        "manu": "VOCO",
        "name": "VOCO_DoC_MDD_Bifix Temp_2021-2.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDD", "coverage_scope": "group",
        },
        "t0_ref_min": 1,  # pdfplumber table yields REF '1055'
        "ground_truth": {
            "type": "DoC", "regulation": "MDD",
            "validity_from": "2021-02-18", "validity_to": None,
            "coverage_scope": "group", "ref_list": ["1055"],
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "MDD DoC with an inline REF table (pdfplumber finds '1055'). "
                 "validity_from ('February 18, 2021' month-name) missed by T0; "
                 "no explicit expiry ('valid as long as the certificate is valid').",
    },
    {
        "manu": "GC",
        "name": "New_Metal_Strips_10102022_R.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDR",
            "basic_udi_di": "++J022MD0042K2", "coverage_scope": "group",
        },
        "t0_ref_min": 15,  # T0 captures 18
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "18 codes (page 2, embedded)",
            "basic_udi_di": "++J022MD0042K2", "cert_number": None,
        },
        "notes": "Class I MDR DoC. 'According to the Attachment' article list is "
                 "EMBEDDED on the PDF's own page 2, not a separate file — S1.5 "
                 "removed the detect_external_ref_list extraction gate (closed T0 "
                 "gap, corpus-verify followup 2026-07-15).",
    },
    {
        "manu": "GC",
        "name": "everX_Posterior_12022026.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"},
        "t0_ref_min": 5,  # T0 captures 6
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "6 codes (page 5, embedded)",
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "Class IIa MDR DoC, 8-language header (EN/DE/FR/IT/LV/PT/SL/TR). "
                 "Exercises multilingual type/regulation keyword coverage (G6). REF "
                 "list embedded on page 5 (same gate fix as New_Metal_Strips); "
                 "Article-Code column previously shadowed by a false 'IUD-ID de "
                 "base' Basic-UDI-DI header match (S1.5 _ref_column fix).",
    },
    {
        "manu": "GC",
        "name": "Unifast_III_23042025.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        # basic_udi_di and cert_number are real in this document but T0 misses
        # both (T1 recovers them) — they belong in ground_truth, not here.
        "t0": {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"},
        "t0_ref_min": 50,  # T0 captures 56 across pages 5-6
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "56 codes (pages 5-6, embedded)",
            "basic_udi_di": "++J022MD0102JT", "cert_number": "MDR 778483",
        },
        "notes": "Multi-page article table. Two bugs, both closed 2026-08-12: the "
                 "playbook asked for the per-page 'table' strategy, which stopped at "
                 "page 5 and silently dropped 41 of 56 codes; and "
                 "ref_from_stitched_tables re-scanned the header locally instead of "
                 "using _ref_column, so it picked the Basic-UDI-DI column and "
                 "returned '++J022MD0102JT' 56 times. Catalogue item 003489 lives on "
                 "the continuation page and was unreachable until both were fixed.",
    },
    {
        "manu": "GC",
        "name": "Fuji_Coat_LC_12022026.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"},
        "t0_ref_min": 2,  # T0 captures exactly 2
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "2 codes (page 5, embedded)",
            "basic_udi_di": "++J022MD0098KV", "cert_number": None,
        },
        "notes": "Carries a spurious 1-row table on page 3 whose single cell reads "
                 "as a REF column. The stitched strategy latched onto it, and page 4 "
                 "(no tables at all) then ended the scan before page 5 where the real "
                 "20-row article table lives — returning zero codes and losing "
                 "catalogue item 000176. Fixed 2026-08-12 by requiring a header "
                 "candidate to actually yield codes.",
    },
    {
        "manu": "STRAUMANN",
        "name": "Izjava o skladnosti za vsadke TL SP.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "regulation": "MDR", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "~125 codes",
            "basic_udi_di": None, "cert_number": "G10 020326 0062",
        },
        "notes": "Slovenian filename, EN/DE body. Class IIb implants DoC. Covers "
                 "125 articles ('125 Article(s)') -> group; EU certificate "
                 "G10 020326 0062. [verified from full text 2026-07-06]",
    },
    {
        "manu": "PLANMECA",
        "name": "Viso G1 MDR EN.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDR", "coverage_scope": "manufacturer",
            "basic_udi_di": "6430035420245R",
        },
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": "6430035420245R", "cert_number": "FI23/00000059",
        },
        "notes": "Single-device MDR DoC, class IIb. basic_udi_di 'BASIC UDI-DI "
                 "(GMN) 6430035420245R' — S1.5 broadened _UDI_LABEL for the "
                 "'(GMN)' parenthetical (closed T0 gap). cert_number 'EC certificate: "
                 "FI23/00000059, issue 6' still missed by T0 (different label shape).",
    },
    {
        "manu": "NEODENT",
        "name": "neodent izjava o skaldnosti za T-baze.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "coverage_scope": "group"},
        "t0_ref_min": 940,  # 947 through the NEODENT text-column template, 2026-09-11
        "ground_truth": {
            "type": "DoC", "regulation": "MDD",
            "validity_from": None, "validity_to": "2027-12-31",
            "coverage_scope": "group",
            "ref_list": "948 codes, Item | Description | GMDN Code table, pages 1-26",
            "basic_udi_di": None, "cert_number": "2224396CE01",
        },
        "notes": "Scope corrected 2026-09-11 from `manufacturer`: the declaration "
                 "enumerates its devices, and T0 now reads that table through "
                 "playbooks/neodent.json ([neodent-ref-list-needs-t0]); before, "
                 "with no template, T0 found no REF list and scoped it to the "
                 "manufacturer. "
                 "PT/EN, 26 pages. MDD legacy device under MDR Art.120(3). Regulation "
                 "only in the title string 'DoC MDD ND_Class IIb' (no '93/42' phrase) "
                 "so T0 abstains — semantic case for T1. Expiry '31 December 2027' and "
                 "EC cert '2224396CE01' both buried deep in text, missed by T0. "
                 "[verified from full text 2026-07-06]",
    },
    {
        "manu": "KOMET",
        "name": "532862_RA_810_DoC_EU_MDR_SIGNED.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDR",
            "cert_number": "HZ 1470094-1 G",  # over-captures trailing 'G' (see followups)
            "basic_udi_di": "++E22653286254",
            "coverage_scope": "manufacturer",
        },
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": "2031-02-28",
            "coverage_scope": "group", "ref_list": EXTERNAL,
            "basic_udi_di": "++E22653286254", "cert_number": "HZ 1470094-1",
        },
        "notes": "DE/EN Brasseler DoC. External 'Artikelliste' -> group. T0 "
                 "over-captures cert as 'HZ 1470094-1 G' — the 'G' is the start of "
                 "the very next word 'Gültigkeitsdatum 2031-02-28', which is the "
                 "expiry T0 also misses (date split by a line break). "
                 "[verified from image 2026-07-06]",
    },

    # ---- ISO / QMS certs (manufacturer scope, no REF list — G2) ------------- #
    {
        "manu": "IVOCLAR",
        "name": "Ivoclar ISO certifikat do 30_10_2027.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "ISO", "cert_number": "Q5 043306 0209 Rev. 06",
            "validity_from": "2024-10-31", "validity_to": "2027-10-30",
            "coverage_scope": "manufacturer",
        },
        "ground_truth": {
            "type": "ISO", "regulation": None,
            "validity_from": "2024-10-31", "validity_to": "2027-10-30",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "Q5 043306 0209 Rev. 06",
        },
        "notes": "TUV SUD ISO 13485 QMS cert. T0 nails it (dates ISO-format). "
                 "Existing test_t0/test_pdf reference file. G2: no REF list.",
    },
    {
        "manu": "3SHAPE",
        "name": "ISO 13485 - Certificate 3Shape AS.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "ISO", "cert_number": "MD 583456",
            "validity_to": "2026-05-06", "coverage_scope": "manufacturer",
        },
        "ground_truth": {
            "type": "ISO", "regulation": None,
            "validity_from": "2023-05-07", "validity_to": "2026-05-06",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "MD 583456",
        },
        "notes": "BSI ISO 13485 cert. validity_from ('Effective Date: 2023-05-07') "
                 "missed — label not in T0's _FROM_LABELS. T1 target.",
    },

    # ---- EC certificates --------------------------------------------------- #
    {
        "manu": "DENSTPLY",
        "name": "IMP - EC Certificate - MDD - Dentsply Implants Manufacturing GmbH - G1 082649 0002 - EN.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "EC", "regulation": "MDD",
            "validity_from": "2019-02-01", "validity_to": "2023-06-30",
            "coverage_scope": "manufacturer",
            "cert_number": "G1 082649 0002 Rev. 00",
        },
        "ground_truth": {
            "type": "EC", "regulation": "MDD",
            "validity_from": "2019-02-01", "validity_to": "2023-06-30",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "G1 082649 0002 Rev. 00",
        },
        "notes": "TUV SUD MDD EC cert (expired 2023 — past-date handling). "
                 "cert_number 'No. G1 082649 0002 Rev. 00' — S1.5 added a bare-'No.' "
                 "same-line fallback regex (closed T0 gap).",
    },
    {
        "manu": "DENSTPLY",
        "name": "LAB-UKCA-Certificate-795695-Brazil-EN.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "EC", "cert_number": "UKCA 795695",
            "validity_to": "2029-09-05", "coverage_scope": "manufacturer",
        },
        "ground_truth": {
            "type": "EC", "regulation": None,
            "validity_from": "2024-09-06", "validity_to": "2029-09-05",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "UKCA 795695",
        },
        "notes": "UK UKCA cert (not EU MDR/MDD -> regulation None). Edge: non-EU "
                 "conformity route. validity_from ('First Issued: 2024-09-06') "
                 "label not recognised by T0.",
    },

    # ---- DoCs that correctly ABSTAIN on regulation (not MDR/MDD) ----------- #
    {
        "manu": "VOCO",
        "name": "VOCO_DoC_(EU)2023-1542_Celalux 3.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": None,
            "validity_from": "2025-02-14", "validity_to": None,
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "Filed under 'MDR - Declaration of Conformity' but the body cites "
                 "EU2023/1542 (batteries) + REACH — NOT a medical-device reg. T0 "
                 "correctly returns regulation=None. Folder name must NOT force MDR "
                 "(passes basename, not path). Key negative case.",
    },
    {
        "manu": "DENSTPLY",
        "name": "EI-INS-DoC-Siroseal Premium-6526961-2021-05-21.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": None,
            "validity_from": None, "validity_to": None,
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "MELAG bar-sealer DoC (reseller = Dentsply, mfr = MELAG). Cites "
                 "2014/35/EU (Low Voltage) — not MDR/MDD, so regulation=None. "
                 "canonical_manufacturer != folder owner (REF-gate relevance).",
    },
    {
        "manu": "LUMIWHITE",
        "name": "Declaration_of_Conformity_LUMIWHITE_OU.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": None,
            "validity_from": None, "validity_to": None,
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "COSMETIC product declaration (Regulation (EC) 1223/2009) — NOT a "
                 "medical device (folder 'ni MD - KOZMETIKA'). T0 can't tell it's "
                 "out-of-scope; downstream gate/T1 must flag. Scope edge case.",
    },

    # ---- KOMET text-column REF list: closed by the S1.5 layout engine ------ #
    {
        "manu": "KOMET",
        "name": "533068_RA_812_Liste_DoC.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "coverage_scope": "group"},
        # 19 of the page's 20 rows. The 20th prints its code with the size
        # column blank ("589.204.", a BOHRERSCHAFTVERLÄNGERUNG) and the Komet
        # playbook pattern deliberately does not take a sizeless code -- see
        # test_t0_layout.py::test_the_komet_pattern_leaves_out_a_sizeless_code
        # for the corpus measurement behind that choice.
        "t0_ref_min": 19,
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": TEXTLIST,
            "basic_udi_di": "++E2265330681", "cert_number": None,
        },
        "notes": "KOMET 'Liste_DoC' — REF codes (033792R0, K210L16.204.020, ...) are "
                 "in a TEXT COLUMN, not a pdfplumber table (the KOMET layout template's "
                 "text-column ref_strategy closes this, docs/specs/t0-layout.md §0). "
                 "~40+ files of this shape. basic_udi_di is a per-row column value, not "
                 "a single doc-level label — out of scope here (T0 doesn't extract it).",
    },

    # ---- scanned docs -> T2 (is_scan True, T0 gets little) ----------------- #
    {
        "manu": "DENSTPLY",
        "name": "IMP - DOC - ATIS TX SURGICAL INSTRUMENTS - DEC-00101190.pdf",
        "doc_class": "compliance-doc",
        "is_scan": True,
        "t0": {"type": "DoC", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": "2025-05-27", "validity_to": "2030-05-26",
            "coverage_scope": "group", "ref_list": EXTERNAL,
            "basic_udi_di": None, "cert_number": "G10 082649 0004",
        },
        "notes": "Scanned (0 text). Represents DENSTPLY/DOC where 196/355 are "
                 "scanned — T2 is the PRIMARY tier here, not a fallback. T0 only "
                 "gets type=DoC from the basename. ATIS TX Surgical Instruments, "
                 "MDR, Cert of Conformity G10 082649 0004, valid until 2030-05-26 "
                 "(annex list -> group). [verified from page-1 image 2026-07-06]",
    },
    {
        "manu": "DENTAURUM",
        "name": "TD_14_Konformitätserklärung_Kl. IIa_2021_05_25.pdf",
        "doc_class": "compliance-doc",
        "is_scan": True,
        "t0": {"type": "DoC", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "DoC", "regulation": "MDD",
            "validity_from": "2021-05-25", "validity_to": "2023-12-17",
            "coverage_scope": "group", "ref_list": EXTERNAL,
            "basic_udi_di": None, "cert_number": "D1002600038",
        },
        "notes": "Scanned German Konformitaetserklaerung (DENTAURUM/DOC is 30/30 "
                 "scanned). Class IIa, RL 93/42 -> MDD. Product group 014 dental "
                 "ceramics (attachment -> group); EC-cert D1002600038; valid until "
                 "2023-12-17 (expired). [verified from page-1 image 2026-07-06]",
    },
    {
        "manu": "IVOCLAR",
        "name": "MDR Certificate IV AG 2017_745.pdf",
        "doc_class": "compliance-doc",
        "is_scan": True,
        "t0": {"type": "EC", "regulation": "MDR", "coverage_scope": "manufacturer"},
        "ground_truth": {
            "type": "ISO", "regulation": "MDR",
            "validity_from": "2026-05-05", "validity_to": "2031-05-04",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "G15 043306 0282 Rev. 01",
        },
        "notes": "Scanned. It is an 'EU Quality Management System Certificate' "
                 "(Annex IX Ch.I) -> type is really ISO/QMS, NOT EC. T0 guesses "
                 "type=EC from the basename 'Certificate' (0.6) — a real T0-vs-truth "
                 "mismatch that vision corrects. Cert G15 043306 0282 Rev.01 is the "
                 "very QMS cert the e.max DoC references (Rev.00). "
                 "[verified from page-1 image 2026-07-06]",
    },
    {
        "manu": "VOCO",
        "name": "93-42 Zertifikat_DE.pdf",
        "doc_class": "compliance-doc",
        "is_scan": True,
        "t0": {"regulation": "MDD"},
        "ground_truth": {
            "type": "EC", "regulation": "MDD",
            "validity_from": "2021-03-01", "validity_to": "2024-05-27",
            "coverage_scope": "manufacturer", "ref_list": None,
            "basic_udi_di": None, "cert_number": "0675DE410210301B",
        },
        "notes": "Scanned MEDCERT EG-Konformitaetsbescheinigung (EC cert) under MDD, "
                 "cert 0675DE410210301B, valid 2021-03-01..2024-05-27 (expired). "
                 "From basename T0 gets ONLY regulation=MDD ('93-42'); "
                 "type=EC is NOT inferable (basename 'Zertifikat' has no 'cert' "
                 "substring; only the SFTP 'Certificates' folder did — which we "
                 "correctly don't use). Confirms folder-leak avoidance. Type -> T2.",
    },

    # ---- rich REF-list DoC (pdfplumber table works) ------------------------ #
    {
        "manu": "IVOCLAR",
        "name": "IPS e.max Ceram.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {
            "type": "DoC", "regulation": "MDR",
            "cert_number": "G15 043306 0282 Rev. 00",
            "validity_to": "2026-05-04",
            "coverage_scope": "group",
            "basic_udi_di": "76152082ACERA008F6",
        },
        "t0_ref_min": 200,  # T0 captures exactly 206 (stitched-table ref_strategy)
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": "2026-05-04",
            "coverage_scope": "group", "ref_list": "~206 codes (pages 3-7)",
            "basic_udi_di": "76152082ACERA008F6", "cert_number": "G15 043306 0282 Rev. 00",
        },
        "notes": "Ivoclar IVAG DoC. The REF attachment spans pages 3-7 (~206 codes, "
                 "596839..762718EN) but T0 captures only 46 — pdfplumber detects the "
                 "table on the first attachment page only; continuation pages lack a "
                 "repeated header (see followups, multi-page REF tables; S1.5 t0_layout "
                 "stitched-table strategy closes this). basic_udi_di "
                 "'Basic-UDI-DI 76152082ACERA008F6' — S1.5 broadened _UDI_LABEL for the "
                 "hyphen variant (closed T0 gap). [verified from full text 2026-07-06]",
    },

    # ---- MSDS: must be skipped (G1) ---------------------------------------- #
    {
        "manu": "GC",
        "name": "pattern-resin-ls-liquid-sds-ca-en.pdf",
        "doc_class": "msds",
        "is_scan": False,
        "t0": {},  # short-circuits: safety data sheet, no compliance fields
        "ground_truth": {
            "type": None, "regulation": None,
            "validity_from": None, "validity_to": None,
            "coverage_scope": None, "ref_list": None,
            "basic_udi_di": None, "cert_number": None,
        },
        "notes": "Safety Data Sheet — G1 doc-class gate must skip it (no compliance "
                 "extraction). Negative case: pipeline must not treat MSDS as a DoC.",
    },
]


# Fields T0 SHOULD extract deterministically but currently can't. The corpus test
# marks these xfail(strict=False): they surface as xfail now and flip to xpass the
# moment the underlying regex/parser is fixed (then update this list + the manifest).
KNOWN_T0_GAPS: list[dict] = []
# All four S0.4-era entries closed in S1.5 (docs/specs/t0-layout.md §0):
# 533068_RA_812_Liste_DoC.pdf::ref_list (KOMET text-column ref_strategy),
# IPS e.max Ceram.pdf::basic_udi_di (hyphen-label regex broadening),
# Viso G1 MDR EN.pdf::basic_udi_di ((GMN) parenthetical),
# IMP - EC Certificate ...::cert_number (bare-No fallback).

TARGET_FIELDS = (
    "type", "regulation", "validity_from", "validity_to",
    "coverage_scope", "ref_list", "basic_udi_di", "cert_number",
)


def fixture_path(entry: dict) -> pathlib.Path:
    return CORPUS_DIR / entry["manu"] / entry["name"]


def by_name(name: str) -> dict:
    for e in FIXTURES:
        if e["name"] == name:
            return e
    raise KeyError(name)
