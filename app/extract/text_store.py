"""Parsed-text capture: what EXTRACT actually read out of a PDF.

Written once per content hash at the start of extraction. Three consumers:
re-extraction after a prompt or model change (no re-parse), mechanical
verification of evidence.verbatim against the source, and human/SQL search
over the corpus without opening a file.

Provenance is explicit. 'pdf-text' is a deterministic PyMuPDF read.
'none' is a scan with no text layer: the row is still written, so the count
of unrecoverable documents is a number we have rather than a guess.
"""
from __future__ import annotations

import logging

from app.extract import pdf as pdfutil

log = logging.getLogger(__name__)

ENGINE = "pymupdf"


def store_text(conn, content_hash: str, archive_url: str, doc=None) -> dict:
    """Parse `archive_url` and upsert its text. Returns a summary dict for the
    job result. Never raises on an unreadable PDF: extraction proper owns that
    failure, and losing the sidecar must not dead-letter a document.

    Short-circuits when a row already exists for `content_hash`: the archived
    bytes are immutable and keyed by content hash, so an existing row is
    current by definition, and re-parsing it would only re-derive and
    re-write the same content. This is what makes re-extraction cheap -- a
    batch job that self-defers across several poll cycles calls this once per
    poll, and only the first call actually opens and reads the PDF.

    `doc` is an already-open document for the same archived bytes. EXTRACT needs
    that document for the tier ladder anyway, so it opens once and passes it
    here instead of making this function open and parse the identical immutable
    file a second time within one execution. A document passed in belongs to the
    caller and is left open; one opened here is closed here. It is only ever
    touched when the short-circuit above does not fire, so a caller that already
    has one loses nothing by passing it."""
    existing = conn.execute(
        "SELECT source, pages, chars FROM document_text WHERE content_hash=%s",
        (content_hash,),
    ).fetchone()
    if existing is not None:
        return {"source": existing["source"], "pages": existing["pages"],
                "chars": existing["chars"]}

    own_doc = doc is None
    if own_doc:
        try:
            doc = pdfutil.open_doc(archive_url)
        except Exception as exc:
            log.warning("document_text: cannot open %s: %s", archive_url, exc)
            return {"source": "none", "pages": 0, "chars": 0}

    try:
        if pdfutil.is_scan(doc):
            source, content, pages = "none", "", doc.page_count
        else:
            source = "pdf-text"
            content = pdfutil.full_text(doc, page_markers=True)
            pages = doc.page_count
    finally:
        if own_doc:
            doc.close()

    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (content_hash) DO UPDATE SET "
        "source=EXCLUDED.source, engine=EXCLUDED.engine, pages=EXCLUDED.pages, "
        "chars=EXCLUDED.chars, content=EXCLUDED.content, built_at=now()",
        (content_hash, source, ENGINE, pages, len(content), content),
    )
    return {"source": source, "pages": pages, "chars": len(content)}
