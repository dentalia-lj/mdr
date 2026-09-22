"""Migration 014: upload_inbox spool + upload.ingest enum value + web grants."""
from __future__ import annotations


def _enum_labels(conn, typename):
    return {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = %s", (typename,)).fetchall()}


def test_upload_ingest_is_a_job_type(conn):
    assert "upload.ingest" in _enum_labels(conn, "job_type")


def test_upload_inbox_table_exists_with_expected_columns(conn):
    cols_info = {r["column_name"]: {
        "data_type": r["data_type"],
        "is_nullable": r["is_nullable"]
    } for r in conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'upload_inbox'").fetchall()}

    # Data types
    assert cols_info["content"]["data_type"] == "bytea"
    assert cols_info["target_group_id"]["data_type"] == "bigint"
    assert {"id", "filename", "catalogue", "uploaded_by", "created_at"} <= set(cols_info)

    # NOT NULL constraints: filename, content, catalogue
    assert cols_info["filename"]["is_nullable"] == "NO"
    assert cols_info["content"]["is_nullable"] == "NO"
    assert cols_info["catalogue"]["is_nullable"] == "NO"

    # Nullable columns: target_group_id, uploaded_by
    assert cols_info["target_group_id"]["is_nullable"] == "YES"
    assert cols_info["uploaded_by"]["is_nullable"] == "YES"


def test_web_role_can_insert_upload_inbox_but_not_the_registry(conn):
    def _has(priv, table):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', %s, %s) AS ok",
            (table, priv)).fetchone()["ok"]

    # dentalia_api has INSERT on upload_inbox only
    assert _has("INSERT", "upload_inbox") is True
    # Negative: no SELECT on upload_inbox (worker-only)
    assert _has("SELECT", "upload_inbox") is False
    # Negative: no DELETE/UPDATE on upload_inbox (worker-only)
    assert _has("DELETE", "upload_inbox") is False
    assert _has("UPDATE", "upload_inbox") is False

    # Negative: no write access to registry (invariant 1)
    assert _has("INSERT", "document") is False

    # Sequence grant: dentalia_api has USAGE on upload_inbox_id_seq
    seq_usage = conn.execute(
        "SELECT has_sequence_privilege('dentalia_api', %s, %s) AS ok",
        ("upload_inbox_id_seq", "USAGE")).fetchone()["ok"]
    assert seq_usage is True
