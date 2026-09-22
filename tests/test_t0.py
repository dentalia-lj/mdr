"""T0 deterministic extractor: classification, field regexes, guardrails.

Text-based extractors are pure functions on strings (fast, no corpus). REF-list
extraction and the whole-document orchestrator are exercised on real corpus PDFs
(skipped when the corpus is absent).
"""

from __future__ import annotations

import pytest

from app.extract import pdf
from app.extract import t0_templates as t0

# committed fixtures (tests/fixtures/corpus/), so these run in CI
ISO_CERT = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"
REF_DOC = "VOCO/VOCO_DoC_MDD_Bifix Temp_2021-2.pdf"  # inline REF table -> '1055'


# --------------------------------------------------------------------------- #
# document-class gate (guardrail G1)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("RAČUN - DOBAVNICA št. 123 za dentalni material", "business-doc"),
        ("PONUDBA / OFFER No. 5", "business-doc"),
        ("Safety Data Sheet according to Regulation (EC) No 1907/2006", "msds"),
        ("EU Declaration of Conformity per Regulation (EU) 2017/745", "compliance-doc"),
        ("Quality Management System Certificate ISO 13485:2016", "compliance-doc"),
        ("some unrelated brochure text", "unknown"),
        # Real GC Slovenian SDS layout: pymupdf extracts a justified/tabular
        # page as double-spaced text, so a marker matched against RAW text is
        # invisible and the document walks into the paid tiers ([sds-reaches-
        # extraction], 2026-08-13). Every marker family is matched the same way,
        # so this is not specific to MSDS.
        ("Varnostni  list  za  kemikalijo\nRazdelek  1", "msds"),
        ("Sicherheitsdatenblatt gemäß  1907/2006", "msds"),
        ("EU  Declaration  of  Conformity", "compliance-doc"),
        ("RAČUN  -  DOBAVNICA  št.  123", "business-doc"),
    ],
)
def test_classify_doc_class(text, expected):
    assert t0.classify_doc_class(text) == expected


def test_classify_folds_whitespace_before_matching():
    """The marker list is the contract; how the PDF spaced its glyphs is not.

    Guards the mutation that reverts `classify_doc_class` to matching raw text:
    with the fold removed this returns "unknown" and the document is extracted
    at full price."""
    assert t0.classify_doc_class("varnostni\t\tlist") == "msds"
    assert t0.classify_doc_class("varnostni\nlist") == "msds"
    assert t0.classify_doc_class("varnostni     list") == "msds"
    # Folding collapses runs of whitespace; it must not delete them. A marker
    # is still a phrase, so the word on its own is not a match.
    assert t0.classify_doc_class("varnostnilist") == "unknown"
    assert t0.classify_doc_class("varnostni ukrepi za list papirja") == "unknown"


# --------------------------------------------------------------------------- #
# type
# --------------------------------------------------------------------------- #
def test_type_from_text_is_conf_1_with_evidence():
    ev = t0.extract_type("EU Declaration of Conformity", "irrelevant.pdf")
    assert ev["value"] == "DoC"
    assert ev["conf"] == 1.0
    assert ev["tier"] == "T0"
    assert "Declaration of Conformity" in ev["verbatim"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("This is an ISO 13485:2016 certificate", "ISO"),
        ("EC Certificate issued by notified body", "EC"),
        ("Instructions for Use — read carefully", "IFU"),
        ("Konformitätserklärung gemäß", "DoC"),
        ("Izjava o skladnosti", "DoC"),
        ("EC-Declaration of Conformity", "DoC"),  # DoC wins over EC
    ],
)
def test_type_variants(text, expected):
    assert t0.extract_type(text, "")["value"] == expected


def test_type_filename_fallback_is_lower_confidence():
    ev = t0.extract_type("body has no type markers", "Ivoclar_DoC_MDR.pdf")
    assert ev["value"] == "DoC"
    assert ev["conf"] < 1.0


def test_type_none_when_absent():
    assert t0.extract_type("nothing relevant here", "nothing.pdf") is None


# --------------------------------------------------------------------------- #
# type: subject is not class (doc 420 and friends, 2026-08-19 measurement)
#
# `_TYPE_MARKERS` conflated what a document is ABOUT with what it IS. Seven
# documents across five manufacturers were typed ISO off the phrase "quality
# management system"; every one is a certificate or a declaration, and doc 420
# reached `production` that way. Two aggravating mechanics, both measured: the
# scan returned the first marker in LIST order found ANYWHERE, and a MENTION of
# a document type was read as the document's own identity at confidence 1.0.
# --------------------------------------------------------------------------- #
DOC_420_HEAD = (
    "[[page 1]]\nEU Certificate\nQuality Management System\n"
    "REGULATION (EU) 2017/745 on Medical Devices\n"
    "Annex IX Chapters I and III\nRegistration No.:\nHZ 1470094-1"
)

DOC_248_HEAD = (
    "[[page 1]]\nEU Quality Management System Certificate\n"
    "Regulation (EU) 2017/745, Annex IX Chapter I and III\nMDR 737603 R000"
)


def test_eu_certificate_about_a_qms_is_a_certificate_not_an_iso():
    """Doc 420, live at `production` with the wrong type since 2026-08-12."""
    ev = t0.extract_type(DOC_420_HEAD, "94-1_Rev6_CVE_2026-03-01_signed.pdf")
    assert ev["value"] == "EC"


def test_quality_management_system_alone_never_types_iso():
    """Docs 248/260/316. The phrase describes the SUBJECT of a certificate and
    is equally true of an ISO 13485 one, so it settles nothing: T0 must decline
    and let the ladder decide rather than assert a wrong type at conf 1.0."""
    ev = t0.extract_type(DOC_248_HEAD, "")
    assert ev is None or ev["value"] != "ISO"


def test_iso_13485_is_still_the_iso_signal():
    ev = t0.extract_type("Certificate\nISO 13485:2016\nQuality Management System", "")
    assert ev["value"] == "ISO"


def test_earliest_marker_in_the_document_wins_over_list_order():
    """NEODENT doc 520: an IFU that CITES a declaration was typed DoC at 1.0
    because `declaration of conformity` sits earlier in `_TYPE_MARKERS` than
    `instructions for use`, regardless of where either appears on the page."""
    text = "Instructions for Use\n" + ("filler line\n" * 40) + \
           "This device is covered by a Declaration of Conformity."
    assert t0.extract_type(text, "")["value"] == "IFU"


def test_a_declaration_that_cites_iso_13485_is_still_a_declaration():
    """CARL MARTIN `DOC - 2008.pdf`, found when its folder arrived 2026-08-20.

    `iso 13485` is a STANDARD NAME, and standards get cited -- this MDD
    declaration names the manufacturer's certification at character 943, before
    its own `konformitätserklärung` at 1023. Earliest-position alone reads that
    citation as the document's class, which is the same subject-versus-class
    confusion the QMS marker caused, one phrase over. So ISO must be the ONLY
    type family present to win: no other document type announces itself by
    naming a standard, and no declaration or certificate stops being one
    because it cites the QMS it was issued under."""
    text = ("Gemäß EG-Richtlinie 93/42/EWG\n" + "x" * 900 +
            "\nEN ISO 13485\nKonformitätserklärung\nDeclaration of Conformity")
    assert t0.extract_type(text, "")["value"] == "DoC"


def test_iso_wins_when_it_is_the_only_type_family_on_the_page():
    text = "[[page 1]]\nCertificate\nQuality Management System\nEN ISO 13485:2016"
    assert t0.extract_type(text, "")["value"] == "ISO"


def test_a_type_mentioned_deep_in_the_body_is_not_settled():
    """conf 1.0 means "an exact phrase matched", never "the type is certain".
    A marker found past the title region is a mention; it must stay under
    `_TYPE_SETTLED_CONF` so a later tier can overrule it."""
    text = "[[page 1]]\nInstructions for Use\n" + ("boilerplate\n" * 1400) + \
           "covered by the Declaration of Conformity referenced above"
    assert len(text) > t0._TYPE_TITLE_CHARS
    ev = t0.extract_type(text[text.index("boilerplate"):], "")
    assert ev["value"] == "DoC"
    assert ev["conf"] < t0._TYPE_SETTLED_CONF


# --------------------------------------------------------------------------- #
# regulation
# --------------------------------------------------------------------------- #
def test_regulation_mdr_from_text():
    assert t0.extract_regulation("... Regulation (EU) 2017/745 ...", "")["value"] == "MDR"


def test_regulation_mdd_from_text():
    assert t0.extract_regulation("... Council Directive 93/42/EEC ...", "")["value"] == "MDD"


def test_regulation_abstains_when_both_present():
    # transition-period doc referencing both -> T0 abstains, escalate to T1
    assert t0.extract_regulation("covers 2017/745 and 93/42/EEC", "") is None


def test_regulation_filename_fallback():
    ev = t0.extract_regulation("no legal reference in body", "Straumann cert (MDR).pdf")
    assert ev["value"] == "MDR"
    assert ev["conf"] < 1.0


# --------------------------------------------------------------------------- #
# cert_number / basic_udi_di
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Certificate No.: 0675GB448260109", "0675GB448260109"),
        ("Zertifikatsnr.: HZ 1470094-1", "HZ 1470094-1"),
    ],
)
def test_cert_number(text, expected):
    assert t0.extract_cert_number(text)["value"] == expected


def test_basic_udi_di():
    # A real IVOCLAR code from the registry. It used to be `7612147BDTPFEW`,
    # which is synthetic and fails its own check pair (expected TU, got EW) --
    # harmless while nothing verified it, misleading now that something does.
    assert (t0.extract_basic_udi("Basic UDI-DI: 76152082ACERA004EW")["value"]
            == "76152082ACERA004EW")


# --------------------------------------------------------------------------- #
# check-character scoring (app.extract.udi wired into the T0 field)
# --------------------------------------------------------------------------- #
def test_a_verified_basic_udi_keeps_full_confidence():
    """1.0 is the pre-existing behaviour for a good code -- no regression."""
    assert t0.extract_basic_udi("Basic UDI-DI: 76152082ACERA004EW")["conf"] == 1.0


def test_a_basic_udi_that_fails_its_check_pair_is_not_trusted():
    """Confidence must fall below `needs_escalation`'s 0.95 so the document
    goes to a higher tier, rather than feeding a corrupt identifier to the
    `basic-udi-di` link basis that Invariant 3 treats as production-capable."""
    ev = t0.extract_basic_udi("Basic UDI-DI: ++E221049001OOOOOOOFX")
    assert ev["value"] == "++E221049001OOOOOOOFX"   # never rewritten or dropped
    assert ev["conf"] < 0.95


def test_an_unverifiable_basic_udi_is_also_not_trusted():
    """Junk the label regex swallowed -- `document.basic_udi_di` holds seven
    such rows today, every one of them at confidence 1.0."""
    assert t0.extract_basic_udi("Basic UDI-DI: Document")["conf"] < 0.95


def test_a_failed_check_pair_is_reported_as_an_anomaly():
    from app.results import Result

    r = Result()
    t0.extract_basic_udi("Basic UDI-DI: ++E221049001OOOOOOOFX", r)
    kinds = [a["kind"] for a in r.as_dict()["anomalies"]]
    assert "basic_udi_check_failed" in kinds


def test_a_good_basic_udi_reports_no_anomaly():
    from app.results import Result

    r = Result()
    t0.extract_basic_udi("Basic UDI-DI: 76152082ACERA004EW", r)
    assert not r.as_dict()["anomalies"]


def test_extraction_without_a_result_envelope_still_scores():
    """T0 runs from tools and tests with no job envelope; an anomaly is a
    count, never an exception."""
    assert t0.extract_basic_udi("Basic UDI-DI: ++E221049001OOOOOOOFX")["conf"] < 0.95


def test_basic_udi_di_hyphen_variant():
    # IVOCLAR e.max: "Basic-UDI-DI" (hyphen between Basic and UDI too).
    assert t0.extract_basic_udi("Basic-UDI-DI 76152082ACERA008F6")["value"] == "76152082ACERA008F6"


def test_basic_udi_di_gmn_parenthetical_variant():
    # PLANMECA: parenthetical between the label and the value.
    assert (
        t0.extract_basic_udi("with BASIC UDI-DI (GMN) 6430035420245R")["value"]
        == "6430035420245R"
    )


def test_cert_number_bare_no_same_line():
    # Real DENSTPLY EC-cert shape: "Certificate" and "No." are several
    # non-whitespace lines apart, so the primary "certificate\s*no" regex
    # cannot bridge them — this must resolve via the bare-No fallback.
    text = (
        "EC Certificate\n"
        "Full Quality Assurance System\n"
        "Directive 93/42/EEC on Medical Devices (MDD), Annex II excluding (4)\n"
        "(Devices in Class IIa, IIb or III)\n"
        "No. G1 082649 0002 Rev. 00\n"
        "Page 1 of 1"
    )
    assert t0.extract_cert_number(text)["value"] == "G1 082649 0002 Rev. 00"


def test_cert_number_bare_no_does_not_match_wrapped_table_header():
    # A pdfplumber-extracted table header cell wraps "Article\nNo." across a
    # newline, followed by the next cell's text ("Description") on its own
    # line — must not be mistaken for a same-line "No. <value>" declaration.
    assert t0.extract_cert_number("Article\nNo.\nDescription") is None


def test_cert_number_bare_no_never_overrides_primary_label():
    # A doc with BOTH a real "Certificate No." label and an unrelated
    # mid-sentence "no." must resolve via the primary label, not the fallback.
    text = "Certificate No.: 0675GB448260109\nNotified Body identification no. 0123"
    assert t0.extract_cert_number(text)["value"] == "0675GB448260109"


def test_playbook_cert_pattern_reads_a_number_the_global_regexes_miss():
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  cert_number_pattern=r"Zertifikat-Kennung\s+([A-Z]{2}-\d{6})")
    text = "Zertifikat-Kennung DE-123456\n"

    ev = t0.extract_cert_number(text, pb)

    assert ev["value"] == "DE-123456"


def test_a_global_cert_label_still_wins_when_both_match():
    """Determinism: the global regexes are tried first, so an authored pattern
    can add a read but can never change one that already worked -- proves the
    branch where the additive property could actually FAIL, not only the one
    where the authored pattern has nothing to compete with."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  cert_number_pattern=r"Kennung\s+(\S+)")
    text = "Certificate No. G1 234567 8901\nKennung WRONG-ONE\n"

    ev = t0.extract_cert_number(text, pb)

    assert ev["value"] == "G1 234567 8901"


def test_no_playbook_leaves_cert_extraction_exactly_as_it_was():
    text = "Certificate No. G1 234567 8901\n"

    assert t0.extract_cert_number(text) == t0.extract_cert_number(text, None)


def test_playbook_cert_pattern_with_an_optional_group_that_does_not_participate():
    """An authored pattern's single capture group is allowed to be OPTIONAL --
    'the label is always there, the number sometimes isn't' is an ordinary
    authoring shape. It passes load-time validation (exactly one group,
    unconditionally) and then, on text where the label matches but the number
    doesn't, `m.group(1)` is None. That must resolve as no match, not raise --
    a crash here previously failed every extract job for that manufacturer."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  cert_number_pattern=r"Zertifikat\s*(?:Nr\.?\s*([A-Z]{2}-\d{6}))?")
    text = "Zertifikat folgt separat\n"

    assert t0.extract_cert_number(text, pb) is None


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,field,iso",
    [
        ("Expiry Date: 07.07.2027", "validity_to", "2027-07-07"),
        ("valid until 2027-10-30", "validity_to", "2027-10-30"),
        ("Issue Date: 2024-03-15", "validity_from", "2024-03-15"),
    ],
)
def test_extract_dates(text, field, iso):
    got = t0.extract_dates(text)
    assert field in got and got[field]["value"] == iso


# --------------------------------------------------------------------------- #
# long-form month names (2026-08-31)
#
# `_DATE_RE` read `2022-07-08` and `07.07.2027` and nothing else, so every date
# a manufacturer spelled out was invisible -- the label could match and the
# value still could not be read. Measured over the corpus that day: of the 620
# text PDFs with no `validity_from`, **464 (75%) contain a long-form date**,
# across VOCO (243), IVOCLAR (120), GC (59), KOMET (14), DENSTPLY (13),
# NEODENT/STRAUMANN (5 each), HENRY SCHEIN (3) and PLANMECA (1).
#
# `docs/dentalia-imports-corpus-analysis.md` listed `January 21, 2031` as an
# observed format in July, beside the two that were implemented. This closes
# the gap that doc already named.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,field,iso",
    [
        # English: "<Month> <D>, <YYYY>" -- VOCO's own spelling.
        ("Issue Date: February 18, 2021", "validity_from", "2021-02-18"),
        ("Valid as of: February 14, 2025", "validity_from", "2025-02-14"),
        ("Issue Date: January 21, 2031", "validity_from", "2031-01-21"),
        # The comma is optional in the wild.
        ("Issue Date: March 3 2024", "validity_from", "2024-03-03"),
        # German/Slovenian: "<D>. <Month> <YYYY>".
        ("Ausstellungsdatum / Issue Date: 18. Februar 2021", "validity_from",
         "2021-02-18"),
        ("Issue Date: 1. marec 2021", "validity_from", "2021-03-01"),
        # Expiry too -- the same regex serves both label lists.
        ("Expiry Date: December 31, 2027", "validity_to", "2027-12-31"),
    ],
)
def test_a_spelled_out_month_is_read_like_a_numeric_date(text, field, iso):
    got = t0.extract_dates(text)
    assert field in got, f"{text!r} produced {sorted(got)}"
    assert got[field]["value"] == iso


def test_valid_as_of_is_a_start_date_label():
    """VOCO's `(EU)2023-1542` declarations label the issue date `Valid as of:`,
    which no global label matched. Specific enough to be unambiguous -- unlike a
    bare `Date:`, which is deliberately still NOT in the list."""
    assert "valid as of" in t0._FROM_LABELS
    got = t0.extract_dates("Valid as of: February 14, 2025")
    assert got["validity_from"]["value"] == "2025-02-14"


def test_a_bare_date_label_is_still_not_read():
    """Held back on purpose (Denis, 2026-08-31). `Date:` matches almost
    anything, and reaching VOCO's Bifix layout also needs the label window
    widened past 46 characters. `validity_from` drives invariant 4's
    auto-supersession, so a confidently wrong date is worse than none. Ships
    only once its false-positive rate is measured."""
    assert "date" not in t0._FROM_LABELS
    assert t0.extract_dates("Date: February 18, 2021") == {}


def test_a_month_name_inside_a_word_is_not_a_date():
    """`march` and `may` are ordinary English words. The pattern must need a
    day and a year around them, or prose starts producing dates."""
    assert t0.extract_dates("Issue Date: the march of progress 2021") == {}
    assert t0.extract_dates("Issue Date: they may attend in 2021") == {}


# --------------------------------------------------------------------------- #
# accent folding + `effective date` (2026-08-31)
#
# Mined from the corpus: DENSTPLY declarations print "Diese Konformitatserklarung
# ist gultig bis: 2028-12-31" -- the font encoding strips the umlauts, so the
# page says `gultig bis` while `_TO_LABELS` says `gültig bis`. G7 in the corpus
# analysis documents exactly this artifact class ("Konjormitätserklärung",
# "ec Certifieate"). The vocabulary was right and the matcher could not reach it.
#
# `effective date` is STRAUMANN's Polarion header ("PDD-00042754, v3.0,
# Uncontrolled Copy, Effective Date: 2024-07-22") and also appears on DENSTPLY
# and 3SHAPE certificates. 68 occurrences in the STRAUMANN set alone.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,iso", [
    # The corpus spelling: umlauts stripped by the encoding.
    ("Diese Konformitatserklarung ist gultig bis: 2028-12-31", "2028-12-31"),
    # The correct spelling still reads -- folding must not cost the original.
    ("Diese Konformitätserklärung ist gültig bis: 2028-12-31", "2028-12-31"),
])
def test_a_stripped_umlaut_still_matches_its_label(text, iso):
    got = t0.extract_dates(text)
    assert got["validity_to"]["value"] == iso


def test_folding_does_not_shift_the_label_window():
    """The fold must be length-preserving. `low.find()` returns an index into
    the FOLDED text and `_DATE_RE` then searches the ORIGINAL -- so a fold that
    changed any string's length would slice the wrong window, and it would only
    show up on a document carrying an accent BEFORE the label. Hence the
    accented prefix here."""
    text = "Ärztliche Prüfung für Zähne. Gültig bis: 2028-12-31"
    assert t0.extract_dates(text)["validity_to"]["value"] == "2028-12-31"


def test_slovenian_carons_fold_too():
    """`č`/`š`/`ž` are the same class of artifact in the Slovenian documents."""
    assert "veljavnost do" in t0._TO_LABELS
    got = t0.extract_dates("Storitve za zobozdravstvo. Veljavnost do: 2027-05-01")
    assert got["validity_to"]["value"] == "2027-05-01"


def test_effective_date_is_a_start_date_label():
    """STRAUMANN's Polarion header. Specific and adjacent, unlike a bare
    `Date:`, which stays out (see the test of that name)."""
    assert "effective date" in t0._FROM_LABELS
    got = t0.extract_dates(
        "PDD-00042754, v3.0, Uncontrolled Copy, Effective Date: 2024-07-22")
    assert got["validity_from"]["value"] == "2024-07-22"


def test_a_more_specific_issue_label_still_beats_effective_date():
    """Order is precedence. A certificate carrying both must read the current
    issue date -- the corpus analysis's §226 rule -- not whichever appears
    first on the page."""
    text = "Effective Date: 2022-01-01\nCurrent Issue Date: 2024-11-25"
    assert t0.extract_dates(text)["validity_from"]["value"] == "2024-11-25"


def test_a_print_timestamp_is_not_an_issue_date():
    """IVOCLAR's most frequent phrase by far (~400 hits) is
    `Printed on 17.11.2025 10:21:00 by Ritter, Regina (RITTREG)` -- when the PDF
    was printed, not when the declaration issued. Rejected on inspection, and
    pinned here so nobody mines the corpus and authors it later."""
    got = t0.extract_dates(
        "Printed on 17.11.2025 10:21:00 by Ritter, Regina (RITTREG)")
    assert "validity_from" not in got


# --------------------------------------------------------------------------- #
# the label window, and dates that cannot exist (2026-08-31)
#
# Found by asking whether a WIDER window would change a date already read. It
# does, on 9 DENSTPLY declarations, and the old value was wrong every time: the
# 30-character window cut `2020-08-06` to `2020-08-0`, which still satisfies
# `\d{4}-\d{1,2}-\d{1,2}`, so `_parse_date` built `2020-08-00` from it. Three
# were visibly broken (day 00) and six were plausible and simply wrong --
# `2021-05-01` for `2021-05-10`. Each file name carries the true date and
# confirms the wider read (`...Air Motor-6439884-2021-05-10.pdf`).
# --------------------------------------------------------------------------- #
def test_a_date_is_not_cut_in_half_by_the_label_window():
    """The DENSTPLY shape: enough text between label and date that 30
    characters lands mid-date."""
    text = "Valid from" + " " * 22 + "2021-05-10"
    assert t0.extract_dates(text)["validity_from"]["value"] == "2021-05-10"


@pytest.mark.parametrize("bad", [
    "Issue Date: 2020-08-00",     # day zero -- what truncation produced
    "Issue Date: 2020-04-31",     # April has 30 days
    "Issue Date: 2021-02-30",     # February never has 30
    "Issue Date: 2021-13-01",     # month 13
])
def test_a_date_that_cannot_exist_is_not_a_date(bad):
    """`_parse_date` reformatted its captures without ever asking whether the
    result was a real day, so `2020-04-31` became evidence. A field with no
    value escalates and gets read by a tier that can see the page; a field with
    an impossible value is stored and believed."""
    assert "validity_from" not in t0.extract_dates(bad)


def test_a_real_date_still_parses():
    """The guard must not cost a leap day."""
    assert t0.extract_dates("Issue Date: 2024-02-29")["validity_from"]["value"] \
        == "2024-02-29"


def test_a_spelled_out_place_and_date_signature_is_read():
    """The unlabelled signature fallback shares `_DATE_RE`'s vocabulary, so it
    gains long-form for the same reason -- it was blind to `Cuxhaven,
    February 18, 2021` while reading `Cuxhaven, 18.02.2021`."""
    got = t0.extract_dates("Cuxhaven, February 18, 2021")
    assert got["validity_from"]["value"] == "2021-02-18"
    assert got["validity_from"]["conf"] == 0.95


def test_playbook_date_label_is_read_when_the_global_list_misses_it():
    """The measured driver: 208 of 239 vision calls went to dates. A label the
    global EN/DE/SL list does not carry sends the whole document to a paid tier
    for a date printed in plain text."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"from": ["ausstellungsdatum"]})
    text = "Konformitätserklärung\nAusstellungsdatum 12.02.2026\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_from"]["value"] == "2026-02-12"


def test_playbook_labels_are_merged_with_the_globals_never_replacing_them():
    """A playbook that REPLACED the global list would silently lose every date
    the generic path already reads."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"from": ["ausstellungsdatum"]})
    text = "Date of issue 12.02.2026\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_from"]["value"] == "2026-02-12"


def test_a_global_label_still_wins_when_both_match():
    """Determinism: the global list is tried first, so adding a playbook label
    can add a read but can never change an existing one."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"to": ["ablaufdatum"]})
    text = "Expiry date 01.01.2030\nAblaufdatum 02.02.2031\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_to"]["value"] == "2030-01-01"


def test_no_playbook_leaves_date_extraction_exactly_as_it_was():
    text = "Date of issue 12.02.2026\nValid until 01.01.2030\n"

    assert t0.extract_dates(text) == t0.extract_dates(text, None)


# --------------------------------------------------------------------------- #
# guardrails G2 (QMS -> manufacturer scope) and G3 (external ref list)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "REF list: According to the Attachment",
        "Artikelliste / Gemäß Anhang",
        "see attached Device Schedule",
    ],
)
def test_detect_external_ref_list(text):
    assert t0.detect_external_ref_list(text) is True


def test_coverage_scope_qms_is_manufacturer():
    assert t0.derive_coverage_scope("ISO", has_ref_list=False)["value"] == "manufacturer"


def test_coverage_scope_product_doc_is_group():
    assert t0.derive_coverage_scope("DoC", has_ref_list=True)["value"] == "group"


# --------------------------------------------------------------------------- #
# _ref_column — UDI-exclusion preference
# --------------------------------------------------------------------------- #
def test_ref_column_prefers_article_over_udi_header():
    # GC everX: a French "IUD-ID de base" cell inside a multi-language Basic
    # UDI-DI header literally contains "ID" (matches the bare `id\b` keyword),
    # which must not shadow the real "Article Code" column next to it.
    table = [
        ["Basis-UDI-DI\nBasic UDI-DI\nIUD-ID de base", "Artikel-Code\nArticle Code"],
        ["++J022MD0126K9", "005117"],
    ]
    assert t0._ref_column(table) == 1


def test_ref_column_regression_no_udi_column_present():
    # No UDI-labelled column at all — must still resolve the genuine header.
    table = [["Description", "REF"], ["Widget", "1055"]]
    assert t0._ref_column(table) == 1


# --------------------------------------------------------------------------- #
# _looks_like_ref — the single choke point for all three REF strategies
# (_ref_from_tables, ref_from_stitched_tables, ref_from_text_columns).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "003477",           # GC article code
    "104 H251EF 060",   # KOMET ISO bur code — the SPACES ARE LOAD BEARING
    "061.7310",         # STRAUMANN
    "9553.204.060",     # KOMET
    "645986WW",         # IVOCLAR, market suffix intact (G5: never normalised here)
    "1055",
    "K210L16.204.020",  # KOMET, from the text-column strategy
])
def test_looks_like_ref_accepts_real_codes(value):
    assert t0._looks_like_ref(value) is True


@pytest.mark.parametrize("value", [
    "++J022MD0102JT",                 # Basic UDI-DI — extract_basic_udi owns this field
    "CE 01488 issued by BSI (2797)",  # prose read out of a table cell
    "According to the Attachment",
    "Gemaess Anhang 1",
])
def test_looks_like_ref_rejects_udi_and_prose(value):
    assert t0._looks_like_ref(value) is False


# --------------------------------------------------------------------------- #
# ref_pattern — a playbook's exact REF-code shape, applied AFTER the generic
# filter above, never instead of it (task 5).
# --------------------------------------------------------------------------- #
def test_ref_pattern_rejects_a_code_that_is_not_this_manufacturers_shape():
    """The generic filter accepts anything with a digit and no prose. An
    authored shape turns that into an exact test -- which is what keeps page
    numbers, quantities and order codes out of ref_list."""
    assert t0._looks_like_ref("H1.314.006", r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$")
    assert not t0._looks_like_ref("12", r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$")


def test_ref_pattern_is_a_narrowing_never_a_widening():
    """A value the GENERIC filter rejects stays rejected even if the authored
    pattern would match it: the pattern removes false positives, it does not
    grant entry to prose."""
    assert not t0._looks_like_ref("see attachment 3", r".*3$")


def test_no_pattern_leaves_the_generic_filter_untouched():
    assert t0._looks_like_ref("1800") is t0._looks_like_ref("1800", None)


def test_extract_ref_list_forwards_playbook_ref_pattern_to_the_generic_table_path(monkeypatch):
    """No template selected (the `else` branch of `extract_ref_list`) still
    reaches `_looks_like_ref` -- via `_ref_from_tables` -- so a manufacturer
    with a `ref_pattern` but no T0 layout template still benefits."""
    from app.playbooks import Playbook

    captured = {}

    def fake_ref_from_tables(path, ref_pattern=None):
        captured["ref_pattern"] = ref_pattern
        return (["1055"], 1)

    monkeypatch.setattr(t0, "_ref_from_tables", fake_ref_from_tables)
    pb = Playbook(slug="x", manufacturer="X", ref_pattern=r"^\d{4}$")

    ev = t0.extract_ref_list("dummy.pdf", playbook=pb)

    assert captured["ref_pattern"] == r"^\d{4}$"
    assert ev["value"] == ["1055"]


def test_extract_ref_list_with_no_playbook_passes_no_pattern(monkeypatch):
    """The branch most likely to regress silently: a caller that supplies no
    playbook (the common case, and every call site before this task) must keep
    getting `ref_pattern=None`, not an accidental leftover from another call."""
    captured = {}

    def fake_ref_from_tables(path, ref_pattern=None):
        captured["ref_pattern"] = ref_pattern
        return (["1055"], 1)

    monkeypatch.setattr(t0, "_ref_from_tables", fake_ref_from_tables)

    t0.extract_ref_list("dummy.pdf")

    assert captured["ref_pattern"] is None


# --------------------------------------------------------------------------- #
# labelled inline code lists — the Kiwa Cermet EC-certificate annex (doc 310)

#: Lifted verbatim from doc 310's text layer. The VALUE precedes its LABEL:
#: this is a two-column form and extraction reads the value cell first, so a
#: parser that looks forward from `Codici / Codes:` finds the next device's
#: heading instead of this device's codes.
_ANNEX = [
    "Codice NANDO / NANDO codes:", "MD 0402", "GC TEMP PRINT", "Modello / Model:",
    "901595/10004798, 901596/10004797", "Codici / Codes:",
    "Tipologia / Medical Devices:", "MI Varnish", "Modello / Model:",
    "900746/10003389, 900747/10003390, 900748/10003391, 900749/10003392, "
    "900750/10003393, 901460/10003xxx",
    "Codici / Codes:", "Chief Operating Officer",
]


def test_labelled_code_list_reads_the_line_above_the_label():
    """Doc 310 enumerates the devices it covers with their codes, and nothing
    read them: `extract_ref_list`'s table path finds no table and its REF-header
    path finds no `REF #`. With no ref_list, `derive_coverage_scope` returned
    `manufacturer` and machine binding attached an EC certificate for three
    products to 356 GC items."""
    codes, page = t0._ref_from_labelled_codes(["\n".join(_ANNEX)])
    assert "900748" in codes                      # = item 900748, MI VARNISH MINT 35KOS
    assert "901595" in codes
    assert page == 1


def test_labelled_code_list_splits_a_paired_code():
    """Codes print as `catalogue/internal` pairs. Both halves are emitted --
    which half matches the catalogue is the matching layer's business, not the
    reader's, and guessing here would silently drop the one that does."""
    codes, _ = t0._ref_from_labelled_codes(["901595/10004798\nCodici / Codes:"])
    assert codes == ["901595", "10004798"]


def test_labelled_code_list_refuses_a_placeholder():
    """The annex ends its last pair with `901460/10003xxx` -- a code the issuer
    had not assigned yet. A digits-only rule drops it and keeps its sibling; a
    looser one would put a literal `10003xxx` into the registry as a REF."""
    codes, _ = t0._ref_from_labelled_codes(["901460/10003xxx\nCodici / Codes:"])
    assert codes == ["901460"]


def test_labelled_code_list_ignores_a_label_with_no_codes_above_it():
    """The first device on doc 310's annex, Dental Prescale II, carries a model
    name and no code line at all. A reader that grabs whatever precedes the
    label would harvest the device name as a REF."""
    codes, page = t0._ref_from_labelled_codes(["Dental Prescale II\nCodici / Codes:"])
    assert codes == []
    assert page is None


def test_labelled_codes_run_only_after_the_table_and_header_paths(monkeypatch):
    """Third fallback, not a replacement. Everything that reads a ref list today
    keeps precedence -- this path exists for the layout where both of the others
    come back empty."""
    called = []
    monkeypatch.setattr(t0, "_ref_from_tables",
                        lambda path, ref_pattern=None: (called.append("tables"), (["1055"], 1))[1])
    monkeypatch.setattr(t0, "_ref_from_labelled_codes",
                        lambda texts: (called.append("labelled"), ([], None))[1])

    ev = t0.extract_ref_list("dummy.pdf")

    assert ev["value"] == ["1055"]
    assert called == ["tables"]


# --------------------------------------------------------------------------- #
# REF codes verbatim, no normalization (guardrail G5) — real corpus
# --------------------------------------------------------------------------- #
def test_ref_list_extracted_verbatim(fixture_pdf):
    ev = t0.extract_ref_list(fixture_pdf(REF_DOC))
    assert ev is not None
    assert len(ev["value"]) >= 1
    # codes are strings preserved as-read (no int coercion / zero-stripping)
    assert all(isinstance(code, str) for code in ev["value"])


# --------------------------------------------------------------------------- #
# whole-document orchestrator on the real ISO cert (done-criteria target)
# --------------------------------------------------------------------------- #
def test_t0_extract_iso_cert(fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    doc = pdf.open_doc(path)
    doc_class, fields = t0.t0_extract(doc, filename=path)
    assert doc_class == "compliance-doc"
    assert fields["type"]["value"] == "ISO"
    assert fields["type"]["conf"] == 1.0
    assert fields["coverage_scope"]["value"] == "manufacturer"
    # QMS cert carries no REF list (guardrail G2)
    assert "ref_list" not in fields or not fields["ref_list"]["value"]


# --------------------------------------------------------------------------- #
# coverage_scope — deduced, not guessed
# --------------------------------------------------------------------------- #
# A DoC's scope follows from something T0 already holds: whether the document
# enumerates articles. Stamping that deduction at 0.9 put it under the 0.95
# ladder threshold, so it escalated on every document and a vision guess
# overwrote it — measured on the corpus as 37 of 57 registered documents
# carrying `item`, a value the committed ground truth never once assigns
# (corpus_manifest.py: 20 group, 25 manufacturer, 0 item), including two
# 42-REF GC DoCs the model called `item` and `group` at the same 0.95.

def test_coverage_scope_from_a_ref_list_clears_the_escalation_threshold():
    ev = t0.derive_coverage_scope("DoC", has_ref_list=True, type_conf=1.0)
    assert ev["value"] == "group"
    assert ev["conf"] >= 0.95        # deduced, not worth a vision call


def test_coverage_scope_stays_low_when_the_type_came_from_the_filename():
    # The deduction is only as good as the `type` it keys off. A filename-
    # derived type (FILENAME_CONF) is itself headed for T1/T2, and if it is
    # corrected to EC/ISO the scope must be re-decided as manufacturer, not
    # left pinned at `group` from a premise that no longer holds.
    ev = t0.derive_coverage_scope("DoC", has_ref_list=True, type_conf=t0.FILENAME_CONF)
    assert ev["value"] == "group"
    assert ev["conf"] < 0.95         # still escalates, along with the type


def test_coverage_scope_without_a_ref_list_still_escalates():
    # T0 finding no REF list does NOT mean the document has none: the corpus
    # holds a DoC whose ~125 codes T0 cannot parse. Calling that `manufacturer`
    # confidently would route it to the C4 binding flow and link it to every
    # item of the manufacturer.
    ev = t0.derive_coverage_scope("DoC", has_ref_list=False, type_conf=1.0)
    assert ev["value"] == "manufacturer"
    assert ev["conf"] < 0.95


def test_coverage_scope_certificates_are_unaffected():
    for type_value in ("ISO", "EC"):
        ev = t0.derive_coverage_scope(type_value, has_ref_list=False, type_conf=1.0)
        assert ev["value"] == "manufacturer"
        assert ev["conf"] == 1.0


def test_t0_never_deduces_item_scope():
    # `item` has no deterministic producer and the ground truth never uses it.
    values = {
        t0.derive_coverage_scope(t, has_ref_list=r, type_conf=c)["value"]
        for t in ("DoC", "ISO", "EC")
        for r in (True, False)
        for c in (1.0, t0.FILENAME_CONF)
    }
    assert "item" not in values


# --------------------------------------------------------------------------- #
# T0 manufacturer: the document's own legal entity, matched against playbooks.
#
# The deterministic half of ext-manufacturer Phase 2. Matches the LEGAL NAME a
# playbook declares, never `match.anchors` -- those are document-management
# artifacts and nomenclature strings (`otcs`, `umdns code`) that would resolve a
# Straumann DoC to KOMET -- and never the folder name, which is the supplier and
# not the manufacturer (the DENSTPLY corpus folder holds Maillefer, Sirona and
# VDW documents, BC codes 022, 012 and 010).
# --------------------------------------------------------------------------- #

def _pb(manufacturer, aliases=()):
    from app.playbooks import Playbook
    return Playbook(slug=manufacturer.lower().replace(" ", "-"),
                    manufacturer=manufacturer, aliases=tuple(aliases))


def test_manufacturer_matches_a_playbook_legal_name():
    pbs = (_pb("GC EUROPE N.V."), _pb("Ivoclar Vivadent AG"))
    ev = t0.extract_manufacturer(
        "DECLARATION OF CONFORMITY\nGC EUROPE N.V.\nResearchpark Haasrode", pbs)
    assert ev["value"] == "GC EUROPE N.V."
    assert ev["tier"] == "T0"


def test_manufacturer_match_folds_case_and_whitespace():
    pbs = (_pb("GC EUROPE N.V."),)
    ev = t0.extract_manufacturer("issued by gc  europe   n.v. on request", pbs)
    assert ev["value"] == "GC EUROPE N.V."


def test_a_short_alias_never_matches():
    """GC's alias is the bare string `GC`. Two characters match inside `GC`,
    `GCF`, `MAGCARE` and any table header, so a short alias is a false-positive
    generator, not identity."""
    pbs = (_pb("GC EUROPE N.V.", aliases=("GC",)),)
    assert t0.extract_manufacturer("REF GC-1 spare part list", pbs) is None


def test_a_long_alias_still_matches():
    pbs = (_pb("KOMET", aliases=("Gebr. Brasseler GmbH & Co. KG",)),)
    ev = t0.extract_manufacturer("Hersteller: Gebr. Brasseler GmbH & Co. KG", pbs)
    assert ev["value"] == "KOMET"      # the canonical name, not the alias read


def test_two_manufacturers_in_one_document_is_not_identity():
    """A distributor declaration naming several companies, or a document citing
    another manufacturer's cert. Ambiguous is not a value -- abstain and let a
    tier that can read the layout decide."""
    pbs = (_pb("GC EUROPE N.V."), _pb("Ivoclar Vivadent AG"))
    text = "GC EUROPE N.V. and Ivoclar Vivadent AG jointly declare"
    assert t0.extract_manufacturer(text, pbs) is None


@pytest.mark.parametrize("label", [
    "Authorised Representative:",
    "Authorized Representative:",
    "authorized eu-representative name of the site:",   # the real corpus spelling
    "Authorised UK Representative",
    "EC REP",
])
def test_a_name_only_inside_a_representative_block_is_not_trusted(label):
    # Measured over all 1.303 readable corpus PDFs (2026-08-17): `Dentsply IH
    # Limited` appears in 41 files and is the post-Brexit UK representative in
    # every one, with no real DENTSPLY entity present -- yet the identity match
    # fires on all 41. Promoting those without a second read is the failure this
    # guard exists to prevent.
    pbs = (_pb("DENTSPLY"),)
    ev = t0.extract_manufacturer(f"Declaration\n{label} DENTSPLY DeTrey GmbH", pbs)
    assert ev["value"] == "DENTSPLY"
    assert ev["conf"] == t0.MFR_REP_ONLY_CONF      # escalates, does not self-answer
    assert ev["conf"] < 0.95


def test_a_name_printed_outside_a_representative_block_is_trusted():
    pbs = (_pb("GC EUROPE N.V."),)
    ev = t0.extract_manufacturer(
        "DECLARATION OF CONFORMITY\nGC EUROPE N.V.\nResearchpark Haasrode", pbs)
    assert ev["conf"] == t0.MFR_TRUSTED_CONF
    assert ev["conf"] >= 0.95                      # T0 answers alone


def test_one_occurrence_outside_the_block_is_enough():
    # The ordinary layout: manufacturer in the header, representative in the
    # footer. Penalising a document for carrying both would hold back most of
    # the corpus, so a single occurrence away from the label decides it.
    pbs = (_pb("Institut Straumann AG"),)
    ev = t0.extract_manufacturer(
        "Institut Straumann AG, Basel\n...\n"
        "Authorised Representative: Institut Straumann AG UK Branch", pbs)
    assert ev["conf"] == t0.MFR_TRUSTED_CONF


def test_no_playbook_match_abstains():
    assert t0.extract_manufacturer("Some Unknown Company Ltd", (_pb("GC EUROPE N.V."),)) is None


def test_manufacturer_confidence_decides_whether_the_field_escalates():
    """Same contract this test always asserted, now split by WHERE the name sits.

    It used to assert a flat sub-threshold confidence, on the argument that a
    name on the page could be the authorised representative -- which the prompts
    bar by role, never by company name. That argument survives measurement for
    exactly the case it describes, and fails for every other: T0 agreed with the
    paid tiers on 284 of 285 registry documents, so the flat constant was
    escalating the whole corpus to re-derive an answer it already had.

    Both halves are asserted through `needs_escalation`, the thing the
    confidence actually controls -- not against a bare number."""
    from app.extract import tiers
    pbs = (_pb("GC EUROPE N.V."),)

    plain = t0.extract_manufacturer("GC EUROPE N.V.", pbs)
    assert plain["conf"] >= 0.95
    assert "manufacturer" not in tiers.needs_escalation({"manufacturer": plain})

    rep_only = t0.extract_manufacturer(
        "Authorised Representative: GC EUROPE N.V.", pbs)
    assert rep_only["conf"] < 0.95
    assert "manufacturer" in tiers.needs_escalation({"manufacturer": rep_only})


def test_every_target_field_is_produced_by_t0_or_named_exempt():
    """Tier/field parity as a contract (ext-manufacturer plan). T0 produced 8 of
    9 and silently skipped `referenced_docs`; nothing said so, and nothing
    stopped a tenth field being half-wired. A field must be either produced here
    or listed as exempt WITH its reason, so the gap is a decision on the record
    rather than an omission nobody notices."""
    from app.extract import tiers
    target, produced, exempt = set(tiers.TARGET), set(t0.T0_PRODUCED), set(t0.T0_EXEMPT)
    assert produced & exempt == set(), f"a field cannot be both: {produced & exempt}"
    assert produced | exempt == target, (
        f"missing a decision for {target - (produced | exempt)}; "
        f"declared but not a target: {(produced | exempt) - target}")


def test_t0_actually_produces_what_it_claims(fixture_pdf):
    """The set above is a declaration; this checks it against reality on a real
    document, so a field can't be listed as produced and never wired."""
    from app.extract import pdf as pdfutil
    path = fixture_pdf("GC/everX_Posterior_12022026.pdf")
    # The full PATH, not a basename: `filename` is dual-purpose ([extract-t0]),
    # and a bare basename silently disables REF extraction because
    # extract_ref_list re-opens it with pdfplumber.
    doc = pdfutil.open_doc(path)
    _, fields = t0.t0_extract(doc, str(path))
    for field in ("type", "regulation", "manufacturer", "coverage_scope", "ref_list"):
        assert field in fields, f"{field} is declared produced but T0 returned nothing"


# --------------------------------------------------------------------------- #
# steering_playbook: which playbook's lexicons and hints apply to this doc
# --------------------------------------------------------------------------- #
def test_steering_playbook_is_the_one_the_document_names():
    from app import playbooks as pb_mod
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH", aliases=("VOCO",))
    komet = Playbook(slug="komet", manufacturer="KOMET")

    got = t0.steering_playbook("Declaration by VOCO GmbH, Cuxhaven", (voco, komet))

    assert got.slug == "voco"


def test_steering_playbook_is_none_when_the_document_names_nobody():
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH")

    assert t0.steering_playbook("Declaration by SOMEONE ELSE", (voco,)) is None


def test_steering_playbook_prefers_an_authoritative_override():
    """A group's canonical_manufacturer is established before extraction; T0's
    own text match is a guess. When the caller has the former, it wins."""
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH")
    komet = Playbook(slug="komet", manufacturer="KOMET")

    got = t0.steering_playbook("Declaration by VOCO GmbH", (voco, komet), override=komet)

    assert got.slug == "komet"


def test_t0_extract_is_unchanged_by_the_reorder(fixture_pdf):
    """Pure refactor guard over a committed fixture (never skips, unlike
    `corpus_pdf`). Values below were measured from the pre-reorder code (task 2
    baseline capture) -- this test pins today's behaviour, not an assumption
    about it."""
    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    with pdf.open_doc(path) as doc:
        doc_class, fields = t0.t0_extract(doc, path)

    assert doc_class == "compliance-doc"
    assert fields["type"]["value"] == "DoC"
    assert fields["manufacturer"]["value"] == "GC EUROPE N.V."


# --------------------------------------------------------------------------- #
# a playbook template that stops matching is a regression, not a shrug
# --------------------------------------------------------------------------- #
# When a manufacturer has a playbook with a REF template and T0 still extracts
# nothing, that is OUR parser breaking -- today indistinguishable from "this
# document lists no articles". It falls through to the LLM, which costs money
# and returns a capped sample instead of an enumeration, and nothing counts it.
GC_DOC = "GC/everX_Posterior_12022026.pdf"    # matches playbooks/gc.json (stitched-table)


def _t0_with_result(path, monkeypatch=None):
    from app.results import Result

    doc = pdf.open_doc(path)
    r = Result()
    _, fields = t0.t0_extract(doc, filename=str(path), result=r)
    return fields, r.as_dict()["anomalies"]


def test_a_template_that_stops_matching_is_reported_as_an_anomaly(fixture_pdf, monkeypatch):
    from app.extract import t0_layout

    path = fixture_pdf(GC_DOC)
    # the template still MATCHES the document; its parser returns nothing --
    # exactly what a layout change on the manufacturer's side looks like.
    monkeypatch.setattr(t0_layout, "ref_from_stitched_tables", lambda p, ref_pattern=None: ([], None))

    fields, anomalies = _t0_with_result(path)

    assert not fields.get("ref_list")
    assert [a["kind"] for a in anomalies] == ["t0_ref_template_miss"]
    assert anomalies[0]["subject"] == "gc"          # the playbook to go and fix
    assert anomalies[0]["detail"]["ref_strategy"] == "stitched-table"


def test_a_working_template_reports_nothing(fixture_pdf):
    path = fixture_pdf(GC_DOC)
    fields, anomalies = _t0_with_result(path)
    assert fields["ref_list"]["value"]              # the template really works
    assert anomalies == []


def test_no_template_means_no_template_miss(fixture_pdf, monkeypatch):
    """The anomaly says "OUR template broke". A manufacturer we have no template
    for yielding no REF list is the ordinary case, not a regression."""
    from app.extract import t0_layout

    path = fixture_pdf(GC_DOC)
    monkeypatch.setattr(t0_layout, "match", lambda text, templates=None: None)
    monkeypatch.setattr(t0, "extract_ref_list", lambda path, template=None, playbook=None: None)

    fields, anomalies = _t0_with_result(path)

    assert not fields.get("ref_list")
    assert anomalies == []


def test_the_anomaly_is_a_count_never_an_exception(fixture_pdf, monkeypatch):
    """No Result to report into is not a reason to fail an extraction: T0 is
    used by tools and tests that carry no envelope."""
    from app.extract import t0_layout

    path = fixture_pdf(GC_DOC)
    monkeypatch.setattr(t0_layout, "ref_from_stitched_tables", lambda p, ref_pattern=None: ([], None))
    doc = pdf.open_doc(path)
    doc_class, fields = t0.t0_extract(doc, filename=str(path))     # no result=
    assert doc_class == "compliance-doc"
    assert fields["type"]["value"] == "DoC"


# --------------------------------------------------------------------------- #
# place-and-date signature line -> validity_from (Denis's ruling 2026-08-13)
# --------------------------------------------------------------------------- #
def test_a_place_and_date_line_becomes_validity_from():
    """GC signs its declarations "Leuven, 12/02/2026" with no label at all, so
    neither _FROM_LABELS nor _TO_LABELS matched and the field went to the LLM,
    which stored it as an EXPIRY -- documents 160/227/239 read as expired.
    A declaration takes effect when it is issued, so the signing date is its
    start."""
    ev = t0.extract_dates("Leuven, 12/02/2026\nName: J. Janssens")
    assert ev.get("validity_from", {}).get("value") == "2026-02-12"
    assert "validity_to" not in ev


def test_a_labelled_expiry_still_wins_over_a_signature_line():
    """The signature line must never displace a real stated expiry, and must not
    be read as one either."""
    ev = t0.extract_dates("Expiry Date: 2027-05-22\nLeuven, 12/02/2026")
    assert ev["validity_to"]["value"] == "2027-05-22"
    assert ev.get("validity_from", {}).get("value") == "2026-02-12"


def test_a_labelled_issue_date_beats_a_bare_signature_line():
    """An explicit label is stronger evidence than a positional guess."""
    ev = t0.extract_dates("Date of issue: 2025-01-09\nLeuven, 12/02/2026")
    assert ev["validity_from"]["value"] == "2025-01-09"


def test_a_bare_date_with_no_place_is_not_a_signature_line():
    """The guard against turning every date in the document into a start date:
    the pattern needs a place name and a comma, which is what a signature block
    looks like ("Leuven, 12/02/2026", "Ljubljana, 3.4.2025")."""
    assert t0.extract_dates("Printed 12/02/2026 by the QA system") == {}
    assert t0.extract_dates("Lot 4471 12/02/2026") == {}


@pytest.mark.parametrize("line", [
    "Leuven, …10/10/2022…",                                  # the commonest form
    "Leuven,…10/10/2022…",                                   # no space after the comma
    "Leuven,  …10/10/2022…",                                 # two spaces
    "Leuven, ….10/10/2022…",                                 # ellipsis then a period
    "Leuven, .....10/10/2022..",                             # plain periods, both sides
    "Leuven, …10/10/2022.....",                              # ellipsis in, periods out
    "Leuven, 12/02/2026……………",                               # trailing rule only
    "Leuven, 10/10/2022                    ..........",      # dots after wide padding
    "Leuven, ___10/10/2022___",                              # an underscore rule
])
def test_a_dotted_fill_in_rule_does_not_hide_the_signature_date(line):
    """The signature line is a form field, not prose, and the blank is drawn.

    GC's declarations print "Leuven, ……" with the date typed into the dots, so
    the ellipsis and the trailing rule sit between the comma and the date the
    original pattern demanded be adjacent. That is not an edge case in this
    corpus: it is 41 of GC's 149 documents, every one of them a Class I DoC,
    and it took the brand's T0 date hit-rate from 16/149 to 57/149 (measured
    over the real corpus, 2026-08-14). Both the 10/10/2022 and the 12/02/2026
    batches were affected, the second being the signing date whose misfiling as
    an EXPIRY started this whole thread."""
    ev = t0.extract_dates(line + "\n                    Date")
    assert ev.get("validity_from", {}).get("value") in ("2022-10-10", "2026-02-12")
    assert "validity_to" not in ev


@pytest.mark.parametrize("line", [
    "Leuven, ... and more text 10/10/2022",   # words between the rule and the date
    "Leuven, 10/10/2022 signed by M. Minale",  # prose continues after the date
    "See annex, 10/10/2022 for the list",
])
def test_the_fill_in_rule_does_not_loosen_the_pattern_into_prose(line):
    """Widening what may sit around the date must not widen it to ANY text, or
    a table row carrying a city and a date becomes a validity date."""
    assert "validity_from" not in t0.extract_dates(line)


# --------------------------------------------------------------------------- #
# companion annex: one manufacturer's document split across two files
# --------------------------------------------------------------------------- #
def _komet_pdf(path, rows: list[str]):
    """A minimal Komet-shaped list page: the playbook's match anchors, the
    text-column header its `ref_strategy_config` looks for, then `rows`."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    lines = ["UMDNS Code GMDN-Code REF / Product",
             "REF / Product Description UMDNS Code"] + rows
    page.insert_text((20, 40), "\n".join(lines), fontsize=9)
    doc.save(str(path))
    doc.close()


def _declaration(path, rows: list[str] | None = None):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    lines = ["Declaration of Conformity",
             "Regulation (EU) 2017/745",
             "Gebr. Brasseler GmbH & Co. KG"]
    if rows:
        lines += ["REF / Product Description UMDNS Code"] + rows
    page.insert_text((20, 40), "\n".join(lines), fontsize=9)
    doc.save(str(path))
    doc.close()


def test_a_declaration_with_no_list_takes_its_annexs_and_says_so(tmp_path):
    """Komet's declaration and its product list are two files. Without the
    pairing the declaration carries no item identifier at all -- 81 of its 102
    declarations, every one of them unlinkable and bound for the manual queue."""
    primary = tmp_path / "532624_RA_810_DoC_EU_SIGNED.pdf"
    annex = tmp_path / "532624_RA_812_Liste_DoC.pdf"
    _declaration(primary)
    _komet_pdf(annex, ["004081K3 K3 368.204.023 DIAM 16669", "001627 1.204.005 STEEL BUR 16669"])

    doc = pdf.open_doc(str(primary))
    try:
        _, fields = t0.t0_extract(doc, filename=str(primary), companion_url=str(annex))
    finally:
        doc.close()

    ev = fields["ref_list"]
    assert ev["value"] == ["368.204.023", "1.204.005"]
    assert ev["tier"] == "T0"
    # invariant 2: the handle is where the value was READ, which is the annex --
    # sending a reviewer to the declaration would show them a page with no list.
    assert ev["archive_url"] == str(annex)
    assert "companion annex" in ev["verbatim"]


def test_ref_pattern_narrows_the_companion_annexs_ref_list(tmp_path):
    """The branch most likely to hide a defect: the annex is read through
    `_ref_from_companion`, a second call site `extract_ref_list` has, and a
    playbook's `ref_pattern` must reach it exactly as it reaches the primary
    document's own extraction -- not just be silently dropped on the way.

    Same fixture as `test_a_declaration_with_no_list_takes_its_annexs_and_says_so`,
    whose baseline is both codes unnarrowed (`["368.204.023", "1.204.005"]`).
    Here the steering playbook carries a narrower shape than komet.json's own
    `field_pattern` -- only a first segment starting with "3" -- so "1.204.005"
    is dropped and "368.204.023" survives."""
    from app.playbooks import Playbook

    primary = tmp_path / "532624_RA_810_DoC_EU_SIGNED.pdf"
    annex = tmp_path / "532624_RA_812_Liste_DoC.pdf"
    _declaration(primary)
    _komet_pdf(annex, ["004081K3 K3 368.204.023 DIAM 16669", "001627 1.204.005 STEEL BUR 16669"])

    # Constructed here, never authored into a real playbook file (task
    # constraint) -- passed as `t0_extract`'s authoritative override, exactly
    # how a group's canonical manufacturer would arrive in production.
    steering = Playbook(slug="test-komet", manufacturer="Test Komet AG",
                        ref_pattern=r"^3\d\d\.\d{3}\.\d{3}$")

    doc = pdf.open_doc(str(primary))
    try:
        _, fields = t0.t0_extract(doc, filename=str(primary),
                                  companion_url=str(annex), playbook=steering)
    finally:
        doc.close()

    ev = fields["ref_list"]
    assert ev["value"] == ["368.204.023"]
    assert ev["archive_url"] == str(annex)


def test_a_declaration_that_prints_its_own_list_keeps_it(tmp_path):
    """21 of Komet's 102 declarations print a complete list inline. An annex
    must never overwrite a reading the document itself supports."""
    primary = tmp_path / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf"
    annex = tmp_path / "532797_RA_812_Liste_DoC.pdf"
    _declaration(primary, ["004081K3 K3 368.204.023 DIAM 16669"])
    _komet_pdf(annex, ["009999K9 K9 999.204.999 OTHER 16669"])

    doc = pdf.open_doc(str(primary))
    try:
        _, fields = t0.t0_extract(doc, filename=str(primary), companion_url=str(annex))
    finally:
        doc.close()

    ev = fields["ref_list"]
    assert ev["value"] == ["368.204.023"]
    assert "archive_url" not in ev          # read from the document itself
    assert "999.204.999" not in ev["value"]


def test_an_unreadable_annex_leaves_the_declaration_as_it_was(tmp_path):
    """A missing or corrupt annex is a coverage gap, never a failed extraction:
    the declaration is still a real document and the rest of its fields are
    still worth having."""
    primary = tmp_path / "532624_RA_810_DoC_EU_SIGNED.pdf"
    _declaration(primary)

    doc = pdf.open_doc(str(primary))
    try:
        _, fields = t0.t0_extract(doc, filename=str(primary),
                                  companion_url=str(tmp_path / "gone.pdf"))
    finally:
        doc.close()

    assert "ref_list" not in fields
    assert fields["regulation"]["value"] == "MDR"


def test_komets_authored_date_labels_read_the_issue_date_t0_used_to_miss(fixture_pdf):
    """The first playbook to author `date_labels`, and the measurement that
    justified it: Komet prints the issue date as "Rev. Stand: 02.03.2026" or
    "Revisionsstand 26.05.2024", neither of which is in the global EN/DE/SL
    list, so 155 of its 156 readable declarations escalated to a paid tier to
    read a date sitting in the text layer. With the labels authored, T0 reads
    106 of them. This pins the fixture end of that measurement."""
    from app import playbooks as playbooks_mod

    text = pdf.full_text(pdf.open_doc(
        fixture_pdf("KOMET/532862_RA_810_DoC_EU_MDR_SIGNED.pdf")))
    komet = playbooks_mod.for_manufacturer("KOMET")

    assert komet is not None and komet.date_labels["from"]
    assert t0.extract_dates(text).get("validity_from") is None
    assert t0.extract_dates(text, komet)["validity_from"]["value"] == "2026-03-02"


def test_an_authored_date_label_never_overrides_one_the_global_list_reads():
    """Merge, never replace (design rule): globals are tried first, so an
    authored label can add a read but can never change one that already worked."""
    from app.playbooks import Playbook

    text = "Issue date: 01.02.2020\nRev. Stand: 09.09.2024"
    pb = Playbook(slug="komet", manufacturer="KOMET",
                  date_labels={"from": ["rev. stand"]})

    assert t0.extract_dates(text, pb)["validity_from"]["value"] == "2020-02-01"


def test_playbook_type_marker_reads_a_type_the_global_list_misses():
    """The fourth T0 lexicon, same additive contract as date_labels: a
    manufacturer that titles its declaration in a language the global list does
    not carry can add the phrase rather than pay a tier to read a title."""
    from app.playbooks import Playbook

    text = "PROHLÁŠENÍ O SHODĚ\nnejaky text"
    pb = Playbook(slug="x", manufacturer="X",
                  type_markers={"DoC": ["prohlášení o shodě"]})

    assert t0.extract_type(text) is None
    assert t0.extract_type(text, "", pb)["value"] == "DoC"


def test_a_global_type_marker_wins_a_tie_with_an_authored_one():
    """Merge, never replace: globals are tried first, so an authored marker can
    add a read but never change one the global list already made."""
    from app.playbooks import Playbook

    text = "Declaration of conformity for the product"
    pb = Playbook(slug="x", manufacturer="X",
                  type_markers={"IFU": ["declaration of conformity"]})

    assert t0.extract_type(text, "", pb)["value"] == "DoC"


# --------------------------------------------------------------------------- #
# stated_class: the MDR risk class the document itself prints
# --------------------------------------------------------------------------- #
# Every string below is verbatim from the dev corpus (doc ids noted). The field
# is T0-only by design -- it sits in no `_ESCALATE_*` set -- so what T0 declines
# here is simply not captured, never chased with a paid tier. That is why the
# abstain cases carry more weight than the hits: the field exists to CROSS-CHECK
# what BC already says about an item, and a confidently wrong class would
# manufacture a conflict for a human to chase down.
def test_stated_class_reads_the_voco_label():             # docs 600/694
    ev = t0.extract_stated_class("Item Number: see annex Class: IIa Rule: 7 Name and address")
    assert ev["value"] == "IIa" and ev["tier"] == "T0" and ev["conf"] == 0.95
    assert "Class: IIa" in ev["verbatim"]


def test_stated_class_reads_the_annex_viii_header():      # docs 229/272/115
    ev = t0.extract_stated_class("EU Risk Class (MDR Annex VIII) Class IIa Conformity Assessment")
    assert ev["value"] == "IIa"


def test_stated_class_reads_the_bilingual_subclass():     # docs 349/394
    ev = t0.extract_stated_class("Klasse/ Class: Is Regel/ Rule: 6 Basic UDI-DI: ++E226")
    assert ev["value"] == "Is"


def test_a_multi_class_boilerplate_abstains():            # docs 519/522
    """A pre-printed form naming every class at once says nothing about THIS
    document. The tell is the enumeration itself: class tokens separated by
    slashes, OCR'd `llb`/`lll` included."""
    assert t0.extract_stated_class(
        "EU Declaration of Conformity Class Is/Ir/IIa/llb/lll EU Konformitaetserklaerung") is None


def test_a_regulation_quoting_text_abstains():            # doc 260 (ISO cert quoting the reg)
    """Prose quoting the regulation names classes of devices in general, never
    this document's. The tell is plural "devices" right after the token
    (`_CLASS_DEVICES_TAIL`), so both mentions here are suppressed and the
    document has nothing usable to say."""
    assert t0.extract_stated_class(
        "placing on the market of Class III devices, and Class IIb implantable devices") is None


def test_conditional_boilerplate_no_longer_reads_as_a_class():   # doc 389
    """The resolved KNOWN LIMIT (Denis ruling 2026-08-24): one class in a
    conditional clause about somebody else's device used to survive the
    multi-class guard because it was the only distinct class on the page.
    TUEV SUED prints this sentence on every Annex II cert."""
    txt = ("For marketing of class III devices an additional "
           "Annex II (4) certificate is mandatory.")
    assert t0.extract_stated_class(txt) is None
    assert t0.mentions_class(txt) is True    # suppressed, not silent


def test_transition_prose_no_longer_reads_as_a_class():          # doc 791
    """Same family, different sentence: a DoC narrating the MDD->MDR
    transition names "Class I medical devices" as a population, not itself."""
    assert t0.extract_stated_class(
        "Class I medical devices were already converted to MDR on May 26, 2021.") is None


def test_classified_as_rescues_a_plural_self_statement():        # doc 418
    """The one measured counter-shape: a genuine self-statement PHRASED
    plural. "classified as" immediately before the token is what separates it
    from the regulation-quoting prose above."""
    ev = t0.extract_stated_class(
        "Being classified as Class IIa devices and Rule 8 in accordance with Annex VIII")
    assert ev["value"] == "IIa"


def test_suppression_unstarves_a_genuine_schedule():             # docs 248/439
    """A QMS certificate quoting Article 52's "class III devices or class IIb
    implantable devices" used to abstain even though its own device schedule
    is uniformly IIa -- the quoted classes polluted the distinct-class count.
    With the quote suppressed, the schedule's own statement survives."""
    ev = t0.extract_stated_class(
        "If class III devices or class IIb implantable devices referred to in "
        "the second subparagraph are concerned ... "
        "Products: Product of Class IIa: Q010104 - DENTAL PROCEDURE DEVICES")
    assert ev["value"] == "IIa"


def test_no_class_statement_returns_none():
    assert t0.extract_stated_class("Declaration of Conformity for dental restoratives") is None


def test_a_class_token_split_by_a_space_is_still_that_class():   # doc 310
    """Kiwa Cermet prints `II a` and `I m`, not `IIa` and `Im`. The space is
    typography, not a different class, and until 2026-08-24 it made the token
    invisible: `IIa` occurred zero times in doc 310's text while `II a`
    occurred twice."""
    assert t0.extract_stated_class("Classe di rischio / Risk class:\nII a")["value"] == "IIa"
    assert t0.extract_stated_class("Risk class:\nI m - restricted to the "
                                   "metrological requirements")["value"] == "Im"


def test_the_verbatim_keeps_the_space_the_document_printed():
    """Normalisation belongs to the VALUE. Invariant 2's verbatim is what the
    page says, so a reviewer comparing evidence to the PDF finds the same
    characters -- rewriting `II a` to `IIa` there would falsify the quote."""
    ev = t0.extract_stated_class("Risk class:\nII a")
    assert ev["value"] == "IIa"
    assert "II a" in ev["verbatim"]


def test_a_spaced_multi_class_annex_abstains():                  # doc 310, the whole point
    """The abstain guard was never wrong; it was starved. Doc 310's technical
    annex states one class per covered device -- `I m`, `II a`, `II a` -- and
    the parser saw only a bare `I`, so `len(seen) == 1` and a certificate
    covering three device types wrote `I` at conf 0.95. Machine binding then
    fanned that across 356 GC items and every one of the 314 conflicts on
    /data-quality came from it."""
    annex = ("Tipologia / Medical Devices: Device for dental occlusal force test\n"
             "Classe di rischio / Risk class:\nI m - restricted to the metrological requirements\n"
             "Tipologia / Medical Devices: 3D Printable light curing composite\n"
             "Classe di rischio / Risk class:\nII a\n"
             "Tipologia / Medical Devices: Dental varnish topical application\n"
             "Classe di rischio / Risk class:\nII a\n")
    assert t0.extract_stated_class(annex) is None
    assert t0.mentions_class(annex) is True


def test_a_bare_II_is_not_a_class_however_it_is_spaced():
    """`II` alone is not an Annex VIII class. Widening the token to tolerate a
    space must not invent one -- nor may `Class I annex` become `Class Ia`, or
    `Class I sterile` become `Is`, both of which the word boundary refuses."""
    assert t0.extract_stated_class("Risk class: II") is None
    assert t0.extract_stated_class("Class I annex 3 applies")["value"] == "I"
    assert t0.extract_stated_class("Class I sterile products")["value"] == "I"


def test_repeated_same_class_is_not_an_abstain():         # multi-product DoC, one class throughout
    """A declaration covering forty articles repeats one class forty times.
    Abstention is about DISTINCT classes, never about how often one is named."""
    ev = t0.extract_stated_class("Class: I Rule: 5 ... Class I, Rule 5 Article List")
    assert ev["value"] == "I"


def test_a_stated_class_carries_pageless_t0_evidence():
    """[evidence-page] ruling 2026-07-31: T0 evidence is legitimately pageless.
    The reader is the whole document's text, so there is no honest page to name
    and inventing one would be worse than None."""
    ev = t0.extract_stated_class("Class: IIb Rule: 8")
    assert ev["page"] is None
    assert set(ev) == {"value", "conf", "tier", "verbatim", "page"}


def test_mentions_class_separates_silence_from_an_abstain():
    """Two different outcomes hide behind one `None` return, and the history
    backfill has to count them apart (CLAUDE.md: nothing skipped is silent). A
    document that never says the word is not the same as one that says it about
    five classes at once -- the first is a document with nothing to give, the
    second is a parser declining."""
    assert t0.mentions_class("Class Is/Ir/IIa/llb/lll") is True
    assert t0.mentions_class("Class III devices, and Class IIb implantable devices") is True
    assert t0.mentions_class("Declaration of Conformity for dental restoratives") is False


def _class_declaration(path, class_line: str):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((20, 40), "\n".join(
        ["Declaration of Conformity", "Regulation (EU) 2017/745", class_line]), fontsize=9)
    doc.save(str(path))
    doc.close()


def test_t0_extract_puts_the_stated_class_in_fields(tmp_path):
    """Wired into the orchestrator, not merely callable on its own -- the
    half-wired `referenced_docs` is the precedent this guards against."""
    path = tmp_path / "doc.pdf"
    _class_declaration(path, "Class: IIa Rule: 7")
    doc = pdf.open_doc(str(path))
    try:
        _, fields = t0.t0_extract(doc, str(path))
    finally:
        doc.close()
    assert fields["stated_class"]["value"] == "IIa"
    assert fields["stated_class"]["tier"] == "T0"


def test_filename_fallbacks_read_the_basename_not_the_directories():
    """The fallback is a guess off the document's OWN name, so only the last
    path segment may speak.

    `filename` here is `payload["archive_url"]`, and `archiving.archive_path`
    builds that as `{manufacturer}/{doc|ifu|iso}/{hash}__{name}` -- so EVERY
    archived path carries a type marker as a directory, and matching the whole
    string let the shelf decide the document. Measured 2026-08-31: 505 of 769
    live archive_urls contain one. It had never fired in production (843 `type`
    evidence rows, none from a filename), because a document with text hits a
    real marker first -- but a SCAN falls straight through to here, and the
    corpus carries exactly such a file.

    Found when a git worktree named `document-manufacturer-binding` typed three
    corpus certificates as `DoC` off its own directory name.
    """
    # Directory says DoC, basename says certificate: the basename wins.
    ev = t0.extract_type("body has no type markers",
                         "/srv/VOCO/doc/abc123def456__EC-Certificate-991.pdf")
    assert ev["value"] == "EC"

    # A directory marker with a basename carrying none decides nothing.
    assert t0.extract_type("body has no type markers",
                           "/srv/VOCO/doc/abc123def456__795695-Brazil-EN.pdf") is None

    # Same rule for the regulation fallback, which reads the path identically.
    assert t0.extract_regulation("no legal reference in body",
                                 "/srv/mdr-archive/iso/aaa__scope-only.pdf") is None
    assert t0.extract_regulation("no legal reference in body",
                                 "/srv/mdr-archive/iso/aaa__cert (MDR).pdf")["value"] == "MDR"


# --- doc_class must be earned on page 1 -------------------------------------
#
# `classify_doc_class` reads PAGE-1 text, but until 2026-09-02 a document that
# fell to `unknown` there was promoted to `compliance-doc` whenever
# `extract_type` (which reads the FULL text) found any marker anywhere. That
# promotion is how four web pages became documents: Solventum's Resources
# landing page has no marker on page 1 and the phrase "instructions for use" at
# character 19.160, inside the sentence "Access essential healthcare
# information, including instructions for use, regulatory details".
#
# Measured 2026-09-02 over 1.261 real archived PDFs and 13 PDFs the ranker chose
# off the open web:
#
#                       real docs        web junk
#   marker on page 1    1074  (85%)       1  (8%)   <- the one genuine DoC
#   deeper only           48  ( 4%)       6 (46%)   <- 320p manual, 45p archive
#   none                 139  (11%)       6
#
# So the rule is position, not corroboration. Corroboration was tried first and
# failed: a 320-page NSK operation manual cites MDR 2017/745 legitimately, so
# "names a regulation" cannot separate a manual from a declaration.

def test_a_marker_only_deep_in_the_text_no_longer_promotes_to_compliance_doc():
    """The Solventum shape, reduced: nothing on page 1, the phrase far later."""
    first = "Resources\nSolventum\nProducts for professionals and consumers\n"
    full = first + ("filler paragraph. " * 400) + \
        "Access essential healthcare information, including instructions for use."

    assert t0.classify_doc_class(first) == "unknown"
    assert t0.promote_doc_class("unknown", first, full) == "unknown"


def test_a_marker_in_the_header_window_still_classifies():
    header = "EU Declaration of Conformity\nIvoclar Vivadent AG\n"
    assert t0.promote_doc_class("unknown", header, header + " more") == "compliance-doc"


def test_a_cover_page_certificate_classifies_from_page_two():
    """A TUV SUD ISO 13485 certificate: page 1 is the holder and scope, and the
    standard is named on page 2. Real fixture shape, and why the window is 2."""
    header = ("Certificate\nNo. Q5 043306 0209\nHolder of Certificate:\n"
              "Ivoclar Vivadent AG\nScope of Certificate:\n"
              "\nEN ISO 13485:2016 requirements\n")
    assert t0.promote_doc_class("unknown", header, header) == "compliance-doc"


def test_a_regulation_cited_on_page_one_still_classifies():
    """A declaration that names only the regulation, no type phrase — the case
    the old promotion existed to catch. It is kept, but page-1 scoped."""
    first = "Regulation (EU) 2017/745\nWe declare under our sole responsibility\n"
    assert t0.promote_doc_class("unknown", first, first) == "compliance-doc"


def test_a_regulation_cited_only_deep_in_a_manual_does_not_classify():
    """The 320-page NSK operation manual: cites MDR, legitimately, on page 200."""
    first = "Operation Manual\nSurgic Pro2\n"
    full = first + ("manual text. " * 2000) + "Regulation (EU) 2017/745"
    assert t0.promote_doc_class("unknown", first, full) == "unknown"


def test_business_and_msds_short_circuits_are_untouched():
    for first, expected in (
        ("Safety Data Sheet\nSection 1", "msds"),
        ("Invoice No. 4711\nPurchase Order", "business-doc"),
    ):
        assert t0.classify_doc_class(first) == expected
        assert t0.promote_doc_class(expected, first, first) == expected


# --------------------------------------------------------------------------- #
# Label/value pairing must not cross a column boundary
#
# `extract_dates` used to take the first date within `_LABEL_WINDOW` of a label
# and pair them at confidence 1.0, synthesising the verbatim as
# `label + " " + date`. In a two-column certificate the label and its value are
# not adjacent in PyMuPDF's linear text, so it paired a label with ANOTHER
# field's value -- and the manufactured verbatim made the wrong value look
# quoted. Measured 2026-09-03 over 184 registry documents: 4 wrong values, all
# of this shape, the worst off by four and a half years.
#
# Design: docs/superpowers/specs/2026-09-03-label-adjacency-and-type-conflation-design.md
# --------------------------------------------------------------------------- #

#: The real DQS certificate text that exposed this (doc 966, Edenta), reduced to
#: the shape that matters: FOUR labels, then the values column. The first date
#: after "Expiry date" is the EFFECTIVE date, two rows above the expiry.
_TWO_COLUMN = (
    "Certificate registration no.\n"
    "Certificate unique ID \n"
    "Effective date \n"
    "Expiry date \n"
    "Frankfurt am Main \n"
    "549934 MP2021\n"
    "1000316844 \n"
    "2026-05-21 \n"
    "2027-12-19 \n"
    "2026-05-21 \n"
)


def test_a_two_column_layout_yields_no_expiry_rather_than_a_wrong_one():
    """The whole defect in one assertion. 2026-05-21 is the EFFECTIVE date; the
    expiry is 2027-12-19, two rows further down the values column. Reading the
    first is a 19-month error that makes a live certificate read as expired."""
    got = t0.extract_dates(_TWO_COLUMN)
    assert got.get("validity_to") is None


def test_the_adjacent_case_still_reads_and_keeps_confidence():
    got = t0.extract_dates("Expiry date: \n2031-02-28 \nIssue date: \n2026-02-23")
    assert got["validity_to"]["value"] == "2031-02-28"
    assert got["validity_to"]["conf"] == 1.0


def test_a_second_labelled_date_in_the_window_is_not_a_reason_to_refuse():
    """The obvious guard -- refuse when more than one date is in the window --
    is inverted: this exact shape is the COMMON correct one (measured on docs
    44, 405, 420, 816), and it is the two-column case that has a single date."""
    got = t0.extract_dates("Valid until:\n2027-10-30\nDate,\n2024-10-23\nSigned")
    assert got["validity_to"]["value"] == "2027-10-30"


def test_a_non_adjacent_label_falls_through_to_the_next_one():
    """Doc 317: a bilingual declaration whose English label is a bare heading
    and whose German label carries the value. Falling through rather than
    refusing is what makes the rule cost nothing on real documents."""
    text = "valid until\nDiese Konformitätserklärung ist gültig bis: \n13.02.2031\nApprover:"
    assert t0.extract_dates(text)["validity_to"]["value"] == "2031-02-13"


def test_the_verbatim_is_the_real_span_not_a_manufactured_quote():
    """`label + " " + date` asserts a sentence the document may not contain,
    which is what made the wrong value look trustworthy. Invariant 2 exists so
    a human can check the quote against the page."""
    got = t0.extract_dates("Expiry date: \n2031-02-28 \nIssue date: \n2026-02-23")
    verbatim = got["validity_to"]["verbatim"]
    assert "2031-02-28" in verbatim
    assert verbatim.replace("\n", " ").replace("  ", " ").strip() in (
        "Expiry date:  2031-02-28".replace("  ", " "),
        "Expiry date: 2031-02-28",
    )


# --- validity_from is deliberately laxer, and the asymmetry is the point ----
#
# MDR Annex IV mandates a place-and-date signature line, so a place legitimately
# sits between the label and the date for validity_from. Strict adjacency there
# would have dropped 21 correct documents to catch 1 (measured 2026-09-03).
# A wrong EXPIRY raises a false compliance alarm; a wrong start date does not,
# which is the same asymmetry `_PLACE_AND_DATE` already encodes in this module.

def test_validity_from_accepts_a_place_and_date_signature_line():
    got = t0.extract_dates("valid from\nSchaan, 2021-05-25\n")
    assert got["validity_from"]["value"] == "2021-05-25"


def test_validity_from_accepts_a_place_and_date_with_a_full_stop():
    got = t0.extract_dates("valid from\nSchaan. 2021-05-25\n")
    assert got["validity_from"]["value"] == "2021-05-25"


def test_validity_from_refuses_a_date_another_label_owns():
    """Doc 913: the between-text is ' manufacturing date: '. A colon means a
    second label claims that date; a signature line never has one."""
    got = t0.extract_dates("date of issue manufacturing date: 2025-07-14\n")
    assert got.get("validity_from") is None


def test_validity_to_is_strict_where_validity_from_is_not():
    """The same place-and-date shape that validity_from accepts must NOT give
    an expiry -- reading a signature line as an expiry is the defect this
    module's own comments already warn about."""
    got = t0.extract_dates("expiry date\nSchaan, 2021-05-25\n")
    assert got.get("validity_to") is None


# --- the refusal is recorded, because it is otherwise a non-event ----------- #
#
# `iso-without-standard-number` is computed in `tiers.integrity_flags` from the
# stored fields, because a wrong type is still a stored VALUE. A refused date
# pairing stores nothing at all, so unless the extractor says so at the time,
# nothing downstream can ever know the page had a layout T0 could not read.

def test_a_refusal_that_loses_the_field_is_recorded():
    got = t0.extract_dates(_TWO_COLUMN)
    assert got.get("validity_to") is None
    assert got[t0.DATE_LABEL_REFUSED_KEY] == ["validity_to"]


def test_a_refusal_a_later_label_satisfied_is_not_recorded():
    """Doc 317 again, from the other side. The fall-through is the rule working
    as designed, and a flag that fires on correct behaviour teaches people to
    ignore flags."""
    text = "valid until\nDiese Konformitätserklärung ist gültig bis: \n13.02.2031\nApprover:"
    got = t0.extract_dates(text)
    assert got["validity_to"]["value"] == "2031-02-13"
    assert t0.DATE_LABEL_REFUSED_KEY not in got


def test_a_clean_document_records_nothing():
    got = t0.extract_dates("Expiry date: \n2031-02-28 \nIssue date: \n2026-02-23")
    assert t0.DATE_LABEL_REFUSED_KEY not in got


def test_the_record_is_a_list_so_gate_can_never_write_it_as_evidence():
    """`gate.py`'s evidence writer skips any `fields` entry that is not a dict.
    A dict-shaped marker would have become an `evidence` row for a field named
    `_date_label_refused`, carrying a value no document contains."""
    got = t0.extract_dates(_TWO_COLUMN)
    assert isinstance(got[t0.DATE_LABEL_REFUSED_KEY], list)
