"""EMAIL inbound adapters (S2.4) — PRD §4 sibling-producer, handbook row 6.

`email.poll` reads a dedicated mailbox and hands the handler a uniform
`list[EmailMessage]`. Nothing downstream knows which mailbox implementation is
live (invariant 11): the handler archives + emits identically regardless.

    ImapEmailAdapter   (real) — imaplib over IMAPS; lazy-imported.
    FakeEmailAdapter   (test/dev) — injected in-memory messages; never HTTP/IMAP.

Provider selection is config-switched by `adapters.email` via
`make_email_adapter(cfg)`, the same shape as `make_search_adapter` /
`make_storage_adapter` / `make_fetcher`.

Invariant 12: the adapter only READS the mailbox; it never parses documents and
never fetches with an LLM. Classification and archiving are the handler's job.
Invariant 1: a producer input only — no adapter here writes the registry.

The live IMAP path is built against the CPython `imaplib` / `email` API and is
NOT connected in this slice — no credentials exist yet (GAP G8). All tests run
against `FakeEmailAdapter`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = [
    "EmailAttachment",
    "extract_body_text",
    "EmailMessage",
    "EmailAdapter",
    "EmailNotConfigured",
    "ImapEmailAdapter",
    "FakeEmailAdapter",
    "make_email_adapter",
]


class EmailNotConfigured(RuntimeError):
    """Raised when the IMAP adapter has no host/user/password. Distinct from a
    live connection failure: `email.poll` treats "not configured" as a skipped
    rung (the operator hasn't wired the mailbox — GAP G8) and returns cleanly,
    while a genuine connect/auth error with creds present propagates and
    dead-letters. Mirrors `SearchNotConfigured` in adapters/search.py."""


@dataclass(frozen=True)
class EmailAttachment:
    """One attachment part. `content` is the decoded bytes."""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class EmailMessage:
    """One mailbox message — the uniform shape every adapter maps into.

    `uid` + `uid_validity` are the IMAP coordinates the processed-email ledger
    keys on (UIDs are only stable within a UIDVALIDITY epoch). `message_id` is
    the durable RFC822 identity. `in_reply_to` / `references` are captured for
    the outbound slice's reply matcher (spec §9) and unused inbound.
    """

    uid: str
    uid_validity: str
    message_id: str
    from_addr: str
    subject: str
    received_at: datetime | None
    in_reply_to: str | None
    references: str | None
    attachments: list[EmailAttachment] = field(default_factory=list)
    #: Inline body text, already decoded and bounded to `BODY_CHAR_CAP`. Held in
    #: memory for the poll's summariser and NEVER persisted -- `email_poll_log`
    #: stores the summary, not this (spec §10, invariant 12). Empty when the
    #: message carries no readable text part.
    body_text: str = ""


@runtime_checkable
class EmailAdapter(Protocol):
    @property
    def mailbox_id(self) -> str:
        """Stable mailbox identity ("{host}/{user}/{folder}") — the ledger scope
        so two mailboxes cannot collide on the same IMAP UID."""
        ...

    def fetch_unseen(self, *, limit: int | None = None) -> list[EmailMessage]: ...

    def mark_seen(self, uid: str) -> None: ...


# --------------------------------------------------------------------------- #
# header parsing helpers (stdlib email; pure functions on a Message)
# --------------------------------------------------------------------------- #
def _decode_header(raw: str | None) -> str:
    """RFC 2047-decoded header text (handles =?utf-8?...?= encoded words)."""
    if not raw:
        return ""
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # pragma: no cover - defensive: a malformed header is still text
        return raw


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):  # pragma: no cover - malformed Date header
        return None


def _from_addr(raw: str | None) -> str:
    if not raw:
        return ""
    from email.utils import parseaddr

    name, addr = parseaddr(raw)
    return addr or _decode_header(raw)


#: Memory bound on the extracted body. A supplier's 40-page HTML newsletter is
#: not worth holding whole, and the summariser caps its own input far lower
#: (`email.summary_max_chars`) -- this cap exists so the adapter cannot be made
#: to hold an arbitrary payload, independent of what any consumer asks for.
BODY_CHAR_CAP = 20_000

_TAG_RE = re.compile(r"<[^>]+>")
_DROP_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # pragma: no cover - a charset python does not know
        return payload.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    from html import unescape

    text = _DROP_RE.sub(" ", html)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    return unescape(_TAG_RE.sub(" ", text))


def _tidy(text: str) -> str:
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS_RE.sub("\n\n", text).strip()


def extract_body_text(msg) -> str:
    """The readable text of a message, for the poll's summariser only.

    text/plain wins outright; text/html is the fallback and only when no plain
    part exists at all (a multipart/alternative carries both, and the plain
    half is what a human wrote). Parts are collected in `walk()` order at ANY
    depth, so a forward's real content -- which lives inside the nested
    message/rfc822, not the "FYI, see below" wrapper -- is included rather than
    summarised away. Attachment-disposition parts are never body, even when
    their type is text/*.

    Public so `parse_message` and tests share one definition. Returns "" when
    there is no readable text; the caller decides what that means.
    """
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if (part.get_content_disposition() or "").lower() == "attachment":
            continue
        if part.get_filename():
            continue
        subtype = part.get_content_subtype()
        if part.get_content_maintype() != "text":
            continue
        if subtype == "plain":
            plain.append(_part_text(part))
        elif subtype == "html":
            html.append(_html_to_text(_part_text(part)))

    body = _tidy("\n\n".join(plain) if plain else "\n\n".join(html))
    return body[:BODY_CHAR_CAP]


def parse_message(uid: str, uid_validity: str, raw_bytes: bytes) -> EmailMessage:
    """Map a raw RFC822 message to `EmailMessage`. Public so tests can build
    fixtures from real .eml bytes without an IMAP server, and so the IMAP
    adapter and any future transport share one parser."""
    import email as email_pkg

    msg = email_pkg.message_from_bytes(raw_bytes)
    attachments: list[EmailAttachment] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        # An attachment is a part with a filename or an explicit attachment
        # disposition. Inline body parts (text/plain, text/html with no
        # filename) are not attachments.
        if not filename and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        attachments.append(
            EmailAttachment(
                filename=_decode_header(filename) or "attachment",
                content_type=(part.get_content_type() or "application/octet-stream"),
                content=payload,
            )
        )
    return EmailMessage(
        uid=str(uid),
        uid_validity=str(uid_validity),
        message_id=_decode_header(msg.get("Message-ID")),
        from_addr=_from_addr(msg.get("From")),
        subject=_decode_header(msg.get("Subject")),
        received_at=_parse_date(msg.get("Date")),
        in_reply_to=_decode_header(msg.get("In-Reply-To")) or None,
        references=_decode_header(msg.get("References")) or None,
        attachments=attachments,
        body_text=extract_body_text(msg),
    )


# --------------------------------------------------------------------------- #
# live IMAP adapter (built to the imaplib API; not connected in this slice)
# --------------------------------------------------------------------------- #
class ImapEmailAdapter:
    """IMAPS mailbox reader. `imaplib` is lazy-imported inside the connect path
    so the fake-adapter tests and the web container never pull it in (it is
    stdlib, but the discipline matches search.py/fetcher.py).

    Uses UID commands throughout so message coordinates are stable across the
    session. Construction is inert; the connection opens on the first
    `fetch_unseen`. An empty host/user/password raises `EmailNotConfigured`
    (a skipped rung, not a dead job)."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 993,
        user: str = "",
        password: str = "",
        folder: str = "INBOX",
        ssl: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.folder = folder
        self.ssl = ssl
        self._conn = None
        self._uid_validity: str | None = None

    @property
    def mailbox_id(self) -> str:
        return f"{self.host}/{self.user}/{self.folder}"

    # -- connection ---------------------------------------------------------
    def _require_configured(self) -> None:
        if not self.host or not self.user or not self.password:
            raise EmailNotConfigured(
                "IMAP mailbox not configured (IMAP_HOST/IMAP_USER/IMAP_PASSWORD "
                "empty) — the email.poll rung is not wired (GAP G8). Set the "
                "credentials or leave scheduler.email_poll_enabled off."
            )

    def _connect(self):  # pragma: no cover - real network I/O
        import imaplib

        self._require_configured()
        conn = (imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4)(self.host, self.port)
        conn.login(self.user, self.password)
        typ, _ = conn.select(self.folder, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"IMAP SELECT {self.folder!r} failed: {typ}")
        # UIDVALIDITY comes back as an untagged response to SELECT.
        vtyp, vdata = conn.response("UIDVALIDITY")
        if vdata and vdata[0]:
            self._uid_validity = vdata[0].decode() if isinstance(vdata[0], bytes) else str(vdata[0])
        else:
            self._uid_validity = "0"
        self._conn = conn
        return conn

    # -- reads --------------------------------------------------------------
    def fetch_unseen(self, *, limit: int | None = None) -> list[EmailMessage]:
        # Not-configured is checked eagerly so a keyless poll raises the typed
        # skip signal before any socket work.
        self._require_configured()
        conn = self._connect()  # pragma: no cover - real network I/O
        try:  # pragma: no cover - real network I/O
            typ, data = conn.uid("SEARCH", None, "UNSEEN")
            if typ != "OK":
                raise RuntimeError(f"IMAP UID SEARCH failed: {typ}")
            uids = (data[0].split() if data and data[0] else [])
            if limit is not None:
                uids = uids[:limit]
            messages: list[EmailMessage] = []
            for raw_uid in uids:
                uid = raw_uid.decode() if isinstance(raw_uid, bytes) else str(raw_uid)
                ftyp, fdata = conn.uid("FETCH", uid, "(RFC822)")
                if ftyp != "OK" or not fdata or not isinstance(fdata[0], tuple):
                    continue
                raw_bytes = fdata[0][1]
                messages.append(parse_message(uid, self._uid_validity or "0", raw_bytes))
            return messages
        finally:  # pragma: no cover - real network I/O
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

    def mark_seen(self, uid: str) -> None:  # pragma: no cover - real network I/O
        # Courtesy only — the durable email_poll_log row is the reprocess guard,
        # not \Seen (spec §2). Best-effort: a mailbox that refuses the flag must
        # not fail the poll, because the ledger already recorded the message.
        conn = self._conn
        if conn is None:
            return
        try:
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# test/dev fake
# --------------------------------------------------------------------------- #
class FakeEmailAdapter:
    """In-memory mailbox for tests/dev. `fetch_unseen` returns injected messages
    the fake has not been told are seen; `mark_seen` records the UID. The
    handler's `email_poll_log` remains the durable authority, so a test can also
    prove idempotency without relying on this seen-set."""

    def __init__(
        self,
        messages: list[EmailMessage] | None = None,
        *,
        mailbox_id: str = "fake/inbox@example.test/INBOX",
        error: Exception | None = None,
    ) -> None:
        self._messages = list(messages or [])
        self._mailbox_id = mailbox_id
        self._seen: set[str] = set()
        self._error = error

    @property
    def mailbox_id(self) -> str:
        return self._mailbox_id

    def fetch_unseen(self, *, limit: int | None = None) -> list[EmailMessage]:
        if self._error is not None:
            raise self._error
        out = [m for m in self._messages if m.uid not in self._seen]
        return out[:limit] if limit is not None else out

    def mark_seen(self, uid: str) -> None:
        self._seen.add(str(uid))


def make_email_adapter(cfg) -> EmailAdapter:
    """Config-switched adapter over the closed `email` enum (`imap | fake`).
    `cfg` is the full composed `Config`. Credentials may be empty — the IMAP
    adapter raises `EmailNotConfigured` from `fetch_unseen`, so a misconfigured
    but flag-gated poll skips cleanly rather than dead-lettering."""
    name = cfg.adapters.email
    if name == "imap":
        return ImapEmailAdapter(
            host=cfg.email.imap_host,
            port=cfg.email.imap_port,
            user=cfg.connection.imap_user,
            password=cfg.connection.imap_password,
            folder=cfg.email.imap_folder,
            ssl=cfg.email.imap_ssl,
        )
    if name == "fake":
        return FakeEmailAdapter()
    raise ValueError(f"unknown email adapter {name!r} (closed enum: imap | fake)")
