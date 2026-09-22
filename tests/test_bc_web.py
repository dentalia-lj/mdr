"""The two ways a person reaches the Business Central writeback.

Both are producer-only, like every other POST in this app: they enqueue
`bc.push` and never open an HTTP connection to BC. An outbound call inside a
request handler would sit outside the domain lease, outside the retry ladder,
and would make a page's response time depend on someone else's ERP.

Runs as the real `dentalia_api` role, so "it cannot write" is proven by grants
rather than by intention.
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import bc_fields
from app.config import Web
from app.handlers import bc_push
from web import bc_push_view
from tests.conftest import TEST_API_URL as API_TEST_URL
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                     imports_dir=str(tmp_path))))


@pytest.fixture
def seeded(conn):
    """An item with a live group-scope declaration, committed so the app's own
    connection can see it."""
    suffix = uuid.uuid4().hex[:12]
    item_ref = f"BCW-{suffix}"
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) "
        "VALUES (%s,'Widget','t',%s,TRUE,'LJ',now())", (item_ref, f"R-{suffix}"))
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, "
        "status, content_hash, archive_url, coverage_scope) "
        "VALUES ('DoC','MDR','2022-01-01','2031-01-01','production',%s,'/a.pdf',"
        "'group') RETURNING doc_id", (f"h-{suffix}",)).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')", (item_ref, doc_id))
    conn.commit()
    return item_ref


def _jobs(conn):
    return conn.execute(
        "SELECT payload, priority FROM job WHERE type='bc.push' ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------- #
# one item
# --------------------------------------------------------------------------- #
def test_the_item_button_enqueues_and_does_not_write(client, conn, seeded):
    resp = client.post(f"/items/{seeded}/bc-push", follow_redirects=False)

    assert resp.status_code == 303
    jobs = _jobs(conn)
    assert [j["payload"]["item_refs"] for j in jobs] == [[seeded]]
    assert jobs[0]["priority"] == "interactive", "a person is waiting"


def test_pressing_it_twice_in_a_day_enqueues_once(client, conn, seeded):
    """Same dedupe shape as the re-discover button: one push per item per day,
    and a terminal job never blocks tomorrow's."""
    client.post(f"/items/{seeded}/bc-push", follow_redirects=False)
    client.post(f"/items/{seeded}/bc-push", follow_redirects=False)

    assert len(_jobs(conn)) == 1


def test_the_outcome_travels_back_on_the_redirect(client, seeded):
    """No flash store exists in this app, so the result rides the query string
    the way `rediscover` does."""
    resp = client.post(f"/items/{seeded}/bc-push", follow_redirects=False)

    assert "bc_push=queued" in resp.headers["location"]


def test_the_web_role_cannot_write_the_ledger(conn, seeded):
    """The grant, not the code, is what makes this true: 063 gives
    `dentalia_api` SELECT and nothing else."""
    with psycopg.connect(API_TEST_URL) as api:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(
                "INSERT INTO bc_push_log (item_ref, field, new_value) "
                "VALUES (%s,'pteWarehouseURL','u')", (seeded,))


# --------------------------------------------------------------------------- #
# in bulk
# --------------------------------------------------------------------------- #
def test_the_preview_says_what_would_change_and_enqueues_nothing(client, conn,
                                                                 seeded):
    """Preview then apply, like /import. Looking must never write."""
    resp = client.get("/bc-push")

    assert resp.status_code == 200
    assert seeded in resp.text
    assert _jobs(conn) == []


def test_apply_enqueues_the_batch(client, conn, seeded):
    client.post("/bc-push/apply", data={"confirm": "1"}, follow_redirects=False)

    assert [j["payload"]["item_refs"] for j in _jobs(conn)] == [[seeded]]


def test_the_preview_recomputes_rather_than_replaying_what_was_shown(client,
                                                                     conn, seeded):
    """Deliberately not spooled: a preview held in a store can go stale between
    the two presses, and applying a stale diff writes yesterday's answer."""
    client.get("/bc-push")
    conn.execute("UPDATE item_document SET status='retracted' WHERE item_ref=%s",
                 (seeded,))
    conn.commit()

    client.post("/bc-push/apply", data={"confirm": "1"}, follow_redirects=False)

    assert [j["payload"]["item_refs"] for j in _jobs(conn)] == [[seeded]], (
        "the item is still selected -- what changed is the value it will carry"
    )


def test_an_unprocessed_item_is_not_offered(client, conn):
    """`bc.push` would skip it; offering it would make the preview's counts a
    promise the run cannot keep."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) "
        "VALUES ('BCW-BARE','W','t','R',TRUE,'LJ',now())")
    conn.commit()

    resp = client.get("/bc-push")

    assert "BCW-BARE" not in resp.text


# --------------------------------------------------------------------------- #
# the preview reads the certificate the way the push does
# --------------------------------------------------------------------------- #
@pytest.fixture
def certified(conn):
    """An item under a named manufacturer, its EC document carrying a
    certificate number, committed. The pieces the certificate join needs."""

    def _seed(*, manufacturer="ACME-PREVIEW", cert_number="HZ 2158053-1"):
        suffix = uuid.uuid4().hex[:12]
        item_ref = f"BCW-{suffix}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
            "md_flag, catalogue, updated_at) "
            "VALUES (%s,'Widget','t',%s,TRUE,'LJ',now())",
            (item_ref, f"R-{suffix}"))
        group_id = conn.execute(
            "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
            "RETURNING group_id", (manufacturer,)).fetchone()["group_id"]
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'manual')", (group_id, item_ref))
        doc_id = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, validity_to, "
            "status, content_hash, archive_url, coverage_scope, cert_number) "
            "VALUES ('EC','MDR','2022-01-01','2031-01-01','production',%s,'/a.pdf',"
            "'group',%s) RETURNING doc_id",
            (f"h-{suffix}", cert_number)).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list','production')", (item_ref, doc_id))
        conn.commit()
        return item_ref

    return _seed


def _trust(conn, name):
    """A trusted SRN for one canonical manufacturer."""
    srn = f"DE-MF-{uuid.uuid4().int % 10**9:09d}"
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,%s,'register-exact','auto')", (name, srn))
    return srn


def _eudamed_cert(conn, number, srn, status, revision=""):
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "actor_srn, certificate_status, synced_at) VALUES (%s,%s,%s,%s,now())",
        (number, revision, srn, status))


def _previewed(conn, item_ref):
    """What the bulk preview says it would send for one item."""
    plan = bc_push_view.plan(conn, Web())
    for row in plan["rows"]:
        if row["item_ref"] == item_ref:
            return row["changed"]
    return {}


def _pushed(conn, item_ref):
    """What `bc.push` actually computes for the same item."""
    holdings = conn.execute(bc_push._HOLDINGS_SQL, (item_ref,)).fetchall()
    return bc_fields.fields_for(
        item_ref, holdings, processed=bool(holdings), today=dt.date.today(),
        base_url=Web().public_base_url, link_key=Web().bc_link_key)


def test_another_manufacturers_certificate_cannot_falsify_the_preview(conn,
                                                                      certified):
    """The preview is what an operator reads before pressing Apply, so it has to
    answer the question the push will answer. Live on 2026-09-14: 55 of the 123
    production documents carrying a certificate number match a FOREIGN actor on
    the bare number, touching 640 production-linked items. A number collision is
    not evidence -- only a certificate under the item's own manufacturer's
    trusted SRN is (the rule `bc.push` has applied since 2026-09-11)."""
    item_ref = certified()
    _trust(conn, "ACME-PREVIEW")
    _eudamed_cert(conn, "HZ 2158053-1", "CN-MF-000009139", "withdrawn")
    conn.commit()

    assert _previewed(conn, item_ref)["pteValidCECertificate"] is True


def test_the_preview_sees_our_own_manufacturers_withdrawal(conn, certified):
    """The scoping must not blind it: a withdrawal under the item's own trusted
    SRN still falsifies, in the preview as in the push."""
    item_ref = certified()
    srn = _trust(conn, "ACME-PREVIEW")
    _eudamed_cert(conn, "HZ 2158053-1", srn, "withdrawn")
    conn.commit()

    assert _previewed(conn, item_ref)["pteValidCECertificate"] is False


def test_the_latest_revision_decides_in_the_preview_too(conn, certified):
    """An unscoped join also returns every revision, letting an older `issued`
    row outvote a newer withdrawal."""
    item_ref = certified()
    srn = _trust(conn, "ACME-PREVIEW")
    _eudamed_cert(conn, "HZ 2158053-1", srn, "issued", revision="1")
    _eudamed_cert(conn, "HZ 2158053-1", srn, "withdrawn", revision="2")
    conn.commit()

    assert _previewed(conn, item_ref)["pteValidCECertificate"] is False


def test_the_preview_and_the_push_agree(conn, certified):
    """The one assertion that outlives both queries: whatever the rule is, the
    page an operator reads and the job that writes must compute it the same way."""
    item_ref = certified()
    _trust(conn, "ACME-PREVIEW")
    _eudamed_cert(conn, "HZ 2158053-1", "CN-MF-000009139", "withdrawn")
    conn.commit()

    assert _previewed(conn, item_ref) == _pushed(conn, item_ref)
