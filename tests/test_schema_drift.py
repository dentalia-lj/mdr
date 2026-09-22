"""The guard for the class of defect migration 058 repaired.

`compare` is pure so the reporting is tested without touching a database; the
fingerprint query is exercised against the real one, because its whole job is
to read the catalogue correctly and a fake would prove nothing.

What is NOT tested here is `check()`'s scratch-database round trip -- it
creates and drops a database, which is too heavy for the suite and is covered
by running the command itself (`python -m app.cli schema-drift`).
"""

from __future__ import annotations

from app import schema_drift


# --------------------------------------------------------------------------- #
# compare(): pure, so every disposition is cheap to pin
# --------------------------------------------------------------------------- #
def test_identical_schemas_report_nothing():
    fp = {("view", "a"): "h1", ("column", "t.c"): "h2"}
    assert schema_drift.compare(fp, dict(fp)) == []


def test_a_definition_that_differs_is_reported_as_changed():
    """The 043 shape: the object exists on both sides under the same name and
    the live one is the older text. Nothing about the NAME reveals it, which is
    why the fingerprint compares definitions rather than presence."""
    live = {("view", "certificate_drift_candidate"): "before-distinct"}
    tree = {("view", "certificate_drift_candidate"): "after-distinct"}

    assert schema_drift.compare(live, tree) == [
        schema_drift.Drift("view", "certificate_drift_candidate", "changed")
    ]


def test_an_object_only_the_live_database_has_is_extra():
    """A hand-run CREATE TABLE in psql -- three of these were found in the dev
    database when this check was first run."""
    drift = schema_drift.compare({("column", "tmp_voco.r"): "h"}, {})
    assert drift == [schema_drift.Drift("column", "tmp_voco.r", "extra")]


def test_an_object_only_the_tree_has_is_missing():
    """A migration that never reached this database at all."""
    drift = schema_drift.compare({}, {("index", "job_pending_idx"): "h"})
    assert drift == [schema_drift.Drift("index", "job_pending_idx", "missing")]


def test_drift_is_sorted_by_kind_and_name_not_by_status():
    """An operator reads this looking for one object, not triaging by category."""
    live = {("view", "b"): "1", ("column", "a.x"): "1", ("view", "a"): "9"}
    tree = {("view", "a"): "1", ("column", "a.x"): "1", ("index", "z"): "1"}

    assert [(d.kind, d.name) for d in schema_drift.compare(live, tree)] == [
        ("index", "z"), ("view", "a"), ("view", "b"),
    ]


# --------------------------------------------------------------------------- #
# fingerprint(): against the real catalogue
# --------------------------------------------------------------------------- #
def test_fingerprint_covers_every_object_kind_the_schema_uses(conn):
    fp = schema_drift.fingerprint(conn)
    kinds = {kind for kind, _ in fp}
    assert {"column", "view", "index", "constraint", "enum", "grant"} <= kinds


def test_fingerprint_sees_the_view_that_drifted(conn):
    """`certificate_drift_candidate` is the object migration 058 repaired. If a
    future refactor drops views from the fingerprint, this fails rather than
    silently narrowing what the check can see."""
    fp = schema_drift.fingerprint(conn)
    assert ("view", "certificate_drift_candidate") in fp
    assert ("view", "certificate_drift") in fp


# --------------------------------------------------------------------------- #
# applied_out_of_order(): what order this database actually applied them in
# --------------------------------------------------------------------------- #
def test_a_fresh_database_applied_everything_in_disk_order(conn):
    """`db._out_of_order` is the PROSPECTIVE guard and refuses a pending file
    that sorts below one already applied; conftest migrates with strict=True,
    so the test database cannot be out of order. This is the retrospective
    question, and on a database built in one pass the answer is nothing."""
    assert schema_drift.applied_out_of_order(conn) == []


def test_a_migration_applied_late_is_reported_with_both_positions(conn):
    """The real shape: on the dev database 11 migrations had been applied at a
    different position than their filename implies -- 034 before 032 and 033,
    among three other inversions -- because parallel branches landed numbers
    below master's maximum. Reproduced here by backdating one row.

    Rolled back, so the test database's own tracking table is untouched.
    """
    first = conn.execute(
        "SELECT filename FROM schema_migrations ORDER BY filename LIMIT 1"
    ).fetchone()["filename"]
    conn.execute(
        "UPDATE schema_migrations SET applied_at = now() + interval '1 day' "
        "WHERE filename = %s", (first,))
    try:
        late = schema_drift.applied_out_of_order(conn)
        names = [o.filename for o in late]
        assert first in names, names
        moved = next(o for o in late if o.filename == first)
        # It sorts first on disk but was applied last.
        assert moved.disk_pos == 1
        assert moved.applied_pos > moved.disk_pos
    finally:
        conn.rollback()


def test_out_of_order_is_advisory_and_does_not_decide_the_exit_code():
    """A divergent apply order cannot be undone, so failing on it forever would
    train an operator to ignore the command. Only real drift is fatal."""
    clean = schema_drift.Report(drift=[], order=[
        schema_drift.OutOfOrder("014_upload.sql", 16, 15)], scratch="x")
    assert clean.ok

    drifted = schema_drift.Report(
        drift=[schema_drift.Drift("view", "v", "changed")], order=[], scratch="x")
    assert not drifted.ok


def test_a_view_altered_behind_the_migrations_is_detected(conn):
    """End to end on the real catalogue, and the closest reproduction of the
    actual incident: fingerprint, redefine the view the way a stale database
    holds it (no DISTINCT), fingerprint again, and compare the two.

    The rollback leaves the test database as it found it -- `conn`'s teardown
    would anyway, but the view is restored explicitly so a failure here cannot
    leak a broken view into another test on the same worker.
    """
    before = schema_drift.fingerprint(conn)

    conn.execute("CREATE OR REPLACE VIEW certificate_drift_candidate AS "
                 "SELECT doc_id, canonical_name, raw_cert_number, possible_match, "
                 "       eudamed_revision, certificate_status, eudamed_expiry_date, "
                 "       possible_rule "
                 "  FROM certificate_drift_candidate")
    try:
        after = schema_drift.fingerprint(conn)
        drift = schema_drift.compare(after, before)
        assert schema_drift.Drift(
            "view", "certificate_drift_candidate", "changed") in drift
        # and nothing else moved
        assert len(drift) == 1, drift
    finally:
        conn.rollback()
