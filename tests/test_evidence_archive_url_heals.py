"""`evidence.archive_url` must self-heal, exactly as `document.archive_url` does.

The GC pilot wrote 130 documents and 731 evidence rows whose `archive_url` was
the corpus SOURCE path (`/imports/dentalia-sftp/GC/...`), because GATE had no
payload value and fell back to `fetch_log.url_normalized`. Once the payload
carried the archived handle, replaying VALIDATE repaired every document --
`_upsert_document` already does `archive_url = EXCLUDED.archive_url` -- but not
one evidence row, because `_insert_evidence`'s NOT EXISTS guard on
(doc_id, field, extract_rev) means an existing row is skipped entirely.

That left the registry half-correct: documents pointing at the archive, their
evidence still pointing at paths the next corpus re-dump destroys. Invariant 2
requires evidence to carry a usable `archive_url`, so the pointer has to heal.

Deliberately narrow. `archive_url` is a STORAGE HANDLE, not evidence content --
the same distinction `docs/runbook.md` draws when it says never to render that
column as a link. Only the handle is corrected. `value`, `verbatim`, `page`,
`tier`, `model_id` and `confidence` are what the model actually read and must
never be rewritten by a re-delivery: that would be falsifying evidence, not
repairing a pointer. A genuinely new reading arrives as a NEW extract_rev with
its own row, which is what the append-only design is for.
"""

from __future__ import annotations

from app.handlers import gate as gate_mod


def _fields():
    return {
        "type": {"value": "DoC", "conf": 1.0, "tier": "T0",
                 "verbatim": "DECLARATION OF CONFORMITY", "page": None},
    }


def _doc(conn, content_hash="hash-heal"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group',%s,%s,'staged') "
        "RETURNING doc_id",
        (content_hash, "/archive/GC/doc/abc__x.pdf"),
    ).fetchone()["doc_id"]


def _evidence(conn, doc_id):
    return conn.execute(
        "SELECT field, archive_url, value, verbatim, tier, confidence "
        "FROM evidence WHERE doc_id=%s ORDER BY field", (doc_id,)
    ).fetchall()


def test_existing_evidence_archive_url_is_corrected(conn):
    doc_id = _doc(conn)
    gate_mod._insert_evidence(conn, doc_id, 1, _fields(),
                              "/imports/dentalia-sftp/GC/DOC/x.pdf")
    assert _evidence(conn, doc_id)[0]["archive_url"].startswith("/imports/")

    # Re-delivery of the SAME revision, now carrying the archived handle.
    gate_mod._insert_evidence(conn, doc_id, 1, _fields(),
                              "/archive/GC/doc/abc__x.pdf")

    rows = _evidence(conn, doc_id)
    assert len(rows) == 1, "must correct in place, never duplicate the row"
    assert rows[0]["archive_url"] == "/archive/GC/doc/abc__x.pdf"


def test_healing_does_not_rewrite_what_the_model_read(conn):
    # The pointer heals; the reading does not. A re-delivery claiming a
    # different verbatim/confidence must not overwrite the recorded evidence.
    doc_id = _doc(conn, "hash-immutable")
    gate_mod._insert_evidence(conn, doc_id, 1, _fields(),
                              "/imports/dentalia-sftp/GC/DOC/x.pdf")

    tampered = {
        "type": {"value": "EC", "conf": 0.1, "tier": "T2",
                 "verbatim": "SOMETHING ELSE ENTIRELY", "page": 9},
    }
    gate_mod._insert_evidence(conn, doc_id, 1, tampered,
                              "/archive/GC/doc/abc__x.pdf")

    row = _evidence(conn, doc_id)[0]
    assert row["archive_url"] == "/archive/GC/doc/abc__x.pdf"   # handle healed
    assert row["value"] == "DoC"                                # reading intact
    assert row["verbatim"] == "DECLARATION OF CONFORMITY"
    assert row["tier"] == "T0"
    assert float(row["confidence"]) == 1.0


def test_a_new_extract_rev_still_gets_its_own_row(conn):
    # The append-only property must survive: a genuine re-extraction is a new
    # revision with its own evidence, not an update of the old one.
    doc_id = _doc(conn, "hash-rev")
    gate_mod._insert_evidence(conn, doc_id, 1, _fields(), "/archive/a.pdf")
    gate_mod._insert_evidence(conn, doc_id, 2, _fields(), "/archive/a.pdf")

    revs = conn.execute(
        "SELECT extract_rev FROM evidence WHERE doc_id=%s ORDER BY extract_rev",
        (doc_id,),
    ).fetchall()
    assert [r["extract_rev"] for r in revs] == [1, 2]
