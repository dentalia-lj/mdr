"""tools.crossref — reproduces the manufacturer-code sweep as re-runnable code.

The two well-specified, corpus-independent pieces get thorough TDD here: the
confidence-tier assignment (sweep doc §Method thresholds) and the tracker↔BC
join. Tracker-file reading + orchestration are covered by a synthetic-fixture
integration test.
"""

from __future__ import annotations

from tools import crossref


# --------------------------------------------------------------------------- #
# tier() — sweep §Method confidence tiers (pure)
# --------------------------------------------------------------------------- #
def test_no_matches_is_none():
    assert crossref.tier("tracker", matches=0, concentration=0.0, volume_share=0.0) is None


def test_tracker_confirmed_needs_20_matches_and_60pct_concentration():
    assert crossref.tier("tracker", matches=20, concentration=0.60, volume_share=0.9) == "CONFIRMED"


def test_tracker_below_20_matches_is_not_confirmed():
    assert crossref.tier("tracker", matches=19, concentration=0.99, volume_share=0.9) != "CONFIRMED"


def test_tracker_below_60pct_concentration_is_not_confirmed():
    assert crossref.tier("tracker", matches=100, concentration=0.59, volume_share=0.9) != "CONFIRMED"


def test_text_confirmed_needs_10_hits_and_85pct_concentration():
    assert crossref.tier("text", matches=10, concentration=0.85, volume_share=0.5) == "CONFIRMED"


def test_text_at_tracker_threshold_is_not_confirmed_by_text_rule():
    # 10 hits / 60% concentration meets neither the text rule (needs 85%) nor
    # the tracker rule (needs 20) — falls through to LIKELY at most.
    assert crossref.tier("text", matches=10, concentration=0.60, volume_share=0.5) != "CONFIRMED"


def test_likely_needs_5_matches_50pct_and_5pct_volume():
    assert crossref.tier("tracker", matches=5, concentration=0.50, volume_share=0.05) == "LIKELY"


def test_likely_fails_when_volume_share_below_5pct():
    assert crossref.tier("tracker", matches=8, concentration=0.60, volume_share=0.04) == "WEAK"


def test_weak_is_any_signal_below_likely():
    assert crossref.tier("tracker", matches=3, concentration=0.30, volume_share=0.01) == "WEAK"


def test_confirmed_outranks_likely():
    # meets both CONFIRMED and LIKELY predicates -> CONFIRMED wins
    assert crossref.tier("tracker", matches=50, concentration=0.90, volume_share=0.50) == "CONFIRMED"


# --------------------------------------------------------------------------- #
# cross_reference() — tracker keys joined to BC item_ref (pure)
# --------------------------------------------------------------------------- #
BC = [
    {"item_ref": "1001", "manufacturer_raw": "001"},
    {"item_ref": "1002", "manufacturer_raw": "001"},
    {"item_ref": "1003", "manufacturer_raw": "001"},
    {"item_ref": "2001", "manufacturer_raw": "008"},
    {"item_ref": "9999", "manufacturer_raw": "001"},   # unmatched by the tracker
]


def test_join_matches_on_item_ref():
    r = crossref.cross_reference({"1001", "1002", "2001"}, BC)
    assert r["matches"] == 3


def test_join_finds_the_dominant_code():
    r = crossref.cross_reference({"1001", "1002", "2001"}, BC)
    assert r["dominant_code"] == "001"       # 2 of 3 matched rows


def test_join_concentration_is_dominant_over_matches():
    r = crossref.cross_reference({"1001", "1002", "2001"}, BC)
    assert round(r["concentration"], 2) == 0.67


def test_join_volume_share_is_dominant_matches_over_code_total():
    # dominant 001 has 4 BC items total (1001,1002,1003,9999); 2 matched -> 0.5
    r = crossref.cross_reference({"1001", "1002", "2001"}, BC)
    assert r["volume_share"] == 0.5


def test_join_with_no_matches_is_empty():
    r = crossref.cross_reference({"nope"}, BC)
    assert r["matches"] == 0
    assert r["dominant_code"] is None


def test_join_ignores_blank_codes_for_dominant():
    bc = [{"item_ref": "1", "manufacturer_raw": ""}, {"item_ref": "2", "manufacturer_raw": "005"}]
    r = crossref.cross_reference({"1", "2"}, bc)
    assert r["dominant_code"] == "005"


# --------------------------------------------------------------------------- #
# TRACKERS config
# --------------------------------------------------------------------------- #
def test_trackers_is_data_with_the_five_usable_trackers():
    brands = {t["brand"] for t in crossref.TRACKERS}
    assert {"IVOCLAR", "GC", "KOMET", "VOCO"} <= brands


def test_each_tracker_config_names_a_file_and_key_column():
    for t in crossref.TRACKERS:
        assert {"brand", "file", "key_column"} <= set(t)


# --------------------------------------------------------------------------- #
# read_tracker_keys + sweep (synthetic fixtures)
# --------------------------------------------------------------------------- #
import csv

import pytest

pd = pytest.importorskip("pandas")


def _write_xlsx(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=header).to_excel(path, index=False)


def test_read_tracker_keys_returns_the_key_column_values(tmp_path):
    f = tmp_path / "t.xlsx"
    _write_xlsx(f, ["Koda", "x"], [["596797", "a"], ["596798", "b"], ["", "c"]])
    keys = crossref.read_tracker_keys(str(f), "Koda")
    assert keys == {"596797", "596798"}          # blank dropped


def test_read_tracker_keys_missing_column_raises(tmp_path):
    f = tmp_path / "t.xlsx"
    _write_xlsx(f, ["Wrong"], [["1"]])
    with pytest.raises(ValueError):
        crossref.read_tracker_keys(str(f), "Koda")


def _synth_export(tmp_path):
    from app.adapters.source import LJ_CSV_PROFILE as H
    p = tmp_path / "export.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([H["item_ref"], H["name"], H["name_fallback"],
                    H["manufacturer_raw"], H["mfr_ref"], H["md_class"]])
        for i in range(30):                       # 30 IVOCLAR items under code 001
            w.writerow([f"5{i:05d}", f"Item {i}", "", "001", f"v{i}", "RAZRED IIA"])
        w.writerow(["70000", "Other", "", "008", "g", "RAZRED I"])
    return str(p)


def test_sweep_confirms_a_tracker_that_maps_cleanly_to_one_code(tmp_path):
    corpus = tmp_path / "corpus"
    # 25 of the 30 code-001 item refs present in the IVOCLAR Koda tracker
    _write_xlsx(corpus / "IVOCLAR" / "IVOCLAR MD or NOT.xlsx",
                ["Koda", "x"], [[f"5{i:05d}", "a"] for i in range(25)])
    rows = crossref.sweep(str(corpus), _synth_export(tmp_path))

    ivo = [r for r in rows if r["tracker_file"].startswith("IVOCLAR/IVOCLAR MD")]
    assert len(ivo) == 1
    assert ivo[0]["dominant_code"] == "001"
    assert ivo[0]["matches"] == 25
    assert ivo[0]["tier"] == "CONFIRMED"          # >=20 matches, 100% concentration


def test_sweep_marks_absent_trackers_without_crashing(tmp_path):
    corpus = tmp_path / "corpus"          # no tracker files at all
    corpus.mkdir()
    rows = crossref.sweep(str(corpus), _synth_export(tmp_path))
    assert len(rows) == len(crossref.TRACKERS)
    assert all(r["tier"] == "" and "absent" in r["note"] for r in rows)


def test_sweep_rows_have_stable_schema(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for r in crossref.sweep(str(corpus), _synth_export(tmp_path)):
        assert set(r) == set(crossref.ROW_COLUMNS)


def test_sweep_does_not_write_config():
    """Invariant 3 / sweep-doc posture: crossref emits candidates only; a human
    promotes CONFIRMED rows into Ingest config. The module must expose no writer."""
    assert not hasattr(crossref, "write_config")
    assert not any(name.startswith("write_ingest") for name in dir(crossref))
