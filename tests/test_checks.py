"""Evidence checks and page classes (extraction ladder, task 2).

Table-driven from the eval cases in
`docs/private/2026-10-08-extraction-ladder-research.md` sections 13-15. Pure
functions: no database, no model.
"""

from __future__ import annotations

import pymupdf
import pytest

from app.extract import checks, pdf
from app.extract.checks import Ctx

LONG = "Declaration of conformity. " * 12  # 300+ characters, a "text" page


def ctx(*pages, classes=None, truncated=False, unread=()):
    pages = list(pages)
    return Ctx(pages=pages, classes=classes or ["text"] * len(pages),
               truncated=truncated, unread_pages=frozenset(unread))


def ev(value, verbatim="", page=1):
    return {"value": value, "verbatim": verbatim, "page": page}


# --------------------------------------------------------------------------- #
# dates parsed from the quote
# --------------------------------------------------------------------------- #
DATE_ROWS = [
    ("Issue Date: 2022-07-08", "2022-07-08"),
    ("Valid until 07.07.2027", "2027-07-07"),
    ("Issue Date: February 18, 2021", "2021-02-18"),
    ("Ausstellungsdatum: 18. Februar 2021", "2021-02-18"),
    ("Issue Date: 1. marec 2021", "2021-03-01"),
    ("Date of issue 25. 08. 2025", "2025-08-25"),
    ("Effective date: 30-Apr-2025", "2025-04-30"),
    ("Issued June 24th, 2021", "2021-06-24"),
    ("Data de emissao: fev. 18, 2026", "2026-02-18"),
    ("Signed Jul 14, 2025", "2025-07-14"),
]


@pytest.mark.parametrize("quote,iso", DATE_ROWS)
def test_a_date_parsed_from_the_quote_settles(quote, iso):
    c = ctx(LONG + quote)
    assert checks.settled("validity_from", ev(iso, quote), c) is True


@pytest.mark.parametrize("quote,iso", DATE_ROWS[:3])
def test_a_date_the_quote_does_not_contain_does_not_settle(quote, iso):
    c = ctx(LONG + quote)
    assert checks.settled("validity_to", ev("1999-01-01", quote), c) is False


# The four eval dates the wider parser must NOT settle (2026-10-08 probe): the
# value is right or plausible, but the quote does not prove it.
UNPROVEN_DATES = [
    # the quote carries no date at all; the date sits elsewhere on the page
    ("validity_from", "2026-01-21",
     "Date of issue is the date of release, please refer to Signatures list at "
     "the beginning of the document.",
     "Date of issue is the date of release, please refer to Signatures list at "
     "the beginning of the document.\nPolarion ALM 2410\n2026-01-21 10:26\n"),
    # a two-column certificate: labels, then values; the quote is the reader's
    # reconstruction, with a remark the page does not hold
    ("validity_to", "2023-12-17",
     "Valid until ... 2023-12-17 (layout order: 2020-06-03, 2023-12-17, D1002600038)",
     "Valid from\nValid until\nRegistration no.\n2020-06-03\n2023-12-17\nD1002600038\n"),
    # a revision-history row, with the reader's own note appended
    ("validity_from", "2025-11-18",
     "5.0 2025-11-18 R. Ritter APM112430 (revision history entry for Version 5.0; "
     "no explicit 'Valid from' date stated)",
     "4.0\n2025-10-30 \nR. Ritter\nAPM112242\n5.0\n2025-11-18 \nR. Ritter\nAPM112430\n"),
    # a revision-history table read across its columns
    ("validity_from", "2025-11-24",
     "1.0 \nDate \n2025-11-24 \nAuthor \nR. Ritter \nRemark \nFirst MDR Version",
     "Revision History\nVersion \nDate \nAuthor\nRemark\n1.0 \n2025-11-24 \nR. Ritter\n"
     "First MDR Version\n"),
]


@pytest.mark.parametrize("f,iso,quote,page_text", UNPROVEN_DATES)
def test_the_eval_dates_the_quote_does_not_prove_stay_unsettled(f, iso, quote, page_text):
    c = ctx(LONG + page_text)
    assert checks.settled(f, ev(iso, quote), c) is False


# --------------------------------------------------------------------------- #
# an IFU's own date: its revision date, read off its quote (Task 9 replay)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value, quote, proven", [
    # the measured IFU revision lines (docs 709, 712, 714, 1059)
    ("2023-06-01", "REV.00_06-2023", True),
    ("2024-01-01", "REV.01_01-2024", True),
    ("2024-01-01", "REV.01_01/2024", True),
    ("2025-04-01", "04/2025", True),
    ("2023-06-01", "Rev. June 2023", True),
    ("2023-06-01", "Edition 2023-06", True),
    ("2024-03-15", "Date of issue: 2024-03-15", True),        # a whole printed date
    # decisions.md 2026-10-02: "701593/M/12 04/26" is revision 12 of April 2026
    ("2026-04-01", "701593/M/12 04/26", True),
    # ... and the STRAUMANN read it was built against: a day the code never printed
    ("2026-04-26", "701593/M/12 04/26", False),
    ("2025", "(c) Institut Straumann AG, 2025", False),       # not a date at all
    ("2023-06-01", "REV.00_07-2023", False),                  # another month
    ("2023-06-01", "15-06-2023", False),                      # a day the value drops
    ("2023-06-01", "2023-06-15", False),
    ("2023-06-01", "Instructions for use", False),            # no date in the quote
])
def test_an_ifu_date_is_one_its_own_quote_prints(value, quote, proven):
    assert checks.issue_date_proven(ev(value, quote)) is proven


def test_an_ifu_date_quote_must_be_on_its_text_page():
    c = ctx(LONG + "REV.00_06-2023")
    assert checks.issue_date_proven(ev("2023-06-01", "REV.00_06-2023"), c) is True
    assert checks.issue_date_proven(ev("2023-06-01", "REV.00_06-2023", page=2),
                                    ctx(LONG + "REV.00_06-2023", LONG)) is False
    # a page the text does not read in full: the image read's quote stands alone
    assert checks.issue_date_proven(
        ev("2023-06-01", "REV.00_06-2023", page=2),
        ctx(LONG, "", classes=["text", "image"], unread=(2,))) is True


def test_a_quote_that_is_not_on_the_cited_page_does_not_settle():
    c = ctx(LONG, LONG + "Issue Date: 2022-07-08")
    quote = "Issue Date: 2022-07-08"
    assert checks.settled("validity_from", ev("2022-07-08", quote, page=2), c) is True
    assert checks.settled("validity_from", ev("2022-07-08", quote, page=1), c) is False


def test_the_quote_match_tolerates_whitespace_and_case():
    c = ctx(LONG + "ISSUE   DATE:\n 2022-07-08")
    assert checks.settled("validity_from", ev("2022-07-08", "issue date: 2022-07-08"), c)


@pytest.mark.parametrize("ellipsis", ["...", "\u2026", "...."])
def test_a_shortened_quote_holds_when_every_piece_is_on_the_page(ellipsis):
    # GC declarations (Fuji Coat LC, Fuji ORTHO LC): the label sits in a table
    # header, the code rows below, and the reader quotes "label ... code".
    page = LONG + "Basis-UDI-DI\nProduct name\nFuji Coat LC\n76152081XSCAN001YC\n"
    c = ctx(page)
    quote = f"Basis-UDI-DI {ellipsis} 76152081XSCAN001YC"
    assert checks.quote_on_page(quote, 1, c.pages) is True
    assert checks.settled("basic_udi_di", ev("76152081XSCAN001YC", quote), c) is True


def test_a_shortened_quote_fails_when_one_piece_is_not_on_the_page():
    c = ctx(LONG + "Basis-UDI-DI\n76152081XSCAN001YC\n", LONG + "Fuji Coat LC")
    assert checks.quote_on_page("Basis-UDI-DI ... Fuji Coat LC", 1, c.pages) is False
    assert checks.quote_on_page("Basis-UDI-DI ... Fuji Coat LC", None, c.pages) is False
    assert checks.quote_on_page("...", 1, c.pages) is False


# --------------------------------------------------------------------------- #
# literal fields
# --------------------------------------------------------------------------- #
def test_manufacturer_must_be_literally_present():
    c = ctx(LONG + "Manufacturer: Ceramill GmbH")
    assert checks.settled("manufacturer", ev("Ceramill GmbH", "Manufacturer: Ceramill GmbH"), c)
    assert not checks.settled("manufacturer", ev("Amann Girrbach AG", "Manufacturer: Ceramill GmbH"), c)


@pytest.mark.parametrize("number,ok", [
    ("MDR 778483", True),
    ("HZ 1234567", True),
    ("0482", False),
    ("CE-0197", False),
    ("CE 0197", False),
    ("NB1639", False),
    ("1234", False),
])
def test_cert_number_is_never_a_notified_body_number(number, ok):
    quote = f"Certificate No. {number}"
    c = ctx(LONG + quote)
    assert checks.settled("cert_number", ev(number, quote), c) is ok


@pytest.mark.parametrize("udi,ok", [
    ("76152081XSCAN001YC", True),
    ("76152081XSCANOOIYC", False),  # check pair fails: OCR read 001 as OOI
])
def test_basic_udi_must_pass_its_check_pair(udi, ok):
    quote = f"Basic UDI-DI {udi}"
    c = ctx(LONG + quote)
    assert checks.settled("basic_udi_di", ev(udi, quote), c) is ok


def test_a_value_absent_from_the_text_layer_is_not_settled_even_if_check_valid():
    quote = "Basic UDI-DI 76152081XSCANOOIYC"
    c = ctx(LONG + quote)
    assert checks.settled("basic_udi_di", ev("76152081XSCAN001YC", quote), c) is False
    # ... but a later task may settle it from an image read via the check pair.
    assert checks.udi_check_valid("76152081XSCAN001YC") is True
    assert checks.udi_check_valid("76152081XSCANOOIYC") is False
    assert checks.udi_check_valid(None) is False


def test_an_unscorable_udi_is_not_check_valid_but_still_settles_when_printed():
    udi = "B-12345"  # no scorable agency check pair
    quote = f"Basic UDI-DI {udi}"
    c = ctx(LONG + quote)
    assert checks.udi_check_valid(udi) is False
    assert checks.settled("basic_udi_di", ev(udi, quote), c) is True


def test_ref_list_needs_95_percent_of_codes_printed():
    codes = [f"AB{n:04d}" for n in range(20)]
    text = LONG + " ".join(codes[:19])
    c = ctx(text)
    assert checks.settled("ref_list", ev(codes), c) is True        # 19/20 = 95%
    assert checks.settled("ref_list", ev(codes[:19] + ["ZZ9999", "YY8888"]), c) is False
    assert checks.settled("ref_list", ev([]), c) is False


def test_ref_list_codes_match_across_whitespace_and_case():
    c = ctx(LONG + "ref ab 0001  AB 0002")
    assert checks.settled("ref_list", ev(["AB0001", "ab0002"]), c) is True


# --------------------------------------------------------------------------- #
# regulation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,value,ok", [
    ("Regulation (EU) 2017/745", "MDR", True),
    ("Directive 93/42/EEC", "MDD", True),
    ("Directive 93/42/EEC", "MDR", False),
    ("Regulation (EU) 2017/745", "MDD", False),
    ("Nothing cited here", "n.a.", True),
    ("Nothing cited here", "MDR", False),
    ("Regulation (EU) 2017/745", "n.a.", False),
    # both printed: the check cannot tell which one the document is under
    ("2017/745 replaces Directive 93/42/EEC", "MDR", False),
    ("2017/745 replaces Directive 93/42/EEC", "MDD", False),
    ("2017/745 replaces Directive 93/42/EEC", "n.a.", False),
    ("Regulation 2017 / 745", "MDR", True),
])
def test_regulation_is_proven_by_the_printed_citation_alone(text, value, ok):
    assert checks.settled("regulation", ev(value), ctx(LONG + text)) is ok


#: Citation proof for GATE (plan Task 5b): `settled`, and text for the whole
#: document, since "only one citation printed" (or none, for `n.a.`) is a
#: claim about what is NOT printed, which a page with no text (a scanned page,
#: an unread page, text past the budget) does not support. An OCR layer is
#: text, as `settled` and the measured route read it.
@pytest.mark.parametrize("value, kw, ok", [
    ("MDR", {}, True),
    ("n.a.", {"text": "Nothing cited here"}, True),
    ("MDD", {"text": "Directive 93/42/EEC"}, True),
    ("MDD", {}, False),                                       # not the citation printed
    ("MDR", {"text": "2017/745 replaces Directive 93/42/EEC"}, False),   # both printed
    ("MDR", {"truncated": True}, False),
    ("MDR", {"classes": ["text", "image"]}, False),           # a scanned page
    ("MDR", {"classes": ["text", "ocr"]}, True),              # an OCR layer is text
    ("n.a.", {"text": "Nothing cited here", "classes": ["ocr", "ocr"]}, True),
    ("MDR", {"unread": (2,)}, False),
    ("MDR", {"classes": ["text"]}, False),                    # classes do not match the pages
    ("MDR", {"classes": ["text", "sparse"]}, True),           # a cover page is read as text
    # a sparse page (a signature page, LUMIWHITE's) is read as text (F7, round 2)
    ("n.a.", {"text": "Nothing cited here", "classes": ["text", "sparse"]}, True),
    ("n.a.", {"text": "", "classes": ["sparse", "sparse"]}, False),   # no text layer at all
    ("mdr", {}, False),                                       # well-formed values only (F7)
    ("MDR (EU) 2017/745", {}, False),
    (None, {}, False),
], ids=["mdr", "na", "mdd", "wrong-citation", "both", "truncated", "image-page",
        "ocr-page", "na-ocr", "unread-page", "classes-unknown", "sparse-page", "na-sparse",
        "no-text-page", "lower-case", "not-a-value", "empty"])
def test_a_citation_proves_the_regulation_only_where_every_page_was_read_as_text(value, kw, ok):
    text = kw.get("text", "Regulation (EU) 2017/745")
    c = Ctx(pages=[LONG + text, LONG], classes=kw.get("classes", ["text", "text"]),
            truncated=kw.get("truncated", False), unread_pages=frozenset(kw.get("unread", ())))
    assert checks.citation_proven(ev(value), c) is ok


# --------------------------------------------------------------------------- #
# semantic fields, missing evidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("f", ["type", "coverage_scope"])
def test_a_semantic_field_settles_on_a_quote_on_the_cited_page(f):
    # Denis, 2026-10-12: as measured, a T1 quote found on the cited page stops
    # the ladder for type and coverage_scope; the gate still wants T0 or two
    # agreeing reads before production (task 5).
    c = ctx(LONG, "EU Declaration of Conformity. " * 10)
    assert checks.settled(f, ev("DoC", "EU Declaration of Conformity", page=2), c) is True
    assert checks.settled(f, ev("DoC", "EU Declaration of Conformity", page=1), c) is False
    assert checks.settled(f, ev("DoC", "a sentence the page does not hold", page=2), c) is False
    assert checks.settled(f, ev(None, "EU Declaration of Conformity", page=2), c) is False


def test_an_unknown_field_is_never_settled():
    assert checks.settled("unknown_field", ev("DoC", "Declaration of conformity"), ctx(LONG)) is False


def test_null_or_missing_evidence_is_not_settled():
    c = ctx(LONG)
    assert checks.settled("manufacturer", None, c) is False
    assert checks.settled("manufacturer", ev(None), c) is False
    assert checks.settled("manufacturer", ev(""), c) is False


# --------------------------------------------------------------------------- #
# null rule
# --------------------------------------------------------------------------- #
def test_a_null_is_settled_when_no_label_occurs():
    c = ctx("Certificate body text without that field.  " * 10)
    assert checks.null_settled("basic_udi_di", c) is True


def test_a_label_in_the_text_unsettles_a_null():
    c = ctx(LONG + "Basic UDI-DI: see annex")
    assert checks.null_settled("basic_udi_di", c) is False


def test_the_word_including_is_not_a_udi_label():
    c = ctx("Products including accessories and guidelines. " * 10)
    assert checks.null_settled("basic_udi_di", c) is True


@pytest.mark.parametrize("kwargs", [
    {"classes": ["text", "image"]},
    {"classes": ["text", "ocr"]},
    {"unread": {3}},
    {"truncated": True},
])
def test_a_null_is_not_settled_when_part_of_the_document_was_not_read(kwargs):
    c = ctx("Plain body text.  " * 20, "Second page text.  " * 20, **kwargs)
    assert checks.null_settled("basic_udi_di", c) is False


def test_a_sparse_page_does_not_unsettle_a_null():
    c = ctx("Plain body text.  " * 20, "Cover", classes=["text", "sparse"])
    assert checks.null_settled("basic_udi_di", c) is True


@pytest.mark.parametrize("classes", [
    [],                     # no classes given: unknown, not reliable
    ["text"],               # one class for two pages
    ["sparse", "sparse"],   # no page carries a text layer
])
def test_a_null_is_not_settled_when_the_page_classes_cannot_vouch_for_the_text(classes):
    c = Ctx(pages=["Plain body text.  " * 20, "Second page text.  " * 20], classes=classes)
    assert checks.null_settled("basic_udi_di", c) is False


def test_a_vector_drawn_page_does_not_let_a_null_settle():
    # A GC declaration drawn as outlines: no characters, no large image, only
    # paths. Classed "image", so an empty text layer proves nothing.
    doc = _build(["text", "vector"])
    c = Ctx(pages=pdf.page_texts(doc), classes=pdf.page_classes(doc))
    assert c.classes == ["text", "image"]
    assert checks.null_settled("cert_number", c) is False
    only = _build(["vector"])
    c = Ctx(pages=pdf.page_texts(only), classes=pdf.page_classes(only))
    assert checks.null_settled("cert_number", c) is False


def test_semantic_fields_have_no_null_rule():
    assert checks.null_settled("type", ctx("anything")) is False


def test_one_empty_read_never_settles_the_manufacturer():
    """Doc 2173 (Task 9 replay): EURONDA's Euroseal declaration has a text
    layer in a font with a private encoding ('(MVIGXMZI9)' for 'Directive'),
    so T1 could not read the name and no "Manufacturer" label was there to
    find. The null rule took that as proof the document names none, and the
    image read, which reads EURONDA S.P.A. at 0.95, was never asked. Every
    compliance document names its manufacturer, often as a letterhead with no
    label, so a missing label never proves the name is absent."""
    assert checks.null_settled("manufacturer", ctx("Plain body text.  " * 20)) is False
    assert checks.null_settled("validity_to", ctx("Plain body text.  " * 20)) is True


# --------------------------------------------------------------------------- #
# two empty reads (PRD v3 section 5, corrected 2026-10-12 to the measured rule)
# --------------------------------------------------------------------------- #
BODY = "Plain body text.  " * 20


@pytest.mark.parametrize("f", ["cert_number", "validity_to", "ref_list", "type",
                               "coverage_scope", "manufacturer", "basic_udi_di"])
def test_two_empty_reads_settle_any_field(f):
    # labels for the field do not stop it; that is the single-read null rule
    c = ctx(BODY + "Certificate no. Valid until Manufacturer REF")
    assert checks.both_null_settled(f, c) is True


def test_two_empty_reads_settle_despite_image_and_ocr_pages():
    c = ctx(BODY, "", BODY, classes=["text", "image", "ocr"])
    assert checks.both_null_settled("cert_number", c) is True


@pytest.mark.parametrize("label", ["Basic UDI-DI: see annex", "Basis-UDI-DI", "UDI"])
def test_two_empty_udi_reads_do_not_settle_when_the_text_carries_a_udi_label(label):
    assert checks.both_null_settled("basic_udi_di", ctx(BODY + label)) is False
    assert checks.both_null_settled("cert_number", ctx(BODY + label)) is True


def test_including_is_not_a_udi_label_for_two_empty_reads():
    assert checks.both_null_settled("basic_udi_di", ctx(BODY + "including guidelines")) is True


def test_two_empty_udi_reads_do_not_settle_without_a_text_layer():
    # a scan: no text to look for a UDI label in (the measured scan route never
    # settled a UDI on two nulls); other fields still settle
    c = ctx("", "", classes=["image", "image"])
    assert checks.both_null_settled("basic_udi_di", c) is False
    assert checks.both_null_settled("validity_to", c) is True
    assert checks.both_null_settled("basic_udi_di", Ctx(pages=[BODY], classes=[])) is False


@pytest.mark.parametrize("kwargs", [{"unread": {5}}, {"truncated": True}])
@pytest.mark.parametrize("f", ["cert_number", "basic_udi_di", "type"])
def test_two_empty_reads_do_not_settle_when_part_was_not_read(f, kwargs):
    assert checks.both_null_settled(f, ctx(BODY, **kwargs)) is False


# --------------------------------------------------------------------------- #
# mixed PDF: a literal on the text page settles, a null does not
# --------------------------------------------------------------------------- #
def test_mixed_pdf_text_page_settles_a_value_but_not_a_null():
    quote = "Manufacturer: Ceramill GmbH"
    c = ctx(LONG + quote, "", classes=["text", "image"])
    assert checks.settled("manufacturer", ev("Ceramill GmbH", quote), c) is True
    assert checks.null_settled("validity_to", c) is False


def test_truncated_text_settles_a_present_value_but_not_a_null():
    quote = "Manufacturer: Ceramill GmbH"
    c = ctx(LONG + quote, truncated=True)
    assert checks.settled("manufacturer", ev("Ceramill GmbH", quote), c) is True
    assert checks.null_settled("validity_to", c) is False


# --------------------------------------------------------------------------- #
# page classes, on PDFs built here
# --------------------------------------------------------------------------- #
def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def _build(kinds: list[str]) -> pymupdf.Document:
    doc = pymupdf.open()
    for kind in kinds:
        page = doc.new_page()
        if kind == "text":
            y = 72
            for _ in range(12):
                page.insert_text((72, y), "Declaration of conformity text line.")
                y += 20
        elif kind == "image":
            page.insert_image(page.rect, stream=_png())
        elif kind == "ocr":
            page.insert_image(page.rect, stream=_png())
            y = 72
            for _ in range(12):
                page.insert_text((72, y), "Declaration of conformity text line.",
                                 render_mode=3)
                y += 20
        elif kind == "background":  # digital text OVER a full-page image
            page.insert_image(page.rect, stream=_png())
            for i in range(12):
                page.insert_text((72, 72 + 20 * i), "Declaration of conformity text line.")
        elif kind == "under":  # visible text drawn first, a scan painted over it
            for i in range(12):
                page.insert_text((72, 72 + 20 * i), "Declaration of conformity text line.")
            page.insert_image(page.rect, stream=_png())
        elif kind == "vector":  # text converted to outlines: paths, no characters
            for k in range(250):
                x, y = 72 + (k % 25) * 18, 72 + (k // 25) * 18
                page.draw_rect(pymupdf.Rect(x, y, x + 10, y + 10), color=(0, 0, 0), fill=(0, 0, 0))
        elif kind == "blank":
            pass
        elif kind == "sparse":
            page.insert_text((72, 72), "Cover")
        elif kind == "overlay":  # a scan with a short visible caption over it
            page.insert_image(page.rect, stream=_png())
            page.insert_text((72, 72), "Scanned 2025-08-25 by the quality office")
        elif kind == "signature":  # a short page, no image: a signature block
            page.insert_text((72, 72), "Leuven, 25 August 2025")
            page.insert_text((72, 92), "Signed: J. Peeters, Quality Manager")
        elif kind == "logo":  # small image + a real text layer
            page.insert_image(pymupdf.Rect(72, 700, 132, 780), stream=_png())
            for i in range(12):
                page.insert_text((72, 72 + 20 * i), "Declaration of conformity text line.")
    return doc


@pytest.mark.parametrize("kinds,expected", [
    (["text"], ["text"]),
    (["image"], ["image"]),
    (["ocr"], ["ocr"]),
    (["sparse"], ["sparse"]),
    (["logo"], ["text"]),
    # a full-page background image under visible text is a digital page, not an
    # OCR layer (14 such certificates and declarations in the eval set)
    (["background"], ["text"]),
    # visible text painted over by a later full-page image is hidden: OCR-like
    (["under"], ["ocr"]),
    # no characters, drawn content: classed with scans
    (["vector"], ["image"]),
    (["blank"], ["sparse"]),
    # a scan with a short visible overlay: its content is the image (F7)
    (["overlay"], ["image"]),
    (["signature"], ["sparse"]),
    (["text", "image", "ocr", "sparse"], ["text", "image", "ocr", "sparse"]),
])
def test_page_classes(kinds, expected):
    assert pdf.page_classes(_build(kinds)) == expected


@pytest.mark.parametrize("kinds,has_text", [
    (["text"], True),
    (["image", "text"], True),
    (["image"], False),
    # an OCR layer is a text layer: the measured ladder ran T1 on these
    (["ocr"], True),
    (["image", "ocr"], True),
    (["background"], True),
    (["sparse", "image"], False),
    (["vector"], False),
])
def test_has_text_page(kinds, has_text):
    assert pdf.has_text_page(_build(kinds)) is has_text


def test_is_scan_is_unchanged_by_the_new_classes():
    # page 1 is an OCR page with a text layer: is_scan still reads page-1 length only
    assert pdf.is_scan(_build(["ocr"])) is False
    assert pdf.is_scan(_build(["image", "text"])) is True


# --------------------------------------------------------------------------- #
# reads off page images: shape, normal form, agreement (scan ladder, Task 3)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("field_name, a, b, same", [
    ("validity_from", "2025-08-25", "25.08.2025", True),        # dates compare as ISO
    ("validity_from", "2025-08-25", "25. 08. 2025", True),
    ("validity_from", "2025-08-25", "2025-08-26", False),
    ("cert_number", "G10 082649 0004", "G10082649 0004", True),  # codes compacted
    ("basic_udi_di", "++J022MD0098KV", "++j022md 0098kv", True),
    ("ref_list", ["003477", "003478"], ["003478", "00 3477"], True),  # lists as sets
    ("ref_list", ["003477"], ["003477", "003478"], False),
    ("manufacturer", "GC EUROPE N.V.", "gc  europe n.v", True),  # spacing, case, end dot
    ("manufacturer", "GC EUROPE N.V.", "GC Corporation", False),
    ("type", "DoC", "doc", True),
    ("type", "EC", "ISO", False),
])
def test_normalized_compares_what_two_reads_mean(field_name, a, b, same):
    assert (checks.normalized(field_name, a) == checks.normalized(field_name, b)) is same


@pytest.mark.parametrize("value", [None, "", "  ", [], ["", " "]])
def test_an_empty_answer_normalizes_to_none(value):
    assert checks.normalized("ref_list" if isinstance(value, list) else "type", value) is None


@pytest.mark.parametrize("field_name, value, ok", [
    ("validity_from", "2025-08-25", True),
    ("validity_from", "25.08.2025", False),     # an image read answers ISO, as asked
    ("validity_from", "2025-02-30", False),     # the shape of a date, not a day
    ("validity_to", "2025", False),             # the STRAUMANN copyright year
    ("type", "EC", True),
    ("type", "Declaration of Conformity", False),
    ("coverage_scope", "group", True),
    ("coverage_scope", "item", False),
    ("regulation", "n.a.", True),
    ("regulation", "MDR 2017/745", False),
    ("cert_number", "G10 082649 0004", True),
    ("cert_number", "0482", False),             # a notified-body number
    ("cert_number", "CE-0197", False),
    ("basic_udi_di", "++J022MD0098KV", True),
    ("ref_list", ["003477"], True),
    ("ref_list", [], False),
    ("manufacturer", "GC EUROPE N.V.", True),
    ("manufacturer", None, False),
])
def test_well_formed_is_the_shape_a_field_takes(field_name, value, ok):
    assert checks.well_formed(field_name, value) is ok


def _r(value, conf):
    return {"value": value, "conf": conf}


@pytest.mark.parametrize("a, b, minimum, agreed", [
    (_r("EC", 0.70), _r("EC", 0.70), 0.70, True),     # each read at the floor
    (_r("EC", 0.69), _r("EC", 0.95), 0.70, False),    # one under it
    (_r("EC", 0.95), _r("EC", 0.69), 0.70, False),
    (_r("EC", 0.90), _r("ISO", 0.90), 0.70, False),   # different values
    (_r(None, 0.90), _r(None, 0.90), 0.70, False),    # two nulls: both_null_settled's
    (_r("Declaration", 0.9), _r("Declaration", 0.9), 0.70, False),  # agreed, malformed
    (_r("EC", 0.90), None, 0.70, False),              # the second read gave nothing
])
def test_agree_needs_one_well_formed_value_and_each_read_at_the_floor(a, b, minimum, agreed):
    assert checks.agree("type", a, b, minimum) is agreed


def test_agree_reads_dates_and_codes_in_their_normal_form():
    assert checks.agree("cert_number", _r("G10 082649 0004", 0.8),
                        _r("G10082649 0004", 0.8), 0.70)
    # Well-formed means ISO for a date, so a non-ISO answer never settles one.
    assert not checks.agree("validity_from", _r("2025-08-25", 0.9), _r("25.08.2025", 0.9), 0.70)


# --------------------------------------------------------------------------- #
# A Basic UDI-DI with no digit is a word, not a code (review 2026-10-13)
# --------------------------------------------------------------------------- #
#: What T0 read after a "Basic UDI-DI" label on the SFTP mirror: 12 documents,
#: every value the word that follows the label in a table header or a column.
#: `udi.validate_basic_udi` calls each UNSCORABLE (unknown agency), not
#: INVALID, so the checks let them pass until the digit rule.
MIRROR_WORDS = ["PRODUCT", "Thermaprep", "Intended", "Classification", "Document",
                "VivaScan", "Update", "Terminology", "Grandio", "Phasor"]


@pytest.mark.parametrize("word", MIRROR_WORDS)
def test_a_digitless_word_is_never_a_basic_udi_di(word):
    quote = f"Basic UDI-DI {word}"
    c = ctx(LONG + quote)
    assert checks.settled("basic_udi_di", ev(word, quote), c) is False
    assert checks.well_formed("basic_udi_di", word) is False


@pytest.mark.parametrize("udi", [
    "76152081XSCAN001YC",       # GS1, check-valid
    "++J022MD0098KV",           # HIBCC, check-valid
    "B-12345",                  # EUDAMED-assigned: unscorable, left as it was
    "A040500000300",            # unknown agency, unscorable, but it has digits
])
def test_codes_with_an_agency_or_a_digit_are_untouched(udi):
    quote = f"Basic UDI-DI {udi}"
    c = ctx(LONG + quote)
    assert checks.settled("basic_udi_di", ev(udi, quote), c) is True
    assert checks.well_formed("basic_udi_di", udi) is True


@pytest.mark.parametrize("kinds, value, proven", [
    # the LUMIWHITE shape: a declaration and a short signature page, no image
    (["text", "signature"], "n.a.", True),
    # a scan with a short caption over it: what it prints is in the image (F7)
    (["text", "overlay"], "n.a.", False),
    (["text", "overlay"], "MDR", False),
])
def test_a_citation_proof_over_a_real_document(kinds, value, proven):
    """Review 2026-10-13, F7 round 2: the root cause was the page class, not
    the absence proof. A signature page is read as text and stops nothing; a
    scanned page with a caption is an image page and stops every proof."""
    doc = _build(kinds)
    if value == "MDR":
        doc[0].insert_text((72, 400), "Regulation (EU) 2017/745")
    c = Ctx(pages=pdf.page_texts(doc), classes=pdf.page_classes(doc))
    assert checks.citation_proven(ev(value), c) is proven


# --------------------------------------------------------------------------- #
# a read's value that is provably not the field (Task 9 replay, 2026-10-14)
# --------------------------------------------------------------------------- #
#: Doc 622 (Ultradent IsoBlock): a two-column form whose text layer prints the
#: labels first and the values after, so T1 paired "Model Numbers:" with the
#: GMDN and EMDN lines.
ULTRADENT_FORM = """UMDNS Code:
GMDN Code:
EMDN Code:
Basic UDI-D1:
Model Numbers:
14066, Tongue Depressor
36311, Occlusal splint, dental preformed
V9001, Tongue Depressors, Single-Use
0883205-TF057B8
331-"""


def test_codes_printed_as_nomenclature_entries_are_found():
    codes = ["14066", "36311", "V9001", "331-"]
    assert checks.nomenclature_codes(codes, ULTRADENT_FORM) == ["14066", "36311", "V9001"]


@pytest.mark.parametrize("text", [
    "EMDN code / description\nV0599 - CLINICAL KITS - OTHERS\n"
    "GMDN code / description\n45247 - Endodontic obturation system",
    "EMDN Code:\nQ010199 Conservative dentistry\nUMDNS Code:\n16-411\nBohrinstrument",
    "EMDN Code + term\nQ01010103\nDental Conservation Composites",
])
def test_a_code_after_a_nomenclature_label_is_found(text):
    codes = ["V0599", "45247", "Q010199", "16-411", "Q01010103", "1510310B"]
    found = checks.nomenclature_codes(codes, text)
    assert found and "1510310B" not in found


@pytest.mark.parametrize("codes, text", [
    # a REF table on a declaration that names a GMDN code (doc 1406's layout)
    (["1510310B", "1510311B"], "Product reference GMDN code\n1510310B 10 D.T. LIGHT-POST #0.5\n"
                               "1510311B 10 D.T. LIGHT-POST #1\n38609"),
    # five-digit article numbers in a list, nothing after them
    (["10234", "10235"], "GMDN 12345\nREF\n10234\n10235"),
    # the same codes on a document that names no nomenclature
    (["14066", "V9001"], "Model Numbers:\n14066, Tongue Depressor\nV9001, Tongue Depressors"),
])
def test_real_ref_codes_are_not_nomenclature_codes(codes, text):
    assert checks.nomenclature_codes(codes, text) == []


def test_a_list_the_read_itself_disowns_is_rejected():
    """Doc 305: Barco's type designations, which the read returned at 0.4 while
    saying they are not REF codes. Under 0.5 and in its own words only: real
    Ultradent model numbers were read at 0.55 to 0.7 with "not labelled REF"
    (Task 9 replay), and a real list can be read at 0.35."""
    quote = ("Type: MDAC-2422 SNIB TER, MDAC-2422 SNNB ESA "
             "(these are type designations, not explicit REF codes)")
    disowned = {"value": ["MDAC-2422 SNIB TER", "MDAC-2422 SNNB ESA"], "conf": 0.4,
                "verbatim": quote}
    assert checks.not_the_field("ref_list", disowned)[0] is not None
    assert checks.not_the_field("ref_list", dict(disowned, conf=0.55))[0] is None
    assert checks.not_the_field("ref_list", {"value": ["5941-"], "conf": 0.35,
                                              "verbatim": "Model Numbers: '5941-' as printed"})[0] is None


def test_nomenclature_codes_leave_the_rest_of_the_list():
    ev_ = {"value": ["331-", "14066"], "conf": 0.8, "verbatim": "Model Numbers"}
    reason, kept = checks.not_the_field("ref_list", ev_, ULTRADENT_FORM)
    assert reason and kept == ["331-"]
    reason, kept = checks.not_the_field(
        "ref_list", {"value": ["14066", "36311", "V9001"], "conf": 0.8, "verbatim": "x"},
        ULTRADENT_FORM)
    assert reason and kept is None


def test_a_value_with_no_rule_is_never_rejected():
    assert checks.not_the_field("type", {"value": "DoC", "conf": 0.1, "verbatim": "x"}) == (None, None)
    assert checks.not_the_field("ref_list", None) == (None, None)


@pytest.mark.parametrize("value, quote, rejected", [
    # doc 1406, Sonnet: the declaration's own reference, printed above its body
    ("1000-TF_8_EUDOC_000219 [5]", "Declaration of conformity reference: 1000-TF_8_EUDOC_000219 [5]", True),
    ("DC-2021-05", "DoC No. DC-2021-05", True),
    ("KE 2021/3", "Konformitätserklärung Nr. KE 2021/3", True),
    ("1000-TF 8 EUDOC 000219 [5]", "Référence de la déclaration de conformité : 1000-TF 8 EUDOC 000219 [5]", True),
    # ruling A: the certificate a declaration cites, and a certificate's own number
    ("8445 rev.18", "EC certificate n° 8445 rev.18 of Approval of Full Quality Assurance System", False),
    ("G1 021108 0031", "Certificate No.: G1 021108 0031", False),
    ("MDR 778483", "This declaration is based on EU certificate MDR 778483", False),
    ("HZ 1470094-1", "Declaration of Conformity ... No. of Certificate: HZ 1470094-1", False),
])
def test_a_certificate_number_the_quote_names_the_declarations_own_is_rejected(value, quote, rejected):
    """Ruling A (2026-10-12): a DoC's `cert_number` is the certificate it cites.
    The declaration's own document or reference number is never one, as a
    notified body's four-digit number is never one (`is_notified_body_number`,
    a shape rule on the value); this one needs the label the quote prints."""
    reason, _ = checks.not_the_field("cert_number", {"value": value, "conf": 0.6, "verbatim": quote})
    assert (reason is not None) is rejected


@pytest.mark.parametrize("field, value, quote, rejected", [
    # doc 617 (Ultradent): the release date of the form the declaration is made on
    ("validity_from", "2024-05-31", "Template RP00S0.07 Released May 31, 2024", True),
    ("validity_to", "2024-05-31", "Template RP00S0.07 Released May 31, 2024", True),
    ("validity_from", "2024-08-08", "Subscribed and sworn to before me on 08 August 2024", False),
    # doc 622: the declaration's own release line stays (arguable, not a template)
    ("validity_from", "2023-04-20", "RTD-002882.02 Released April 20, 2023", False),
])
def test_a_date_the_quote_gives_a_template_is_rejected(field, value, quote, rejected):
    reason, _ = checks.not_the_field(field, {"value": value, "conf": 0.3, "verbatim": quote})
    assert (reason is not None) is rejected


def test_a_model_number_label_unsettles_an_empty_ref_list():
    """Doc 617: Ultradent prints its article numbers as "Model Numbers: 5916-,
    S5917-". With no REF label in the text, T1's empty answer settled under
    the null rule and the image read was never asked."""
    assert checks.null_settled("ref_list", ctx(LONG + "Model Numbers: 5916-, S5917-")) is False
    assert checks.null_settled("ref_list", ctx(LONG)) is True
