"""`reextract` (app/reextract.py): selectors, exclusions, dry run, apply, sync only."""
from __future__ import annotations

import uuid

import pytest
from psycopg.types.json import Json

from app import cli, reextract
from app.extract import batching
from app.extract.tiers import NOT_A_DOCUMENT, write_extraction_attempt
from app.handlers import extract as extract_handler


def _hash():
    return uuid.uuid4().hex


def _cert(tier, value):
    return {"type": {"value": "DoC", "conf": 0.9, "tier": "T1", "verbatim": "x", "page": 1},
            "cert_number": {"value": value, "conf": 0.9, "tier": tier, "verbatim": "x", "page": 1}}


def _extract_job(conn, h, *, status="done", result=None, payload=None):
    payload = payload or {"content_hash": h, "archive_url": f"/archive/X/{h}.pdf",
                          "group_id": 7, "source_url": "u",
                          "companion_archive_url": f"/archive/X/{h}_RA.pdf"}
    terminal = status in ("done", "dead")
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('extract.doc', %s, %s, %s::job_status, %s)",
        (Json(payload), f"extract:{h}:{uuid.uuid4().hex}" if terminal else f"extract:{h}",
         status, Json(result) if result is not None else None))
    return payload


def _audit(conn, did, event, decided_by):
    conn.execute("INSERT INTO audit_log (event, doc_id, decided_by, job_snapshot) "
                 "VALUES (%s, %s, %s, '{}')", (event, did, decided_by))


def _doc(conn, *, tier="T1", cert=None, status="staged", doc_type="DoC", job=True,
         t3=False, reopened=False, job_status="done", archive_url=None, audit=()):
    h = _hash()
    write_extraction_attempt(conn, h, ["T0", "T1"], _cert(tier, cert))
    did = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status) VALUES (%s,'MDR','group',%s,%s,%s) RETURNING doc_id",
        (doc_type, h, archive_url or f"/archive/X/{h}.pdf", status)).fetchone()["doc_id"]
    if job:
        _extract_job(conn, h, status=job_status)
    if t3:
        conn.execute(
            "INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, tier, "
            "model_id, confidence, extracted_at) VALUES (%s,'cert_number','1',%s,'x','T3','reviewer',1,now())",
            (did, f"/archive/X/{h}.pdf"))
    if reopened:
        _audit(conn, did, "reopen", "u")
    for event, actor in audit:
        _audit(conn, did, event, actor)
    return h


def _scan(conn, *, doc_class=NOT_A_DOCUMENT, source="none", document=False, job=True):
    h = _hash()
    conn.execute("INSERT INTO document_text (content_hash, source) VALUES (%s,%s)", (h, source))
    if job:
        _extract_job(conn, h, result={"doc_class": doc_class})
    if document:
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
            "status) VALUES ('DoC','MDR','group',%s,'/a','staged')", (h,))
    return h


def _sent(p):
    return {r["content_hash"] for r in p.send}


# --- selector (a): DoCs with a null T1 certificate number ---------------------------

def test_cert_null_selects_only_t1_null_doc_certs(conn):
    hit = _doc(conn)
    _doc(conn, cert="CE 123")                    # has a value
    _doc(conn, tier="T0")                        # null, but T0 said so
    _doc(conn, doc_type="IFU")                   # not a DoC
    p = reextract.plan(conn, doc_cert_null=True)
    assert _sent(p) == {hit}
    assert (p.selections[0].found, p.selections[0].eligible) == (1, 1)


def test_cert_null_reads_the_latest_attempt_only(conn):
    h = _doc(conn)
    write_extraction_attempt(conn, h, ["T1"], _cert("T1", "CE 9"), extract_rev=2)
    assert reextract.plan(conn, doc_cert_null=True).selections[0].found == 0


def test_cert_null_selects_a_value_lost_by_a_later_rev(conn):
    h = _doc(conn, cert="CE 9")
    write_extraction_attempt(conn, h, ["T1"], _cert("T1", None), extract_rev=2)
    assert _sent(reextract.plan(conn, doc_cert_null=True)) == {h}


@pytest.mark.parametrize("first, second, selected", [
    (None, "CE 9", False),                       # the later row of rev 1 has a value
    ("CE 9", None, True),                        # the later row of rev 1 is null
])
def test_cert_null_breaks_an_extract_rev_tie_by_id(conn, first, second, selected):
    h = _doc(conn, cert=first)
    write_extraction_attempt(conn, h, ["T1"], _cert("T1", second), extract_rev=1)
    assert (h in _sent(reextract.plan(conn, doc_cert_null=True))) is selected


# --- selector (b): scans dropped before the gate -----------------------------------

def test_dropped_scans_selects_refused_scans_without_a_document(conn):
    hit = _scan(conn)
    _scan(conn, doc_class="doc-of-value")        # was not refused
    _scan(conn, source="pdf-text")               # has a text layer
    _scan(conn, document=True)                   # became a document
    p = reextract.plan(conn, dropped_scans=True)
    assert _sent(p) == {hit}


def test_dropped_scans_uses_the_latest_extract_result(conn):
    h = _scan(conn)
    _extract_job(conn, h, result={"doc_class": "doc-of-value"})
    assert reextract.plan(conn, dropped_scans=True).selections[0].found == 0


@pytest.mark.parametrize("status", ["pending", "running", "dead"])
def test_a_scan_whose_latest_job_has_no_result_is_not_found(conn, status):
    h = _scan(conn)
    _extract_job(conn, h, status=status)
    assert reextract.plan(conn, dropped_scans=True).selections[0].found == 0


def test_a_dropped_scan_keeps_its_job_archive_url(conn):
    h = _scan(conn)
    p = reextract.plan(conn, dropped_scans=True)
    assert p.send[0]["payload"]["archive_url"] == f"/archive/X/{h}.pdf" and p.rehomed == 0


# --- selector (c): explicit hashes --------------------------------------------------

def test_hashes_selector_takes_the_list_and_dedupes_it(conn):
    a, b = _doc(conn, cert="x"), _doc(conn, cert="y")
    p = reextract.plan(conn, hashes=[a, b, a])
    assert _sent(p) == {a, b} and p.selections[0].found == 2


def test_a_hash_chosen_by_two_selectors_is_sent_once(conn):
    h = _doc(conn)
    p = reextract.plan(conn, doc_cert_null=True, hashes=[h])
    assert [r["selectors"] for r in p.send] == [["doc-cert-null", "hashes"]]
    assert p.selections[1].left_alone["repeat"] == 1 and p.selections[1].eligible == 0


# --- exclusions, each counted by reason --------------------------------------------

@pytest.mark.parametrize("reason, kwargs", [
    ("no_extract_job", {"job": False}),
    ("active", {"job_status": "pending"}),
    ("active", {"job_status": "running"}),
    ("active", {"job_status": "failed"}),
    ("rejected", {"status": "rejected"}),
    ("superseded", {"status": "superseded"}),
    ("edited", {"t3": True}),
    ("reopened", {"reopened": True}),
    ("published", {"status": "production"}),
    ("approved", {"status": "production", "audit": [("approve", "user:admin")]}),
    ("approved", {"status": "production", "audit": [("bind-manufacturer", "marija")]}),
    ("approved", {"status": "production", "audit": [("production-write", "user:admin")]}),
])
def test_exclusions_are_counted_by_reason(conn, reason, kwargs):
    _doc(conn, **kwargs)
    p = reextract.plan(conn, doc_cert_null=True)
    s = p.selections[0]
    assert (s.found, s.eligible, p.send) == (1, 0, [])
    assert s.left_alone[reason] == 1 and sum(s.left_alone.values()) == 1
    assert any(f"{reextract._WORDS[reason]} 1" in ln for ln in reextract.render(p))


def test_a_dead_latest_job_is_not_active_and_its_payload_is_copied(conn):
    h = _doc(conn)
    dead = _extract_job(conn, h, status="dead",
                        payload={"content_hash": h, "archive_url": f"/archive/X/{h}.pdf",
                                 "group_id": None, "source_url": "dead"})
    assert reextract.plan(conn, doc_cert_null=True).send[0]["payload"] == dead


def test_include_published_sends_published_documents_and_counts_them(conn):
    h = _doc(conn, status="production",
             audit=[("production-write", "gate"), ("archive-url-repair", "ops:repair-archive-urls")])
    p = reextract.plan(conn, doc_cert_null=True, include_published=True)
    assert _sent(p) == {h} and p.selections[0].published == 1
    assert any("eligible 1 (1 published)" in ln for ln in reextract.render(p))


@pytest.mark.parametrize("actor", ["gate", "system", "ops:repair-mfr-overbind",
                                   "tool:repair-document-manufacturer"])
def test_a_machine_production_write_is_not_an_approval(conn, actor):
    h = _doc(conn, status="production", audit=[("production-write", actor)])
    p = reextract.plan(conn, doc_cert_null=True, include_published=True)
    assert _sent(p) == {h} and p.selections[0].left_alone["approved"] == 0


def test_a_reviewer_approval_is_left_alone_even_with_include_published(conn):
    _doc(conn, status="production",
         audit=[("production-write", "gate"), ("approve", "user:admin")])
    p = reextract.plan(conn, doc_cert_null=True, include_published=True)
    assert p.send == [] and p.selections[0].left_alone["approved"] == 1


def test_an_unknown_hash_has_no_previous_job(conn):
    p = reextract.plan(conn, hashes=["f" * 64])
    assert p.selections[0].left_alone["no_extract_job"] == 1 and p.send == []


# --- the archive handle --------------------------------------------------------------

def test_the_registry_archive_url_replaces_the_old_jobs(conn):
    h = _doc(conn, archive_url="/archive/X/rehomed.pdf")
    p = reextract.plan(conn, doc_cert_null=True)
    payload = p.send[0]["payload"]
    assert payload["archive_url"] == "/archive/X/rehomed.pdf"
    assert payload["companion_archive_url"] == f"/archive/X/{h}_RA.pdf"
    assert p.rehomed == 1
    assert any("archive handle taken from the registry" in ln for ln in reextract.render(p))


def test_an_unchanged_archive_url_is_not_counted_as_rehomed(conn):
    _doc(conn)
    assert reextract.plan(conn, doc_cert_null=True).rehomed == 0


# --- limit and the printed list --------------------------------------------------------

def test_limit_keeps_the_first_hashes_and_counts_the_rest(conn):
    hs = sorted(_doc(conn) for _ in range(3))
    p = reextract.plan(conn, doc_cert_null=True, limit=2)
    assert [r["content_hash"] for r in p.send] == hs[:2] and p.held_back == 1
    lines = reextract.render(p)
    assert "Total extract.doc jobs to enqueue: 2 (--limit 2; 1 more eligible" in "\n".join(lines)
    assert any(ln.strip().startswith(hs[0]) for ln in lines)


def test_render_prints_the_first_hashes_only(conn):
    hs = sorted(_doc(conn) for _ in range(reextract.SHOWN + 2))
    lines = reextract.render(reextract.plan(conn, doc_cert_null=True))
    assert sum(1 for ln in lines if ln.strip()[:32] in {h[:32] for h in hs}) == reextract.SHOWN
    assert lines[-1].strip() == "... (2 more)"


# --- dry run and apply ---------------------------------------------------------------

def _jobs(conn):
    return conn.execute("SELECT payload, dedupe_key, status FROM job "
                        "WHERE type='extract.doc' AND status='pending'").fetchall()


def test_apply_enqueues_exactly_the_plan_with_copied_payloads(conn):
    h = _doc(conn)
    want = conn.execute("SELECT payload FROM job WHERE type='extract.doc'").fetchone()["payload"]
    _doc(conn, cert="x")
    p = reextract.plan(conn, doc_cert_null=True)
    assert _jobs(conn) == []                     # planning wrote nothing
    assert reextract.apply(conn, p) == {"queued": 1, "already_queued": 0}
    (job,) = _jobs(conn)
    assert job["payload"] == want and job["dedupe_key"] == f"extract:{h}"
    assert job["payload"]["companion_archive_url"] and job["payload"]["group_id"] == 7


def test_apply_counts_a_job_that_became_active_meanwhile(conn):
    h = _doc(conn)
    p = reextract.plan(conn, doc_cert_null=True)
    _extract_job(conn, h, status="pending")
    assert reextract.apply(conn, p) == {"queued": 0, "already_queued": 1}


def test_the_latest_extract_job_payload_is_the_one_copied(conn):
    h = _doc(conn)
    newer = _extract_job(conn, h, payload={"content_hash": h, "archive_url": f"/archive/X/{h}.pdf",
                                           "group_id": None})
    assert reextract.plan(conn, hashes=[h]).send[0]["payload"] == newer


# --- sync only ------------------------------------------------------------------------

def test_batch_mode_refuses(monkeypatch):
    monkeypatch.setenv("DENTALIA_EXTRACT_MODE", "batch")
    assert "sync mode" in reextract.refusal()
    monkeypatch.setenv("DENTALIA_EXTRACT_MODE", "sync")
    assert reextract.refusal() is None


def test_the_refusal_and_the_dispatcher_read_one_mode(monkeypatch):
    """Both readers go through `batching.extract_mode`: change it once and the
    handler's transport and the command's refusal move together."""
    monkeypatch.setenv("DENTALIA_EXTRACT_MODE", "sync")
    monkeypatch.setattr(batching, "extract_mode", lambda: "batch")
    seen = {}
    monkeypatch.setattr(extract_handler, "_default_batch_client", lambda: "batch-client")
    monkeypatch.setattr(extract_handler, "handle_extract_doc",
                        lambda conn, job, **kw: seen.update(kw))
    extract_handler._dispatch(None, {})
    assert seen == {"batch_client": "batch-client"}
    assert reextract.refusal() is not None


def test_apply_refuses_in_batch_mode(conn, monkeypatch):
    _doc(conn)
    p = reextract.plan(conn, doc_cert_null=True)
    monkeypatch.setenv("DENTALIA_EXTRACT_MODE", "batch")
    with pytest.raises(ValueError):
        reextract.apply(conn, p)
    assert _jobs(conn) == []


# --- the command ----------------------------------------------------------------------

def _run(monkeypatch, test_db_url, *argv):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.delenv("DENTALIA_EXTRACT_MODE", raising=False)
    return cli.main(["reextract", *argv])


def test_cli_dry_run_writes_nothing_then_apply_queues(test_db_url, connect_test,
                                                      monkeypatch, capsys):
    seed = connect_test()
    h = _doc(seed)
    seed.commit()
    assert _run(monkeypatch, test_db_url, "--doc-cert-null") == 0
    out = capsys.readouterr().out
    assert "doc-cert-null: found 1, eligible 1" in out and "Dry run" in out and h in out
    check = connect_test()
    assert _jobs(check) == []
    check.rollback()
    assert _run(monkeypatch, test_db_url, "--doc-cert-null", "--apply") == 0
    assert "queued: 1 extract.doc" in capsys.readouterr().out
    check.rollback()
    assert [j["dedupe_key"] for j in _jobs(check)] == [f"extract:{h}"]


def test_cli_needs_a_selector(test_db_url, monkeypatch, capsys):
    assert _run(monkeypatch, test_db_url) == 2


def test_cli_refuses_batch_mode(test_db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("DENTALIA_EXTRACT_MODE", "batch")
    assert cli.main(["reextract", "--doc-cert-null", "--apply"]) == 1
    assert "sync mode" in capsys.readouterr().err


def test_cli_hashes_file(test_db_url, connect_test, monkeypatch, tmp_path, capsys):
    seed = connect_test()
    h = _doc(seed, cert="x")
    seed.commit()
    f = tmp_path / "h.txt"
    f.write_text(f"# note\n   # indented note\n{h}\n\n")
    assert _run(monkeypatch, test_db_url, "--hashes-file", str(f)) == 0
    out = capsys.readouterr().out
    assert "hashes: found 1, eligible 1" in out and "no previous extract job" not in out


def test_cli_hashes_comma_list(test_db_url, connect_test, monkeypatch, capsys):
    seed = connect_test()
    a, b = _doc(seed, cert="x"), _doc(seed, cert="y")
    seed.commit()
    assert _run(monkeypatch, test_db_url, "--hashes", f" {a}, {b} ,") == 0
    assert "hashes: found 2, eligible 2" in capsys.readouterr().out


def test_cli_include_published_and_limit(test_db_url, connect_test, monkeypatch, capsys):
    seed = connect_test()
    hs = sorted(_doc(seed, status="production") for _ in range(2))
    seed.commit()
    assert _run(monkeypatch, test_db_url, "--doc-cert-null") == 0
    assert "published (add --include-published to read them too) 2" in capsys.readouterr().out
    assert _run(monkeypatch, test_db_url, "--doc-cert-null", "--include-published",
                "--limit", "1", "--apply") == 0
    assert "queued: 1 extract.doc" in capsys.readouterr().out
    check = connect_test()
    assert [j["dedupe_key"] for j in _jobs(check)] == [f"extract:{hs[0]}"]


def test_cli_limit_must_be_positive(test_db_url, monkeypatch, capsys):
    assert _run(monkeypatch, test_db_url, "--doc-cert-null", "--limit", "0") == 2
