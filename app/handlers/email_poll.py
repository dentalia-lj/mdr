"""email.poll — S2.4 EMAIL inbound (spec: docs/specs/email.md, contract: PRD §4
sibling-producer / §5 EXTRACT, handbook row 6).

Reads a dedicated mailbox, pulls PDF attachments, archives each under our
control (hash-addressed, PRD §9), and enters the spine at extract.doc — the same
door backfill.scan and upload.ingest use. Inbound attachments carry
`group_id=None` and self-identify from their content (REF list / Basic UDI-DI),
exactly like backfill.

Two dedupes, kept distinct (spec §2):
  * processed-email guard: `email_poll_log` keyed by (mailbox, uid_validity,
    uid). A recorded UID is NEVER reprocessed — this is the authority, NOT the
    IMAP \\Seen flag. Per SOURCE EMAIL.
  * content-attachment dedupe: fetch_log + `extract:{content_hash}` — one
    physical file however many emails carry it. Per PHYSICAL DOCUMENT.

Per attachment:
    non-PDF                 -> skipped (reason non-pdf)
    MSDS / business-doc     -> skipped (reason noise-*), reusing the existing
                               deterministic classify_doc_class (no LLM)
    new compliance content  -> archive + extract.doc {archive_url, content_hash,
                               group_id=None, source_url="email:{message_id}"}
    already-seen hash       -> deduped; validate.doc iff a group_id is present
                               (none inbound today), else nothing

Archives are opened before any of that (`expand_containers`), so a supplier
sending `documents.zip` is sending documents rather than one refused blob.

The body is read too, and NOT kept: `app/email_summary.py` compresses it at
poll time and the summary is what reaches the row (migration 030). That is the
one LLM call this handler makes, it is the cheap tier, and it happens after the
attachments so a slow or failing summariser can never cost the poll a document.

Producer only (invariant 1): writes email_poll_log + fetch_log + the archived
file, emits jobs; never document/item_document/evidence. AI: the body summary
only (invariant 12, widened + ratified 2026-08-20) — fetching and classifying
stay deterministic, the classifier being keyword matching over page-1 text.
Idempotent: a recorded UID is skipped; a re-scan emits/archives nothing, and
summarises nothing — including a message whose summary previously failed.
"""

from __future__ import annotations

import hashlib
import logging
import re

from psycopg.types.json import Json

from app.adapters.email import EmailNotConfigured, make_email_adapter
from app.adapters.storage import make_storage_adapter
from app.config import load_config
from app.email_summary import summarise_email
from app.handlers import archiving, register
from app.handlers.email_request import CLOSED_STATES
from app.results import Result

log = logging.getLogger("dentalia.handler.email_poll")


# Containers whose OWN signature disqualifies them, checked before the tolerant
# `%PDF` search below. A ZIP of documents is the commonest shape a supplier
# mailbox carries, and one whose first entry is a PDF puts `%PDF` at offset 45 --
# inside the search window. Measured 2026-08-20 against the fake mailbox: the
# ZIP was archived and an `extract.doc` emitted for it, i.e. an LLM paid to
# parse a file it cannot open.
_CONTAINER_SIGNATURES = (
    b"PK\x03\x04",       # zip (also docx/xlsx/odt, which are zips)
    b"PK\x05\x06",       # empty zip
    b"Rar!\x1a\x07",     # rar
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"\x1f\x8b",         # gzip (.pdf.gz, tar.gz)
    b"\xd0\xcf\x11\xe0",   # OLE2: legacy .doc/.xls/.msg
)


def _is_pdf(att) -> bool:
    """A PDF is decided by content, not by the sender's naming.

    Two rules, in order. A file that announces itself as an ARCHIVE is not a
    PDF, whatever it contains -- we do not unpack, so a PDF inside a zip is not
    reachable and must not be archived as though it were one. Otherwise the
    `%PDF` search stays deliberately tolerant of leading bytes: the format only
    requires the header within the first 1024 bytes, and real-world files do
    arrive with a stray BOM or preamble in front of it.
    """
    head = att.content[:1024]
    if head.startswith(_CONTAINER_SIGNATURES):
        return False
    return b"%PDF" in head


ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")


class _Member:
    """One file lifted out of an archive, shaped like an `EmailAttachment` so
    the rest of the handler cannot tell the difference. `filename` keeps the
    archive it came from -- "documents.zip!declaration.pdf" -- because the
    ledger has to say where a document actually arrived, and "declaration.pdf"
    alone would read as a plain attachment."""

    __slots__ = ("filename", "content_type", "content")

    def __init__(self, filename: str, content_type: str, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.content = content


def _refusal(filename: str, content_type: str, reason: str) -> dict:
    return {"filename": filename, "content_type": content_type,
            "verdict": "skipped", "reason": reason,
            "content_hash": None, "archive_url": None, "disposition": None}


def expand_containers(attachments, cfg) -> tuple[list, list[dict], dict[str, int]]:
    """Turn attachments into the things worth looking at.

    A supplier who sends "documents.zip" is sending documents; the magic-byte
    gate correctly refuses the archive itself, so before this the PDFs inside
    were counted `non-pdf` and lost. One level only -- an archive inside an
    archive is refused rather than followed, because each level multiplies what
    an attacker controls and no legitimate supplier nests them.

    Returns (things to process, refusal records, counters). Every refusal is
    recorded and counted, never silently dropped.
    """
    import zipfile
    import io

    out: list = []
    refusals: list[dict] = []
    counts: dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for att in attachments:
        head = att.content[:4]
        if not head.startswith(ZIP_SIGNATURES):
            out.append(att)
            continue
        if not cfg.email.zip_expand:
            refusals.append(_refusal(att.filename, att.content_type, "archive-not-expanded"))
            bump("skipped_archive")
            continue

        bump("archives")
        try:
            zf = zipfile.ZipFile(io.BytesIO(att.content))
            infos = [i for i in zf.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError):
            refusals.append(_refusal(att.filename, att.content_type, "archive-unreadable"))
            bump("archive_unreadable")
            continue

        if len(infos) > cfg.email.zip_max_members:
            # Counted and named rather than truncated silently: an archive with
            # 4.000 entries is not a document delivery and a human should look.
            refusals.append(_refusal(
                att.filename, att.content_type,
                f"archive-too-many-members ({len(infos)} > {cfg.email.zip_max_members})"))
            bump("archive_too_many_members")
            continue

        total_cap = cfg.email.zip_max_total_mb * 1024 * 1024
        member_cap = cfg.email.zip_max_member_mb * 1024 * 1024
        total = 0
        members: list[_Member] = []
        refused_here: list[dict] = []
        for info in infos:
            name = f"{att.filename}!{info.filename}"
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size > member_cap:
                refused_here.append(_refusal(name, "application/zip", "archive-member-too-large"))
                bump("archive_member_too_large")
                continue
            if ratio > cfg.email.zip_max_ratio:
                # The zip bomb guard. Decided on the DECLARED sizes, before a
                # single byte is decompressed -- checking after reading is not
                # a guard, it is a memory exhaustion.
                refused_here.append(_refusal(name, "application/zip", "archive-suspicious-ratio"))
                bump("archive_suspicious_ratio")
                continue
            total += info.file_size
            if total > total_cap:
                refused_here.append(_refusal(name, "application/zip", "archive-total-too-large"))
                bump("archive_total_too_large")
                break
            try:
                blob = zf.read(info)
            except (zipfile.BadZipFile, RuntimeError, OSError):
                # RuntimeError is what zipfile raises for an encrypted entry.
                refused_here.append(_refusal(name, "application/zip", "archive-member-unreadable"))
                bump("archive_member_unreadable")
                continue
            if blob[:4].startswith(ZIP_SIGNATURES):
                refused_here.append(_refusal(name, "application/zip", "archive-nested"))
                bump("archive_nested")
                continue
            if b"%PDF" not in blob[:1024]:
                # Counted, not row-recorded: a .docx is a zip of a dozen XML
                # parts and one ledger row each would bury the actual documents.
                # The count is exact and lands on the job result, and an archive
                # that yields NOTHING still gets its own row below.
                bump("archive_member_not_pdf")
                continue
            members.append(_Member(name, "application/pdf", blob))

        if not members and not refused_here:
            # An archive we opened and found nothing usable in -- a .docx is a
            # zip too, and so is every .xlsx a supplier attaches. Recorded
            # rather than dropped: an attachment that produces no row at all
            # reads as "there was no attachment", which is the one thing the
            # ledger must never say.
            refusals.append(_refusal(att.filename, att.content_type,
                                     "archive-no-usable-members"))
            bump("archive_no_usable_members")

        refusals.extend(refused_here)
        out.extend(members)
        counts["archive_members"] = counts.get("archive_members", 0) + len(members)

    return out, refusals, counts


def _classify(pdf_bytes: bytes) -> str:
    """Reuse the deterministic page-1 doc-class classifier EXTRACT already
    short-circuits on (business-doc/msds -> no LLM). Fail-open: a PDF pymupdf
    cannot open classifies `unknown` and proceeds, so a genuine cert with an odd
    structure is never dropped at the door (EXTRACT records it for a human)."""
    try:
        import pymupdf

        from app.extract import pdf as pdfmod
        from app.extract.t0_templates import classify_doc_class

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = pdfmod.first_page_text(doc)
        finally:
            doc.close()
        return classify_doc_class(text)
    except Exception as exc:  # pragma: no cover - defensive: broken PDF
        log.warning("email.poll: could not classify attachment (%s); proceeding", exc)
        return "unknown"


_NOISE_REASON = {"msds": "noise-msds", "business-doc": "noise-business"}


def _already_processed(conn, mailbox: str, uid_validity: str, uid: str,
                       message_id: str | None) -> bool:
    """The durable reprocess guard (spec §2). Primary key is the IMAP-native
    (mailbox, uid_validity, uid). A `message_id` match WITHIN THE SAME MAILBOX is
    a second guard: if UIDVALIDITY resets (a mailbox migration/rebuild), the same
    message reappears under a new UID, and Message-ID — globally unique per
    message — still recognises it. Scoped to the mailbox so the same message
    delivered to two polled mailboxes keeps its own provenance row."""
    mid = (message_id or "").strip()
    return conn.execute(
        "SELECT 1 FROM email_poll_log "
        "WHERE mailbox=%s AND ( (uid_validity=%s AND imap_uid=%s) "
        "                       OR (%s <> '' AND message_id=%s) ) LIMIT 1",
        (mailbox, uid_validity, uid, mid, mid),
    ).fetchone() is not None


#: The chase's reference as `email_request.subject_for` stamps it.
_REFERENCE = re.compile(r"\[DENT-(\d+)\]")
_ADDR_DOMAIN = re.compile(r"@([A-Za-z0-9.-]+)")


def _domain(text: str | None) -> str | None:
    m = _ADDR_DOMAIN.search(text or "")
    return m.group(1).lower().rstrip(".") if m else None


def _match_request(conn, msg, own_domain: str | None) -> tuple[int | None, str | None]:
    """Which `renewal_request` a message answers, and how we know.

    `reference`: the subject carries `[DENT-{id}]` naming a request that
    exists. The system never sends, so it never learns the Message-ID a reply
    would cite in In-Reply-To; the subject token is the handle a reply keeps.

    `sender`: no usable token, but the sender's domain is a contact domain of
    exactly ONE manufacturer-request pair still open. Two open requests, or
    none, is no match: a guess between two chases is worse than no link. The
    polled mailbox's own domain never matches, so a forward from the office
    names no supplier.
    """
    m = _REFERENCE.search(msg.subject or "")
    if m:
        row = conn.execute("SELECT id FROM renewal_request WHERE id=%s",
                           (int(m.group(1)),)).fetchone()
        if row is not None:
            return row["id"], "reference"
    domain = _domain(msg.from_addr)
    if domain is None or domain == own_domain:
        return None, None
    rows = conn.execute(
        "SELECT r.id FROM renewal_request r "
        "JOIN manufacturer m ON m.canonical_name = r.manufacturer "
        "WHERE r.state <> ALL(%s) AND EXISTS ("
        "  SELECT 1 FROM unnest(m.contact_emails) e "
        "   WHERE lower(split_part(e, '@', 2)) = %s)",
        (list(CLOSED_STATES), domain)).fetchall()
    if len(rows) == 1:
        return rows[0]["id"], "sender"
    return None, None


def _record(conn, adapter_mailbox, msg, *, had_attachments, attachments, emitted,
            summary, renewal_request_id=None):
    """The ledger row. Note what is NOT here: `msg.body_text`. The body is read
    into memory, summarised, and dropped -- only `summary` persists (migration
    030, invariant 12). Writing the row is also what makes the summary run
    exactly once: the UID key means this message is never polled again, and a
    failed summary is recorded with its status rather than left to retry."""
    conn.execute(
        "INSERT INTO email_poll_log "
        "(mailbox, uid_validity, imap_uid, message_id, from_addr, subject, "
        " received_at, in_reply_to, references_hdr, had_attachments, "
        " attachments, emitted_jobs, body_summary, summary_intent, "
        " summary_status, summary_model, summary_input_tokens, "
        " summary_output_tokens, summary_cost_usd, renewal_request_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (mailbox, uid_validity, imap_uid) DO NOTHING",
        (adapter_mailbox, msg.uid_validity, msg.uid, msg.message_id or None,
         msg.from_addr or None, msg.subject or None, msg.received_at,
         msg.in_reply_to, msg.references, had_attachments,
         Json(attachments), Json(emitted),
         summary.summary, summary.intent, summary.status, summary.model,
         summary.input_tokens, summary.output_tokens, summary.cost_usd,
         renewal_request_id),
    )


def handle_email_poll(conn, job, *, adapter=None, store=None, llm=None) -> dict:
    cfg = load_config()
    if adapter is None:
        adapter = make_email_adapter(cfg)

    r = Result()

    try:
        messages = adapter.fetch_unseen(limit=cfg.email.poll_max_messages)
    except EmailNotConfigured as exc:
        # A keyless poll is a skipped rung, not a dead job (mirrors DISCOVER's
        # SearchNotConfigured). The scheduler flag is off until G8 anyway.
        log.info("email.poll: %s", exc)
        return {**r.as_dict(), "outcome": "not-configured"}

    mailbox = adapter.mailbox_id
    own_domain = _domain(mailbox)

    for msg in messages:
        r.count("messages_seen")

        if _already_processed(conn, mailbox, msg.uid_validity, msg.uid, msg.message_id):
            # Durable reprocess guard — authority over \Seen (spec §2).
            r.count("already_processed")
            continue

        att_records: list[dict] = []
        emitted: list[dict] = []

        # Archives are opened here, once, so everything below sees one flat list
        # of files and cannot tell a zip member from a plain attachment.
        candidates, refusals, container_counts = expand_containers(msg.attachments, cfg)
        for key, n in container_counts.items():
            r.count(key, n)
        att_records.extend(refusals)

        for att in candidates:
            r.count("attachments")

            if not _is_pdf(att):
                r.count("skipped_non_pdf")
                att_records.append({
                    "filename": att.filename, "content_type": att.content_type,
                    "verdict": "skipped", "reason": "non-pdf",
                    "content_hash": None, "archive_url": None, "disposition": None,
                })
                continue
            r.count("pdf")

            doc_class = _classify(att.content)
            if doc_class in _NOISE_REASON:
                r.count("skipped_noise")
                att_records.append({
                    "filename": att.filename, "content_type": att.content_type,
                    "verdict": "skipped", "reason": _NOISE_REASON[doc_class],
                    "content_hash": None, "archive_url": None, "disposition": None,
                })
                continue

            content_hash = hashlib.sha256(att.content).hexdigest()
            url_norm = f"email:{content_hash}"
            archiving.ledger_upsert(conn, url_norm, etag=None, last_modified=None,
                                    content_hash=content_hash, source="email")

            group_id = None  # inbound self-identifies (spec §1); no thread group yet
            rev = archiving.hash_extracted_rev(conn, content_hash)
            if rev is not None:
                # Content already extracted. With no requesting group there is
                # nothing to link (C3 needs a group) — a future reply that
                # resolves to a renewal request's group takes the validate.doc
                # path here (spec §7/§9).
                if group_id is not None:
                    archiving.link_existing_doc(conn, url_norm, content_hash)
                    archiving.emit_validate(conn, content_hash, rev, group_id)
                    emitted.append({"type": "validate.doc",
                                    "dedupe_key": f"validate:{content_hash}:{rev}:{group_id}"})
                r.count("deduped")
                att_records.append({
                    "filename": att.filename, "content_type": att.content_type,
                    "verdict": "useful", "reason": None,
                    "content_hash": content_hash, "archive_url": None,
                    "disposition": "deduped",
                })
                continue

            if store is None:
                store = make_storage_adapter(cfg)
            archive_url = store.put(
                att.content,
                archiving.archive_path("unknown", content_hash, att.filename,
                                       "application/pdf"),
            )
            archiving.emit_extract(conn, archive_url, content_hash, group_id,
                                   source_url=f"email:{msg.message_id}")
            emitted.append({"type": "extract.doc",
                            "dedupe_key": f"extract:{content_hash}"})
            r.count("archived")
            att_records.append({
                "filename": att.filename, "content_type": att.content_type,
                "verdict": "useful", "reason": None,
                "content_hash": content_hash, "archive_url": archive_url,
                "disposition": "archived",
            })

        # AFTER the attachments, so a summarisation that somehow costs time or
        # money never delays archiving the documents -- those are the point of
        # the poll; the summary is context around them.
        summary = summarise_email(msg.body_text, msg.subject, cfg=cfg, client=llm)
        r.count(f"summary_{summary.status.replace('-', '_')}")

        request_id, basis = _match_request(conn, msg, own_domain)
        r.count(f"reply_linked_by_{basis}" if basis else "reply_unlinked")

        _record(conn, mailbox, msg,
                had_attachments=bool(msg.attachments),
                attachments=att_records, emitted=emitted, summary=summary,
                renewal_request_id=request_id)

        adapter.mark_seen(msg.uid)  # courtesy; the ledger row is the real guard

    return {**r.as_dict(), "outcome": "polled", "mailbox": mailbox}


register("email.poll", handle_email_poll)
