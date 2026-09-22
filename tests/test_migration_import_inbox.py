"""Migration 040: the import_inbox spool that carries an uploaded BC export
from the web producer to the worker that parses it.

Distinct from upload_inbox on purpose (spec 2026-08-25): this spool is
two-phase -- a preview reads and leaves the row, an apply reads and consumes
it -- whereas upload_inbox's "the row is gone" means "already processed".
"""
from __future__ import annotations

import pathlib

import pytest


def _cols(conn, table):
    return {r["column_name"]: {"data_type": r["data_type"],
                               "is_nullable": r["is_nullable"]}
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_name = %s",
                (table,)).fetchall()}


def test_import_inbox_has_expected_shape(conn):
    cols = _cols(conn, "import_inbox")
    assert cols["content"]["data_type"] == "bytea"
    assert {"id", "kind", "filename", "catalogue", "uploaded_by", "created_at"} <= set(cols)
    # kind/filename/content are the row's identity -- a spool row without any
    # of them cannot be parsed or routed.
    assert cols["kind"]["is_nullable"] == "NO"
    assert cols["filename"]["is_nullable"] == "NO"
    assert cols["content"]["is_nullable"] == "NO"
    # catalogue is nullable because slice B's vendor rows carry no catalogue.
    assert cols["catalogue"]["is_nullable"] == "YES"
    assert cols["uploaded_by"]["is_nullable"] == "YES"


def test_kind_is_constrained_to_the_two_spool_kinds(conn):
    conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) VALUES ('items','a.xlsx','\\x00')")
    conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) VALUES ('vendors','b.xlsx','\\x00')")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO import_inbox (kind, filename, content) "
            "VALUES ('documents','c.pdf','\\x00')")


def test_web_role_may_insert_but_not_read_or_delete_the_spool(conn):
    def _has(priv):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', 'import_inbox', %s) AS ok",
            (priv,)).fetchone()["ok"]

    # INSERT-only, same posture as upload_inbox: the producer spools and never
    # reads the spool back. The preview page gets the filename from job.payload.
    assert _has("INSERT") is True
    assert _has("SELECT") is False
    assert _has("DELETE") is False


def test_slice_a_reuses_ingest_run_rather_than_adding_a_tag(conn):
    """Slice A widens `ingest.run`'s `source` field and adds no queue tag of its
    own -- the browser item import IS an ingest. This asserted the absence of
    `vendor.import` until slice B's migration 041 added it (2026-08-26); the
    claim worth keeping is that 040 is not where it came from."""
    sql = pathlib.Path("migrations/040_import_inbox.sql").read_text()
    assert "ALTER TYPE job_type" not in sql
