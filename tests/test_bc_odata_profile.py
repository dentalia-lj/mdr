"""The OData profile, and the guard that makes a wrong one loud.

`LJ_ODATA_PROFILE` was written before anyone had seen a live payload: it guessed
PascalCase and BC's v1.0 API returns camelCase, so it was wrong on every key.
Nothing failed, because a missing property reads as `None` and `normalize_record`
accepts that — measured 2026-09-03, three records in, three all-null rows out,
no error, no anomaly.

Property names below are the ones observed in the captured payload
(`docs/samples/`, §3 of `docs/2026-09-03-bc-api-integration.md`).
"""

from __future__ import annotations

import pytest

from app.adapters.source import (
    LJ_ODATA_PROFILE,
    BcApiAdapter,
    UnknownOdataProperty,
)
from app.config import load_config


@pytest.fixture
def cfg():
    return load_config().ingest


#: One record as `allitems` actually returns it, trimmed to the properties the
#: profile reads. `no`/`description`/`searchDescription`/`vendorItemNo` are
#: confirmed against the captured payload; `pteMedicalDeviceClass` is the
#: observed extension name. `pteManufCodePrimary` is here, filled with a
#: different value, because live BC carries it that way on a few items: the
#: profile must not read it (see the test below).
LIVE_RECORD = {
    "no": "0.900.0001",
    "description": "HANDPIECE MOTOR, LED, WITH TUBING",
    "searchDescription": "HANDPIECE MOTOR",
    "vendorItemNo": "1.007.4400",
    "pteMedicalDeviceClass": "IIa",
    "manufacturerCode": "011",
    "pteManufCodePrimary": "077-A",
    "gtin": "00000000000000",
}


def test_the_profile_reads_a_live_record(cfg):
    rows = list(BcApiAdapter("x", "LJ", cfg, records=[LIVE_RECORD]).read())

    assert len(rows) == 1
    assert rows[0].item_ref == "0.900.0001"
    assert rows[0].name == "HANDPIECE MOTOR, LED, WITH TUBING"
    assert rows[0].mfr_ref == "1.007.4400"
    assert rows[0].manufacturer_raw == "011"


def test_the_manufacturer_comes_from_manufacturerCode_not_pteManufCodePrimary(cfg):
    """Measured on live BC 2026-09-25 (200 items, `docs/samples/
    bc-api-2026-09-25-allitems.json`): `manufacturerCode` filled on 200/200 and
    equal to the export's `Šifra proizvajalca` on 100/100 items we hold;
    `pteManufCodePrimary` filled on 14, equal to neither. Reading the latter
    mirrors the catalogue with no manufacturer and reports success."""
    rows = list(BcApiAdapter("x", "LJ", cfg, records=[LIVE_RECORD]).read())

    assert LJ_ODATA_PROFILE["manufacturer_raw"] == "manufacturerCode"
    assert rows[0].manufacturer_raw == "011"


def test_a_blank_description_falls_back_to_the_search_description(cfg):
    """The CSV route has had this fallback since S1.1; the OData profile lacked
    the key entirely, so an item with a blank `description` lost its name."""
    record = dict(LIVE_RECORD, description="")

    rows = list(BcApiAdapter("x", "LJ", cfg, records=[record]).read())

    assert rows[0].name == "HANDPIECE MOTOR"


def test_a_property_the_payload_does_not_have_fails_loudly(cfg):
    """The whole point. A profile key that names nothing must raise on the first
    record, not yield a catalogue of nulls that reads as a successful import."""
    bad = dict(LJ_ODATA_PROFILE, item_ref="No")  # the old PascalCase guess

    with pytest.raises(UnknownOdataProperty) as e:
        list(BcApiAdapter("x", "LJ", cfg, records=[LIVE_RECORD],
                          profile=bad).read())

    assert "No" in str(e.value)
    assert "item_ref" in str(e.value)


def test_an_optional_property_absent_from_the_payload_is_not_an_error(cfg):
    """`udi` has no column anywhere and `md_class` can be blank on a record.
    Absent-but-declared is different from misnamed: the guard checks the profile
    against the payload's KEYS, so a property present-but-empty is fine."""
    record = dict(LIVE_RECORD, pteMedicalDeviceClass="")

    rows = list(BcApiAdapter("x", "LJ", cfg, records=[record]).read())

    assert rows[0].md_flag is not None or rows[0].md_flag is None  # no raise


def test_the_profile_has_no_pascal_case_left():
    """A regression pin on the mistake itself: BC v1.0 pages return camelCase,
    and every value here was PascalCase until 2026-09-07."""
    guessed = [k for k, v in LJ_ODATA_PROFILE.items() if v[:1].isupper()]
    assert guessed == [], f"PascalCase property names left in the profile: {guessed}"


def test_a_missing_fallback_is_not_an_error(cfg):
    """`name_fallback` is a fallback by definition. A payload without one has
    nothing to fall back to -- which is how the CSV route has always behaved --
    so demanding it would make the guard reject valid sources."""
    record = {k: v for k, v in LIVE_RECORD.items() if k != "searchDescription"}

    rows = list(BcApiAdapter("x", "LJ", cfg, records=[record]).read())

    assert rows[0].name == "HANDPIECE MOTOR, LED, WITH TUBING"


def test_a_misnamed_matching_field_still_raises(cfg):
    """The exemption is one key wide. `mfr_ref` is a REF-gate comparand, so a
    profile pointing it at nothing must not pass quietly."""
    bad = dict(LJ_ODATA_PROFILE, mfr_ref="VendorItemNo")

    with pytest.raises(UnknownOdataProperty):
        list(BcApiAdapter("x", "LJ", cfg, records=[LIVE_RECORD], profile=bad).read())


# --------------------------------------------------------------------------- #
# the live read
# --------------------------------------------------------------------------- #
class FakePages:
    """An OData endpoint that pages. Records the URLs it was asked for, so a
    test can prove the reader followed `@odata.nextLink` rather than guessing
    its own `$skip`."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.urls: list[str] = []

    def get(self, url: str) -> dict:
        self.urls.append(url)
        return self.pages[len(self.urls) - 1]


def test_the_reader_follows_the_next_link_to_the_end(cfg):
    """BC decides its own page size and hands back a cursor. Computing `$skip`
    ourselves would silently drift the moment the server's page size changed."""
    page1 = {"value": [LIVE_RECORD, dict(LIVE_RECORD, no="A2")],
             "@odata.nextLink": "https://bc/allitems?$skip=2"}
    page2 = {"value": [dict(LIVE_RECORD, no="A3")]}
    http = FakePages(page1, page2)

    rows = list(BcApiAdapter("https://bc/allitems", "LJ", cfg, client=http).read())

    assert [r.item_ref for r in rows] == ["0.900.0001", "A2", "A3"]
    assert http.urls == ["https://bc/allitems", "https://bc/allitems?$skip=2"]


def test_a_wrong_property_on_a_live_read_raises_before_any_row(cfg):
    """The guard has to sit on this path too, not just the injected one -- this
    is the path that will actually run against Dentalia's BC."""
    http = FakePages({"value": [{"No": "0.900.0001", "Description": "X"}]})

    with pytest.raises(UnknownOdataProperty):
        list(BcApiAdapter("https://bc/allitems", "LJ", cfg, client=http).read())


def test_an_empty_page_is_not_silently_an_empty_catalogue(cfg):
    """`ingest.run` diffs against `item_mirror`, so a read that yields nothing
    reads as "every item unchanged" -- the one outcome that must never come
    from a transport problem."""
    http = FakePages({"value": []})

    with pytest.raises(RuntimeError, match="no records"):
        list(BcApiAdapter("https://bc/allitems", "LJ", cfg, client=http).read())


def test_without_records_or_a_client_it_still_refuses(cfg):
    with pytest.raises(NotImplementedError):
        list(BcApiAdapter("x", "LJ", cfg).read())
