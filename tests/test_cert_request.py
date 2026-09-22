"""Asking for a certificate EUDAMED says they hold and we do not (F38).

This is the only letter this system can write that names the DOCUMENT. A
declaration gap request names articles and asks the supplier to work out which
document covers them; this one carries the certificate number, its revision,
its dates and the notified body, straight off the manufacturer's own entry in
the public register.

The properties that matter are the same three the gap request has, because it
is the same machinery and the same failure modes:

  * it is a PRODUCER -- the button enqueues, the handler writes the draft, and
    nothing sends (invariant 1, spec 7.1);
  * one ask per manufacturer, any status counting, so a second press after the
    first job finished cannot write a second letter (queue dedupe is scoped to
    ACTIVE jobs by design, which is what made this go wrong for draft 25/26);
  * an empty gap is refused at the button rather than enqueued, because a job
    that can only no-op reads as success.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.handlers import email_request as er

ACME = "ACME DENTAL"
SRN = "DE-MF-000099999"
NB = "0197"


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE email_draft RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE eudamed_certificate RESTART IDENTITY CASCADE")
            c.execute("DELETE FROM trusted_manufacturer_srn "
                      " WHERE canonical_name = %s", (ACME,))
            c.execute("DELETE FROM manufacturer_alias WHERE canonical_name = %s", (ACME,))
            c.execute("DELETE FROM manufacturer WHERE canonical_name = %s", (ACME,))
    wipe()
    yield
    wipe()


def _manufacturer(conn, contacts=None):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, contact_emails) VALUES (%s,%s) "
        "ON CONFLICT (canonical_name) DO UPDATE SET contact_emails=EXCLUDED.contact_emails",
        (ACME, contacts))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,%s,'text-mined','confirmed') ON CONFLICT DO NOTHING",
        (ACME, SRN))


def _certificate(conn, number, *, revision="R001", issued=dt.date(2026, 6, 18),
                 expires=dt.date(2030, 6, 30)):
    conn.execute(
        "INSERT INTO eudamed_certificate (actor_srn, certificate_number, "
        "  revision_number, certificate_type, certificate_status, issue_date, "
        "  expiry_date, notified_body_srn, actor_name, synced_at) "
        "VALUES (%s,%s,%s,'quality-management-system','valid',%s,%s,%s,%s,now()) "
        "ON CONFLICT DO NOTHING",
        (SRN, number, revision, issued, expires, NB, "Acme Dental GmbH"))


def _run(conn, reason="certificate-gap"):
    return er.handle_email_request(
        conn, {"id": 1, "payload": {"manufacturer": ACME, "reason": reason}})


def _draft(conn):
    return conn.execute(
        "SELECT * FROM email_draft WHERE kind='cert-request' "
        "ORDER BY id DESC LIMIT 1").fetchone()


# --- what it writes --------------------------------------------------------

def test_a_gap_becomes_one_draft_naming_every_certificate(conn):
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")
    _certificate(conn, "MDR 835078", revision="R002")

    res = _run(conn)

    assert res["counts"]["cert_drafted"] == 1
    assert res["counts"]["cert_certificates"] == 2
    d = _draft(conn)
    assert d["manufacturer"] == ACME
    assert "MDR 835077" in d["body"] and "MDR 835078" in d["body"]
    assert d["status"] == "draft"


def test_the_letter_carries_what_makes_it_answerable(conn):
    """Number, revision, dates and notified body -- the reason this ask is
    stronger than a declaration gap. A supplier can look it up without asking
    us which document we mean."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")

    _run(conn)
    body = _draft(conn)["body"]

    assert "MDR 835077" in body
    assert "rev. R001" in body
    assert "18.06.2026" in body and "30.06.2030" in body
    assert NB in body


def test_the_letter_says_where_the_numbers_came_from(conn):
    """A recipient who does not know the list came from their own EUDAMED entry
    reads it as a demand. One who does reads it as a reconciliation."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")

    _run(conn)
    assert "EUDAMED" in _draft(conn)["body"]


def test_the_draft_is_addressed_when_a_contact_is_known(conn):
    _manufacturer(conn, contacts=["info@acme.example"])
    _certificate(conn, "MDR 835077")

    _run(conn)
    assert _draft(conn)["to_addrs"] == ["info@acme.example"]


def test_no_contact_is_not_fatal(conn):
    """Drafts-only: a person addresses it, and `/drafts` refuses to release an
    unaddressed letter. Refusing to WRITE it would lose the work instead."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")

    _run(conn)
    assert _draft(conn)["to_addrs"] == []


# --- what it refuses -------------------------------------------------------

def test_no_gap_writes_nothing(conn):
    _manufacturer(conn)

    res = _run(conn)

    assert res["counts"]["no_cert_gap"] == 1
    assert _draft(conn) is None


def test_it_never_asks_twice(conn):
    """Any status counts, `cancelled` included: archived means "we are not
    asking these people". Queue dedupe cannot carry this rule -- it is scoped to
    ACTIVE jobs, so once the first job finished a second press would write a
    second letter."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")
    _run(conn)

    res = _run(conn)

    assert res["counts"]["cert_already_asked"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM email_draft WHERE kind='cert-request'"
    ).fetchone()["c"] == 1


def test_a_cancelled_ask_is_not_re_offered(conn):
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")
    _run(conn)
    conn.execute("UPDATE email_draft SET status='cancelled' WHERE kind='cert-request'")

    res = _run(conn)

    assert res["counts"]["cert_already_asked"] == 1


def test_a_certificate_request_is_not_a_renewal_and_takes_no_cadence_slot(conn):
    """It hangs off no `renewal_request`. Consuming a cadence slot would silence
    that manufacturer's real renewal mail for a whole period."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")

    _run(conn)

    assert _draft(conn)["renewal_request_id"] is None
    assert conn.execute(
        "SELECT count(*) c FROM renewal_request WHERE manufacturer=%s", (ACME,)
    ).fetchone()["c"] == 0


def test_the_gap_branch_and_the_certificate_branch_are_different_letters(conn):
    """Different `kind`, so the drafts board can tell "we have never had a
    declaration for these articles" from "you hold this certificate and we do
    not". Different urgency, often a different person."""
    _manufacturer(conn)
    _certificate(conn, "MDR 835077")

    _run(conn)

    assert _draft(conn)["kind"] == "cert-request"
    assert conn.execute(
        "SELECT count(*) c FROM email_draft WHERE kind='gap-request'"
    ).fetchone()["c"] == 0


# --- the button ------------------------------------------------------------
#
# Its own file rather than test_web.py: the page this renders on is also being
# reworked by the UI redesign, and a test that seeds its own manufacturer
# cannot collide with that work.

from fastapi.testclient import TestClient          # noqa: E402

from app.config import Web                         # noqa: E402
from tests.conftest import TEST_API_URL            # noqa: E402
from web.app import create_app                     # noqa: E402


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _bc_code(conn):
    """The manufacturer page is reachable only for an entity carrying at least
    one BC code -- `manufacturer_detail` returns None without one, which the
    route turns into a 404. The code lives on `manufacturer_alias.raw_name`,
    which is what the detail query reads."""
    # No `vendor_master` row: the detail query LEFT JOINs it for a display
    # name only, so a code with no vendor row is a legitimate state and the
    # lighter fixture is the honest one.
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES ('999',%s,'self-seed') ON CONFLICT DO NOTHING", (ACME,))


def test_the_button_enqueues_and_writes_nothing_itself(conn, client):
    _manufacturer(conn)
    _bc_code(conn)
    _certificate(conn, "MDR 835077")
    conn.commit()

    r = client.post(f"/manufacturers/{ACME}/cert-request", follow_redirects=False)

    assert r.status_code == 303
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='email.request'"
    ).fetchone()["c"] == 1
    # Invariant 1: the producer wrote no draft. The handler does that.
    assert _draft(conn) is None


def test_an_empty_gap_is_refused_at_the_button(conn, client):
    """A job that can only no-op reads as success and teaches an operator to
    distrust the button."""
    _manufacturer(conn)
    _bc_code(conn)
    conn.commit()

    r = client.post(f"/manufacturers/{ACME}/cert-request", follow_redirects=False)

    assert r.status_code == 400
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='email.request'"
    ).fetchone()["c"] == 0


def test_the_page_offers_the_button_only_where_there_is_a_gap(conn, client):
    _manufacturer(conn)
    _bc_code(conn)
    _certificate(conn, "MDR 835077")
    conn.commit()

    body = client.get(f"/manufacturers/{ACME}").text
    assert "cert-request" in body

    conn.execute("TRUNCATE eudamed_certificate RESTART IDENTITY CASCADE")
    conn.commit()
    assert "cert-request" not in client.get(f"/manufacturers/{ACME}").text


def test_expiry_shows_the_findings_without_a_button(conn, client):
    """The same macro renders every manufacturer's gaps on /expiry. One press
    there would mean a letter to each of them -- a bulk action nobody asked for,
    from a screen that exists to be read."""
    _manufacturer(conn)
    _bc_code(conn)
    _certificate(conn, "MDR 835077")
    conn.commit()

    body = client.get("/expiry").text
    assert "MDR 835077" in body
    assert "cert-request" not in body
