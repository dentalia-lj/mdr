"""Retracting the mfr-scope production links of an enumerating QA certificate.

The rows this repairs: doc 310 (Kiwa Cermet MED 31385) enumerates exactly
three device types in its own technical annex and was C16-machine-bound to
356 GC production items anyway. The part-(a) guard (`device-enumeration`)
stops new bindings of that shape; this tool retracts the ones already
written (Denis ruling 2026-08-24, "Guard + clean 310").

The properties that keep the repair honest: it only ever acts on a document
whose stored text the part-(a) detector itself says enumerates devices (the
tool and the guard cannot disagree — same function); it refuses a
human-made binding (the guard never barred those); retraction is a status
flip to `retracted` with `match_basis` kept, never a delete; every flipped
link leaves its own self-contained `audit_log` entry; and a second run
plans zero.
"""

from __future__ import annotations

import pytest

from app import repair_mfr_overbind
from tests.fixtures import qa_cert_texts as qa


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE document, item_document, evidence, manual_task, "
                      "audit_log, document_text, item_mirror RESTART IDENTITY CASCADE")

    wipe()
    yield
    wipe()


def _seed_bound_doc(conn, *, content_hash="e1" * 32, text=qa.KIWA_ENUMERATING,
                    text_source="pdf-text", bound_by="gate", n_links=3):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) "
        "VALUES ('EC','MDD','manufacturer',%s,'/archive/GC/cert.pdf','production') "
        "RETURNING doc_id",
        (content_hash,)).fetchone()["doc_id"]
    if text is not None:
        conn.execute(
            "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
            "VALUES (%s, %s, 'pymupdf', 3, %s, %s)",
            (content_hash, text_source, len(text), text))
    if bound_by is not None:
        conn.execute(
            "INSERT INTO audit_log (event, doc_id, decided_by, job_snapshot) "
            "VALUES ('bind-manufacturer', %s, %s, '{}'::jsonb)",
            (doc_id, bound_by))
    for i in range(n_links):
        item = f"IT-{i}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
            "catalogue, mirror_rev, updated_at) "
            "VALUES (%s,'n','008',TRUE,'LJ',1,now()) ON CONFLICT (item_ref) DO NOTHING",
            (item,))
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s, %s, 'mfr-scope', 'production')", (item, doc_id))
    return doc_id


def _link_states(conn, doc_id):
    return {(r["item_ref"], r["match_basis"], r["status"]) for r in conn.execute(
        "SELECT item_ref, match_basis, status FROM item_document WHERE doc_id=%s",
        (doc_id,)).fetchall()}


def test_apply_retracts_machine_bound_links_and_audits_each(test_db_url):
    from app import db

    with db.connect(test_db_url) as conn:
        doc_id = _seed_bound_doc(conn, n_links=3)
        retractions, report = repair_mfr_overbind.plan(conn, doc_id)
        assert report["blockers"] == []
        assert sorted(r["item_ref"] for r in retractions) == ["IT-0", "IT-1", "IT-2"]

        stats = repair_mfr_overbind.apply(conn, doc_id, retractions)
        assert stats == {"links_retracted": 3}

        # status flipped, basis kept (append-only: the row records HOW the
        # link was proposed even after it stops asserting coverage)
        assert _link_states(conn, doc_id) == {
            ("IT-0", "mfr-scope", "retracted"),
            ("IT-1", "mfr-scope", "retracted"),
            ("IT-2", "mfr-scope", "retracted"),
        }
        audits = conn.execute(
            "SELECT event, item_ref, decided_by, job_snapshot, detail FROM audit_log "
            "WHERE doc_id=%s AND event='link-retracted' ORDER BY item_ref",
            (doc_id,)).fetchall()
        assert [a["item_ref"] for a in audits] == ["IT-0", "IT-1", "IT-2"]
        for a in audits:
            assert a["decided_by"] == repair_mfr_overbind.DECIDED_BY
            assert a["job_snapshot"]["ruling"] == "Denis 2026-08-24"
            assert a["job_snapshot"]["reason"] == "device-enumeration"
            assert a["detail"]["link_status_before"] == "production"

        # the document row is deliberately untouched: the certificate is
        # real, archived and evidenced — it just no longer asserts coverage.
        assert conn.execute("SELECT status FROM document WHERE doc_id=%s",
                            (doc_id,)).fetchone()["status"] == "production"

        # re-run plans zero: retracted links stop being selected
        retractions2, report2 = repair_mfr_overbind.plan(conn, doc_id)
        assert retractions2 == [] and report2["blockers"] == []


def test_a_non_enumerating_cert_is_refused(test_db_url):
    # The Carl Martin shape: the part-(a) guard would still allow this
    # binding, so there is nothing for the cleanup to take back.
    from app import db

    with db.connect(test_db_url) as conn:
        doc_id = _seed_bound_doc(conn, text=qa.CARL_MARTIN_SCOPE_ONLY)
        retractions, report = repair_mfr_overbind.plan(conn, doc_id)
        assert retractions == []
        assert any("does not enumerate" in b["reason"] for b in report["blockers"])
        # and apply on an empty plan writes nothing
        assert repair_mfr_overbind.apply(conn, doc_id, retractions) == {"links_retracted": 0}
        assert all(s == "production" for _, _, s in _link_states(conn, doc_id))


def test_a_human_made_binding_is_refused(test_db_url):
    # gate.apply bind-manufacturer is a person's decision; the part-(a) guard
    # never barred those, so this tool must not undo one.
    from app import db

    with db.connect(test_db_url) as conn:
        doc_id = _seed_bound_doc(conn, bound_by="denis")
        retractions, report = repair_mfr_overbind.plan(conn, doc_id)
        assert retractions == []
        assert any("machine" in b["reason"] for b in report["blockers"])


def test_a_doc_with_no_bind_event_is_refused(test_db_url):
    # No bind-manufacturer audit event at all: nothing establishes who bound
    # it, and a repair must never guess (conservative refusal).
    from app import db

    with db.connect(test_db_url) as conn:
        doc_id = _seed_bound_doc(conn, bound_by=None)
        retractions, report = repair_mfr_overbind.plan(conn, doc_id)
        assert retractions == []
        assert any("machine" in b["reason"] for b in report["blockers"])


def test_missing_or_scanned_text_is_refused(test_db_url):
    from app import db

    with db.connect(test_db_url) as conn:
        no_text = _seed_bound_doc(conn, content_hash="a1" * 32, text=None)
        scanned = _seed_bound_doc(conn, content_hash="b2" * 32, text="",
                                  text_source="none")
        for doc_id in (no_text, scanned):
            retractions, report = repair_mfr_overbind.plan(conn, doc_id)
            assert retractions == []
            assert any("no stored text" in b["reason"] for b in report["blockers"])


def test_no_such_document_is_refused(test_db_url):
    from app import db

    with db.connect(test_db_url) as conn:
        retractions, report = repair_mfr_overbind.plan(conn, 424242)
        assert retractions == []
        assert any("no document" in b["reason"] for b in report["blockers"])


def test_only_mfr_scope_production_links_are_touched(test_db_url):
    # A REF-matched production link is evidence-backed coverage the guard
    # never questioned, and an already-retracted link must not re-audit.
    from app import db

    with db.connect(test_db_url) as conn:
        doc_id = _seed_bound_doc(conn, n_links=1)
        for item, basis, status in (("KEEP-REF", "ref-list", "production"),
                                    ("ALREADY", "mfr-scope", "retracted")):
            conn.execute(
                "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
                "catalogue, mirror_rev, updated_at) "
                "VALUES (%s,'n','008',TRUE,'LJ',1,now())", (item,))
            conn.execute(
                "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                "VALUES (%s, %s, %s, %s::link_status)", (item, doc_id, basis, status))

        retractions, report = repair_mfr_overbind.plan(conn, doc_id)
        assert report["blockers"] == []
        assert [r["item_ref"] for r in retractions] == ["IT-0"]
        # untouched links are counted and reported, never silent
        assert report["untouched_links"] == 2
        lines = "\n".join(repair_mfr_overbind.render(doc_id, retractions, report))
        assert "untouched" in lines and "2" in lines

        repair_mfr_overbind.apply(conn, doc_id, retractions)
        assert _link_states(conn, doc_id) == {
            ("IT-0", "mfr-scope", "retracted"),
            ("KEEP-REF", "ref-list", "production"),
            ("ALREADY", "mfr-scope", "retracted"),
        }
        # exactly one audit row — the pre-existing retracted link gained none
        n = conn.execute(
            "SELECT count(*) c FROM audit_log WHERE doc_id=%s AND event='link-retracted'",
            (doc_id,)).fetchone()["c"]
        assert n == 1
