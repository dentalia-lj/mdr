"""gate.apply `approve` with `items`, and `add-items` (picker spec §5).

A ticked item becomes a confirmed link exactly as `confirm-link` records one:
`manual`, production, `link-confirmed` + `production-write` under the
reviewer's name. Failures raise before any write. Tests don't commit.
"""
from __future__ import annotations

import pytest

from app import item_picks
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


def test_add_items_publishes_a_filed_document(conn):
    """2026-11-12: a filed document (read correctly, covering nothing BC had)
    takes items from a person, and that publishes it, audited like an approval."""
    doc_id = _doc(conn, "filed", status="filed")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert _doc_status(conn, doc_id) == "production"
    assert dict(_link(conn, doc_id, "A1")) == {"status": "production", "match_basis": "manual", "udi": None}
    assert _trail(conn, doc_id) == [
        ("link-confirmed", "A1"), ("production-write", "A1"),
        ("add-items", None), ("production-write", None)]
    assert _detail(conn, doc_id, "add-items") == {"items": 1, "manufacturer": MFR, "from": "filed"}


def test_add_items_on_a_filed_document_is_quiet_when_redelivered(conn):
    doc_id = _doc(conn, "filed-again", status="filed")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert [e for e in _trail(conn, doc_id) if e[0] == "production-write" and e[1] is None] == [
        ("production-write", None)]


def test_add_items_refuses_a_filed_document_with_no_issue_date(conn):
    # Every publication needs an issue date (ruled 2026-10-02).
    doc_id = _doc(conn, "filed-nodate", status="filed")
    conn.execute("UPDATE document SET validity_from=NULL WHERE doc_id=%s", (doc_id,))
    _item(conn, "A1")
    with pytest.raises(ValueError, match="no issue date"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert _doc_status(conn, doc_id) == "filed"
    assert _link(conn, doc_id, "A1") is None


def test_add_items_without_items_raises(conn):
    doc_id = _doc(conn, "empty", status="production")
    with pytest.raises(ValueError, match="items is required"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items"))


def test_add_items_leaves_open_review_tasks_alone(conn):
    """Final review: add-items is the link steps without the promote (spec
    §5.2); it must not resolve an open task on the published document under
    the reviewer's name."""
    doc_id = _doc(conn, "task", status="production")
    _item(conn, "A1")
    conn.execute("INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')",
                 (doc_id,))
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert conn.execute("SELECT status FROM manual_task WHERE doc_id=%s",
                        (doc_id,)).fetchone()["status"] == "open"


def _existing(conn, doc_id, ref, status="staged", basis="ref-list"):
    _item(conn, ref)
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s, %s, %s, %s::link_status)", (ref, doc_id, basis, status))


# --- reversing a refusal: `reopen` beside `items` (spec 2026-11-09 §4.4) -----

def test_a_refused_item_in_items_alone_still_raises(conn):
    doc_id = _doc(conn, "alone")
    _existing(conn, doc_id, "A1", "rejected")
    with pytest.raises(ValueError, match="refused: A1"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))
    assert _link(conn, doc_id, "A1")["status"] == "rejected"
    assert _doc_status(conn, doc_id) == "staged"


def test_check_items_lets_a_listed_refusal_through(conn):
    doc_id = _doc(conn, "chk")
    _existing(conn, doc_id, "A1", "rejected")
    _item(conn, "A2")
    conn.execute("UPDATE item_mirror SET manufacturer_raw=%s WHERE item_ref='A1'", (MFR,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'vendor-master') ON CONFLICT (raw_name) DO NOTHING", (MFR, MFR))
    assert item_picks.check_items(conn, doc_id, MFR, ["A1"]) == {"refused": ["A1"]}
    assert item_picks.check_items(conn, doc_id, MFR, ["A1"], reopen=["A1"]) == {}


def test_reopen_reverses_a_refusal_on_approve(conn):
    doc_id = _doc(conn, "rev")
    _existing(conn, doc_id, "A1", "rejected")

    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], reopen=["A1"]))

    assert dict(_link(conn, doc_id, "A1")) == {"status": "production", "match_basis": "manual", "udi": None}
    assert [e for e in _trail(conn, doc_id) if e[1] == "A1"] == [
        ("link-reopened", "A1"), ("link-confirmed", "A1"), ("production-write", "A1")]
    detail = _detail(conn, doc_id, "link-reopened")
    assert detail["via"] == "approve"
    assert detail["link_status_before"] == "rejected"
    assert detail["match_basis_before"] == "ref-list"


def test_reopen_works_on_add_items(conn):
    doc_id = _doc(conn, "revadd", status="production")
    _existing(conn, doc_id, "A1", "rejected")

    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"], reopen=["A1"]))

    assert _link(conn, doc_id, "A1")["status"] == "production"
    assert _detail(conn, doc_id, "link-reopened")["via"] == "add-items"


@pytest.mark.parametrize("extra", [
    {"items": ["A1"], "reopen": ["A2"]},
    {"reopen": ["A1"]},
    {"items": ["A1"], "reopen": "A1"},
])
def test_reopen_must_name_ticked_items(conn, extra):
    doc_id = _doc(conn, "subset")
    _existing(conn, doc_id, "A1", "rejected")
    with pytest.raises(ValueError, match="reopen"):
        gh.handle_gate_apply(conn, _job(doc_id, **extra))
    assert _link(conn, doc_id, "A1")["status"] == "rejected"
    assert _doc_status(conn, doc_id) == "staged"


def test_a_reopened_trusted_link_is_confirmed_not_promoted(conn):
    """Spec §4.5: reopened before the promote it would publish unconfirmed."""
    doc_id = _doc(conn, "order")
    _existing(conn, doc_id, "A1", "rejected", "ref-item")

    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], reopen=["A1"]))

    assert _link(conn, doc_id, "A1")["match_basis"] == "manual"
    assert _trail(conn, doc_id).count(("link-confirmed", "A1")) == 1


def test_a_redelivered_reopen_writes_nothing_more(conn):
    doc_id = _doc(conn, "again")
    _existing(conn, doc_id, "A1", "rejected")
    job = _job(doc_id, items=["A1"], reopen=["A1"])

    gh.handle_gate_apply(conn, job)
    first = _trail(conn, doc_id).count(("link-reopened", "A1"))
    gh.handle_gate_apply(conn, job)

    assert first == 1
    assert _trail(conn, doc_id).count(("link-reopened", "A1")) == 1


# --- taking an item off on Review: `remove` on approve (spec 2026-11-09 §4.2) --

def test_approve_remove_refuses_the_link_and_publishes_the_rest(conn):
    doc_id = _doc(conn, "rm")
    _existing(conn, doc_id, "A1", "staged", "ref-item")
    _existing(conn, doc_id, "A2", "staged", "ref-list")

    gh.handle_gate_apply(conn, _job(doc_id, remove=["A1"]))

    assert _doc_status(conn, doc_id) == "production"
    assert _link(conn, doc_id, "A1")["status"] == "rejected"
    assert _link(conn, doc_id, "A1")["match_basis"] == "ref-item"
    assert _link(conn, doc_id, "A2")["status"] == "production"
    detail = _detail(conn, doc_id, "link-rejected")
    assert detail == {"match_basis_before": "ref-item", "link_status_before": "staged",
                      "via": "approve"}
    assert _detail(conn, doc_id, "approve")["removed"] == 1


@pytest.mark.parametrize("start,end,audited,skipped", [
    ("staged", "rejected", 1, 0),
    ("production", "rejected", 1, 0),
    ("rejected", "rejected", 0, 0),
    ("retracted", "retracted", 0, 1),
])
def test_what_remove_does_to_each_link_status(conn, start, end, audited, skipped):
    doc_id = _doc(conn, f"st-{start}")
    _existing(conn, doc_id, "A1", start)

    out = gh.handle_gate_apply(conn, _job(doc_id, remove=["A1"]))

    assert _link(conn, doc_id, "A1")["status"] == end
    assert _trail(conn, doc_id).count(("link-rejected", "A1")) == audited
    assert out.get("remove_skipped", 0) == skipped


def test_removing_an_item_with_no_link_raises(conn):
    doc_id = _doc(conn, "norow")
    with pytest.raises(LookupError, match="A9"):
        gh.handle_gate_apply(conn, _job(doc_id, remove=["A9"]))
    assert _doc_status(conn, doc_id) == "staged"


def test_the_same_item_in_remove_and_items_raises(conn):
    doc_id = _doc(conn, "both")
    _existing(conn, doc_id, "A1")
    with pytest.raises(ValueError, match="both"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], remove=["A1"]))
    assert _link(conn, doc_id, "A1")["status"] == "staged"
    assert _doc_status(conn, doc_id) == "staged"


def test_remove_on_a_whole_range_document_raises(conn):
    doc_id = _doc(conn, "whole", scope="manufacturer")
    _existing(conn, doc_id, "A1")
    with pytest.raises(ValueError, match="whole range"):
        gh.handle_gate_apply(conn, _job(doc_id, remove=["A1"]))
    assert _doc_status(conn, doc_id) == "staged"


def test_a_redelivered_approve_with_remove_writes_no_second_audit_row(conn):
    doc_id = _doc(conn, "redeliver")
    _existing(conn, doc_id, "A1")
    job = _job(doc_id, remove=["A1"])

    gh.handle_gate_apply(conn, job)
    gh.handle_gate_apply(conn, job)

    assert _trail(conn, doc_id).count(("link-rejected", "A1")) == 1


def test_a_re_proposal_after_removal_stays_rejected(conn):
    doc_id = _doc(conn, "sticky")
    _existing(conn, doc_id, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, remove=["A1"]))

    gh._upsert_link(conn, "A1", doc_id, None, "ref-list", "staged")

    assert _link(conn, doc_id, "A1")["status"] == "rejected"


# --- taking an item off a published document: `remove-link` (§4.3) ------------

def _remove_link(doc_id, ref="A1"):
    return _job(doc_id, decision="remove-link", item_ref=ref)


def test_remove_link_refuses_a_published_link(conn):
    doc_id = _doc(conn, "pub", status="production")
    _existing(conn, doc_id, "A1", "production", "ref-item")

    gh.handle_gate_apply(conn, _remove_link(doc_id))

    assert dict(_link(conn, doc_id, "A1")) == {"status": "rejected", "match_basis": "ref-item", "udi": None}
    detail = _detail(conn, doc_id, "link-rejected")
    assert detail["link_status_before"] == "production"
    assert detail["match_basis_before"] == "ref-item"


def test_remove_link_needs_a_published_link(conn):
    doc_id = _doc(conn, "pubstaged", status="production")
    _existing(conn, doc_id, "A1", "staged")
    with pytest.raises(ValueError, match="expected 'production'"):
        gh.handle_gate_apply(conn, _remove_link(doc_id))
    assert _link(conn, doc_id, "A1")["status"] == "staged"


def test_remove_link_needs_a_published_document(conn):
    doc_id = _doc(conn, "docstaged")
    _existing(conn, doc_id, "A1", "production")
    with pytest.raises(ValueError, match="not 'production'"):
        gh.handle_gate_apply(conn, _remove_link(doc_id))


def test_remove_link_refuses_a_whole_range_document(conn):
    doc_id = _doc(conn, "pubwhole", status="production", scope="manufacturer")
    _existing(conn, doc_id, "A1", "production", "mfr-scope")
    with pytest.raises(ValueError, match="whole range"):
        gh.handle_gate_apply(conn, _remove_link(doc_id))
    assert _link(conn, doc_id, "A1")["status"] == "production"


def test_a_redelivered_remove_link_is_quiet(conn):
    doc_id = _doc(conn, "pubagain", status="production")
    _existing(conn, doc_id, "A1", "production")

    gh.handle_gate_apply(conn, _remove_link(doc_id))
    out = gh.handle_gate_apply(conn, _remove_link(doc_id))

    assert out["idempotent"] is True
    assert _trail(conn, doc_id).count(("link-rejected", "A1")) == 1


def test_a_removed_link_comes_back_through_reopen_and_confirm(conn):
    doc_id = _doc(conn, "pubback", status="production")
    _existing(conn, doc_id, "A1", "production")
    gh.handle_gate_apply(conn, _remove_link(doc_id))

    gh.handle_gate_apply(conn, _job(doc_id, decision="reopen-link", item_ref="A1"))
    assert _link(conn, doc_id, "A1")["status"] == "staged"
    gh.handle_gate_apply(conn, _job(doc_id, decision="confirm-link", item_ref="A1"))

    assert dict(_link(conn, doc_id, "A1")) == {"status": "production", "match_basis": "manual", "udi": None}


# --- a replayed job must not override a later person (review finding 2) --------

def test_a_replayed_approve_does_not_undo_a_later_re_tick(conn):
    doc_id = _doc(conn, "replay-a")
    _existing(conn, doc_id, "A1")
    approve = _job(doc_id, remove=["A1"])
    gh.handle_gate_apply(conn, approve)
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", jid=32, items=["A1"], reopen=["A1"]))

    gh.handle_gate_apply(conn, approve)

    assert _link(conn, doc_id, "A1")["status"] == "production"
    assert _trail(conn, doc_id).count(("link-rejected", "A1")) == 1


def test_a_replayed_approve_does_not_reverse_a_later_removal(conn):
    doc_id = _doc(conn, "replay-b")
    _existing(conn, doc_id, "A1", "rejected")
    approve = _job(doc_id, items=["A1"], reopen=["A1"])
    gh.handle_gate_apply(conn, approve)
    gh.handle_gate_apply(conn, _job(doc_id, decision="remove-link", jid=33, item_ref="A1"))

    gh.handle_gate_apply(conn, approve)

    assert _link(conn, doc_id, "A1")["status"] == "rejected"
    assert _trail(conn, doc_id).count(("link-reopened", "A1")) == 1
    assert _trail(conn, doc_id).count(("link-confirmed", "A1")) == 1


def test_a_replayed_add_items_does_not_reverse_a_later_removal(conn):
    doc_id = _doc(conn, "replay-c", status="production")
    _existing(conn, doc_id, "A1", "rejected")
    add = _job(doc_id, decision="add-items", items=["A1"], reopen=["A1"])
    gh.handle_gate_apply(conn, add)
    gh.handle_gate_apply(conn, _job(doc_id, decision="remove-link", jid=33, item_ref="A1"))

    gh.handle_gate_apply(conn, add)

    assert _link(conn, doc_id, "A1")["status"] == "rejected"
    assert _trail(conn, doc_id).count(("link-confirmed", "A1")) == 1


def _undated(conn, key, scope="group"):
    doc_id = _doc(conn, key, scope=scope)
    conn.execute("UPDATE document SET validity_from = NULL WHERE doc_id=%s", (doc_id,))
    return doc_id


def test_approve_refuses_a_document_without_an_issue_date(conn):
    doc_id = _undated(conn, "nodate")
    with pytest.raises(ValueError, match="no issue date"):
        gh.handle_gate_apply(conn, _job(doc_id))


def test_an_entered_date_lets_approve_through(conn):
    doc_id = _undated(conn, "dated")
    gh.handle_gate_apply(conn, _job(doc_id, edits={"validity_from": "2026-04-01"}))
    assert _doc_status(conn, doc_id) == "production"


def test_bind_takes_the_date_and_only_the_date(conn):
    doc_id = _undated(conn, "bind", scope="manufacturer")
    _item(conn, "A1")   # a name with BC codes; GATE refuses a bind that reaches none
    with pytest.raises(ValueError, match="only the issue date"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR,
                                        edits={"validity_from": "2026-04-01", "type": "EC"}))
    with pytest.raises(ValueError, match="no issue date"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR))
    gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR,
                                    edits={"validity_from": "2026-04-01"}))
    assert _doc_status(conn, doc_id) == "production"


def test_remove_link_works_on_a_superseded_document_that_still_answers(conn):
    """Per-item currency: the old flag decides nothing, so a wrong link on such
    a document must be removable like any other."""
    doc_id = _doc(conn, "pubsup", status="superseded")
    _existing(conn, doc_id, "A1", "production")

    gh.handle_gate_apply(conn, _remove_link(doc_id))

    assert _link(conn, doc_id, "A1")["status"] == "rejected"
