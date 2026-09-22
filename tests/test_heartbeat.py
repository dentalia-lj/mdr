"""service_heartbeat: is a long-running process still turning over?

The question the strip on every page has to answer, and the one nothing in this
system could answer before. `docker compose ps` knows, but the web container has
no daemon socket and must never get one, so Postgres -- the one thing every
service already talks to -- is the channel.

Deriving it from the queue was the cheap alternative and it is wrong in exactly
the state the system is in most of the time: with the queue drained, a healthy
idle worker and a dead one both have a stale `max(job.claimed_at)`.
"""
from __future__ import annotations

import datetime as dt

from app import heartbeat


def test_a_beat_records_the_service_and_when(conn):
    heartbeat.beat(conn, "worker", "worker-1")
    conn.commit()

    row = heartbeat.read(conn)["worker"]
    assert row["instance"] == "worker-1"
    assert (dt.datetime.now(dt.timezone.utc) - row["last_seen"]).total_seconds() < 10


def test_beating_again_moves_the_timestamp_and_adds_no_row(conn):
    """One row per (service, instance), upserted. A worker beating every second
    for a week must not leave 600.000 rows behind."""
    heartbeat.beat(conn, "worker", "worker-1")
    first = heartbeat.read(conn)["worker"]["last_seen"]
    conn.execute("UPDATE service_heartbeat SET last_seen = last_seen - interval '5 s'")
    heartbeat.beat(conn, "worker", "worker-1")
    conn.commit()

    rows = conn.execute("SELECT count(*) AS n FROM service_heartbeat").fetchone()
    assert rows["n"] == 1
    assert heartbeat.read(conn)["worker"]["last_seen"] >= first - dt.timedelta(seconds=5)


def test_two_workers_are_two_rows_but_one_service(conn):
    """`docker compose up --scale worker=3` is the documented way to run this,
    so the key is (service, instance) and the reader collapses to the freshest:
    three replicas of which one is wedged still means the service is up."""
    heartbeat.beat(conn, "worker", "worker-1")
    heartbeat.beat(conn, "worker", "worker-2")
    conn.commit()

    assert conn.execute(
        "SELECT count(*) AS n FROM service_heartbeat").fetchone()["n"] == 2
    assert heartbeat.read(conn)["worker"]["instances"] == 2


def test_a_stale_beat_reads_as_down(conn):
    """`stale_after_s` is the caller's, not the table's -- a scheduler ticking
    once a minute and a worker polling every second cannot share one threshold."""
    heartbeat.beat(conn, "worker", "worker-1")
    conn.execute("UPDATE service_heartbeat SET last_seen = now() - interval '90 s'")
    conn.commit()

    assert heartbeat.read(conn, stale_after_s=60)["worker"]["up"] is False
    assert heartbeat.read(conn, stale_after_s=120)["worker"]["up"] is True


def test_a_row_silent_for_a_day_is_pruned_by_the_next_beat(conn):
    """Ghost rows: a replaced container beats under a new instance id and its
    old row would otherwise sit in the table forever, counted as an instance.
    Any beat prunes rows older than PRUNE_AFTER_S; a merely stale row (minutes)
    survives, because staleness is a finding and pruning is housekeeping."""
    conn.execute(
        "INSERT INTO service_heartbeat (service, instance, last_seen) "
        "VALUES ('worker', 'ghost', now() - interval '2 days'), "
        "       ('worker', 'slow',  now() - interval '10 minutes')")
    conn.commit()
    heartbeat.beat(conn, "worker", "live")
    rows = {r["instance"] for r in conn.execute(
        "SELECT instance FROM service_heartbeat WHERE service='worker'").fetchall()}
    assert rows == {"live", "slow"}
    assert heartbeat.read(conn)["worker"]["instances"] == 2


def test_a_service_that_never_beat_is_absent_not_down(conn):
    """Absence and death are different findings. A scheduler that was never
    started (it sits behind the `full` profile) has no row, and reporting that
    as `down` would put a permanent red chip on every page -- which is how a
    strip stops being read."""
    heartbeat.beat(conn, "worker", "worker-1")
    conn.commit()

    seen = heartbeat.read(conn)
    assert "scheduler" not in seen


def test_detail_is_optional_and_round_trips(conn):
    """Somewhere to put what the process wants to say about itself -- the source
    fingerprint the worker already logs at startup, so a stale image is visible
    from the UI rather than only from that first log line."""
    heartbeat.beat(conn, "worker", "worker-1", detail={"source": "abc123"})
    conn.commit()

    assert heartbeat.read(conn)["worker"]["detail"]["source"] == "abc123"


def test_beat_never_raises_on_a_broken_connection():
    """A heartbeat is telemetry. It must not be the thing that kills a worker
    mid-job, so the write is best-effort and failure is logged, not raised."""
    class Dead:
        def execute(self, *a, **k):
            raise RuntimeError("connection is closed")

    heartbeat.beat(Dead(), "worker", "worker-1")  # must not raise


def test_the_instance_id_is_the_container_not_the_pid():
    """`--scale worker=3` gives three containers that are all PID 1, so the
    default `worker-{pid}` makes three replicas write one row and read as a
    single instance. The container hostname is what Docker guarantees unique;
    the queue's `claimed_by` keeps its own naming and travels in `detail`, so
    the dead-jobs board is unaffected."""
    from app.workers import runner

    a = runner.heartbeat_instance("worker-1", hostname="c0ffee111111")
    b = runner.heartbeat_instance("worker-1", hostname="deadbeef2222")
    assert a != b
    assert "c0ffee111111" in a


def test_the_instance_id_falls_back_off_a_container():
    """Run from a shell with no container hostname worth the name, it must
    still produce something stable and non-colliding rather than an empty key."""
    from app.workers import runner

    assert runner.heartbeat_instance("worker-99", hostname="") == "worker-99"


# --------------------------------------------------------------------------- #
# the container's own HEALTHCHECK

def test_healthcheck_passes_on_a_fresh_beat_for_this_container(conn):
    """Each container probes ITS OWN row, not the service's freshest. Checking
    the service would let two healthy replicas mask a third that is wedged --
    the container would report healthy while doing nothing."""
    from app import heartbeat as hb

    hb.beat(conn, "worker", "worker-1@abc123")
    conn.commit()
    assert hb.check(conn, "worker", "worker-1@abc123", stale_after_s=30) is True


def test_healthcheck_fails_when_this_containers_own_beat_is_stale(conn):
    from app import heartbeat as hb

    hb.beat(conn, "worker", "worker-1@abc123")
    conn.execute("UPDATE service_heartbeat SET last_seen = now() - interval '90 s'")
    conn.commit()
    assert hb.check(conn, "worker", "worker-1@abc123", stale_after_s=30) is False


def test_healthcheck_is_not_fooled_by_a_sibling_replica(conn):
    """The bug this exists to prevent: a wedged container whose neighbour is
    beating happily must still fail its own probe."""
    from app import heartbeat as hb

    hb.beat(conn, "worker", "wedged@aaa")
    conn.execute("UPDATE service_heartbeat SET last_seen = now() - interval '90 s'")
    hb.beat(conn, "worker", "healthy@bbb")
    conn.commit()

    assert hb.check(conn, "worker", "healthy@bbb", stale_after_s=30) is True
    assert hb.check(conn, "worker", "wedged@aaa", stale_after_s=30) is False


def test_healthcheck_fails_when_the_container_never_beat(conn):
    """No row is not health. A process that died before its first beat, or one
    whose migration never ran, must fail rather than pass by absence."""
    from app import heartbeat as hb

    assert hb.check(conn, "worker", "never-started@zzz", stale_after_s=30) is False
