"""Repairing REF lists that the pre-fix `tiers.merge` erased.

The bug this repairs: `extract_ref_list` stamps a flat 0.9 that can never clear
the 0.95 escalation threshold, so a successful T0 REF list escalated on every
document, and the old unconditional `{**base, **new}` merge let a vision tier's
empty answer delete it. `tiers.merge` is fixed; this module repairs the rows
written before the fix, without paying for re-extraction.

The properties that matter are the ones that keep the repair honest: it must
append rather than overwrite (migration 004 keeps every tier attempt), it must
not invent values T0 cannot reproduce, and it must leave the queue pointing at
the rev it just wrote — VALIDATE reads `(content_hash, extract_rev)` from a
payload that invariant 9 forbids mutating.
"""

from __future__ import annotations

import pytest

from app import repair_ref_list
from app.extract import tiers

GC_FIXTURE = "GC/everX_Posterior_12022026.pdf"   # 8-language table embedded on page 5
HASH = "a" * 64


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE extraction_attempt RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE fetch_log CASCADE")

    wipe()
    yield
    wipe()


def _seed(conn, path, fields, *, content_hash=HASH, rev=1, tier="T0+T1+T2"):
    """One persisted extraction attempt plus the ledger row naming its file."""
    from psycopg.types.json import Json

    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, extract_rev) "
        "VALUES (%s,%s,'claude-sonnet-5',%s,%s)",
        (content_hash, tier, Json(fields), rev),
    )
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at) "
        "VALUES (%s,%s,'backfill',now())",
        (path, content_hash),
    )


def _stored(path, **overrides):
    """A realistically-shaped persisted row: the real ladder output is
    `merge(T0, llm)`, so every T0 field is already present in it. Building the
    fixture from an actual T0 run rather than a hand-written stub keeps the test
    honest — a thin stub would make the repair look like it restores fields it
    only appears to restore because the stub omitted them.
    """
    from app.extract import pdf as pdfutil
    from app.extract.t0_templates import t0_extract

    _, fields = t0_extract(pdfutil.open_doc(path), path)
    return {**fields, **overrides}


def _erased(path, tier="T2"):
    """What the vision tier actually returned on the GC corpus: an empty REF
    list, in good faith, because page 2 says the articles are in an attachment."""
    return _stored(path, ref_list={"value": [], "conf": 0.3, "tier": tier,
                                   "verbatim": "According to the Attachment"},
                   type={"value": "DoC", "conf": 0.98, "tier": "T1", "verbatim": "DoC"})


def _enqueue_validate(conn, rev=1, content_hash=HASH, group_id=None):
    from psycopg.types.json import Json

    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key) VALUES "
        "('validate.doc'::job_type, %s, %s) RETURNING id",
        (Json({"content_hash": content_hash, "group_id": group_id, "extract_rev": rev}),
         f"validate:{content_hash}:{rev}:{group_id}"),
    ).fetchone()["id"]


# --------------------------------------------------------------------------- #
# plan — what needs repairing, and what must be left alone
# --------------------------------------------------------------------------- #
def test_plan_restores_a_ref_list_the_later_tier_erased(conn, fixture_pdf):
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repairs, skipped = repair_ref_list.plan(conn)
    assert skipped == []
    assert len(repairs) == 1
    assert repairs[0]["restored"] == ["ref_list"]
    assert len(repairs[0]["repaired"]["ref_list"]["value"]) == 6
    assert repairs[0]["repaired"]["ref_list"]["tier"] == "T0"


def test_plan_leaves_a_fuller_later_answer_alone(conn, fixture_pdf):
    """A later tier that enumerated MORE than the parser did is authoritative —
    repairing that would overwrite a paid answer with a thinner one. This is the
    KOMET shape, where T0's text-column parser under-reads and the LLM sees more."""
    path = fixture_pdf(GC_FIXTURE)
    fields = _erased(path)
    fields["ref_list"] = {"value": [f"90000{i}" for i in range(9)],   # T0 finds 6
                          "conf": 0.9, "tier": "T2", "verbatim": "x"}
    _seed(conn, path, fields)
    assert repair_ref_list.plan(conn)[0] == []


def test_plan_restores_a_truncated_later_answer(conn, fixture_pdf):
    """CHANGED 2026-08-13, and this test used to assert the opposite.

    The old rule was "any non-empty later answer is authoritative". It is not: the
    prompt CAPS `ref_list` and tells the model a deterministic parser owns the
    full list, so a SHORTER LLM answer is a truncated sample of the same table T0
    enumerated, never a correction. Measured on the GC corpus, that rule stored
    1.502 codes where T0 reads 2.843 and cost 199 catalogue items of coverage."""
    path = fixture_pdf(GC_FIXTURE)
    fields = _erased(path)
    fields["ref_list"] = {"value": ["999999"], "conf": 0.9, "tier": "T2", "verbatim": "x"}
    _seed(conn, path, fields)
    repairs = repair_ref_list.plan(conn)[0]
    assert len(repairs) == 1
    assert repairs[0]["restored"] == ["ref_list"]
    assert len(repairs[0]["repaired"]["ref_list"]["value"]) == 6


def test_plan_keeps_the_later_tiers_other_fields(conn, fixture_pdf):
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repaired = repair_ref_list.plan(conn)[0][0]["repaired"]
    assert repaired["type"]["tier"] == "T1"   # not re-derived from T0
    assert repaired["type"]["conf"] == 0.98


def test_plan_reports_an_unreadable_file_instead_of_dropping_it(conn, fixture_pdf):
    _seed(conn, "/nonexistent/gone.pdf", _erased(fixture_pdf(GC_FIXTURE)))
    repairs, skipped = repair_ref_list.plan(conn)
    assert repairs == []
    assert len(skipped) == 1 and skipped[0]["content_hash"] == HASH
    assert skipped[0]["reason"]          # a stated reason, never a silent skip


def test_plan_reads_the_latest_rev_not_the_first(conn, fixture_pdf):
    """Re-running the repair must diff against the newest attempt, or it would
    append a duplicate repair every time it is run."""
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path), rev=1)
    good = _stored(path)   # what the fixed ladder would have written
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, extract_rev) "
        "VALUES (%s,'T0+T1+T2','m',%s,2)",
        (HASH, __import__("psycopg").types.json.Json(good)),
    )
    assert repair_ref_list.plan(conn)[0] == []


# --------------------------------------------------------------------------- #
# apply — append-only, and the queue follows the rev
# --------------------------------------------------------------------------- #
def test_apply_appends_a_new_rev_and_preserves_the_original(conn, fixture_pdf):
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    rows = conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt WHERE content_hash=%s "
        "ORDER BY extract_rev", (HASH,),
    ).fetchall()
    assert [r["extract_rev"] for r in rows] == [1, 2]
    assert rows[0]["fields"]["ref_list"]["value"] == []       # history intact
    assert len(rows[1]["fields"]["ref_list"]["value"]) == 6


def test_apply_repoints_the_pending_validate_job_at_the_new_rev(conn, fixture_pdf):
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    old_id = _enqueue_validate(conn, rev=1)
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    jobs = conn.execute(
        "SELECT id, payload, status FROM job WHERE type='validate.doc'"
    ).fetchall()
    assert len(jobs) == 1                       # the stale one is gone, not duplicated
    assert jobs[0]["id"] != old_id
    assert jobs[0]["payload"]["extract_rev"] == 2


def test_apply_carries_the_group_id_from_the_job_it_replaces(conn, fixture_pdf):
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    _enqueue_validate(conn, rev=1, group_id=77)
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    row = conn.execute("SELECT payload FROM job WHERE type='validate.doc'").fetchone()
    assert row["payload"]["group_id"] == 77     # dropping it would unscope the match


def test_apply_never_touches_a_running_job(conn, fixture_pdf):
    """A claimed job is mid-flight; deleting it would lose the worker's work."""
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    running = _enqueue_validate(conn, rev=1)
    conn.execute("UPDATE job SET status='running' WHERE id=%s", (running,))
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM job WHERE type='validate.doc'").fetchall()}
    assert "running" in statuses


def test_apply_emits_a_validate_job_even_with_no_stale_one(conn, fixture_pdf):
    """The repaired rev must be validated whether or not a job was waiting."""
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    row = conn.execute("SELECT payload FROM job WHERE type='validate.doc'").fetchone()
    assert row["payload"]["extract_rev"] == 2


def test_apply_is_idempotent(conn, fixture_pdf):
    """Running it twice must not append a second identical repair."""
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    repair_ref_list.apply(conn, repair_ref_list.plan(conn)[0])
    n = conn.execute(
        "SELECT count(*) n FROM extraction_attempt WHERE content_hash=%s", (HASH,)
    ).fetchone()["n"]
    assert n == 2


# --------------------------------------------------------------------------- #
# CLI — dry run by default, like vendor-master and regroup
# --------------------------------------------------------------------------- #
def test_cmd_dry_run_writes_nothing(conn, connect_test, test_db_url,
                                    fixture_pdf, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    conn.commit()                      # cli.main opens its own connection

    rc = cli.main(["repair-ref-list"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    assert "ref_list" in out
    n = connect_test().execute(
        "SELECT count(*) n FROM extraction_attempt WHERE content_hash=%s", (HASH,)
    ).fetchone()["n"]
    assert n == 1                      # nothing appended


def test_cmd_apply_writes(conn, connect_test, test_db_url, fixture_pdf, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    conn.commit()

    rc = cli.main(["repair-ref-list", "--apply"])

    assert rc == 0
    assert "repaired: 1" in capsys.readouterr().out
    rows = connect_test().execute(
        "SELECT extract_rev, fields FROM extraction_attempt WHERE content_hash=%s "
        "ORDER BY extract_rev", (HASH,)
    ).fetchall()
    assert [r["extract_rev"] for r in rows] == [1, 2]
    assert len(rows[1]["fields"]["ref_list"]["value"]) == 6


def test_cmd_on_a_clean_database_is_a_no_op(test_db_url, monkeypatch, capsys):
    """The repair is a one-off; on data extracted after the fix it plans zero."""
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["repair-ref-list"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "0 to repair" in out


def test_repair_uses_the_shipped_merge_rule(conn, fixture_pdf, monkeypatch):
    """The repair must be the fixed ladder replayed, not a parallel rule — or
    the database and the code would disagree about what a repair means."""
    calls = []
    real = tiers.merge
    monkeypatch.setattr(repair_ref_list.tiers, "merge",
                        lambda b, n: calls.append((b, n)) or real(b, n))
    path = fixture_pdf(GC_FIXTURE)
    _seed(conn, path, _erased(path))
    repair_ref_list.plan(conn)
    assert calls, "plan() must delegate the field decision to tiers.merge"
