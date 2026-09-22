"""scheduler.tick — one perpetual job per cron."""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from app import queue
from app.handlers import HANDLERS
from app.handlers import scheduler_tick as tick_mod

#: The crons live TODAY. Hand-maintained on purpose: comparing the production
#: dict to itself proves nothing, so this is the second opinion that makes
#: `test_every_cron_is_dispatchable` a real assertion rather than a tautology.
#: Adding a cron means editing this list, and that is the point.
CRONS = [
    "ingest.monthly",
    "expiry-scan",
    "failure-monitor",
    "coverage-scan",
    "report.weekly",
    "email.poll",
    "eudamed.certregister",
    "eudamed.sweep-due",
    "health-watch",
    "bc.push-drift",
]

#: What migration 055 SEEDED, on 2026-08-31. A migration is a historical
#: artifact and cannot grow a row for a cron invented after it ran, so pinning
#: it to `CRONS` asserted something that had to become false -- and did, the
#: first time a cron was added (`coverage-scan`, 2026-09-02). `arm_crons` is
#: what covers the difference, on every worker start; the test below is what
#: pins that it actually does.
SEEDED_CRONS = [
    "ingest.monthly",
    "expiry-scan",
    "failure-monitor",
    "report.weekly",
    "email.poll",
    "eudamed.certregister",
    "eudamed.sweep-due",
]

_SEED = (pathlib.Path(__file__).resolve().parents[1]
         / "migrations" / "055_scheduler_tick_seed.sql")


def _enqueue(conn, cron):
    job_id = queue.enqueue(
        conn, "scheduler.tick", {"cron": cron},
        f"scheduler.tick:{cron}", priority="delta",
    )
    return conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()


# --------------------------------------------------------------------------- #
# the seed (migration 055)
# --------------------------------------------------------------------------- #
def test_the_seed_arms_every_cron(conn):
    """Runs 055's own SQL rather than trusting migration state.

    `job` is in conftest's _RESET_TABLES, so rows the migration inserted are
    deleted before every test. Executing the shipped file is what actually
    tests the shipped file.
    """
    # Clear first: migration 064 seeds a ninth cron at migration time, and these tests are about what 055's OWN sql arms. Until 064 existed the leftover migration rows happened to equal 055's set exactly, so the omission passed for the wrong reason.
    conn.execute("DELETE FROM job WHERE type='scheduler.tick'")
    conn.execute(_SEED.read_text())

    rows = conn.execute(
        "SELECT payload->>'cron' AS cron, dedupe_key, status, priority "
        "  FROM job WHERE type='scheduler.tick' ORDER BY 1"
    ).fetchall()

    assert sorted(r["cron"] for r in rows) == sorted(SEEDED_CRONS)
    for r in rows:
        assert r["dedupe_key"] == f"scheduler.tick:{r['cron']}"
        assert r["status"] == "pending"
        # interactive is for human-triggered work; sweep would let a daily scan
        # starve for hours behind a backlog.
        assert r["priority"] == "delta"


def test_the_seed_is_safe_to_run_twice(conn):
    # Clear first: migration 064 seeds a ninth cron at migration time, and these tests are about what 055's OWN sql arms. Until 064 existed the leftover migration rows happened to equal 055's set exactly, so the omission passed for the wrong reason.
    conn.execute("DELETE FROM job WHERE type='scheduler.tick'")
    conn.execute(_SEED.read_text())
    conn.execute(_SEED.read_text())

    n = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]
    assert n == len(SEEDED_CRONS)


def test_the_seed_is_not_claimable_during_a_container_swap(conn):
    """`migrate` finishes before the NEW worker starts, but the OLD worker is
    still polling once a second while it runs -- and that worker has no
    scheduler.tick handler.

    Reproduced against a real database on 2026-08-31: one claim left
    `status=failed attempts=1` with `LookupError: no handler registered for
    scheduler.tick`. Five of those and a cron is dead before it ever ran.
    """
    # Clear first: migration 064 seeds a ninth cron at migration time, and these tests are about what 055's OWN sql arms. Until 064 existed the leftover migration rows happened to equal 055's set exactly, so the omission passed for the wrong reason.
    conn.execute("DELETE FROM job WHERE type='scheduler.tick'")
    conn.execute(_SEED.read_text())

    rows = conn.execute(
        "SELECT run_after > now() + interval '1 minute' AS out_of_reach "
        "  FROM job WHERE type='scheduler.tick'"
    ).fetchall()

    assert rows, "seed produced no rows"
    assert all(r["out_of_reach"] for r in rows), (
        "the seed is claimable immediately; an old worker mid-deploy will "
        "dead-letter these before the new image is up"
    )


# --------------------------------------------------------------------------- #
# the handler
# --------------------------------------------------------------------------- #
def test_every_cron_is_dispatchable():
    assert "scheduler.tick" in HANDLERS
    assert set(tick_mod.POLL_SECONDS) == set(CRONS)
    assert set(tick_mod.CRONS) == set(CRONS)


def test_a_cron_registered_only_in_the_legacy_orchestrator_is_not_live():
    """`app/scheduler.py::tick()` is dead: nothing in `app/` or `web/` calls it,
    only tests do. It still LOOKS like the place a cron is registered, and on
    2026-09-02 `coverage-scan` was added there alone -- it passed every test,
    read as wired in a live worker's own config dump, and would never have run.

    So the two registries must agree. If `tick()` is ever deleted this test goes
    with it; what must not happen is the two drifting quietly again.
    """
    import inspect
    import re
    from app import scheduler

    src = inspect.getsource(scheduler.tick)
    for cron in tick_mod.CRONS:
        assert f'"{cron}"' in src, (
            f"{cron} dispatches via scheduler.tick but is missing from "
            f"app.scheduler.tick() -- the two cron registries have drifted")
    # `("name", lambda: _tick_x(...))`, including the multi-line spellings.
    legacy = set(re.findall(r'\(\s*"([\w.\-]+)"\s*,\s*lambda', src, re.S))
    assert legacy, "could not parse any cron out of scheduler.tick(); fix the test"

    unknown = legacy - set(tick_mod.CRONS)
    assert not unknown, (
        f"app.scheduler.tick() runs {sorted(unknown)}, which no scheduler.tick "
        f"job dispatches -- registering there alone means the cron never fires")


def test_the_handler_runs_the_named_cron_and_defers(conn, monkeypatch):
    seen = {}

    def _fake(conn, cfg, now):
        seen["ran"] = True
        return {"fired": True}

    monkeypatch.setitem(tick_mod.CRONS, "report.weekly", _fake)
    job = _enqueue(conn, "report.weekly")

    result = tick_mod.handle_scheduler_tick(conn, job)

    assert seen["ran"] is True
    assert result["_deferred"] is True
    assert result["cron"] == "report.weekly"
    row = conn.execute(
        "SELECT status, run_after > now() AS later FROM job WHERE id=%s", (job["id"],)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["later"] is True


def test_an_unknown_cron_fails_loudly(conn):
    job = _enqueue(conn, "not-a-cron")

    with pytest.raises(LookupError):
        tick_mod.handle_scheduler_tick(conn, job)


def test_it_dispatches_through_the_real_runner(conn):
    from app.workers import runner

    _enqueue(conn, "report.weekly")
    conn.commit()

    # a self-defer moves nothing forward, so run_once reports "nothing to do"
    assert runner.run_once(conn, "test-worker") is False

    row = conn.execute(
        "SELECT status FROM job WHERE dedupe_key='scheduler.tick:report.weekly'"
    ).fetchone()
    assert row["status"] == "pending"   # deferred, not finished


def test_one_broken_cron_does_not_stop_the_others(conn, monkeypatch):
    """Isolation is structural now: separate jobs, separate backoff. This is
    what tick()'s try/except used to provide inside one loop."""
    from app.workers import runner

    def _boom(conn, cfg, now):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(tick_mod.CRONS, "failure-monitor", _boom)
    _enqueue(conn, "failure-monitor")
    _enqueue(conn, "report.weekly")
    conn.commit()

    runner.run_once(conn, "test-worker")
    runner.run_once(conn, "test-worker")

    rows = {
        r["dedupe_key"]: r["status"]
        for r in conn.execute(
            "SELECT dedupe_key, status FROM job WHERE type='scheduler.tick'"
        ).fetchall()
    }
    assert rows["scheduler.tick:failure-monitor"] in ("failed", "dead")
    assert rows["scheduler.tick:report.weekly"] == "pending"


def test_the_ledger_still_gates_firing(conn):
    """Two polls inside one period fire once. run_after is a poll interval, not
    a fire time -- scheduler_run stays the authority."""
    job = _enqueue(conn, "report.weekly")

    first = tick_mod.handle_scheduler_tick(conn, job)
    second = tick_mod.handle_scheduler_tick(conn, job)

    assert first["fired"] is True
    assert second["fired"] is False
    n = conn.execute(
        "SELECT count(*) AS n FROM scheduler_run WHERE name='report.weekly'"
    ).fetchone()["n"]
    assert n == 1


def test_the_old_process_and_the_new_job_do_not_double_fire(conn):
    """Tasks 3-5 ship while the task-1 scheduler process is still running.

    Both compute the same period key and consult the same ledger, so whichever
    gets there first fires and the other is a no-op. This is what makes the
    change safe to deploy without a maintenance window.
    """
    from app import scheduler as sched

    now = dt.datetime.now(dt.timezone.utc)
    from_process = sched._tick_weekly_report(conn, now=now)

    job = _enqueue(conn, "report.weekly")
    from_queue = tick_mod.handle_scheduler_tick(conn, job)

    assert from_process["fired"] is True
    assert from_queue["fired"] is False
    n = conn.execute(
        "SELECT count(*) AS n FROM scheduler_run WHERE name='report.weekly'"
    ).fetchone()["n"]
    assert n == 1


# --------------------------------------------------------------------------- #
# re-arming (worker startup)
# --------------------------------------------------------------------------- #
def test_arming_is_a_noop_when_every_cron_is_live(conn):
    from app.workers import runner

    assert runner.arm_crons(conn) == len(CRONS)
    before = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]

    assert runner.arm_crons(conn) == 0

    after = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]
    assert after == before == len(CRONS)


def test_a_dead_cron_is_rearmed(conn):
    """`dead` is terminal and outside the active-only dedupe index, so the key
    is free and the same enqueue revives the cron. This is the whole reason a
    dead-lettered cron does not stay dead until someone notices."""
    from app.workers import runner

    runner.arm_crons(conn)
    conn.execute(
        "UPDATE job SET status='dead' "
        " WHERE type='scheduler.tick' AND dedupe_key='scheduler.tick:report.weekly'")

    assert runner.arm_crons(conn) == 1

    row = conn.execute(
        "SELECT count(*) AS n FROM job "
        " WHERE type='scheduler.tick' AND status='pending' "
        "   AND dedupe_key='scheduler.tick:report.weekly'").fetchone()
    assert row["n"] == 1


def test_arming_covers_exactly_the_crons_the_handler_knows(conn):
    """A cron added to the handler but not armed would never run, and one armed
    but unknown to the handler would dead-letter. Same set, both ways."""
    from app.workers import runner

    runner.arm_crons(conn)

    armed = {r["cron"] for r in conn.execute(
        "SELECT payload->>'cron' AS cron FROM job WHERE type='scheduler.tick'"
    ).fetchall()}
    assert armed == set(tick_mod.CRONS)
