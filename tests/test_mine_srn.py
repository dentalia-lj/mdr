"""Reading an SRN off a document we already hold.

The properties that keep this honest are mostly REFUSALS, because a confirmed
SRN makes a manufacturer's ENTIRE catalogue sweepable against EUDAMED:

  * nothing is ever written `auto`. The pattern matches an importer's or a
    notified body's SRN quoted inside somebody else's document just as happily
    as the manufacturer's own, and only a person can tell those apart;
  * a document with no `canonical_manufacturer` yields nothing -- there is no
    manufacturer to attribute the SRN to, and guessing from the filename is
    exactly the kind of inference this table's `status` column exists to stop;
  * a manufacturer that already holds a TRUSTED SRN is skipped, so re-running
    cannot pile duplicates behind a decision already taken;
  * the source document is recorded, so a reviewer can open the page rather
    than confirm a bare string;
  * dry run by default, like every other write-capable tool in the CLI.
"""

from __future__ import annotations

import pytest

from app import mine_srn


GC = "GC EUROPE N.V."
VOCO = "VOCO"
SRN_GC = "BE-MF-000001608"
SRN_VOCO = "DE-MF-000008601"


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE manufacturer_srn RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE document_text RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE item_document, evidence, document "
                      "RESTART IDENTITY CASCADE")
            c.execute("DELETE FROM manufacturer WHERE canonical_name = ANY(%s)",
                      ([GC, VOCO, "ACME"],))
    wipe()
    yield
    wipe()


def _seed_doc(conn, content_hash, text, *, manufacturer=GC, doc_id=None):
    if manufacturer is not None:
        conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                     "ON CONFLICT (canonical_name) DO NOTHING", (manufacturer,))
    row = conn.execute(
        "INSERT INTO document (doc_id, content_hash, type, regulation, "
        "  canonical_manufacturer, coverage_scope, archive_url, status) "
        "VALUES (COALESCE(%s, nextval('document_doc_id_seq')), %s, 'EC','MDR',"
        "  %s, 'manufacturer', %s, 'staged') RETURNING doc_id",
        (doc_id, content_hash, manufacturer,
         f"/archive/x/ec/{content_hash[:12]}__c.pdf"),
    ).fetchone()
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, "
        "  chars, content) VALUES (%s,'pdf-text','pymupdf',1,%s,%s) "
        "ON CONFLICT (content_hash) DO UPDATE SET content = EXCLUDED.content",
        (content_hash, len(text), text))
    return row["doc_id"]


# --- what it finds ---------------------------------------------------------

def test_an_srn_in_a_held_document_becomes_a_pending_candidate(conn):
    doc = _seed_doc(conn, "a" * 64,
                    f"Manufacturer GC EUROPE N.V., SRN {SRN_GC}, Leuven")

    found, counts = mine_srn.plan(conn)

    assert len(found) == 1
    hit = found[0]
    assert hit["canonical_name"] == GC
    assert hit["srn"] == SRN_GC
    assert hit["source_doc_id"] == doc
    assert counts["scanned"] >= 1

    mine_srn.apply(conn, found)
    row = conn.execute(
        "SELECT status, discovered_via, source_doc_id FROM manufacturer_srn "
        "WHERE canonical_name=%s AND srn=%s", (GC, SRN_GC)).fetchone()
    assert row["status"] == "pending"       # never 'auto' -- a person decides
    assert row["discovered_via"] == "text-mined"
    assert row["source_doc_id"] == doc


def test_nine_digits_not_twelve(conn):
    """The tracker entry that proposed this carried `[0-9]{12}` and therefore
    matched nothing at all. An SRN's numeric part is nine digits."""
    _seed_doc(conn, "b" * 64, "SRN BE-MF-000001608 nine digits")
    _seed_doc(conn, "c" * 64, "SRN BE-MF-000000001608 twelve digits",
              manufacturer=VOCO, doc_id=None)

    found, _ = mine_srn.plan(conn)
    srns = {f["srn"] for f in found}
    assert SRN_GC in srns
    assert not any(len(s.split("-")[-1]) == 12 for s in srns)


def test_one_document_naming_two_srns_yields_both(conn):
    _seed_doc(conn, "d" * 64, f"issued to {SRN_GC} and also {SRN_VOCO}")
    found, _ = mine_srn.plan(conn)
    assert {f["srn"] for f in found} == {SRN_GC, SRN_VOCO}
    # Both attributed to the document's manufacturer, both pending: which one
    # is really GC's is exactly the question the reviewer answers.
    assert {f["canonical_name"] for f in found} == {GC}


# --- what it refuses -------------------------------------------------------

def test_a_document_with_no_manufacturer_yields_nothing(conn):
    _seed_doc(conn, "e" * 64, f"SRN {SRN_GC}", manufacturer=None)
    found, counts = mine_srn.plan(conn)
    assert found == []
    assert counts["no_manufacturer"] == 1


def test_a_manufacturer_that_already_holds_a_trusted_srn_is_skipped(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (GC,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        "  status) VALUES (%s,%s,'register-exact','auto')", (GC, "BE-MF-999999999"))
    _seed_doc(conn, "f" * 64, f"SRN {SRN_GC}")

    found, counts = mine_srn.plan(conn)
    assert found == []
    assert counts["already_trusted"] == 1


def test_a_rejected_row_is_not_re_proposed(conn):
    """A person said no. Re-running must not put it back in their queue."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (GC,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        "  status) VALUES (%s,%s,'text-mined','rejected')", (GC, SRN_GC))
    _seed_doc(conn, "g" * 64, f"SRN {SRN_GC}")

    found, counts = mine_srn.plan(conn)
    assert found == []
    assert counts["already_decided"] == 1


def test_re_running_after_apply_proposes_nothing_new(conn):
    _seed_doc(conn, "h" * 64, f"SRN {SRN_GC}")
    found, _ = mine_srn.plan(conn)
    mine_srn.apply(conn, found)

    again, counts = mine_srn.plan(conn)
    assert again == []
    assert counts["already_decided"] == 1
    assert conn.execute("SELECT count(*) c FROM manufacturer_srn").fetchone()["c"] == 1


def test_plan_writes_nothing(conn):
    _seed_doc(conn, "i" * 64, f"SRN {SRN_GC}")
    mine_srn.plan(conn)
    assert conn.execute("SELECT count(*) c FROM manufacturer_srn").fetchone()["c"] == 0


def test_render_names_every_candidate_and_its_document(conn):
    doc = _seed_doc(conn, "j" * 64, f"SRN {SRN_GC}")
    found, counts = mine_srn.plan(conn)
    out = mine_srn.render(found, counts, apply=False)
    assert SRN_GC in out and GC in out and str(doc) in out
    assert "dry run" in out.lower()
