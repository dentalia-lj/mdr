"""The one item-documents query, shared by /api/items/{ref}/documents and
/item/{ref}. Extracted so the two routes cannot drift apart about what an
item's paperwork is (spec §6)."""

from __future__ import annotations

from datetime import date

import pytest

from tests.test_web import _seed_item_with_doc
from web.item_docs import (
    CUSTOMER_HIDDEN_DOC_FIELDS, CUSTOMER_HIDDEN_ITEM_FIELDS, file_name_of, is_legally_expired, item_documents,
)


def test_full_view_carries_the_internal_fields(conn):
    _seed_item_with_doc(conn, "ID-FULL")
    out = item_documents(conn, "ID-FULL")
    assert out["item_ref"] == "ID-FULL"
    assert out["manufacturer_code"] == "077"
    assert out["documents"][0]["match_basis"] == "ref-list"
    assert "expiry_basis" in out["documents"][0]


def test_customer_view_drops_every_internal_field(conn):
    """A clinic must not read our matching vocabulary or the inferred expiry
    basis -- `staleness` would read as a legal expiry we invented."""
    _seed_item_with_doc(conn, "ID-CUST")
    out = item_documents(conn, "ID-CUST", view="customer")
    for field in CUSTOMER_HIDDEN_ITEM_FIELDS:
        assert field not in out
    for field in CUSTOMER_HIDDEN_DOC_FIELDS:
        assert field not in out["documents"][0]
    # what the clinic legitimately needs to confirm the article is still there
    assert out["item_name"] == "Widget"
    assert out["mfr_ref"] == "W-1"
    assert out["documents"][0]["type"] == "DoC"


def test_base_url_makes_document_urls_absolute(conn):
    _seed_item_with_doc(conn, "ID-ABS")
    out = item_documents(conn, "ID-ABS", base_url="https://api.cw.dentalia.si")
    assert out["documents"][0]["url"].startswith(
        "https://api.cw.dentalia.si/documents/"
    )


def test_relative_url_when_no_base_url(conn):
    _seed_item_with_doc(conn, "ID-REL")
    out = item_documents(conn, "ID-REL")
    assert out["documents"][0]["url"].startswith("/documents/")


def test_unknown_item_returns_null_identity_and_no_documents(conn):
    out = item_documents(conn, "no-such-item-at-all")
    assert out["item_name"] is None
    assert out["manufacturer"] is None
    assert out["documents"] == []


def test_staged_link_is_invisible(conn):
    """Visibility rule: BOTH the document and the link must be production."""
    _seed_item_with_doc(conn, "ID-STAGED", link_status="staged")
    assert item_documents(conn, "ID-STAGED")["documents"] == []


def test_staged_document_is_invisible(conn):
    _seed_item_with_doc(conn, "ID-STAGEDOC", doc_status="staged")
    assert item_documents(conn, "ID-STAGEDOC")["documents"] == []


def _seed_superseded_pair(conn, item_ref):
    """An item linked to two documents: the current one, and the older one the
    old document-wide rule marked replaced (status `superseded`,
    `superseded_by` set, the `item_document` row untouched, so its link stays
    `production`). Since migration 072 the status decides nothing: the old
    document drops out of `item_document_production` because the newer one
    (validity_from 2024-01-01, the helper's default) outranks it for this
    item, and it would drop out the same way left at `production`."""
    old = _seed_item_with_doc(conn, item_ref, validity_to="2025-01-01",
                              validity_from="2020-01-01")
    new = _seed_item_with_doc(conn, item_ref, validity_to="2027-01-01")
    conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
        (new, old),
    )
    conn.commit()
    return old, new


def test_superseded_document_is_invisible_by_default(conn):
    """The visibility rule is unchanged for every existing caller: an
    unflagged response carries the current document and nothing else."""
    old, new = _seed_superseded_pair(conn, "ID-SUPER-DEFAULT")
    ids = [d["doc_id"] for d in item_documents(conn, "ID-SUPER-DEFAULT")["documents"]]
    assert ids == [new]
    assert old not in ids


def test_include_superseded_returns_the_replaced_document(conn):
    old, new = _seed_superseded_pair(conn, "ID-SUPER-ON")
    docs = item_documents(conn, "ID-SUPER-ON", include_superseded=True)["documents"]
    assert sorted(d["doc_id"] for d in docs) == sorted([old, new])


def test_include_superseded_carries_status_and_chain_pointer(conn):
    """Without these two fields the flag is useless: the caller cannot tell
    which row is current, nor walk from the old document to its replacement."""
    old, new = _seed_superseded_pair(conn, "ID-SUPER-CHAIN")
    by_id = {d["doc_id"]: d
             for d in item_documents(conn, "ID-SUPER-CHAIN",
                                     include_superseded=True)["documents"]}
    assert by_id[old]["status"] == "superseded"
    assert by_id[old]["superseded_by"] == new
    assert by_id[new]["status"] == "production"
    assert by_id[new]["superseded_by"] is None


def test_include_superseded_lists_newest_first(conn):
    """Spec §4: the history reads newest first -- `validity_from` descending,
    undated last, `doc_id` descending on a tie. Seeded in an order that the
    old `ORDER BY doc_id` gets wrong at every position."""
    ref = "ID-SUPER-ORDER"
    tie_low = _seed_item_with_doc(conn, ref, validity_from="2024-01-01")
    older = _seed_item_with_doc(conn, ref, validity_from="2020-01-01")
    undated = _seed_item_with_doc(conn, ref, validity_from=None)
    tie_high = _seed_item_with_doc(conn, ref, validity_from="2024-01-01")
    docs = item_documents(conn, ref, include_superseded=True)["documents"]
    assert [d["doc_id"] for d in docs] == [tie_high, tie_low, older, undated]


def test_the_default_response_keeps_its_order(conn):
    """Only the history changed order; the default stays by `doc_id`."""
    ref = "ID-DEFAULT-ORDER"
    first = _seed_item_with_doc(conn, ref, validity_from="2024-01-01")
    second = _seed_item_with_doc(conn, ref, validity_from="2024-01-01")
    docs = item_documents(conn, ref)["documents"]
    assert [d["doc_id"] for d in docs] == [first, second]


def test_unflagged_response_gains_no_new_fields(conn):
    """`status`/`superseded_by` are constant for an unflagged caller, and the
    batch endpoint would carry them 100 times a call for no information."""
    _seed_item_with_doc(conn, "ID-SUPER-SHAPE")
    doc = item_documents(conn, "ID-SUPER-SHAPE")["documents"][0]
    assert "status" not in doc
    assert "superseded_by" not in doc


def test_customer_view_refuses_the_flag(conn):
    """A superseded document is the paperwork we replaced. Rendering it on a
    clinic-facing page is exactly the harm the customer projection exists to
    prevent, so the combination is refused rather than silently ignored."""
    _seed_superseded_pair(conn, "ID-SUPER-CUST")
    with pytest.raises(ValueError):
        item_documents(conn, "ID-SUPER-CUST", view="customer",
                       include_superseded=True)


def test_flag_does_not_reveal_staged_or_rejected_documents(conn):
    """The flag widens the DOCUMENT status filter to production+superseded and
    nothing further -- staged is ungated machine output."""
    _seed_item_with_doc(conn, "ID-SUPER-STAGEDOC", doc_status="staged")
    assert item_documents(conn, "ID-SUPER-STAGEDOC",
                          include_superseded=True)["documents"] == []


def test_flag_does_not_relax_the_link_filter(conn):
    """A non-production link is a human saying this document does not cover
    this item. The flag must not override that in either direction."""
    _seed_item_with_doc(conn, "ID-SUPER-STAGEDLINK", link_status="staged")
    assert item_documents(conn, "ID-SUPER-STAGEDLINK",
                          include_superseded=True)["documents"] == []


def test_include_superseded_marks_which_document_is_current(conn):
    old, new = _seed_superseded_pair(conn, "ID-CURRENT")
    docs = {d["doc_id"]: d for d in
            item_documents(conn, "ID-CURRENT", include_superseded=True)["documents"]}
    assert docs[new]["current"] is True
    assert docs[old]["current"] is False


# --------------------------------------------------------------------------- #
# The webstore view (2026-11-23 webshop API spec § 5.2)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("legal_to,expected", [
    (date(2049, 12, 31), True),
    # the expiry day itself is still valid
    (date(2050, 1, 1), False),
    (date(2050, 1, 2), False),
    # no legal date (a class I DoC): never expires, only comes up for review
    (None, False),
])
def test_is_legally_expired(legal_to, expected):
    assert is_legally_expired(legal_to, date(2050, 1, 1)) is expected


def test_shop_view_drops_a_real_expired_document(conn):
    _seed_item_with_doc(conn, "ID-SHOP-EXP", validity_to="2001-01-01",
                        validity_from="2000-01-01")
    assert item_documents(conn, "ID-SHOP-EXP", for_shop=True)["documents"] == []
    # staff still sees it, to renew it
    assert len(item_documents(conn, "ID-SHOP-EXP")["documents"]) == 1


def test_shop_view_keeps_a_valid_document_in_the_customer_projection(conn):
    _seed_item_with_doc(conn, "ID-SHOP-OK", validity_to="2099-01-01")
    out = item_documents(conn, "ID-SHOP-OK", for_shop=True)
    (doc,) = out["documents"]
    assert str(doc["valid_to"]) == "2099-01-01"
    assert "valid to 2099-01-01" in doc["name"]
    for field in CUSTOMER_HIDDEN_DOC_FIELDS:
        assert field not in doc
    for field in CUSTOMER_HIDDEN_ITEM_FIELDS:
        assert field not in out


def test_shop_view_blanks_a_staleness_date(conn):
    """A DoC with an issue date and no stated expiry gets a review horizon
    (`staleness`). Printed on a clinic's page it reads as an expiry, so the
    webstore gets no date at all; staff keeps it."""
    _seed_item_with_doc(conn, "ID-SHOP-STALE", validity_to=None,
                        validity_from="2001-01-01")
    staff = item_documents(conn, "ID-SHOP-STALE")["documents"][0]
    assert staff["expiry_basis"] == "staleness"
    assert staff["valid_to"] is not None
    (doc,) = item_documents(conn, "ID-SHOP-STALE", for_shop=True)["documents"]
    assert doc["valid_to"] is None
    assert "valid to" not in doc["name"]


def test_shop_view_shows_the_stated_date_not_the_review_horizon(conn):
    """Issued 2024, stating validity to 2099: the review horizon (2029) wins
    `document_effective_expiry`, basis `staleness`. The clinic gets the
    document's own 2099, never 2029."""
    _seed_item_with_doc(conn, "ID-SHOP-LATE", validity_from="2024-01-01",
                        validity_to="2099-01-01")
    staff = item_documents(conn, "ID-SHOP-LATE")["documents"][0]
    assert staff["expiry_basis"] == "staleness"
    (doc,) = item_documents(conn, "ID-SHOP-LATE", for_shop=True)["documents"]
    assert str(doc["valid_to"]) == "2099-01-01"


def test_shop_view_drops_a_passed_stated_date_hidden_behind_staleness(conn):
    """Issued 2015, stating 2022: the horizon (2020) wins, basis `staleness`,
    yet the document's own validity ended in 2022. Dropped."""
    _seed_item_with_doc(conn, "ID-SHOP-HIDDEN", validity_from="2015-01-01",
                        validity_to="2022-01-01")
    assert item_documents(conn, "ID-SHOP-HIDDEN")["documents"][0]["expiry_basis"] == "staleness"
    assert item_documents(conn, "ID-SHOP-HIDDEN", for_shop=True)["documents"] == []


def test_shop_view_drops_a_document_whose_certificate_expired(conn):
    """A DoC with no date of its own, citing a production certificate that
    expired: the legal expiry is the certificate's."""
    doc_id = _seed_item_with_doc(conn, "ID-SHOP-CERT", validity_to=None,
                                 validity_from=None)
    cert = conn.execute(
        "INSERT INTO document (type, regulation, validity_to, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','2001-01-01','production',"
        "%s,'/archive/c.pdf','group') RETURNING doc_id",
        (f"h-cert-{doc_id}",)).fetchone()["doc_id"]
    conn.execute("UPDATE document SET cert_doc_id=%s WHERE doc_id=%s", (cert, doc_id))
    conn.commit()
    assert item_documents(conn, "ID-SHOP-CERT", for_shop=True)["documents"] == []
    assert len(item_documents(conn, "ID-SHOP-CERT")["documents"]) == 1



@pytest.mark.parametrize("archive_url,expected", [
    ("/archive/DENTAL_WORLD/ifu/0123456789ab__CreaFILL_Flow_IFU.pdf", "CreaFILL_Flow_IFU.pdf"),
    ("file:///srv/archive/GC/doc/abcdef012345__Fuji_Plus.pdf", "Fuji_Plus.pdf"),
    ("/imports/corpus/VOCO DoC 2021.pdf", "VOCO DoC 2021.pdf"),
    # a name that merely starts like a hash prefix keeps it
    ("/archive/x/abc__notahash.pdf", "abc__notahash.pdf"),
    ("", ""),
    (None, ""),
])
def test_file_name_of(archive_url, expected):
    assert file_name_of(archive_url) == expected


def test_shop_view_carries_the_file_name(conn):
    doc = _seed_item_with_doc(conn, "ID-FNAME", validity_to="2099-01-01")
    conn.execute("UPDATE document SET archive_url=%s WHERE doc_id=%s",
                 ("/archive/DW/ifu/0123456789ab__Canal_Prep_IFU.pdf", doc))
    conn.commit()
    (d,) = item_documents(conn, "ID-FNAME", for_shop=True)["documents"]
    assert d["file_name"] == "Canal_Prep_IFU.pdf"
    assert "item_count" not in d and "items" not in d
    # staff payload unchanged
    assert "file_name" not in item_documents(conn, "ID-FNAME")["documents"][0]
