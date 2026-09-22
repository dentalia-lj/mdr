"""Asking whether a five-year-old declaration is still current (F34's last part).

A declaration carries no expiry of its own, so Dentalia lists it five years
after issue. Ruled 2026-09-11: that is review due, not expired, and what it
earns is "please confirm this is still current" -- never a renewal demand.
F34 taught four screens to draw that line; this is the letter, which had no
kind of its own until now.

Asking the wrong question here is not a small error. A class I device has no
notified body and no certificate, so this horizon is the only trigger it will
ever produce, and a renewal demand asks the manufacturer to reissue a document
that has not lapsed and that they are under no obligation to reissue.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.handlers import email_request as er

ACME = "REVIEW TEST GMBH"


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE email_draft RESTART IDENTITY CASCADE")
            c.execute("DELETE FROM item_document WHERE item_ref LIKE 'REV-%'")
            c.execute("DELETE FROM item_group_member WHERE item_ref LIKE 'REV-%'")
            c.execute("DELETE FROM item_group WHERE canonical_manufacturer = %s", (ACME,))
            c.execute("DELETE FROM item_mirror WHERE item_ref LIKE 'REV-%'")
            c.execute("DELETE FROM document WHERE archive_url LIKE '/rev/%'")
            c.execute("DELETE FROM manufacturer WHERE canonical_name = %s", (ACME,))
    wipe()
    yield
    wipe()


def _doc(conn, *, issued_years_ago=6, stated_expiry=None, item="REV-1",
         stated_class="I"):
    """A declaration linked to one device item of one group.

    With an issue date and no stated expiry and no cited certificate, migration
    027 gives it `basis='staleness'` -- which is the whole population this
    letter is for.
    """
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (ACME,))
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "  md_flag, updated_at) VALUES (%s,'Probe','081','LJ',TRUE,now()) "
        "ON CONFLICT DO NOTHING", (item,))
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,'Rev') "
        "RETURNING group_id", (ACME,)).fetchone()["group_id"]
    conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                 "VALUES (%s,%s,'manual')", (gid, item))
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, status, "
        "  content_hash, archive_url, validity_from, validity_to, stated_class) "
        "VALUES ('DoC','MDR','group','production',%s,%s, "
        "  current_date - make_interval(years => %s), %s, %s) RETURNING doc_id",
        (f"h-rev-{item}-{issued_years_ago}", f"/rev/{item}.pdf",
         issued_years_ago, stated_expiry, stated_class),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')", (item, doc_id))
    return doc_id


def _run(conn, reason="review-due"):
    return er.handle_email_request(
        conn, {"id": 1, "payload": {"manufacturer": ACME, "reason": reason}})


def _draft(conn):
    return conn.execute(
        "SELECT * FROM email_draft WHERE kind='review-confirm' "
        "ORDER BY id DESC LIMIT 1").fetchone()


def test_a_declaration_past_five_years_becomes_a_confirmation_request(conn):
    doc_id = _doc(conn)

    res = _run(conn)

    assert res["counts"]["review_drafted"] == 1
    assert res["counts"]["review_documents"] == 1
    d = _draft(conn)
    assert d["manufacturer"] == ACME
    assert f"#{doc_id}" in d["body"]


def test_the_letter_asks_for_confirmation_and_never_for_a_renewal(conn):
    """The ruling in one assertion. These documents have not lapsed, and a
    renewal demand against a class I declaration asks for something the
    manufacturer does not owe."""
    _doc(conn)

    _run(conn)
    body = _draft(conn)["body"].lower()

    assert "still current" in body
    # It may SAY the documents state no expiry -- that is the reason for the
    # letter. What it must never do is demand a renewal or claim a lapse.
    assert "please renew" not in body and "has expired" not in body
    assert "cannot pick up the goods" not in body


def test_the_letter_names_the_issue_date_not_an_expiry(conn):
    """Printing "expired 2020-01-01" against a document that has not expired
    would be the same error F34 fixed on the screens, in writing, to the
    manufacturer."""
    _doc(conn)

    _run(conn)

    assert "issued" in _draft(conn)["body"]


def test_a_document_with_a_stated_expiry_is_not_this_letter(conn):
    """That is a renewal, and `compose` already writes it. The filter is
    `basis='staleness'` -- the same column the item card, /expiry and the weekly
    report read, which is what F34 was about.

    Issued two years ago with a date of its own that has passed: the stated date
    comes first, so migration 027 gives it `basis='stated'`. (A stated date
    LATER than the five-year horizon loses to the horizon, by that migration's
    own tie rule -- whichever comes first is the date shown -- so this fixture
    has to put the stated date early to be about `stated` at all.)"""
    _doc(conn, issued_years_ago=2,
         stated_expiry=dt.date.today() - dt.timedelta(days=30))

    res = _run(conn)

    assert res["counts"]["no_review_due"] == 1
    assert _draft(conn) is None


def test_a_declaration_inside_its_five_years_is_not_asked_about(conn):
    _doc(conn, issued_years_ago=2)

    res = _run(conn)

    assert res["counts"]["no_review_due"] == 1


def test_it_does_not_stack_a_second_open_ask(conn):
    _doc(conn)
    _run(conn)

    res = _run(conn)

    assert res["counts"]["review_already_open"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM email_draft WHERE kind='review-confirm'"
    ).fetchone()["c"] == 1


def test_it_may_ask_again_once_the_last_one_was_sent(conn):
    """Unlike the gap and certificate asks, this one recurs by design: the
    horizon moves, and a declaration confirmed in 2026 is due again in 2031."""
    _doc(conn)
    _run(conn)
    conn.execute("UPDATE email_draft SET status='sent' WHERE kind='review-confirm'")

    res = _run(conn)

    assert res["counts"]["review_drafted"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM email_draft WHERE kind='review-confirm'"
    ).fetchone()["c"] == 2


def test_it_takes_no_cadence_slot(conn):
    """It hangs off no `renewal_request`; consuming a cadence slot would silence
    that manufacturer's real renewal mail for a whole period."""
    _doc(conn)

    _run(conn)

    assert _draft(conn)["renewal_request_id"] is None
    assert conn.execute(
        "SELECT count(*) c FROM renewal_request WHERE manufacturer=%s", (ACME,)
    ).fetchone()["c"] == 0
