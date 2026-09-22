# app/handlers/archiving.py
"""Archive-path, fetch-ledger, and spine-emit helpers shared by FETCH and
UPLOAD (and, later, email.poll). One home so the archive layout and the
extract/validate dedupe keys cannot diverge between producers — same rationale
as app/urls.py owning the fetch dedupe key for DISCOVER + FETCH.

Producer-side only: writes fetch_log, emits jobs. Never document/item_document/
evidence (invariant 1).
"""
from __future__ import annotations

import re
import urllib.parse

from app import queue


# --- is this a document at all? --------------------------------------------
#
# Lifted here from fetch.py on 2026-09-02 (`[gate-covers-only-fetch]`) so every
# producer that can write into the archive shares one answer. FETCH learned the
# rule the expensive way: `bredent.com/.../Declaration_of_Conformity.pdf`
# answers `200 text/html` with 301.268 bytes of site navigation, and until the
# gate landed that was archived, paginated by PyMuPDF, typed `IFU` at
# confidence 1.00 off a menu item, and approved into production by a human.
#
# Bytes, not the header and not the filename, in both directions: that URL ends
# in `.pdf` and lies, while other servers send `application/octet-stream` for
# genuine PDFs.

#: Byte signatures we accept as a document. PDF is what the pipeline parses;
#: the office formats are here because the SFTP corpus contains a handful
#: (`.xlsx`/`.docx` coverage-map indexes, and 3 loose files measured
#: 2026-09-02) and refusing them would be a new regression, not a fix.
_DOCUMENT_MAGIC = (
    b"%PDF",              # PDF
    b"PK\x03\x04",        # zip container: docx / xlsx / odt
    b"\xd0\xcf\x11\xe0",  # OLE2: legacy .doc / .xls
)

#: Leading whitespace is stripped before matching. `dentalsky.com` answers a
#: body that literally begins `" <!do"`, so a bare `startswith` would have
#: passed it (measured 2026-09-02).
_MAGIC_SCAN = 8


def is_document(body: bytes) -> bool:
    """True if `body` opens with a signature we archive.

    Deliberately stricter than `email_poll._is_pdf`, which scans the first 1024
    bytes because a mail attachment may legitimately carry a preamble. Bodies
    reaching the archive from FETCH, UPLOAD and BACKFILL do not, and a wide
    window is what lets an HTML page with `%PDF` in its text sneak through.
    """
    if not body:
        return False
    head = body[:_MAGIC_SCAN].lstrip()
    return any(head.startswith(m) for m in _DOCUMENT_MAGIC)


def magic_of(body: bytes) -> str:
    """The leading bytes, printable, for a refusal message or job result."""
    return body[:_MAGIC_SCAN].lstrip()[:5].decode("latin-1", "replace")


# --- archive path (PRD §9) --------------------------------------------------

_TYPE_TOKENS = [
    ("ifu", "ifu"),
    ("declaration", "doc"),
    ("conformity", "doc"),
    ("/doc", "doc"),
    ("cert", "ec"),
    ("iso", "iso"),
]


def _guess_type_dir(url, content_type):
    """Coarse HUMAN-facing bucket only — FETCH cannot know the authoritative type
    (DoC/EC/IFU/ISO); EXTRACT/GATE set `document.type`. The archive is hash-
    addressed, so this dir is cosmetic (spec §3.4)."""
    u = url.lower()
    for token, d in _TYPE_TOKENS:
        if token in u:
            return d
    return "unknown"


def _sanitize(text):
    return re.sub(r"[^\w.-]+", "_", text).strip("_") or "unknown"


def _filename(url, content_type):
    base = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    base = _sanitize(base)
    if base in ("", "unknown"):
        ext = ".pdf" if content_type and "pdf" in content_type else ".bin"
        return f"doc{ext}"
    if "." not in base and content_type and "pdf" in content_type:
        base += ".pdf"
    return base


def archive_path(manufacturer, content_hash, url, content_type):
    return (
        f"{_sanitize(manufacturer)}/{_guess_type_dir(url, content_type)}/"
        f"{content_hash[:12]}__{_filename(url, content_type)}"
    )


def manufacturer_for_group(conn, group_id):
    if group_id is None:
        return "unknown"
    row = conn.execute(
        "SELECT canonical_manufacturer FROM item_group WHERE group_id=%s", (group_id,)
    ).fetchone()
    return (row and row["canonical_manufacturer"]) or "unknown"


# --- ledger -----------------------------------------------------------------

def ledger_upsert(conn, url_norm, *, etag, last_modified, content_hash, source):
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, etag, last_modified, content_hash, "
        "fetched_at, last_checked_at, source) VALUES (%s,%s,%s,%s, now(), now(), %s) "
        "ON CONFLICT (url_normalized) DO UPDATE SET "
        "etag=EXCLUDED.etag, last_modified=EXCLUDED.last_modified, "
        "content_hash=EXCLUDED.content_hash, fetched_at=now(), last_checked_at=now(), "
        "source=EXCLUDED.source",
        (url_norm, etag, last_modified, content_hash, source),
    )


def hash_extracted_rev(conn, content_hash):
    """Latest extract_rev for content_hash, or None if never extracted (C3)."""
    row = conn.execute(
        "SELECT MAX(extract_rev) AS rev FROM extraction_attempt WHERE content_hash=%s",
        (content_hash,),
    ).fetchone()
    return row["rev"] if row else None


def link_existing_doc(conn, url_norm, content_hash):
    """Best-effort: point this URL's ledger row at the document with that hash
    (may not exist yet — GATE creates documents; then the C3 validate path runs)."""
    conn.execute(
        "UPDATE fetch_log SET doc_id = "
        "(SELECT doc_id FROM document WHERE content_hash=%s ORDER BY doc_id LIMIT 1) "
        "WHERE url_normalized=%s AND doc_id IS NULL",
        (content_hash, url_norm),
    )


def archive_handle_for_hash(conn, content_hash):
    """Our stored copy's handle for an already-extracted hash, if the registry
    holds one (GATE wrote document.archive_url when the first candidate for
    this content landed). None when no document exists yet — the direct
    extract→validate chain for the same hash carries the handle in its own
    payload and heals the registry when it lands."""
    row = conn.execute(
        "SELECT archive_url FROM document WHERE content_hash=%s ORDER BY doc_id LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row["archive_url"] if row else None


# --- emits ------------------------------------------------------------------

def emit_validate(conn, content_hash, rev, group_id):
    # The C3 re-entry payload carries the stored copy's handle when the
    # registry already holds one (defect fix, 2026-08-24): a validate.doc
    # without archive_url made GATE fall back to fetch_log.url_normalized —
    # for a live fetch that is the remote SOURCE url, and the gate.candidate
    # refreshed document.archive_url plus the evidence rows to it (measured:
    # RENFERT job 35563 / doc 843). Additive payload field; consumers tolerate
    # its absence (first-candidate-in-flight case).
    payload = {"content_hash": content_hash, "group_id": group_id, "extract_rev": rev}
    handle = archive_handle_for_hash(conn, content_hash)
    if handle:
        payload["archive_url"] = handle
    queue.enqueue(
        conn, "validate.doc",
        payload,
        dedupe_key=f"validate:{content_hash}:{rev}:{group_id}",
    )


def emit_extract(conn, archive_url, content_hash, group_id, source_url):
    queue.enqueue(
        conn, "extract.doc",
        {"archive_url": archive_url, "content_hash": content_hash,
         "group_id": group_id, "source_url": source_url},
        dedupe_key=f"extract:{content_hash}",
    )
