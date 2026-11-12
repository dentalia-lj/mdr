"""gate.candidate `route-held` (spec 2026-11-12 §9, ruled by Denis 2026-11-12:
"build it, at every status").

A person who changed what a document covers (a T3 `coverage_scope`, written by
retype or by an edit) has decided which panel it belongs in. VALIDATE never
reads T3 and routes by the reading, so a later machine reading of the same file
can arrive as `mfr-binding` over a declaration a person said covers items, or
as a group candidate over a certificate a person said covers a whole range.
Neither may publish it, link it or move it: on a published, superseded,
rejected or filed document the reading is kept, and on a staged one the
reading's values land where they are not held and the task points at the held
scope's panel. Tests don't commit, except the one that reads Review.
"""
from __future__ import annotations

import html
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from app.extract.tiers import write_extraction_attempt
from app.handlers import gate as gh
from tests.conftest import TEST_API_URL
from web.app import MFR_BINDING_REASON, create_app

MFR = "ACME"
CODE = "A01"
READ_REV = 3     # past rev 1 (first read) and rev 2 (the retype's T3 rows)


def _ev(value, conf=0.97):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": 1, "model_id": "m"}


def _fields(type_="DoC", scope="group", conf=0.97, **over):
    base = {"type": _ev(type_, conf), "regulation": _ev("MDR", conf),
            "coverage_scope": _ev(scope, conf), "cert_number": _ev("CERT-1", conf),
            "validity_from": _ev("2024-01-01", conf), "manufacturer": _ev(MFR, 0.95),
            "ref_list": _ev([])}
    base.update(over)
    return base


def _catalogue(conn, md=True, refs=("RH-1", "RH-2")):
    """ACME, resolvable by its printed name, with items under its BC code."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (MFR,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s) "
                 "ON CONFLICT (raw_name) DO NOTHING", (CODE, MFR))
    for ref in refs:
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
            "mirror_rev, updated_at) VALUES (%s,'n',%s,%s,'LJ',1,now()) "
            "ON CONFLICT (item_ref) DO NOTHING", (ref, CODE, md))


def _job(ch, *, rev=READ_REV, group_id=7, jid=11, **payload):
    p = {"content_hash": ch, "extract_rev": rev, "group_id": group_id,
         "archive_url": "file:///a.pdf"}
    p.update(payload)
    return {"id": jid, "type": "gate.candidate", "payload": p,
            "dedupe_key": f"gate:{ch}:{rev}:{group_id}"}


def _row(conn, doc_id):
    return conn.execute("SELECT * FROM document WHERE doc_id=%s", (doc_id,)).fetchone()


def _links(conn, doc_id):
    return {(r["item_ref"], r["status"]) for r in conn.execute(
        "SELECT item_ref, status FROM item_document WHERE doc_id=%s", (doc_id,))}


def _tasks(conn, doc_id, status="open"):
    return conn.execute("SELECT * FROM manual_task WHERE doc_id=%s AND kind='gate-manual' "
                        "AND status=%s ORDER BY id", (doc_id, status)).fetchall()


def _events(conn, doc_id):
    return [r["event"] for r in conn.execute(
        "SELECT event FROM audit_log WHERE doc_id=%s ORDER BY id", (doc_id,))]


def _staged(conn, ch, before, after=None):
    """A staged document first read as `before` = (type, scope), then retyped by
    a person to `after` (or not at all), with no open task left over."""
    write_extraction_attempt(conn, ch, ["T0", "T1"], _fields(*before), extract_rev=1)
    first = gh.handle_gate_candidate(conn, _job(ch, rev=1, ref_gate=False, flags=[], links=[]))
    doc_id = first["doc_id"]
    assert _row(conn, doc_id)["status"] == "staged"
    if after is not None:
        row = _row(conn, doc_id)
        gh.handle_gate_apply(conn, {"type": "gate.apply", "dedupe_key": "x", "payload": {
            "doc_id": doc_id, "decision": "retype", "decided_by": "user:natasa",
            "type": after[0], "regulation": "MDR", "coverage_scope": after[1],
            "expected": {"type": row["type"], "regulation": row["regulation"],
                         "coverage_scope": row["coverage_scope"]}}})
    conn.execute("UPDATE manual_task SET status='resolved', resolved_by='test' "
                 "WHERE doc_id=%s AND status='open'", (doc_id,))
    return doc_id


def _read(conn, ch, fields, rev=READ_REV):
    write_extraction_attempt(conn, ch, ["T0", "T1"], fields, extract_rev=rev)


def _bind_reading(conn, ch):
    """A confident whole-range reading: it binds ACME's MD items at production
    when nothing holds the document's scope."""
    _read(conn, ch, _fields("ISO", "manufacturer"))
    return gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", flags=[]))


def _ref_reading(conn, ch, *, basis="ref-list", ref_gate=True):
    """A group reading whose links match two items at a trusted basis: it
    publishes the document and both links when nothing holds the scope."""
    _read(conn, ch, _fields("DoC", "group", ref_list=_ev(["RH-1", "RH-2"])))
    return gh.handle_gate_candidate(conn, _job(ch, ref_gate=ref_gate, flags=[], links=[
        {"item_ref": "RH-1", "match_basis": basis},
        {"item_ref": "RH-2", "match_basis": basis}]))


def _retype_row(conn, doc_id):
    return conn.execute("SELECT decided_by, at FROM audit_log WHERE doc_id=%s "
                        "AND event='retype' ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()


# --------------------------------------------------------------------------- #
# the controls: no held scope, the two routes do what they always did
# --------------------------------------------------------------------------- #
def test_control_no_held_scope_a_confident_binding_still_binds(conn):
    _catalogue(conn)
    doc_id = _staged(conn, "rh-ctl-bind", ("ISO", "manufacturer"))

    r = _bind_reading(conn, "rh-ctl-bind")

    assert r["disposition"] == "mfr-bound"
    assert _row(conn, doc_id)["status"] == "production"
    assert _links(conn, doc_id) == {("RH-1", "production"), ("RH-2", "production")}


def test_control_no_held_scope_a_trusted_group_reading_still_publishes(conn):
    _catalogue(conn)
    doc_id = _staged(conn, "rh-ctl-ref", ("DoC", "group"))

    r = _ref_reading(conn, "rh-ctl-ref")

    assert r["disposition"] == "production"
    assert _links(conn, doc_id) == {("RH-1", "production"), ("RH-2", "production")}


# --------------------------------------------------------------------------- #
# staged: the reading lands where it is not held, and waits for the person
# --------------------------------------------------------------------------- #
def test_held_group_stops_a_confident_binding(conn):
    _catalogue(conn)
    ch = "rh-grp-bind"
    doc_id = _staged(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    events_before = _events(conn, doc_id)

    r = _bind_reading(conn, ch)

    assert r == {"disposition": "route-held", "doc_id": doc_id, "held_scope": "group",
                 "reviewer_edits_kept": ["coverage_scope", "type"],
                 "counts": {"reviewer_edits_kept": 2}}
    row = _row(conn, doc_id)
    assert (row["status"], row["type"], row["coverage_scope"]) == ("staged", "DoC", "group")
    assert _links(conn, doc_id) == set()
    new_events = _events(conn, doc_id)[len(events_before):]
    assert "bind-manufacturer" not in new_events and "production-write" not in new_events
    (task,) = _tasks(conn, doc_id)
    assert "route" not in task["payload"] and "reason" not in task["payload"]
    assert "flags" not in task["payload"]
    a = _retype_row(conn, doc_id)
    assert task["payload"]["retyped"] == {"by": "user:natasa", "at": a["at"].isoformat()}
    assert task["group_id"] == 7     # the latest gate-manual task's group


def test_held_manufacturer_stops_a_trusted_group_reading(conn):
    _catalogue(conn)
    ch = "rh-mfr-ref"
    doc_id = _staged(conn, ch, ("DoC", "group"), ("ISO", "manufacturer"))

    r = _ref_reading(conn, ch)

    assert r["disposition"] == "route-held" and r["held_scope"] == "manufacturer"
    row = _row(conn, doc_id)
    assert (row["status"], row["type"], row["coverage_scope"]) == ("staged", "ISO",
                                                                   "manufacturer")
    assert _links(conn, doc_id) == set()
    assert "production-write" not in _events(conn, doc_id)
    (task,) = _tasks(conn, doc_id)
    assert task["payload"]["route"] == "mfr-binding"
    assert task["payload"]["manufacturer"] == MFR
    assert "reason" not in task["payload"]         # ACME holds MD items
    assert task["payload"]["retyped"]["by"] == "user:natasa"


def test_a_held_scope_that_agrees_with_the_route_still_waits_for_the_person(conn):
    _catalogue(conn)
    ch = "rh-agree"
    doc_id = _staged(conn, ch, ("DoC", "group"), ("EC", "manufacturer"))

    r = _bind_reading(conn, ch)

    assert r["disposition"] == "route-held" and r["held_scope"] == "manufacturer"
    row = _row(conn, doc_id)
    assert (row["status"], row["type"]) == ("staged", "EC")     # type held too
    assert _links(conn, doc_id) == set()
    assert "bind-manufacturer" not in _events(conn, doc_id)
    (task,) = _tasks(conn, doc_id)
    assert task["payload"]["route"] == "mfr-binding"


def test_a_held_staged_document_still_takes_the_reading_where_it_is_not_held(conn):
    _catalogue(conn)
    ch = "rh-unheld"
    doc_id = _staged(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    _read(conn, ch, _fields("ISO", "manufacturer", cert_number=_ev("CERT-9"),
                            validity_from=_ev("2025-03-01")))

    gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", flags=[]))

    row = _row(conn, doc_id)
    assert row["cert_number"] == "CERT-9"
    assert row["validity_from"].isoformat() == "2025-03-01"
    revs = {r["field"]: r["extract_rev"] for r in conn.execute(
        "SELECT field, max(extract_rev) extract_rev FROM evidence WHERE doc_id=%s "
        "AND tier<>'T3' GROUP BY field", (doc_id,))}
    assert revs["cert_number"] == READ_REV
    assert revs["coverage_scope"] == 1 and revs["type"] == 1   # held: no machine row


def test_a_held_upsert_takes_no_pointer_and_no_manufacturer_from_the_reading(conn):
    _catalogue(conn)
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('OTHER')")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('OTHER','OTHER')")
    ch = "rh-ptr"
    doc_id = _staged(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    others = [conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status) VALUES ('EC','MDR','manufacturer',%s,'file:///o.pdf','production') "
        "RETURNING doc_id", (f"rh-ptr-other-{i}",)).fetchone()["doc_id"] for i in range(3)]
    _read(conn, ch, _fields("DoC", "group", manufacturer=_ev("OTHER", 0.97)))

    r = gh.handle_gate_candidate(conn, _job(
        ch, ref_gate=True, flags=[], links=[], supersedes=others[0],
        related_doc_id=others[1], cert_doc_id=others[2]))

    assert r["disposition"] == "route-held"
    row = _row(conn, doc_id)
    assert (row["supersedes"], row["referenced_doc_id"], row["cert_doc_id"]) == (None, None, None)
    assert row["canonical_manufacturer"] == MFR


def test_a_held_staged_document_is_not_auto_superseded(conn):
    _catalogue(conn)
    ch = "rh-auto-sup"
    doc_id = _staged(conn, ch, ("DoC", "manufacturer"), ("DoC", "group"))
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'rh-newer','file:///n.pdf','group') RETURNING doc_id").fetchone()["doc_id"]
    _read(conn, ch, _fields("DoC", "group", validity_from=_ev("2020-01-01", 0.99)))

    r = gh.handle_gate_candidate(conn, _job(
        ch, superseded_by_doc_id=newer, flags=["older-than-current", "auto-superseded"],
        ref_gate=True, links=[]))

    assert r["disposition"] == "route-held"
    row = _row(conn, doc_id)
    assert (row["status"], row["superseded_by"]) == ("staged", None)
    assert "auto-superseded" not in _events(conn, doc_id)
    (task,) = _tasks(conn, doc_id)
    assert "flags" not in task["payload"]           # the reading's flags dropped


def test_route_held_keeps_an_open_task_and_rewrites_it(conn):
    """An open task is retargeted, never duplicated, and its old route and the
    reading's flags go."""
    _catalogue(conn)
    ch = "rh-open"
    doc_id = _staged(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    conn.execute("UPDATE manual_task SET status='open' WHERE doc_id=%s", (doc_id,))
    conn.execute("UPDATE manual_task SET payload = payload || "
                 "'{\"route\":\"mfr-binding\",\"flags\":[\"x\"]}'::jsonb WHERE doc_id=%s",
                 (doc_id,))

    _bind_reading(conn, ch)

    (task,) = _tasks(conn, doc_id)
    assert set(task["payload"]) == {"retyped"}


# --------------------------------------------------------------------------- #
# the stale-reading no-op still comes first, on both routes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("route", ["mfr-binding", None])
def test_a_superseded_reading_is_a_no_op_before_the_held_branch(conn, route):
    _catalogue(conn)
    ch = f"rh-stale-{route}"
    doc_id = _staged(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    _read(conn, ch, _fields("ISO", "manufacturer"))      # rev 3 is the newest
    tasks_before = conn.execute("SELECT count(*) c FROM manual_task").fetchone()["c"]
    row_before = dict(_row(conn, doc_id))
    payload = {"route": route} if route else {}

    r = gh.handle_gate_candidate(conn, _job(ch, rev=1, flags=[], links=[], **payload))

    assert r == {"skipped": "superseded-rev", "content_hash": ch, "extract_rev": 1}
    assert dict(_row(conn, doc_id)) == row_before
    assert conn.execute("SELECT count(*) c FROM manual_task").fetchone()["c"] == tasks_before


# --------------------------------------------------------------------------- #
# every other status: kept, no link at all, no task
# --------------------------------------------------------------------------- #
def _published_held(conn, ch, before, after, status="production"):
    doc_id = _staged(conn, ch, before, after)
    conn.execute("UPDATE document SET status=%s WHERE doc_id=%s", (status, doc_id))
    return doc_id


def test_held_group_on_a_published_document_stops_a_confident_binding(conn):
    """Today this links every MD item at production, silently: the document is
    already production, so no audit row is written either."""
    _catalogue(conn)
    ch = "rh-pub-bind"
    doc_id = _published_held(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"))
    tasks_before = conn.execute("SELECT count(*) c FROM manual_task WHERE doc_id=%s",
                                (doc_id,)).fetchone()["c"]

    r = _bind_reading(conn, ch)

    assert r["disposition"] == "route-held" and r["held_scope"] == "group"
    assert r["kept"] == "production"
    assert _row(conn, doc_id)["status"] == "production"
    assert _links(conn, doc_id) == set()
    assert conn.execute("SELECT count(*) c FROM manual_task WHERE doc_id=%s",
                        (doc_id,)).fetchone()["c"] == tasks_before
    assert not _tasks(conn, doc_id)


def test_held_scope_on_a_published_document_stops_the_staged_proposals(conn):
    _catalogue(conn)
    ch = "rh-pub-prop"
    doc_id = _published_held(conn, ch, ("DoC", "group"), ("DoC", "manufacturer"))

    r = _ref_reading(conn, ch, basis="name-family", ref_gate=False)

    assert r["disposition"] == "route-held" and r["kept"] == "production"
    assert _links(conn, doc_id) == set()
    assert not _tasks(conn, doc_id)


@pytest.mark.parametrize("status", ["rejected", "superseded", "filed"])
@pytest.mark.parametrize("reading", ["bind", "ref"])
def test_held_scope_on_a_settled_document_is_kept(conn, status, reading):
    _catalogue(conn)
    ch = f"rh-{status}-{reading}"
    doc_id = _published_held(conn, ch, ("ISO", "manufacturer"), ("DoC", "group"), status)
    row_before = dict(_row(conn, doc_id))
    ev_before = conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                             (doc_id,)).fetchone()["c"]

    r = _bind_reading(conn, ch) if reading == "bind" else _ref_reading(conn, ch)

    assert r["disposition"] == "route-held" and r["kept"] == status
    assert dict(_row(conn, doc_id)) == row_before
    assert conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                        (doc_id,)).fetchone()["c"] == ev_before
    assert _links(conn, doc_id) == set()
    assert not _tasks(conn, doc_id)


# --------------------------------------------------------------------------- #
# a T3 scope no person retyped: the task carries no `retyped`
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def test_an_edited_scope_without_a_retype_row_writes_no_retyped_and_review_reads_as_usual(
        client, conn):
    _catalogue(conn)
    ch = "rh-edit"
    doc_id = _staged(conn, ch, ("DoC", "group"))
    gh._apply_edits(conn, doc_id, {"coverage_scope": "manufacturer"})
    assert gh._reviewer_held(conn, doc_id) == ("coverage_scope",)

    r = _ref_reading(conn, ch)

    assert r["disposition"] == "route-held" and r["held_scope"] == "manufacturer"
    (task,) = _tasks(conn, doc_id)
    assert "retyped" not in task["payload"]
    assert task["payload"]["route"] == "mfr-binding"
    assert _links(conn, doc_id) == set()
    conn.commit()

    page = client.get("/staging/docs", params={"reason": "range"}).text
    m = re.search(rf'<div class="stage-row[^"]*" id="doc-{doc_id}">.*?</details>', page, re.S)
    assert m, "the document should be listed under Covers a whole range"
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(0))))
    assert MFR_BINDING_REASON in text
