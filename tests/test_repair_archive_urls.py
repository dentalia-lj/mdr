"""Re-homing corpus-path archive handles into the hash-addressed archive.

The rows this repairs: backfill runs before the archive fix stored the
`/imports` corpus path as `document.archive_url` (and on the document's
evidence rows), a handle nothing serves and end-state goal #2 forbids. The
properties that keep the repair honest: a file is re-pointed only when its
bytes still hash to the stored `content_hash`; evidence rows carrying an
UNRELATED handle are untouched; every write leaves a self-contained
`audit_log` entry; and a second run plans zero (applied rows fall under the
archive prefix and stop being selected).
"""

from __future__ import annotations

import hashlib

import pytest

from app import repair_archive_urls
from app.adapters.storage import LocalFsStore


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE document, item_document, evidence, manual_task, "
                      "audit_log RESTART IDENTITY CASCADE")

    wipe()
    yield
    wipe()


def _seed_doc(conn, *, content_hash, archive_url, status="production"):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group',%s,%s,%s) RETURNING doc_id",
        (content_hash, archive_url, status)).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, tier, "
        "confidence, extracted_at) "
        "VALUES (%s,'type','DoC',%s,'Declaration of Conformity','T0',1.0,now())",
        (doc_id, archive_url))
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, tier, "
        "confidence, extracted_at) "
        "VALUES (%s,'manufacturer','VOCO',%s,'VOCO GmbH','T0',0.97,now())",
        (doc_id, "/archive/unrelated/other.pdf"))
    return doc_id


def _corpus_file(tmp_path, rel, body: bytes):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return p, hashlib.sha256(body).hexdigest()


def _resolver(tmp_path):
    def resolve(url, content_hash=""):
        p = tmp_path / url.lstrip("/")
        return p if p.is_file() else None
    return resolve


def test_apply_repoints_document_matching_evidence_and_audits(test_db_url, tmp_path):
    from app import db

    body = b"%PDF-1.4 fake declaration"
    _, h = _corpus_file(tmp_path, "imports/GC/doc.pdf", body)
    store = LocalFsStore(str(tmp_path / "archive"), "/archive")
    with db.connect(test_db_url) as conn:
        doc_id = _seed_doc(conn, content_hash=h, archive_url="/imports/GC/doc.pdf")
        repairs, skipped = repair_archive_urls.plan(conn, resolver=_resolver(tmp_path))
        assert [r["doc_id"] for r in repairs] == [doc_id] and skipped == []
        counts = repair_archive_urls.apply(conn, store, repairs)
        assert counts == {"documents": 1, "evidence_rows": 1}

        new_url = conn.execute(
            "SELECT archive_url FROM document WHERE doc_id=%s", (doc_id,)
        ).fetchone()["archive_url"]
        assert new_url.startswith("/archive/") and h[:12] in new_url
        # the archived copy holds the same bytes, hash-addressed under the store
        assert (tmp_path / "archive" / new_url.removeprefix("/archive/")).read_bytes() == body

        evs = conn.execute(
            "SELECT field, archive_url FROM evidence WHERE doc_id=%s ORDER BY field",
            (doc_id,)).fetchall()
        assert {e["field"]: e["archive_url"] for e in evs} == {
            "manufacturer": "/archive/unrelated/other.pdf",  # untouched
            "type": new_url,                                  # re-pointed
        }
        audit = conn.execute(
            "SELECT event, decided_by, job_snapshot FROM audit_log WHERE doc_id=%s",
            (doc_id,)).fetchone()
        assert audit["event"] == "archive-url-repair"
        assert audit["decided_by"] == repair_archive_urls.DECIDED_BY
        assert audit["job_snapshot"]["old"] == "/imports/GC/doc.pdf"
        assert audit["job_snapshot"]["new"] == new_url

        # re-run plans empty: the repaired row now sits under the prefix
        repairs2, skipped2 = repair_archive_urls.plan(conn, resolver=_resolver(tmp_path))
        assert repairs2 == [] and skipped2 == []


def test_hash_mismatch_is_skipped_and_never_repointed(test_db_url, tmp_path):
    from app import db

    _, _h_real = _corpus_file(tmp_path, "imports/X/tampered.pdf", b"current bytes")
    with db.connect(test_db_url) as conn:
        doc_id = _seed_doc(conn, content_hash="0" * 64,
                           archive_url="/imports/X/tampered.pdf")
        repairs, skipped = repair_archive_urls.plan(conn, resolver=_resolver(tmp_path))
        assert repairs == []
        assert [s["doc_id"] for s in skipped] == [doc_id]
        assert "mismatch" in skipped[0]["reason"]
        untouched = conn.execute(
            "SELECT archive_url FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
        assert untouched["archive_url"] == "/imports/X/tampered.pdf"


def test_missing_file_is_skipped_loudly(test_db_url, tmp_path):
    from app import db

    with db.connect(test_db_url) as conn:
        _seed_doc(conn, content_hash="1" * 64, archive_url="/imports/gone/nope.pdf")
        repairs, skipped = repair_archive_urls.plan(conn, resolver=_resolver(tmp_path))
        assert repairs == [] and len(skipped) == 1
        assert "missing" in skipped[0]["reason"]
        assert any("SKIPPED" in line
                   for line in repair_archive_urls.render(repairs, skipped))


def test_archive_prefixed_rows_are_never_selected(test_db_url, tmp_path):
    from app import db

    with db.connect(test_db_url) as conn:
        _seed_doc(conn, content_hash="2" * 64, archive_url="/archive/ok/fine.pdf")
        repairs, skipped = repair_archive_urls.plan(conn, resolver=_resolver(tmp_path))
        assert repairs == [] and skipped == []


# --------------------------------------------------------------------------- #
# The C3 clobber shape: archive_url is the remote SOURCE url, the fetched copy
# sits in the store under FETCH's `{hash12}__name` layout (doc 843,
# [c3-validate-payload-clobbers-archive-url]).
# --------------------------------------------------------------------------- #
def _store_resolver(store_root):
    import functools
    return functools.partial(repair_archive_urls.default_resolver,
                             store_root=store_root)


def test_a_remote_url_row_is_rehomed_from_the_stored_copy(test_db_url, tmp_path):
    from app import db

    body = b"%PDF-1.4 fetched declaration"
    digest = hashlib.sha256(body).hexdigest()
    stored = tmp_path / "RENFERT" / "unknown" / f"{digest[:12]}__CONF.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(body)

    with db.connect(test_db_url) as conn:
        doc_id = _seed_doc(conn, content_hash=digest,
                           archive_url="https://www.example.com/file/CONF.pdf")
        repairs, skipped = repair_archive_urls.plan(
            conn, resolver=_store_resolver(tmp_path))
        assert skipped == [] and len(repairs) == 1

        store = LocalFsStore(str(tmp_path), "/archive")
        repair_archive_urls.apply(conn, store, repairs)
        row = conn.execute("SELECT archive_url FROM document WHERE doc_id=%s",
                           (doc_id,)).fetchone()
        assert row["archive_url"].startswith("/archive/")
        # the matching evidence row moved with it; the unrelated one did not
        urls = {r["archive_url"] for r in conn.execute(
            "SELECT archive_url FROM evidence WHERE doc_id=%s", (doc_id,)).fetchall()}
        assert "https://www.example.com/file/CONF.pdf" not in urls
        assert "/archive/unrelated/other.pdf" in urls


def test_a_remote_url_row_with_no_stored_copy_is_skipped(test_db_url, tmp_path):
    from app import db

    with db.connect(test_db_url) as conn:
        _seed_doc(conn, content_hash="3" * 64,
                  archive_url="https://www.example.com/file/GONE.pdf")
        repairs, skipped = repair_archive_urls.plan(
            conn, resolver=_store_resolver(tmp_path))
        assert repairs == [] and len(skipped) == 1
        assert "missing" in skipped[0]["reason"]


def test_an_ambiguous_hash_prefix_match_is_skipped_not_guessed(test_db_url, tmp_path):
    from app import db

    body = b"%PDF-1.4 fetched declaration"
    digest = hashlib.sha256(body).hexdigest()
    for sub in ("a", "b"):
        p = tmp_path / sub / f"{digest[:12]}__CONF.pdf"
        p.parent.mkdir(parents=True)
        p.write_bytes(body)

    with db.connect(test_db_url) as conn:
        _seed_doc(conn, content_hash=digest,
                  archive_url="https://www.example.com/file/CONF.pdf")
        repairs, skipped = repair_archive_urls.plan(
            conn, resolver=_store_resolver(tmp_path))
        assert repairs == [] and len(skipped) == 1
