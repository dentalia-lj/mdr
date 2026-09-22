"""vendor.import — the BC manufacturer master handed to us through /import.

Real Postgres, no mocks (CLAUDE.md). The handler is thin over
`app/vendor_master.py`; what is tested here is the spool lifecycle, the report
shape, and the one outcome that is neither success nor failure: a refused
rename.
"""
from __future__ import annotations

import io

import pytest

from app import import_spool, vendor_master
from app.handlers import vendor_import


def _vendor_bytes(rows) -> bytes:
    """A real .xlsx. The spooled filename must match these bytes' format --
    `read_frame` branches on the suffix, and slice A lost half a task to a
    fixture writing CSV under an .xlsx name."""
    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame([{"Šifra": c, "Ime": n} for c, n in rows]).to_excel(buf, index=False)
    return buf.getvalue()


def _spool_vendors(conn, rows, *, kind="vendors", filename="Proizvajalci.xlsx"):
    return conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) VALUES (%s,%s,%s) "
        "RETURNING id",
        (kind, filename, _vendor_bytes(rows)),
    ).fetchone()["id"]


def _job(upload_id, *, dry_run=True, allow_renames=False,
         filename="Proizvajalci.xlsx"):
    return {"id": 1, "type": "vendor.import",
            "payload": {"upload_id": upload_id, "filename": filename,
                        "dry_run": dry_run, "allow_renames": allow_renames}}


def _count(conn, table):
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


# --------------------------------------------------------------------------- #
# the two phases
# --------------------------------------------------------------------------- #
def test_dry_run_reports_the_diff_and_writes_nothing(conn):
    uid = _spool_vendors(conn, [("001", "IVOCLAR"), ("999", "NEW MAKER")])

    report = vendor_import.handle_vendor_import(conn, _job(uid))

    assert report["seen"] == 2
    assert report["added"] == 2
    assert report["written"] is False
    assert _count(conn, "vendor_master") == 0
    # The apply still needs these bytes: `import_inbox` is two-phase, unlike
    # `upload_inbox` where one read is the whole lifecycle.
    assert _count(conn, "import_inbox") == 1


def test_apply_writes_the_mirror_and_consumes_the_spool(conn):
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])

    report = vendor_import.handle_vendor_import(conn, _job(uid, dry_run=False))

    assert report["written"] is True and report["added"] == 1
    assert conn.execute(
        "SELECT name FROM vendor_master WHERE code='001'").fetchone()["name"] == "IVOCLAR"
    assert _count(conn, "import_inbox") == 0


def test_apply_reports_what_the_gc_collected(conn):
    """Abandoned previews age out opportunistically on the next import rather
    than on a scheduler tick, so the count belongs on this job's result."""
    conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, created_at) "
        "VALUES ('vendors','old.xlsx','\\x00', now() - make_interval(days => %s))",
        (import_spool.SPOOL_RETENTION_DAYS + 1,))
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])

    report = vendor_import.handle_vendor_import(conn, _job(uid, dry_run=False))

    assert report["spool_gc"] == 1
    assert _count(conn, "import_inbox") == 0


# --------------------------------------------------------------------------- #
# the guarded outcome
# --------------------------------------------------------------------------- #
def test_a_rename_is_refused_and_nothing_is_written_or_consumed(conn):
    """`canonical_manufacturer` is effectively write-once -- RESOLVE's ladder
    short-circuits on `_existing_link` -- so re-pointing a code after items are
    grouped splits that manufacturer with no repair path. The refusal is
    all-or-nothing: the genuinely new row does not slip in alongside."""
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW"), ("002", "ALSO NEW")])

    report = vendor_import.handle_vendor_import(conn, _job(uid, dry_run=False))

    assert report["outcome"] == "rename-refused"
    assert report["written"] is False
    assert report["renamed"] == [["001", "OLD", "NEW"]]
    assert conn.execute(
        "SELECT name FROM vendor_master WHERE code='001'").fetchone()["name"] == "OLD"
    assert _count(conn, "vendor_master") == 1
    # Spool intact, so the operator can re-apply with the box ticked.
    assert _count(conn, "import_inbox") == 1


def test_a_refused_rename_is_a_finished_job_not_a_dead_one(conn):
    """It is an expected outcome, not a failure: the operator previewed a clean
    diff and something re-pointed a code in between. Dead-lettering would bury
    the reason and strand the spool row."""
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW")])

    report = vendor_import.handle_vendor_import(conn, _job(uid, dry_run=False))

    assert "001" in report["error"]


def test_allow_renames_lets_the_rename_through(conn):
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW")])

    report = vendor_import.handle_vendor_import(
        conn, _job(uid, dry_run=False, allow_renames=True))

    assert report["written"] is True
    assert report["renamed"] == [["001", "OLD", "NEW"]]
    assert conn.execute(
        "SELECT name FROM vendor_master WHERE code='001'").fetchone()["name"] == "NEW"


def test_a_dry_run_reports_a_rename_without_needing_the_opt_in(conn):
    """The preview is how the operator learns there is one to opt into."""
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW")])

    report = vendor_import.handle_vendor_import(conn, _job(uid))

    assert report["renamed"] == [["001", "OLD", "NEW"]]
    assert "outcome" not in report


def test_a_disappeared_code_is_reported_and_kept(conn):
    """Never deleted: the row is a fact to report, and deleting it would strand
    any alias already derived from it."""
    vendor_master.apply(conn, (vendor_master.VendorRow("277", "GONE SOON"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])

    report = vendor_import.handle_vendor_import(conn, _job(uid))

    assert report["disappeared"] == ["277"]
    assert _count(conn, "vendor_master") == 1


# --------------------------------------------------------------------------- #
# failure paths and the runner contract
# --------------------------------------------------------------------------- #
def test_a_wrong_kind_spool_row_is_a_dead_job_not_a_silent_zero_import(conn):
    uid = conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) "
        "VALUES ('items','Artikli.xlsx','\\x00') RETURNING id").fetchone()["id"]

    with pytest.raises(ValueError, match="kind"):
        vendor_import.handle_vendor_import(conn, _job(uid, filename="Artikli.xlsx"))


def test_a_missing_spool_row_says_so_rather_than_importing_zero(conn):
    with pytest.raises(import_spool.SpoolMissing):
        vendor_import.handle_vendor_import(conn, _job(999999))


def test_the_report_carries_no_anomalies_key(conn):
    """`app/workers/runner.py` gates on `if result.get("anomalies")`. A vendor
    import produces none, so the key is absent rather than an empty list."""
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])

    assert "anomalies" not in vendor_import.handle_vendor_import(conn, _job(uid))


def test_the_handler_is_registered_and_is_not_the_placeholder(conn):
    from app.handlers import HANDLERS, noop

    assert HANDLERS["vendor.import"] is not noop._noop
    assert HANDLERS["vendor.import"] is vendor_import.handle_vendor_import


def test_the_handler_emits_nothing(conn):
    """`playbooks sync` stays a deliberate CLI operation: it reports
    `orphaned_groups`, work a human must act on, and running it as a side
    effect of an upload produces that count where nobody reads it."""
    before = _count(conn, "job")
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])

    vendor_import.handle_vendor_import(conn, _job(uid, dry_run=False))

    assert _count(conn, "job") == before
