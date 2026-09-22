"""Queue semantics — table-driven, against a real Postgres (CLAUDE.md).

Covers the S0.1 required set: claim ordering (priority then run_after),
active-scope dedupe, backoff -> dead-letter, kill-mid-job reclaim, tx atomicity
of claim+result, plus the FETCH domain lease. Time is simulated by ageing rows
with direct SQL (no sleeps) so the tests are deterministic.
"""

from __future__ import annotations

import datetime as dt
import decimal

import pytest

from app import queue
from app.workers import runner

WORKER = "w-test"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _status(conn, job_id: int) -> str:
    return conn.execute("SELECT status FROM job WHERE id=%s", (job_id,)).fetchone()[
        "status"
    ]


def _row(conn, job_id: int) -> dict:
    return conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()


def _force_status(conn, job_id: int, status: str) -> None:
    conn.execute("UPDATE job SET status=%s WHERE id=%s", (status, job_id))


def _age_claimed(conn, job_id: int, seconds: int) -> None:
    """Push claimed_at into the past to simulate a worker that died."""
    conn.execute(
        "UPDATE job SET claimed_at = now() - make_interval(secs => %s) WHERE id=%s",
        (seconds, job_id),
    )


# --------------------------------------------------------------------------- #
# enqueue
# --------------------------------------------------------------------------- #
def test_enqueue_inserts_pending_job(conn):
    jid = queue.enqueue(conn, "ingest.run", {"catalogue": "LJ"}, "ingest:LJ:1")
    conn.commit()
    assert isinstance(jid, int)
    assert _status(conn, jid) == "pending"


def test_claim_returns_none_when_empty(conn):
    assert queue.claim(conn, WORKER) is None


# --------------------------------------------------------------------------- #
# claim ordering: priority (interactive<delta<sweep) then run_after asc
# --------------------------------------------------------------------------- #
def test_claim_orders_by_priority_then_run_after(conn):
    # (label, priority, run_after offset seconds — all in the past = claimable)
    seed = [
        ("A", "sweep", -10),
        ("B", "interactive", -5),
        ("C", "delta", -8),
        ("D", "interactive", -3),
        ("E", "sweep", -20),
    ]
    for label, prio, off in seed:
        conn.execute(
            "INSERT INTO job (type, payload, dedupe_key, priority, run_after) "
            "VALUES ('ingest.run', %s, %s, %s, now() + make_interval(secs => %s))",
            (queue._json({"l": label}), f"k-{label}", prio, off),
        )
    conn.commit()

    got = []
    while (row := queue.claim(conn, WORKER)) is not None:
        got.append(row["payload"]["l"])
        conn.commit()

    # interactive first (B before D by earlier run_after), then delta (C), then
    # sweep (E before A by earlier run_after).
    assert got == ["B", "D", "C", "E", "A"]


def test_claim_skips_future_run_after(conn):
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, run_after) "
        "VALUES ('ingest.run', %s, 'future', now() + interval '1 hour')",
        (queue._json({}),),
    )
    conn.commit()
    assert queue.claim(conn, WORKER) is None


def test_skip_locked_two_workers_get_distinct_jobs(connect_test):
    a = connect_test(autocommit=False)
    b = connect_test(autocommit=False)
    queue.enqueue(a, "ingest.run", {}, "j1")
    queue.enqueue(a, "ingest.run", {}, "j2")
    a.commit()

    ra = queue.claim(a, "wa")  # holds row lock, uncommitted
    rb = queue.claim(b, "wb")  # must SKIP LOCKED past ra's row
    assert ra is not None and rb is not None
    assert ra["id"] != rb["id"]


# --------------------------------------------------------------------------- #
# active-scope dedupe (C2): pending/running/failed block; done/dead free
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status,blocks",
    [
        ("pending", True),
        ("running", True),
        ("failed", True),
        ("done", False),
        ("dead", False),
    ],
)
def test_dedupe_scoped_to_active_status(conn, status, blocks):
    first = queue.enqueue(conn, "fetch.url", {}, "fetch:http://x")
    _force_status(conn, first, status)
    conn.commit()

    second = queue.enqueue(conn, "fetch.url", {}, "fetch:http://x")
    conn.commit()

    if blocks:
        assert second is None
    else:
        assert isinstance(second, int) and second != first


# --------------------------------------------------------------------------- #
# backoff -> dead-letter
# --------------------------------------------------------------------------- #
def test_fail_sets_failed_and_advances_run_after(conn):
    jid = queue.enqueue(conn, "fetch.url", {}, "f1", max_attempts=3)
    conn.commit()
    claimed = queue.claim(conn, WORKER)
    assert claimed["attempts"] == 1
    new_status = queue.fail(conn, jid, "boom")
    conn.commit()
    row = _row(conn, jid)
    assert new_status == "failed"
    assert row["status"] == "failed"
    assert row["last_error"] == "boom"
    assert row["run_after"] > row["created_at"]  # pushed into the future


def test_exhausted_attempts_go_dead(conn):
    jid = queue.enqueue(conn, "fetch.url", {}, "f2", max_attempts=3)
    conn.commit()
    last = None
    for _ in range(3):
        # make the retry immediately claimable again
        conn.execute("UPDATE job SET run_after = now() WHERE id=%s", (jid,))
        conn.commit()
        assert queue.claim(conn, WORKER) is not None
        last = queue.fail(conn, jid, "boom")
        conn.commit()
    assert last == "dead"
    assert _status(conn, jid) == "dead"


# --------------------------------------------------------------------------- #
# a dead job raises a manual task (nothing produced `dead-job-followup` before)
# --------------------------------------------------------------------------- #
def _kill(conn, tag="extract.doc", payload=None, key="dj", error="boom"):
    """Drive a job all the way to dead and return its id."""
    jid = queue.enqueue(conn, tag, payload if payload is not None else {}, key,
                        max_attempts=2)
    conn.commit()
    for _ in range(2):
        conn.execute("UPDATE job SET run_after = now() WHERE id=%s", (jid,))
        conn.commit()
        queue.claim(conn, WORKER)
        status = queue.fail(conn, jid, error)
        conn.commit()
    assert status == "dead"
    return jid


def _tasks(conn, kind="dead-job-followup"):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM manual_task WHERE kind=%s ORDER BY id", (kind,)).fetchall()]


def test_a_dead_job_raises_a_manual_task(conn):
    """`manual_kind` has carried `dead-job-followup` since the schema was
    written and nothing ever produced one, so 45 dead jobs on 2026-08-14 were
    visible only to somebody who thought to open the /dead board.

    (Those 45 turned out to be 2 distinct documents, both safety data sheets
    that correctly do not belong in the registry -- so nothing was actually
    lost. The alarm is warranted by what a dead job CAN cost, not by that
    count; see `test_a_stale_rev_cannot_dead_letter_on_a_value_a_newer_rev_replaced`
    for the permanent-failure loop the same audit found.)"""
    jid = _kill(conn)

    tasks = _tasks(conn)
    assert len(tasks) == 1, "a dead job produced no manual task"
    assert tasks[0]["status"] == "open"
    assert tasks[0]["payload"]["job_id"] == jid
    assert tasks[0]["payload"]["job_type"] == "extract.doc"
    assert tasks[0]["payload"]["error"] == "boom"


def test_the_task_carries_what_a_rerun_needs(conn):
    """Actionable without opening the job row: the identifying hash and where
    the bytes live are what re-running a lost document takes."""
    _kill(conn, payload={"content_hash": "abc123", "archive_url": "/archive/x.pdf",
                         "source_url": "DOC/Real Name.pdf", "group_id": None})

    p = _tasks(conn)[0]["payload"]
    assert p["content_hash"] == "abc123"
    assert p["archive_url"] == "/archive/x.pdf"
    assert p["source_url"] == "DOC/Real Name.pdf"


def test_a_job_with_no_document_still_raises_one(conn):
    """Every tag, not just the document-carrying ones -- the alarm is about a
    lost JOB. The hash keys are simply absent when the payload has none, rather
    than null-filled, so a reader can tell "no hash" from "hash unknown"."""
    _kill(conn, tag="report.weekly", payload={"period": "2026-W33"}, key="dj-rep")

    p = _tasks(conn)[0]["payload"]
    assert p["job_type"] == "report.weekly"
    assert "content_hash" not in p and "archive_url" not in p


def test_the_alarm_closes_when_the_same_work_finally_succeeds(conn):
    """The board must empty on success, or it becomes a list of things already
    fixed and stops being read. "Same work" is the dedupe key -- the re-run
    carries a new job id, so job_id alone could never match it."""
    _kill(conn, key="dj-heal")
    rerun = queue.enqueue(conn, "extract.doc", {}, "dj-heal")   # dead frees the key
    conn.commit()
    queue.claim(conn, WORKER)
    queue.finish(conn, rerun, result={"ok": True})
    conn.commit()

    t = _tasks(conn)[0]
    assert t["status"] == "resolved"
    assert t["resolved_by"] == "worker"
    assert t["resolved_at"] is not None


def test_an_unrelated_success_leaves_the_alarm_open(conn):
    """Scoped to the failing work, not "any job finished". Closing every alarm
    on any success would hide documents still missing from the registry."""
    _kill(conn, key="dj-other")
    unrelated = queue.enqueue(conn, "extract.doc", {}, "dj-unrelated")
    conn.commit()
    queue.claim(conn, WORKER)
    queue.finish(conn, unrelated, result={"ok": True})
    conn.commit()

    assert _tasks(conn)[0]["status"] == "open"


def test_a_retry_that_has_not_died_yet_raises_nothing(conn):
    """The alarm belongs to the dead transition alone. Firing on every failed
    attempt would put five tasks on the board for one lost document."""
    jid = queue.enqueue(conn, "extract.doc", {}, "dj-retry", max_attempts=3)
    conn.commit()
    queue.claim(conn, WORKER)
    assert queue.fail(conn, jid, "boom") == "failed"
    conn.commit()

    assert _tasks(conn) == []


# --------------------------------------------------------------------------- #
# kill-mid-job -> reclaimable (visibility timeout)
# --------------------------------------------------------------------------- #
def test_stale_running_job_is_reclaimed(conn):
    jid = queue.enqueue(conn, "extract.doc", {}, "e1")
    conn.commit()
    first = queue.claim(conn, WORKER)
    conn.commit()
    assert first["id"] == jid and first["attempts"] == 1

    _age_claimed(conn, jid, 600)  # worker died 10 min ago
    conn.commit()

    reclaimed = queue.claim(conn, "w2", visibility_timeout_s=300)
    conn.commit()
    assert reclaimed is not None
    assert reclaimed["id"] == jid
    assert reclaimed["attempts"] == 2  # reclaim counts as an attempt (poison guard)


def test_fresh_running_job_not_reclaimed(conn):
    jid = queue.enqueue(conn, "extract.doc", {}, "e2")
    conn.commit()
    queue.claim(conn, WORKER)  # claimed_at = now, still alive
    conn.commit()
    assert queue.claim(conn, "w2", visibility_timeout_s=300) is None


# --------------------------------------------------------------------------- #
# tx atomicity of claim + result
# --------------------------------------------------------------------------- #
def test_claim_rollback_leaves_job_pending(connect_test):
    setup = connect_test(autocommit=True)
    queue.enqueue(setup, "ingest.run", {}, "atomic")

    work = connect_test(autocommit=False)
    claimed = queue.claim(work, WORKER)
    assert claimed is not None and claimed["status"] == "running"
    work.rollback()  # simulate crash before commit

    check = connect_test(autocommit=True)
    row = check.execute(
        "SELECT status, attempts FROM job WHERE dedupe_key='atomic'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 0  # the claim's increment was rolled back too


def test_finish_marks_done(conn):
    jid = queue.enqueue(conn, "ingest.run", {}, "done1")
    conn.commit()
    queue.claim(conn, WORKER)
    queue.finish(conn, jid)
    conn.commit()
    assert _status(conn, jid) == "done"


def test_finish_persists_the_result_envelope(conn):
    # NOTE: the brief's snippet enqueues type "noop", which is not a member of
    # the closed job_type enum (migrations/001_queue.sql) and would raise on
    # insert. Substituted "ingest.run", already used the same way just above.
    job_id = queue.enqueue(conn, "ingest.run", {}, dedupe_key="res-1")
    queue.claim(conn, "w1")
    queue.finish(conn, job_id, result={"counts": {"seen": 3}})
    row = conn.execute("SELECT status, result FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["result"]["counts"]["seen"] == 3


def test_finish_serialises_the_types_psycopg_hands_back(conn):
    """A `date` column read back out of Postgres must survive `finish`.

    `report.weekly` selects `document_effective_expiry.expires` and puts the
    rows straight in its result. psycopg returns a `datetime.date`, the bare
    `json.dumps` here refused it, and the stage dead-lettered on every one of
    its five attempts -- it had never once completed on the live registry.
    Dates, timestamps and numerics are what this schema's columns ARE; a result
    envelope that cannot hold them is the boundary being wrong, not the caller.
    """
    job_id = queue.enqueue(conn, "ingest.run", {}, dedupe_key="res-dates")
    queue.claim(conn, "w1")
    queue.finish(
        conn,
        job_id,
        result={
            "day": dt.date(2020, 7, 3),
            "at": dt.datetime(2026, 8, 21, 7, 6, 0, tzinfo=dt.timezone.utc),
            "cost": decimal.Decimal("0.0242"),
        },
    )
    row = conn.execute("SELECT result FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["result"]["day"] == "2020-07-03"
    assert row["result"]["at"].startswith("2026-08-21T07:06:00")
    assert row["result"]["cost"] == pytest.approx(0.0242)


def test_finish_still_refuses_a_result_it_cannot_honestly_represent(conn):
    """The default widens the contract; it must not blanket-`str` everything.

    Coercing any unknown object to its repr would turn a handler bug into a
    plausible-looking result row -- the failure mode this queue spends
    `job_snapshot` and the dead-letter path avoiding. Unknown types still
    raise.
    """
    job_id = queue.enqueue(conn, "ingest.run", {}, dedupe_key="res-unserialisable")
    queue.claim(conn, "w1")
    with pytest.raises(TypeError):
        queue.finish(conn, job_id, result={"conn": object()})


def test_finish_without_a_result_leaves_it_null(conn):
    job_id = queue.enqueue(conn, "ingest.run", {}, dedupe_key="res-2")
    queue.claim(conn, "w1")
    queue.finish(conn, job_id)
    row = conn.execute("SELECT result FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["result"] is None


def test_defer_reschedules_without_consuming_attempt(conn):
    jid = queue.enqueue(conn, "extract.doc", {}, "batch1")
    conn.commit()
    queue.claim(conn, WORKER)  # attempts -> 1
    conn.commit()
    queue.defer(conn, jid, seconds=900)
    conn.commit()
    row = _row(conn, jid)
    assert row["status"] == "pending"
    assert row["attempts"] == 0  # voluntary reschedule refunds the claim increment
    assert row["run_after"] > row["created_at"]


# --------------------------------------------------------------------------- #
# domain lease (FETCH politeness)
# --------------------------------------------------------------------------- #
def test_domain_lease_blocks_within_interval(conn):
    assert queue.try_domain_lease(conn, "example.com", politeness_ms=2000) is True
    conn.commit()
    assert queue.try_domain_lease(conn, "example.com", politeness_ms=2000) is False
    conn.commit()


def test_domain_lease_reacquire_after_expiry(conn):
    assert queue.try_domain_lease(conn, "example.com", politeness_ms=2000) is True
    conn.commit()
    conn.execute(
        "UPDATE domain_lease SET leased_until = now() - interval '1 second' "
        "WHERE domain='example.com'"
    )
    conn.commit()
    assert queue.try_domain_lease(conn, "example.com", politeness_ms=2000) is True


# --------------------------------------------------------------------------- #
# min_politeness_ms — the robots.txt Crawl-delay floor (migration 036)
# --------------------------------------------------------------------------- #
# The bug this closes: `politeness_ms` is rewritten from the caller on EVERY
# acquire, and FETCH always passes cfg.fetch.politeness_ms, so a slower value
# written for a host survived exactly one fetch. `min_politeness_ms` is written
# only by the robots reader and never by an acquire, so the floor persists.
def _seed_floor(conn, domain, min_ms):
    conn.execute(
        "INSERT INTO domain_lease (domain, min_politeness_ms) VALUES (%s, %s) "
        "ON CONFLICT (domain) DO UPDATE SET min_politeness_ms=EXCLUDED.min_politeness_ms",
        (domain, min_ms),
    )
    conn.commit()


def test_domain_lease_floor_holds_the_host_longer_than_the_caller_asked(conn):
    _seed_floor(conn, "slow.example", 10_000)
    assert queue.try_domain_lease(conn, "slow.example", politeness_ms=2000) is True
    conn.commit()
    # 3s later the caller's own 2s interval has passed; the robots floor has not.
    conn.execute(
        "UPDATE domain_lease SET leased_until = leased_until - interval '3 seconds' "
        "WHERE domain='slow.example'"
    )
    conn.commit()
    assert queue.try_domain_lease(conn, "slow.example", politeness_ms=2000) is False


def test_domain_lease_floor_survives_an_acquire(conn):
    """The regression that motivated the column: an acquire must not lower it."""
    _seed_floor(conn, "renfert.example", 10_000)
    queue.try_domain_lease(conn, "renfert.example", politeness_ms=2000)
    conn.commit()
    row = conn.execute(
        "SELECT politeness_ms, min_politeness_ms FROM domain_lease WHERE domain=%s",
        ("renfert.example",),
    ).fetchone()
    assert row["min_politeness_ms"] == 10_000   # untouched by the acquire
    assert row["politeness_ms"] == 2000         # still the caller's value


def test_domain_lease_without_a_floor_is_unchanged(conn):
    """Default 0 floor: every host without a robots Crawl-delay behaves as before."""
    assert queue.try_domain_lease(conn, "plain.example", politeness_ms=2000) is True
    conn.commit()
    conn.execute(
        "UPDATE domain_lease SET leased_until = now() - interval '1 second' "
        "WHERE domain='plain.example'"
    )
    conn.commit()
    assert queue.try_domain_lease(conn, "plain.example", politeness_ms=2000) is True


# --------------------------------------------------------------------------- #
# lineage (job.caused_by) — which job's handler enqueued this one
# --------------------------------------------------------------------------- #
def test_enqueue_outside_a_job_is_a_root(conn):
    jid = queue.enqueue(conn, "fetch.url", {"url": "x"}, "lineage-root-1")
    row = conn.execute("SELECT caused_by FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["caused_by"] is None


def test_enqueue_during_dispatch_records_the_claiming_job(conn):
    parent = queue.enqueue(conn, "discover.group", {"group_id": 1}, "lineage-p-1")
    queue.current_job_id = parent          # what run_once sets around dispatch
    try:
        child = queue.enqueue(conn, "fetch.url", {"url": "y"}, "lineage-c-1")
    finally:
        queue.current_job_id = None
    row = conn.execute("SELECT caused_by FROM job WHERE id=%s", (child,)).fetchone()
    assert row["caused_by"] == parent


def test_runner_clears_the_slot_even_when_the_handler_raises(conn):
    # A handler that enqueues then raises: the child enqueued during dispatch
    # must still carry the parent id (captured inside the handler, before the
    # raise rolls the insert back), and afterward the slot must be clear again
    # -- a raising handler must not leak its id onto whatever enqueues next.
    parent = queue.enqueue(conn, "discover.group", {"group_id": 2}, "lineage-p-2")
    conn.commit()

    seen_caused_by = []

    def handler(c, job):
        child = queue.enqueue(c, "fetch.url", {"url": "z"}, "lineage-c-2")
        row = c.execute(
            "SELECT caused_by FROM job WHERE id=%s", (child,)
        ).fetchone()
        seen_caused_by.append(row["caused_by"])
        raise RuntimeError("boom")

    assert runner.run_once(conn, "w", handlers={"discover.group": handler}) is True
    assert queue.current_job_id is None
    assert seen_caused_by == [parent]


# --------------------------------------------------------------------------- #
# per-test reset (tests/conftest.py `_RESET_TABLES`)
# --------------------------------------------------------------------------- #
def test_reset_clears_every_table_the_old_cascade_reached(conn):
    """The reset used to be `TRUNCATE ... CASCADE`, which swept referencing
    tables implicitly. DELETE names them, so a migration adding a table with an
    FK into the reset set must fail HERE -- not by leaking rows into whichever
    test happens to run next, which is the failure this replaces and is far
    harder to read.
    """
    from tests.conftest import _RESET_TABLES

    seeds = {
        "job", "domain_lease", "scheduler_run", "import_inbox", "upload_inbox", "email_poll_log",
        "data_anomaly",
        "vendor_master", "manufacturer_alias", "item_mirror", "item_group",
        "item_group_member", "grouping_suggestion", "discovery_log", "document",
        "item_document", "manual_task",
        # EUDAMED (S2.3 stage 2, migration 042) and service_heartbeat (035).
        # None of the four is reachable by FK closure from the roots above:
        # `manufacturer` has no FK in, `eudamed_certificate`/`eudamed_mirror`/
        # `service_heartbeat` carry no FK at all (their "srn" columns are
        # plain text, not references) -- so each has to be seeded directly, or
        # this closure could never include them regardless of what else is
        # reset. `manufacturer_srn` is NOT listed here on purpose: it has a
        # real FK to `manufacturer(canonical_name)`, so seeding the parent is
        # what pulls the child in through the loop below -- an explicit entry
        # here would be redundant with what the closure already proves.
        "manufacturer", "eudamed_certificate", "eudamed_mirror", "service_heartbeat",
        # `playbook_probe` (059) is a leaf by design: both its refs (`slug`,
        # `via_job`) are soft, so the closure cannot reach it either.
        "playbook_probe",
        # `onboarding_state` (060): same shape, `canonical_name` is soft.
        "onboarding_state",
        # The six FK-less leaves added 2026-09-04 (see conftest): each has to
        # be a seed for the same reason as `data_anomaly`.
        "audit_log", "batch_ref", "document_text", "extraction_attempt",
        "extraction_cost", "robots_cache",
        "crawl_link_rank",
        # `bc_push_log` (063): same shape again -- `via_job` soft by invariant
        # 10, `item_ref` plain text.
        "bc_push_log",
        # `alert_state` (064): same shape again.
        "alert_state",
    }
    # `data_anomaly` and `service_heartbeat` are seeds because they have no
    # foreign keys in either direction -- the closure below can never reach
    # them, and the reset list has to name them explicitly or INGEST's anomaly
    # rows and the web/runner liveness beats survive into the next test.
    fks = [
        (r["child"], r["parent"])
        for r in conn.execute(
            "SELECT c.conrelid::regclass::text AS child, "
            "       c.confrelid::regclass::text AS parent "
            "FROM pg_constraint c "
            "WHERE c.contype='f' AND c.connamespace='public'::regnamespace"
        ).fetchall()
    ]

    closure, grew = set(seeds), True
    while grew:
        grew = False
        for child, parent in fks:
            if parent in closure and child not in closure:
                closure.add(child)
                grew = True

    assert set(_RESET_TABLES) == closure

    # Children before parents: nothing deleted may still be referenced by a
    # table deleted later. `document` self-references, which one DELETE handles.
    position = {t: i for i, t in enumerate(_RESET_TABLES)}
    for child, parent in fks:
        if child in position and parent in position and child != parent:
            assert position[child] < position[parent], f"{child} after {parent}"
