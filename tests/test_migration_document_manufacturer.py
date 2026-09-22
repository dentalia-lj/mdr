"""Migration 053: `document.canonical_manufacturer`, the edge that never existed.

Both document types reached a manufacturer only by fanning out to catalogue
items -- group-scope through `item_group`, manufacturer-scope through
`mfr-scope` links -- so a document with no item links reached none at all.
Measured 2026-08-31: 540 documents in that state, 443 of them `filed`, which is
every filed document by definition (C15 files exactly what we stock nothing for,
and the manufacturer path runs through what we stock).

NOT `evidence.manufacturer` (PRD §201), which is the name as PRINTED with a
confidence and a verbatim. This column is what a human or C16 CONFIRMED.
"""
from __future__ import annotations

import pytest
from psycopg import errors


def test_document_carries_a_nullable_manufacturer_column(conn):
    """Nullable and additive: an existing document row is valid with the column
    unset, so the migration needs no backfill to be correct."""
    col = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name='document' AND column_name='canonical_manufacturer'"
    ).fetchone()
    assert col is not None, "migration 053 did not add the column"
    assert col["data_type"] == "text"
    assert col["is_nullable"] == "YES"


def test_the_fk_cascades_on_rename_and_refuses_on_delete(conn):
    """Migration 052's ruling, applied one table over: a rename does not
    invalidate the attribution, it renames the thing described. ON DELETE stays
    NO ACTION -- deleting a manufacturer something still references is refused,
    never silently cascaded."""
    fk = conn.execute(
        "SELECT confupdtype, confdeltype FROM pg_constraint "
        "WHERE conrelid='document'::regclass AND contype='f' "
        "  AND conname LIKE '%canonical_manufacturer%'"
    ).fetchone()
    assert fk is not None, "no FK on document.canonical_manufacturer"
    assert fk["confupdtype"] == "c", "ON UPDATE must CASCADE"
    assert fk["confdeltype"] == "a", "ON DELETE must stay NO ACTION"


def test_a_rename_carries_the_bound_documents_with_it(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('REN AG')")
    conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, canonical_manufacturer) "
        "VALUES ('ISO','n.a.','manufacturer','r1','file:///r.pdf','filed','REN AG')")

    conn.execute("UPDATE manufacturer SET canonical_name='RENAMED AG' "
                 "WHERE canonical_name='REN AG'")

    assert conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE content_hash='r1'"
    ).fetchone()["canonical_manufacturer"] == "RENAMED AG"


def test_an_unknown_manufacturer_is_refused(conn):
    """The FK is what stops a garbled name being minted onto a document. GATE
    guards this in `_bindable_manufacturer` too; this is the floor under it."""
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status, canonical_manufacturer) "
            "VALUES ('ISO','n.a.','manufacturer','r2','file:///r.pdf','filed','NO SUCH CO')")


def test_the_column_is_indexed_for_the_manufacturer_lookup(conn):
    """`manufacturer_documents` filters on this column, and that query used to
    seq-scan `item_mirror` (15.936 rows removed to find 22, measured
    2026-08-31). Partial because null means undecided and nothing looks it up."""
    idx = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname='document_manufacturer_idx'"
    ).fetchone()
    assert idx is not None, "migration 053 did not add the index"
    assert "canonical_manufacturer" in idx["indexdef"]
    assert "IS NOT NULL" in idx["indexdef"]
