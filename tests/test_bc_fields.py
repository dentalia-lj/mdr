"""What we assert into Dentalia's Business Central, and what we refuse to.

`app/bc_fields.py` answers a different question from `app/compliance.py`, which
is why it is a different module. The card asks *what does a reviewer need to
look at*; this asks *what do we state as fact inside the client's own ERP*.
They disagree on two states on purpose, and the tests naming those two are the
reason this file exists (see `test_expiring_today_is_still_valid` and
`test_staleness_horizon_does_not_falsify`).

Rules ruled by Denis 2026-09-07: a valid declaration covers the article or a
group the article belongs to, never the manufacturer alone, and is within its
validity. Design: `docs/superpowers/specs/2026-09-07-bc-writeback-design.md`.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.bc_fields import URL_MAX_LEN, fields_for, valid_ce, valid_doc, warehouse_url

TODAY = date(2026, 9, 7)


def holding(
    *,
    doc_type: str = "DoC",
    doc_status: str = "production",
    link_status: str = "production",
    coverage_scope: str = "group",
    expires: date | None = None,
    expiry_basis: str | None = None,
    certificate_status: str | None = None,
) -> dict:
    """One holding as the caller reads it out of the registry, overridable field
    by field. Same shape discipline as `tests/test_compliance.py::held`."""
    return {
        "doc_type": doc_type,
        "doc_status": doc_status,
        "link_status": link_status,
        "coverage_scope": coverage_scope,
        "expires": expires,
        "expiry_basis": expiry_basis,
        "certificate_status": certificate_status,
    }


def test_a_group_scope_production_declaration_is_valid():
    """The plain case: a declaration covering this article's group, live, unexpired."""
    assert valid_doc([holding()], today=TODAY) is True


# --------------------------------------------------------------------------- #
# coverage: the article, or a group it belongs to -- never the manufacturer
# --------------------------------------------------------------------------- #
def test_manufacturer_scope_alone_is_not_enough():
    """One ISO certificate carries 2.567 CARL MARTIN articles. A claim about the
    supplier's quality system is not evidence about the article, and pushing it
    into BC as one would be that trap in a new place."""
    assert valid_doc([holding(coverage_scope="manufacturer")], today=TODAY) is False


def test_a_staged_document_is_not_valid():
    assert valid_doc([holding(doc_status="staged")], today=TODAY) is False


def test_a_staged_link_is_not_valid():
    assert valid_doc([holding(link_status="staged")], today=TODAY) is False


# --------------------------------------------------------------------------- #
# validity: the basis decides, not the date
# --------------------------------------------------------------------------- #
def test_a_passed_stated_expiry_falsifies():
    """The document's own `validity_to`. This one is a real lapse."""
    assert valid_doc(
        [holding(expires=date(2026, 9, 6), expiry_basis="stated")], today=TODAY
    ) is False


def test_a_passed_inherited_expiry_falsifies():
    """A declaration citing a notified-body certificate inherits its expiry --
    MDR Art. 56 caps that at five years. Once the certificate has lapsed the
    declaration's grounds are gone, so the article is not covered."""
    assert valid_doc(
        [holding(expires=date(2026, 9, 6), expiry_basis="inherited")], today=TODAY
    ) is False


def test_staleness_horizon_does_not_falsify():
    """Dentalia's own five-year review horizon on a declaration that states no
    expiry. Migration 027 calls it "a review horizon, not a legal expiry", and
    reporting a house rule as a breach inside the client's ERP is the one error
    this field cannot afford. 139 declarations lapse on this basis."""
    assert valid_doc(
        [holding(expires=date(2021, 1, 1), expiry_basis="staleness")], today=TODAY
    ) is True


def test_expiring_today_is_still_valid():
    """Diverges from the reviewer card on purpose: `cell_state` colours a
    declaration lapsing within 30 days amber, because a human should chase it.
    It is still valid today, and today is what BC is being told about."""
    assert valid_doc(
        [holding(expires=TODAY, expiry_basis="stated")], today=TODAY
    ) is True


def test_no_date_of_any_kind_is_valid():
    """MDR Annex IV requires only an issue date on a declaration."""
    assert valid_doc([holding(expires=None, expiry_basis=None)], today=TODAY) is True


# --------------------------------------------------------------------------- #
# the CE field -- same coverage rule, two extra guards
# --------------------------------------------------------------------------- #
def test_an_ec_certificate_covering_the_group_is_valid():
    """MDR Annex XII requires a notified-body certificate to identify the devices
    it covers -- name, model and Basic UDI-DI for a type-examination, device
    groups for a quality-management-system one -- and the body must be able to
    show which individual devices any certificate covers. So a certificate has a
    determinate device scope, and `coverage_scope='group'` is that scope."""
    assert valid_ce([holding(doc_type="EC")], today=TODAY) is True


def test_iso_never_satisfies_the_ce_field():
    """ISO 13485 is a voluntary quality-standard certificate, not an MDR
    conformity certificate -- and it is the type carrying 2.567 CARL MARTIN
    articles on one document."""
    assert valid_ce([holding(doc_type="ISO")], today=TODAY) is False


def test_a_declaration_does_not_satisfy_the_ce_field():
    assert valid_ce([holding(doc_type="DoC")], today=TODAY) is False


@pytest.mark.parametrize(
    "status", ["withdrawn", "cancelled", "suspended", "restricted"]
)
def test_an_adverse_eudamed_status_falsifies(status):
    """A notified body can suspend, restrict, withdraw or cancel a certificate
    without its expiry date moving. An unexpired but suspended certificate
    reading true inside the client's ERP is the worst failure this field has."""
    assert valid_ce(
        [holding(doc_type="EC", certificate_status=status)], today=TODAY
    ) is False


@pytest.mark.parametrize("status", [None, "issued", "reinstated"])
def test_a_live_certificate_status_does_not_falsify(status):
    assert valid_ce(
        [holding(doc_type="EC", certificate_status=status)], today=TODAY
    ) is True


def test_the_ce_field_obeys_the_same_expiry_bases():
    assert valid_ce(
        [holding(doc_type="EC", expires=date(2026, 9, 6), expiry_basis="stated")],
        today=TODAY,
    ) is False


# --------------------------------------------------------------------------- #
# the warehouse URL
# --------------------------------------------------------------------------- #
BASE = "https://api.cw.dentalia.si"


def test_the_warehouse_url_is_the_bc_item_card_link():
    """BC builds hyperlinks by string concatenation and cannot compute anything,
    so the key travels in the URL. The route is live: `web/item_link.py:96`."""
    assert warehouse_url("0.900.0001", base_url=BASE, link_key="k3y") == (
        "https://api.cw.dentalia.si/item/0.900.0001?k=k3y"
    )


def test_an_item_ref_with_a_slash_is_quoted():
    """BC item numbers are free text. An unquoted slash would walk the path and
    hit a different route."""
    assert warehouse_url("A/17", base_url=BASE, link_key="k") == (
        "https://api.cw.dentalia.si/item/A%2F17?k=k"
    )


def test_the_url_fits_bc_s_column():
    """`pteWarehouseURL` is text[250]."""
    url = warehouse_url("0" * 40, base_url=BASE, link_key="k" * 40)
    assert len(url) <= URL_MAX_LEN


# --------------------------------------------------------------------------- #
# the whole item
# --------------------------------------------------------------------------- #
def test_an_unprocessed_item_is_never_written():
    """A boolean has no "not checked yet", and 3.987 of 4.265 flagged devices
    have never been searched. Writing false for those would assert "we looked
    and there is none" about an item nobody has looked at."""
    assert fields_for(
        "0.900.0001", [], processed=False, today=TODAY, base_url=BASE, link_key="k"
    ) is None


def test_a_processed_item_yields_all_three_fields():
    got = fields_for(
        "0.900.0001",
        [holding(), holding(doc_type="EC")],
        processed=True,
        today=TODAY,
        base_url=BASE,
        link_key="k",
    )
    assert got == {
        "pteValidDeclarationOfConformity": True,
        "pteValidCECertificate": True,
        "pteWarehouseURL": "https://api.cw.dentalia.si/item/0.900.0001?k=k",
    }


def test_a_processed_item_holding_nothing_still_gets_its_url():
    """Processed and empty is a real answer: we looked and found nothing. The
    link still points at the item page, which says so."""
    got = fields_for(
        "X1", [], processed=True, today=TODAY, base_url=BASE, link_key="k"
    )
    assert got["pteValidDeclarationOfConformity"] is False
    assert got["pteValidCECertificate"] is False
    assert got["pteWarehouseURL"].endswith("/item/X1?k=k")
