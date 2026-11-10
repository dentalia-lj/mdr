"""Recent uploads on /upload: what became of each file. Read-only, and worked
out on every view from the job chain and the registry, so it cannot drift."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.test_web import API_TEST_URL
from web.uploads import recent_uploads

_HASH = "a" * 64


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))))


from web.app import create_app  # noqa: E402


def _job(conn, type_, *, status="done", payload=None, result=None, caused_by=None,
         key=None, created_at=None):
    n = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result, caused_by, created_at) "
        "VALUES (%s::job_type, %s, %s, %s::job_status, %s, %s, coalesce(%s::timestamptz, now())) "
        "RETURNING id",
        (type_, Json(payload or {}), key or f"k{n}", status,
         Json(result) if result is not None else None, caused_by, created_at),
    ).fetchone()["id"]


def _upload(conn, *, name="a.pdf", status="done", result=None, group_id=None,
            created_at=None, key=None):
    payload = {"upload_id": 1, "filename": name}
    if group_id is not None:
        payload["group_id"] = group_id
    if result is None and status == "done":
        result = {"outcome": "archived", "content_hash": _HASH}
    return _job(conn, "upload.ingest", status=status, payload=payload, result=result,
                created_at=created_at, key=key)


def _doc(conn, status, content_hash=_HASH):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','group',%s,'/a.pdf',%s::doc_status) RETURNING doc_id",
        (content_hash, status),
    ).fetchone()["doc_id"]


def _only(conn):
    rows = recent_uploads(conn)
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("status,state,open_", [
    ("pending", "Being read", True),
    ("running", "Being read", True),
    ("failed", "Being read", True),       # still retrying
    ("dead", "Failed", False),
])
def test_the_upload_job_itself(conn, status, state, open_):
    _upload(conn, status=status)
    row = _only(conn)
    assert (row["state"], row["open"]) == (state, open_)


@pytest.mark.parametrize("status,state,link", [
    ("staged", "Waiting for review", "/staging?doc={}"),
    ("production", "Published", "/documents/{}"),
    ("filed", "On file", "/documents/{}"),
    ("superseded", "Replaced", "/documents/{}"),
    ("rejected", "Rejected", "/documents/{}"),
])
def test_a_document_decides_the_row(conn, status, state, link):
    _upload(conn)
    doc_id = _doc(conn, status)
    row = _only(conn)
    assert row["state"] == state
    assert row["href"] == link.format(doc_id)
    assert row["open"] is False


def _member(conn, group_id, item_ref):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'n','ACME','LJ',1,now()) ON CONFLICT DO NOTHING", (item_ref,))
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer, label) "
                 "VALUES (%s,'ACME','FUJI') ON CONFLICT DO NOTHING", (group_id,))
    conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                 "VALUES (%s,%s,'manual')", (group_id, item_ref))


def test_a_published_document_with_this_items_link_proposed_waits_for_review(conn):
    _member(conn, 7, "I-1")
    _upload(conn, group_id=7)
    doc_id = _doc(conn, "production")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('I-1',%s,'fetch-context','staged')", (doc_id,))
    row = _only(conn)
    assert row["state"] == "Waiting for review"
    assert row["href"] == "/staging#staged-links"
    assert row["item"] == "FUJI"


@pytest.mark.parametrize("doc_class", ["not-a-compliance-document", "business-doc", "msds"])
def test_not_a_compliance_document(conn, doc_class):
    up = _upload(conn)
    ex = _job(conn, "extract.doc", caused_by=up, result={"doc_class": doc_class})
    if doc_class != "not-a-compliance-document":
        _job(conn, "validate.doc", caused_by=ex,
             result={"emitted": False, "reason": "incomplete-evidence"})
    row = _only(conn)
    assert (row["state"], row["detail"]) == ("Nothing filed", "Not a compliance document")
    assert row["open"] is False


def test_could_not_read_enough(conn):
    up = _upload(conn)
    ex = _job(conn, "extract.doc", caused_by=up, result={"doc_class": "DoC"})
    _job(conn, "validate.doc", caused_by=ex,
         result={"emitted": False, "reason": "incomplete-evidence"})
    row = _only(conn)
    assert (row["state"], row["detail"]) == ("Nothing filed", "Could not read enough")


def test_a_later_stage_still_running_is_being_read(conn):
    up = _upload(conn)
    _job(conn, "extract.doc", caused_by=up, status="pending")
    row = _only(conn)
    assert (row["state"], row["open"]) == ("Being read", True)


def test_a_document_with_a_stage_still_running_keeps_polling(conn):
    up = _upload(conn)
    _doc(conn, "staged")
    _job(conn, "validate.doc", caused_by=up, status="pending")
    row = _only(conn)
    assert (row["state"], row["open"]) == ("Waiting for review", True)


def test_a_dead_later_stage_is_failed(conn):
    up = _upload(conn)
    _job(conn, "extract.doc", caused_by=up, status="dead")
    row = _only(conn)
    assert (row["state"], row["open"]) == ("Failed", False)


def test_a_dead_stage_that_was_rerun_follows_the_rerun(conn):
    # A rerun is a new row with no caused_by, sharing the dead one's key.
    up = _upload(conn)
    _job(conn, "extract.doc", caused_by=up, status="dead", key="extract:x")
    rerun = _job(conn, "extract.doc", key="extract:x", result={"doc_class": "DoC"})
    _job(conn, "validate.doc", caused_by=rerun,
         result={"emitted": False, "reason": "incomplete-evidence"})
    assert _only(conn)["detail"] == "Could not read enough"


def test_a_rerun_still_going_is_being_read(conn):
    up = _upload(conn)
    _job(conn, "extract.doc", caused_by=up, status="dead", key="extract:y")
    _job(conn, "extract.doc", key="extract:y", status="pending")
    assert _only(conn)["state"] == "Being read"


def test_a_rerun_of_the_upload_job_is_one_row(conn):
    _upload(conn, status="dead", key="upload:9")
    _upload(conn, key="upload:9")
    _doc(conn, "staged")
    assert _only(conn)["state"] == "Waiting for review"


def test_no_trail_is_being_read_until_a_day_has_passed(conn):
    _upload(conn)
    row = _only(conn)
    assert (row["state"], row["open"]) == ("Being read", True)


def test_no_trail_after_a_day_says_so(conn):
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    _upload(conn, created_at=old)
    row = _only(conn)
    assert (row["state"], row["detail"]) == ("No result", "Ask a developer")
    assert row["open"] is False


def test_a_file_seen_before_with_no_document(conn):
    _upload(conn, result={"outcome": "duplicate", "content_hash": _HASH})
    row = _only(conn)
    assert (row["state"], row["detail"]) == ("Nothing filed", "Read before, no document came of it")
    assert row["open"] is False


def test_newest_first_and_a_batch_fits(conn):
    for i in range(101):
        _upload(conn, name=f"f{i}.pdf", status="pending")
    rows = recent_uploads(conn)
    assert len(rows) == 100
    assert rows[0]["filename"] == "f100.pdf"


def test_uploads_from_before_this_list_are_left_out(conn):
    _job(conn, "upload.ingest", payload={"upload_id": 1}, result={"outcome": "archived"})
    assert recent_uploads(conn) == []


@pytest.mark.parametrize("doc_status,tone", [
    ("staged", "staged"), ("production", "production"), ("filed", "filed"),
    ("superseded", "superseded"), ("rejected", "rejected"),
])
def test_a_documents_colour_is_its_status_colour(conn, doc_status, tone):
    _upload(conn)
    _doc(conn, doc_status)
    assert _only(conn)["tone"] == tone


@pytest.mark.parametrize("status,tone", [("pending", "running"), ("dead", "dead")])
def test_a_jobs_colour(conn, status, tone):
    _upload(conn, status=status)
    assert _only(conn)["tone"] == tone


def test_nothing_filed_is_red(conn):
    up = _upload(conn)
    _job(conn, "extract.doc", caused_by=up, result={"doc_class": "msds"})
    assert _only(conn)["tone"] == "rejected"


def test_the_list_draws_a_coloured_label(client, conn):
    _upload(conn)
    _doc(conn, "staged")
    conn.commit()
    assert '<span class="badge badge-staged">Waiting for review</span>' in client.get("/upload/recent").text


def test_only_being_read_has_the_spinner(client, conn):
    _upload(conn, name="moving.pdf", status="pending")
    conn.commit()
    assert 'class="upload-spinner"' in client.get("/upload/recent").text
    conn.execute("UPDATE job SET status='dead'")
    conn.commit()
    assert 'class="upload-spinner"' not in client.get("/upload/recent").text


def test_upload_page_shows_the_list(client, conn):
    _upload(conn, name="cert_one.pdf", status="pending")
    conn.commit()
    page = client.get("/upload").text
    assert "Recent uploads" in page and "cert_one.pdf" in page


def test_the_list_polls_while_something_is_open(client, conn):
    _upload(conn, status="pending")
    conn.commit()
    assert "every 5s" in client.get("/upload/recent").text


def test_the_list_stops_polling_when_all_settled(client, conn):
    _upload(conn)
    _doc(conn, "staged")
    conn.commit()
    assert "every 5s" not in client.get("/upload/recent").text


def test_a_hostile_file_name_is_escaped(client, conn):
    _upload(conn, name="<img src=x onerror=alert(1)>.pdf", status="pending")
    conn.commit()
    text = client.get("/upload/recent").text
    assert "<img src=x" not in text
