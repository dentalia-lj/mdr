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


def test_a_quote_that_is_not_on_the_cited_page_does_not_settle():
    c = ctx(LONG, LONG + "Issue Date: 2022-07-08")
    quote = "Issue Date: 2022-07-08"
    assert checks.settled("validity_from", ev("2022-07-08", quote, page=2), c) is True
    assert checks.settled("validity_from", ev("2022-07-08", quote, page=1), c) is False


def test_the_quote_match_tolerates_whitespace_and_case():
    c = ctx(LONG + "ISSUE   DATE:\n 2022-07-08")
    assert checks.settled("validity_from", ev("2022-07-08", "issue date: 2022-07-08"), c)


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


# --------------------------------------------------------------------------- #
# semantic fields, missing evidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("f", ["type", "coverage_scope", "unknown_field"])
def test_semantic_fields_are_never_settled_here(f):
    assert checks.settled(f, ev("DoC", "Declaration of conformity"), ctx(LONG)) is False


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


def test_semantic_fields_have_no_null_rule():
    assert checks.null_settled("type", ctx("anything")) is False


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
        elif kind == "sparse":
            page.insert_text((72, 72), "Cover")
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
    (["text", "image", "ocr", "sparse"], ["text", "image", "ocr", "sparse"]),
])
def test_page_classes(kinds, expected):
    assert pdf.page_classes(_build(kinds)) == expected


@pytest.mark.parametrize("kinds,has_text", [
    (["text"], True),
    (["image", "text"], True),
    (["image"], False),
    (["ocr"], False),
    (["sparse", "image"], False),
])
def test_has_text_page(kinds, has_text):
    assert pdf.has_text_page(_build(kinds)) is has_text


def test_is_scan_is_unchanged_by_the_new_classes():
    # page 1 is an OCR page with a text layer: is_scan still reads page-1 length only
    assert pdf.is_scan(_build(["ocr"])) is False
    assert pdf.is_scan(_build(["image", "text"])) is True
