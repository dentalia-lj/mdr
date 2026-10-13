"""PDF access for EXTRACT: text with page markers, rasterization, scan detection.

Engine: PyMuPDF (text + raster). pdfplumber is used separately in
`t0_templates` for REF-list tables. T0 layout templates stay engine-agnostic
data; this module is the only place that touches PyMuPDF directly.

`archive_url` handling here is an S0.2 stopgap for the real StorageAdapter
(S1.4): a filesystem path or `file://` URL resolves to a local file so EXTRACT
can run over the local seed corpus.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import pymupdf


def resolve_local(archive_url: str) -> Path:
    """Map an archive_url to a local path (path or file:// URL). Stopgap: the
    real StorageAdapter arrives in S1.4."""
    if archive_url.startswith("file://"):
        return Path(unquote(urlparse(archive_url).path))
    return Path(archive_url)


def open_doc(archive_url: str) -> pymupdf.Document:
    """Open a PDF from an archive_url. Caller closes (or use as a context mgr)."""
    return pymupdf.open(resolve_local(archive_url))


def _page_text(page) -> str:
    """The single point where PDF text enters the process, and so the single
    point that sanitises it.

    PDFs legitimately carry NUL (0x00) in their text streams, and PyMuPDF passes
    it straight through. Everything downstream is Postgres text or jsonb, which
    rejects NUL outright — two GC pilot extractions died on `DataError:
    PostgreSQL text fields cannot contain NUL (0x00) bytes` (2026-08-12).
    Sanitising here rather than at each write site is the difference between one
    correct place and an open-ended set of them.

    Dropped, not replaced: NUL carries no textual meaning, and a substitute
    character would end up in `verbatim` evidence that a human reads back
    against the source document."""
    return page.get_text("text").replace("\x00", "")


def page_texts(doc: pymupdf.Document) -> list[str]:
    """Text per page; index 0 is page 1."""
    return [_page_text(page) for page in doc]


def first_page_text(doc: pymupdf.Document, limit: int | None = None) -> str:
    """Page-1 text (used by T0 for type/regulation/manufacturer classification)."""
    if doc.page_count == 0:
        return ""
    # Sanitise before slicing: `limit` is a budget on real characters, and
    # counting NULs against it would shorten the classification window on
    # exactly the malformed documents that need the most text.
    text = _page_text(doc[0])
    return text[:limit] if limit else text


def full_text(doc: pymupdf.Document, page_markers: bool = True) -> str:
    """All text, optionally with `[[page N]]` markers so the LLM tiers can cite
    a page in their evidence."""
    parts = []
    for i, page in enumerate(doc, start=1):
        text = _page_text(page)
        parts.append(f"[[page {i}]]\n{text}" if page_markers else text)
    return "\n".join(parts)


#: Character budget for text handed to an LLM tier. Deliberately a CHARACTER
#: budget: it is exact, free to compute, and independent of any tokenizer
#: version, and the failure it exists to prevent is a hard 400 rather than a
#: cost.
#:
#: MEASURED, not assumed (STRAUMANN IFUs, 2026-08-17): 360.052 characters of
#: Slovene became **147.289 input tokens** -- 2,44 chars/token, not the ~4 an
#: English estimate suggests, and 74% of the 200k ceiling rather than the 45%
#: first claimed here. The margin is real but thin: a document tokenizing at
#: 2,0 chars/token would put this budget at ~180k tokens and, once the system
#: prompt, the JSON schema and the output allowance are added, back through the
#: ceiling. Slovene, Croatian and any heavily-accented or tabular text sit in
#: that range. Revisit the number if a 400 recurs -- see
#: [llm-text-budget-is-flat] in tasks/followups.md, where the per-type budget
#: that would replace it is specified.
LLM_TEXT_BUDGET_CHARS = 360_000

_TRUNCATION_NOTE = "\n[[truncated: document continues beyond this point]]"


def bounded_text(doc: pymupdf.Document, max_chars: int = LLM_TEXT_BUDGET_CHARS
                 ) -> tuple[str, int]:
    """`full_text` clipped to `max_chars`. Returns (text, characters_dropped).

    Nothing bounded the text handed to T1/T2 until 2026-08-17. On the STRAUMANN
    backfill two `navodila za uporabo` IFUs -- 109 and 143 pages, 558k and 818k
    characters -- were sent whole and rejected with `prompt is too long: 341460
    tokens > 200000 maximum`. A 400 is not billed, but the job retries into the
    same wall and dead-letters, so the document is lost rather than degraded.
    T0 had meanwhile typed both correctly from 1.290 characters of page 1, which
    is the whole argument for a window: the marginal page is worth far less than
    the first one.

    Page order is preserved and the front of the document is kept, because that
    is where type, manufacturer, dates and certificate numbers live. Page 1 is
    NOT exempt from the budget -- a single page larger than the whole allowance
    is exactly the case that would still 400 -- but it is always reached first,
    so it survives except when it alone exceeds the cap.

    The truncation is announced IN the text. Without that marker the model
    cannot distinguish "this document states no expiry" from "the expiry was on
    a page you did not send", and would report the first with confidence. The
    dropped count is returned so the caller can put it on the job result rather
    than losing it: a truncation nobody counts is the silent skip CLAUDE.md
    forbids."""
    text = full_text(doc)
    if len(text) <= max_chars:
        return text, 0
    return text[:max_chars] + _TRUNCATION_NOTE, len(text) - max_chars


def is_scan(doc: pymupdf.Document, min_chars: int = 20) -> bool:
    """Heuristic scan detection: a page-1 with almost no extractable text is an
    image (goes straight to the T2 vision tier). Matches the corpus analysis's
    page-1 length test."""
    return len(first_page_text(doc).strip()) < min_chars


#: Page-class thresholds, from `tools/ladder_probe.py` `page_classes` (kept):
#: 200 characters is a text page, under 20 is no text layer at all, and half
#: the page covered by images is a scanned page.
#:
#: Two changes from the probe, both measured 2026-10-12 on the eval's 79 text
#: documents and 34 scans and on the 1,341-PDF SFTP mirror:
#:
#: - "ocr" is a page whose text layer is HIDDEN behind a large image: the text
#:   is invisible (render mode 3, PyMuPDF's `ignore-text`) or drawn before an
#:   image covering half the page. That is what an OCR layer over a scan looks
#:   like, and such a layer can be wrong ("OOIYC" for "001YC", Ceramill), so a
#:   null read from it must not count as settled. It is decided by the text's
#:   visibility, not by how much of the page the image covers: 14 digital
#:   certificates and declarations draw their text OVER a full-page or tiled
#:   background image (coverage 0.93 to 1.0, the same as a scan), and an
#:   image-coverage cut flagged all 14 next to the 8 real OCR documents. The
#:   visibility rule flags exactly the 8.
#: - "image" also takes a page with no text layer whose content is VECTOR
#:   DRAWING (text converted to outlines): GC prints whole declarations that way
#:   (EverX Flow, Fuji II Caps: 0 characters, 1-2% image, 470 to 2,037 path
#:   operations a page). The probe called them "sparse", which let a null
#:   settle on an empty text layer. In the mirror, every other page under 20
#:   characters and under half covered by images has at most 25 path operations
#:   (blank or near-blank pages), so 200 separates the two with a wide margin.
TEXT_PAGE_CHARS = 200
NO_TEXT_CHARS = 20
IMAGE_COVERAGE = 0.5
VECTOR_PAGE_PATHS = 200

#: bboxlog kinds that put text on the page where it can be seen.
_VISIBLE_TEXT = frozenset({"fill-text", "stroke-text"})


def _image_coverage(page) -> float:
    """Share of the page area covered by images (0..1; overlaps add up, so it
    is capped)."""
    area = page.rect.width * page.rect.height or 1
    total = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if not r.is_empty:
            total += r.width * r.height
    return min(total / area, 1.0)


def _drawing_order(page) -> tuple[bool, int]:
    """(text hidden behind a large image, number of vector path operations).

    Read from PyMuPDF's bbox log, which lists what the page draws in drawing
    order. The text counts as hidden when most of its runs are invisible or
    drawn before the last image covering half the page (a later image paints
    over them)."""
    log = page.get_bboxlog()
    area = page.rect.width * page.rect.height or 1
    big = [i for i, (kind, r) in enumerate(log)
           if kind == "fill-image"
           and (pymupdf.Rect(r) & page.rect).get_area() >= IMAGE_COVERAGE * area]
    texts = [(i, kind) for i, (kind, _) in enumerate(log) if "text" in kind]
    paths = sum(1 for kind, _ in log if "path" in kind)
    if not big or not texts:
        return False, paths
    hidden = sum(1 for i, kind in texts if kind not in _VISIBLE_TEXT or i < big[-1])
    return 2 * hidden > len(texts), paths


def page_classes(doc: pymupdf.Document) -> list[str]:
    """One class per page, index 0 is page 1:

    - "text":   a real text layer (200+ characters) that is not hidden behind
                an image; a background image under visible text stays "text"
    - "ocr":    a text layer hidden behind a large image (an OCR layer over a scan)
    - "image":  no usable text layer and the content is a scanned image or
                vector drawing (text converted to outlines): under 20
                characters, or under 200 over a page images cover at least
                `IMAGE_COVERAGE` of (a scan with a short visible overlay, a
                stamp or a scanner's caption; review 2026-10-13, F7: as
                "sparse" its scanned content counted as read, which let a
                regulation be proven by citation over it; none such in the
                eval or the 1,336 PDFs of the SFTP mirror on 2026-10-13)
    - "sparse": little text and nothing drawn (cover, blank, divider, a
                signature page)
    """
    out = []
    for page in doc:
        chars = len(_page_text(page).strip())
        hidden, paths = _drawing_order(page)
        if chars >= NO_TEXT_CHARS and hidden:
            out.append("ocr")
        elif chars >= TEXT_PAGE_CHARS:
            out.append("text")
        elif (_image_coverage(page) >= IMAGE_COVERAGE
              or (chars < NO_TEXT_CHARS and paths >= VECTOR_PAGE_PATHS)):
            out.append("image")
        else:
            out.append("sparse")
    return out


#: Classes whose page carries a text layer a text read can use.
TEXT_BEARING = frozenset({"text", "ocr"})


def has_text_page(doc: pymupdf.Document) -> bool:
    """Any page with a text layer, an OCR layer included. The ladder's notion of
    "not a scan" (Task 3 switches `is_scan` callers over; `is_scan` itself is
    unchanged): the measured ladder ran T1 on OCR-layer documents too, so they
    are text documents here; `checks.null_settled` is what distrusts the layer."""
    return any(c in TEXT_BEARING for c in page_classes(doc))


def rasterize_page(doc: pymupdf.Document, page_index: int = 0, dpi: int = 150) -> bytes:
    """Render a page to PNG bytes for the T2 vision tier."""
    pix = doc[page_index].get_pixmap(dpi=dpi)
    return pix.tobytes("png")
