"""manual_task persistence (G13): durable home for GATE manual dispositions
(and later DISCOVER dead-ends / dead-job follow-ups).

Owner-role writes only; the API gets SELECT (producer stays a producer — inv. 1
is about the registry, but the same discipline applies: resolution goes through
the queue, never a direct write here). Tests don't commit — the conn fixture
rolls back on teardown, so rows never leak (conftest truncates only job/lease).
"""

from __future__ import annotations

import psycopg
import pytest


def test_manual_task_defaults_open_and_roundtrips(conn):
    row = conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload) "
        "VALUES ('gate-manual', 42, '{\"flags\": [\"low-confidence\"]}') "
        "RETURNING id, status"
    ).fetchone()
    assert row["status"] == "open"

    got = conn.execute(
        "SELECT kind, group_id, payload, resolved_at FROM manual_task WHERE id=%s",
        (row["id"],),
    ).fetchone()
    assert got["kind"] == "gate-manual"
    assert got["group_id"] == 42
    assert got["payload"]["flags"] == ["low-confidence"]
    assert got["resolved_at"] is None


def test_manual_task_kind_is_a_closed_enum(conn):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        conn.execute("INSERT INTO manual_task (kind, payload) VALUES ('not-a-kind', '{}')")


def test_manual_task_open_index_query(conn):
    conn.execute("INSERT INTO manual_task (kind, payload) VALUES ('gate-manual', '{}')")
    conn.execute(
        "INSERT INTO manual_task (kind, payload, status, resolved_at, resolved_by) "
        "VALUES ('gate-manual', '{}', 'resolved', now(), 'user:marta')"
    )
    open_count = conn.execute(
        "SELECT count(*) c FROM manual_task WHERE status='open'"
    ).fetchone()["c"]
    assert open_count == 1


def test_manual_task_doc_id_fk_to_document(conn):
    # gate-manual tasks point at the staged document; the FK is safe (documents
    # are never deleted, 10-y retention). A dangling doc_id is a bug -> rejected.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO manual_task (kind, doc_id, payload) "
            "VALUES ('gate-manual', 999999, '{}')"
        )
