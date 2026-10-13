"""PDF access for EXTRACT: text with page markers, rasterization, scan detection.

Engine: PyMuPDF (text + raster). pdfplumber is used separately in
`t0_templates` for REF-list tables. T0 layout templates stay engine-agnostic
data; this module is the only place that touches PyMuPDF directly.

`archive_url` handling here is an S0.2 stopgap for the real StorageAdapter
(S1.4): a filesystem path or `file://` URL resolves to a local file so EXTRACT
can run over the local seed corpus.
"""

from __future__ import annotations

import re
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


#: Annotation flags under which a viewer does not draw the annotation (PDF
#: 32000-1, 12.5.3): Hidden (bit 2) and NoView (bit 6). MuPDF skips them too.
_NOT_DRAWN = 2 | 32
_REF = re.compile(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R")
_INLINE_IMAGE = re.compile(rb"(?:^|\s)BI\s*/")
#: Forms followed inside one appearance, against a malformed self-reference.
_MAX_FORMS = 16


def annotation_image_pages(doc: pymupdf.Document) -> tuple[int, ...]:
    """1-based pages where a drawn annotation paints an IMAGE in its normal
    appearance: printed content that no text layer holds.

    The case (Task 8, 2026-10-13): VOCO's e-signed declarations print their
    issue date ("Cuxhaven, 14.01.2026") only inside the signature field's
    appearance, which paints one image (Certifaction's stamp) on an otherwise
    sparse page. The page text has no date and no date label, so T1's empty
    `validity_from` passed the null rule and the date was lost.

    Text an appearance WRITES is not this case: MuPDF runs a page's
    annotations when it extracts text, so `get_text` already holds it
    (measured on the SFTP mirror, 2026-10-13: no appearance with text that
    `get_text` missed). An image the page content itself places (a pasted
    signature, a stamp scanned into the page) is not an annotation and not
    found here (`docs/dev/limits.md`).

    Links are passed over: a link points somewhere, it prints nothing (the
    mirror's are all bare, some written as direct dictionaries with no object
    number to read them by). Hidden, never-viewed and zero-area annotations
    are not drawn and do not count. An annotation that cannot be read counts,
    so that a broken file costs an image read rather than an absence proved
    over content nobody saw."""
    out = []
    for n, page in enumerate(doc, start=1):
        if any(_paints_image(doc, xref) for xref, kind, _ in page.annot_xrefs()
               if kind != pymupdf.PDF_ANNOT_LINK):
            out.append(n)
    return tuple(out)


def _paints_image(doc: pymupdf.Document, annot_xref: int) -> bool:
    try:
        kind, flags = doc.xref_get_key(annot_xref, "F")
        if kind == "int" and int(flags) & _NOT_DRAWN:
            return False
        kind, rect = doc.xref_get_key(annot_xref, "Rect")
        if kind == "array":                # corners in any order: normalized first
            corners = map(float, rect.strip("[]").split()[:4])
            if pymupdf.Rect(*corners).normalize().is_empty:
                return False
        kind, ap = doc.xref_get_key(annot_xref, "AP/N")
        if kind == "xref":
            forms = [int(ap.split()[0])]
        elif kind == "dict":               # one appearance per state (a check box)
            forms = [int(x) for _, x in _REF.findall(ap)]
        else:
            return False                   # no appearance: nothing drawn
        return any(_form_paints_image(doc, x, set()) for x in forms)
    except Exception:  # noqa: BLE001 -- unreadable: counted, see the docstring above
        return True


def _form_paints_image(doc: pymupdf.Document, xref: int, seen: set[int]) -> bool:
    """The form XObject paints an image: inline, or an Image XObject it invokes
    with `Do`, itself or through a form it invokes."""
    if xref in seen or len(seen) >= _MAX_FORMS:
        return False
    seen.add(xref)
    stream = doc.xref_stream(xref) or b""
    if _INLINE_IMAGE.search(stream):
        return True
    kind, xobjects = doc.xref_get_key(xref, "Resources/XObject")
    if kind == "xref":
        kind, xobjects = "dict", doc.xref_object(int(xobjects.split()[0]))
    if kind != "dict":
        return False
    for name, ref in _REF.findall(xobjects):
        if not re.search(b"/" + re.escape(name.encode()) + rb"\s*Do\b", stream):
            continue
        subtype = doc.xref_get_key(int(ref), "Subtype")[1]
        if subtype == "/Image" or (subtype == "/Form"
                                   and _form_paints_image(doc, int(ref), seen)):
            return True
    return False


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
