"""File-vs-database drift for playbooks.

Migration 050 made the database authoritative and left the files as the
authoring record. Nothing kept the two honest: a file edited after the import
went nowhere, silently. `manufacturers seed` detected a difference but could
only say "resolve by hand", because two differing bodies carry no direction.

`manufacturer_playbook_revision` supplies the direction. A file matching an
OLDER revision is stale and regenerable -- the database provably holds
everything that file had. A file matching NO revision was edited independently
and regenerating it would destroy that edit, so it is refused every time.
"""

from __future__ import annotations

import json

import pytest

from app import manufacturer_seed as seed
from psycopg.types.json import Json


def _mfr(conn, slug, body, rev=0):
    row = conn.execute(
        "INSERT INTO manufacturer (canonical_name, slug, body, playbook_rev) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (slug.upper(), slug, Json(body), rev)).fetchone()
    return row["id"]


def _rev(conn, mfr_id, rev, body):
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "VALUES (%s,%s,%s,'test','t')", (mfr_id, rev, Json(body)))


def test_identical_bodies_are_in_sync(conn):
    body = {"domains": ["acme.example"]}
    _mfr(conn, "acme", body)
    state, _ = seed.classify_drift(conn, "acme", body)
    assert state == seed.IN_SYNC


def test_a_file_matching_an_older_revision_is_stale(conn):
    """The regenerable case: the db moved ahead and the file did not."""
    old = {"domains": ["acme.example"]}
    new = {"domains": ["acme.example"], "date_labels": {"from": ["Date:"]}}
    mid = _mfr(conn, "acme", new, rev=1)
    _rev(conn, mid, 0, old)
    _rev(conn, mid, 1, new)
    state, d = seed.classify_drift(conn, "acme", old)
    assert state == seed.FILE_STALE
    assert d["matches_rev"] == 0 and d["rev"] == 1
    assert "db has +date_labels" in d["delta"]


def test_a_file_matching_no_revision_has_diverged(conn):
    """The refused case. Both sides were edited, so the file carries something
    that exists nowhere else and no rewrite is safe."""
    mid = _mfr(conn, "acme", {"domains": ["db.example"]}, rev=1)
    _rev(conn, mid, 0, {"domains": ["acme.example"]})
    _rev(conn, mid, 1, {"domains": ["db.example"]})
    state, d = seed.classify_drift(conn, "acme", {"domains": ["file.example"]})
    assert state == seed.DIVERGED
    assert "matches_rev" not in d
    assert "differs: domains" in d["delta"]


def test_a_playbook_with_no_row_is_not_imported(conn):
    state, _ = seed.classify_drift(conn, "nobody", {"domains": []})
    assert state == seed.NOT_IMPORTED


def test_a_row_with_no_file_is_reported(conn, tmp_path):
    _mfr(conn, "ghost", {"domains": []})
    states = {slug: st for slug, st, _ in seed.drift(conn, dir_path=tmp_path)}
    assert states["ghost"] == seed.DB_ONLY


def test_delta_names_keys_and_never_prints_values(conn):
    """A body holds regexes and URL lists. A diff that printed them would be
    unreadable in a terminal and would put the whole playbook in a log."""
    d = seed.body_delta({"ref_pattern": "^SECRET-[0-9]+$"},
                        {"ref_pattern": "^OTHER-[0-9]+$", "domains": []})
    assert "differs: ref_pattern" in d and "db has +domains" in d
    assert "SECRET" not in d and "OTHER" not in d


def test_drift_is_sorted_and_stable(conn, tmp_path):
    for slug in ("zeta", "alpha", "mid"):
        (tmp_path / f"{slug}.json").write_text(
            json.dumps({"manufacturer": slug.upper(), "domains": []}))
    out = [slug for slug, _, _ in seed.drift(conn, dir_path=tmp_path)]
    assert out == sorted(out)
    assert seed.drift(conn, dir_path=tmp_path) == seed.drift(conn, dir_path=tmp_path)


# --------------------------------------------------------------------------- #
# regeneration must not churn the file
# --------------------------------------------------------------------------- #
from app.cli import _reorder


def test_reorder_keeps_the_files_key_order():
    """Postgres normalises jsonb key order, so a body read back has lost the
    order it was authored in. Regenerating in the database's order turned a
    one-key change into a twelve-line diff."""
    old = {"doc_type": "IFU", "kind": "portal", "url": "u", "note": "n"}
    new = {"url": "u", "kind": "portal", "note": "n2", "doc_type": "IFU"}
    assert list(_reorder(old, new)) == ["doc_type", "kind", "url", "note"]
    assert _reorder(old, new)["note"] == "n2", "the database's VALUE must win"


def test_reorder_appends_keys_the_file_does_not_have():
    got = _reorder({"a": 1}, {"a": 1, "b": 2})
    assert list(got) == ["a", "b"]


def test_reorder_drops_keys_the_database_dropped():
    assert list(_reorder({"a": 1, "gone": 2}, {"a": 1})) == ["a"]


def test_reorder_recurses_into_lists_positionally():
    """Only OBJECT keys are normalised by jsonb; list order survives, so
    element i is the same entry on both sides."""
    old = [{"b": 1, "a": 2}, {"d": 3, "c": 4}]
    new = [{"a": 2, "b": 9}, {"c": 4, "d": 3}]
    got = _reorder(old, new)
    assert [list(x) for x in got] == [["b", "a"], ["d", "c"]]
    assert got[0]["b"] == 9


def test_reorder_gives_up_on_a_list_that_changed_length():
    """Nothing here can say which element moved, so the database's value stands
    rather than a guess."""
    assert _reorder([{"a": 1}], [{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_reorder_passes_scalars_through():
    assert _reorder("table", "text-column") == "text-column"
