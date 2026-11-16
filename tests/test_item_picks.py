"""The picker's tick-set check, shared by the route and GATE (spec §5.1, §5.3)."""
from __future__ import annotations

from app import item_picks


def _item(conn, ref, raw):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,NULL,'LJ',1,now())", (ref, raw))


def _doc(conn):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('IFU','MDR','group','picks-1','file:///a.pdf','staged') RETURNING doc_id"
    ).fetchone()["doc_id"]


def test_a_clean_tick_set_has_no_problems(conn):
    _item(conn, "A1", "ACME")
    assert item_picks.check_items(conn, _doc(conn), "ACME", ["A1"]) == {}


def test_each_kind_of_problem_is_named(conn):
    doc_id = _doc(conn)
    _item(conn, "A1", "ACME")
    _item(conn, "B1", "OTHER")
    _item(conn, "R1", "ACME")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    got = item_picks.check_items(conn, doc_id, "ACME", ["A1", "B1", "R1", "GONE", "A1"])
    assert got == {"unknown": ["GONE"], "other-manufacturer": ["B1"], "refused": ["R1"]}


def test_a_manufacturer_with_no_codes_owns_no_item(conn):
    _item(conn, "A1", "ACME")
    got = item_picks.check_items(conn, _doc(conn), "NOBODY", ["A1"])
    assert got == {"other-manufacturer": ["A1"]}


def test_check_items_accepts_listing_brand_items(conn, monkeypatch):
    """Woodpecker's declaration 458 covers Komet's ENFILL: Komet lists WOODPECKER
    as a subcontractor (spec 2026-11-13 § 4.6)."""
    from app import subcontractors
    from app.playbooks import Playbook
    monkeypatch.setattr(subcontractors, "_loaded", lambda playbooks: (
        Playbook(slug="komet", manufacturer="KOMET", subcontractors=("WOODPECKER",)),))
    for raw, canonical in (("077", "KOMET"), ("430", "WOODPECKER")):
        conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s)",
                     (raw, canonical))
    _item(conn, "000 ENFILL", "077")
    assert item_picks.check_items(conn, _doc(conn), "WOODPECKER", ["000 ENFILL"]) == {}
