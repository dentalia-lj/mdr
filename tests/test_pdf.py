"""PDF util behavior on real fixture PDFs (committed subset, runs in CI)."""

from __future__ import annotations

from app.extract import pdf

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
