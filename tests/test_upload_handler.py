"""upload.ingest — fetch.url minus the HTTP fetch. Bytes arrive in an
upload_inbox row; archive + hash + emit extract.doc (new) / validate.doc (seen)
exactly like FETCH's tail, then delete the spool row.
"""
from __future__ import annotations

from app.handlers import upload as uh


class FakeStore:
    def __init__(self):
        self.puts = []

    def put(self, body: bytes, path: str) -> str:
        self.puts.append((body, path))
        return f"file:///archive/{path}"


def _insert_upload(conn, content: bytes, *, filename="cert.pdf", group_id=None, catalogue="LJ"):
    return conn.execute(
        "INSERT INTO upload_inbox (filename, content, target_group_id, catalogue, uploaded_by) "
        "VALUES (%s,%s,%s,%s,'admin') RETURNING id",
        (filename, content, group_id, catalogue),
    ).fetchone()["id"]


def _run(conn, upload_id, store=None):
    return uh.handle_upload_ingest(
        conn, {"payload": {"upload_id": upload_id}}, store=store or FakeStore())


def _seed_extracted(conn, content_hash, rev=1):
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES (%s, 'T0', '{}'::jsonb, %s)", (content_hash, rev))


def test_new_content_archives_and_emits_extract(conn):
    uid = _insert_upload(conn, b"%PDF-NEW")
    store = FakeStore()
    res = _run(conn, uid, store=store)

    assert res["outcome"] == "archived"
    assert res["emitted"] == "extract.doc"
    assert len(store.puts) == 1
    job = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='extract.doc'").fetchone()
    assert job["payload"]["content_hash"] == res["content_hash"]
    assert job["payload"]["group_id"] is None
    assert job["payload"]["source_url"] == "upload:cert.pdf"
    assert job["dedupe_key"] == f"extract:{res['content_hash']}"
    assert conn.execute("SELECT count(*) c FROM fetch_log WHERE source='upload'").fetchone()["c"] == 1
    assert conn.execute("SELECT count(*) c FROM upload_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0


def test_seen_content_with_group_emits_validate_not_extract(conn):
    import hashlib
    body = b"%PDF-SEEN"
    h = hashlib.sha256(body).hexdigest()
    _seed_extracted(conn, h, rev=2)
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (5, 'ACME')")
    uid = _insert_upload(conn, body, group_id=5)

    res = _run(conn, uid)

    assert res["outcome"] == "linked"
    assert res["emitted"] == "validate.doc"
    v = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='validate.doc'").fetchone()
    assert v["payload"] == {"content_hash": h, "group_id": 5, "extract_rev": 2}
    assert v["dedupe_key"] == f"validate:{h}:2:5"
    assert conn.execute("SELECT count(*) c FROM job WHERE type='extract.doc'").fetchone()["c"] == 0


def test_seen_content_without_group_skips(conn):
    import hashlib
    body = b"%PDF-SEEN2"
    h = hashlib.sha256(body).hexdigest()
    _seed_extracted(conn, h)
    uid = _insert_upload(conn, body, group_id=None)

    res = _run(conn, uid)

    assert res["outcome"] == "duplicate"
    assert conn.execute("SELECT count(*) c FROM job WHERE type IN ('extract.doc','validate.doc')").fetchone()["c"] == 0


def test_hash_in_ledger_but_never_extracted_still_emits_extract(conn):
    import hashlib
    body = b"%PDF-ARCHIVED-NOT-EXTRACTED"
    h = hashlib.sha256(body).hexdigest()
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES (%s,%s,'upload', now(), now())", (f"upload:{h}", h))
    uid = _insert_upload(conn, body, group_id=None)

    res = _run(conn, uid)

    assert res["outcome"] == "archived"
    assert conn.execute("SELECT count(*) c FROM job WHERE type='extract.doc'").fetchone()["c"] == 1


def test_missing_row_is_idempotent_noop(conn):
    res = uh.handle_upload_ingest(conn, {"payload": {"upload_id": 999999}}, store=FakeStore())
    assert res["outcome"] == "already-processed"


def test_upload_resolves_originating_dead_end_task(conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (12, 'ACME')")
    tid = conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status) "
        "VALUES ('discovery-dead-end', 12, '{}'::jsonb, 'open') RETURNING id"
    ).fetchone()["id"]
    uid = _insert_upload(conn, b"%PDF-DEADEND", group_id=12)

    uh.handle_upload_ingest(
        conn, {"payload": {"upload_id": uid, "manual_task_id": tid}}, store=FakeStore())

    row = conn.execute(
        "SELECT status, resolved_by FROM manual_task WHERE id=%s", (tid,)).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_by"] == "upload"


def test_upload_manual_task_resolve_guard_ignores_already_resolved(conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (13, 'ACME')")
    # an already-resolved dead-end task must not be re-touched (idempotent guard)
    done = conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status, resolved_by) "
        "VALUES ('discovery-dead-end', 13, '{}'::jsonb, 'resolved', 'discover') RETURNING id"
    ).fetchone()["id"]
    uid = _insert_upload(conn, b"%PDF-GUARD", group_id=13)

    uh.handle_upload_ingest(
        conn, {"payload": {"upload_id": uid, "manual_task_id": done}}, store=FakeStore())

    row = conn.execute(
        "SELECT resolved_by FROM manual_task WHERE id=%s", (done,)).fetchone()
    assert row["resolved_by"] == "discover"     # untouched — guard held


# --- the archive gate (`[gate-covers-only-fetch]`, 2026-09-02) --------------
#
# The web form checks the bytes first, so these are the backstop for anything
# that reaches upload_inbox by another path. The shape of the bug they guard:
# a saved web page named `Declaration_of_Conformity.pdf` was archived, opened
# by PyMuPDF (which paginates HTML), typed `IFU` at confidence 1.00 off a menu
# item, and approved into production by a human who saw plausible metadata.

def test_html_named_as_a_pdf_is_refused_not_archived(conn):
    uid = _insert_upload(conn, b"<!doctype html><html>bredent.com</html>",
                         filename="Declaration_of_Conformity.pdf")
    store = FakeStore()
    res = _run(conn, uid, store=store)

    assert res["outcome"] == "not-a-document"
    assert res["magic"] == "<!doc"
    assert store.puts == [], "a non-document must never reach the archive"


def test_refused_upload_leaves_no_ledger_row(conn):
    # A refused upload is not "seen content": a fetch_log row for it would point
    # at nothing, and `upload:<hash>` is not re-fetchable anyway.
    body = b"<html>not a pdf</html>"
    uid = _insert_upload(conn, body)
    _run(conn, uid)

    import hashlib
    h = hashlib.sha256(body).hexdigest()
    assert conn.execute("SELECT count(*) AS n FROM fetch_log WHERE url_normalized=%s",
                        (f"upload:{h}",)).fetchone()["n"] == 0


def test_refused_upload_still_clears_the_spool_row(conn):
    # Or the same doomed upload is retried on every scan.
    uid = _insert_upload(conn, b"<html>x</html>")
    _run(conn, uid)
    assert conn.execute("SELECT count(*) AS n FROM upload_inbox WHERE id=%s",
                        (uid,)).fetchone()["n"] == 0


def test_refused_upload_leaves_the_manual_task_open(conn):
    # Nobody supplied the document the task is waiting for, so it is not
    # resolved -- the opposite of test_upload_resolves_originating_dead_end_task.
    tid = conn.execute(
        "INSERT INTO manual_task (kind, status, payload) "
        "VALUES ('discovery-dead-end','open','{}'::jsonb) RETURNING id"
    ).fetchone()["id"]
    uid = _insert_upload(conn, b"<html>x</html>")
    uh.handle_upload_ingest(
        conn, {"payload": {"upload_id": uid, "manual_task_id": tid}}, store=FakeStore())

    assert conn.execute("SELECT status FROM manual_task WHERE id=%s",
                        (tid,)).fetchone()["status"] == "open"


def test_an_empty_upload_is_refused(conn):
    uid = _insert_upload(conn, b"")
    store = FakeStore()
    assert _run(conn, uid, store=store)["outcome"] == "not-a-document"
    assert store.puts == []


def test_office_formats_still_archive(conn):
    # The corpus carries .xlsx/.docx coverage-map indexes; refusing them would
    # be a new regression, not a fix.
    for body in (b"PK\x03\x04sheet", b"\xd0\xcf\x11\xe0legacy"):
        uid = _insert_upload(conn, body)
        assert _run(conn, uid)["outcome"] == "archived"
