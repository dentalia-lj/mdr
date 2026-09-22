"""Outbound renewal-draft UI (S2.4) — /drafts list, detail, edit, state machine.

This is the ONE web surface that writes something other than `upload_inbox`
(migration 029 grants `dentalia_api` SELECT + UPDATE on `email_draft`), so the
boundary is asserted structurally here and not just at app level: the same role
must still be unable to touch the registry (invariant 1), and a released draft
must stop being editable so the text a person is about to send by hand cannot
change under them (spec §7.1 — the system never sends).

Real Postgres, no mocking. Rows are committed by the owner connection because
the TestClient reads and writes on its own `dentalia_api` connection.
"""

from __future__ import annotations

import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _seed_document(conn, content_hash, expires="2026-05-04") -> int:
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, validity_to) "
        "VALUES ('DoC','MDR','group',%s,'file:///x','production',%s) RETURNING doc_id",
        (content_hash, expires),
    ).fetchone()["doc_id"]


def _seed_draft(conn, *, manufacturer="IVOCLAR", period="2026-W34", kind="reminder",
                status="draft", doc_ids=()) -> int:
    req = conn.execute(
        "INSERT INTO renewal_request (doc_id, state, manufacturer, reason, period_key, "
        "created_at, updated_at) VALUES (%s,'due',%s,'expiry',%s, now(), now()) RETURNING id",
        (doc_ids[0] if doc_ids else None, manufacturer, period),
    ).fetchone()["id"]
    for d in doc_ids:
        conn.execute(
            "INSERT INTO renewal_request_document (renewal_request_id, doc_id) VALUES (%s,%s)",
            (req, d),
        )
    return conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, to_addrs, subject, body, status) "
        "VALUES (%s,%s,%s,'RE: MDR DOCUMENTS','Dear all,',%s) RETURNING id",
        (req, kind, ["quality@ivoclar.example"], status),
    ).fetchone()["id"]


def test_board_lists_one_row_per_manufacturer_period(client, conn):
    d1 = _seed_document(conn, "a" * 64)
    d2 = _seed_document(conn, "b" * 64)
    # The cadence rule (spec §7.2): two expiring documents, ONE draft.
    _seed_draft(conn, doc_ids=(d1, d2))
    conn.commit()

    body = client.get("/drafts").text
    assert "IVOCLAR" in body
    # The period is a week, and is labelled as one on every screen that shows
    # a week (office UI redesign spec § 9): the bucket key `2026-W34` is ours
    # and lives under the draft's Technical details.
    assert "Week 34 (17–23 Aug)" in body
    # the document COUNT is on the row, not two rows
    assert body.count("RE: MDR DOCUMENTS") == 1


def test_detail_lists_every_document_in_the_chase(client, conn):
    d1 = _seed_document(conn, "c" * 64)
    d2 = _seed_document(conn, "d" * 64)
    draft = _seed_draft(conn, doc_ids=(d1, d2))
    conn.commit()

    body = client.get(f"/drafts/{draft}").text
    assert f"/documents/{d1}" in body
    assert f"/documents/{d2}" in body


def test_edit_saves_and_records_the_editor(client, conn):
    draft = _seed_draft(conn, doc_ids=(_seed_document(conn, "e" * 64),))
    conn.commit()

    resp = client.post(f"/drafts/{draft}", data={
        "subject": "RE: MDR DOCUMENTS", "body": "Dear Sara,",
        "to_addrs": "natasa@dentalia.si, quality@ivoclar.example"})
    assert resp.status_code == 200

    row = conn.execute(
        "SELECT subject, body, to_addrs, edited_by, edited_at FROM email_draft WHERE id=%s",
        (draft,)).fetchone()
    assert row["body"] == "Dear Sara,"
    assert row["to_addrs"] == ["natasa@dentalia.si", "quality@ivoclar.example"]
    assert row["edited_by"]
    assert row["edited_at"] is not None


@pytest.mark.parametrize("payload,reason", [
    ({"subject": "", "body": "x", "to_addrs": "a@b.si"}, "empty subject"),
    ({"subject": "x", "body": "   ", "to_addrs": "a@b.si"}, "whitespace body"),
    ({"subject": "x", "body": "y", "to_addrs": "  "}, "no recipient"),
])
def test_edit_rejects_unsendable_drafts(client, conn, payload, reason):
    draft = _seed_draft(conn, doc_ids=(_seed_document(conn, "f" * 64),))
    conn.commit()
    assert client.post(f"/drafts/{draft}", data=payload).status_code == 422, reason


@pytest.mark.parametrize("start,action,expected", [
    ("draft", "ready", 200),
    ("draft", "cancelled", 200),
    ("draft", "sent", 422),        # cannot skip approval
    ("ready", "sent", 200),
    ("ready", "ready", 422),       # not twice
    ("sent", "cancelled", 422),    # terminal
    ("draft", "deleted", 422),     # not a vocabulary member
])
def test_status_transitions(client, conn, start, action, expected):
    draft = _seed_draft(conn, status=start, doc_ids=(_seed_document(conn, "0" * 63 + "1"),))
    conn.commit()
    resp = client.post(f"/drafts/{draft}/status", data={"action": action})
    assert resp.status_code == expected


def test_a_released_draft_is_no_longer_editable(client, conn):
    """The point of `ready`: a person has approved this exact text and is about
    to send it by hand. It must not change under them."""
    draft = _seed_draft(conn, status="ready", doc_ids=(_seed_document(conn, "9" * 64),))
    conn.commit()

    resp = client.post(f"/drafts/{draft}", data={
        "subject": "changed", "body": "changed", "to_addrs": "a@b.si"})
    assert resp.status_code == 422
    assert conn.execute(
        "SELECT subject FROM email_draft WHERE id=%s", (draft,)
    ).fetchone()["subject"] == "RE: MDR DOCUMENTS"


def test_web_role_still_cannot_write_the_registry(test_db_url):
    """Migration 029 widened the web role by exactly one table. Invariant 1 is
    asserted here, not assumed: the same connection that may UPDATE a draft must
    still be refused on `document`, and must not be able to INSERT or DELETE
    drafts either (those belong to the unbuilt handler, and a draft nobody wants
    is `cancelled`, never removed)."""
    with psycopg.connect(TEST_API_URL, autocommit=True) as api:
        for sql in (
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url) VALUES ('DoC','MDR','group','z','x')",
            "INSERT INTO email_draft (kind, to_addrs, subject, body, status) "
            "VALUES ('request','{a@b.si}','s','b','draft')",
            "DELETE FROM email_draft",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                api.execute(sql)


# --------------------------------------------------------------------------- #
# The two mirrors: /expiry and /documents/{id} link to /drafts and must name a
# draft the way /drafts names it (spec § 9, rule 2).
# --------------------------------------------------------------------------- #
# T9 was told to leave the raw `email_draft.status` to T10, and T10's brief
# covered /emails, /drafts and the draft detail. These two cross-links fell
# between the briefs: a draft read "cancelled" on /expiry and "Archived" on the
# page that link opens.
DRAFT_WORDS = {"draft": "Draft", "ready": "Ready to send",
               "sent": "Sent", "cancelled": "Archived"}


def _seed_covered_document(conn, content_hash, manufacturer="IVOCLAR",
                           expires="2026-05-04") -> int:
    """A production document with a production link into a group, so `/expiry`
    can name its manufacturer (`_LAPSING_CTE` reads it off `item_group`)."""
    from tests.fixtures import seed_ui

    doc = _seed_document(conn, content_hash, expires=expires)
    ref = seed_ui.seed_item(conn, f"exp-{content_hash[:6]}",
                            manufacturer_raw=manufacturer)
    seed_ui.seed_link(conn, ref, doc, match_basis="ref-list", status="production")
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
        "RETURNING group_id", (manufacturer,)).fetchone()["group_id"]
    conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                 "VALUES (%s,%s,'ref-list')", (gid, ref))
    return doc


@pytest.mark.parametrize("status,word", sorted(DRAFT_WORDS.items()))
def test_the_document_page_names_a_draft_the_way_drafts_does(
        client, conn, status, word):
    doc = _seed_covered_document(conn, "e" * 64)
    draft_id = _seed_draft(conn, status=status, doc_ids=(doc,))
    conn.commit()

    page = client.get(f"/documents/{doc}").text
    # `[^<]*`, not `.*?`: the page's first badge is the document's own status
    # pill, and a lazy dot-all would start there and swallow the whole card.
    m = re.search(r'<span class="badge[^"]*">([^<]*)</span>\s*'
                  rf'<a href="/drafts/{draft_id}"', page)
    assert m, f"no chase pill for draft {draft_id}"
    assert m.group(1).strip() == word


@pytest.mark.parametrize("status,word", [("draft", "Draft"),
                                         ("ready", "Ready to send")])
def test_expiry_names_a_draft_the_way_drafts_does(client, conn, status, word):
    """`/expiry` only ever shows a live chase, so only those two reach it."""
    doc = _seed_covered_document(conn, "f" * 64)
    draft_id = _seed_draft(conn, status=status, doc_ids=(doc,))
    conn.commit()

    page = client.get("/expiry?back=3650").text
    m = re.search(rf'<a href="/drafts/{draft_id}">'
                  r'<span class="badge[^"]*">(.*?)</span>', page, re.S)
    assert m, f"no chase pill for draft {draft_id}"
    assert m.group(1).strip() == word
