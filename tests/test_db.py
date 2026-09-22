"""Migration-runner guards.

The subject here is `_out_of_order` and its wiring into `run_migrations`.

**What it is for.** A migration numbered below one this database has already
applied is a migration a *fresh* database will apply in a different relative
order than this one did -- which is the single thing a numbered-migration
convention exists to prevent. Measured on the dev database 2026-08-27, three
separate episodes were already present and undetected: `014_upload` after
`015_supersession`, `032_stated_class` + `033_item_class_check` after
`034_item_document_production_expiry`, and `041_vendor_import` after
`043_eudamed_cert_views`.

**What it is NOT for.** Duplicate numeric prefixes. That was the original
proposal and it is the wrong invariant twice over: the 032/033 episode above
carries no duplicate at all and would sail straight past such a check, and 18
of the repo's 20 branches carry the unrenameable `014_evidence_rev` /
`014_upload` pair, so refusing on a duplicate prefix would brick `migrate`
almost everywhere. Ordering is the property that matters; a duplicate number is
merely one way to break it.
"""

from __future__ import annotations

import pathlib

import psycopg
import pytest

from app import db
from tests.conftest import ADMIN_URL, _with_db


# --------------------------------------------------------------------------
# The pure check. No database needed -- it is a function of two name lists.
# --------------------------------------------------------------------------

# (label, disk filenames, already-applied filenames, expected out-of-order)
_CASES = [
    (
        "fresh database applies everything in disk order",
        ["001_a.sql", "002_b.sql", "003_c.sql"],
        [],
        [],
    ),
    (
        "fully migrated, nothing pending",
        ["001_a.sql", "002_b.sql"],
        ["001_a.sql", "002_b.sql"],
        [],
    ),
    (
        "the normal case: the new file sorts above every applied one",
        ["001_a.sql", "002_b.sql", "003_c.sql"],
        ["001_a.sql", "002_b.sql"],
        [],
    ),
    (
        "a branch lands a LOWER number than the applied maximum",
        ["001_a.sql", "002_b.sql", "003_c.sql"],
        ["001_a.sql", "003_c.sql"],
        ["002_b.sql"],
    ),
    (
        "the real 041 case: a duplicate prefix sorting below the maximum",
        [
            "040_import_inbox.sql",
            "041_eudamed_job_types.sql",
            "041_vendor_import.sql",
            "042_eudamed_manufacturer.sql",
        ],
        [
            "040_import_inbox.sql",
            "041_eudamed_job_types.sql",
            "042_eudamed_manufacturer.sql",
        ],
        ["041_vendor_import.sql"],
    ),
    (
        # The episode a duplicate-prefix guard cannot see: three distinct
        # numbers, no collision, still applied out of order.
        "the real 032/033 case: distinct numbers, still out of order",
        [
            "032_stated_class.sql",
            "033_item_class_check.sql",
            "034_item_document_production_expiry.sql",
        ],
        ["034_item_document_production_expiry.sql"],
        ["032_stated_class.sql", "033_item_class_check.sql"],
    ),
    (
        "several late files are all reported, not just the first",
        ["001_a.sql", "002_b.sql", "003_c.sql", "009_i.sql"],
        ["009_i.sql"],
        ["001_a.sql", "002_b.sql", "003_c.sql"],
    ),
    (
        # `max(done)` must come from the applied set, not from disk: a file
        # present on disk but unapplied must not raise the bar.
        "an unapplied high number does not make lower pending files late",
        ["001_a.sql", "002_b.sql", "050_z.sql"],
        ["001_a.sql"],
        [],
    ),
    (
        # A file deleted from disk stays in schema_migrations forever. It must
        # still count towards the bar, and must not crash the check.
        "an applied file no longer on disk still sets the bar",
        ["001_a.sql", "005_e.sql"],
        ["001_a.sql", "007_gone.sql"],
        ["005_e.sql"],
    ),
]


@pytest.mark.parametrize(
    "disk,done,expected",
    [(c[1], c[2], c[3]) for c in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_out_of_order_cases(disk, done, expected):
    assert db._out_of_order(disk, set(done)) == expected


def test_out_of_order_returns_disk_order_not_set_order():
    """The report is read by a human deciding what to renumber, so it must be
    deterministic. `done` is a set, whose iteration order is not."""
    disk = ["001_a.sql", "002_b.sql", "003_c.sql", "004_d.sql", "099_z.sql"]
    done = {"099_z.sql"}
    assert db._out_of_order(disk, done) == [
        "001_a.sql",
        "002_b.sql",
        "003_c.sql",
        "004_d.sql",
    ]


# --------------------------------------------------------------------------
# The wiring. A check nothing calls is not a check -- these run the real
# `run_migrations` against a real throwaway database.
# --------------------------------------------------------------------------

_SCRATCH_DB = "dentalia_test_migration_order"


@pytest.fixture
def scratch_db():
    """A real, empty database of its own.

    Not the shared test database: `run_migrations` writes `schema_migrations`,
    and seeding fake filenames into the one every other test shares would be
    exactly the cross-test pollution `_RESET_TABLES` exists to prevent (it does
    not, and should not, reset `schema_migrations`).
    """
    url = _with_db(ADMIN_URL, _SCRATCH_DB)
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {_SCRATCH_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {_SCRATCH_DB}")
    try:
        with psycopg.connect(url, autocommit=True, row_factory=db.dict_row) as conn:
            yield conn
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {_SCRATCH_DB} WITH (FORCE)")


def _write(d: pathlib.Path, *names: str) -> pathlib.Path:
    """Migrations whose content is irrelevant -- ordering is the subject."""
    for n in names:
        (d / n).write_text("SELECT 1;")
    return d


def test_run_migrations_applies_in_disk_order(scratch_db, tmp_path):
    applied = db.run_migrations(
        scratch_db, _write(tmp_path, "002_b.sql", "001_a.sql", "003_c.sql")
    )
    assert applied == ["001_a.sql", "002_b.sql", "003_c.sql"]


def test_late_migration_warns_by_default_and_still_applies(
    scratch_db, tmp_path, capsys
):
    """Warn, not refuse. A branch landing a lower number is the routine cost of
    parallel sessions here (Denis, 2026-08-27), and it is fixed at merge time --
    so `migrate` has to keep working at exactly the moment it fires."""
    d = _write(tmp_path, "001_a.sql", "003_c.sql")
    db.run_migrations(scratch_db, d)

    (d / "002_b.sql").write_text("SELECT 1;")
    applied = db.run_migrations(scratch_db, d)

    assert applied == ["002_b.sql"], "the late migration must still be applied"
    warning = capsys.readouterr().out
    assert "002_b.sql" in warning
    assert "003_c.sql" in warning, "the warning names the bar it fell below"


def test_late_migration_raises_under_strict(scratch_db, tmp_path):
    """Strict is for a database that cannot legitimately be out of order: a
    fresh one (tests, CI, a new deploy). There the condition is a real defect."""
    d = _write(tmp_path, "001_a.sql", "003_c.sql")
    db.run_migrations(scratch_db, d)

    (d / "002_b.sql").write_text("SELECT 1;")
    with pytest.raises(db.OutOfOrderMigration) as exc:
        db.run_migrations(scratch_db, d, strict=True)
    assert "002_b.sql" in str(exc.value)

    remaining = scratch_db.execute(
        "SELECT filename FROM schema_migrations ORDER BY filename"
    ).fetchall()
    assert [r["filename"] for r in remaining] == ["001_a.sql", "003_c.sql"], (
        "strict must refuse BEFORE applying anything, not half-way through"
    )


def test_strict_is_silent_on_a_fresh_database(scratch_db, tmp_path):
    """Scenario B: `done` is empty, so nothing can be late. This is the path
    `tests/conftest.py` takes on every run -- if it could fire here, every test
    session in the repo would fail."""
    applied = db.run_migrations(
        scratch_db, _write(tmp_path, "001_a.sql", "002_b.sql"), strict=True
    )
    assert applied == ["001_a.sql", "002_b.sql"]


def test_strict_is_silent_when_nothing_is_pending(scratch_db, tmp_path):
    """Scenario A: the dev database today. It carries three historical
    out-of-order episodes, but with no pending files there is nothing to judge,
    so even strict mode must stay quiet -- otherwise `cli migrate` on a
    fully-migrated database becomes a permanent failure."""
    d = _write(tmp_path, "001_a.sql", "002_b.sql")
    db.run_migrations(scratch_db, d, strict=True)
    assert db.run_migrations(scratch_db, d, strict=True) == []


def test_conftest_migrates_strictly():
    """The guard on the guard.

    `tests/conftest.py` builds a throwaway database from scratch, which is the
    one context where an out-of-order file is unambiguously a defect rather
    than a merge in progress. If someone drops `strict=True` there, this check
    stops protecting CI silently -- the suite would still pass, having simply
    stopped looking. Assert the call site, not the behaviour, because the
    behaviour is invisible until the day it matters.
    """
    source = pathlib.Path(__file__).with_name("conftest.py").read_text()
    assert "db.migrate(TEST_URL, strict=True)" in source, (
        "conftest must migrate the throwaway database strictly"
    )
