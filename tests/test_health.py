"""What the system can notice about itself, and say out loud.

`app/alerts.py` could always PUSH; until now exactly one thing called it, a
dead-lettered job. This is the rest: a service that stopped beating, a queue
that stopped moving, a cron that stopped firing.

**The honest limit, stated here because it belongs next to the tests:** these
checks run as a cron ON the queue, so they cannot report that the queue is
dead. If every worker is gone, nothing evaluates them. That last mile needs an
external dead-man's switch and is recorded in `docs/dev/limits.md`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.health import (
    CRON_SILENT,
    QUEUE_STALLED,
    SERVICE_STALE,
    conditions,
    raise_alerts,
)

NOW = dt.datetime(2026, 9, 7, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def beat(conn):
    """A service heartbeat, aged by `minutes_ago`."""

    def _beat(service: str, *, instance: str = "i1", minutes_ago: int = 0):
        conn.execute(
            "INSERT INTO service_heartbeat (service, instance, last_seen, detail) "
            "VALUES (%s,%s,%s,'{}'::jsonb) "
            "ON CONFLICT (service, instance) DO UPDATE SET last_seen = EXCLUDED.last_seen",
            (service, instance, NOW - dt.timedelta(minutes=minutes_ago)),
        )

    return _beat


def test_a_fresh_heartbeat_raises_nothing(conn, beat):
    beat("worker", minutes_ago=0)
    assert conditions(conn, now=NOW) == []


def test_a_stale_heartbeat_is_a_condition(conn, beat):
    """A worker that stopped beating is the case this whole module exists for."""
    beat("worker", minutes_ago=90)

    got = conditions(conn, now=NOW)

    assert [c["kind"] for c in got] == [SERVICE_STALE]
    assert got[0]["key"] == "service-stale:worker:i1"
    assert "worker" in got[0]["message"]


# --------------------------------------------------------------------------- #
# a queue that stopped moving
# --------------------------------------------------------------------------- #
@pytest.fixture
def pending_job(conn):
    def _job(*, minutes_ago: int, claimed_minutes_ago: int | None = None):
        conn.execute(
            "INSERT INTO job (type, payload, dedupe_key, status, run_after, "
            "created_at, claimed_at) "
            "VALUES ('extract.doc','{}'::jsonb,%s,'pending',%s,%s,%s)",
            (f"k-{minutes_ago}-{claimed_minutes_ago}",
             NOW - dt.timedelta(minutes=minutes_ago),
             NOW - dt.timedelta(minutes=minutes_ago),
             None if claimed_minutes_ago is None
             else NOW - dt.timedelta(minutes=claimed_minutes_ago)),
        )

    return _job


def test_a_queue_that_never_started_is_not_a_stall(conn, beat):
    """A fresh database is not an incident. Migration 055 seeds the eight
    perpetual cron ticks pending and immediately due, so "due work exists" is
    true from the first migration -- with no claim ever made, that is an install
    nobody started rather than a queue that stopped."""
    beat("worker", minutes_ago=0)

    assert [c for c in conditions(conn, now=NOW)
            if c["kind"] == QUEUE_STALLED] == []


def test_work_waiting_while_nothing_is_being_claimed_is_a_stall(conn, pending_job):
    """Due work plus no claim in the window: something is holding the queue,
    and no single job will dead-letter to tell anyone."""
    pending_job(minutes_ago=120, claimed_minutes_ago=90)

    got = [c for c in conditions(conn, now=NOW) if c["kind"] == QUEUE_STALLED]

    assert len(got) == 1
    assert got[0]["key"] == QUEUE_STALLED


def test_work_waiting_but_recently_claimed_is_not_a_stall(conn, pending_job):
    """A busy queue always has due work. Depth is not the signal; movement is."""
    pending_job(minutes_ago=120, claimed_minutes_ago=2)

    assert [c for c in conditions(conn, now=NOW)
            if c["kind"] == QUEUE_STALLED] == []


def test_a_job_not_yet_due_is_not_a_stall(conn, pending_job):
    """Self-deferring crons sit pending with a future `run_after` by design."""
    pending_job(minutes_ago=-120)

    assert [c for c in conditions(conn, now=NOW)
            if c["kind"] == QUEUE_STALLED] == []


# --------------------------------------------------------------------------- #
# a cron that stopped firing
# --------------------------------------------------------------------------- #
def test_a_dead_cron_tick_is_a_condition(conn):
    """The eight crons are perpetual self-deferring jobs. `arm_crons()` re-arms
    them on worker start, so a tick that died stays dead until someone restarts
    a container -- silent, and invisible on every existing screen."""
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status) "
        "VALUES ('scheduler.tick','{\"cron\":\"expiry_scan\"}'::jsonb,"
        "'scheduler.tick:expiry_scan','dead')"
    )

    got = [c for c in conditions(conn, now=NOW) if c["kind"] == CRON_SILENT]

    assert [c["key"] for c in got] == [f"{CRON_SILENT}:expiry_scan"]


def test_a_live_cron_tick_is_not_a_condition(conn):
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, run_after) "
        "VALUES ('scheduler.tick','{\"cron\":\"expiry_scan\"}'::jsonb,"
        "'scheduler.tick:expiry_scan','pending', now() + interval '1 hour')"
    )

    assert [c for c in conditions(conn, now=NOW)
            if c["kind"] == CRON_SILENT] == []


# --------------------------------------------------------------------------- #
# saying it once
# --------------------------------------------------------------------------- #
class Sent:
    """Collects what would have gone to ntfy."""

    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def __call__(self, webhook_url, title, message, **kw):
        self.lines.append((title, message))
        return True


def test_a_new_condition_is_announced(conn, beat):
    beat("worker", minutes_ago=90)
    sent = Sent()

    raise_alerts(conn, webhook_url="https://ntfy.example/t", now=NOW, notify=sent)

    assert len(sent.lines) == 1
    assert "worker" in sent.lines[0][1]


def test_the_same_condition_is_not_announced_twice(conn, beat):
    """Every poll re-evaluates the same world. Without memory a stale worker
    pushes a line every five minutes until someone mutes the channel -- and a
    muted channel is worse than no channel."""
    beat("worker", minutes_ago=90)
    sent = Sent()

    raise_alerts(conn, webhook_url="x", now=NOW, notify=sent)
    raise_alerts(conn, webhook_url="x", now=NOW + dt.timedelta(minutes=5),
                 notify=sent)

    assert len(sent.lines) == 1


def test_a_condition_is_repeated_after_the_cooldown(conn, beat):
    beat("worker", minutes_ago=90)
    sent = Sent()

    raise_alerts(conn, webhook_url="x", now=NOW, notify=sent, cooldown_s=3600)
    raise_alerts(conn, webhook_url="x", now=NOW + dt.timedelta(hours=2),
                 notify=sent, cooldown_s=3600)

    assert len(sent.lines) == 2


def test_recovery_is_announced_and_the_row_cleared(conn, beat):
    """Silence after an alert is ambiguous -- fixed, or forgotten? Saying so
    closes the loop, and is the only way the reader learns the check still runs."""
    beat("worker", minutes_ago=90)
    sent = Sent()
    raise_alerts(conn, webhook_url="x", now=NOW, notify=sent)

    beat("worker", minutes_ago=0)
    raise_alerts(conn, webhook_url="x", now=NOW + dt.timedelta(minutes=5),
                 notify=sent)

    assert len(sent.lines) == 2
    assert "recovered" in sent.lines[1][0].lower()
    assert conn.execute("SELECT count(*) AS n FROM alert_state").fetchone()["n"] == 0


def test_nothing_is_sent_when_no_webhook_is_configured(conn, beat):
    """The default everywhere: tests, CI and a laptop must not post anywhere."""
    beat("worker", minutes_ago=90)
    sent = Sent()

    raise_alerts(conn, webhook_url="", now=NOW, notify=sent)

    assert sent.lines == []
    assert conn.execute("SELECT count(*) AS n FROM alert_state").fetchone()["n"] == 0, (
        "an unconfigured install must not accumulate state it will one day flush"
    )
