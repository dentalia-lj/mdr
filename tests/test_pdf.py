"""PDF util behavior on real fixture PDFs (committed subset, runs in CI)."""

from __future__ import annotations

import pymupdf
import pytest

from app.extract import pdf
from tests.fixtures.annotated_pdf import SIGNED_AT, annotate, text_png

TEXT_PDF = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"
SCAN_PDF = "IVOCLAR/MDR Certificate IV AG 2017_745.pdf"
PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def test_text_pdf_is_not_scan_and_carries_page_markers(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    assert doc.page_count > 0
    assert pdf.is_scan(doc) is False
    assert "[[page 1]]" in pdf.full_text(doc)
    assert "Certificate" in pdf.first_page_text(doc)


def test_scanned_pdf_is_detected_and_rasterizable(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(SCAN_PDF))
    assert pdf.is_scan(doc) is True
    png = pdf.rasterize_page(doc, 0, dpi=100)
    assert png.startswith(PNG_MAGIC)


def test_file_url_and_bare_path_resolve_the_same(fixture_pdf):
    path = fixture_pdf(TEXT_PDF)
    assert pdf.resolve_local("file://" + path) == pdf.resolve_local(path)


# --- bounded text for the LLM tiers ----------------------------------------- #
#
# Measured on the STRAUMANN backfill, 2026-08-17: two `navodila za uporabo` IFUs
# of 109 and 143 pages produced 558k and 818k characters and BOTH dead-lettered
# on `prompt is too long: 341460 tokens > 200000 maximum`. Nothing in the extract
# path bounded the input -- `llm._text_content` handed `full_text(doc)` over
# whole, and `max_tokens` in that module is the OUTPUT budget, not the input one.
# T0 had already read the type correctly off 1.290 characters of page 1.

def _synthetic(pages: list[str | int]):
    """A document with controlled page sizes. An `int` means "a page of about
    this many extractable characters"; a `str` is written verbatim.

    Text is inserted as many short LINES rather than one long string: a single
    `insert_text` call does not wrap, so anything past the page edge is never
    rendered and never extracted -- which silently produced a 200-character page
    where 2.000 were intended, and a test that passed for the wrong reason.

    Real fixtures cannot exercise the cap: the committed corpus subset has
    nothing near the context window, and committing a 143-page IFU to git to
    test a slice is the wrong trade."""
    import pymupdf
    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        if isinstance(body, int):
            lines = ["x" * 60 for _ in range(max(1, body // 61))]
        else:
            lines = [body]
        page.insert_text((40, 40), "\n".join(lines), fontsize=6)
    return doc


def test_bounded_text_returns_everything_when_it_fits():
    doc = _synthetic(["alpha", "beta"])
    text, dropped = pdf.bounded_text(doc, max_chars=100_000)
    assert dropped == 0
    assert "[[page 1]]" in text and "[[page 2]]" in text
    assert text == pdf.full_text(doc)      # identical below the cap
    doc.close()


def test_bounded_text_keeps_page_one_and_counts_what_it_dropped():
    doc = _synthetic(["page one marker", 4_000, 4_000])
    text, dropped = pdf.bounded_text(doc, max_chars=120)
    assert "page one marker" in text        # page 1 is the classification window
    assert dropped > 0                      # and the loss is COUNTED, never silent
    doc.close()


def test_bounded_text_says_in_the_text_that_it_truncated():
    # The model must be able to tell a window from a whole document, otherwise
    # "no expiry present" is indistinguishable from "the expiry was on page 97".
    doc = _synthetic(["one", 4_000])
    text, dropped = pdf.bounded_text(doc, max_chars=80)
    assert "truncated" in text.lower()
    assert dropped > 0
    doc.close()


def test_bounded_text_honours_the_budget_even_on_an_oversized_first_page():
    # The case that would still 400 if page 1 were exempt from the cap.
    doc = _synthetic([4_000])
    text, dropped = pdf.bounded_text(doc, max_chars=200)
    assert len(text) <= 200 + 80            # budget + truncation marker
    assert dropped > 0
    doc.close()


# --- annotation images: printed content no text layer holds ------------------ #
#
# VOCO's e-signed declarations print the issue date only inside the signature
# field's appearance, as an image (Task 8, 2026-10-13: Ceramic Bond's
# `validity_from` settled empty on T1's null). MuPDF's text extraction runs
# annotations, so an appearance that WRITES text is in the text layer already;
# one that PAINTS an image is not, and only an image read sees it.

VOCO_SIGNED = "VOCO/VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf"
LUMIWHITE = "LUMIWHITE/Declaration_of_Conformity_LUMIWHITE_OU.pdf"


def _signature_pages(n: int = 1) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"Signatures, page {i + 1}")
        page.insert_text((72, 92), "This document has been electronically signed by:")
    return doc


def test_a_signature_painted_as_an_image_is_found_and_its_date_is_in_no_text_layer():
    doc = annotate(_signature_pages())
    assert SIGNED_AT not in doc[0].get_text()
    assert pdf.annotation_image_pages(doc) == (1,)
    assert pdf.page_classes(doc) == ["sparse"]       # the page's class does not change


def test_only_the_page_carrying_the_annotation_is_listed():
    assert pdf.annotation_image_pages(annotate(_signature_pages(4), 3)) == (3,)


def test_a_stamp_annotation_painting_an_image_counts_too():
    assert pdf.annotation_image_pages(annotate(_signature_pages(), subtype="Stamp")) == (1,)


@pytest.mark.parametrize("kw", [
    {"appearance": "text"},       # written text: MuPDF's text extraction reads it
    {"appearance": None},         # an invisible signature: nothing drawn
    {"hidden": True},             # a viewer does not draw it
], ids=["text-appearance", "no-appearance", "hidden"])
def test_an_annotation_that_paints_no_image_is_not_listed(kw):
    doc = annotate(_signature_pages(), **kw)
    assert pdf.annotation_image_pages(doc) == ()
    assert (SIGNED_AT in doc[0].get_text()) is (kw.get("appearance") == "text")


def test_an_image_behind_indirect_resources_is_found():
    doc = annotate(_signature_pages())
    annot = doc[0].annot_xrefs()[0][0]
    form = int(doc.xref_get_key(annot, "AP/N")[1].split()[0])
    xobjects = doc.get_new_xref()
    doc.update_object(xobjects, doc.xref_get_key(form, "Resources/XObject")[1])
    resources = doc.get_new_xref()
    doc.update_object(resources, f"<</XObject {xobjects} 0 R>>")
    doc.xref_set_key(form, "Resources", f"{resources} 0 R")
    assert pdf.annotation_image_pages(pymupdf.open("pdf", doc.tobytes())) == (1,)


def test_a_link_written_inline_is_passed_over():
    """Henry Schein's safety data sheets write their links as direct
    dictionaries in `/Annots` (no object number to read them by): a link is
    not drawn content, and an unreadable annotation must not be taken for one
    that paints an image."""
    doc = _signature_pages()
    doc.xref_set_key(doc[0].xref, "Annots", "[<</Type/Annot/Subtype/Link/Border[0 0 0]"
                     "/Rect[150 548 228 555]/A<</S/URI/URI(mailto:x@example.com)>>>>]")
    doc = pymupdf.open("pdf", doc.tobytes())
    assert [t for _, t, _ in doc[0].annot_xrefs()] == [pymupdf.PDF_ANNOT_LINK]
    assert pdf.annotation_image_pages(doc) == ()


def test_an_image_in_the_page_content_is_not_an_annotation():
    """Out of this rule's reach: a date pasted into the page as an image (see
    `docs/dev/limits.md`)."""
    doc = _signature_pages()
    doc[0].insert_image(pymupdf.Rect(72, 600, 322, 660), stream=text_png(SIGNED_AT))
    assert pdf.annotation_image_pages(doc) == ()


def test_voco_prints_its_issue_date_only_in_the_signature_image(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(VOCO_SIGNED))
    assert pdf.page_classes(doc) == ["text", "text", "sparse"]
    assert pdf.annotation_image_pages(doc) == (3,)
    assert "14.01.2026" not in pdf.full_text(doc)


def test_lumiwhites_signature_page_carries_no_annotation(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(LUMIWHITE))
    assert pdf.page_classes(doc) == ["text", "sparse"]
    assert pdf.annotation_image_pages(doc) == ()
