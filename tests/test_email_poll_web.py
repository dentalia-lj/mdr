"""Inbound-email UI (S2.4) — the producer-only /emails page. Same boundary as
the rest of web/: a GET render over the durable `email_poll_log`, and the
`dentalia_api` role is structurally unable to write it (invariant 1). Runs
against the real test Postgres (no mocking); rows are committed by the owner
connection because the TestClient reads on its own `dentalia_api` connection.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

USEFUL_HASH = "a" * 64
DEDUPED_HASH = "b" * 64


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _seed_email(conn, *, uid, from_addr, subject, had_attachments, attachments,
                summary=None, intent=None, status="no-body", model=None):
    conn.execute(
        "INSERT INTO email_poll_log (mailbox, uid_validity, imap_uid, message_id, "
        "from_addr, subject, had_attachments, attachments, body_summary, "
        "summary_intent, summary_status, summary_model) "
        "VALUES ('mb/rep@x/INBOX', '1', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (uid, f"<{uid}@x>", from_addr, subject, had_attachments, Json(attachments),
         summary, intent, status, model),
    )


def _seed_document(conn, content_hash) -> int:
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group',%s,'file:///x','production') "
        "RETURNING doc_id", (content_hash,),
    ).fetchone()["doc_id"]


def test_emails_page_renders_rows_and_verdicts(client, conn):
    doc_id = _seed_document(conn, USEFUL_HASH)
    _seed_email(
        conn, uid="1", from_addr="rep@manu.example", subject="Updated DoC",
        had_attachments=True,
        attachments=[
            {"filename": "cert.pdf", "content_type": "application/pdf",
             "verdict": "useful", "reason": None, "content_hash": USEFUL_HASH,
             "archive_url": "file:///a", "disposition": "archived"},
            {"filename": "sds.pdf", "content_type": "application/pdf",
             "verdict": "skipped", "reason": "noise-msds",
             "content_hash": None, "archive_url": None, "disposition": None},
        ],
    )
    conn.commit()

    resp = client.get("/emails")
    assert resp.status_code == 200
    body = resp.text
    assert "rep@manu.example" in body
    assert "cert.pdf" in body
    # useful attachment links through to the registry document
    assert f"/documents/{doc_id}" in body
    # skipped attachment shows its reason
    assert "noise-msds" in body


def test_emails_page_useful_without_document_says_it_is_being_read(client, conn):
    _seed_email(
        conn, uid="2", from_addr="a@b.c", subject="cert",
        had_attachments=True,
        attachments=[{"filename": "x.pdf", "content_type": "application/pdf",
                      "verdict": "useful", "reason": None,
                      "content_hash": DEDUPED_HASH, "archive_url": "file:///b",
                      "disposition": "archived"}],
    )
    conn.commit()

    resp = client.get("/emails")
    assert resp.status_code == 200
    # "Being read", not "in extraction": one word per thing, and the word is
    # the § 9 one (office UI redesign spec, P7c).
    assert "Being read" in resp.text


def test_emails_page_empty_state(client):
    resp = client.get("/emails")
    assert resp.status_code == 200
    assert "No emails processed yet" in resp.text


def test_emails_nav_link_present(client):
    resp = client.get("/emails")
    assert 'href="/emails"' in resp.text


def test_api_role_cannot_write_email_poll_log():
    """Invariant 1, proven structurally: the web role can only SELECT the ledger."""
    with psycopg.connect(TEST_API_URL) as api:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(
                "INSERT INTO email_poll_log (mailbox, uid_validity, imap_uid) "
                "VALUES ('x', '1', '1')"
            )
        api.rollback()


# --------------------------------------------------------------------------- #
# body summary (migration 030). The page must carry what a message asked for,
# and must never let that read as the sender's own words.
# --------------------------------------------------------------------------- #
def test_the_summary_and_its_intent_render_on_the_row(client, conn):
    _seed_email(
        conn, uid="9", from_addr="rep@manu.example", subject="RE: MDR DOCUMENTS",
        had_attachments=False, attachments=[],
        summary="Asks for the renewed Nexco DoC, expiring 04-05-2026.",
        intent="requests-info", status="ok", model="claude-haiku-4-5",
    )
    conn.commit()

    text = client.get("/emails").text

    assert "Asks for the renewed Nexco DoC" in text
    assert "requests-info" in text


def test_the_summary_is_attributed_so_it_is_not_read_as_the_sender_s_words(client, conn):
    """A summary rendered bare is indistinguishable from a quote. The model id
    is what makes the difference visible on the page."""
    _seed_email(
        conn, uid="9", from_addr="rep@manu.example", subject="s",
        had_attachments=False, attachments=[],
        summary="Sends the MDR certificate.", intent="sends-documents",
        status="ok", model="claude-haiku-4-5",
    )
    conn.commit()

    text = client.get("/emails").text

    # The model id is its own line under the summary (class email-summary-src),
    # not a clause inside the sentence -- attribution has to be present without
    # competing with the text it annotates.
    assert "email-summary-src" in text
    assert "claude-haiku-4-5" in text
    assert "body itself is not stored" in text


def test_a_failed_summary_says_so_rather_than_leaving_the_cell_blank(client, conn):
    """A blank cell reads as "the email said nothing", which is a different fact
    from "we could not summarise it" (never-silent)."""
    _seed_email(
        conn, uid="9", from_addr="rep@manu.example", subject="s",
        had_attachments=False, attachments=[], status="llm-error",
    )
    conn.commit()

    text = client.get("/emails").text

    # Says what failed, in words, rather than printing the status slug -- the
    # cell is read by a person chasing documents, not by whoever wrote the enum.
    assert "summary failed" in text
    assert "the body was read but not compressed" in text


def test_a_message_with_no_body_adds_no_noise_to_the_row(client, conn):
    """A bare PDF with no covering note is the common case. Saying "no summary:
    no-body" on every such row would be true and useless."""
    _seed_email(
        conn, uid="9", from_addr="rep@manu.example", subject="s",
        had_attachments=False, attachments=[], status="no-body",
    )
    conn.commit()

    assert "no summary" not in client.get("/emails").text


def test_the_page_states_that_the_body_is_not_kept(client, conn):
    """The ruling behind the slice, on the page that acts on it."""
    text = client.get("/emails").text

    assert "compressed, and dropped" in text


# --------------------------------------------------------------------------- #
# Reply matching (2026-09-11): a message `email.poll` linked to a renewal
# request says which chase it answers, under its subject, linking to that
# request's draft. A sender-address match is a guess and says so.
# --------------------------------------------------------------------------- #
def _request_with_draft(conn):
    rid = conn.execute(
        "INSERT INTO renewal_request (manufacturer, state, period_key) "
        "VALUES ('IVOCLAR','awaiting','2026-W37') RETURNING id").fetchone()["id"]
    did = conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, to_addrs, "
        "subject, body, status) VALUES (%s,'request','IVOCLAR','{}',%s,'b','draft') "
        "RETURNING id", (rid, f"RE: MDR DOCUMENTS [DENT-{rid}]")).fetchone()["id"]
    return rid, did


def test_a_reply_names_the_request_it_answers_and_links_its_draft(client, conn):
    rid, did = _request_with_draft(conn)
    _seed_email(conn, uid="1", from_addr="q@ivoclar.example",
                subject=f"RE: RE: MDR DOCUMENTS [DENT-{rid}]",
                had_attachments=False, attachments=[])
    conn.execute("UPDATE email_poll_log SET renewal_request_id=%s WHERE imap_uid='1'",
                 (rid,))
    conn.commit()

    body = client.get("/emails").text

    assert f'href="/drafts/{did}"' in body
    assert f"Reply to request #{rid}, IVOCLAR" in body
    assert "matched by sender" not in body


def test_a_sender_match_is_labelled_as_one(client, conn):
    rid, _ = _request_with_draft(conn)
    _seed_email(conn, uid="1", from_addr="q@ivoclar.example", subject="Our certificates",
                had_attachments=False, attachments=[])
    conn.execute("UPDATE email_poll_log SET renewal_request_id=%s WHERE imap_uid='1'",
                 (rid,))
    conn.commit()

    body = client.get("/emails").text

    assert f"Reply to request #{rid}, IVOCLAR" in body
    assert "matched by sender address" in body
