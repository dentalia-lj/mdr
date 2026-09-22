"""EMAIL inbound adapters (S2.4) — the same boundary the other adapter tests
assert: nothing downstream knows which mailbox is live (invariant 11), the
adapter only READS (invariant 12), and a keyless live adapter raises the typed
skip signal rather than connecting. Everything here runs against fakes / raw
bytes — no IMAP server (GAP G8).
"""

from __future__ import annotations

from email.message import EmailMessage as StdEmailMessage

import pytest

from app.adapters.email import (
    BODY_CHAR_CAP,
    EmailAttachment,
    EmailMessage,
    EmailNotConfigured,
    FakeEmailAdapter,
    ImapEmailAdapter,
    make_email_adapter,
    extract_body_text,
    parse_message,
)
from app.config import load_config


def _msg(uid: str, atts=None) -> EmailMessage:
    return EmailMessage(
        uid=uid, uid_validity="1", message_id=f"<{uid}@x>", from_addr="a@b.c",
        subject="s", received_at=None, in_reply_to=None, references=None,
        attachments=list(atts or []),
    )


# --- factory ---------------------------------------------------------------
def test_make_email_adapter_selects_imap_by_default(monkeypatch):
    monkeypatch.delenv("EMAIL_ADAPTER", raising=False)
    adapter = make_email_adapter(load_config())
    assert isinstance(adapter, ImapEmailAdapter)


def test_make_email_adapter_selects_fake(monkeypatch):
    monkeypatch.setenv("EMAIL_ADAPTER", "fake")
    adapter = make_email_adapter(load_config())
    assert isinstance(adapter, FakeEmailAdapter)


def test_make_email_adapter_unknown_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ADAPTER", "graph")
    with pytest.raises(ValueError):
        make_email_adapter(load_config())


# --- ImapEmailAdapter: never connects unconfigured -------------------------
def test_imap_fetch_unseen_unconfigured_raises():
    adapter = ImapEmailAdapter(host="", user="", password="")
    with pytest.raises(EmailNotConfigured):
        adapter.fetch_unseen()


def test_imap_mailbox_id_shape():
    adapter = ImapEmailAdapter(host="mail.example.test", user="rep@x.test", folder="INBOX")
    assert adapter.mailbox_id == "mail.example.test/rep@x.test/INBOX"


# --- FakeEmailAdapter ------------------------------------------------------
def test_fake_fetch_unseen_returns_injected_and_respects_limit():
    fake = FakeEmailAdapter([_msg("1"), _msg("2"), _msg("3")])
    assert [m.uid for m in fake.fetch_unseen()] == ["1", "2", "3"]
    assert [m.uid for m in fake.fetch_unseen(limit=2)] == ["1", "2"]


def test_fake_mark_seen_hides_message():
    fake = FakeEmailAdapter([_msg("1"), _msg("2")])
    fake.mark_seen("1")
    assert [m.uid for m in fake.fetch_unseen()] == ["2"]


def test_fake_error_propagates():
    fake = FakeEmailAdapter(error=RuntimeError("imap down"))
    with pytest.raises(RuntimeError):
        fake.fetch_unseen()


# --- parse_message: real RFC822 bytes, no server ---------------------------
def test_parse_message_finds_a_pdf_inside_a_forwarded_message():
    """Suppliers forward the manufacturer's mail rather than re-attaching it,
    so the document arrives one level down inside a message/rfc822 part.
    Verified live against the fake mailbox on 2026-08-20 -- walk() descends and
    the nested PDF is found -- and pinned here so it stays that way."""
    inner = StdEmailMessage()
    inner["From"] = "regulatory@manu.example"
    inner["Subject"] = "Declaration of conformity"
    inner["Message-ID"] = "<inner@manu.example>"
    inner.set_content("Attached.")
    inner.add_attachment(b"%PDF-1.7 inner", maintype="application", subtype="pdf",
                         filename="nested-doc.pdf")

    outer = StdEmailMessage()
    outer["From"] = "sales@dobavitelj.example"
    outer["Subject"] = "FW: Declaration of conformity"
    outer["Message-ID"] = "<outer@dobavitelj.example>"
    outer.set_content("Forwarding what the manufacturer sent us.")
    outer.add_attachment(inner, filename="forwarded.eml")

    parsed = parse_message("43", "7", outer.as_bytes())

    names = [a.filename for a in parsed.attachments]
    assert "nested-doc.pdf" in names
    nested = next(a for a in parsed.attachments if a.filename == "nested-doc.pdf")
    assert nested.content.startswith(b"%PDF")


def test_parse_message_captures_both_reply_headers():
    """In-Reply-To AND References. The reply matcher (spec §9) needs the whole
    chain, not just the immediate parent -- a supplier's client may quote only
    one of the two, and a long thread carries the original request id only in
    References."""
    m = StdEmailMessage()
    m["From"] = "regulatory@manu.example"
    m["Subject"] = "RE: MDR DOCUMENTS"
    m["Message-ID"] = "<reply@manu.example>"
    m["In-Reply-To"] = "<req-1@dentalia.si>"
    m["References"] = "<req-0@dentalia.si> <req-1@dentalia.si>"
    m.set_content("Attached.")

    parsed = parse_message("44", "7", m.as_bytes())

    assert parsed.in_reply_to == "<req-1@dentalia.si>"
    assert parsed.references == "<req-0@dentalia.si> <req-1@dentalia.si>"


def test_parse_message_extracts_headers_and_pdf_attachment():
    m = StdEmailMessage()
    m["From"] = "Rep Name <rep@manu.example>"
    m["Subject"] = "Updated certificate"
    m["Message-ID"] = "<abc123@manu.example>"
    m["Date"] = "Tue, 19 Aug 2026 10:00:00 +0000"
    m["In-Reply-To"] = "<req99@dentalia.test>"
    m.set_content("Please find the DoC attached.")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                     filename="cert.pdf")

    parsed = parse_message("42", "7", m.as_bytes())

    assert parsed.uid == "42"
    assert parsed.uid_validity == "7"
    assert parsed.message_id == "<abc123@manu.example>"
    assert parsed.from_addr == "rep@manu.example"
    assert parsed.subject == "Updated certificate"
    assert parsed.received_at is not None
    assert parsed.in_reply_to == "<req99@dentalia.test>"
    assert len(parsed.attachments) == 1
    att = parsed.attachments[0]
    assert att.filename == "cert.pdf"
    assert att.content_type == "application/pdf"
    assert att.content.startswith(b"%PDF")


def test_parse_message_ignores_inline_body_parts():
    m = StdEmailMessage()
    m["From"] = "x@y.z"
    m["Message-ID"] = "<noatt@y.z>"
    m.set_content("body text only, no attachment")
    parsed = parse_message("1", "1", m.as_bytes())
    assert parsed.attachments == []


def test_email_attachment_is_frozen():
    att = EmailAttachment(filename="a.pdf", content_type="application/pdf", content=b"%PDF")
    with pytest.raises(Exception):
        att.filename = "b.pdf"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# body text (2026-08-20): the summariser's input. Held in memory only -- what
# persists is the summary, never this (spec §10). The adapter's job is to find
# the text a human actually wrote and bound it; nothing here is policy.
# --------------------------------------------------------------------------- #
def test_the_inline_body_is_carried_even_though_it_is_not_an_attachment():
    """`parse_message` skips inline parts when collecting attachments, which is
    correct -- and until 2026-08-20 meant the body was discarded entirely, so
    there was nothing for the poll to summarise."""
    m = StdEmailMessage()
    m["From"] = "rep@manu.example"
    m["Message-ID"] = "<body@manu.example>"
    m.set_content("Please send the DoC for REF 4056 before the audit.")

    parsed = parse_message("1", "1", m.as_bytes())

    assert parsed.attachments == []
    assert "REF 4056" in parsed.body_text


def test_plain_text_wins_over_the_html_alternative():
    """multipart/alternative carries both halves of the same message. The plain
    half is what a person typed; the html half is the same words wrapped in
    markup that would cost tokens to send and add nothing."""
    m = StdEmailMessage()
    m["From"] = "rep@manu.example"
    m["Message-ID"] = "<alt@manu.example>"
    m.set_content("the plain half")
    m.add_alternative("<html><body><p>the html half</p></body></html>", subtype="html")

    body = parse_message("1", "1", m.as_bytes()).body_text

    assert body == "the plain half"
    assert "html half" not in body


def test_an_html_only_message_falls_back_to_stripped_text():
    """Some senders emit html only. Tags, script and style are dropped and
    entities unescaped, so the summariser sees prose rather than markup."""
    raw = (
        b"From: rep@manu.example\r\n"
        b"Message-ID: <html@manu.example>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head><style>p{color:red}</style></head>"
        b"<body><p>Certificate expires 04-05-2026 &amp; must be renewed</p>"
        b"<script>track()</script></body></html>"
    )

    body = parse_message("1", "1", raw).body_text

    assert "Certificate expires 04-05-2026 & must be renewed" in body
    assert "color:red" not in body
    assert "track()" not in body
    assert "<p>" not in body


def test_a_forwarded_message_body_is_read_at_its_real_depth():
    """A forward's top-level text is "FYI, see below"; the content the client
    cares about is inside the nested message/rfc822. Walking at any depth is
    what makes the summary about the supplier's words, not the forwarder's."""
    inner = StdEmailMessage()
    inner["From"] = "rep@manu.example"
    inner["Subject"] = "Nexco DoC"
    inner.set_content("The declaration you asked for expired on 04-05-2026.")

    outer = StdEmailMessage()
    outer["From"] = "natasa@dentalia.test"
    outer["Message-ID"] = "<fwd@dentalia.test>"
    outer.set_content("FYI, see below")
    outer.add_attachment(inner, disposition="inline")

    body = parse_message("1", "1", outer.as_bytes()).body_text

    assert "FYI, see below" in body
    assert "expired on 04-05-2026" in body


def test_a_text_attachment_is_not_body():
    """An attached .txt is a document the sender chose to attach, not the note
    they wrote. Letting it in would put file contents into the summary."""
    m = StdEmailMessage()
    m["From"] = "rep@manu.example"
    m["Message-ID"] = "<txtatt@manu.example>"
    m.set_content("see attached")
    m.add_attachment(b"SERIAL NUMBERS 1 2 3", maintype="text", subtype="plain",
                     filename="serials.txt")

    body = parse_message("1", "1", m.as_bytes()).body_text

    assert body == "see attached"
    assert "SERIAL NUMBERS" not in body


def test_the_body_is_bounded_so_a_newsletter_cannot_be_held_whole():
    """The cap is the adapter's own memory bound, independent of what any
    consumer asks for -- the summariser caps its input far lower again."""
    m = StdEmailMessage()
    m["From"] = "rep@manu.example"
    m["Message-ID"] = "<big@manu.example>"
    m.set_content("x" * (BODY_CHAR_CAP * 3))

    assert len(parse_message("1", "1", m.as_bytes()).body_text) == BODY_CHAR_CAP


def test_a_message_with_no_text_part_has_an_empty_body():
    """A bare PDF with no note. Empty, not None: the caller reads a length, and
    "" is below any floor it applies."""
    m = StdEmailMessage()
    m["From"] = "rep@manu.example"
    m["Message-ID"] = "<pdfonly@manu.example>"
    m.set_content(b"%PDF-1.4 x", maintype="application", subtype="pdf",
                  filename="doc.pdf")

    assert parse_message("1", "1", m.as_bytes()).body_text == ""


def test_extract_body_text_is_shared_not_reimplemented():
    """`parse_message` must not carry its own copy of the walk -- the IMAP
    adapter and any future transport go through the same definition."""
    import email as email_pkg

    raw = b"From: a@b.c\r\nMessage-ID: <s@b.c>\r\n\r\nshared definition"
    parsed = parse_message("1", "1", raw)
    direct = extract_body_text(email_pkg.message_from_bytes(raw))

    assert parsed.body_text == direct == "shared definition"
