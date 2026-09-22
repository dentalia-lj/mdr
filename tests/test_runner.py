"""Worker dispatch loop: claim -> handler -> finish, with the failure path.

The handler's writes and the finish must be atomic; a raising handler must not
leave the job stuck running — it dead-letters through the backoff path.
"""

from __future__ import annotations

import pytest
import subprocess
import sys

from app import queue
from app.workers import runner


def test_runner_wires_real_handlers_over_noops():
    # In a fresh interpreter, importing the worker must register the REAL handlers
    # (not the S0.1 no-ops) for the stages that have landed. A subprocess isolates
    # it from other test modules that may have imported the handler packages.
    code = (
        "import app.workers.runner;"
        "from app.handlers import HANDLERS;"
        "m = {t: HANDLERS[t].__module__ for t in "
        "  ['ingest.run','extract.doc','validate.doc','gate.candidate','gate.apply','backfill.scan','resolve.group','discover.group','fetch.url','report.weekly','upload.ingest','email.poll']};"
        "assert m['ingest.run'] == 'app.handlers.ingest', m;"
        "assert m['extract.doc'] == 'app.handlers.extract', m;"
        "assert m['validate.doc'] == 'app.handlers.validate', m;"
        "assert m['gate.candidate'] == 'app.handlers.gate', m;"
        "assert m['gate.apply'] == 'app.handlers.gate', m;"
        "assert m['backfill.scan'] == 'app.handlers.backfill', m;"
        "assert m['resolve.group'] == 'app.handlers.resolve', m;"
        "assert m['discover.group'] == 'app.handlers.discover', m;"
        "assert m['fetch.url'] == 'app.handlers.fetch', m;"
        "assert m['report.weekly'] == 'app.handlers.report', m;"
        "assert m['upload.ingest'] == 'app.handlers.upload', m;"
        "assert m['email.poll'] == 'app.handlers.email_poll', m;"
        "print('wired')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "wired" in out.stdout


def test_run_once_returns_false_when_empty(conn):
    assert runner.run_once(conn, "w", handlers={}) is False


def test_run_once_dispatches_and_finishes(conn):
    seen = []

    def handler(c, job):
        seen.append(job["id"])

    jid = queue.enqueue(conn, "ingest.run", {"x": 1}, "r1")
    conn.commit()

    assert runner.run_once(conn, "w", handlers={"ingest.run": handler}) is True
    assert seen == [jid]
    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (jid,)
    ).fetchone()["status"] == "done"


def test_run_once_failing_handler_marks_failed(conn):
    def boom(c, job):
        raise RuntimeError("kaboom")

    jid = queue.enqueue(conn, "ingest.run", {}, "r2")
    conn.commit()

    assert runner.run_once(conn, "w", handlers={"ingest.run": boom}) is True
    row = conn.execute(
        "SELECT status, last_error FROM job WHERE id=%s", (jid,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "kaboom" in row["last_error"]


def test_run_once_unregistered_type_dead_letters_via_backoff(conn):
    jid = queue.enqueue(conn, "ingest.run", {}, "r3", max_attempts=1)
    conn.commit()
    # empty registry -> no handler -> failure path; max_attempts=1 -> dead first go
    assert runner.run_once(conn, "w", handlers={}) is True
    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (jid,)
    ).fetchone()["status"] == "dead"


def test_run_once_deferred_handler_skips_finish_and_commits_writes(conn):
    # A batch-mode handler that self-defers (waiting on a Batch API result) must
    # NOT be finished — the job stays reschedulable — and its side-table writes
    # (batch_ref) must commit, or C9 would resubmit on the next poll.
    def deferring(c, job):
        c.execute(
            "INSERT INTO batch_ref (content_hash, tier, batch_id) "
            "VALUES ('h-defer', 'T1', 'batch_1')"
        )
        queue.defer(c, job["id"], 60)
        return {"_deferred": True, "waiting_on": "T1"}

    jid = queue.enqueue(conn, "extract.doc", {}, "d1")
    conn.commit()

    # False = "nothing moved forward, back off" (2026-08-21). This test's
    # subject is the finish-skip and the committed side-table write; the return
    # value is incidental to it and is pinned by
    # test_a_self_deferred_job_reports_no_progress_so_the_loop_backs_off.
    assert runner.run_once(conn, "w", handlers={"extract.doc": deferring}) is False

    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (jid,)
    ).fetchone()["status"] == "pending"  # deferred, not done
    assert conn.execute(
        "SELECT batch_id FROM batch_ref WHERE content_hash='h-defer'"
    ).fetchone()["batch_id"] == "batch_1"  # write committed despite no finish


def test_run_once_handler_writes_are_atomic_with_finish(conn):
    # A handler that writes then the finish both commit together: the write is
    # visible exactly when the job is done.
    def writer(c, job):
        c.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
            "VALUES ('ACME co', 'ACME')"
        )

    queue.enqueue(conn, "resolve.group", {}, "r4")
    conn.commit()
    runner.run_once(conn, "w", handlers={"resolve.group": writer})
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer_alias WHERE raw_name='ACME co'"
    ).fetchone()["canonical_name"] == "ACME"


def test_an_unbuilt_stage_dead_letters_instead_of_reporting_success(conn):
    """A placeholder that returns makes the job finish `done`, which is
    indistinguishable from the work having been performed. Three producers
    already exist for two unbuilt tags (`email.request` from the expiry scan and
    from DISCOVER's email rung, `playbook.reonboard` from the failure monitor),
    each behind a config flag defaulting off -- so the cost of this was one
    flipped flag away from silently swallowing renewal requests.
    """
    from app.handlers import HANDLERS
    from app.handlers.noop import UnimplementedStage
    import app.workers.runner  # noqa: F401  -- registers the real handlers

    # email.request / email.reminder left this list on 2026-08-20 when the S2.4
    # outbound handlers landed (app/handlers/email_request.py); eudamed.sync on
    # 2026-08-25 (app/handlers/eudamed.py); eudamed.certregister left it with
    # the same file's handle_eudamed_certregister (task 4, `register()`'d at
    # app/handlers/eudamed.py:435). eudamed.sweep left it in task 10, same file's
    # handle_eudamed_sweep.
    # playbook.reonboard left this list on 2026-09-03: its PROBE branch landed
    # (app/handlers/playbook_probe.py, S2.2). Every tag now has a real handler,
    # so there is nothing left for the placeholder to catch and this loop is
    # empty -- kept, rather than deleted, because the next new tag reinstates
    # it and the reasoning above is what that tag's author needs to read.
    unbuilt: list[str] = []
    for tag in unbuilt:
        with pytest.raises(UnimplementedStage, match=tag):
            HANDLERS[tag](conn, {"id": 1, "type": tag, "payload": {}})

    # The half of reonboard that is still unbuilt raises from inside the REAL
    # handler rather than from the placeholder, for the same reason: a
    # scheduler-enqueued reonboard job must dead-letter, not report success.
    with pytest.raises(NotImplementedError, match="not built"):
        HANDLERS["playbook.reonboard"](
            conn, {"id": 1, "type": "playbook.reonboard",
                   "payload": {"manufacturer": "ACME"}})


def test_every_built_stage_still_handles_without_raising():
    """The other half of the guard: the twelve landed tags must NOT be reached
    by the placeholder, or this change would dead-letter the whole pipeline."""
    from app.handlers import HANDLERS
    from app.handlers import noop
    import app.workers.runner  # noqa: F401

    still_placeholder = [t for t in noop.JOB_TYPES if HANDLERS[t] is noop._noop]
    # Empty since 2026-09-03: playbook.reonboard was the last one, and its probe
    # branch landed with S2.2. The placeholder module stays -- it is what a NEW
    # tag gets between the migration that adds it and the handler that serves
    # it, and that gap is exactly when a silent no-op would do the damage.
    assert still_placeholder == []


def test_every_job_type_in_the_enum_has_a_handler(conn):
    """The dispatch table and the closed enum must be the same set.

    `tests/test_web.py::test_job_type_lists_match_db_enum` guards the web's two
    lists against the enum; nothing guarded HANDLERS, which is the one that
    decides whether a queued job runs. A tag in the enum with no handler
    dead-letters every job of that type; a handler for a tag not in the enum can
    never be enqueued at all, so it is decoration -- the same shape as
    `coverage-scan` registered only in the orchestrator nothing calls
    (`[scheduler-tick-is-dead-code]`, 2026-09-02).

    Imports `runner`, deliberately: it is what imports every handler module, so
    this asserts the set a production worker actually has, not the subset
    pytest's collection order happened to import.
    """
    from app.handlers import HANDLERS
    import app.workers.runner  # noqa: F401 - populates HANDLERS the way a worker does

    enum_values = {r["v"] for r in conn.execute(
        "SELECT unnest(enum_range(NULL::job_type))::text AS v").fetchall()}

    assert set(HANDLERS) == enum_values, (
        f"unhandled enum tags (jobs would dead-letter): "
        f"{sorted(enum_values - set(HANDLERS))}; "
        f"handlers for tags nothing can enqueue: {sorted(set(HANDLERS) - enum_values)}")


def test_noop_job_types_match_the_db_enum(conn):
    """`noop.JOB_TYPES` calls itself "the closed job_type set" and nothing
    checked it.

    The sibling guards cover the other two hand-maintained mirrors --
    `test_job_type_lists_match_db_enum` for the web's lists,
    `test_every_job_type_in_the_enum_has_a_handler` for HANDLERS. This tuple
    escaped both, because a tag missing from it is invisible while that tag's
    real handler imports fine: `scheduler.tick` was added by migration 054 and
    never added here, so for the whole life of the cron-on-the-queue design a
    `scheduler.tick` job whose handler module failed to import would have found
    no entry at all instead of the loud placeholder this module exists to be.
    """
    from app.handlers import noop

    enum_values = {r["v"] for r in conn.execute(
        "SELECT unnest(enum_range(NULL::job_type))::text AS v").fetchall()}

    assert set(noop.JOB_TYPES) == enum_values, (
        f"enum tags with no placeholder: {sorted(enum_values - set(noop.JOB_TYPES))}; "
        f"placeholders for tags nothing can enqueue: "
        f"{sorted(set(noop.JOB_TYPES) - enum_values)}")


def test_importing_noop_after_a_real_handler_does_not_clobber_it():
    """`noop` used to `register()` unconditionally, so importing it AFTER a
    handler module replaced that handler with the placeholder. `runner.py`
    imports noop first and never saw it; pytest's collection order does the
    opposite, which put `extract.doc` and `backfill.scan` on the placeholder
    for the whole suite. Silent while the placeholder returned; a dead-lettered
    pipeline once it raises.
    """
    code = (
        "import app.handlers.extract;"                      # real handler FIRST
        "import app.handlers.noop;"                         # placeholder second
        "from app.handlers import HANDLERS;"
        "assert HANDLERS['extract.doc'].__module__ == 'app.handlers.extract', "
        "  HANDLERS['extract.doc'].__module__;"
        "assert HANDLERS['eudamed.sync'].__module__ == 'app.handlers.noop'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_self_deferred_job_reports_no_progress_so_the_loop_backs_off(conn):
    """`run_once` returns False for a self-defer, which is what makes
    `run_forever` sleep instead of re-claiming immediately.

    Measured shape of the problem: `_CLAIMABLE` gates on `run_after`, so a
    deferred job is invisible for its defer window -- but when a whole backlog
    shares one domain lease, every job in it becomes claimable again together.
    One acquires the lease, the rest each cost a claim (UPDATE, attempts+1) and
    a defer (UPDATE, attempts-1) and are immediately reclaimable. At the 2000ms
    default that is ~499 wasted UPDATE pairs every 2 seconds, sustained for the
    ~17 minutes a 500-URL backlog takes to drain politely.
    """
    def deferring(c, job):
        queue.defer(c, job["id"], 60)
        return {"_deferred": True}

    queue.enqueue(conn, "extract.doc", {}, "d-backoff")
    conn.commit()

    assert runner.run_once(conn, "w", handlers={"extract.doc": deferring}) is False


def test_a_lease_blocked_fetch_reports_no_progress(conn):
    """The real starvation case, end to end through the registered handler: the
    lease check precedes the transport call, so this never touches the network."""
    from app import queue as q

    assert q.try_domain_lease(conn, "leased.example", 60_000) is True   # held by "someone else"
    queue.enqueue(conn, "fetch.url",
                  {"url": "https://leased.example/a.pdf", "domain": "leased.example",
                   "group_id": None, "source_rank": "test"},
                  "fetch:leased-1")
    conn.commit()

    assert runner.run_once(conn, "w") is False
    assert conn.execute(
        "SELECT status FROM job WHERE dedupe_key='fetch:leased-1'"
    ).fetchone()["status"] == "pending"


# --------------------------------------------------------------------------- #
# the config actually reaches the queue
# --------------------------------------------------------------------------- #
# Live defect 2026-09-02 -> 2026-09-04. `queue.claim` and `queue.fail` declare
# their tuning as parameters with their own defaults, so a caller that passes
# nothing gets `queue.py`'s numbers and every `QUEUE_*` env var is inert. The
# config default was raised 300 -> 1800 to stop a 38-page `eudamed.sweep` being
# reclaimed mid-transaction (job 35679, reclaimed at exactly 301s, rolling back
# every page already stored) and the running worker kept reclaiming at 300,
# because `run_once` called `queue.claim(conn, worker_id)` and passed nothing.
# The old tests could not see it: they assert on `queue.claim`'s own behaviour
# when given a value, never on what the runner hands it.

def test_run_once_hands_the_configured_visibility_timeout_to_claim(conn):
    """The regression guard. Records what `claim` was actually called with."""
    from app import queue as q
    from app.config import Queue

    seen = {}
    real_claim = q.claim

    def spy(c, worker_id, types=None, visibility_timeout_s=300):
        seen["visibility_timeout_s"] = visibility_timeout_s
        return real_claim(c, worker_id, types, visibility_timeout_s)

    runner.queue.claim = spy
    try:
        runner.run_once(conn, "w", handlers={}, queue_cfg=Queue(visibility_timeout_s=1800))
    finally:
        runner.queue.claim = real_claim

    assert seen["visibility_timeout_s"] == 1800, (
        "run_once dropped the configured timeout and fell back to queue.py's default")


def test_run_once_hands_the_configured_backoff_to_fail(conn):
    """Same defect, the other call site: `fail`'s two backoff knobs."""
    from app import queue as q
    from app.config import Queue

    seen = {}
    real_fail = q.fail

    def spy(c, job_id, error, *, backoff_base_s=5, backoff_cap_s=3600):
        seen.update(base=backoff_base_s, cap=backoff_cap_s)
        return real_fail(c, job_id, error,
                         backoff_base_s=backoff_base_s, backoff_cap_s=backoff_cap_s)

    def boom(c, job):
        raise RuntimeError("nope")

    queue.enqueue(conn, "ingest.run", {}, "cfg-backoff")
    conn.commit()
    runner.queue.fail = spy
    try:
        runner.run_once(conn, "w", handlers={"ingest.run": boom},
                        queue_cfg=Queue(backoff_base_s=11, backoff_cap_s=222))
    finally:
        runner.queue.fail = real_fail

    assert seen == {"base": 11, "cap": 222}, (
        "run_once dropped the configured backoff and fell back to queue.py's defaults")


# --------------------------------------------------------------------------- #
# the dead-letter alert — the only push notification in the system
# --------------------------------------------------------------------------- #
# Before 2026-09-04 nothing in app/ or web/ could reach a human: a job that
# dead-lettered on a Friday evening sat unseen until someone opened /dead.
# These pin WHEN it fires, because the failure modes are asymmetric -- a missed
# alert costs a delay, an alert on every retry teaches the reader to ignore the
# channel, and an alert that raises turns a contained failure into an outage.

def _alert_spy(monkeypatch):
    sent = []
    monkeypatch.setattr(runner.alerts, "notify",
                        lambda url, title, msg, **kw: sent.append((url, title, msg, kw)) or True)
    return sent


def test_a_dead_lettered_job_alerts(conn, monkeypatch):
    sent = _alert_spy(monkeypatch)

    def boom(c, job):
        raise RuntimeError("nope")

    queue.enqueue(conn, "ingest.run", {}, "alert-dead")
    conn.execute("UPDATE job SET attempts = max_attempts WHERE dedupe_key='alert-dead'")
    conn.commit()
    runner.run_once(conn, "w", handlers={"ingest.run": boom},
                    alert_url="https://ntfy.sh/t")

    assert len(sent) == 1, "a dead-lettered job must reach a person"
    url, title, msg, kw = sent[0]
    assert url == "https://ntfy.sh/t"
    assert "ingest.run" in title and "dead" in title.lower()
    assert "RuntimeError" in msg and "/dead" in msg
    assert kw["priority"] == runner.alerts.HIGH


def test_a_retryable_failure_does_not_alert(conn, monkeypatch):
    """`failed` runs again on its own. Alerting on it would page a person for a
    transient 404 and train them to ignore the channel."""
    sent = _alert_spy(monkeypatch)

    def boom(c, job):
        raise RuntimeError("transient")

    queue.enqueue(conn, "ingest.run", {}, "alert-retry")
    conn.commit()
    runner.run_once(conn, "w", handlers={"ingest.run": boom},
                    alert_url="https://ntfy.sh/t")

    assert sent == [], "a job with retries left is not news"


def test_no_alert_url_means_no_notify_call(conn, monkeypatch):
    sent = _alert_spy(monkeypatch)

    def boom(c, job):
        raise RuntimeError("nope")

    queue.enqueue(conn, "ingest.run", {}, "alert-off")
    conn.execute("UPDATE job SET attempts = max_attempts WHERE dedupe_key='alert-off'")
    conn.commit()
    runner.run_once(conn, "w", handlers={"ingest.run": boom})
    assert sent == []


def test_a_failing_alert_never_breaks_the_dead_letter(conn, monkeypatch):
    """The contract. `notify` swallows its own errors, but if it ever stopped,
    this is what would catch it: the job must still be `dead` and run_once must
    still return True."""
    def explode(*a, **kw):
        raise RuntimeError("ntfy is down")

    monkeypatch.setattr(runner.alerts, "notify", explode)

    def boom(c, job):
        raise RuntimeError("nope")

    queue.enqueue(conn, "ingest.run", {}, "alert-explodes")
    conn.execute("UPDATE job SET attempts = max_attempts WHERE dedupe_key='alert-explodes'")
    conn.commit()

    with pytest.raises(RuntimeError):
        runner.run_once(conn, "w", handlers={"ingest.run": boom},
                        alert_url="https://ntfy.sh/t")
    # The point: the dead-letter itself committed before the alert was attempted.
    assert conn.execute(
        "SELECT status FROM job WHERE dedupe_key='alert-explodes'"
    ).fetchone()["status"] == "dead"
