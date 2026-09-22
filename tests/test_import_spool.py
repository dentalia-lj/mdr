"""app/import_spool.py — the two-phase spool's whole lifecycle.

Real Postgres, no mocks (CLAUDE.md). The `conn` fixture never commits, so
inserts roll back on close.
"""
from __future__ import annotations

import pytest

from app import import_spool


def _spool(conn, *, kind="items", filename="Artikli.xlsx", content=b"\x01\x02",
           catalogue="LJ", age_days=0):
    return conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue, created_at) "
        "VALUES (%s,%s,%s,%s, now() - make_interval(days => %s)) RETURNING id",
        (kind, filename, content, catalogue, age_days),
    ).fetchone()["id"]


def test_read_returns_the_row(conn):
    uid = _spool(conn, content=b"BYTES", filename="Artikli 3.7.2026.xlsx")
    row = import_spool.read(conn, uid)
    assert row["kind"] == "items"
    assert row["filename"] == "Artikli 3.7.2026.xlsx"
    assert bytes(row["content"]) == b"BYTES"
    assert row["catalogue"] == "LJ"


def test_read_leaves_the_row_so_an_apply_can_read_it_again(conn):
    """The whole reason this is not upload_inbox: a preview must not consume."""
    uid = _spool(conn)
    import_spool.read(conn, uid)
    import_spool.read(conn, uid)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 1


def test_read_raises_when_the_row_is_gone(conn):
    with pytest.raises(import_spool.SpoolMissing):
        import_spool.read(conn, 999999)


def test_consume_deletes_and_is_idempotent(conn):
    uid = _spool(conn)
    import_spool.consume(conn, uid)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0
    import_spool.consume(conn, uid)  # at-least-once delivery: a retry is a no-op


def test_gc_drops_abandoned_rows_and_spares_fresh_ones(conn):
    old = _spool(conn, age_days=import_spool.SPOOL_RETENTION_DAYS + 1)
    fresh = _spool(conn, age_days=1)
    assert import_spool.gc(conn) == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM import_inbox").fetchall()}
    assert old not in remaining
    assert fresh in remaining


def test_gc_retention_is_overridable_for_tests(conn):
    uid = _spool(conn, age_days=2)
    assert import_spool.gc(conn, retention_days=30) == 0
    assert import_spool.gc(conn, retention_days=1) == 1
    assert uid not in {r["id"] for r in conn.execute("SELECT id FROM import_inbox").fetchall()}
