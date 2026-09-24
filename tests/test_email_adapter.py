"""EMAIL inbound adapters (S2.4) — the same boundary the other adapter tests
assert: nothing downstream knows which mailbox is live (invariant 11), the
adapter only READS (invariant 12), and a keyless live adapter raises the typed
skip signal rather than connecting. Everything here runs against fakes / raw
bytes — no IMAP server (GAP G8).
"""

from __future__ import annotations

from datetime import date
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
    imap_date,
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


def test_make_email_adapter_carries_the_start_date(monkeypatch):
    monkeypatch.delenv("EMAIL_ADAPTER", raising=False)
    monkeypatch.setenv("EMAIL_POLL_SINCE", "2026-09-24")
    assert make_email_adapter(load_config()).since == "2026-09-24"


def test_make_email_adapter_unknown_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ADAPTER", "graph")
    with pytest.raises(ValueError):
        make_email_adapter(load_config())


def _none_logged(uid_validity):
    return set()


# --- ImapEmailAdapter: never connects unconfigured -------------------------
def test_imap_fetch_new_unconfigured_raises():
    adapter = ImapEmailAdapter(host="", user="", password="")
    with pytest.raises(EmailNotConfigured):
        adapter.fetch_new(processed=_none_logged)


def test_imap_without_a_start_date_refuses_loudly():
    """Credentials set, EMAIL_POLL_SINCE not: the poll must NOT read the whole
    mailbox's history. ValueError, not EmailNotConfigured, so it dead-letters
    and alerts rather than skipping quietly."""
    adapter = ImapEmailAdapter(host="h", user="u", password="p", since="",
                               imap_factory=_boom)
    with pytest.raises(ValueError, match="EMAIL_POLL_SINCE is unset"):
        adapter.fetch_new(processed=_none_logged)


def test_imap_with_a_malformed_start_date_refuses_loudly():
    adapter = ImapEmailAdapter(host="h", user="u", password="p", since="24.9.2026",
                               imap_factory=_boom)
    with pytest.raises(ValueError, match="not a date"):
        adapter.fetch_new(processed=_none_logged)


def _boom(host, port):
    raise AssertionError("must refuse before opening any connection")


# --- ImapEmailAdapter against a recording IMAP double ----------------------
#
# The wire path used to be `pragma: no cover` because no IMAP server exists in
# the suite. This double speaks imaplib's call shapes and records every command
# sent, which is what the read-only guarantee is asserted against.

_RAW = (b"From: a@manu.example\r\nSubject: s\r\nMessage-ID: <m%d@x>\r\n"
        b"\r\nbody %d\r\n")


class RecordingImap:
    """UIDs 1..n; `arrived[uid]` is the INTERNALDATE the server compares SINCE with."""

    def __init__(self, arrived: dict[int, date], uidvalidity: str = "7"):
        self.arrived = arrived
        self.uidvalidity = uidvalidity
        self.commands: list[tuple] = []

    def __call__(self, host, port):          # the factory
        self.commands.append(("CONNECT", host, port))
        return self

    def login(self, user, password):
        self.commands.append(("LOGIN", user))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.commands.append(("EXAMINE" if readonly else "SELECT", mailbox))
        return "OK", [str(len(self.arrived)).encode()]

    def response(self, code):
        return code, [self.uidvalidity.encode()]

    def uid(self, command, *args):
        args = tuple(a for a in args if a is not None)
        self.commands.append(("UID", command.upper(), *args))
        if command.upper() == "SEARCH":
            assert args[0] == "SINCE", args
            d, mon, y = args[1].split("-")
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            floor = date(int(y), months.index(mon) + 1, int(d))
            hits = sorted(u for u, day in self.arrived.items() if day >= floor)
            return "OK", [" ".join(str(u) for u in reversed(hits)).encode()]
        if command.upper() == "FETCH":
            u = int(args[0])
            return "OK", [(b"%d (UID %d BODY[] {99}" % (u, u), _RAW % (u, u)), b")"]
        return "NO", [b"unexpected"]

    def logout(self):
        self.commands.append(("LOGOUT",))
        return "BYE", [b""]

    # Present so a regression that calls them is recorded, not an AttributeError
    def close(self):
        self.commands.append(("CLOSE",))

    def store(self, *a):
        self.commands.append(("STORE",) + a)

    def expunge(self):
        self.commands.append(("EXPUNGE",))

    def verbs(self) -> set[str]:
        out = set()
        for c in self.commands:
            out.add(c[1] if c[0] == "UID" else c[0])
        return out


def _adapter(server, since="2026-09-24"):
    return ImapEmailAdapter(host="mail.example.test", user="mdr@x.test",
                            password="p", since=since, imap_factory=server)


SEP = {1: date(2026, 9, 20), 2: date(2026, 9, 23), 3: date(2026, 9, 24),
       4: date(2026, 9, 25), 5: date(2026, 9, 26)}


def test_imap_reads_only_mail_on_or_after_the_start_date():
    server = RecordingImap(SEP)
    batch = _adapter(server).fetch_new(processed=_none_logged)

    assert [m.uid for m in batch.messages] == ["3", "4", "5"]   # the start day included
    assert ("UID", "SEARCH", "SINCE", "24-Sep-2026") in server.commands


def test_imap_is_read_only_on_the_wire():
    """Never delete, never mark: EXAMINE not SELECT, BODY.PEEK[] not RFC822,
    LOGOUT not CLOSE, and no STORE / EXPUNGE / MOVE / COPY at all."""
    server = RecordingImap(SEP)
    _adapter(server).fetch_new(processed=_none_logged)

    assert "EXAMINE" in server.verbs()
    assert not server.verbs() & {"SELECT", "CLOSE", "STORE", "EXPUNGE", "MOVE", "COPY"}
    fetches = [c for c in server.commands if c[:2] == ("UID", "FETCH")]
    assert fetches and all(c[3] == "(BODY.PEEK[])" for c in fetches)
    assert server.commands[-1] == ("LOGOUT",)


def test_imap_filters_the_ledger_before_downloading_anything():
    server = RecordingImap(SEP)
    asked = []

    def processed(uid_validity):
        asked.append(uid_validity)
        return {"3", "4"}

    batch = _adapter(server).fetch_new(processed=processed)

    assert asked == ["7"]                                      # once, in the right epoch
    assert [m.uid for m in batch.messages] == ["5"]
    assert batch.already_logged == 2
    fetched = [c[2] for c in server.commands if c[:2] == ("UID", "FETCH")]
    assert fetched == ["5"]                                    # 3 and 4 never downloaded


def test_imap_cap_paces_oldest_first_and_counts_the_rest():
    server = RecordingImap(SEP)
    batch = _adapter(server).fetch_new(processed=_none_logged, limit=2)

    assert [m.uid for m in batch.messages] == ["3", "4"]       # oldest first
    assert batch.deferred == 1                                  # 5 waits, counted


def test_imap_second_poll_reaches_what_the_first_deferred():
    """With the ledger filter before the cap, a fixed start date can never
    starve: each poll moves forward."""
    server = RecordingImap(SEP)
    first = _adapter(server).fetch_new(processed=_none_logged, limit=2)
    logged = {m.uid for m in first.messages}
    second = _adapter(server).fetch_new(processed=lambda uv: logged, limit=2)

    assert [m.uid for m in second.messages] == ["5"]
    assert (second.already_logged, second.deferred) == (2, 0)


@pytest.mark.parametrize("d, expected", [
    (date(2026, 1, 1), "01-Jan-2026"),
    (date(2026, 9, 4), "04-Sep-2026"),
    (date(2026, 12, 31), "31-Dec-2026"),
])
def test_imap_date_is_rfc3501_and_locale_free(d, expected):
    assert imap_date(d) == expected


def test_imap_mailbox_id_shape():
    adapter = ImapEmailAdapter(host="mail.example.test", user="rep@x.test", folder="INBOX")
    assert adapter.mailbox_id == "mail.example.test/rep@x.test/INBOX"


# --- FakeEmailAdapter ------------------------------------------------------
def test_fake_fetch_new_returns_injected_and_paces_with_the_cap():
    fake = FakeEmailAdapter([_msg("1"), _msg("2"), _msg("3")])
    assert [m.uid for m in fake.fetch_new(processed=_none_logged).messages] == ["1", "2", "3"]
    batch = fake.fetch_new(processed=_none_logged, limit=2)
    assert [m.uid for m in batch.messages] == ["1", "2"]
    assert batch.deferred == 1


def test_fake_skips_what_the_ledger_holds():
    fake = FakeEmailAdapter([_msg("1"), _msg("2")])
    batch = fake.fetch_new(processed=lambda uv: {"1"})
    assert [m.uid for m in batch.messages] == ["2"]
    assert batch.already_logged == 1


def test_fake_error_propagates():
    fake = FakeEmailAdapter(error=RuntimeError("imap down"))
    with pytest.raises(RuntimeError):
        fake.fetch_new(processed=_none_logged)


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
