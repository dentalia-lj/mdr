"""A process that cannot reach the database must die, not spin.

The worker's run_forever swallows every exception by design -- `heartbeat.beat`
by contract (app/heartbeat.py:52-56), the job by `run_once`'s try. There is no
reconnect anywhere in app/. So a lost connection left a live process looping
forever over a dead socket, and `restart: unless-stopped` only fires on EXIT.
Compose does not restart on a failing healthcheck and there is no autoheal
service, so nothing else would have recovered it. Dying IS the recovery path.

This covered two loops until 2026-09-02, when the scheduler process was deleted
and its crons became `scheduler.tick` jobs. They are claimed by the worker, so
the worker's guard below is now the crons' guard too.

The tests bound their own sleep: without the guard the loop never returns, and a
hanging test is worse than a failing one. `_Spun` turns "it kept going" into a
legible failure.
"""
from __future__ import annotations

import pytest

from app.workers import runner


class _Spun(Exception):
    """The loop took another lap on a dead connection instead of exiting."""


class _BrokenConn:
    """What psycopg hands back after the server goes away."""

    closed = False
    broken = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Cfg:
    class connection:
        anthropic_api_key = "sk-ant-present"
        database_url = "postgresql://unused/unused"

    class scheduler:
        tick_interval_s = 300


def _bound_the_loop(monkeypatch, module, laps=3):
    """Let it sleep a couple of times, then make the spin visible."""
    calls = {"n": 0}

    def _sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= laps:
            raise _Spun(
                f"{module.__name__} slept {calls['n']}x on a broken connection "
                "instead of raising"
            )

    monkeypatch.setattr(module.time, "sleep", _sleep)
    return calls


def test_worker_exits_when_its_connection_is_gone(monkeypatch):
    worked = {"claimed": False}

    def _claim(*a, **kw):
        worked["claimed"] = True
        return False

    monkeypatch.setattr(runner, "load_config", lambda: _Cfg())
    monkeypatch.setattr(runner.db, "connect", lambda url: _BrokenConn())
    monkeypatch.setattr(runner, "run_once", _claim)
    _bound_the_loop(monkeypatch, runner)

    with pytest.raises(RuntimeError) as exc:
        runner.run_forever(worker_id="test-worker", poll_interval_s=0.01)

    assert "connection" in str(exc.value).lower()
    assert worked["claimed"] is False, "claimed a job on a dead connection"



def test_a_healthy_connection_is_not_mistaken_for_a_dead_one(monkeypatch):
    """The guard must not stop a working process. Regression cover for the
    obvious way to get this wrong: guarding on the wrong attribute."""

    class _LiveConn(_BrokenConn):
        closed = False
        broken = False

    monkeypatch.setattr(runner, "load_config", lambda: _Cfg())
    monkeypatch.setattr(runner.db, "connect", lambda url: _LiveConn())
    monkeypatch.setattr(runner, "run_once", lambda conn, wid: False)
    calls = _bound_the_loop(monkeypatch, runner)

    with pytest.raises(_Spun):
        runner.run_forever(worker_id="test-worker", poll_interval_s=0.01)

    assert calls["n"] == 3
