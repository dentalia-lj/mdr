"""The one item-documents query, shared by /api/items/{ref}/documents and
/item/{ref}. Extracted so the two routes cannot drift apart about what an
item's paperwork is (spec §6)."""

from __future__ import annotations

import pytest

from tests.test_web import _seed_item_with_doc
from web.item_docs import CUSTOMER_HIDDEN_DOC_FIELDS, CUSTOMER_HIDDEN_ITEM_FIELDS, item_documents


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
    """An item linked to two documents: the current one, and the older one it
    replaced. Mirrors what GATE actually writes -- supersession flips
    `document.status` and sets `superseded_by`, and never touches the
    `item_document` row (app/handlers/gate.py::_supersede), so the old
    document's link stays `production` and it drops out of
    `item_document_production` purely on the document's status."""
    old = _seed_item_with_doc(conn, item_ref, validity_to="2025-01-01")
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
