"""INGEST source adapters (S1.1, PRD §1 / handbook §1).

Two adapters, one normalization core: `CsvExportAdapter` (BC export file) and
`BcApiAdapter` (OData, v2 stub). Both must produce byte-identical NormalizedRow
output — nothing downstream may detect the source (invariant 11). These tests
pin the field mapping, the tri-state `md_flag` derivation, the C1 `mfr_ref`
logical mapping, and the cross-adapter byte-identity contract.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.adapters import source as S
from app.config import Ingest


# --------------------------------------------------------------------------- #
# Normalization core — the shared derivation both adapters feed.
# --------------------------------------------------------------------------- #
def _raw(**over):
    base = {
        "item_ref": "0.900.0001",
        "name": "HANDPIECE MOTOR KL703",
        "manufacturer_raw": "011",
        "mfr_ref": "09000001",
        "md_class": "RAZRED IIA",
        "udi": None,
    }
    base.update(over)
    return base


DEFAULT = Ingest()


def test_normalize_maps_all_logical_fields():
    row = S.normalize_record(_raw(), "LJ", DEFAULT)
    assert row.item_ref == "0.900.0001"
    assert row.name == "HANDPIECE MOTOR KL703"
    assert row.manufacturer_raw == "011"
    assert row.mfr_ref == "09000001"
    assert row.product_class == "IIa"
    assert row.md_flag is True
    assert row.udi is None
    assert row.catalogue == "LJ"
    assert row.raw == _raw()  # original preserved for skipped-row reporting


@pytest.mark.parametrize(
    "md_class, flag, pclass",
    [
        ("RAZRED IIA", True, "IIa"),
        ("RAZRED IIB", True, "IIb"),
        ("RAZRED IR", True, "Ir"),
        ("RAZRED I", True, "I"),
        ("RAZRED III", True, "III"),
        ("NI MP", False, None),      # explicit non-MD
        ("", None, None),            # unclassified -> unknown
        (None, None, None),          # absent -> unknown
    ],
)
def test_md_flag_is_tristate(md_class, flag, pclass):
    row = S.normalize_record(_raw(md_class=md_class), "LJ", DEFAULT)
    assert row.md_flag is flag
    assert row.product_class == pclass


def test_blank_strings_normalize_to_none():
    row = S.normalize_record(
        _raw(mfr_ref="   ", manufacturer_raw="", udi=""), "LJ", DEFAULT
    )
    assert row.mfr_ref is None          # blank vendor article -> null (C1, counted downstream)
    assert row.manufacturer_raw is None
    assert row.udi is None


def test_missing_item_ref_yields_row_with_none_key():
    # The adapter never drops; the handler decides to skip. So a row missing its
    # key still comes back, with item_ref None and raw preserved.
    row = S.normalize_record(_raw(item_ref=""), "LJ", DEFAULT)
    assert row.item_ref is None
    assert row.raw["name"] == "HANDPIECE MOTOR KL703"


def test_mfr_ref_source_override_uses_item_ref():
    # C1 per-supplier mapping: for a CONFIRMED plain-style supplier, BC item No.
    # IS the manufacturer article number, so mfr_ref sources from item_ref.
    cfg = Ingest(mfr_ref_source_by_code={"011": "item_ref"})
    row = S.normalize_record(_raw(item_ref="196644050", mfr_ref="junk"), "LJ", cfg)
    assert row.mfr_ref == "196644050"


def test_mfr_ref_source_override_default_uses_mfr_ref_column():
    cfg = Ingest(mfr_ref_source_by_code={"999": "item_ref"})  # different code
    row = S.normalize_record(_raw(manufacturer_raw="011", mfr_ref="09000001"), "LJ", cfg)
    assert row.mfr_ref == "09000001"  # 011 not overridden -> column value


def test_md_class_with_unparseable_subclass_stays_md():
    row = S.normalize_record(_raw(md_class="RAZRED IuB??"), "LJ", DEFAULT)
    # It starts with RAZRED so it IS an MD; the subclass is unparseable, so we
    # carry a best-effort class string rather than silently dropping the signal.
    assert row.md_flag is True
    assert row.product_class  # non-empty


def test_md_class_lowercase_normalizes_to_canonical():
    row = S.normalize_record(_raw(md_class="razred iia"), "LJ", DEFAULT)
    assert row.md_flag is True and row.product_class == "IIa"


# --------------------------------------------------------------------------- #
# CsvExportAdapter — reads a BC export file through the LJ column profile.
# --------------------------------------------------------------------------- #
# Real LJ header row (Artikli 3.7.2026.xlsx), the columns the profile maps.
_LJ_HEADERS = [
    "Št.", "Opis", "Dobaviteljeva št. artikla", "Opis za iskanje",
    "Šifra proizvajalca", "Razred medicinskega pripomočka",
]


def _lj_row(st, opis, dobav, opis_isk, sifra, razred):
    return {
        "Št.": st, "Opis": opis, "Dobaviteljeva št. artikla": dobav,
        "Opis za iskanje": opis_isk, "Šifra proizvajalca": sifra,
        "Razred medicinskega pripomočka": razred,
    }


def _write_csv(path, rows):
    pd.DataFrame(rows, columns=_LJ_HEADERS).to_csv(path, index=False)


def _write_xlsx(path, rows):
    pd.DataFrame(rows, columns=_LJ_HEADERS).to_excel(path, index=False)


def test_csv_adapter_maps_lj_columns(tmp_path):
    p = tmp_path / "lj.csv"
    _write_csv(p, [
        _lj_row("0.900.0001", "MOTOR KL703", "09000001", "", "011", "RAZRED IIA"),
        _lj_row("196644050", "KOMET BUR", "", "", "081", "NI MP"),
    ])
    rows = list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())
    assert len(rows) == 2
    r0 = rows[0]
    assert (r0.item_ref, r0.name, r0.manufacturer_raw) == ("0.900.0001", "MOTOR KL703", "011")
    assert r0.mfr_ref == "09000001" and r0.md_flag is True and r0.product_class == "IIa"
    assert r0.udi is None  # no UDI column in the LJ export
    assert rows[1].md_flag is False and rows[1].mfr_ref is None


def test_csv_adapter_preserves_numeric_codes_as_strings(tmp_path):
    # item_ref / mfr_ref are codes: a leading-zero or long numeric must NOT become
    # a float ('09000001' -> not 321703, '85011093...' -> not 8.5e7).
    p = tmp_path / "lj.csv"
    _write_csv(p, [_lj_row("09000001", "X", "00123", "", "011", "RAZRED I")])
    r = list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())[0]
    assert r.item_ref == "09000001"
    assert r.mfr_ref == "00123"


def test_csv_adapter_name_falls_back_to_search_description(tmp_path):
    p = tmp_path / "lj.csv"
    _write_csv(p, [_lj_row("A1", "", "ref1", "SEARCH DESC", "011", "RAZRED I")])
    r = list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())[0]
    assert r.name == "SEARCH DESC"  # Opis blank -> Opis za iskanje


def test_xlsx_and_csv_produce_identical_rows(tmp_path):
    data = [
        _lj_row("0.900.0001", "MOTOR KL703", "09000001", "", "011", "RAZRED IIA"),
        _lj_row("A2", "OTHER", "", "SRCH", "041", ""),
    ]
    csv_p, xlsx_p = tmp_path / "lj.csv", tmp_path / "lj.xlsx"
    _write_csv(csv_p, data)
    _write_xlsx(xlsx_p, data)
    from_csv = list(S.CsvExportAdapter(str(csv_p), "LJ", DEFAULT).read())
    from_xlsx = list(S.CsvExportAdapter(str(xlsx_p), "LJ", DEFAULT).read())
    # Same catalogue data, different container -> identical normalized rows
    # (ignoring `raw`, whose dict values differ only in provenance).
    strip = lambda rs: [(r.item_ref, r.name, r.manufacturer_raw, r.mfr_ref,
                         r.md_flag, r.product_class, r.udi) for r in rs]
    assert strip(from_csv) == strip(from_xlsx)


def test_csv_adapter_unknown_source_file_type_raises(tmp_path):
    p = tmp_path / "lj.parquet"
    p.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())


def test_csv_adapter_missing_expected_column_fails_loudly(tmp_path):
    # A renamed/dropped BC column must dead-letter the job with an actionable
    # error, not silently map every row to None and skip the whole export.
    p = tmp_path / "drift.csv"
    pd.DataFrame(
        [{"Št.": "A1", "Opis": "X"}],  # missing mfr_ref / manufacturer / class cols
        columns=["Št.", "Opis"],
    ).to_csv(p, index=False)
    with pytest.raises(ValueError, match="missing expected column"):
        list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())


def test_csv_adapter_export_without_name_fallback_column_reads(tmp_path):
    # The 2026-09 export has no `Opis za iskanje`. It is only the fallback for a
    # blank `Opis`, so its absence is a fact about the export, not drift -- the
    # same rule the OData route applies (_OPTIONAL_PROFILE_KEYS).
    p = tmp_path / "no_fallback.xlsx"
    pd.DataFrame([{
        "Št.": "A1", "Opis": "Kompozit", "Šifra proizvajalca": "011",
        "Dobaviteljeva št. artikla": "REF-1",
        "Razred medicinskega pripomočka": "IIa",
    }]).to_excel(p, index=False)
    [row] = list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())
    assert (row.item_ref, row.name, row.manufacturer_raw) == ("A1", "Kompozit", "011")


def test_csv_adapter_still_refuses_a_missing_identity_column(tmp_path):
    # Optional means name_fallback only: dropping a real column still fails.
    p = tmp_path / "no_mfr.xlsx"
    pd.DataFrame([{"Št.": "A1", "Opis": "X", "Dobaviteljeva št. artikla": "R",
                   "Razred medicinskega pripomočka": "IIa"}]).to_excel(p, index=False)
    with pytest.raises(ValueError, match="Šifra proizvajalca"):
        list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())


def test_xlsx_numeric_typed_cell_reads_as_string(tmp_path):
    # A numeric-TYPED Excel cell (BC may store item/mfr numbers as numbers) must
    # read back as a plain string — no scientific notation, no trailing '.0' —
    # since these are REF-gate keys. (A leading zero already lost in a numeric
    # source cell is unrecoverable and out of scope: it's gone before pandas.)
    p = tmp_path / "num.xlsx"
    pd.DataFrame([{
        "Št.": 321703, "Opis": "X", "Dobaviteljeva št. artikla": 8501109300012,
        "Opis za iskanje": "", "Šifra proizvajalca": 11,
        "Razred medicinskega pripomočka": "RAZRED I",
    }]).to_excel(p, index=False)
    r = list(S.CsvExportAdapter(str(p), "LJ", DEFAULT).read())[0]
    assert r.item_ref == "321703"          # not '321703.0'
    assert r.mfr_ref == "8501109300012"    # not '8.501109300012e+12'
    assert r.manufacturer_raw == "11"


# --------------------------------------------------------------------------- #
# BcApiAdapter (v2 stub) + the byte-identity contract (invariant 11).
# --------------------------------------------------------------------------- #
def _odata_rec(no, desc, vendor, mfr_code, mdclass, udi=None):
    """One record shaped like a real `allitems` page.

    camelCase, corrected 2026-09-07: every name here was PascalCase, guessed
    before anyone had seen a live payload. `udi` is accepted and ignored -- no
    BC property carries one, `gtin` is not a Basic UDI-DI, and the profile
    dropped the key rather than manufacture the evidence.
    """
    return {"no": no, "description": desc, "vendorItemNo": vendor,
            "manufacturerCode": mfr_code, "pteMedicalDeviceClass": mdclass}


def test_bc_adapter_normalizes_odata_records():
    recs = [_odata_rec("0.900.0001", "MOTOR KL703", "09000001", "011", "RAZRED IIA")]
    rows = list(S.BcApiAdapter("ref", "LJ", DEFAULT, records=recs).read())
    assert rows[0].item_ref == "0.900.0001" and rows[0].product_class == "IIa"


def test_bc_adapter_with_neither_records_nor_a_client_refuses():
    # The live path exists now (2026-09-07: paged read, `@odata.nextLink`), so
    # this is no longer "the stub raises" -- it is that an adapter given no
    # source at all must fail loudly rather than return an empty catalogue,
    # which INGEST would diff as "0 items changed".
    with pytest.raises(NotImplementedError, match="records"):
        list(S.BcApiAdapter({"company": "X"}, "LJ", DEFAULT).read())


def test_both_adapters_are_byte_identical_downstream(tmp_path):
    # THE invariant-11 contract: the same catalogue data, one expressed as a BC
    # export file and one as OData records, must yield identical normalized rows.
    csv_data = [
        _lj_row("0.900.0001", "MOTOR KL703", "09000001", "", "011", "RAZRED IIA"),
        _lj_row("196644050", "KOMET BUR", "", "", "081", "NI MP"),
        _lj_row("A3", "UNKNOWN CLASS ITEM", "V3", "", "041", ""),
    ]
    odata_recs = [
        _odata_rec("0.900.0001", "MOTOR KL703", "09000001", "011", "RAZRED IIA"),
        _odata_rec("196644050", "KOMET BUR", "", "081", "NI MP"),
        _odata_rec("A3", "UNKNOWN CLASS ITEM", "V3", "041", ""),
    ]
    csv_p = tmp_path / "lj.csv"
    _write_csv(csv_p, csv_data)

    from_csv = list(S.CsvExportAdapter(str(csv_p), "LJ", DEFAULT).read())
    from_odata = list(S.BcApiAdapter("ref", "LJ", DEFAULT, records=odata_recs).read())

    fields = lambda rs: [(r.item_ref, r.name, r.manufacturer_raw, r.mfr_ref,
                          r.md_flag, r.product_class, r.udi, r.catalogue) for r in rs]
    assert fields(from_csv) == fields(from_odata)


# --------------------------------------------------------------------------- #
# make_source_adapter — the config-switched factory (closed source enum).
# --------------------------------------------------------------------------- #
def test_factory_builds_csv_adapter(tmp_path):
    p = tmp_path / "lj.csv"
    _write_csv(p, [_lj_row("A1", "X", "r", "", "011", "RAZRED I")])
    payload = {"source": "csv", "ref": str(p), "catalogue": "LJ"}
    adapter = S.make_source_adapter(payload, DEFAULT)
    rows = list(adapter.read())
    assert isinstance(adapter, S.CsvExportAdapter) and rows[0].item_ref == "A1"


def test_factory_resolves_relative_ref_against_imports_dir(tmp_path):
    p = tmp_path / "bc_export.csv"
    _write_csv(p, [_lj_row("A1", "X", "r", "", "011", "RAZRED I")])
    payload = {"source": "csv", "ref": "bc_export.csv", "catalogue": "LJ"}
    adapter = S.make_source_adapter(payload, DEFAULT, imports_dir=str(tmp_path))
    assert list(adapter.read())[0].item_ref == "A1"


def test_factory_builds_bc_adapter():
    payload = {"source": "bc_odata", "ref": {"company": "X"}, "catalogue": "LJ"}
    adapter = S.make_source_adapter(payload, DEFAULT)
    assert isinstance(adapter, S.BcApiAdapter)


def test_factory_rejects_unknown_source():
    with pytest.raises(ValueError, match="source"):
        S.make_source_adapter({"source": "ftp", "ref": "x", "catalogue": "LJ"}, DEFAULT)


# --------------------------------------------------------------------------- #
# UploadExportAdapter — same reader, bytes instead of a path (invariant 11).
# --------------------------------------------------------------------------- #
def test_upload_adapter_is_byte_identical_to_the_path_adapter(tmp_path):
    """Invariant 11: nothing after INGEST may detect which source is live.
    The same bytes read from a file and from a spool must normalize the same."""
    import dataclasses
    import pandas as pd
    from app.adapters import source as src
    from app.config import Ingest

    rows = [
        {"Št.": "001234", "Opis": "Composite syringe",
         "Dobaviteljeva št. artikla": "0450", "Opis za iskanje": "",
         "Šifra proizvajalca": "011", "Razred medicinskega pripomočka": "RAZRED IIA"},
        {"Št.": "005678", "Opis": "Bur", "Dobaviteljeva št. artikla": "",
         "Opis za iskanje": "", "Šifra proizvajalca": "011",
         "Razred medicinskega pripomočka": ""},
    ]
    path = tmp_path / "Artikli.xlsx"
    pd.DataFrame(rows, columns=[
        "Št.", "Opis", "Dobaviteljeva št. artikla", "Opis za iskanje",
        "Šifra proizvajalca", "Razred medicinskega pripomočka"]).to_excel(
            path, index=False)

    cfg = Ingest()
    from_path = list(src.CsvExportAdapter(str(path), "LJ", cfg).read())
    from_bytes = list(src.UploadExportAdapter(
        path.read_bytes(), "Artikli.xlsx", "LJ", cfg).read())

    assert [dataclasses.astuple(r) for r in from_path] == \
           [dataclasses.astuple(r) for r in from_bytes]


def test_upload_adapter_preserves_leading_zeros(tmp_path):
    """dtype=str is load-bearing: '001234' read as an int stops matching
    item_mirror.manufacturer_raw and corrupts the REF-gate key."""
    import pandas as pd
    from app.adapters import source as src
    from app.config import Ingest

    path = tmp_path / "Artikli.xlsx"
    pd.DataFrame([{
        "Št.": "001234", "Opis": "X", "Dobaviteljeva št. artikla": "0450",
        "Opis za iskanje": "", "Šifra proizvajalca": "011",
        "Razred medicinskega pripomočka": "RAZRED IIA"}]).to_excel(path, index=False)

    row = next(iter(src.UploadExportAdapter(
        path.read_bytes(), "Artikli.xlsx", "LJ", Ingest()).read()))
    assert row.item_ref == "001234"
    assert row.mfr_ref == "0450"


def test_upload_adapter_names_the_file_in_a_schema_drift_error(tmp_path):
    """The error must name the UPLOADED filename -- there is no path to fall
    back on, and 'missing column' with no file named is unactionable."""
    import pandas as pd
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    path = tmp_path / "wrong.xlsx"
    pd.DataFrame([{"Nope": "1"}]).to_excel(path, index=False)
    with pytest.raises(ValueError, match="wrong.xlsx"):
        list(src.UploadExportAdapter(path.read_bytes(), "wrong.xlsx", "LJ", Ingest()).read())


def test_upload_adapter_rejects_an_unsupported_suffix():
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    with pytest.raises(ValueError, match="unsupported export file type"):
        list(src.UploadExportAdapter(b"%PDF-", "cert.pdf", "LJ", Ingest()).read())


def test_make_source_adapter_routes_upload_to_the_bytes_reader():
    from app.adapters import source as src
    from app.config import Ingest

    payload = {"source": "upload", "ref": {"upload_id": 7}, "catalogue": "LJ"}
    adapter = src.make_source_adapter(
        payload, Ingest(), spool={"content": b"x", "filename": "a.csv"})
    assert isinstance(adapter, src.UploadExportAdapter)


def test_make_source_adapter_refuses_upload_without_a_spool_row():
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    payload = {"source": "upload", "ref": {"upload_id": 7}, "catalogue": "LJ"}
    with pytest.raises(ValueError, match="spool"):
        src.make_source_adapter(payload, Ingest())
