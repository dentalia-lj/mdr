"""Vendor-master mirror (S1.8): reading BC's Proizvajalci export and diffing it
against `vendor_master` before writing.

The diff is the point. A re-import that blind-upserts names can silently
re-point a code whose old name is already baked into `item_group.
canonical_manufacturer`, and that column is effectively write-once
(`resolve.py`'s ladder short-circuits on `_existing_link`). So a rename is a
decision the operator makes, not something an import does on its way past.
"""

from __future__ import annotations

import pytest

from app import vendor_master as vm


# --------------------------------------------------------------------------- #
# read_file — real xlsx, no fixtures invented
# --------------------------------------------------------------------------- #
def _write_xlsx(path, rows):
    import pandas as pd

    pd.DataFrame(rows, columns=["Šifra", "Ime"]).to_excel(path, index=False)
    return path




# --------------------------------------------------------------------------- #
# reading spooled bytes (slice B) — one reader, two entry points
# --------------------------------------------------------------------------- #
def _vendor_xlsx(tmp_path, rows, name="Proizvajalci.xlsx"):
    """A real BC vendor export. The filename must match the bytes' format --
    `read_frame` branches on the suffix, and slice A lost half a task to a
    helper that wrote CSV under an .xlsx name."""
    import pandas as pd
    path = tmp_path / name
    pd.DataFrame([{"Šifra": c, "Ime": n} for c, n in rows]).to_excel(path, index=False)
    return path


def test_read_bytes_matches_read_file_on_the_same_export(tmp_path):
    """One reader, two entry points. If these disagree, the browser import and
    the CLI import are silently importing different things."""
    path = _vendor_xlsx(tmp_path, [("001", "IVOCLAR VIVADENT"), ("002", "KOMET")])

    assert vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx") == vm.read_file(path)


def test_read_bytes_treats_a_blank_name_as_absent_exactly_as_read_file_does(tmp_path):
    """`read_frame` passes keep_default_na=False, which `read_file` did not:
    a blank Ime now arrives as '' rather than NaN. `_clean` maps both to None,
    and one of the 390 delivered rows is a real nameless code, so this
    equivalence is load-bearing rather than incidental."""
    path = _vendor_xlsx(tmp_path, [("003", None)])

    rows = vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx")
    assert rows == (vm.VendorRow(code="003", name=None),)
    assert rows == vm.read_file(path)


def test_read_bytes_keeps_a_leading_zero_code_as_text(tmp_path):
    """`dtype=str` is load-bearing: without it pandas reads `001` as the
    integer 1 and the code stops matching `item_mirror.manufacturer_raw`."""
    path = _vendor_xlsx(tmp_path, [("001", "IVOCLAR")])

    assert vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx")[0].code == "001"


def test_read_bytes_rejects_a_file_missing_the_bc_columns(tmp_path):
    import pandas as pd
    path = tmp_path / "Proizvajalci.xlsx"
    pd.DataFrame([{"nope": "1"}]).to_excel(path, index=False)

    with pytest.raises(ValueError, match="missing column"):
        vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx")


def test_read_bytes_rejects_an_unreadable_file_type(tmp_path):
    with pytest.raises(ValueError, match="unsupported export file type"):
        vm.read_bytes(b"%PDF-1.4", "Proizvajalci.pdf")


def test_the_mirror_is_written_under_the_one_code_source(conn):
    """The column stays -- it is half the PK and already DEFAULT 'LJ'. What
    goes is the caller's ability to choose: one Business Central issues these
    codes (Denis, 2026-08-26)."""
    vm.apply(conn, (vm.VendorRow("001", "IVOCLAR"),), batch="b1")
    row = conn.execute("SELECT code_source, name FROM vendor_master").fetchone()
    assert row["code_source"] == vm.DEFAULT_CODE_SOURCE == "LJ"


def test_diff_and_apply_take_no_code_source(conn):
    import inspect
    for fn in (vm.diff, vm.apply):
        assert "code_source" not in inspect.signature(fn).parameters, fn.__name__


def test_read_file_parses_code_and_name(tmp_path):
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "IVOCLAR VIVADENT"], ["008", "GC"]])

    rows = vm.read_file(f)

    assert [(r.code, r.name) for r in rows] == [
        ("001", "IVOCLAR VIVADENT"),
        ("008", "GC"),
    ]


def test_read_file_keeps_leading_zeros_and_non_numeric_codes(tmp_path):
    """`001` must not become `1`, and at least one real Šifra is non-numeric."""
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "A"], ["CEFLA", "CEFLA"]])

    assert [r.code for r in vm.read_file(f)] == ["001", "CEFLA"]


def test_read_file_keeps_rows_with_no_name(tmp_path):
    """One of the 390 delivered rows has no name; it is still a real code."""
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "A"], ["999", None]])

    rows = vm.read_file(f)

    assert [(r.code, r.name) for r in rows] == [("001", "A"), ("999", None)]


def test_read_file_strips_whitespace_and_drops_codeless_rows(tmp_path):
    f = _write_xlsx(tmp_path / "v.xlsx", [["  001  ", "  A  "], [None, "orphan"]])

    rows = vm.read_file(f)

    assert [(r.code, r.name) for r in rows] == [("001", "A")]


# --------------------------------------------------------------------------- #
# diff / apply
# --------------------------------------------------------------------------- #
ROWS = (vm.VendorRow("001", "IVOCLAR VIVADENT"), vm.VendorRow("008", "GC"))


def test_diff_on_empty_table_is_all_added(conn):
    d = vm.diff(conn, ROWS)

    assert [r.code for r in d.added] == ["001", "008"]
    assert d.renamed == () and d.disappeared == () and d.unchanged == 0


def test_apply_then_diff_reports_no_change(conn):
    vm.apply(conn, ROWS, batch="b1")

    d = vm.diff(conn, ROWS)

    assert d.added == () and d.renamed == () and d.disappeared == ()
    assert d.unchanged == 2


def test_apply_is_idempotent(conn):
    vm.apply(conn, ROWS, batch="b1")
    stats = vm.apply(conn, ROWS, batch="b2")

    assert stats["added"] == 0 and stats["renamed"] == 0
    assert conn.execute("SELECT count(*) AS n FROM vendor_master").fetchone()["n"] == 2


def test_diff_detects_a_rename(conn):
    vm.apply(conn, ROWS, batch="b1")

    d = vm.diff(conn, (vm.VendorRow("001", "Ivoclar Vivadent AG"), ROWS[1]))

    assert d.renamed == (("001", "IVOCLAR VIVADENT", "Ivoclar Vivadent AG"),)
    assert d.added == () and d.disappeared == ()


def test_diff_detects_a_disappeared_code(conn):
    vm.apply(conn, ROWS, batch="b1")

    d = vm.diff(conn, (ROWS[0],))

    assert d.disappeared == ("008",)


def test_apply_refuses_a_rename_unless_allowed(conn):
    vm.apply(conn, ROWS, batch="b1")
    renamed = (vm.VendorRow("001", "Ivoclar Vivadent AG"), ROWS[1])

    with pytest.raises(vm.RenameRefused) as exc:
        vm.apply(conn, renamed, batch="b2")

    assert "001" in str(exc.value)
    row = conn.execute(
        "SELECT name FROM vendor_master WHERE code='001'"
    ).fetchone()
    assert row["name"] == "IVOCLAR VIVADENT", "refused import must write nothing"


def test_apply_applies_a_rename_when_allowed(conn):
    vm.apply(conn, ROWS, batch="b1")
    renamed = (vm.VendorRow("001", "Ivoclar Vivadent AG"), ROWS[1])

    stats = vm.apply(conn, renamed, batch="b2", allow_renames=True)

    assert stats["renamed"] == 1
    row = conn.execute("SELECT name FROM vendor_master WHERE code='001'").fetchone()
    assert row["name"] == "Ivoclar Vivadent AG"


def test_disappeared_code_is_kept_not_deleted(conn):
    """A vendor dropping out of the export is a fact to report, not to forget:
    deleting the row would strand any alias derived from it."""
    vm.apply(conn, ROWS, batch="b1")

    vm.apply(conn, (ROWS[0],), batch="b2")

    assert conn.execute("SELECT count(*) AS n FROM vendor_master").fetchone()["n"] == 2
    seen = conn.execute(
        "SELECT code, import_batch FROM vendor_master ORDER BY code"
    ).fetchall()
    assert seen[0]["import_batch"] == "b2"   # still present, refreshed
    assert seen[1]["import_batch"] == "b1"   # disappeared, left stale


def test_the_reader_stays_scoped_to_the_one_code_source(conn):
    """The column and its WHERE clause are kept (Denis, 2026-08-26: collapse
    the code paths, keep the columns), so a row under any other value must stay
    invisible. No API can write one any more -- hence the raw INSERT. This
    replaces a test asserting that two BC instances may issue the same code,
    which is the premise that was ruled out."""
    vm.apply(conn, (vm.VendorRow("001", "IVOCLAR VIVADENT"),), batch="b1")
    conn.execute(
        "INSERT INTO vendor_master (code_source, code, name, import_batch) "
        "VALUES ('OTHER','001','SOMETHING ELSE','b1')")

    assert vm._existing(conn) == {"001": "IVOCLAR VIVADENT"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cmd_dry_run_writes_nothing(conn, test_db_url, tmp_path, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "IVOCLAR VIVADENT"]])

    rc = cli.main(["vendor-master", "--file", str(f)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    assert conn.execute("SELECT count(*) AS n FROM vendor_master").fetchone()["n"] == 0


def test_cmd_apply_writes(connect_test, test_db_url, tmp_path, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "IVOCLAR VIVADENT"]])

    rc = cli.main(["vendor-master", "--file", str(f), "--apply"])

    assert rc == 0 and "1 added" in capsys.readouterr().out
    fresh = connect_test(autocommit=True)
    assert fresh.execute(
        "SELECT name FROM vendor_master WHERE code='001'"
    ).fetchone()["name"] == "IVOCLAR VIVADENT"


def test_cmd_refuses_rename_without_flag(connect_test, test_db_url, tmp_path,
                                         monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    f = _write_xlsx(tmp_path / "v.xlsx", [["001", "IVOCLAR VIVADENT"]])
    cli.main(["vendor-master", "--file", str(f), "--apply"])

    g = _write_xlsx(tmp_path / "w.xlsx", [["001", "Ivoclar Vivadent AG"]])
    rc = cli.main(["vendor-master", "--file", str(g), "--apply"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "write-once" in captured.err
    assert "Traceback" not in captured.err + captured.out
    fresh = connect_test(autocommit=True)
    assert fresh.execute(
        "SELECT name FROM vendor_master WHERE code='001'"
    ).fetchone()["name"] == "IVOCLAR VIVADENT"


def test_cmd_missing_file_is_a_clean_error(test_db_url, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["vendor-master", "--file", "does/not/exist.xlsx"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "Traceback" not in captured.err + captured.out


# --------------------------------------------------------------------------- #
# the real delivered file
# --------------------------------------------------------------------------- #
def test_real_export_shape():
    """Guards the delivered file's contract: if BC changes the column names or
    the row count moves a lot, this is where it surfaces."""
    import pathlib

    path = pathlib.Path("imports/Proizvajalci.xlsx")
    if not path.exists():
        pytest.skip("vendor master export not present")

    rows = vm.read_file(path)

    assert len(rows) == 390
    assert len({r.code for r in rows}) == 390          # codes unique
    assert sum(1 for r in rows if r.name is None) == 1  # exactly one nameless row
    named = [r for r in rows if r.name]
    assert len({r.name for r in named}) == 386          # many codes -> one name
