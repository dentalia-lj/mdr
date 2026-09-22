"""Replacing the manufactured date verbatims with the real span.

The properties that keep this repair honest are mostly REFUSALS, because the
thing it corrects is evidence and the way to get that wrong is to write a
different fabrication over the first one:

  * append, never overwrite -- a new `extract_rev`, migration 004's contract,
    and the wrong quote stays in the record at its own rev because that is
    what a reviewer actually saw;
  * the new rev keeps every key at identical values; only the `verbatim` of a
    date field moves, so page/tier/confidence keep their meaning;
  * **a value disagreement is refused**, not applied. Rewriting a compliance
    date is a much larger event than correcting a quote and must not happen as
    a side effect of one;
  * **T1/T2 date evidence is never touched.** That verbatim is the model's own
    quote of the page; re-deriving it from T0 would replace a genuine quote and
    misattribute the tier;
  * `validate.doc` IS re-emitted, unlike `repair_stated_class`: only GATE may
    write `evidence` (invariant 1), and a corrected quote that never reaches
    the `evidence` table has repaired nothing;
  * nothing is silent: every scanned hash lands in exactly one count.
"""

from __future__ import annotations

import pytest

from app import repair_label_dates as rld

HASH = "b" * 64
OTHER = "c" * 64
URL = "/archive/ACME/doc/deadbeef__cert.pdf"


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE extraction_attempt RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE job RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE item_document, evidence, document RESTART IDENTITY CASCADE")
            c.execute("DELETE FROM manufacturer WHERE canonical_name='ACME'")

    wipe()
    yield
    wipe()


def _ev(value, verbatim, tier="T0", conf=1.0):
    return {"value": value, "conf": conf, "tier": tier, "verbatim": verbatim,
            "page": 1}


def _fields(**overrides):
    base = {
        "type": _ev("EC", "EC Certificate"),
        "regulation": _ev("MDR", "(EU) 2017/745"),
        "coverage_scope": _ev("manufacturer", "quality system"),
        # The manufactured quote: `label + " " + date`, no colon, because the
        # old code assembled it from the two halves rather than quoting.
        "validity_to": _ev("2030-04-16", "Expiry Date 2030-04-16"),
    }
    base.update(overrides)
    return base


def _seed(conn, fields, *, content_hash=HASH, rev=1, status="production",
          archive_url=URL, doc_id=None):
    # `document.canonical_manufacturer` is foreign-keyed (053), so the row has
    # to exist before a document can name it.
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('ACME') "
        "ON CONFLICT (canonical_name) DO NOTHING")
    conn.execute(
        "INSERT INTO document (doc_id, content_hash, type, regulation, "
        "  canonical_manufacturer, coverage_scope, archive_url, status) "
        "VALUES (COALESCE(%s, nextval('document_doc_id_seq')), %s,'EC','MDR',"
        "  'ACME','manufacturer',%s,%s::doc_status)",
        (doc_id, content_hash, archive_url, status))
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, "
        "  extract_rev) VALUES (%s,'T0',NULL,%s,%s)",
        (content_hash, __import__("json").dumps(fields), rev))
    conn.commit()


def _reader(result):
    """Stand in for reopening the archived PDF. `plan` takes it for the same
    reason `repair_archive_urls` takes a `resolver`: a test that had to write
    real PDFs would be testing PyMuPDF, not this module's decisions."""
    return lambda url, playbooks: result


# --- what it repairs -------------------------------------------------------- #

def test_a_manufactured_quote_is_replaced_with_the_real_span(conn):
    _seed(conn, _fields())
    repairs, counts, skipped = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))

    assert counts["repaired"] == 1
    assert len(repairs) == 1
    new = repairs[0]["fields_new"]["validity_to"]
    assert new["verbatim"] == "Expiry Date: 2030-04-16"
    # Only the quote moves. The value a compliance question reads, and the
    # tier/page/confidence that say where it came from, are untouched.
    assert new["value"] == "2030-04-16"
    assert (new["tier"], new["page"], new["conf"]) == ("T0", 1, 1.0)
    # And every OTHER field is carried through unchanged, at identical values:
    # the latest rev is what later readers take, so it must never be poorer.
    assert repairs[0]["fields_new"]["type"] == _fields()["type"]
    assert repairs[0]["fields_new"]["regulation"] == _fields()["regulation"]


def test_the_signature_line_case_is_repaired_too(conn):
    """`validity_from` is deliberately laxer -- MDR Annex IV mandates a
    place-and-date line, so `Valid from Schaan, 2021-05-25` is a legitimate
    pairing across content. The old code still threw the place away."""
    _seed(conn, _fields(validity_from=_ev("2021-05-25", "Valid from 2021-05-25")))
    repairs, counts, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({
            "validity_to": _ev("2030-04-16", "Expiry Date 2030-04-16"),
            "validity_from": _ev("2021-05-25", "Valid from Schaan, 2021-05-25")}))
    assert counts["repaired"] == 1          # only validity_from moved
    assert counts["already_true"] == 1      # validity_to was already exact
    assert repairs[0]["fields_new"]["validity_from"]["verbatim"] == (
        "Valid from Schaan, 2021-05-25")


def test_an_already_true_quote_earns_no_revision(conn):
    """What makes this re-runnable: once applied, the repair's own output is
    the incumbent and plans empty."""
    _seed(conn, _fields(validity_to=_ev("2030-04-16", "Expiry Date: 2030-04-16")))
    repairs, counts, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    assert repairs == []
    assert counts["already_true"] == 1 and counts["repaired"] == 0


# --- what it refuses -------------------------------------------------------- #

def test_a_value_disagreement_is_refused_and_reported(conn):
    """Rewriting a compliance date is a much larger event than correcting a
    quote. Measured 2026-09-04 this is zero on the whole corpus, so refusing
    costs nothing -- and it is what stops a future run silently moving an
    expiry because the reader changed again."""
    _seed(conn, _fields())
    repairs, counts, skipped = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2024-01-01", "Expiry Date: 2024-01-01")}))
    assert repairs == []
    assert counts["value_disagrees"] == 1
    assert "VALUE would change" in skipped[0]["reason"]
    assert "2030-04-16" in skipped[0]["reason"] and "2024-01-01" in skipped[0]["reason"]


def test_a_field_t0_no_longer_reads_is_left_alone_and_reported(conn):
    """The DQS shape: the corrected rule refuses every labelled pairing, so T0
    now returns nothing. The stored value came from somewhere and this tool
    does not get to delete it."""
    _seed(conn, _fields())
    repairs, counts, skipped = rld.plan(conn, playbooks=(), reread=_reader({}))
    assert repairs == []
    assert counts["t0_now_silent"] == 1
    assert "reads nothing today" in skipped[0]["reason"]


def test_a_t1_date_is_never_touched(conn):
    """A T1/T2 verbatim is the model's own quote of the page. Re-deriving it
    from T0 would replace a genuine quote with a different one and would file
    a T1 read under T0's attribution."""
    _seed(conn, _fields(validity_to=_ev("2030-04-16", "the model's quote", tier="T1",
                                        conf=0.95)))
    repairs, counts, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    assert repairs == []
    # Not even scanned: the document holds no T0 date at all.
    assert counts["scanned"] == 0


def test_an_unreadable_archive_file_is_a_finding_not_a_crash(conn):
    _seed(conn, _fields())

    def boom(url, playbooks):
        raise FileNotFoundError(url)

    repairs, counts, skipped = rld.plan(conn, playbooks=(), reread=boom)
    assert repairs == []
    assert counts["unreadable"] == 1
    assert "FileNotFoundError" in skipped[0]["reason"]


def test_every_scanned_document_lands_in_exactly_one_count(conn):
    """The arithmetic guard. A hash that fell through every branch would be a
    silent skip, which is the thing CLAUDE.md forbids."""
    _seed(conn, _fields())
    _seed(conn, _fields(validity_to=_ev("2031-01-01", "Expiry Date: 2031-01-01")),
          content_hash=OTHER, archive_url="/archive/ACME/doc/other.pdf")
    repairs, counts, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    # Two documents, one field each: one repaired, one already true (its stored
    # quote matches what the reader returns for it -- the fake returns the same
    # thing for both, so the second's stored value disagrees).
    assert counts["scanned"] == 2
    assert sum(counts[k] for k in rld.OUTCOMES) == 2


# --- what it writes --------------------------------------------------------- #

def test_apply_appends_a_new_rev_and_never_overwrites(conn):
    _seed(conn, _fields())
    repairs, _, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    rld.apply(conn, repairs)
    conn.commit()

    rows = conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt WHERE content_hash=%s "
        "ORDER BY extract_rev", (HASH,)).fetchall()
    assert [r["extract_rev"] for r in rows] == [1, 2]
    # The wrong quote is still there at rev 1. That is correct: it is what a
    # reviewer saw, and migration 004 keeps every attempt.
    assert rows[0]["fields"]["validity_to"]["verbatim"] == "Expiry Date 2030-04-16"
    assert rows[1]["fields"]["validity_to"]["verbatim"] == "Expiry Date: 2030-04-16"


def test_apply_emits_validate_doc_carrying_the_archive_url(conn):
    """Only GATE may write `evidence` (invariant 1), so the job is the only way
    a corrected quote reaches the table the review screen reads. The
    `archive_url` rides along deliberately: VALIDATE's fallback when the
    payload has none is `fetch_log.url_normalized`, the corpus SOURCE path for
    a backfill, which is what put a /imports/... URL on 130 GC documents."""
    _seed(conn, _fields())
    repairs, _, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    stats = rld.apply(conn, repairs)
    conn.commit()

    assert stats["jobs_emitted"] == 1
    job = conn.execute(
        "SELECT type, payload FROM job WHERE type='validate.doc'").fetchone()
    assert job["payload"]["content_hash"] == HASH
    assert job["payload"]["extract_rev"] == 2
    assert job["payload"]["archive_url"] == URL


def test_apply_repoints_a_pending_validate_job_instead_of_leaving_it_stale(conn):
    """Payloads are immutable (invariant 9), so a pending `validate.doc` pinned
    to the old rev is deleted and re-emitted, and the group scope it carried is
    kept -- dropping it would turn a group-scoped match into an unscoped one."""
    from app import queue
    _seed(conn, _fields())
    queue.enqueue(conn, "validate.doc",
                  {"content_hash": HASH, "extract_rev": 1, "group_id": 77},
                  dedupe_key=f"validate:{HASH}:1:77")
    conn.commit()

    repairs, _, _ = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    stats = rld.apply(conn, repairs)
    conn.commit()

    assert stats["jobs_deleted"] == 1
    jobs = conn.execute(
        "SELECT payload FROM job WHERE type='validate.doc'").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["payload"]["extract_rev"] == 2
    assert jobs[0]["payload"]["group_id"] == 77


def test_the_report_prints_both_quotes_in_full(conn):
    """The whole subject of this repair is what the quote says, so a report
    that truncated it would hide its own result."""
    _seed(conn, _fields())
    repairs, counts, skipped = rld.plan(
        conn, playbooks=(),
        reread=_reader({"validity_to": _ev("2030-04-16", "Expiry Date: 2030-04-16")}))
    text = "\n".join(rld.render(repairs, counts, skipped))
    assert "'Expiry Date 2030-04-16'" in text
    assert "'Expiry Date: 2030-04-16'" in text
    assert "repaired 1" in text
