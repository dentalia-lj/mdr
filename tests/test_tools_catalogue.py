"""tools.catalogue — profiles a BC export by driving the pipeline's own
CsvExportAdapter, so the profile reflects exactly what INGEST would see.

Tests run against a small synthetic .csv fixture (LJ_CSV_PROFILE columns), never
the real 19k-row export.
"""

from __future__ import annotations

import csv

import pytest

from app.adapters.source import LJ_CSV_PROFILE
from tools import catalogue

# LJ_CSV_PROFILE header -> logical field. Build a fixture with these exact BC
# columns so the adapter's schema-drift guard is satisfied.
H = LJ_CSV_PROFILE  # {"item_ref": "Št.", "manufacturer_raw": "Šifra proizvajalca", ...}

ROWS = [
    # item_ref, name, name_fallback, mfr_code, mfr_ref, md_class
    ("1001", "Item A", "", "001", "V-100", "RAZRED IIA"),   # MD, has ref, plain
    ("1002.5", "Item B", "", "001", "", "RAZRED IR"),        # MD, NO ref, dotted
    ("2003", "Item C", "", "008", "GC-3", "NI MP"),          # non-MD, has ref
    ("3004", "Item D", "", "008", "", ""),                   # unknown class, NO ref
    ("4005", "Item E", "", "", "X-5", "RAZRED I"),           # MD, blank code, plain
]


@pytest.fixture
def export_csv(tmp_path):
    path = tmp_path / "export.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([H["item_ref"], H["name"], H["name_fallback"],
                    H["manufacturer_raw"], H["mfr_ref"], H["md_class"]])
        w.writerows(ROWS)
    return str(path)


@pytest.fixture
def profiled(export_csv):
    return catalogue.profile(export_csv)


def test_profile_returns_rows_and_summary(profiled):
    rows, summary = profiled
    assert isinstance(rows, list) and isinstance(summary, dict)


def test_one_row_per_input_record(profiled):
    rows, _ = profiled
    assert len(rows) == len(ROWS)


def test_rows_have_stable_schema(profiled):
    rows, _ = profiled
    for row in rows:
        assert set(row) == set(catalogue.COLUMNS)


def test_md_flag_is_tri_state(profiled):
    _, summary = profiled
    assert summary["md_flag"] == {"true": 3, "false": 1, "unknown": 1}


def test_missing_mfr_ref_counted(profiled):
    _, summary = profiled
    assert summary["missing_mfr_ref"]["n"] == 2          # 1002.5 and 3004
    assert summary["missing_mfr_ref"]["pct"] == 40.0


def test_per_manufacturer_code_counts(profiled):
    _, summary = profiled
    assert summary["by_code"]["001"] == 2
    assert summary["by_code"]["008"] == 2
    assert summary["by_code"][""] == 1


def test_item_ref_style_split_plain_vs_dotted(profiled):
    """§G4: plain-style item refs (BC No == mfr number) vs dotted/suffixed."""
    _, summary = profiled
    assert summary["item_ref_style"] == {"plain": 4, "dotted": 1}


def test_total_items(profiled):
    _, summary = profiled
    assert summary["total_items"] == 5


def test_profile_is_deterministic(export_csv):
    assert catalogue.profile(export_csv)[1] == catalogue.profile(export_csv)[1]


def test_schema_drift_raises_a_clear_error(tmp_path):
    """A renamed/missing BC column must fail loudly, not map every row to None
    (the adapter's own guard — the tool must surface it, not swallow it)."""
    bad = tmp_path / "bad.csv"
    bad.write_text("wrong,columns\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        catalogue.profile(str(bad))


def test_render_returns_sections(profiled):
    _, summary = profiled
    sections = catalogue.render(summary)
    headings = [h for h, _ in sections]
    assert "Device-class split" in headings
    assert "mfr_ref coverage" in headings
    assert "Top manufacturer codes" in headings


def test_render_is_deterministic(profiled):
    _, summary = profiled
    assert catalogue.render(summary) == catalogue.render(summary)
