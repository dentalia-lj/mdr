"""gate.apply `retype` (spec 2026-11-12 §6; PRD v3 decision `retype`).

A person changes what a staged document IS. GATE locks the row, refuses on
`app/retype.py` `problems`, then writes type and scope as T3 evidence, takes
staged links off a whole-range document, fixes citations both ways and moves
the review task to the new panel. It never publishes, never enqueues, and
leaves the task open. Tests don't commit.
"""
from __future__ import annotations

import json

import pytest

from app import retype
from app.handlers import gate as gh

MFR = "ACME"


def _mfr(conn, name=MFR):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _item(conn, ref, raw=MFR, md=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,%s,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (ref, raw, md))


def _doc(conn, key, type_="ISO", regulation="MDR", scope="manufacturer", *,
         canonical=MFR, cert_number=None, validity_from="2026-04-01", validity_to=None,
         cert_doc_id=None, status="staged"):
    if canonical:
        _mfr(conn, canonical)
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, validity_from, validity_to, cert_number, cert_doc_id, canonical_manufacturer) "
        "VALUES (%s,%s,%s,%s,'file:///a.pdf',%s,%s,%s,%s,%s,%s) RETURNING doc_id",
        (type_, regulation, scope, f"retype-{key}", status, validity_from, validity_to,
         cert_number, cert_doc_id, canonical)).fetchone()["doc_id"]


def _row(conn, doc_id):
    return conn.execute("SELECT * FROM document WHERE doc_id=%s", (doc_id,)).fetchone()


def _link(conn, doc_id, ref, status="staged", basis="ref-list"):
    _item(conn, ref)
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s,%s,%s,%s)", (ref, doc_id, basis, status))


def _links(conn, doc_id):
    return {r["item_ref"]: r["status"] for r in conn.execute(
        "SELECT item_ref, status FROM item_document WHERE doc_id=%s", (doc_id,))}


def _task(conn, doc_id, payload, kind="gate-manual", group_id=None):
    return conn.execute(
        "INSERT INTO manual_task (kind, doc_id, group_id, payload) VALUES (%s,%s,%s,%s) "
        "RETURNING id", (kind, doc_id, group_id, json.dumps(payload))).fetchone()["id"]


def _tasks(conn, doc_id, kind="gate-manual"):
    return conn.execute("SELECT * FROM manual_task WHERE doc_id=%s AND kind=%s ORDER BY id",
                        (doc_id, kind)).fetchall()


def _t3(conn, doc_id):
    return conn.execute(
        "SELECT field, value, page, confidence, extract_rev FROM evidence "
        "WHERE doc_id=%s AND tier='T3' ORDER BY extract_rev, field", (doc_id,)).fetchall()


def _audit(conn, doc_id, event=None):
    sql = "SELECT * FROM audit_log WHERE doc_id=%s"
    args: tuple = (doc_id,)
    if event:
        sql += " AND event=%s"
        args += (event,)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def _count(conn, table):
    return conn.execute(f"SELECT count(*) c FROM {table}").fetchone()["c"]


def _vals(conn, doc_id):
    r = _row(conn, doc_id)
    return {f: r[f] for f in retype.FIELDS}


def _job(conn, doc_id, after, *, expected=None, jid=None, note=None):
    payload = {"doc_id": doc_id, "decision": "retype", "decided_by": "user:natasa",
               "type": after[0], "regulation": after[1], "coverage_scope": after[2],
               "expected": expected if expected is not None else _vals(conn, doc_id)}
    if note:
        payload["note"] = note
    job = {"type": "gate.apply", "payload": payload, "dedupe_key": f"apply:{doc_id}:retype"}
    if jid is not None:
        job["id"] = jid
    return job


def _apply(conn, doc_id, after, **kw):
    return gh.handle_gate_apply(conn, _job(conn, doc_id, after, **kw))


# --- (before, after) table -------------------------------------------------
# Each row seeds one staged link and a gate-manual task carrying the old
# reading's route and flags, then checks the columns, the T3 rows, the link,
# and the task's route afterwards.
TABLE = [
    # id,                    before,                       after,                         T3 fields,                            link after,  route after
    ("ISO->EC type only",    ("ISO", "MDR", "manufacturer"), ("EC", "MDR", "manufacturer"), ["coverage_scope", "type"],             "retracted", "mfr-binding"),
    ("ISO/mfr->DoC/group",   ("ISO", "MDR", "manufacturer"), ("DoC", "MDR", "group"),       ["coverage_scope", "type"],             "staged",   None),
    ("DoC/group->EC/mfr",    ("DoC", "MDR", "group"),        ("EC", "MDR", "manufacturer"), ["coverage_scope", "type"],             "retracted", "mfr-binding"),
    ("EC/mfr->DoC/group",    ("EC", "MDD", "manufacturer"),  ("DoC", "MDD", "group"),       ["coverage_scope", "type"],             "staged",   None),
    ("DoC->IFU",             ("DoC", "MDR", "group"),        ("IFU", "MDR", "group"),       ["coverage_scope", "type"],             "staged",   None),
    ("regulation only grp",  ("DoC", "MDR", "group"),        ("DoC", "MDD", "group"),       ["coverage_scope", "regulation", "type"], "staged", None),
    ("regulation only mfr",  ("ISO", "MDR", "manufacturer"), ("ISO", "n.a.", "manufacturer"), ["coverage_scope", "regulation", "type"], "retracted", "mfr-binding"),
    ("ISO->EC group (394)",  ("ISO", "MDR", "manufacturer"), ("EC", "MDR", "group"),        ["coverage_scope", "type"],             "staged",   None),
    ("DoC->DoC whole range", ("DoC", "MDR", "group"),        ("DoC", "MDR", "manufacturer"), ["coverage_scope", "type"],            "retracted", "mfr-binding"),
]


@pytest.mark.parametrize("case, before, after, t3_fields, link_after, route_after", TABLE,
                         ids=[t[0] for t in TABLE])
def test_retype_table(conn, case, before, after, t3_fields, link_after, route_after):
    doc_id = _doc(conn, "tbl", *before)
    _link(conn, doc_id, "L1")
    old_route = {"route": "mfr-binding", "manufacturer": MFR} if before[2] == "manufacturer" else {}
    _task(conn, doc_id, {**old_route, "flags": ["no-ref-overlap"]})
    jobs_before = _count(conn, "job")

    out = _apply(conn, doc_id, after)

    assert _vals(conn, doc_id) == dict(zip(retype.FIELDS, after))
    assert _row(conn, doc_id)["status"] == "staged"
    rows = _t3(conn, doc_id)
    assert sorted(r["field"] for r in rows) == t3_fields
    for r in rows:
        assert r["page"] is None and float(r["confidence"]) == 1.0
        assert r["value"] == dict(zip(retype.FIELDS, after))[r["field"]]
    assert _links(conn, doc_id) == {"L1": link_after}
    assert out["links_off"] == (1 if link_after == "retracted" else 0)
    (task,) = _tasks(conn, doc_id)
    assert task["status"] == "open"
    assert task["payload"].get("route") == route_after
    assert "flags" not in task["payload"]
    assert task["payload"]["retyped"]["by"] == "user:natasa"
    assert [a["event"] for a in _audit(conn, doc_id) if a["item_ref"] is None] == ["retype"]
    assert not _audit(conn, doc_id, "production-write")
    assert _count(conn, "job") == jobs_before


# --- the specific writes ----------------------------------------------------

def test_doc_831_iso_whole_range_to_declaration_for_items(conn):
    doc_id = _doc(conn, "831", "ISO", "MDR", "manufacturer", canonical="ZLATARNA CELJE",
                  cert_number="028/MDR", validity_from="2026-01-15", validity_to="2031-01-14")
    task_id = _task(conn, doc_id, {"route": "mfr-binding", "manufacturer": "ZLATARNA CELJE",
                                   "reason": "md-class-unknown", "flags": ["device-enumeration"]})
    jobs_before = _count(conn, "job")

    out = _apply(conn, doc_id, ("DoC", "MDR", "group"), note="read as ISO")

    row = _row(conn, doc_id)
    assert (row["type"], row["coverage_scope"], row["status"]) == ("DoC", "group", "staged")
    assert (row["cert_number"], row["validity_from"].isoformat(),
            row["validity_to"].isoformat()) == ("028/MDR", "2026-01-15", "2031-01-14")
    rows = _t3(conn, doc_id)
    assert [(r["field"], r["value"]) for r in rows] == [("coverage_scope", "group"),
                                                       ("type", "DoC")]
    assert all(r["page"] is None and float(r["confidence"]) == 1.0 for r in rows)
    assert gh._reviewer_held(conn, doc_id) == ("coverage_scope", "type")

    (task,) = _tasks(conn, doc_id)
    assert task["id"] == task_id and task["status"] == "open"
    for gone in ("route", "reason", "flags"):
        assert gone not in task["payload"]
    assert task["payload"]["retyped"]["by"] == "user:natasa"
    assert task["payload"]["retyped"]["at"]

    (a,) = _audit(conn, doc_id, "retype")
    assert a["decided_by"] == "user:natasa"
    assert a["detail"] == {
        "before": {"type": "ISO", "regulation": "MDR", "coverage_scope": "manufacturer"},
        "after": {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"},
        "links_off": 0, "citations_cleared": [], "citations_set": [], "note": "read as ISO"}
    assert not _audit(conn, doc_id, "production-write")
    assert _count(conn, "job") == jobs_before
    assert out == {"decision": "retype", "doc_id": doc_id, "links_off": 0,
                   "citations_cleared": [], "citations_set": []}


def test_declaration_to_whole_range_certificate_takes_links_off_and_answers_citations(conn):
    doc_id = _doc(conn, "d2ec", "DoC", "MDR", "group", cert_number="CE 607775")
    _link(conn, doc_id, "A2")
    _link(conn, doc_id, "A1")
    citing = _doc(conn, "citing", "DoC", "MDR", "group", cert_number="CE 607775 R000")
    _task(conn, doc_id, {"flags": ["no-ref-overlap"]})

    out = _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))

    assert _links(conn, doc_id) == {"A1": "retracted", "A2": "retracted"}
    assert not _audit(conn, doc_id, "link-rejected")
    rej = _audit(conn, doc_id, "link-retracted")
    assert [r["item_ref"] for r in rej] == ["A1", "A2"]
    assert all(r["detail"]["via"] == "retype" and r["detail"]["link_status_before"] == "staged"
               and r["detail"]["match_basis_before"] == "ref-list" for r in rej)
    (task,) = _tasks(conn, doc_id)
    assert task["payload"]["route"] == "mfr-binding"
    assert task["payload"]["manufacturer"] == MFR
    assert _row(conn, citing)["cert_doc_id"] == doc_id
    # the document is no longer a declaration, so its own citation goes
    assert _row(conn, doc_id)["cert_doc_id"] is None
    assert out["links_off"] == 2 and out["citations_set"] == [citing]
    assert _audit(conn, doc_id, "retype")[0]["detail"]["citations_set"] == [citing]


def test_retype_to_whole_range_then_bind_links_every_md_item(conn):
    """The links write 2 takes off are `retracted`, not `rejected` (amended
    2026-11-12 after final review): a person refused none of those items, and
    `rejected` is sticky in `_upsert_link`, so the whole-range approval would
    have skipped exactly the items the reading had matched."""
    for ref in ("B1", "B2", "B3"):
        _item(conn, ref, md=True)
    _item(conn, "N1", md=False)
    doc_id = _doc(conn, "bind", "DoC", "MDR", "group")
    _link(conn, doc_id, "B1")
    _link(conn, doc_id, "B2")
    _task(conn, doc_id, {})

    _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))
    assert _links(conn, doc_id) == {"B1": "retracted", "B2": "retracted"}

    gh.handle_gate_apply(conn, {"type": "gate.apply", "payload": {
        "doc_id": doc_id, "decision": "bind-manufacturer", "decided_by": "user:natasa",
        "manufacturer": MFR, "expected_scope": "manufacturer"}})

    rows = conn.execute("SELECT item_ref, status, match_basis FROM item_document "
                        "WHERE doc_id=%s ORDER BY item_ref", (doc_id,)).fetchall()
    assert [(r["item_ref"], r["status"], r["match_basis"]) for r in rows] == [
        ("B1", "production", "mfr-scope"), ("B2", "production", "mfr-scope"),
        ("B3", "production", "mfr-scope")]
    assert _row(conn, doc_id)["status"] == "production"


def test_certificate_to_declaration_clears_citing_docs(conn):
    cert = _doc(conn, "ec", "EC", "MDR", "manufacturer", cert_number="028/MDR")
    citing = _doc(conn, "c1", "DoC", "MDR", "group", cert_number="028/MDR", cert_doc_id=cert)

    out = _apply(conn, cert, ("DoC", "MDR", "group"))

    assert _row(conn, citing)["cert_doc_id"] is None
    assert out["citations_cleared"] == [citing]
    assert _audit(conn, cert, "retype")[0]["detail"]["citations_cleared"] == [citing]


def test_certificate_to_certificate_keeps_citations(conn):
    cert = _doc(conn, "ec2", "EC", "MDR", "manufacturer", cert_number="028/MDR")
    citing = _doc(conn, "c2", "DoC", "MDR", "group", cert_number="028/MDR", cert_doc_id=cert)

    out = _apply(conn, cert, ("ISO", "MDR", "manufacturer"))

    assert _row(conn, citing)["cert_doc_id"] == cert
    assert out["citations_cleared"] == [] and out["citations_set"] == []


def test_declaration_to_ifu_clears_its_own_citation(conn):
    cert = _doc(conn, "own-cert", "EC", "MDR", "manufacturer")
    doc_id = _doc(conn, "own", "DoC", "MDR", "group", cert_doc_id=cert)

    _apply(conn, doc_id, ("IFU", "MDR", "group"))

    assert _row(conn, doc_id)["cert_doc_id"] is None


def test_regulation_only_keeps_scope_links_and_route_and_drops_flags(conn):
    doc_id = _doc(conn, "reg", "DoC", "MDR", "group")
    _link(conn, doc_id, "R1")
    _task(conn, doc_id, {"manufacturer": MFR, "flags": ["no-ref-overlap"]})

    _apply(conn, doc_id, ("DoC", "MDD", "group"))

    assert _vals(conn, doc_id) == {"type": "DoC", "regulation": "MDD", "coverage_scope": "group"}
    assert _links(conn, doc_id) == {"R1": "staged"}
    (task,) = _tasks(conn, doc_id)
    assert task["payload"] == {"manufacturer": MFR, "retyped": task["payload"]["retyped"]}


def test_type_only_writes_scope_too_and_not_regulation(conn):
    doc_id = _doc(conn, "only", "ISO", "MDR", "manufacturer")
    _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))
    assert [r["field"] for r in _t3(conn, doc_id)] == ["coverage_scope", "type"]


def test_latest_retype_prevails(conn):
    doc_id = _doc(conn, "twice", "ISO", "MDR", "manufacturer")
    _apply(conn, doc_id, ("DoC", "MDR", "group"))
    _apply(conn, doc_id, ("EC", "MDD", "manufacturer"))

    assert _vals(conn, doc_id) == {"type": "EC", "regulation": "MDD",
                                   "coverage_scope": "manufacturer"}
    rows = _t3(conn, doc_id)
    first = {r["field"]: r for r in rows if r["extract_rev"] == rows[0]["extract_rev"]}
    second = {r["field"]: r for r in rows if r["extract_rev"] != rows[0]["extract_rev"]}
    assert set(first) == {"type", "coverage_scope"}
    assert set(second) == {"type", "regulation", "coverage_scope"}
    assert min(r["extract_rev"] for r in second.values()) > rows[0]["extract_rev"]
    assert {f: r["value"] for f, r in second.items()} == {
        "type": "EC", "regulation": "MDD", "coverage_scope": "manufacturer"}
    assert len(_audit(conn, doc_id, "retype")) == 2


@pytest.mark.parametrize("md, reason", [(None, "md-class-unknown"), (True, None),
                                       # BC declassified every item: a statement,
                                       # not a blank, so the usual sentence holds
                                       (False, None)])
def test_whole_range_task_reason_follows_the_catalogue(conn, md, reason):
    doc_id = _doc(conn, "rsn", "DoC", "MDR", "group")
    _item(conn, "M1", md=md)
    _task(conn, doc_id, {})

    _apply(conn, doc_id, ("ISO", "MDR", "manufacturer"))

    (task,) = _tasks(conn, doc_id)
    assert task["payload"].get("reason") == reason
    assert task["payload"]["manufacturer"] == MFR


def test_the_tasks_retyped_time_is_the_audit_rows_time(conn):
    """Both from the transaction's clock, so the task and the trail agree."""
    from datetime import datetime

    doc_id = _doc(conn, "when", "ISO", "MDR", "manufacturer")
    _apply(conn, doc_id, ("DoC", "MDR", "group"))

    (task,) = _tasks(conn, doc_id)
    (a,) = _audit(conn, doc_id, "retype")
    assert datetime.fromisoformat(task["payload"]["retyped"]["at"]) == a["at"]


def test_unknown_manufacturer_to_whole_range_sets_route_without_manufacturer(conn):
    doc_id = _doc(conn, "unk", "DoC", "MDR", "group", canonical=None)
    _task(conn, doc_id, {"flags": ["mfr-unresolved"]})

    _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))

    (task,) = _tasks(conn, doc_id)
    assert task["payload"]["route"] == "mfr-binding"
    assert "manufacturer" not in task["payload"]
    assert "reason" not in task["payload"]


def test_whole_range_keeps_the_tasks_manufacturer_when_the_document_has_none(conn):
    doc_id = _doc(conn, "tm", "DoC", "MDR", "group", canonical=None)
    _mfr(conn)
    _item(conn, "TM1", md=True)
    _task(conn, doc_id, {"manufacturer": MFR})

    _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))

    (task,) = _tasks(conn, doc_id)
    assert task["payload"]["manufacturer"] == MFR and "reason" not in task["payload"]


def test_no_open_task_opens_one(conn):
    doc_id = _doc(conn, "none", "ISO", "MDR", "manufacturer")
    old = _task(conn, doc_id, {"route": "mfr-binding"}, group_id=77)
    conn.execute("UPDATE manual_task SET status='resolved' WHERE id=%s", (old,))

    _apply(conn, doc_id, ("DoC", "MDR", "group"))

    tasks = _tasks(conn, doc_id)
    assert len(tasks) == 2
    new = tasks[1]
    assert new["status"] == "open" and new["group_id"] == 77
    assert set(new["payload"]) == {"retyped"}
    assert tasks[0]["status"] == "resolved"


def test_other_task_kinds_are_left_alone(conn):
    doc_id = _doc(conn, "kinds", "ISO", "MDR", "manufacturer")
    other = _task(conn, doc_id, {"flags": ["x"], "route": "mfr-binding"},
                  kind="dead-job-followup")
    _task(conn, doc_id, {"route": "mfr-binding"})

    _apply(conn, doc_id, ("DoC", "MDR", "group"))

    (o,) = _tasks(conn, doc_id, kind="dead-job-followup")
    assert o["id"] == other and o["status"] == "open"
    assert o["payload"] == {"flags": ["x"], "route": "mfr-binding"}
    (g,) = _tasks(conn, doc_id)
    assert "route" not in g["payload"]


def test_group_scope_after_touches_no_link(conn):
    doc_id = _doc(conn, "gl", "ISO", "MDR", "manufacturer")
    _link(conn, doc_id, "G1")
    _apply(conn, doc_id, ("DoC", "MDR", "group"))
    assert _links(conn, doc_id) == {"G1": "staged"}
    assert not _audit(conn, doc_id, "link-rejected")
    assert not _audit(conn, doc_id, "link-retracted")


def test_only_staged_links_go_off(conn):
    doc_id = _doc(conn, "only-staged", "DoC", "MDR", "group")
    _link(conn, doc_id, "S1")
    _link(conn, doc_id, "X1", "rejected")
    _apply(conn, doc_id, ("EC", "MDR", "manufacturer"))
    assert _links(conn, doc_id) == {"S1": "retracted", "X1": "rejected"}
    assert [r["item_ref"] for r in _audit(conn, doc_id, "link-retracted")] == ["S1"]
    assert not _audit(conn, doc_id, "link-rejected")


def test_reject_then_reopen_keeps_the_retype_held(conn):
    doc_id = _doc(conn, "rr", "ISO", "MDR", "manufacturer")
    _apply(conn, doc_id, ("DoC", "MDR", "group"))
    for decision in ("reject", "reopen"):
        gh.handle_gate_apply(conn, {"type": "gate.apply", "payload": {
            "doc_id": doc_id, "decision": decision, "decided_by": "user:natasa"}})
    assert _row(conn, doc_id)["status"] == "staged"
    assert set(gh._reviewer_held(conn, doc_id)) >= {"type", "coverage_scope"}


def test_own_job_does_not_refuse_itself(conn):
    doc_id = _doc(conn, "own-job", "ISO", "MDR", "manufacturer")
    job = _job(conn, doc_id, ("DoC", "MDR", "group"))
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status) "
        "VALUES ('gate.apply', %s, %s, 'running') RETURNING id",
        (json.dumps(job["payload"]), job["dedupe_key"])).fetchone()["id"]
    job["id"] = jid
    assert gh.handle_gate_apply(conn, job)["decision"] == "retype"


# --- refusals ---------------------------------------------------------------

def _refusal_missing(conn, d):
    return {"doc_id": d + 100000}


def _refusal_published(conn, d):
    conn.execute("UPDATE document SET status='production' WHERE doc_id=%s", (d,))
    return {}


def _refusal_job(type_, payload_of):
    def setup(conn, d):
        h = _row(conn, d)["content_hash"]
        conn.execute("INSERT INTO job (type, payload, dedupe_key, status) "
                     "VALUES (%s::job_type, %s, %s, 'pending')",
                     (type_, json.dumps(payload_of(d, h)), f"refuse-{type_}-{d}"))
        return {}
    return setup


def _refusal_chain(conn, d):
    other = _doc(conn, "chain", "ISO", "MDR", "manufacturer")
    conn.execute("UPDATE document SET superseded_by=%s WHERE doc_id=%s", (d, other))
    return {}


def _refusal_production_link(conn, d):
    _link(conn, d, "PL1", "production", basis="mfr-scope")
    return {}


REFUSALS = [
    ("not-found", _refusal_missing),
    ("not-staged", _refusal_published),
    ("bad-type", lambda c, d: {"after": ("XX", "MDR", "group")}),
    ("bad-regulation", lambda c, d: {"after": ("DoC", "IVDR", "group")}),
    ("bad-scope", lambda c, d: {"after": ("DoC", "MDR", "item")}),
    ("unchanged", lambda c, d: {"after": ("ISO", "MDR", "manufacturer")}),
    ("stale", lambda c, d: {"expected": {"type": "EC", "regulation": "MDR",
                                         "coverage_scope": "manufacturer"}}),
    ("stale (no expected)", lambda c, d: {"expected": {}}),
    ("in-flight", _refusal_job("validate.doc", lambda d, h: {"content_hash": h})),
    ("in-flight (gate.apply)", _refusal_job(
        "gate.apply", lambda d, h: {"doc_id": d, "decision": "reject", "decided_by": "u"})),
    ("production-link", _refusal_production_link),
    ("in-chain", _refusal_chain),
]


@pytest.mark.parametrize("code, setup", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_each_problem_refuses_and_writes_nothing(conn, code, setup):
    doc_id = _doc(conn, "refuse", "ISO", "MDR", "manufacturer")
    _link(conn, doc_id, "RL1")
    _task(conn, doc_id, {"route": "mfr-binding", "flags": ["f"]})
    kw = {"doc_id": doc_id, "after": ("DoC", "MDR", "group"), "expected": None}
    kw.update(setup(conn, doc_id))

    def snap():
        return (conn.execute("SELECT * FROM document ORDER BY doc_id").fetchall(),
                conn.execute("SELECT * FROM item_document ORDER BY item_ref").fetchall(),
                conn.execute("SELECT * FROM manual_task ORDER BY id").fetchall(),
                _count(conn, "evidence"), _count(conn, "audit_log"), _count(conn, "job"))
    before = snap()
    payload = {"doc_id": kw["doc_id"], "decision": "retype", "decided_by": "user:natasa",
               "type": kw["after"][0], "regulation": kw["after"][1],
               "coverage_scope": kw["after"][2],
               "expected": kw["expected"] if kw["expected"] is not None
               else {"type": "ISO", "regulation": "MDR", "coverage_scope": "manufacturer"}}
    with pytest.raises(ValueError, match=code.split(" ")[0]):
        gh.handle_gate_apply(conn, {"type": "gate.apply", "payload": payload})
    assert snap() == before
