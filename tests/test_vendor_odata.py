"""Manufacturers read from BC rather than from a spreadsheet.

b-s.si added `allmanufacturers` on 2026-09-07, which is exactly what the
2026-09-03 analysis asked for: without it, a full OData switch would still have
left the code -> name mapping coming from a hand-exported `Proizvajalci.xlsx`,
and `resolve.group` falling back to a bare BC code as `canonical_manufacturer`
is the failure the playbooks spec was written to prevent.

**The property names were measured on live BC on 2026-10-05**: 390 records,
`code` (the key), `name`, `systemId`. Until then the profile guessed `no` for the
code, from BC's item pages, and the guard below turned that guess into a raise on
the first live record -- which is how it was found, and why the guard stays.
"""

from __future__ import annotations

import pytest

from app import vendor_master as vm
from app.adapters.source import UnknownOdataProperty


def test_it_reads_code_and_name():
    rows = vm.read_odata([
        {"code": "011", "name": "IVOCLAR VIVADENT", "systemId": "7e02abc1"},
        {"code": "081", "name": "CARL MARTIN", "systemId": "7f02abc1"},
    ])

    assert rows == (vm.VendorRow(code="011", name="IVOCLAR VIVADENT"),
                    vm.VendorRow(code="081", name="CARL MARTIN"))


def test_a_code_with_no_name_is_kept():
    """One of the 390 delivered spreadsheet rows is exactly this. A nameless
    code is still a real code that items point at."""
    rows = vm.read_odata([{"code": "099", "name": ""}])

    assert rows == (vm.VendorRow(code="099", name=None),)


def test_a_row_with_no_code_is_dropped():
    """It cannot be joined to anything."""
    assert vm.read_odata([{"code": "", "name": "ORPHAN"}]) == ()


def test_a_wrong_property_name_raises_rather_than_importing_nulls():
    """The failure this exists to prevent: 390 rows in, 390 nameless codes out,
    every manufacturer name in the registry replaced by nothing."""
    with pytest.raises(UnknownOdataProperty):
        vm.read_odata([{"Code": "011", "Name": "IVOCLAR"}])


def test_an_empty_payload_raises_rather_than_reading_as_a_disappearance():
    """`diff` reports every existing code as `disappeared` against an empty
    read, and `apply` would act on that. A transport failure must never look
    like BC deleting its manufacturer master."""
    with pytest.raises(RuntimeError, match="no manufacturer records"):
        vm.read_odata([])


def test_the_guessed_shape_raises():
    """The profile's guess before 2026-10-05, read against the live page. It
    must keep raising: a payload keyed `no` is not BC's manufacturer page."""
    with pytest.raises(UnknownOdataProperty):
        vm.read_odata([{"no": "011", "name": "IVOCLAR VIVADENT"}])


class _PagedBc:
    """BC's paging as measured: `@odata.nextLink` until the last page."""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.pages[url]


def test_fetch_follows_bcs_cursor_from_the_manufacturer_page():
    base = "http://bc.test/api/v1.0/companies(c1)"
    first = f"{base}/allmanufacturers"
    bc = _PagedBc({
        first: {"value": [{"code": "001", "name": "IVOCLAR VIVADENT"}],
                "@odata.nextLink": "http://bc.test/next-1"},
        "http://bc.test/next-1": {"value": [{"code": "002", "name": "STRAUMANN"}]},
    })

    records = vm.fetch_odata(bc, base + "/")

    assert bc.urls == [first, "http://bc.test/next-1"]
    assert [r["code"] for r in records] == ["001", "002"]
