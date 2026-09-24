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
from datetime import date, datetime
from typing import Callable, Protocol, runtime_checkable

__all__ = [
    "EmailAttachment",
    "extract_body_text",
    "EmailMessage",
    "EmailAdapter",
    "EmailNotConfigured",
    "PollBatch",
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


@dataclass(frozen=True)
class PollBatch:
    """What one poll took from the mailbox, and what it knowingly did not.

    `already_logged` — on the server, already in `email_poll_log`, so never
    fetched again. `deferred` — new, but past the per-poll cap; the next poll
    takes them. Both are counted on the job result: nothing is skipped silently."""

    messages: list[EmailMessage]
    already_logged: int = 0
    deferred: int = 0


#: `processed(uid_validity)` -> the UIDs the ledger already holds for this
#: mailbox in that UIDVALIDITY epoch. The adapter calls it once, after opening
#: the folder, so the filter runs BEFORE any message is downloaded.
ProcessedUids = Callable[[str], "set[str]"]


@runtime_checkable
class EmailAdapter(Protocol):
    @property
    def mailbox_id(self) -> str:
        """Stable mailbox identity ("{host}/{user}/{folder}") — the ledger scope
        so two mailboxes cannot collide on the same IMAP UID."""
        ...

    def fetch_new(
        self, *, processed: ProcessedUids, limit: int | None = None
    ) -> PollBatch: ...


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


#: RFC 3501 dates use English month abbreviations whatever the host locale is;
#: `strftime("%b")` would give "sep." under a Slovene one.
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(d: date) -> str:
    """`date(2026, 9, 4)` -> `"04-Sep-2026"`, the RFC 3501 `date` form."""
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


# --------------------------------------------------------------------------- #
# live IMAP adapter (built to the imaplib API; not connected in this slice)
# --------------------------------------------------------------------------- #
class ImapEmailAdapter:
    """IMAPS mailbox reader. `imaplib` is lazy-imported inside the connect path
    so the fake-adapter tests and the web container never pull it in (it is
    stdlib, but the discipline matches search.py/fetcher.py).

    Uses UID commands throughout so message coordinates are stable across the
    session. Construction is inert; the connection opens on `fetch_new`. An
    empty host/user/password raises `EmailNotConfigured` (a skipped rung, not a
    dead job); a configured mailbox without a start date raises `ValueError`
    (a dead job, loudly), because the alternative is reading its whole history.

    READ ONLY, and it has to be structurally rather than by care, since the
    mailbox is a person's as well as ours:

    - `EXAMINE`, not `SELECT`: the server refuses any flag change this session.
    - `BODY.PEEK[]`, not `RFC822`: a plain body fetch sets `\\Seen` by itself.
    - `LOGOUT` only, never `CLOSE`: `CLOSE` on a writable mailbox expunges every
      message flagged `\\Deleted`, including ones another client flagged.
    - no `STORE`, `EXPUNGE`, `MOVE` or `COPY` anywhere. `tests/test_email_adapter.py`
      records every command sent and asserts none of them appears.

    Nothing is marked on the server to say a message was processed. Exchange's
    IMAP keeps only the standard flags, and those belong to the people reading
    the mailbox; the record of what the poll took is `email_poll_log`."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 993,
        user: str = "",
        password: str = "",
        folder: str = "INBOX",
        ssl: bool = True,
        since: str = "",
        imap_factory: Callable | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.folder = folder
        self.ssl = ssl
        self.since = since
        # Test seam: a callable (host, port) -> connection with imaplib's API.
        self._imap_factory = imap_factory
        self._uid_validity: str | None = None

    @property
    def mailbox_id(self) -> str:
        return f"{self.host}/{self.user}/{self.folder}"

    # -- connection ---------------------------------------------------------
    def _require_configured(self) -> date:
        if not self.host or not self.user or not self.password:
            raise EmailNotConfigured(
                "IMAP mailbox not configured (IMAP_HOST/IMAP_USER/IMAP_PASSWORD "
                "empty) — the email.poll rung is not wired (GAP G8). Set the "
                "credentials or leave scheduler.email_poll_enabled off."
            )
        if not self.since:
            raise ValueError(
                "EMAIL_POLL_SINCE is unset. With a mailbox configured, the poll "
                "refuses to run without a start date rather than read the "
                "mailbox's whole history. Set it to the first day to read, "
                "YYYY-MM-DD."
            )
        try:
            return date.fromisoformat(self.since)
        except ValueError:
            raise ValueError(
                f"EMAIL_POLL_SINCE={self.since!r} is not a date; expected YYYY-MM-DD"
            ) from None

    def _connect(self):
        self._require_configured()
        factory = self._imap_factory
        if factory is None:  # pragma: no cover - real network I/O
            import imaplib

            factory = imaplib.IMAP4_SSL if self.ssl else imaplib.IMAP4
        conn = factory(self.host, self.port)
        conn.login(self.user, self.password)
        # readonly=True sends EXAMINE: the server itself refuses flag changes.
        typ, _ = conn.select(self.folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"IMAP EXAMINE {self.folder!r} failed: {typ}")
        # UIDVALIDITY comes back as an untagged response to SELECT.
        vtyp, vdata = conn.response("UIDVALIDITY")
        if vdata and vdata[0]:
            self._uid_validity = vdata[0].decode() if isinstance(vdata[0], bytes) else str(vdata[0])
        else:
            self._uid_validity = "0"
        return conn

    # -- reads --------------------------------------------------------------
    def fetch_new(
        self, *, processed: ProcessedUids, limit: int | None = None
    ) -> PollBatch:
        """Every message that arrived on or after `since` and is not in the
        ledger, oldest first, at most `limit` of them.

        `SINCE` compares the server's arrival date, day granular, so the start
        day itself is included. \\Seen plays no part: a message a person has
        already opened is still new to us, and nothing we do opens one for them."""
        # Checked eagerly so a keyless poll raises the typed skip signal, and a
        # dateless one the loud error, before any socket work.
        since = self._require_configured()
        conn = self._connect()
        try:
            typ, data = conn.uid("SEARCH", None, "SINCE", imap_date(since))
            if typ != "OK":
                raise RuntimeError(f"IMAP UID SEARCH failed: {typ}")
            found = sorted(
                {(u.decode() if isinstance(u, bytes) else str(u))
                 for u in (data[0].split() if data and data[0] else [])},
                key=int,
            )
            logged = processed(self._uid_validity or "0")
            new = [u for u in found if u not in logged]
            take = new if limit is None else new[:limit]
            messages: list[EmailMessage] = []
            for uid in take:
                ftyp, fdata = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if ftyp != "OK" or not fdata or not isinstance(fdata[0], tuple):
                    continue
                messages.append(parse_message(uid, self._uid_validity or "0", fdata[0][1]))
            return PollBatch(
                messages,
                already_logged=len(found) - len(new),
                deferred=len(new) - len(take),
            )
        finally:
            # LOGOUT only. CLOSE would expunge \Deleted messages on a writable
            # mailbox; EXAMINE already makes this one read-only, and not sending
            # CLOSE at all keeps that true even if the open mode ever changes.
            try:
                conn.logout()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# test/dev fake
# --------------------------------------------------------------------------- #
class FakeEmailAdapter:
    """In-memory mailbox for tests/dev. `fetch_new` returns the injected
    messages whose UID the ledger callback does not already hold, in the order
    given, honouring `limit` and counting what it held back — the same contract
    as the IMAP adapter, minus the network and the start date."""

    def __init__(
        self,
        messages: list[EmailMessage] | None = None,
        *,
        mailbox_id: str = "fake/inbox@example.test/INBOX",
        error: Exception | None = None,
    ) -> None:
        self._messages = list(messages or [])
        self._mailbox_id = mailbox_id
        self._error = error

    @property
    def mailbox_id(self) -> str:
        return self._mailbox_id

    def fetch_new(
        self, *, processed: ProcessedUids, limit: int | None = None
    ) -> PollBatch:
        if self._error is not None:
            raise self._error
        logged_by_epoch: dict[str, set[str]] = {}
        new: list[EmailMessage] = []
        for m in self._messages:
            if m.uid_validity not in logged_by_epoch:
                logged_by_epoch[m.uid_validity] = processed(m.uid_validity)
            if m.uid not in logged_by_epoch[m.uid_validity]:
                new.append(m)
        take = new if limit is None else new[:limit]
        return PollBatch(
            take,
            already_logged=len(self._messages) - len(new),
            deferred=len(new) - len(take),
        )


def make_email_adapter(cfg) -> EmailAdapter:
    """Config-switched adapter over the closed `email` enum (`imap | fake`).
    `cfg` is the full composed `Config`. Credentials may be empty — the IMAP
    adapter raises `EmailNotConfigured` from `fetch_new`, so a misconfigured
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
            since=cfg.email.poll_since,
        )
    if name == "fake":
        return FakeEmailAdapter()
    raise ValueError(f"unknown email adapter {name!r} (closed enum: imap | fake)")
