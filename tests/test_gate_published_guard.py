"""GATE: a published or superseded document is replaced only by a reading that
is itself production (Denis, 2026-10-12; extraction-ladder plan, Task 5).

Before this, `_upsert_document` kept a production document's STATUS (sticky)
and overwrote every field with whatever the new read said, whatever that read's
own disposition: a re-read that came out `staged` or `manual` rewrote a
published document's type, regulation and dates, appended evidence for them,
and opened a review task that Review never lists (it lists staged documents
only).

Now a candidate that does not itself reach production, on a document that is
`production` (R1) or `superseded` (R2), keeps the document's fields, evidence,
status and chain pointer and opens no task. It still proposes its links, at
`staged` only: it never retracts one and never makes one production. That is
what makes a second group's candidate for the same reading (fetch.url
hash-dedupe, C3) land the same whichever arrives first. The job result says
`kept` and lists and counts the fields the reading saw differently.
"""

from __future__ import annotations

import pytest

from app.extract.tiers import write_extraction_attempt
from app.handlers import gate as gh


def _ev(value, conf=0.97):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": 1, "model_id": "m"}


def _fields(conf=0.97, **overrides):
    base = {"type": _ev("DoC", conf), "regulation": _ev("MDR", conf),
            "coverage_scope": _ev("group", conf), "cert_number": _ev("CERT-1", conf),
            "validity_from": _ev("2024-01-01", conf)}
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


def _doc(conn, doc_id):
    return conn.execute(
        "SELECT status, type, regulation, cert_number, validity_from, superseded_by "
        "FROM document WHERE doc_id=%s", (doc_id,)).fetchone()


def _evidence_revs(conn, doc_id):
    return sorted({r["extract_rev"] for r in conn.execute(
        "SELECT extract_rev FROM evidence WHERE doc_id=%s", (doc_id,)).fetchall()})


def _open_tasks(conn, doc_id):
    return conn.execute(
        "SELECT count(*) c FROM manual_task WHERE doc_id=%s AND status='open'",
        (doc_id,)).fetchone()["c"]


def _links(conn, doc_id):
    return {(r["item_ref"], r["status"]) for r in conn.execute(
        "SELECT item_ref, status FROM item_document WHERE doc_id=%s", (doc_id,)).fetchall()}


def _snapshot(conn, doc_id):
    """Everything a kept candidate must leave alone (links are asserted apart)."""
    return {
        "doc": dict(conn.execute("SELECT * FROM document WHERE doc_id=%s",
                                 (doc_id,)).fetchone()),
        "evidence": conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                                 (doc_id,)).fetchone()["c"],
        "audit": conn.execute("SELECT count(*) c FROM audit_log WHERE doc_id=%s",
                              (doc_id,)).fetchone()["c"],
        "tasks": conn.execute("SELECT count(*) c FROM manual_task WHERE doc_id=%s",
                              (doc_id,)).fetchone()["c"],
    }


def _published(conn, ch, **over):
    """A document published by the machine at rev 1, with one trusted
    production link and one staged name-family link."""
    _seed_item(conn, f"{ch}-ref")
    _seed_item(conn, f"{ch}-name")
    _seed(conn, ch, _fields(**over))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"},
        {"item_ref": f"{ch}-name", "match_basis": "name-family"}]))
    assert r["disposition"] == "production"
    return r["doc_id"]


# --------------------------------------------------------------------------- #
# a weaker re-read keeps the document, and still proposes its links
# --------------------------------------------------------------------------- #
def test_a_kept_re_read_stages_its_new_links_and_retracts_none(conn):
    ch = "pub-staged"
    doc_id = _published(conn, ch)
    _seed_item(conn, f"{ch}-new")
    before = _snapshot(conn, doc_id)
    _seed(conn, ch, _fields(conf=0.80, regulation=_ev("MDD", 0.80),
                            cert_number=_ev("CERT-2", 0.80)), rev=2)

    # group_id None: a written candidate would retract every staged link it
    # does not claim, so "nothing retracted" below is a real assertion.
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, group_id=None, ref_gate=True,
                                            flags=[], links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))

    assert r["disposition"] == "staged"            # what the re-read scored, reported
    assert r["kept"] == "production"
    assert r["links"] == 1                         # and it did write that one link
    assert r["doc_id"] == doc_id
    doc = _doc(conn, doc_id)
    assert (doc["status"], doc["regulation"], doc["cert_number"]) == ("production", "MDR", "CERT-1")
    assert _snapshot(conn, doc_id) == before        # no field, evidence, audit or task
    assert _links(conn, doc_id) == {(f"{ch}-ref", "production"), (f"{ch}-name", "staged"),
                                    (f"{ch}-new", "staged")}


def test_a_kept_re_read_never_makes_a_link_production(conn):
    """A trusted basis on a reading that is not itself production stages."""
    ch = "pub-trusted"
    doc_id = _published(conn, ch)
    _seed_item(conn, f"{ch}-new")
    _seed(conn, ch, _fields(conf=0.85), rev=2)
    gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))
    assert (f"{ch}-new", "staged") in _links(conn, doc_id)


def test_the_difference_is_counted_on_the_job_result(conn):
    ch = "pub-count"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(conf=0.80, regulation=_ev("MDD", 0.80),
                            cert_number=_ev("CERT-2", 0.80),
                            validity_from=_ev(None, 0.80)), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))

    assert r["kept_differences"] == ["cert_number", "regulation", "validity_from"]
    assert r["counts"] == {"kept_differences": 3}
    assert _doc(conn, doc_id)["validity_from"] is not None


def test_a_re_read_that_agrees_with_the_published_values_counts_nothing(conn):
    """Kept, and said so, but with no difference to count: the count is absent
    rather than zero, as every `counts` key is."""
    ch = "pub-same"
    _published(conn, ch)
    _seed(conn, ch, _fields(conf=0.80), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))
    assert r["kept"] == "production"
    assert "kept_differences" not in r
    assert "counts" not in r


def test_differences_are_read_against_the_document_not_old_evidence(conn):
    """F5: a field a later PRODUCTION read dropped is NULL on the document but
    still has older evidence. The document is what a kept reading is compared
    with, so a reading that also lacks the field differs in nothing."""
    ch = "pub-dropped"
    doc_id = _published(conn, ch, basic_udi_di=_ev("UDI-1"))
    _seed(conn, ch, _fields(), rev=2)               # production, no basic_udi_di
    r2 = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"}]))
    assert r2["disposition"] == "production"
    assert conn.execute("SELECT basic_udi_di FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["basic_udi_di"] is None
    _seed(conn, ch, _fields(conf=0.80), rev=3)      # staged, also no basic_udi_di
    r3 = gh.handle_gate_candidate(conn, _job(ch, rev=3, ref_gate=True, flags=[], links=[]))
    assert r3["kept"] == "production"
    assert "kept_differences" not in r3


def test_a_ref_list_in_another_order_is_no_difference(conn):
    ch = "pub-reforder"
    _published(conn, ch, ref_list=_ev(["A-1", "B-2", "C-3"]))
    _seed(conn, ch, _fields(conf=0.80, ref_list=_ev(["C-3", "A-1", "B-2"], 0.80)), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))
    assert "kept_differences" not in r
    _seed(conn, ch, _fields(conf=0.80, ref_list=_ev(["C-3", "A-1"], 0.80)), rev=3)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=3, ref_gate=True, flags=[], links=[]))
    assert r["kept_differences"] == ["ref_list"]


def test_a_manual_re_read_opens_no_review_task(conn):
    """Review never lists a task on a published document, so opening one
    would leave work nobody can see."""
    ch = "pub-manual"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(conf=0.60, type=_ev("IFU", 0.60)), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))

    assert r["disposition"] == "manual"
    assert r["kept"] == "production"
    assert _open_tasks(conn, doc_id) == 0
    assert _doc(conn, doc_id)["type"] == "DoC"


def test_a_blocking_flag_on_the_re_read_changes_nothing_either(conn):
    ch = "pub-flag"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(cert_number=_ev("CERT-9")), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True,
                                            flags=["no-item-identifier"], links=[]))
    assert r["kept"] == "production"
    assert _doc(conn, doc_id)["cert_number"] == "CERT-1"
    assert _open_tasks(conn, doc_id) == 0


def test_a_filed_re_read_of_a_published_document_is_kept(conn):
    """F2: the normal route's `filed` outcome, which is not production."""
    ch = "pub-filed"
    doc_id = _published(conn, ch)
    before = _snapshot(conn, doc_id)
    _seed(conn, ch, _fields(cert_number=_ev("CERT-2")), rev=2)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=False,
                                            flags=["no-ref-overlap"], links=[]))
    assert r["disposition"] == "filed" and r["kept"] == "production"
    assert _snapshot(conn, doc_id) == before


def test_a_document_a_reviewer_published_is_kept_too(conn):
    """The guard is about the document's status, not about who published it:
    a redelivered staged candidate must not undo a reviewer's approval and edit."""
    ch = "pub-human"
    _seed(conn, ch, _fields(conf=0.85))
    r1 = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert r1["disposition"] == "staged"
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": r1["doc_id"], "decision": "approve", "decided_by": "nat",
        "edits": {"cert_number": "CERT-FIXED"}}})

    r2 = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[], jid=3))

    assert r2["kept"] == "production"
    doc = _doc(conn, r1["doc_id"])
    assert (doc["status"], doc["cert_number"]) == ("production", "CERT-FIXED")
    assert r2["kept_differences"] == ["cert_number"]


def test_an_auto_supersede_on_a_published_document_is_still_reported(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'pub-newer','/archive/n.pdf','group') RETURNING doc_id").fetchone()["doc_id"]
    ch = "pub-older"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(validity_from=_ev("2020-01-01")), rev=2)

    r = gh.handle_gate_candidate(conn, _job(
        ch, rev=2, superseded_by_doc_id=newer,
        flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))

    assert r["disposition"] == "superseded"
    assert r["kept"] == "production"
    assert r["supersede_skipped"]
    row = _doc(conn, doc_id)
    assert (row["status"], row["superseded_by"]) == ("production", None)


# --------------------------------------------------------------------------- #
# R1: a second group's candidate for the same reading
# --------------------------------------------------------------------------- #
def test_a_second_group_s_candidate_on_a_published_document_stages_its_links(conn):
    """fetch.url hash-dedupe re-enters at validate.doc for another group (C3):
    the same reading, a candidate per group. Group 8's here proposes a link
    the published document did not have, and it must not be dropped."""
    ch = "pub-group-b"
    _seed_item(conn, f"{ch}-b1")
    doc_id = _published(conn, ch)
    r = gh.handle_gate_candidate(conn, _job(ch, rev=1, group_id=8, jid=5, ref_gate=False,
                                            flags=[], links=[
        {"item_ref": f"{ch}-b1", "match_basis": "fetch-context"}]))
    assert r["kept"] == "production" and r["links"] == 1
    assert (f"{ch}-b1", "staged") in _links(conn, doc_id)
    assert (f"{ch}-ref", "production") in _links(conn, doc_id)


def _two_groups(conn, ch, order):
    _seed_item(conn, f"{ch}-a1")
    _seed_item(conn, f"{ch}-b1")
    _seed(conn, ch, _fields())
    jobs = {
        "A": _job(ch, group_id=7, jid=1, ref_gate=True, flags=[], links=[
            {"item_ref": f"{ch}-a1", "match_basis": "ref-list"}]),
        "B": _job(ch, group_id=8, jid=2, ref_gate=False, flags=[], links=[
            {"item_ref": f"{ch}-b1", "match_basis": "fetch-context"}]),
    }
    for g in order:
        r = gh.handle_gate_candidate(conn, jobs[g])
    doc_id = r["doc_id"]
    doc = conn.execute("SELECT status, type, regulation, cert_number, validity_from "
                       "FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
    links = {(ref.removeprefix(ch), status) for ref, status in _links(conn, doc_id)}
    evidence = sorted((e["field"], e["value"], e["extract_rev"]) for e in conn.execute(
        "SELECT field, value, extract_rev FROM evidence WHERE doc_id=%s", (doc_id,)).fetchall())
    return dict(doc), links, evidence, _open_tasks(conn, doc_id)


def test_two_groups_land_the_same_whichever_arrives_first(conn):
    a_then_b = _two_groups(conn, "pub-ab", "AB")
    b_then_a = _two_groups(conn, "pub-ba", "BA")
    assert a_then_b == b_then_a
    assert a_then_b[1] == {("-a1", "production"), ("-b1", "staged")}


# --------------------------------------------------------------------------- #
# R2: a superseded document is kept the same way
# --------------------------------------------------------------------------- #
def test_a_superseded_document_is_kept_by_a_re_read_that_is_not_production(conn):
    ch = "pub-superseded"
    doc_id = _published(conn, ch)
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope, supersedes) VALUES ('DoC','MDR','2026-01-01',"
        "'production','pub-superseded-new','/archive/s.pdf','group',%s) RETURNING doc_id",
        (doc_id,)).fetchone()["doc_id"]
    conn.execute("UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
                 (newer, doc_id))
    _seed_item(conn, f"{ch}-new")
    before = _snapshot(conn, doc_id)
    _seed(conn, ch, _fields(conf=0.80, regulation=_ev("MDD", 0.80)), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[
        {"item_ref": f"{ch}-new", "match_basis": "ref-list"}]))

    assert r["kept"] == "superseded"
    assert "supersede_skipped" not in r
    assert _snapshot(conn, doc_id) == before
    assert (f"{ch}-new", "staged") in _links(conn, doc_id)


# --------------------------------------------------------------------------- #
# a re-read that is itself production replaces
# --------------------------------------------------------------------------- #
def test_a_production_re_read_replaces_fields_and_evidence(conn):
    ch = "pub-replace"
    doc_id = _published(conn, ch)
    _seed(conn, ch, _fields(cert_number=_ev("CERT-2")), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, group_id=None, ref_gate=True,
                                            flags=[], links=[
        {"item_ref": f"{ch}-ref", "match_basis": "ref-list"}]))

    assert r["disposition"] == "production"
    assert "kept" not in r
    assert _doc(conn, doc_id)["cert_number"] == "CERT-2"
    assert _evidence_revs(conn, doc_id) == [1, 2]
    # the staged name-family link rev 2 no longer claims is retracted, as before
    assert (f"{ch}-name", "retracted") in _links(conn, doc_id)


def test_a_staged_document_is_still_overwritten_by_any_re_read(conn):
    """The guard is for published and superseded documents only: a document
    nobody published takes the newest reading, whatever its disposition."""
    ch = "pub-not-published"
    _seed(conn, ch, _fields(conf=0.85))
    r1 = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert r1["disposition"] == "staged"
    _seed(conn, ch, _fields(conf=0.80, cert_number=_ev("CERT-2", 0.80)), rev=2)

    r2 = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))

    assert "kept" not in r2
    assert _doc(conn, r1["doc_id"])["cert_number"] == "CERT-2"
    assert _evidence_revs(conn, r1["doc_id"]) == [1, 2]


# --------------------------------------------------------------------------- #
# the manufacturer-binding route
# --------------------------------------------------------------------------- #
def _mfr_fields(scope_conf, cert="EC-1"):
    return {"type": _ev("EC"), "regulation": _ev("MDR"),
            "coverage_scope": _ev("manufacturer", scope_conf),
            "manufacturer": _ev("Ivoclar Vivadent AG"), "cert_number": _ev(cert)}


def _bound(conn, ch):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES "
                 "('001','IVOCLAR'), ('Ivoclar Vivadent AG','IVOCLAR') "
                 "ON CONFLICT (raw_name) DO NOTHING")
    _seed_item(conn, f"{ch}-1", "001", md=True)
    _seed(conn, ch, _mfr_fields(0.97))
    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))
    assert r["disposition"] == "mfr-bound"
    return r["doc_id"]


def test_a_bound_certificate_is_kept_when_the_re_read_cannot_bind(conn):
    ch = "pub-mfr-kept"
    doc_id = _bound(conn, ch)
    _seed(conn, ch, _mfr_fields(0.60, cert="EC-2"), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert r["kept"] == "production"
    assert r["kept_differences"] == ["cert_number"]
    assert _doc(conn, doc_id)["cert_number"] == "EC-1"
    assert _evidence_revs(conn, doc_id) == [1]
    assert _open_tasks(conn, doc_id) == 0


@pytest.mark.parametrize("md_flag,disposition", [
    (None, "mfr-binding"),     # the only MD item lost its class: md-class-unknown
    (False, "filed"),          # BC declassified it: filed
])
def test_a_bound_certificate_is_kept_on_the_other_non_production_outcomes(
        conn, md_flag, disposition):
    """F2: the manufacturer route's md-class-unknown and filed branches each
    carry their own guard."""
    ch = f"pub-mfr-{disposition}"
    doc_id = _bound(conn, ch)
    conn.execute("UPDATE item_mirror SET md_flag=%s WHERE item_ref=%s", (md_flag, f"{ch}-1"))
    before = _snapshot(conn, doc_id)
    _seed(conn, ch, _mfr_fields(0.97, cert="EC-2"), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))

    assert r["disposition"] == disposition
    assert r["kept"] == "production"
    assert _snapshot(conn, doc_id) == before


def test_a_bound_certificate_is_replaced_when_the_re_read_binds(conn):
    ch = "pub-mfr-replace"
    doc_id = _bound(conn, ch)
    _seed(conn, ch, _mfr_fields(0.97, cert="EC-2"), rev=2)

    r = gh.handle_gate_candidate(conn, _job(ch, rev=2, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert "kept" not in r
    assert _doc(conn, doc_id)["cert_number"] == "EC-2"
    assert _evidence_revs(conn, doc_id) == [1, 2]
