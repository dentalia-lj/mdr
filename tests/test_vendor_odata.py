"""Manufacturers read from BC rather than from a spreadsheet.

b-s.si added `allmanufacturers` on 2026-09-07, which is exactly what the
2026-09-03 analysis asked for: without it, a full OData switch would still have
left the code -> name mapping coming from a hand-exported `Proizvajalci.xlsx`,
and `resolve.group` falling back to a bare BC code as `canonical_manufacturer`
is the failure the playbooks spec was written to prevent.

**The property names are unconfirmed.** Nobody has seen a payload -- external
access is blocked and no payload has been supplied. So the profile carries the
names BC uses for these two fields on every other page, and the same guard the
item reader has makes a wrong one raise on the first record instead of importing
390 nameless codes.
"""

from __future__ import annotations

import pytest

from app import vendor_master as vm
from app.adapters.source import UnknownOdataProperty


def test_it_reads_code_and_name():
    rows = vm.read_odata([
        {"no": "011", "name": "IVOCLAR VIVADENT"},
        {"no": "081", "name": "CARL MARTIN"},
    ])

    assert rows == (vm.VendorRow(code="011", name="IVOCLAR VIVADENT"),
                    vm.VendorRow(code="081", name="CARL MARTIN"))


def test_a_code_with_no_name_is_kept():
    """One of the 390 delivered spreadsheet rows is exactly this. A nameless
    code is still a real code that items point at."""
    rows = vm.read_odata([{"no": "099", "name": ""}])

    assert rows == (vm.VendorRow(code="099", name=None),)


def test_a_row_with_no_code_is_dropped():
    """It cannot be joined to anything."""
    assert vm.read_odata([{"no": "", "name": "ORPHAN"}]) == ()


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
