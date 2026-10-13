"""GATE on a re-read: the three guards Task 5a's review asked for (Denis,
2026-10-12, R4).

1. A production re-read must not change the identity (type, regulation,
   coverage scope) of a document in a supersession chain: invariant 5. Such a
   re-read goes to `manual`, says why on the job result, and is kept.
2. A reviewer's edit outranks a machine re-read. A field with T3 evidence keeps
   its value and its evidence; the machine's differing value is counted. Root
   cause of the defect this closes: a T3 edit takes the next evidence rev
   (`MAX(evidence.extract_rev) + 1`), while a reading's rev comes from
   `extraction_attempt` (`tiers.next_extract_rev`), so the next re-read shares
   the edit's rev, the per-(doc, field, rev) evidence guard skipped the
   machine's row, and the column took the machine's value: the column and its
   current evidence disagreed (invariant 2).
3. The manufacturer-binding route skips a stale revision, as the REF route does.
4. The document row is locked when GATE reads its status, so a decision that
   commits in between cannot be overwritten on a status read before it.
"""

from __future__ import annotations

import psycopg
import pytest

from app.extract.tiers import write_extraction_attempt
from app.handlers import gate as gh


def _ev(value, conf=0.97):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": 1, "model_id": "m"}


def _fields(conf=0.97, **overrides):
    base = {"type": _ev("DoC", conf), "regulation": _ev("MDR", conf),
            "coverage_scope": _ev("group", conf), "cert_number": _ev("CERT-1", conf),
            "validity_from": _ev("2024-01-01", conf), "validity_to": _ev("2029-01-01", conf)}
    base.update(overrides)
    return base


def _seed(conn, ch, fields, rev=1):
    write_extraction_attempt(conn, ch, ["T0", "T1"], fields, extract_rev=rev)


def _job(ch, *, rev=1, group_id=7, jid=1, **payload):
    p = {"content_hash": ch, "extract_rev": rev, "group_id": group_id,
         "archive_url": "file:///a.pdf"}
    p.update(payload)
    return {"id": jid, "type": "gate.candidate", "payload": p,
            "dedupe_key": f"gate:{ch}:{rev}:{group_id}"}


def _seed_item(conn, item_ref, manufacturer="ACME", md=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,%s,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (item_ref, manufacturer, md))


def _published(conn, ch, **over):
    _seed_item(conn, f"{ch}-ref")
    _seed(conn, ch, _fields(**over))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"}]))
    assert r["disposition"] == "production"
    return r["doc_id"]


def _production_reread(conn, ch, rev, **over):
    _seed(conn, ch, _fields(**over), rev=rev)
    return gh.handle_gate_candidate(conn, _job(ch, rev=rev, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"}], jid=rev + 10))


def _doc(conn, doc_id):
    return dict(conn.execute(
        "SELECT status, type, regulation, coverage_scope, cert_number, validity_to, "
        "supersedes, superseded_by FROM document WHERE doc_id=%s", (doc_id,)).fetchone())


def _current(conn, doc_id, field):
    return conn.execute(
        "SELECT value, tier, extract_rev FROM evidence WHERE doc_id=%s AND field=%s "
        "ORDER BY extract_rev DESC, id DESC LIMIT 1", (doc_id, field)).fetchone()


def _open_tasks(conn, doc_id):
    return conn.execute("SELECT count(*) c FROM manual_task WHERE doc_id=%s AND status='open'",
                        (doc_id,)).fetchone()["c"]


def _column_matches_current_evidence(conn, doc_id):
    """Invariant 2 for the columns a person can edit: each non-null column is
    the value of that field's current evidence row."""
    doc = conn.execute("SELECT * FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
    wrong = {}
    for f in ("type", "regulation", "coverage_scope", "cert_number", "validity_from",
              "validity_to", "basic_udi_di"):
        if doc[f] is None:
            continue
        cur = _current(conn, doc_id, f)
        if cur is None or cur["value"] != str(doc[f]):
            wrong[f] = (str(doc[f]), cur and cur["value"])
    return wrong


# --------------------------------------------------------------------------- #
# 1. supersession chain identity (invariant 5)
# --------------------------------------------------------------------------- #
def _chain(conn, ch):
    """`ch`'s document supersedes an older one: the C6 pointers in both
    directions, as `_apply_supersession` writes them."""
    doc_id = _published(conn, ch)
    older = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope, superseded_by) VALUES ('DoC','MDR','2020-01-01',"
        "'superseded',%s,'/archive/o.pdf','group',%s) RETURNING doc_id",
        (f"{ch}-old", doc_id)).fetchone()["doc_id"]
    conn.execute("UPDATE document SET supersedes=%s WHERE doc_id=%s", (older, doc_id))
    return doc_id, older


@pytest.mark.parametrize("change,field", [
    ({"regulation": _ev("MDD")}, "regulation"),
    ({"type": _ev("EC")}, "type"),
    ({"coverage_scope": _ev("manufacturer")}, "coverage_scope"),
])
def test_a_production_re_read_cannot_re_identify_a_chained_document(conn, change, field):
    ch = f"chain-{field}"
    doc_id, older = _chain(conn, ch)
    before = _doc(conn, doc_id)

    r = _production_reread(conn, ch, 2, cert_number=_ev("CERT-2"), **change)

    assert r["disposition"] == "manual"
    assert r["chain_identity_changed"] == [field]
    assert r["kept"] == "production"
    assert _doc(conn, doc_id) == before                 # nothing of the reading adopted
    assert _doc(conn, older)["superseded_by"] == doc_id
    assert _open_tasks(conn, doc_id) == 0


def test_a_chained_document_still_takes_a_re_read_of_the_same_identity(conn):
    ch = "chain-same"
    doc_id, _ = _chain(conn, ch)
    r = _production_reread(conn, ch, 2, cert_number=_ev("CERT-2"))
    assert r["disposition"] == "production"
    assert "chain_identity_changed" not in r
    assert _doc(conn, doc_id)["cert_number"] == "CERT-2"


def test_the_auto_supersede_pointer_alone_makes_a_chain(conn):
    """The task-5 fast path writes only the OLDER document's `superseded_by`;
    the newer one carries no `supersedes`. It is in a chain all the same."""
    ch = "chain-auto"
    doc_id = _published(conn, ch)
    conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope, superseded_by) VALUES ('DoC','MDR','2019-01-01',"
        "'superseded','chain-auto-old','/archive/o.pdf','group',%s)", (doc_id,))
    r = _production_reread(conn, ch, 2, regulation=_ev("MDD"))
    assert r["chain_identity_changed"] == ["regulation"]
    assert _doc(conn, doc_id)["regulation"] == "MDR"


def test_a_superseded_document_is_not_re_identified_either(conn):
    """The older end of the chain: its `superseded_by` is the pointer. Since
    R6 (Denis, 2026-10-13) no reading of a superseded document is adopted,
    production or not, so the identity cannot move."""
    _, older = _chain(conn, "chain-older")
    _seed(conn, "chain-older-old", _fields(type=_ev("EC")))
    r = gh.handle_gate_candidate(conn, _job("chain-older-old", ref_gate=True, flags=[],
                                            links=[], jid=50))
    assert r["kept"] == "superseded"
    doc = _doc(conn, older)
    assert (doc["status"], doc["type"]) == ("superseded", "DoC")


def test_a_rejected_document_in_a_chain_is_not_re_identified(conn):
    """A reject does not unwind a supersession; R5 makes a rejected document
    final for the machine, so the pointer and the identity both stay."""
    ch = "chain-rejected"
    doc_id, older = _chain(conn, ch)
    conn.execute("UPDATE document SET status='rejected' WHERE doc_id=%s", (doc_id,))
    r = _production_reread(conn, ch, 2, regulation=_ev("MDD"))
    assert r["kept"] == "rejected"
    doc = _doc(conn, doc_id)
    assert (doc["status"], doc["regulation"]) == ("rejected", "MDR")


def test_the_manufacturer_route_does_not_re_identify_a_chain_member(conn):
    """F4 (review round 2): the chain check on the binding route."""
    ch = "chain-mfr"
    _seed_ivoclar(conn, ch)
    _seed(conn, ch, _mfr_fields("EC-1"))
    x = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))["doc_id"]
    conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "coverage_scope, superseded_by) VALUES ('EC','MDR','superseded','chain-mfr-old',"
        "'/a.pdf','manufacturer',%s)", (x,))
    _seed(conn, ch, {**_mfr_fields("EC-1"), "regulation": _ev("MDD")}, rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))
    assert r["chain_identity_changed"] == ["regulation"]
    assert r["kept"] == "production"
    assert _doc(conn, x)["regulation"] == "MDR"


# --------------------------------------------------------------------------- #
# 2. a reviewer's edit outranks a machine re-read (invariant 2)
# --------------------------------------------------------------------------- #
def _approved_with_edit(conn, ch):
    _seed_item(conn, f"{ch}-ref")
    _seed(conn, ch, _fields(conf=0.85))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"}]))
    assert r["disposition"] == "staged"
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": r["doc_id"], "decision": "approve", "decided_by": "nat",
        "edits": {"cert_number": "CERT-FIXED"}}})
    assert _current(conn, r["doc_id"], "cert_number")["extract_rev"] == 2   # the borrowed rev
    return r["doc_id"]


def test_an_edit_survives_a_re_read_that_shares_its_rev(conn):
    """Probe (a) of the review: rev 2 is both the edit's and the re-read's."""
    ch = "t3-collide"
    doc_id = _approved_with_edit(conn, ch)

    r = _production_reread(conn, ch, 2, cert_number=_ev("CERT-NEW"),
                           validity_to=_ev("2031-01-01"))

    assert r["disposition"] == "production"
    doc = _doc(conn, doc_id)
    assert doc["cert_number"] == "CERT-FIXED"                  # the person's value
    assert str(doc["validity_to"]) == "2031-01-01"             # the rest is the new reading
    cur = _current(conn, doc_id, "cert_number")
    assert (cur["value"], cur["tier"]) == ("CERT-FIXED", "T3")
    assert r["reviewer_edits_kept"] == ["cert_number"]
    assert r["counts"]["reviewer_edits_kept"] == 1
    assert _column_matches_current_evidence(conn, doc_id) == {}


def test_an_edit_survives_a_later_re_read_too(conn):
    """Without a shared rev the machine's row would simply become current and
    outrank the person; the policy is that it does not."""
    ch = "t3-later"
    doc_id = _approved_with_edit(conn, ch)
    _production_reread(conn, ch, 2)
    r = _production_reread(conn, ch, 3, cert_number=_ev("CERT-LATER"))
    assert _doc(conn, doc_id)["cert_number"] == "CERT-FIXED"
    assert _current(conn, doc_id, "cert_number")["tier"] == "T3"
    assert r["reviewer_edits_kept"] == ["cert_number"]
    assert _column_matches_current_evidence(conn, doc_id) == {}


def test_the_manufacturer_route_holds_a_reviewer_s_edit(conn):
    """F4 (review round 2): the hold on the binding route."""
    ch = "t3-mfr"
    _seed_ivoclar(conn, ch)
    _seed(conn, ch, _mfr_fields("EC-1"))
    x = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))["doc_id"]
    gh._apply_edits(conn, x, {"cert_number": "EC-FIXED"})
    _seed(conn, ch, _mfr_fields("EC-NEW"), rev=3)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=3, route="mfr-binding"))
    assert r["disposition"] == "mfr-bound"
    assert _doc(conn, x)["cert_number"] == "EC-FIXED"
    assert _current(conn, x, "cert_number")["tier"] == "T3"
    assert r["reviewer_edits_kept"] == ["cert_number"]


def _ec_fields(cert, conf):
    return {"type": _ev("EC", conf), "regulation": _ev("MDR", conf),
            "coverage_scope": _ev("group", conf), "cert_number": _ev(cert, conf),
            "validity_from": _ev("2024-01-01", conf)}


def _citing_doc(conn, ch, cert_number):
    return conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "coverage_scope, cert_number) VALUES ('DoC','MDR','production',%s,'/archive/d.pdf',"
        "'group',%s) RETURNING doc_id", (ch, cert_number)).fetchone()["doc_id"]


def test_back_resolution_follows_the_reviewer_s_certificate_number(conn):
    """F3 (review round 2): a certificate whose number a person corrected is
    read again and still reads the wrong number. Back-resolution must answer
    the declarations that cite the CORRECTED number, which is what the column
    holds, and not the ones citing the number the person threw out."""
    ch = "t3-ec"
    _seed(conn, ch, _ec_fields("EC-WRONG", 0.85))
    cert = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))["doc_id"]
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": cert, "decision": "approve", "decided_by": "nat",
        "edits": {"cert_number": "EC-FIXED"}}})
    wrong = _citing_doc(conn, "t3-ec-wrong", "EC-WRONG")
    right = _citing_doc(conn, "t3-ec-right", "EC-FIXED")

    _seed(conn, ch, _ec_fields("EC-WRONG", 0.97), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[], jid=3))

    assert r["disposition"] == "production"
    cited = lambda d: conn.execute("SELECT cert_doc_id FROM document WHERE doc_id=%s",  # noqa: E731
                                   (d,)).fetchone()["cert_doc_id"]
    assert cited(wrong) is None
    assert cited(right) == cert


def test_back_resolution_follows_a_held_type(conn):
    """A person who re-typed a document as a certificate decides that it is
    one; a later reading calling it a DoC does not stop it answering citers."""
    ch = "t3-type"
    _seed(conn, ch, _ec_fields("EC-7", 0.85) | {"type": _ev("DoC", 0.85)})
    cert = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))["doc_id"]
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": cert, "decision": "approve", "decided_by": "nat", "edits": {"type": "EC"}}})
    citer = _citing_doc(conn, "t3-type-citer", "EC-7")
    _seed(conn, ch, _ec_fields("EC-7", 0.97) | {"type": _ev("DoC")}, rev=2)
    gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[], jid=3))
    assert conn.execute("SELECT cert_doc_id FROM document WHERE doc_id=%s",
                        (citer,)).fetchone()["cert_doc_id"] == cert


def test_a_re_read_that_agrees_with_the_edit_counts_nothing(conn):
    ch = "t3-agree"
    doc_id = _approved_with_edit(conn, ch)
    r = _production_reread(conn, ch, 2, cert_number=_ev("CERT-FIXED"))
    assert "reviewer_edits_kept" not in r
    assert _column_matches_current_evidence(conn, doc_id) == {}


# --------------------------------------------------------------------------- #
# 3. the manufacturer route skips a stale revision
# --------------------------------------------------------------------------- #
def _mfr_fields(cert):
    return {"type": _ev("EC"), "regulation": _ev("MDR"),
            "coverage_scope": _ev("manufacturer"),
            "manufacturer": _ev("Ivoclar Vivadent AG"), "cert_number": _ev(cert)}


def _seed_ivoclar(conn, ch):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES "
                 "('001','IVOCLAR'), ('Ivoclar Vivadent AG','IVOCLAR') "
                 "ON CONFLICT (raw_name) DO NOTHING")
    _seed_item(conn, f"{ch}-1", "001", md=True)


def test_a_stale_revision_on_the_manufacturer_route_writes_nothing(conn):
    ch = "mfr-stale"
    _seed_ivoclar(conn, ch)
    _seed(conn, ch, _mfr_fields("EC-1"))
    _seed(conn, ch, _mfr_fields("EC-2"), rev=2)
    r2 = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))
    r1 = gh.handle_gate_candidate(conn, _job(ch, rev=1, route="mfr-binding", jid=9))
    assert r1 == {"skipped": "superseded-rev", "content_hash": ch, "extract_rev": 1}
    assert _doc(conn, r2["doc_id"])["cert_number"] == "EC-2"
    assert _current(conn, r2["doc_id"], "cert_number")["value"] == "EC-2"


# --------------------------------------------------------------------------- #
# 4. the status GATE decides on is locked when it is read
# --------------------------------------------------------------------------- #
def test_the_document_row_is_locked_from_the_status_read(conn, connect_test):
    """A kept candidate writes nothing, so before the lock it held no lock at
    all: a reviewer's approve could commit between GATE's status read and its
    decision. Now the row is locked from that read to GATE's commit."""
    ch = "lock-ref"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(conf=0.80), rev=2)
    conn.commit()
    other = connect_test(autocommit=True)
    try:
        r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))
        assert r["kept"] == "production"
        with pytest.raises(psycopg.errors.LockNotAvailable):
            other.execute("SELECT 1 FROM document WHERE doc_id=%s FOR UPDATE NOWAIT", (doc_id,))
    finally:
        conn.rollback()


def test_the_manufacturer_route_locks_the_row_too(conn, connect_test):
    ch = "lock-mfr"
    _seed_ivoclar(conn, ch)
    _seed(conn, ch, _mfr_fields("EC-1"))
    r1 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))
    low = {**_mfr_fields("EC-2"), "coverage_scope": _ev("manufacturer", 0.60)}
    _seed(conn, ch, low, rev=2)
    conn.commit()
    other = connect_test(autocommit=True)
    try:
        r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))
        assert r["kept"] == "production"
        with pytest.raises(psycopg.errors.LockNotAvailable):
            other.execute("SELECT 1 FROM document WHERE doc_id=%s FOR UPDATE NOWAIT",
                          (r1["doc_id"],))
    finally:
        conn.rollback()


# --------------------------------------------------------------------------- #
# 5. a rejected document is final for the machine (R5, Denis 2026-10-13)
# --------------------------------------------------------------------------- #
def _rejected(conn, ch, **over):
    """A document read once at `staged` and rejected by a person; the reject
    cascades its link to `rejected`."""
    _seed_item(conn, f"{ch}-old")
    _seed(conn, ch, _fields(conf=0.85, **over))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-old", "match_basis": "ref-list"}]))
    assert r["disposition"] == "staged"
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": r["doc_id"], "decision": "reject", "decided_by": "nat"}})
    return r["doc_id"]


def _everything(conn, doc_id):
    return {
        "doc": dict(conn.execute("SELECT * FROM document WHERE doc_id=%s", (doc_id,)).fetchone()),
        "evidence": conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                                 (doc_id,)).fetchone()["c"],
        "links": sorted((r["item_ref"], r["status"]) for r in conn.execute(
            "SELECT item_ref, status FROM item_document WHERE doc_id=%s", (doc_id,)).fetchall()),
        "audit": sorted(r["event"] for r in conn.execute(
            "SELECT event FROM audit_log WHERE doc_id=%s", (doc_id,)).fetchall()),
        "tasks": conn.execute("SELECT count(*) c FROM manual_task WHERE doc_id=%s",
                              (doc_id,)).fetchone()["c"],
    }


def test_a_production_reading_of_a_rejected_document_writes_nothing(conn):
    """Probe (a) of review round 2: the reading reaches production and carries
    `supersedes` = a live document. Before R5 it wrote a production link under
    the rejected document and retired the live one in favour of it."""
    ch = "rej-prod"
    y = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2022-01-01','production',"
        "'rej-prod-y','/archive/y.pdf','group') RETURNING doc_id").fetchone()["doc_id"]
    x = _rejected(conn, ch, validity_from=_ev("2025-01-01", 0.85))
    _seed_item(conn, f"{ch}-new")
    before = _everything(conn, x)
    _seed(conn, ch, _fields(validity_from=_ev("2025-01-01")), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], jid=3,
                                            supersedes=y, links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))

    assert r["disposition"] == "production" and r["kept"] == "rejected"
    assert r["links"] == 0   # nothing written, so nothing counted
    assert _everything(conn, x) == before
    row = _doc(conn, y)
    assert (row["status"], row["superseded_by"]) == ("production", None)


def test_a_staged_reading_of_a_rejected_document_writes_nothing(conn):
    """No field, no link (not even staged), no task."""
    ch = "rej-staged"
    x = _rejected(conn, ch)
    _seed_item(conn, f"{ch}-new")
    before = _everything(conn, x)
    _seed(conn, ch, _fields(conf=0.85, cert_number=_ev("CERT-2", 0.85)), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, jid=3,
                                            flags=["no-item-identifier"], links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))

    assert r["kept"] == "rejected"
    assert _everything(conn, x) == before


def test_a_rejected_chain_member_gets_no_staged_link_either(conn):
    ch = "rej-chain"
    doc_id, _ = _chain(conn, ch)
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": doc_id, "decision": "reject", "decided_by": "nat"}})
    _seed_item(conn, f"{ch}-new")
    before = _everything(conn, doc_id)
    _seed(conn, ch, _fields(regulation=_ev("MDD")), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], jid=3, links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))
    assert r["kept"] == "rejected"
    assert _everything(conn, doc_id) == before


def test_a_rejected_binding_is_final_on_the_manufacturer_route(conn):
    ch = "rej-mfr"
    _seed_ivoclar(conn, ch)
    _seed(conn, ch, _mfr_fields("EC-1"))
    x = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))["doc_id"]
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": x, "decision": "reject", "decided_by": "nat"}})
    before = _everything(conn, x)
    _seed(conn, ch, _mfr_fields("EC-2"), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))

    assert r["kept"] == "rejected"
    assert r["disposition"] == "mfr-bound" and r["links"] == 0
    assert _everything(conn, x) == before


def test_reopen_is_the_way_back(conn):
    """Once a person reopens it, the document takes readings again."""
    ch = "rej-reopen"
    x = _rejected(conn, ch)
    gh.handle_gate_apply(conn, {"id": 3, "type": "gate.apply", "payload": {
        "doc_id": x, "decision": "reopen", "decided_by": "nat"}})
    _seed(conn, ch, _fields(conf=0.85, cert_number=_ev("CERT-2", 0.85)), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[], jid=4))
    assert "kept" not in r
    assert _doc(conn, x)["cert_number"] == "CERT-2"
