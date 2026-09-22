"""Backfilling `reason` onto mfr-binding tasks raised before the field existed.

Measured on the live registry 2026-08-31: 17 open mfr-binding tasks, all 17
with no `reason`, so every one renders the DEFAULT review sentence -- the exact
wording `[mfr-bind-empty-class]` was written to replace, on the documents it was
written for. Doc 2 (3Shape Poland, ISO MD 583446) is one of them.
"""
from __future__ import annotations

from app import repair_mfr_task_reason as r


def _seed_entity(conn, name):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _seed_doc(conn, content_hash):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('ISO','n.a.','manufacturer',%s,'file:///a.pdf','staged') "
        "RETURNING doc_id", (content_hash,)).fetchone()["doc_id"]


def _seed_task(conn, doc_id, manufacturer, reason=None):
    payload = {"route": "mfr-binding", "manufacturer": manufacturer}
    if reason:
        payload["reason"] = reason
    from psycopg.types.json import Json
    return conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) "
        "VALUES ('gate-manual', %s, %s) RETURNING id",
        (doc_id, Json(payload))).fetchone()["id"]


def _seed_item(conn, item_ref, raw, md_flag):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) VALUES (%s,'n',%s,%s,'LJ',1,now())",
        (item_ref, raw, md_flag))


def _reason(conn, task_id):
    return conn.execute(
        "SELECT payload->>'reason' AS r FROM manual_task WHERE id=%s", (task_id,)
    ).fetchone()["r"]


def test_a_blank_class_task_is_stamped_and_a_classified_one_is_left_alone(conn):
    """The discriminator is `gate._handle_mfr_binding`'s tri-state md_flag, not
    a re-derivation of it: no TRUE and no FALSE means BC has said nothing."""
    _seed_entity(conn, "BLANK AG")
    _seed_entity(conn, "KNOWN AG")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('B1','BLANK AG'), ('K1','KNOWN AG')")
    _seed_item(conn, "B-1", "B1", None)      # blank class -> stamp
    _seed_item(conn, "K-1", "K1", True)      # BC classified -> leave alone

    blank_task = _seed_task(conn, _seed_doc(conn, "rr-blank"), "BLANK AG")
    known_task = _seed_task(conn, _seed_doc(conn, "rr-known"), "KNOWN AG")

    planned = r.plan(conn)
    assert [p["task_id"] for p in planned] == [blank_task]
    assert planned[0]["reason"] == "md-class-unknown"

    assert r.apply(conn, planned) == 1
    assert _reason(conn, blank_task) == "md-class-unknown"
    assert _reason(conn, known_task) is None


def test_a_declassified_manufacturer_is_left_alone(conn):
    """`md_flag = FALSE` is BC DECLASSIFYING the item -- a positive statement,
    not a gap. The standing sentence is already honest for it."""
    _seed_entity(conn, "GONE AG")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('G1','GONE AG')")
    _seed_item(conn, "G-1", "G1", False)
    task_id = _seed_task(conn, _seed_doc(conn, "rr-gone"), "GONE AG")

    assert r.plan(conn) == []
    assert _reason(conn, task_id) is None


def test_an_already_stamped_task_is_never_rewritten_and_a_second_run_plans_zero(conn):
    _seed_entity(conn, "BLANK AG")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('B1','BLANK AG')")
    _seed_item(conn, "B-1", "B1", None)
    already = _seed_task(conn, _seed_doc(conn, "rr-done"), "BLANK AG",
                         reason="md-class-unknown")
    fresh = _seed_task(conn, _seed_doc(conn, "rr-fresh"), "BLANK AG")

    assert [p["task_id"] for p in r.plan(conn)] == [fresh]
    r.apply(conn, r.plan(conn))

    assert r.plan(conn) == []
    assert _reason(conn, already) == "md-class-unknown"


def test_every_stamp_leaves_its_own_audit_row(conn):
    _seed_entity(conn, "BLANK AG")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('B1','BLANK AG')")
    _seed_item(conn, "B-1", "B1", None)
    doc_id = _seed_doc(conn, "rr-audit")
    _seed_task(conn, doc_id, "BLANK AG")

    r.apply(conn, r.plan(conn))

    row = conn.execute(
        "SELECT event, decided_by, detail FROM audit_log WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["event"] == "task-reason-backfilled"
    assert row["decided_by"] == "tool:repair-mfr-task-reason"
    assert row["detail"]["reason"] == "md-class-unknown"


def test_a_task_naming_no_resolvable_manufacturer_is_left_alone(conn):
    """No BC codes means we cannot say whether the class is blank, so the tool
    declines rather than guessing -- the same refusal `resolve_canonicals` makes
    one layer up."""
    task_id = _seed_task(conn, _seed_doc(conn, "rr-nomfr"), "Nobody Ltd")

    assert r.plan(conn) == []
    assert _reason(conn, task_id) is None
