"""Migration 050: the playbook body, its revision history, and the loader's
cache watermark.

049 shredded the three playbook fields that carry a cross-row invariant into
constrained tables. This covers the other half -- the `body jsonb`, the
append-only revision table that makes a bad edit revertible and keeps
`extraction_attempt.(playbook_slug, playbook_rev)` resolvable, and the epoch
counter the loader probes instead of stat-ing a directory.

The claims here were checked against a throwaway database while the spec was
written (spec §2.0). They live here so they stay checked.
"""
from __future__ import annotations

import pytest


def _epoch(conn) -> int:
    return conn.execute("SELECT epoch FROM playbook_epoch").fetchone()["epoch"]


def _mfr(conn, name: str) -> int:
    return conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) RETURNING id",
        (name,),
    ).fetchone()["id"]


# --------------------------------------------------------------------------- #
# the body

def test_the_body_is_jsonb_and_nullable(conn):
    """Nullable because ~350 of the 384 manufacturer rows have no playbook at
    all. `body IS NULL` is the "no playbook authored" state and is what
    `/manufacturers?no_playbook=1` already lists."""
    row = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name='manufacturer' AND column_name='body'").fetchone()
    assert row["data_type"] == "jsonb"
    assert row["is_nullable"] == "YES"


def test_a_body_round_trips_with_its_nesting_intact(conn):
    """`doc_sources` and `match` are nested; a text column plus json.loads would
    also work, and would lose the ability to query into it later."""
    mid = _mfr(conn, "BODY ROUND TRIP CO")
    conn.execute(
        "UPDATE manufacturer SET body = %s WHERE id = %s",
        ('{"doc_sources": [{"kind": "portal", "url": "https://x/"}],'
         ' "date_labels": {"from": ["velja od"]}}', mid))

    body = conn.execute(
        "SELECT body FROM manufacturer WHERE id=%s", (mid,)).fetchone()["body"]
    assert body["doc_sources"][0]["kind"] == "portal"
    assert body["date_labels"]["from"] == ["velja od"]


# --------------------------------------------------------------------------- #
# the revision table

def test_a_manufacturer_may_hold_many_revisions_but_each_rev_once(conn):
    mid = _mfr(conn, "REVISION CO")
    for rev in (0, 1, 2):
        conn.execute(
            "INSERT INTO manufacturer_playbook_revision (manufacturer_id, rev, body) "
            "VALUES (%s,%s,%s)", (mid, rev, "{}"))

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO manufacturer_playbook_revision (manufacturer_id, rev, body) "
            "VALUES (%s,%s,%s)", (mid, 1, '{"different": true}'))


def test_a_revision_cannot_outlive_its_manufacturer(conn):
    """The FK is what makes `(playbook_slug, playbook_rev)` on an
    `extraction_attempt` resolvable: a revision always has a row to hang off."""
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO manufacturer_playbook_revision (manufacturer_id, rev, body) "
            "VALUES (%s,%s,%s)", (99_999_999, 0, "{}"))


def test_the_web_role_may_append_a_revision_but_never_rewrite_history(conn):
    """The load-bearing grant. Revert in slice 3a writes the old body FORWARD as
    a new revision; it must not be able to delete the mistake instead, or the
    audit trail Denis asked for (2026-08-26) is only a convention.

    Do not widen this when the revert UI is built."""
    def _has(priv):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', "
            "'manufacturer_playbook_revision', %s) AS ok", (priv,)).fetchone()["ok"]

    assert _has("SELECT") is True
    assert _has("INSERT") is True
    assert _has("UPDATE") is False
    assert _has("DELETE") is False


# --------------------------------------------------------------------------- #
# the epoch

def test_the_epoch_table_holds_exactly_one_row(conn):
    """A second row would give two loaders two different answers about whether
    the cache is stale."""
    assert conn.execute("SELECT count(*) n FROM playbook_epoch").fetchone()["n"] == 1
    with pytest.raises(Exception):
        conn.execute("INSERT INTO playbook_epoch (one, epoch) VALUES (true, 99)")


def test_writing_any_of_the_three_tables_bumps_the_epoch(conn):
    """The loader's staleness probe. All three matter: a playbook's identity
    lives in `manufacturer_bc_code` and `manufacturer_name`, so an alias edit
    that left the epoch alone would serve a stale `for_manufacturer`."""
    start = _epoch(conn)

    mid = _mfr(conn, "EPOCH CO")
    after_mfr = _epoch(conn)
    assert after_mfr > start

    conn.execute("INSERT INTO vendor_master (code_source, code, name, import_batch) "
                 "VALUES ('LJ','EP01','EPOCH CO','epoch-test') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
        "VALUES ('LJ','EP01',%s,'playbook')", (mid,))
    after_code = _epoch(conn)
    assert after_code > after_mfr

    conn.execute(
        "INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
        "VALUES ('epoch co','EPOCH CO',%s,'canonical')", (mid,))
    assert _epoch(conn) > after_code


def test_the_epoch_counts_statements_not_rows(conn):
    """`FOR EACH STATEMENT`. The seed writes hundreds of rows in one statement
    and the epoch only has to change, not to count -- a per-row trigger would
    do 384 pointless UPDATEs on a single-row table."""
    start = _epoch(conn)
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) "
        "VALUES ('BULK ONE CO'), ('BULK TWO CO'), ('BULK THREE CO')")
    assert _epoch(conn) == start + 1


def test_a_deletion_bumps_the_epoch_too(conn):
    """Retiring a playbook is a change like any other. If DELETE were left off
    the trigger, a worker would keep serving rules for a manufacturer that no
    longer has any until something unrelated bumped the counter."""
    mid = _mfr(conn, "DELETE EPOCH CO")
    before = _epoch(conn)
    conn.execute("DELETE FROM manufacturer WHERE id=%s", (mid,))
    assert _epoch(conn) > before
