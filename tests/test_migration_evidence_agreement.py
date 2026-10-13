"""Migration 071: the agreeing read on `evidence` (PRD v3 section 5, amended
2026-10-12).

When two reads agreed on a field, the evidence row names the agreeing read's
model and its own confidence beside the first read's, and `confidence` holds
the combined number. Both columns are nullable: every row written before this
migration, every T0 and T3 row, and every field one read settled has no
agreeing read.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("column,data_type", [
    ("agreed_model_id", "text"),
    ("agreed_confidence", "numeric"),
])
def test_evidence_carries_the_agreeing_read(conn, column, data_type):
    col = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name='evidence' AND column_name=%s", (column,)
    ).fetchone()
    assert col is not None, f"migration 071 did not add evidence.{column}"
    assert col["data_type"] == data_type
    assert col["is_nullable"] == "YES"


def test_a_row_without_an_agreeing_read_is_still_valid(conn):
    """Additive: the INSERT GATE wrote before the migration still succeeds."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','m071','file:///m.pdf','staged') "
        "RETURNING doc_id").fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "model_id, confidence, extracted_at) VALUES (%s,'type','DoC','file:///m.pdf',1,"
        "'DoC','T1','m',0.9,now())", (doc_id,))
    row = conn.execute("SELECT agreed_model_id, agreed_confidence FROM evidence "
                       "WHERE doc_id=%s", (doc_id,)).fetchone()
    assert row["agreed_model_id"] is None and row["agreed_confidence"] is None


def test_the_web_role_may_read_the_columns_and_not_write_them(conn):
    """Invariant 1: `dentalia_api` reads `evidence` and never writes it. The
    table-level SELECT from 007 covers new columns; nothing here may add a
    write grant."""
    can = conn.execute(
        "SELECT has_column_privilege('dentalia_api', 'evidence', 'agreed_model_id', 'SELECT') s, "
        "       has_column_privilege('dentalia_api', 'evidence', 'agreed_confidence', 'SELECT') s2, "
        "       has_table_privilege('dentalia_api', 'evidence', 'INSERT') i, "
        "       has_table_privilege('dentalia_api', 'evidence', 'UPDATE') u").fetchone()
    assert can["s"] and can["s2"]
    assert not can["i"] and not can["u"]
