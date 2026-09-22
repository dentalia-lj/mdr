"""email.poll — S2.4 EMAIL inbound handler (spec: docs/specs/email.md §11).

Table-driven against real Postgres (CLAUDE.md: no mocking Postgres), with a
FakeEmailAdapter and a LocalFsStore injected. Asserts the two distinct dedupes
(processed-email ledger vs content-hash), the classify/skip verdicts, the
archive+emit spine hand-off, and that nothing is skipped silently.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app import queue
from app.adapters.email import EmailAttachment, EmailMessage, EmailNotConfigured, FakeEmailAdapter
from app.adapters.storage import LocalFsStore
from app.handlers.email_poll import handle_email_poll
from app.workers import runner

pymupdf = pytest.importorskip("pymupdf")

FAKE_MAILBOX = "fake/inbox@example.test/INBOX"


def _pdf(text: str = "") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


COMPLIANCE_PDF = _pdf("Declaration of Conformity  Regulation (EU) 2017/745")
MSDS_PDF = _pdf("Safety Data Sheet  section 1 identification")
BUSINESS_PDF = _pdf("Invoice No 12345  delivery note")
EMPTY_PDF = _pdf("")  # no text layer -> classify 'unknown' -> proceeds (useful)
# An inline logo, which is the bulk of real mailbox noise. It used to be a
# truncated zip signature -- but a zip is now OPENED rather than refused at
# the door (archives carry the documents), so that fixture stopped testing
# the non-pdf path and started testing the unreadable-archive one. Both are
# covered now, separately.
NON_PDF = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _att(content: bytes, filename="doc.pdf", ct="application/pdf") -> EmailAttachment:
    return EmailAttachment(filename=filename, content_type=ct, content=content)


def _msg(uid: str, atts, *, uid_validity="1", message_id=None, body="") -> EmailMessage:
    return EmailMessage(
        uid=uid, uid_validity=uid_validity,
        message_id=message_id or f"<{uid}@manu.example>",
        from_addr="rep@manu.example", subject="cert", received_at=None,
        in_reply_to=None, references=None, attachments=list(atts),
        body_text=body,
    )


class _NoLlm:
    """The default injected client: constructing is fine, calling is not. A test
    that summarises must say so by passing its own stub -- otherwise an
    accidental live call is a loud failure here rather than quiet spend."""

    @property
    def messages(self):
        raise AssertionError("the summariser was called without a stub client")


class _StubLlm:
    def __init__(self, summary="Asks for the renewed Nexco DoC.",
                 intent="requests-info", raise_exc=None):
        self._payload = json.dumps({"summary": summary, "intent": intent})
        self._raise = raise_exc
        self.calls = 0

    def _create(self, **kwargs):
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._payload)],
            usage=SimpleNamespace(input_tokens=850, output_tokens=32),
        )

    @property
    def messages(self):
        return SimpleNamespace(create=self._create)


#: Long enough to clear `email.summary_min_chars` (40).
BODY = (
    "Dear all, I just found a DOC for Nexco that expired in 04-05-2026. "
    "Please send the renewed declaration of conformity for the whole family "
    "together with the EC certificate. Best regards, Natasa"
)


def _run(conn, messages, store, *, adapter=None, llm=None):
    adapter = adapter or FakeEmailAdapter(messages, mailbox_id=FAKE_MAILBOX)
    return handle_email_poll(conn, {"id": 1, "payload": {}}, adapter=adapter,
                             store=store, llm=llm or _NoLlm())


def _log_row(conn, uid="1"):
    return conn.execute(
        "SELECT body_summary, summary_intent, summary_status, summary_model, "
        "       summary_input_tokens, summary_output_tokens, summary_cost_usd "
        "FROM email_poll_log WHERE imap_uid=%s", (uid,)
    ).fetchone()


def _jobs(conn, dedupe_key):
    return conn.execute(
        "SELECT type FROM job WHERE dedupe_key=%s", (dedupe_key,)
    ).fetchall()


def _ledger(conn):
    return conn.execute(
        "SELECT * FROM email_poll_log ORDER BY id"
    ).fetchall()


# --- new compliance PDF: archive + extract.doc -----------------------------
def test_an_archive_containing_a_pdf_is_not_a_pdf():
    """A ZIP of documents is the single most common shape a supplier mailbox
    carries, and `%PDF` lands inside the first 1024 bytes of one whose first
    entry is a PDF -- at offset 45 in the fixture below. The old substring gate
    accepted it, archived it and emitted an `extract.doc`, i.e. paid an LLM to
    parse a file it cannot open. Decided by the container's OWN signature."""
    import io
    import zipfile

    from app.handlers.email_poll import _is_pdf

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("declaration.pdf", b"%PDF-1.7\n" + b"x" * 200)
    blob = buf.getvalue()
    assert blob[:4] == b"PK\x03\x04"
    assert b"%PDF" in blob[:1024]          # this is why the old gate was fooled

    assert _is_pdf(_Att(blob)) is False


def test_a_pdf_with_leading_junk_is_still_a_pdf():
    """The tolerance is deliberate and must survive the fix: real-world PDFs
    carry leading bytes (a stray BOM, an HTTP preamble), and the format itself
    only requires `%PDF-` within the first 1024 bytes."""
    from app.handlers.email_poll import _is_pdf

    assert _is_pdf(_Att(b"\xef\xbb\xbf\n\n%PDF-1.4\nbody")) is True


class _Att:
    """Minimal stand-in for EmailAttachment: the gate reads `.content` only."""

    def __init__(self, content: bytes) -> None:
        self.content = content


# --------------------------------------------------------------------------- #
# archives: a supplier who sends documents.zip is sending documents
# --------------------------------------------------------------------------- #
def _zip(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    # DEFLATED, not the zipfile default of STORED: a stored entry has
    # compress_size == file_size, so a "bomb" fixture built with the default
    # has a ratio of 1 and proves nothing.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, blob in entries.items():
            z.writestr(name, blob)
    return buf.getvalue()


def _expand(atts, **overrides):
    """expand_containers against the real config, with the caps overridden."""
    import dataclasses

    from app.config import load_config
    from app.handlers.email_poll import expand_containers

    cfg = load_config()
    cfg = dataclasses.replace(cfg, email=dataclasses.replace(cfg.email, **overrides))
    return expand_containers(atts, cfg)


def test_zip_yields_its_pdf_members(conn, tmp_path):
    """The whole point: the PDF inside the archive reaches the spine."""
    store = LocalFsStore(str(tmp_path))
    h = hashlib.sha256(COMPLIANCE_PDF).hexdigest()

    res = _run(conn, [_msg("40", [_att(_zip({"declaration.pdf": COMPLIANCE_PDF}),
                                       filename="documents.zip",
                                       ct="application/zip")])], store)

    assert res["counts"]["archives"] == 1
    assert res["counts"]["archive_members"] == 1
    assert res["counts"]["archived"] == 1
    assert [r["type"] for r in _jobs(conn, f"extract:{h}")] == ["extract.doc"]
    att = _ledger(conn)[0]["attachments"][0]
    # the ledger says where it actually arrived, not just what it was called
    assert att["filename"] == "documents.zip!declaration.pdf"
    assert att["disposition"] == "archived"


def test_zip_members_are_classified_like_any_attachment(conn, tmp_path):
    """Noise inside an archive is still noise -- expansion must not become a
    way to smuggle an MSDS past the classifier."""
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("41", [_att(_zip({"sds.pdf": MSDS_PDF,
                                             "doc.pdf": COMPLIANCE_PDF}),
                                       filename="mixed.zip", ct="application/zip")])],
               store)
    assert res["counts"]["skipped_noise"] == 1
    assert res["counts"]["archived"] == 1


def test_nested_archive_is_refused_not_followed():
    inner = _zip({"declaration.pdf": COMPLIANCE_PDF})
    members, refusals, counts = _expand([_att(_zip({"inner.zip": inner}),
                                              filename="outer.zip", ct="application/zip")])
    assert members == []
    assert counts["archive_nested"] == 1
    assert refusals[0]["reason"] == "archive-nested"


def test_zip_bomb_is_refused_on_declared_sizes():
    """Decided before a byte is decompressed: checking after reading is not a
    guard, it is memory exhaustion."""
    bomb = _zip({"boom.pdf": b"\0" * (2 * 1024 * 1024)})   # compresses ~2000:1
    members, refusals, counts = _expand([_att(bomb, filename="boom.zip",
                                              ct="application/zip")],
                                        zip_max_ratio=200)
    assert members == []
    assert counts["archive_suspicious_ratio"] == 1


def test_member_over_the_size_cap_is_refused():
    big = _zip({"big.pdf": b"%PDF-1.7" + b"random-ish" * 200_000})
    members, refusals, counts = _expand([_att(big, filename="big.zip",
                                              ct="application/zip")],
                                        zip_max_member_mb=1, zip_max_ratio=10_000)
    assert members == []
    assert counts["archive_member_too_large"] == 1


def test_too_many_members_refuses_the_whole_archive():
    many = _zip({f"d{i}.pdf": COMPLIANCE_PDF for i in range(6)})
    members, refusals, counts = _expand([_att(many, filename="many.zip",
                                              ct="application/zip")],
                                        zip_max_members=5)
    assert members == []
    assert counts["archive_too_many_members"] == 1
    assert "6 > 5" in refusals[0]["reason"]


def test_unreadable_archive_is_recorded_not_crashed():
    members, refusals, counts = _expand([_att(b"PK\x03\x04 truncated",
                                              filename="broken.zip",
                                              ct="application/zip")])
    assert members == []
    assert counts["archive_unreadable"] == 1


def test_a_docx_is_a_zip_with_nothing_in_it_for_us():
    """Never silent: an archive we opened and found nothing usable in still
    gets a row, because an attachment that produces no row reads as 'there was
    no attachment'."""
    docx = _zip({"word/document.xml": b"<w:document/>",
                 "[Content_Types].xml": b"<Types/>"})
    members, refusals, counts = _expand([_att(docx, filename="letter.docx",
                                              ct="application/vnd.openxmlformats-"
                                                 "officedocument.wordprocessingml.document")])
    assert members == []
    assert counts["archive_no_usable_members"] == 1
    assert refusals[0]["filename"] == "letter.docx"


def test_expansion_can_be_turned_off():
    members, refusals, counts = _expand(
        [_att(_zip({"declaration.pdf": COMPLIANCE_PDF}), filename="documents.zip",
              ct="application/zip")],
        zip_expand=False)
    assert members == []
    assert counts["skipped_archive"] == 1
    assert refusals[0]["reason"] == "archive-not-expanded"


def test_new_compliance_pdf_archives_and_emits_extract(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    h = hashlib.sha256(COMPLIANCE_PDF).hexdigest()

    res = _run(conn, [_msg("10", [_att(COMPLIANCE_PDF)])], store)

    assert res["counts"]["archived"] == 1
    assert res["counts"]["messages_seen"] == 1
    # fetch ledger records email provenance
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE source='email' AND content_hash=%s", (h,)
    ).fetchone()["c"] == 1
    # spine hand-off
    assert [r["type"] for r in _jobs(conn, f"extract:{h}")] == ["extract.doc"]
    # processed-email ledger row with the useful verdict
    rows = _ledger(conn)
    assert len(rows) == 1
    assert rows[0]["mailbox"] == FAKE_MAILBOX
    assert rows[0]["had_attachments"] is True
    att = rows[0]["attachments"][0]
    assert att["verdict"] == "useful"
    assert att["disposition"] == "archived"
    assert att["content_hash"] == h
    assert rows[0]["emitted_jobs"] == [{"type": "extract.doc", "dedupe_key": f"extract:{h}"}]


def test_empty_text_pdf_is_useful_not_skipped(conn, tmp_path):
    # A scan / no-text-layer PDF classifies 'unknown' and proceeds; EXTRACT's
    # vision tier handles it. It must never be false-skipped at the door.
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("11", [_att(EMPTY_PDF)])], store)
    assert res["counts"]["archived"] == 1
    assert res["counts"].get("skipped_noise", 0) == 0


# --- content already extracted: dedupe, no new extract.doc -----------------
def test_seen_hash_no_group_dedupes_without_emit(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    h = hashlib.sha256(COMPLIANCE_PDF).hexdigest()
    # content already extracted (an earlier document): hash_extracted_rev != None
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields) "
        "VALUES (%s, 'T1', '{}'::jsonb)", (h,),
    )

    res = _run(conn, [_msg("12", [_att(COMPLIANCE_PDF)])], store)

    assert res["counts"]["deduped"] == 1
    assert res["counts"].get("archived", 0) == 0
    # inbound has no requesting group -> nothing to link, no validate.doc
    assert _jobs(conn, f"validate:{h}:1:None") == []
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='extract.doc'"
    ).fetchone()["c"] == 0
    assert _ledger(conn)[0]["attachments"][0]["disposition"] == "deduped"


# --- reprocess guard: a recorded UID is never processed again ---------------
def test_already_processed_uid_is_skipped(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    conn.execute(
        "INSERT INTO email_poll_log (mailbox, uid_validity, imap_uid, had_attachments) "
        "VALUES (%s, '1', '20', true)", (FAKE_MAILBOX,),
    )

    res = _run(conn, [_msg("20", [_att(COMPLIANCE_PDF)])], store)

    assert res["counts"]["already_processed"] == 1
    assert res["counts"].get("archived", 0) == 0
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='extract.doc'"
    ).fetchone()["c"] == 0
    # no second ledger row inserted for the same coordinates
    assert conn.execute("SELECT count(*) c FROM email_poll_log").fetchone()["c"] == 1


def test_message_id_guards_across_uidvalidity_reset(conn, tmp_path):
    # A mailbox migration bumps UIDVALIDITY and the same message reappears under
    # a new UID — the Message-ID guard (within the mailbox) still recognises it.
    store = LocalFsStore(str(tmp_path))
    conn.execute(
        "INSERT INTO email_poll_log (mailbox, uid_validity, imap_uid, message_id, "
        "had_attachments) VALUES (%s, '1', '50', '<dup@manu.example>', true)",
        (FAKE_MAILBOX,),
    )
    # same Message-ID, different uid_validity + uid
    msg = _msg("99", [_att(COMPLIANCE_PDF)], uid_validity="2",
               message_id="<dup@manu.example>")

    res = _run(conn, [msg], store)

    assert res["counts"]["already_processed"] == 1
    assert res["counts"].get("archived", 0) == 0
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='extract.doc'"
    ).fetchone()["c"] == 0


def test_repoll_same_mailbox_is_idempotent(conn, tmp_path):
    # Two polls of the same message (the \Seen flag is NOT the authority): the
    # second poll re-sees the message but the ledger guard makes it a no-op.
    store = LocalFsStore(str(tmp_path))
    h = hashlib.sha256(COMPLIANCE_PDF).hexdigest()
    msg = _msg("21", [_att(COMPLIANCE_PDF)])
    adapter = FakeEmailAdapter([msg], mailbox_id=FAKE_MAILBOX)

    _run(conn, None, store, adapter=adapter)   # first poll: archived
    # a fresh adapter that still returns the same UID (simulates \Seen lost)
    adapter2 = FakeEmailAdapter([msg], mailbox_id=FAKE_MAILBOX)
    res2 = _run(conn, None, store, adapter=adapter2)

    assert res2["counts"]["already_processed"] == 1
    assert res2["counts"].get("archived", 0) == 0
    assert conn.execute("SELECT count(*) c FROM email_poll_log").fetchone()["c"] == 1
    assert len(_jobs(conn, f"extract:{h}")) == 1


# --- noise skips (reusing the existing classifier) -------------------------
# `ids=` is not cosmetic here. `MSDS_PDF`/`BUSINESS_PDF` are built at module
# scope by PyMuPDF, whose `tobytes()` embeds a wall-clock-dependent `/ID` in the
# trailer and produces a different compressed stream on every call. Without
# explicit ids pytest derives the test id from the bytes themselves, so two
# xdist workers importing this module a moment apart collect DIFFERENT test ids
# and the whole run aborts with "Different tests were collected between gwN and
# gwM" before a single test executes. Seen four times; the fourth run named this
# parametrize and showed the two workers' trailers differing at `/ID[<...>]` and
# at `/Length 111` vs `/Length 103`.
# Intermittent rather than constant: the ids only have to differ where two
# workers actually compare them.
@pytest.mark.parametrize("body,reason", [
    (MSDS_PDF, "noise-msds"),
    (BUSINESS_PDF, "noise-business"),
], ids=["msds", "business"])
def test_noise_pdf_skipped_and_recorded(conn, tmp_path, body, reason):
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("30", [_att(body)])], store)

    assert res["counts"]["skipped_noise"] == 1
    assert res["counts"].get("archived", 0) == 0
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='extract.doc'"
    ).fetchone()["c"] == 0
    att = _ledger(conn)[0]["attachments"][0]
    assert att["verdict"] == "skipped"
    assert att["reason"] == reason


def test_non_pdf_attachment_skipped(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("31", [_att(NON_PDF, filename="logo.png", ct="image/png")])], store)

    assert res["counts"]["skipped_non_pdf"] == 1
    assert res["counts"].get("pdf", 0) == 0
    att = _ledger(conn)[0]["attachments"][0]
    assert att["verdict"] == "skipped"
    assert att["reason"] == "non-pdf"


def test_message_with_no_attachments_records_row(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("32", [])], store)

    assert res["counts"]["messages_seen"] == 1
    assert res["counts"].get("attachments", 0) == 0
    row = _ledger(conn)[0]
    assert row["had_attachments"] is False
    assert row["attachments"] == []


def test_mixed_attachments_one_useful_one_noise(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    h = hashlib.sha256(COMPLIANCE_PDF).hexdigest()
    res = _run(conn, [_msg("33", [
        _att(COMPLIANCE_PDF, filename="cert.pdf"),
        _att(MSDS_PDF, filename="sds.pdf"),
    ])], store)

    assert res["counts"]["archived"] == 1
    assert res["counts"]["skipped_noise"] == 1
    atts = _ledger(conn)[0]["attachments"]
    assert len(atts) == 2
    verdicts = {a["filename"]: (a["verdict"], a.get("reason"), a["disposition"]) for a in atts}
    assert verdicts["cert.pdf"] == ("useful", None, "archived")
    assert verdicts["sds.pdf"] == ("skipped", "noise-msds", None)
    assert len(_jobs(conn, f"extract:{h}")) == 1


# --- not configured: a keyless poll is a skipped rung, not a dead job ------
def test_not_configured_returns_cleanly(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    adapter = FakeEmailAdapter(error=EmailNotConfigured("no creds"))
    res = handle_email_poll(conn, {"id": 1, "payload": {}}, adapter=adapter, store=store)

    assert res["outcome"] == "not-configured"
    assert _ledger(conn) == []
    assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == 0


# --- end to end through the runner -----------------------------------------
def test_end_to_end_through_runner(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    fake = FakeEmailAdapter([_msg("40", [_att(COMPLIANCE_PDF)])], mailbox_id=FAKE_MAILBOX)
    jid = queue.enqueue(conn, "email.poll", {"mailbox": "INBOX", "since": "b"},
                        "email.poll:b")
    conn.commit()

    handlers = {"email.poll": lambda c, j: handle_email_poll(c, j, adapter=fake, store=store)}
    assert runner.run_once(conn, "w", handlers=handlers) is True

    row = conn.execute("SELECT status, result FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["status"] == "done"
    assert row["result"]["counts"]["archived"] == 1
    assert row["result"]["outcome"] == "polled"


# --------------------------------------------------------------------------- #
# body summary (migration 030). What `/emails` gets to say about a message, and
# what never reaches the database: the body itself.
# --------------------------------------------------------------------------- #
def test_the_summary_lands_on_the_row_with_its_model_and_cost(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    llm = _StubLlm()

    res = _run(conn, [_msg("1", [_att(COMPLIANCE_PDF)], body=BODY)], store, llm=llm)

    row = _log_row(conn)
    assert row["body_summary"] == "Asks for the renewed Nexco DoC."
    assert row["summary_intent"] == "requests-info"
    assert row["summary_status"] == "ok"
    assert row["summary_model"]
    assert row["summary_input_tokens"] == 850
    assert row["summary_output_tokens"] == 32
    assert row["summary_cost_usd"] is not None
    assert res["counts"]["summary_ok"] == 1


def test_the_body_itself_is_never_stored(conn, tmp_path):
    """The whole reason the slice is a summary: a compliance registry keeps
    documents for ten years, and supplier correspondence is not a document."""
    store = LocalFsStore(str(tmp_path))
    _run(conn, [_msg("1", [_att(COMPLIANCE_PDF)], body=BODY)], store, llm=_StubLlm())

    whole_row = conn.execute(
        "SELECT to_jsonb(t) AS j FROM email_poll_log t WHERE imap_uid='1'"
    ).fetchone()["j"]

    assert "Best regards" not in json.dumps(whole_row)
    assert "renewed declaration of conformity" not in json.dumps(whole_row)


def test_a_message_with_no_body_records_the_reason_and_never_calls(conn, tmp_path):
    """`_NoLlm` raises on use, so this also proves no client is built when
    there is nothing to summarise."""
    store = LocalFsStore(str(tmp_path))
    res = _run(conn, [_msg("1", [_att(COMPLIANCE_PDF)])], store)

    assert _log_row(conn)["summary_status"] == "no-body"
    assert _log_row(conn)["body_summary"] is None
    assert res["counts"]["summary_no_body"] == 1


def test_a_short_body_is_summarised_like_any_other(conn, tmp_path):
    """No floor by default (Denis, 2026-08-21): a one-line body is exactly the
    case where a blank summary cell reads as "nothing to say", so it gets the
    same treatment as a long one."""
    store = LocalFsStore(str(tmp_path))
    llm = _StubLlm(summary="Confirms the documents arrived.", intent="acknowledges")

    res = _run(conn, [_msg("1", [_att(COMPLIANCE_PDF)], body="Thanks, received.")],
               store, llm=llm)

    assert llm.calls == 1
    assert _log_row(conn)["summary_status"] == "ok"
    assert _log_row(conn)["body_summary"] == "Confirms the documents arrived."
    assert res["counts"]["summary_ok"] == 1


def test_a_failed_summary_does_not_cost_the_poll_its_documents(conn, tmp_path):
    """The documents are the point of the poll; the summary is context around
    them. An API outage must not lose an arriving declaration."""
    store = LocalFsStore(str(tmp_path))
    llm = _StubLlm(raise_exc=RuntimeError("overloaded_error"))

    res = _run(conn, [_msg("1", [_att(COMPLIANCE_PDF)], body=BODY)], store, llm=llm)

    assert res["counts"]["archived"] == 1
    assert _jobs(conn, f"extract:{hashlib.sha256(COMPLIANCE_PDF).hexdigest()}")
    assert _log_row(conn)["summary_status"] == "llm-error"
    assert res["counts"]["summary_llm_error"] == 1


def test_a_failed_summary_is_never_retried(conn, tmp_path):
    """"Run once" is structural, not a flag: the row is recorded either way, and
    the UID guard means the message is never polled again. A missing line of UI
    context is not worth a second call."""
    store = LocalFsStore(str(tmp_path))
    failing = _StubLlm(raise_exc=RuntimeError("overloaded_error"))
    msg = _msg("1", [_att(COMPLIANCE_PDF)], body=BODY)
    _run(conn, [msg], store, llm=failing)

    working = _StubLlm()
    _run(conn, [msg], store, llm=working)

    assert working.calls == 0
    assert _log_row(conn)["summary_status"] == "llm-error"


def test_a_message_is_summarised_exactly_once(conn, tmp_path):
    store = LocalFsStore(str(tmp_path))
    llm = _StubLlm()
    msg = _msg("1", [_att(COMPLIANCE_PDF)], body=BODY)

    _run(conn, [msg], store, llm=llm)
    _run(conn, [msg], store, llm=llm)
    _run(conn, [msg], store, llm=llm)

    assert llm.calls == 1


def test_the_summary_is_independent_of_whether_anything_was_attached(conn, tmp_path):
    """"Please send the DoC" carries no attachment and is exactly the message a
    human needs to see on /emails."""
    store = LocalFsStore(str(tmp_path))
    llm = _StubLlm(summary="Asks us to send the DoC for REF 4056.",
                   intent="requests-info")

    res = _run(conn, [_msg("1", [], body=BODY)], store, llm=llm)

    assert res["counts"].get("attachments", 0) == 0
    assert _log_row(conn)["body_summary"].startswith("Asks us to send")


# --------------------------------------------------------------------------- #
# Reply matching ([email-reply-matching], 2026-09-11). Drafts carry
# `[DENT-{request_id}]` in the subject; a reply keeps it. Without it, the
# sender's domain is tried against the contacts of manufacturers with exactly
# one open request. The link lands on `email_poll_log.renewal_request_id`,
# which had existed with no writer since migration 006.
# --------------------------------------------------------------------------- #
def _reply(uid, subject, from_addr="rep@ivoclar.example"):
    return EmailMessage(
        uid=uid, uid_validity="1", message_id=f"<{uid}@reply.example>",
        from_addr=from_addr, subject=subject, received_at=None,
        in_reply_to=None, references=None, attachments=[], body_text="",
    )


def _open_request(conn, manufacturer="IVOCLAR", contacts=("q@ivoclar.example",),
                  state="awaiting", period="2026-W37"):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, contact_emails) VALUES (%s,%s) "
        "ON CONFLICT (canonical_name) DO UPDATE SET contact_emails = excluded.contact_emails",
        (manufacturer, list(contacts)))
    return conn.execute(
        "INSERT INTO renewal_request (manufacturer, state, period_key) "
        "VALUES (%s,%s,%s) RETURNING id", (manufacturer, state, period)).fetchone()["id"]


def _linked(conn, uid):
    return conn.execute(
        "SELECT renewal_request_id FROM email_poll_log WHERE imap_uid=%s",
        (uid,)).fetchone()["renewal_request_id"]


def test_a_reply_carrying_its_reference_is_linked_to_that_request(conn, tmp_path):
    rid = _open_request(conn)
    _open_request(conn, period="2026-W38")   # a second open one: the token decides

    out = _run(conn, [_reply("1", f"RE: RE: MDR DOCUMENTS [DENT-{rid}]")],
               LocalFsStore(str(tmp_path)))

    assert _linked(conn, "1") == rid
    assert out["counts"]["reply_linked_by_reference"] == 1


def test_a_reply_without_the_reference_is_linked_by_its_sender(conn, tmp_path):
    rid = _open_request(conn)

    out = _run(conn, [_reply("1", "Our certificates")], LocalFsStore(str(tmp_path)))

    assert _linked(conn, "1") == rid
    assert out["counts"]["reply_linked_by_sender"] == 1


def test_a_sender_with_two_open_requests_is_left_unlinked(conn, tmp_path):
    _open_request(conn)
    _open_request(conn, period="2026-W38")

    out = _run(conn, [_reply("1", "Our certificates")], LocalFsStore(str(tmp_path)))

    assert _linked(conn, "1") is None
    assert out["counts"]["reply_unlinked"] == 1


def test_a_closed_request_is_never_the_sender_fallback(conn, tmp_path):
    _open_request(conn, state="received")

    _run(conn, [_reply("1", "Our certificates")], LocalFsStore(str(tmp_path)))

    assert _linked(conn, "1") is None


def test_an_unknown_reference_falls_back_to_the_sender(conn, tmp_path):
    rid = _open_request(conn)

    _run(conn, [_reply("1", "RE: MDR DOCUMENTS [DENT-999999]")],
         LocalFsStore(str(tmp_path)))

    assert _linked(conn, "1") == rid


def test_our_own_domain_is_never_a_sender_match(conn, tmp_path):
    """A forward from the office's own mailbox names no supplier."""
    _open_request(conn, contacts=("mdr@dentalia.si",))
    msgs = [_reply("1", "FWD: izjava", from_addr="natasa@dentalia.si")]
    own = FakeEmailAdapter(msgs, mailbox_id="imap/dokumentacija@dentalia.si/INBOX")

    _run(conn, msgs, LocalFsStore(str(tmp_path)), adapter=own)

    assert _linked(conn, "1") is None
