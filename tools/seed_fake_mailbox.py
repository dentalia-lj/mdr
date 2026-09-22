"""Seed the fake compliance mailbox for the S2.4 `email.poll` live path.

    docker compose --profile email-dev up -d greenmail
    python tools/seed_fake_mailbox.py

Why this exists: every `email.poll` test passes against `FakeEmailAdapter`, so
the IMAP wire path in `app/adapters/email.py` (login, SELECT, UIDVALIDITY
capture, `UID SEARCH UNSEEN`, `UID FETCH (RFC822)`) has never executed -- it is
`pragma: no cover` end to end. GreenMail is a real IMAP server, so pointing the
worker at a mailbox this script has filled exercises that path with no client
dependency (GAP G8).

GreenMail storage is IN-MEMORY: restarting the container empties the mailbox,
which is why this script -- not the container -- is the source of truth for
what the mailbox contains.

Dev-time tooling: `tools/` is excluded from the wheel and is never part of the
pipeline. Nothing here imports `app`, and nothing leaves this machine -- the
manufacturer-looking sender addresses are strings handed to a local fake.

Every expected verdict below was verified against the real classifier
(`app.extract.t0_templates.classify_doc_class`) on the real corpus file, not
assumed: the GC SILVERMIX declaration really is a 0-character scanned PDF that
classifies `unknown`, and `unknown` is NOT noise -- the handler drops only
`msds` and `business-doc`.
"""

from __future__ import annotations

import argparse
import mimetypes
import pathlib
import smtplib
import sys
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime

DEFAULT_HOST = "127.0.0.1"   # NOT "localhost": on WSL that resolves ::1 first
DEFAULT_PORT = 3025          # GreenMail SMTP under -Dgreenmail.setup.test.all
DEFAULT_TO = "dokumentacija@dentalia.si"
DEFAULT_CORPUS = "imports/dentalia-sftp"

# Corpus files, relative to --corpus. Classifications are measured, see above.
DOC_PDF = "NEODENT/neodent DOC/neodent vsadki, izjava o skladnosti.pdf"
MSDS_PDF = "GC/MSDS/gc-tissue-conditioner-liquid-sds.pdf"
SCANNED_PDF = "GC/DOC/Declaration of conformity_EN SILVERMIX.pdf"

# Fixed dates so a re-seed produces byte-identical messages (see --fresh).
_SENT = {
    "doc": "Tue, 18 Aug 2026 09:14:02 +0200",
    "msds": "Tue, 18 Aug 2026 11:40:55 +0200",
    "scanned": "Wed, 19 Aug 2026 08:02:31 +0200",
    "trap": "Wed, 19 Aug 2026 10:21:07 +0200",
    "bare": "Wed, 19 Aug 2026 16:45:12 +0200",
    "dup": "Thu, 20 Aug 2026 07:55:40 +0200",
}


def synthetic_pdf(title: str, lines: list[str]) -> bytes:
    """A real, valid PDF built on the spot.

    The corpus is fully archived for every manufacturer we currently chase, so
    a mailed corpus file always hits the seen-hash branch and can never
    exercise archive + extract. A generated PDF is a new hash by construction,
    and its text is ours to control -- which is the only way to mail in a
    document that classifies `compliance-doc` and carries the fields EXTRACT
    looks for.

    Deterministic: same arguments -> same bytes -> same content hash, so a
    re-seed dedupes exactly like a re-sent corpus file.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 80), title, fontsize=16)
    y = 120
    for line in lines:
        page.insert_text((60, y), line, fontsize=10)
        y += 16
    # No metadata and no xref timestamps: PyMuPDF stamps a creation date by
    # default, which would make every run a different hash.
    doc.set_metadata({})
    blob = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return blob


def _doc_pdf(manufacturer: str, ref: str, issued: str) -> bytes:
    """A declaration of conformity with the markers the T0 classifier keys on,
    so this lands as `compliance-doc` and not as `unknown`."""
    return synthetic_pdf("EU DECLARATION OF CONFORMITY", [
        f"Manufacturer: {manufacturer}",
        "This declaration of conformity is issued under the sole responsibility",
        "of the manufacturer.",
        "Regulation (EU) 2017/745 on medical devices (MDR)",
        f"REF: {ref}",
        f"Date of issue: {issued}",
        "Class: IIa",
        "Notified Body: 0123",
    ])


def _shape_cases() -> list[dict]:
    """Shapes a real supplier mailbox contains that the six fixtures do not.

    Every attachment here is generated, so none of them can collide with the
    archive and each exercises a path the corpus fixtures cannot reach.
    """
    import io
    import zipfile

    replacement = _doc_pdf("Ivoclar Vivadent AG", "642453", "2026-08-01")

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("declaration.pdf", _doc_pdf("Zip Sender GmbH", "Z-1", "2026-01-01"))

    # Big, cheaply. Building bulk with 9.000 insert_text calls does not finish
    # in any reasonable time (each call re-serialises a growing content stream),
    # so the padding is a trailing PDF comment instead: bytes after %%EOF are
    # tolerated, the document still opens and still classifies, and generating
    # it is instant.
    _base = synthetic_pdf("EU DECLARATION OF CONFORMITY", [
        "Regulation (EU) 2017/745 on medical devices (MDR)",
        "Manufacturer: Oversize AG",
        "REF: OS-00001",
    ])
    oversize = _base + b"\n%" + b"padding " * (12 * 1024 * 1024 // 8)

    inner = EmailMessage()
    inner["From"] = "regulatory@ivoclar.example"
    inner["To"] = "sales@dobavitelj.example"
    inner["Subject"] = "Declaration of conformity, Nexco"
    inner["Message-ID"] = "<inner-doc@ivoclar.example>"
    inner.set_content("Please find the declaration attached.")
    inner.add_attachment(_doc_pdf("Ivoclar Vivadent AG", "640485", "2026-08-02"),
                         maintype="application", subtype="pdf",
                         filename="nexco-doc-2026.pdf")

    return [
        {
            "key": "reply",
            "from": "regulatory@ivoclar.example",
            "subject": "RE: MDR DOCUMENTS",
            "body": "Dear Nataša,\n\nplease find the renewed declaration attached.",
            "files": [(None, "ivoclar-doc-2026.pdf", replacement)],
            "headers": {"In-Reply-To": "<seed-doc@dentalia.si>",
                        "References": "<seed-doc@dentalia.si>"},
            "expect": "new hash -> archived + extract.doc; In-Reply-To/References "
                      "recorded on email_poll_log (spec §9 hook, matcher not built)",
            "_date": "Thu, 20 Aug 2026 12:00:00 +0200",
            "_msgid": "<seed-reply@ivoclar.example>",
        },
        {
            "key": "forward",
            "from": "sales@dobavitelj.example",
            "subject": "FW: Declaration of conformity, Nexco",
            "body": "Forwarding what the manufacturer sent us.",
            "files": [(None, "forwarded.eml", inner)],
            "expect": "the PDF nested inside a forwarded message/rfc822 is found, "
                      "and so is the forwarded BODY -- verified 2026-08-20, the "
                      "summary read the manufacturer's words, not the wrapper's",
            "_date": "Thu, 20 Aug 2026 12:05:00 +0200",
            "_msgid": "<seed-forward@dobavitelj.example>",
        },
        {
            "key": "zip",
            "from": "docs@dobavitelj.example",
            "subject": "Dokumentacija v arhivu",
            "body": "V prilogi arhiv z dokumenti.",
            "files": [(None, "documents.zip", zbuf.getvalue())],
            "expect": "the archive is OPENED (2026-08-20): the magic-byte gate "
                      "refuses the zip itself, then expand_containers yields its "
                      "members as documents.zip!inner.pdf. Was `skipped: non-pdf` "
                      "until that landed, which silently lost every zipped doc",
            "_date": "Thu, 20 Aug 2026 12:10:00 +0200",
            "_msgid": "<seed-zip@dobavitelj.example>",
        },
        {
            "key": "oversize",
            "from": "regulatory@oversize.example",
            "subject": "Declaration of conformity (large file)",
            "body": "Attached.",
            "files": [(None, "oversize-doc.pdf", oversize)],
            "expect": "no size cap exists in the handler — expected to archive; "
                      "measured 2026-08-20: 12 MB archived without complaint. "
                      "Body is 9 chars, and since 2026-08-21 that is summarised "
                      "like any other body -- the floor is 0, because a blank "
                      "summary cell reads as \"nothing to say\"",
            "_date": "Thu, 20 Aug 2026 12:15:00 +0200",
            "_msgid": "<seed-oversize@oversize.example>",
        },
    ]


def _cases(corpus: pathlib.Path) -> list[dict]:
    """The six messages. Each names the branch of the handler it exercises, so
    a live run can be diffed against `expect` rather than eyeballed."""
    doc = corpus / DOC_PDF
    msds = corpus / MSDS_PDF
    scanned = corpus / SCANNED_PDF
    return [
        {
            "key": "doc",
            "from": "regulatory@neodent.example",
            "subject": "Izjava o skladnosti - Neodent vsadki (MDR)",
            "body": "Spostovani,\n\nv prilogi posiljamo izjavo o skladnosti za "
                    "vsadke.\n\nLep pozdrav,\nRegulatory Affairs",
            "files": [(doc, None, None)],
            # MEASURED 2026-08-20: the backfill had already archived this exact
            # file, so the poll took the seen-hash branch (deduped, nothing
            # emitted) -- correct per invariant 6, and the reason --extra exists.
            "expect": "compliance-doc -> useful; deduped if the corpus backfill "
                      "already archived it, else archived + extract.doc",
        },
        {
            "key": "msds",
            "from": "info@gceurope.example",
            "subject": "Safety data sheet - Tissue Conditioner Liquid",
            "body": "Dear customer,\n\nplease find the SDS attached.\n\nGC Europe",
            "files": [(msds, None, None)],
            "expect": "skipped: noise-msds (classifier says msds, no archive)",
        },
        {
            "key": "scanned",
            "from": "quality@gceurope.example",
            "subject": "Declaration of conformity SILVERMIX",
            "body": "Attached, as requested.",
            "files": [(scanned, None, None)],
            "expect": "useful (0-char scan classifies `unknown`, and `unknown` "
                      "is not noise); deduped if already backfilled",
        },
        {
            "key": "trap",
            "from": "prodaja@dobavitelj.example",
            "subject": "Cenik 2026",
            # A file NAMED .pdf and DECLARED application/pdf that is not a PDF.
            # The handler decides by the %PDF magic bytes, so both lies lose.
            "files": [(None, "cenik-2026.pdf", b"PK\x03\x04 not a pdf at all")],
            "body": "V prilogi cenik.",
            "expect": "skipped: non-pdf (magic-byte gate beats filename AND "
                      "declared content-type)",
        },
        {
            "key": "bare",
            "from": "tajnistvo@dobavitelj.example",
            "subject": "Re: Zahteva za dokumentacijo",
            "body": "Dokumentacijo vam posljemo v prihodnjem tednu.",
            "files": [],
            "expect": "ledger row only: had_attachments=false, no jobs",
        },
        {
            "key": "dup",
            "from": "quality@neodent.example",
            "subject": "FWD: Izjava o skladnosti - Neodent vsadki",
            "body": "Se enkrat, za vsak slucaj.",
            "files": [(doc, None, None)],
            "expect": "NEW ledger row (new source email) but NO new extract.doc "
                      "-- same content hash as `doc`",
        },
    ]


def _extra_case(path: pathlib.Path) -> dict:
    """An ad-hoc attachment supplied with --extra.

    The six fixed cases above are corpus files the backfill has usually already
    archived, so they exercise the SEEN-hash branch. To exercise "new content
    hash -> archive + extract.doc" you need bytes the archive has never held --
    which is a moving target (a file is new exactly once), so it is a flag
    rather than a seventh hard-coded case.

    Identity is derived from the content hash, so re-seeding the same file is
    idempotent and two different files never collide.
    """
    blob = path.read_bytes() if path.is_file() else None
    if blob is None:
        raise SystemExit(f"error: --extra file not found: {path}")
    import hashlib
    digest = hashlib.sha256(blob).hexdigest()
    return {
        "key": f"extra:{digest[:12]}",
        "from": "regulatory@dobavitelj.example",
        "subject": f"Dokumentacija: {path.stem}",
        "body": "V prilogi zahtevana dokumentacija.",
        "files": [(path, None, None)],
        "expect": "new hash -> archived + extract.doc emitted "
                  f"(sha256 {digest[:12]}...)",
        "_date": "Thu, 20 Aug 2026 09:30:00 +0200",
        "_msgid": f"<seed-extra-{digest[:16]}@dentalia.si>",
    }


def _build(case: dict, to_addr: str, fresh: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = case["from"]
    msg["To"] = to_addr
    msg["Subject"] = case["subject"]
    sent = case.get("_date") or _SENT[case["key"]]
    msg["Date"] = sent
    base = case.get("_msgid") or f"<seed-{case['key']}@dentalia.si>"
    if not fresh:
        # Deterministic identity. The handler's second reprocess guard matches
        # on Message-ID within the mailbox, so a re-seed after a GreenMail
        # restart is correctly recognised as already-processed rather than
        # silently re-archived. --fresh mints new ids to force a real re-run.
        msg["Message-ID"] = base
    else:
        d = parsedate_to_datetime(sent)
        msg["Message-ID"] = base.replace("@", f"-{int(d.timestamp())}-fresh@", 1)
    msg.set_content(case["body"])

    for k, v in (case.get("headers") or {}).items():
        msg[k] = v

    for path, name, blob in case["files"]:
        # An EmailMessage as the payload means a genuine message/rfc822 part
        # (a forward), not a file.
        if isinstance(blob, EmailMessage):
            msg.add_attachment(blob, filename=name)
            continue
        if blob is None:
            if not path.is_file():
                raise SystemExit(
                    f"error: corpus file missing: {path}\n"
                    "       point --corpus at the SFTP dump (default "
                    f"{DEFAULT_CORPUS})"
                )
            blob = path.read_bytes()
            name = name or path.name
        ctype, _ = mimetypes.guess_type(name)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(blob, maintype=maintype, subtype=subtype, filename=name)
    return msg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--to", default=DEFAULT_TO, help="mailbox to deliver into")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS, type=pathlib.Path)
    ap.add_argument("--fresh", action="store_true",
                    help="mint new Message-IDs so an already-polled mailbox "
                         "gets genuinely reprocessed")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and describe the messages, send nothing")
    ap.add_argument("--extra", action="append", default=[], type=pathlib.Path,
                    metavar="PATH",
                    help="attach this file as an additional message; repeatable. "
                         "Use a PDF the archive has never seen to exercise the "
                         "new-content-hash branch")
    ap.add_argument("--only-extra", action="store_true",
                    help="send only the --extra messages, not the six fixtures")
    ap.add_argument("--shapes", action="store_true",
                    help="also send the generated-attachment shapes (reply with "
                         "In-Reply-To, forwarded message/rfc822, ZIP, oversize). "
                         "Their PDFs are generated, so unlike the corpus "
                         "fixtures they are always a new hash")
    args = ap.parse_args(argv)

    cases = [] if args.only_extra else _cases(args.corpus)
    if args.shapes:
        cases += _shape_cases()
    cases += [_extra_case(p) for p in args.extra]
    if not cases:
        raise SystemExit("error: --only-extra with no --extra: nothing to send")
    built = [(c, _build(c, args.to, args.fresh)) for c in cases]

    for case, msg in built:
        # iter_attachments, not len(get_payload()): a message with no
        # attachment is not multipart at all and its payload is a plain string,
        # which counted its 46 characters as 46 attachments.
        atts = list(msg.iter_attachments())
        size = sum(len(p.get_payload(decode=True) or b"") for p in atts)
        print(f"  {case['key']:20s} {len(atts)} attach "
              f"{size:>8d}B  {case['expect']}")

    if args.dry_run:
        print(f"\ndry run: {len(built)} messages built, nothing sent")
        return 0

    try:
        with smtplib.SMTP(args.host, args.port, timeout=10) as smtp:
            for _, msg in built:
                smtp.send_message(msg)
    except OSError as exc:
        print(f"\nerror: SMTP {args.host}:{args.port} unreachable ({exc}).\n"
              "       start it with: docker compose --profile email-dev up -d "
              "greenmail", file=sys.stderr)
        return 1

    print(f"\nsent {len(built)} messages to {args.to} via {args.host}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
