"""Backfilling `document.canonical_manufacturer` from manufacturer evidence.

Migration 053 is additive, so every pre-existing document carries NULL. 531 of
the 540 link-less documents hold a `manufacturer` evidence row -- the name as
PRINTED -- and 481 of those resolve to exactly one curated canonical
(measured 2026-08-31). The rest stay NULL rather than being guessed at.
"""
from __future__ import annotations

from psycopg.types.json import Json

from app import repair_document_manufacturer as r


def _seed_entity(conn, name):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _seed_alias(conn, raw, canonical):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES (%s,%s) ON CONFLICT (raw_name) DO NOTHING", (raw, canonical))


def _seed_doc(conn, content_hash, printed, *, bound=None, rev=1):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, canonical_manufacturer) "
        "VALUES ('ISO','n.a.','manufacturer',%s,'file:///a.pdf','filed',%s) "
        "RETURNING doc_id", (content_hash, bound)).fetchone()["doc_id"]
    if printed is not None:
        conn.execute(
            "INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, "
            "tier, confidence, extracted_at, extract_rev) "
            "VALUES (%s,'manufacturer',%s,'file:///a.pdf',%s,'T1',0.95,now(),%s)",
            (doc_id, printed, f"v {printed}", rev))
    return doc_id


def _bound(conn, doc_id):
    return conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["canonical_manufacturer"]


def test_an_unambiguous_name_binds_and_the_rest_are_counted(conn):
    _seed_entity(conn, "BF AG")
    _seed_alias(conn, "BF Ltd", "BF AG")
    good = _seed_doc(conn, "bf-1", "BF Ltd")
    bad = _seed_doc(conn, "bf-2", "Nobody Ltd")

    planned = r.plan(conn)
    assert planned["resolved"] == [
        {"doc_id": good, "canonical_name": "BF AG", "printed": "BF Ltd"}]
    assert [u["doc_id"] for u in planned["unresolved"]] == [bad]
    assert planned["ambiguous"] == []

    assert r.apply(conn, planned["resolved"]) == 1
    assert _bound(conn, good) == "BF AG"
    assert _bound(conn, bad) is None


def test_an_ambiguous_spelling_is_skipped_and_reported_never_guessed(conn):
    """`resolve_canonicals` returning >1 is the guard, not an inconvenience:
    choosing between them is a coin flip over a manufacturer's whole range."""
    _seed_entity(conn, "AMB ONE")
    _seed_entity(conn, "AMB TWO")
    _seed_alias(conn, "Ambiguous Ltd", "AMB ONE")
    _seed_alias(conn, "AMBIGUOUS   LTD", "AMB TWO")
    doc_id = _seed_doc(conn, "bf-amb", "Ambiguous Ltd")

    planned = r.plan(conn)

    assert planned["resolved"] == []
    assert [a["doc_id"] for a in planned["ambiguous"]] == [doc_id]
    assert sorted(planned["ambiguous"][0]["canonicals"]) == ["AMB ONE", "AMB TWO"]
    assert _bound(conn, doc_id) is None
    assert any("a human must choose" in line for line in r.render(planned))


def test_a_binding_already_made_is_never_overwritten(conn):
    """A human decision, or C16's, outranks a string read off a page."""
    _seed_entity(conn, "BF AG")
    _seed_entity(conn, "HUMAN AG")
    _seed_alias(conn, "BF Ltd", "BF AG")
    doc_id = _seed_doc(conn, "bf-3", "BF Ltd", bound="HUMAN AG")

    assert r.plan(conn)["resolved"] == []
    assert _bound(conn, doc_id) == "HUMAN AG"


def test_a_second_run_plans_zero(conn):
    _seed_entity(conn, "BF AG")
    _seed_alias(conn, "BF Ltd", "BF AG")
    _seed_doc(conn, "bf-4", "BF Ltd")

    r.apply(conn, r.plan(conn)["resolved"])

    assert r.plan(conn)["resolved"] == []


def test_the_latest_extraction_revision_wins(conn):
    """A re-extraction that corrected the name is the one that binds -- the
    same DISTINCT ON ... extract_rev DESC rule `decorate_documents` uses."""
    _seed_entity(conn, "NEW AG")
    _seed_alias(conn, "New Ltd", "NEW AG")
    doc_id = _seed_doc(conn, "bf-rev", "Old Misread Ltd", rev=1)
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, "
        "tier, confidence, extracted_at, extract_rev) "
        "VALUES (%s,'manufacturer','New Ltd','file:///a.pdf','v','T1',0.98,now(),2)",
        (doc_id,))

    r.apply(conn, r.plan(conn)["resolved"])

    assert _bound(conn, doc_id) == "NEW AG"


def test_every_binding_leaves_its_own_audit_row(conn):
    _seed_entity(conn, "BF AG")
    _seed_alias(conn, "BF Ltd", "BF AG")
    doc_id = _seed_doc(conn, "bf-audit", "BF Ltd")

    r.apply(conn, r.plan(conn)["resolved"])

    row = conn.execute(
        "SELECT event, decided_by, detail FROM audit_log WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["event"] == "manufacturer-backfilled"
    assert row["decided_by"] == "tool:repair-document-manufacturer"
    assert row["detail"]["canonical_manufacturer"] == "BF AG"
    assert row["detail"]["printed"] == "BF Ltd"


def test_a_name_resolving_to_an_entity_with_no_manufacturer_row_is_declined(conn):
    """The tool and the handler share `_bindable_manufacturer`, so they cannot
    disagree about what the FK will accept."""
    _seed_alias(conn, "Orphan Ltd", "ORPHAN AG")   # alias, but no manufacturer row
    doc_id = _seed_doc(conn, "bf-orphan", "Orphan Ltd")

    planned = r.plan(conn)

    assert planned["resolved"] == []
    assert [u["doc_id"] for u in planned["unresolved"]] == [doc_id]
    assert _bound(conn, doc_id) is None


def test_the_document_row_is_otherwise_untouched(conn):
    """Status, links and evidence are not this tool's business: the document's
    disposition is unchanged, it merely becomes findable."""
    _seed_entity(conn, "BF AG")
    _seed_alias(conn, "BF Ltd", "BF AG")
    doc_id = _seed_doc(conn, "bf-intact", "BF Ltd")

    r.apply(conn, r.plan(conn)["resolved"])

    row = conn.execute(
        "SELECT status, coverage_scope, archive_url FROM document WHERE doc_id=%s",
        (doc_id,)).fetchone()
    assert row["status"] == "filed"
    assert row["coverage_scope"] == "manufacturer"
    assert row["archive_url"] == "file:///a.pdf"
