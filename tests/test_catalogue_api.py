"""Slice 2: manufacturer catalogue and order-batch endpoints.

Service-API-shaped, never public (spec §9): the webshop calls these
server-side and relays to its own logged-in customers, which is what keeps
Dentalia's supplier map off the open internet. They live under `/api/`, so
`web/access.py`'s existing policy covers them with no change -- asserted here
rather than assumed, because "it inherits the policy" is exactly the kind of
claim that silently stops being true when a prefix moves.

Production-visibility only. `registry.manufacturer_detail` deliberately shows
staged and filed documents because the UI's question is "why is this not
published yet"; a consumer's question is "what may I hand a customer", so
nothing here reuses it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.app import create_app

MFR = "077"


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                     imports_dir=str(tmp_path))))


@pytest.fixture
def canonical(conn):
    """The canonical name `manufacturer_raw` 077 maps to, whatever it is in
    this database -- never hardcoded, so the test does not depend on alias
    seed data it did not write."""
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer_alias WHERE raw_name=%s", (MFR,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
            "VALUES (%s,'Test Manufacturer GmbH','self-seed') ON CONFLICT DO NOTHING",
            (MFR,),
        )
        conn.commit()
        return "Test Manufacturer GmbH"
    return row["canonical_name"]


# --------------------------------------------------------------------- list #

def test_manufacturers_list_counts_only_production(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-PROD", manufacturer_raw=MFR)
    _seed_item_with_doc(conn, "CAT-STAGED", link_status="staged", manufacturer_raw=MFR)
    body = client.get("/api/manufacturers").json()
    row = next(m for m in body["manufacturers"] if m["canonical_name"] == canonical)
    assert row["items"] >= 2          # both items are mirrored
    assert row["documents"] >= 1      # only the production link counts
    assert body["total"] == len(body["manufacturers"])


def test_manufacturers_list_filters_by_q(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-Q", manufacturer_raw=MFR)
    body = client.get(f"/api/manufacturers?q={canonical[:6]}").json()
    assert all(canonical[:6].lower() in m["canonical_name"].lower()
               for m in body["manufacturers"])


# ------------------------------------------------------------------- detail #

def test_manufacturer_detail_returns_identity_and_counts(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-DET", manufacturer_raw=MFR)
    body = client.get(f"/api/manufacturers/{canonical}").json()
    assert body["canonical_name"] == canonical
    assert MFR in body["codes"]
    assert body["items"] >= 1
    assert body["documents"] >= 1


def test_unknown_manufacturer_is_404(client):
    assert client.get("/api/manufacturers/No Such Maker Ltd").status_code == 404


# ---------------------------------------------------------------- documents #

def test_manufacturer_documents_are_deduped_across_items(client, conn, canonical):
    """One certificate covering the whole line must appear once, not once per
    item -- that is the entire point of a manufacturer-level endpoint."""
    doc_id = _seed_item_with_doc(conn, "CAT-D1", manufacturer_raw=MFR)
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag,"
        " catalogue, updated_at) VALUES ('CAT-D2','Widget',%s,'W-2',TRUE,'LJ',now())"
        " ON CONFLICT (item_ref) DO NOTHING", (MFR,))
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('CAT-D2',%s,'ref-list','production') ON CONFLICT DO NOTHING", (doc_id,))
    conn.commit()
    docs = client.get(f"/api/manufacturers/{canonical}/documents").json()["documents"]
    ids = [d["doc_id"] for d in docs]
    assert ids.count(doc_id) == 1, "a shared document was listed once per item"


def test_manufacturer_documents_customer_view_drops_internals(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-DCUST", manufacturer_raw=MFR)
    docs = client.get(
        f"/api/manufacturers/{canonical}/documents?view=customer").json()["documents"]
    assert docs
    assert "match_basis" not in docs[0]
    assert "expiry_basis" not in docs[0]


def test_manufacturer_documents_exclude_staged(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-DSTG", link_status="staged", manufacturer_raw=MFR)
    ids = [d["doc_id"] for d in
           client.get(f"/api/manufacturers/{canonical}/documents").json()["documents"]]
    staged = conn.execute(
        "SELECT doc_id FROM item_document WHERE item_ref='CAT-DSTG'").fetchone()
    assert staged["doc_id"] not in ids


# -------------------------------------------------------------------- items #

def test_manufacturer_items_paginate(client, conn, canonical):
    for n in range(5):
        _seed_item_with_doc(conn, f"CAT-P{n}", manufacturer_raw=MFR)
    body = client.get(
        f"/api/manufacturers/{canonical}/items?page=1&per_page=2").json()
    assert len(body["items"]) == 2
    assert body["page"] == 1 and body["per_page"] == 2
    assert body["total"] >= 5
    assert body["pages"] >= 3
    page2 = client.get(
        f"/api/manufacturers/{canonical}/items?page=2&per_page=2").json()
    assert {i["item_ref"] for i in body["items"]} & {
        i["item_ref"] for i in page2["items"]} == set(), "pages overlap"


def test_per_page_is_capped(client, conn, canonical):
    """~16k items today, ~100k at target. An uncapped per_page is a way to ask
    this endpoint for the whole catalogue in one request."""
    body = client.get(
        f"/api/manufacturers/{canonical}/items?per_page=99999").json()
    assert body["per_page"] <= 500


def test_page_beyond_the_end_is_empty_not_an_error(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-END", manufacturer_raw=MFR)
    body = client.get(
        f"/api/manufacturers/{canonical}/items?page=9999&per_page=50").json()
    assert body["items"] == []
    assert body["total"] >= 1


# -------------------------------------------------------------------- batch #

def test_batch_returns_one_entry_per_requested_ref(client, conn):
    _seed_item_with_doc(conn, "BAT-1")
    _seed_item_with_doc(conn, "BAT-2")
    body = client.get("/api/items/documents?ref=BAT-1&ref=BAT-2").json()
    assert [i["item_ref"] for i in body["items"]] == ["BAT-1", "BAT-2"]
    assert all(i["documents"] for i in body["items"])


def test_batch_keeps_unknown_refs_in_the_response(client, conn):
    """An order line we have never mirrored must come back as a null-identity
    entry, not vanish -- the caller is rendering a row per line and needs to
    know which one has nothing."""
    _seed_item_with_doc(conn, "BAT-KNOWN")
    body = client.get(
        "/api/items/documents?ref=BAT-KNOWN&ref=BAT-NEVER-SEEN").json()
    entry = next(i for i in body["items"] if i["item_ref"] == "BAT-NEVER-SEEN")
    assert entry["item_name"] is None
    assert entry["documents"] == []


def test_batch_respects_customer_view(client, conn):
    _seed_item_with_doc(conn, "BAT-CUST")
    body = client.get("/api/items/documents?ref=BAT-CUST&view=customer").json()
    assert "manufacturer_code" not in body["items"][0]


def test_batch_refuses_too_many_refs(client):
    refs = "&".join(f"ref=R{n}" for n in range(101))
    resp = client.get(f"/api/items/documents?{refs}")
    assert resp.status_code == 400


def test_batch_with_no_refs_is_400(client):
    assert client.get("/api/items/documents").status_code == 400


# ------------------------------------------------------------------- access #

@pytest.mark.parametrize("path", [
    "/api/manufacturers",
    "/api/manufacturers/X",
    "/api/manufacturers/X/documents",
    "/api/manufacturers/X/items",
    "/api/items/documents",
])
def test_every_new_endpoint_inherits_the_api_policy(path):
    """Under /api/, therefore service+staff and never bc, never anonymous.
    Asserted, not assumed."""
    from web.access import SERVICE, STAFF, allowed_classes
    assert allowed_classes(path) == frozenset({SERVICE, STAFF})


# ------------------------------------------------- 053: bound-but-unlinked #

def _seed_bound_doc(conn, canonical, content_hash, status="filed"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, canonical_manufacturer) "
        "VALUES ('ISO','n.a.','manufacturer',%s,'file:///b.pdf',%s,%s) "
        "RETURNING doc_id", (content_hash, status, canonical)).fetchone()["doc_id"]


def test_a_bound_document_with_no_links_is_hidden_by_default(client, conn, canonical):
    """`manufacturer_documents` reads `item_document_production`, so a document
    with no production link is structurally invisible to it. Measured
    2026-08-31: /api/manufacturers/VOCO/documents returned 32 of the 270
    documents held under that name.

    053 makes them reachable -- but NOT by default. A filed document is real,
    held and evidenced, and explicitly NOT coverage; returning it unannounced
    would change what an existing consumer's payload means."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (canonical,))
    doc_id = _seed_bound_doc(conn, canonical, "cat-bound-1")
    conn.commit()

    body = client.get(f"/api/manufacturers/{canonical}/documents").json()
    assert doc_id not in [d["doc_id"] for d in body["documents"]]

    widened = client.get(
        f"/api/manufacturers/{canonical}/documents?include_unlinked=true").json()
    assert doc_id in [d["doc_id"] for d in widened["documents"]]


def test_a_link_only_document_still_resolves_with_no_binding(client, conn, canonical):
    """The fallback half of "column wins, links fill gaps": nothing that works
    today may stop working. This document has production links and a NULL
    canonical_manufacturer, which is every document in the registry before the
    backfill runs."""
    _seed_item_with_doc(conn, "CAT-FALLBACK", manufacturer_raw=MFR)
    conn.commit()

    body = client.get(f"/api/manufacturers/{canonical}/documents").json()
    assert body["documents"], "a link-only document must still be returned"
    assert all(d["doc_id"] for d in body["documents"])


def test_the_widened_view_does_not_duplicate_a_document_that_is_both(
        client, conn, canonical):
    """A document can be bound AND linked once the backfill runs. The union
    must return it once, not twice."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (canonical,))
    doc_id = _seed_item_with_doc(conn, "CAT-BOTH", manufacturer_raw=MFR)
    conn.execute("UPDATE document SET canonical_manufacturer=%s WHERE doc_id=%s",
                 (canonical, doc_id))
    conn.commit()

    body = client.get(
        f"/api/manufacturers/{canonical}/documents?include_unlinked=true").json()
    ids = [d["doc_id"] for d in body["documents"]]
    assert ids.count(doc_id) == 1


# ------------------------------------------------- batch: superseded chain #
def test_batch_passes_include_superseded_through(client, conn):
    """An order page reconciling old paperwork asks once, not once per line."""
    old = _seed_item_with_doc(conn, "BAT-SUP", validity_to="2025-01-01")
    new = _seed_item_with_doc(conn, "BAT-SUP", validity_to="2027-01-01")
    conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
        (new, old))
    conn.commit()

    off = client.get("/api/items/documents?ref=BAT-SUP").json()["items"][0]
    assert {d["doc_id"] for d in off["documents"]} == {new}

    on = client.get(
        "/api/items/documents?ref=BAT-SUP&include_superseded=true"
    ).json()["items"][0]
    assert {d["doc_id"] for d in on["documents"]} == {old, new}


def test_batch_customer_view_refuses_include_superseded(client, conn):
    _seed_item_with_doc(conn, "BAT-SUP-CUST")
    resp = client.get(
        "/api/items/documents?ref=BAT-SUP-CUST&view=customer&include_superseded=true")
    assert resp.status_code == 400
