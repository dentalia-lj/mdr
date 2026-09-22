"""BC REST API gap check: is the published API enough to fill our schema?

Three-way comparison, read-only, no DB and no network:

  1. the API sample (`docs/samples/bc-api-2026-09-02-*.json`, transcribed from
     screenshots — see that folder's README for what that costs),
  2. the Excel exports we ingest today (`imports/Artikli*.xlsx`, `Proizvajalci.xlsx`),
  3. what `item_mirror` (migration 002) and `vendor_master` (migration 016) require.

It answers one question: after a switch to OData, can INGEST still fill every column
it fills today? It uses the pipeline's own `LJ_CSV_PROFILE` and `_LOGICAL_FIELDS`, so
the "what we need" half cannot drift from what INGEST actually reads.

Run: `python -m tools bc-api`
"""

from __future__ import annotations

import json
import pathlib

from app.adapters.source import LJ_CSV_PROFILE, _LOGICAL_FIELDS

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
ITEMS_XLSX = ROOT / "imports" / "Artikli 3.7.2026.xlsx"
VENDORS_XLSX = ROOT / "imports" / "Proizvajalci.xlsx"

# item_mirror, migrations/002_ingest.sql:9-24. `catalogue` and `mirror_rev`/`updated_at`
# are supplied by the handler, not by any source, so they are not listed.
ITEM_MIRROR = {
    "item_ref": "PK, NOT NULL",
    "name": "NOT NULL",
    "manufacturer_raw": "NOT NULL",
    "mfr_ref": "nullable, REF-gate key",
    "md_flag": "nullable tri-state",
    "product_class": "nullable",
    "udi": "nullable",
}
# The logical field each item_mirror column is filled from (app/handlers/ingest.py
# writes NormalizedRow straight through; md_flag/product_class both derive from md_class).
COLUMN_SOURCE = {
    "item_ref": "item_ref",
    "name": "name",
    "manufacturer_raw": "manufacturer_raw",
    "mfr_ref": "mfr_ref",
    "md_flag": "md_class",
    "product_class": "md_class",
    "udi": "udi",
}
# vendor_master, migrations/016_vendor_master.sql:35-43.
VENDOR_MASTER = {"code": "PK part, NOT NULL", "name": "nullable but the whole point"}

# Logical field -> the API property that carries it, as far as the sample shows.
# None = nothing in the sample carries it.
API_FOR_LOGICAL = {
    "item_ref": "no",
    "name": "description",
    "manufacturer_raw": "manufacturerCode",
    "mfr_ref": "vendorItemNo",
    "md_class": "pteMedicalDeviceClass",
    "udi": None,
}
NAME_FALLBACK_API = "searchDescription"


def _load(name):
    return json.loads((SAMPLES / name).read_text())


def _frame(path):
    import pandas as pd

    return pd.read_excel(path, dtype=str, keep_default_na=False)


def _blank(series):
    return (series.fillna("").astype(str).str.strip() == "").sum()


def report() -> None:
    items = _frame(ITEMS_XLSX)
    vendors = _frame(VENDORS_XLSX)
    data = _load("bc-api-2026-09-02-dataitems.json")["value"]
    allitems = _load("bc-api-2026-09-02-allitems.json")["value"][0]
    props = set(allitems)
    n = len(items)

    print("=" * 78)
    print("1. DOES THE API AGREE WITH THE EXPORT?  dataitems vs Artikli xlsx")
    print("=" * 78)
    by_no = dict(zip(items["Št."].str.strip(), items["Opis"].str.strip()))
    hit = miss = absent = 0
    for rec in data:
        want = by_no.get(rec["no"])
        if want is None:
            absent += 1
            print(f"  NOT IN EXPORT  {rec['no']}")
        elif want.upper().startswith(rec["description"].upper()[:12]):
            hit += 1
        else:
            miss += 1
            print(f"  DIFFERS        {rec['no']}: api={rec['description']!r} xlsx={want!r}")
    print(f"  {hit} of {len(data)} match the export, {miss} differ, {absent} not in the export")

    print()
    print("=" * 78)
    print("2. ITEMS.  item_mirror column <- logical field <- API property")
    print("=" * 78)
    print(f"  {'column':17} {'requirement':22} {'CSV column today':32} {'API property':22} verdict")
    for col, req in ITEM_MIRROR.items():
        logical = COLUMN_SOURCE[col]
        csv_col = LJ_CSV_PROFILE.get(logical, "(none)")
        api = API_FOR_LOGICAL.get(logical)
        if api and api in props:
            verdict = "OK"
        elif csv_col == "(none)":
            verdict = "absent from BOTH (no regression)"
        else:
            verdict = "*** MISSING ***"
        print(f"  {col:17} {req:22} {csv_col:32} {str(api):22} {verdict}")

    print()
    print("  Coverage of each CSV column in the export, so the loss is quantified:")
    for logical in _LOGICAL_FIELDS:
        csv_col = LJ_CSV_PROFILE.get(logical)
        if not csv_col:
            continue
        filled = n - _blank(items[csv_col])
        api = API_FOR_LOGICAL.get(logical)
        state = "carried by API" if api and api in props else "NOT CARRIED"
        print(f"    {logical:17} {csv_col:32} {filled:6}/{n} rows  ->  {state}")

    print()
    print("  The two manufacturer columns, which is where this is decided:")
    for col in ("Šifra proizvajalca", "Šifra proizvajalca (primarni)  "):
        s = items[col].str.strip()
        print(f"    {col.strip():32} {(s != '').sum():6}/{n} rows, {s[s != ''].nunique()} distinct")
    print(f"    manufacturerCode in the payload   = {'PRESENT' if 'manufacturerCode' in props else 'ABSENT'}"
          f", value {allitems.get('manufacturerCode')!r}")
    print(f"    pteManufCodePrimary               = {allitems.get('pteManufCodePrimary')!r} (the abandoned sub-code field)")

    print()
    print("=" * 78)
    print("3. MANUFACTURERS.  vendor_master needs a code -> name master")
    print("=" * 78)
    codes = items["Šifra proizvajalca"].str.strip()
    known = set(vendors["Šifra"].str.strip())
    used = set(codes[codes != ""])
    print(f"  Proizvajalci.xlsx: {len(vendors)} rows, columns {list(vendors.columns)}")
    print(f"  item export uses {len(used)} distinct codes; {len(used - known)} unresolved by the master")
    for col, req in VENDOR_MASTER.items():
        print(f"  vendor_master.{col:6} ({req}) <- Proizvajalci.xlsx -> API endpoint: NONE")
    print("  Both published endpoints are ITEM pages. No manufacturer master is exposed.")

    print()
    print("=" * 78)
    print("4. WHAT THE API ADDS over the 25-column export")
    print("=" * 78)
    extra = {
        "lastDateTimeModified": "delta sync becomes possible (export has no timestamp)",
        "gtin": "empty here; a UDI-DI carrier if ever populated. NOT a Basic UDI-DI",
        "countryRegionOfOriginCode": "'DE' — corroborates manufacturer country",
        "pteOldNo": "explains the mfr_ref pollution: equals vendorItemNo on this row",
        "salesBlocked": "finer than the export's single 'Blokirano'",
        "purchasingBlocked": "as above",
    }
    for k, why in extra.items():
        mark = "yes" if k in props else "not in sample"
        print(f"  {k:28} {mark:14} {why}")

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    missing = [c for c, l in COLUMN_SOURCE.items()
               if LJ_CSV_PROFILE.get(l) and not (API_FOR_LOGICAL.get(l) in props)]
    if missing:
        print(f"  ITEMS: NOT sufficient as sampled. Missing: {', '.join(sorted(set(missing)))}")
        print("         `manufacturer_raw` is NOT NULL and is filled on 19.089/19.091 rows")
        print("         today. Confirm BC's manufacturer-code property from $metadata.")
    else:
        print("  ITEMS: sufficient.")
    print("  MANUFACTURERS: NOT sufficient. No endpoint exists over the manufacturer")
    print("         master, so vendor_master would still be loaded from a spreadsheet.")
    print("         Ask b-s.si for a third read-only page: Code + Name, 390 rows.")
