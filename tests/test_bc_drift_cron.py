"""The cron that stops Business Central drifting to a stale `true`.

Nothing about these fields is stable: a document expires with no one touching
it, a certificate is suspended, a link is retracted, a gate decision lands. A
push-on-demand-only design would leave BC holding whatever was true the day
somebody last pressed the button -- and 86 production documents were already
past expiry on 2026-09-04.

Selection is a rolling window, oldest-pushed first, not change detection. Expiry
moves with time alone, so every item has to come round again anyway, and
`bc.push` is a no-op when nothing differs: an unchanged item costs one diff
query and no PATCH.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.scheduler import _tick_bc_push_drift

NOW = dt.datetime(2026, 9, 7, 3, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def processed_item(conn):
    def _seed(*, pushed_minutes_ago: int | None = None):
        suffix = uuid.uuid4().hex[:12]
        item_ref = f"DR-{suffix}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
            "md_flag, catalogue, updated_at) "
            "VALUES (%s,'W','t',%s,TRUE,'LJ',now())", (item_ref, f"R-{suffix}"))
        doc_id = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, validity_to, "
            "status, content_hash, archive_url, coverage_scope) "
            "VALUES ('DoC','MDR','2022-01-01','2031-01-01','production',%s,"
            "'/a.pdf','group') RETURNING doc_id", (f"h-{suffix}",)).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list','production')", (item_ref, doc_id))
        if pushed_minutes_ago is not None:
            conn.execute(
                "INSERT INTO bc_push_log (item_ref, field, new_value, http_status, "
                "pushed_at) VALUES (%s,'pteWarehouseURL','u',204,%s)",
                (item_ref, NOW - dt.timedelta(minutes=pushed_minutes_ago)))
        return item_ref

    return _seed


def _enqueued(conn):
    return [r["payload"]["item_refs"] for r in conn.execute(
        "SELECT payload FROM job WHERE type='bc.push' ORDER BY id").fetchall()]


def test_it_enqueues_nothing_while_writing_is_off(conn, processed_item):
    """The gate is one flag, and it stops the work being CREATED as well as
    sent -- a queue full of withheld pushes is noise, not safety."""
    processed_item()

    result = _tick_bc_push_drift(conn, enabled=False, batch=200, cap=1000, now=NOW)

    assert result["enqueued"] == 0
    assert _enqueued(conn) == []


def test_an_item_never_pushed_goes_first(conn, processed_item):
    never = processed_item()
    recent = processed_item(pushed_minutes_ago=1)

    _tick_bc_push_drift(conn, enabled=True, batch=200, cap=1, now=NOW)

    assert _enqueued(conn) == [[never]]
    assert recent not in _enqueued(conn)[0]


def test_the_oldest_push_comes_round_before_a_newer_one(conn, processed_item):
    old = processed_item(pushed_minutes_ago=10_000)
    processed_item(pushed_minutes_ago=5)

    _tick_bc_push_drift(conn, enabled=True, batch=200, cap=1, now=NOW)

    assert _enqueued(conn) == [[old]]


def test_the_cap_bounds_one_run(conn, processed_item):
    """A rolling sweep over 15.958 items must not become one run."""
    for _ in range(3):
        processed_item()

    result = _tick_bc_push_drift(conn, enabled=True, batch=200, cap=2, now=NOW)

    assert result["enqueued"] == 2


def test_a_run_is_split_into_batches(conn, processed_item):
    for _ in range(3):
        processed_item()

    _tick_bc_push_drift(conn, enabled=True, batch=2, cap=3, now=NOW)

    assert [len(b) for b in _enqueued(conn)] == [2, 1]


def test_an_unprocessed_item_is_never_selected(conn):
    """`bc.push` would skip it anyway; selecting it would spend the cap on
    items that can produce no write."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) "
        "VALUES ('DR-BARE','W','t','R',TRUE,'LJ',now())")

    result = _tick_bc_push_drift(conn, enabled=True, batch=200, cap=10, now=NOW)

    assert result["enqueued"] == 0


def test_running_twice_does_not_double_enqueue(conn, processed_item):
    """Same day, same batch: the dedupe key is what stops a second tick piling
    a duplicate push onto an item already waiting."""
    processed_item()

    _tick_bc_push_drift(conn, enabled=True, batch=200, cap=10, now=NOW)
    _tick_bc_push_drift(conn, enabled=True, batch=200, cap=10, now=NOW)

    assert len(_enqueued(conn)) == 1


@pytest.mark.parametrize("write, drift, expect", [
    ("false", "false", 0),
    ("true", "false", 0),
    ("false", "true", 0),
    ("true", "true", 1),
])
def test_the_live_cron_needs_writes_and_its_own_switch(conn, processed_item,
                                                       monkeypatch, write, drift,
                                                       expect):
    """Writes can be on for the item button and the bulk apply while the cron
    stays off: with an empty ledger every item is "changed", so turning writes
    on would otherwise start filling BC at `BC_DRIFT_CAP` items an hour before
    the first bulk run had been checked (Denis, 2026-09-24)."""
    from app.config import load_config
    from app.handlers import scheduler_tick

    processed_item()
    monkeypatch.setenv("BC_WRITE_ENABLED", write)
    monkeypatch.setenv("SCHEDULER_BC_PUSH_DRIFT_ENABLED", drift)

    result = scheduler_tick.CRONS["bc.push-drift"](conn, load_config(), NOW)

    assert (result["enqueued"] > 0) == bool(expect)
