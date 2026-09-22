"""The API key must reach the client, and its absence must be loud.

The GC pilot (2026-08-12) enqueued 149 extractions and failed 79 of them with
"Could not resolve authentication method", one job at a time, twenty minutes
after kickoff. The worker container had never carried ANTHROPIC_API_KEY —
`ANTHROPIC_API_KEY` appears in no commit of docker-compose.yml on any branch,
config.py reads only os.environ, and .env is not in the image. Extraction had
only ever run on the host, where the operator's shell had .env loaded; mounting
the corpus into the worker moved it into the container for the first time and
exposed the gap.

Two defects, one symptom:
  * the key was never passed to the container, and
  * a missing key surfaced per-job at call time rather than once at startup,
    because `anthropic.Anthropic()` constructs happily without one.

So: the client is constructed WITH the configured key (a TOML-only key used to
be silently ignored, since the SDK reads the environment and nothing else), and
a worker with no key refuses to start instead of dead-lettering the queue.
"""

from __future__ import annotations

import pytest

from app.handlers import discover as discover_mod
from app.handlers import extract as extract_mod
from app.workers import runner


class _Cfg:
    """Minimal stand-in for the two config sections these paths read."""

    class connection:
        anthropic_api_key = ""
        database_url = "postgresql://unused/unused"

    class models:
        t1 = "claude-haiku-4-5-20251001"
        t2 = "claude-sonnet-5"
        rank = "claude-haiku-4-5-20251001"


def _cfg_with(key: str):
    cfg = _Cfg()
    cfg.connection.anthropic_api_key = key
    return cfg


# --- the key reaches the client -------------------------------------------

def test_default_llm_passes_the_configured_key(monkeypatch):
    seen = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key=None, **kw):
            seen["api_key"] = api_key

    monkeypatch.setattr(extract_mod, "load_config", lambda: _cfg_with("sk-ant-configured"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic",
                        type("m", (), {"Anthropic": _FakeAnthropic}))

    extract_mod._default_llm()

    # Not `anthropic.Anthropic()` bare: that reads only the environment, so a key
    # set in config.toml was silently dropped.
    assert seen["api_key"] == "sk-ant-configured"


def test_default_batch_client_passes_the_configured_key(monkeypatch):
    seen = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key=None, **kw):
            seen["api_key"] = api_key

    monkeypatch.setattr(extract_mod, "load_config", lambda: _cfg_with("sk-ant-batch"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic",
                        type("m", (), {"Anthropic": _FakeAnthropic}))

    extract_mod._default_batch_client()

    assert seen["api_key"] == "sk-ant-batch"


def test_default_rank_passes_the_configured_key(monkeypatch):
    """DISCOVER's T1 ranker is the third live-API seam (`[discover-t1-ranking]`,
    2026-09-02) and shares the defect this file exists for: `_default_rank`
    builds its own client, so a TOML-only key must reach it too."""
    seen = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key=None, **kw):
            seen["api_key"] = api_key

    monkeypatch.setattr(discover_mod, "load_config", lambda: _cfg_with("sk-ant-rank"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic",
                        type("m", (), {"Anthropic": _FakeAnthropic}))
    # No candidates -> the ranker returns before any messages.create, so this
    # asserts construction without needing a stubbed response.
    discover_mod._default_rank(object(), [])

    assert seen["api_key"] == "sk-ant-rank"


# --- absence is loud, and it is loud BEFORE any job is claimed -------------

def test_worker_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.setattr(runner, "load_config", lambda: _cfg_with(""))

    with pytest.raises(RuntimeError) as exc:
        runner.run_forever(worker_id="test-worker")

    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    # The message has to name the fix, not just the fault: the whole point is
    # that the operator learns this at startup rather than from 79 dead jobs.
    assert "docker-compose" in msg or "compose" in msg


def test_worker_refuses_before_touching_the_database(monkeypatch):
    # Ordering matters: the check is worthless if it happens after the worker has
    # connected and started claiming jobs.
    monkeypatch.setattr(runner, "load_config", lambda: _cfg_with(""))

    def _boom(*a, **kw):
        raise AssertionError("connected to the database before checking the key")

    monkeypatch.setattr(runner.db, "connect", _boom)

    with pytest.raises(RuntimeError):
        runner.run_forever(worker_id="test-worker")


def test_worker_starts_when_the_key_is_present(monkeypatch):
    # The guard must not block a correctly configured worker. Stop the loop
    # immediately by making the first claim signal shutdown.
    monkeypatch.setattr(runner, "load_config", lambda: _cfg_with("sk-ant-present"))

    class _Conn:
        # run_forever checks these before it does anything: a lost connection
        # exits the process rather than spinning (tests/test_loop_liveness.py).
        closed = False
        broken = False

        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(runner.db, "connect", lambda url: _Conn())
    monkeypatch.setattr(runner, "run_once", lambda conn, wid: False)
    # One idle pass, then stop.
    calls = {"n": 0}

    def _sleep(_s):
        calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.time, "sleep", _sleep)

    with pytest.raises(KeyboardInterrupt):
        runner.run_forever(worker_id="test-worker", poll_interval_s=0.01)

    assert calls["n"] == 1
