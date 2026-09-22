"""Reading a supplier's contact address off a document we already hold.

Like `mine_srn`, the properties that keep this honest are mostly REFUSALS,
because a wrong address is worse than no address: a renewal request sent to the
notified body looks like it went to the manufacturer, and nobody finds out until
the certificate lapses.

  * a certifier, regulator or consultancy is never written, whatever else it
    looks like;
  * another company's domain on a document filed under this manufacturer is
    reported, because it means the document is misfiled or a brand-parent
    relation nobody recorded -- both are findings, not contacts;
  * a named individual is reported, never written: the column may hold personal
    data, but a machine proposing to write to a person it found in a PDF is a
    different act from a person choosing to;
  * an address on the manufacturer's own domain that is NOT a role mailbox is
    reported rather than written -- the role list is what makes an address
    survive staff turnover;
  * a column somebody has already filled is never overwritten;
  * dry run by default, like every other write-capable tool in the CLI.
"""

from __future__ import annotations

import pytest

from app import mine_contacts

VOCO = "VOCO"
DETAX = "DETAX"
ACME = "ACME DENTAL"
NAMES = [VOCO, DETAX, ACME]


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE document_text RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE item_document, evidence, document "
                      "RESTART IDENTITY CASCADE")
            c.execute("DELETE FROM manufacturer WHERE canonical_name = ANY(%s)",
                      (NAMES,))
    wipe()
    yield
    wipe()


def _manufacturer(conn, name, domains=None, contacts=None):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, body, contact_emails) "
        "VALUES (%s, %s, %s) ON CONFLICT (canonical_name) DO UPDATE "
        "  SET body = EXCLUDED.body, contact_emails = EXCLUDED.contact_emails",
        (name, __import__("json").dumps({"domains": domains or []}), contacts))


def _seed_doc(conn, content_hash, text, *, manufacturer=VOCO):
    row = conn.execute(
        "INSERT INTO document (content_hash, type, regulation, "
        "  canonical_manufacturer, coverage_scope, archive_url, status) "
        "VALUES (%s,'EC','MDR',%s,'manufacturer',%s,'staged') RETURNING doc_id",
        (content_hash, manufacturer, f"/archive/x/ec/{content_hash[:12]}__c.pdf"),
    ).fetchone()
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, "
        "  chars, content) VALUES (%s,'pdf-text','pymupdf',1,%s,%s) "
        "ON CONFLICT (content_hash) DO UPDATE SET content = EXCLUDED.content",
        (content_hash, len(text), text))
    return row["doc_id"]


def _only(writable, name):
    return next((w for w in writable if w["canonical_name"] == name), None)


# --- what it writes --------------------------------------------------------

def test_a_role_mailbox_on_the_manufacturers_own_domain_is_written(conn):
    _manufacturer(conn, VOCO, domains=["voco.de"])
    doc = _seed_doc(conn, "a" * 64, "Questions: info@voco.de or call us.")

    writable, _reported, counts = mine_contacts.plan(conn)

    hit = _only(writable, VOCO)
    assert hit is not None
    assert hit["addresses"] == ["info@voco.de"]
    assert hit["source_doc_ids"] == [doc]
    assert counts["writable"] == 1

    mine_contacts.apply(conn, writable)
    assert conn.execute(
        "SELECT contact_emails, updated_by FROM manufacturer WHERE canonical_name=%s",
        (VOCO,)).fetchone()["contact_emails"] == ["info@voco.de"]


def test_the_domain_is_matched_on_its_registrable_label(conn):
    """A playbook authors the domain its DOCUMENTS live on; a company sends mail
    from another. VOCO authors `voco.dental` and writes from `voco.de`."""
    _manufacturer(conn, VOCO, domains=["voco.dental"])
    _seed_doc(conn, "b" * 64, "regulatory@voco.de")

    writable, _r, _c = mine_contacts.plan(conn)
    assert _only(writable, VOCO)["addresses"] == ["regulatory@voco.de"]


def test_role_mailboxes_rank_before_the_rest(conn):
    """Order decides which box a letter is addressed to first."""
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "c" * 64, "quality@voco.de and info@voco.de")

    writable, _r, _c = mine_contacts.plan(conn)
    assert _only(writable, VOCO)["addresses"] == ["info@voco.de", "quality@voco.de"]


# --- what it refuses -------------------------------------------------------

def test_a_notified_body_is_never_written(conn):
    """The one that would look right and be wrong: a renewal chase sent to the
    certifier instead of the maker."""
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "d" * 64, "Certificate issued by DNV: medcert-info@dnv.com")

    writable, reported, counts = mine_contacts.plan(conn)
    assert _only(writable, VOCO) is None
    assert counts["third_party"] == 1
    assert reported[0]["address"] == "medcert-info@dnv.com"


def test_another_companys_domain_is_reported_with_whose_it_is(conn):
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _manufacturer(conn, DETAX, domains=["detax.com"])
    _seed_doc(conn, "e" * 64, "post@detax.com", manufacturer=VOCO)

    writable, reported, counts = mine_contacts.plan(conn)
    assert _only(writable, VOCO) is None
    assert counts["other_manufacturer"] == 1
    assert reported[0]["matched"] == DETAX


def test_a_named_individual_is_reported_never_written(conn):
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "f" * 64, "a.novak@voco.de")

    writable, reported, counts = mine_contacts.plan(conn)
    assert _only(writable, VOCO) is None
    assert counts["personal"] == 1
    assert reported[0]["personal"] is True


def test_own_domain_but_not_a_role_mailbox_is_reported_not_written(conn):
    """`marketing@voco.com` is the right company and the wrong mailbox. It goes
    to a person because it is the pile worth reading, not because it is bad."""
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "0" * 64, "marketing@voco.de")

    writable, reported, counts = mine_contacts.plan(conn)
    assert _only(writable, VOCO) is None
    assert counts["own_domain_not_role"] == 1
    assert reported[0]["verdict"] == "manufacturer"


def test_a_document_with_no_manufacturer_yields_nothing(conn):
    """There is nobody to attribute the address to, and inferring one from the
    filename is the guess this whole module avoids."""
    _seed_doc(conn, "1" * 64, "info@somewhere.com", manufacturer=None)

    writable, _reported, counts = mine_contacts.plan(conn)
    assert writable == []
    assert counts["no_manufacturer"] == 1


def test_an_unauthored_domain_cannot_be_matched(conn):
    """No domains authored means nothing to compare against -- the address is
    reported for a person, never assumed to be the manufacturer's."""
    _manufacturer(conn, ACME, domains=[])
    _seed_doc(conn, "2" * 64, "info@acme-dental.com", manufacturer=ACME)

    writable, reported, counts = mine_contacts.plan(conn)
    assert writable == []
    assert counts["unknown"] == 1
    assert reported[0]["role"] is True     # drives the "author the domain" note


# --- what it never touches -------------------------------------------------

def test_a_column_somebody_has_filled_is_left_alone(conn):
    """After the editor shipped, a filled column carries a decision. A re-run
    that reverted it would be indistinguishable from a successful import."""
    _manufacturer(conn, VOCO, domains=["voco.de"],
                  contacts=["dealersupport@voco.de"])
    _seed_doc(conn, "3" * 64, "info@voco.de")

    writable, _reported, counts = mine_contacts.plan(conn)
    assert _only(writable, VOCO) is None
    assert counts["already_addressed"] == 1
    assert conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name=%s",
        (VOCO,)).fetchone()["contact_emails"] == ["dealersupport@voco.de"]


def test_apply_re_checks_the_column_it_is_about_to_fill(conn):
    """A person may fill it between the dry run and the apply. The guard is what
    makes the command safe to re-run without re-reading."""
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "4" * 64, "info@voco.de")
    writable, _r, _c = mine_contacts.plan(conn)

    conn.execute("UPDATE manufacturer SET contact_emails = %s "
                 " WHERE canonical_name = %s", (["someone@voco.de"], VOCO))

    stats = mine_contacts.apply(conn, writable)
    assert stats["written"] == 0
    assert conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name=%s",
        (VOCO,)).fetchone()["contact_emails"] == ["someone@voco.de"]


def test_plan_writes_nothing(conn):
    _manufacturer(conn, VOCO, domains=["voco.de"])
    _seed_doc(conn, "5" * 64, "info@voco.de")

    mine_contacts.plan(conn)
    assert conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name=%s",
        (VOCO,)).fetchone()["contact_emails"] in (None, [])
