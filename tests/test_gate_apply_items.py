"""gate.apply `approve` with `items`, and `add-items` (picker spec §5).

A ticked item becomes a confirmed link exactly as `confirm-link` records one:
`manual`, production, `link-confirmed` + `production-write` under the
reviewer's name. Failures raise before any write. Tests don't commit.
"""
from __future__ import annotations

import pytest

from app.handlers import gate as gh

MFR = "ACME"


def _mfr(conn, name=MFR):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _item(conn, ref, raw=MFR):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,NULL,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (ref, raw))


def _doc(conn, key, *, status="staged", canonical=MFR, scope="group"):
    if canonical:
        _mfr(conn, canonical)
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, validity_from, canonical_manufacturer) "
        "VALUES ('IFU','MDR',%s,%s,'file:///a.pdf',%s,'2026-04-01',%s) RETURNING doc_id",
        (scope, f"pick-{key}", status, canonical)).fetchone()["doc_id"]


def _job(doc_id, decision="approve", jid=31, **extra):
    payload = {"doc_id": doc_id, "decision": decision, "decided_by": "user:natasa", **extra}
    return {"id": jid, "type": "gate.apply", "payload": payload,
            "dedupe_key": f"apply:{doc_id}:{decision}"}


def _link(conn, doc_id, ref):
    return conn.execute(
        "SELECT status, match_basis, udi FROM item_document WHERE doc_id=%s AND item_ref=%s",
        (doc_id, ref)).fetchone()


def _trail(conn, doc_id):
    return [(r["event"], r["item_ref"]) for r in conn.execute(
        "SELECT event, item_ref FROM audit_log WHERE doc_id=%s ORDER BY id", (doc_id,))]


def _detail(conn, doc_id, event):
    return conn.execute("SELECT detail FROM audit_log WHERE doc_id=%s AND event=%s",
                        (doc_id, event)).fetchone()["detail"]


def _doc_status(conn, doc_id):
    return conn.execute("SELECT status FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["status"]


def test_approve_links_ticked_items_as_confirmed_links(conn):
    doc_id = _doc(conn, "ok")
    _item(conn, "A1"); _item(conn, "A2")

    gh.handle_gate_apply(conn, _job(doc_id, items=["A2", "A1"]))

    assert _doc_status(conn, doc_id) == "production"
    for ref in ("A1", "A2"):
        assert dict(_link(conn, doc_id, ref)) == {"status": "production", "match_basis": "manual", "udi": None}
    assert _trail(conn, doc_id) == [
        ("link-confirmed", "A1"), ("production-write", "A1"),
        ("link-confirmed", "A2"), ("production-write", "A2"),
        ("approve", None), ("production-write", None)]
    assert _detail(conn, doc_id, "approve") == {"items": 2, "manufacturer": MFR}
    assert _detail(conn, doc_id, "link-confirmed")["via"] == "approve"


def test_approve_without_items_is_unchanged(conn):
    doc_id = _doc(conn, "plain")
    gh.handle_gate_apply(conn, _job(doc_id))
    assert _doc_status(conn, doc_id) == "production"
    assert _trail(conn, doc_id) == [("approve", None), ("production-write", None)]
    assert _detail(conn, doc_id, "approve") is None


def test_duplicate_ticks_link_once(conn):
    doc_id = _doc(conn, "dup")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1", "A1"]))
    assert [e for e in _trail(conn, doc_id) if e[1] == "A1"] == [
        ("link-confirmed", "A1"), ("production-write", "A1")]
    assert _detail(conn, doc_id, "approve")["items"] == 1


@pytest.mark.parametrize("bad, raw, kind", [
    ("B1", "OTHER", "other-manufacturer"),   # another manufacturer's item
    ("GONE", None, "unknown"),               # left the catalogue (Review Focus 5)
])
def test_a_bad_item_raises_and_writes_nothing(conn, bad, raw, kind):
    doc_id = _doc(conn, f"bad-{bad}")
    _item(conn, "A1")
    if raw:
        _item(conn, bad, raw)
    with pytest.raises(ValueError, match=rf"{kind}: {bad}"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1", bad]))
    assert _doc_status(conn, doc_id) == "staged"       # raised before the promote
    assert _link(conn, doc_id, "A1") is None
    assert _trail(conn, doc_id) == []


def test_a_link_refused_meanwhile_raises(conn):
    """Review Focus 3: a reject-link landed between render and Approve."""
    doc_id = _doc(conn, "refused")
    _item(conn, "R1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    with pytest.raises(ValueError, match="refused: R1"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["R1"]))
    assert _doc_status(conn, doc_id) == "staged"
    assert _link(conn, doc_id, "R1")["status"] == "rejected"


def test_no_manufacturer_anywhere_raises(conn):
    doc_id = _doc(conn, "nomfr", canonical=None)
    _item(conn, "A1")
    with pytest.raises(ValueError, match="no manufacturer"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))


def test_a_manufacturer_we_do_not_hold_counts_as_none(conn):
    doc_id = _doc(conn, "unheld", canonical=None)
    _item(conn, "A1")
    with pytest.raises(ValueError, match="no manufacturer"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer="NOT A ROW"))


def test_the_payload_manufacturer_is_written_when_the_document_has_none(conn):
    doc_id = _doc(conn, "setmfr", canonical=None)
    _mfr(conn)
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer=MFR))
    assert conn.execute("SELECT canonical_manufacturer FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["canonical_manufacturer"] == MFR


def test_a_conflicting_manufacturer_raises(conn):
    doc_id = _doc(conn, "conflict")
    _mfr(conn, "GLOBEX")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="GLOBEX"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer="GLOBEX"))


def test_a_whole_range_document_takes_no_items(conn):
    doc_id = _doc(conn, "range", scope="manufacturer")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="whole range"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))


def test_a_staged_weak_link_becomes_manual_and_keeps_its_udi(conn):
    doc_id = _doc(conn, "weak")
    _item(conn, "N1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, udi, match_basis, status) "
                 "VALUES ('N1', %s, '0761234', 'name-family', 'staged')", (doc_id,))
    gh.handle_gate_apply(conn, _job(doc_id, items=["N1"]))
    assert dict(_link(conn, doc_id, "N1")) == {
        "status": "production", "match_basis": "manual", "udi": "0761234"}
    d = _detail(conn, doc_id, "link-confirmed")
    assert d["match_basis_before"] == "name-family" and d["link_status_before"] == "staged"


def test_a_trusted_link_is_left_alone(conn):
    """A staged ref-list link follows the document (`_promote_pending_links`);
    ticking it as well rewrites nothing and audits nothing extra."""
    doc_id = _doc(conn, "trusted")
    _item(conn, "T1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('T1', %s, 'ref-list', 'staged')", (doc_id,))
    gh.handle_gate_apply(conn, _job(doc_id, items=["T1"]))
    assert dict(_link(conn, doc_id, "T1")) == {
        "status": "production", "match_basis": "ref-list", "udi": None}
    assert ("link-confirmed", "T1") not in _trail(conn, doc_id)


def test_a_redelivered_approve_writes_no_second_pair(conn):
    doc_id = _doc(conn, "again")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))
    assert [e for e in _trail(conn, doc_id) if e[1] == "A1"] == [
        ("link-confirmed", "A1"), ("production-write", "A1")]


def test_reject_ignores_items(conn):
    doc_id = _doc(conn, "rej")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="reject", items=["A1"]))
    assert _link(conn, doc_id, "A1") is None


def test_add_items_links_on_a_published_document(conn):
    doc_id = _doc(conn, "pub", status="production")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert _link(conn, doc_id, "A1")["match_basis"] == "manual"
    assert _trail(conn, doc_id) == [
        ("link-confirmed", "A1"), ("production-write", "A1"), ("add-items", None)]
    assert _detail(conn, doc_id, "add-items") == {"items": 1, "manufacturer": MFR}
    assert _detail(conn, doc_id, "link-confirmed")["via"] == "add-items"


def test_add_items_refuses_a_document_that_is_not_published(conn):
    doc_id = _doc(conn, "notpub")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="not 'production'"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))


def test_add_items_without_items_raises(conn):
    doc_id = _doc(conn, "empty", status="production")
    with pytest.raises(ValueError, match="items is required"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items"))
