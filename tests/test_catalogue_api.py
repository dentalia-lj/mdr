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


@pytest.fixture
def mfr_id(conn, canonical):
    """`manufacturer.id` for the canonical name 077 maps to, with the canonical
    `manufacturer_name` row production carries for every manufacturer
    (measured 2026-11-23: 384 of 384)."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (canonical,))
    mid = conn.execute("SELECT id FROM manufacturer WHERE canonical_name=%s",
                       (canonical,)).fetchone()["id"]
    conn.execute("INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
                 "VALUES (%s,%s,%s,'canonical') ON CONFLICT DO NOTHING",
                 (canonical.casefold(), canonical, mid))
    conn.commit()
    return mid


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
    body = client.get(f"/api/manufacturers/by-name/{canonical}").json()
    assert body["canonical_name"] == canonical
    assert MFR in body["codes"]
    assert body["items"] >= 1
    assert body["documents"] >= 1


def test_unknown_manufacturer_is_404(client):
    assert client.get("/api/manufacturers/by-name/No Such Maker Ltd").status_code == 404


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
    docs = client.get(f"/api/manufacturers/by-name/{canonical}/documents").json()["documents"]
    ids = [d["doc_id"] for d in docs]
    assert ids.count(doc_id) == 1, "a shared document was listed once per item"


def test_manufacturer_documents_customer_view_drops_internals(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-DCUST", manufacturer_raw=MFR)
    docs = client.get(
        f"/api/manufacturers/by-name/{canonical}/documents?view=customer").json()["documents"]
    assert docs
    assert "match_basis" not in docs[0]
    assert "expiry_basis" not in docs[0]


def test_manufacturer_documents_exclude_staged(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-DSTG", link_status="staged", manufacturer_raw=MFR)
    ids = [d["doc_id"] for d in
           client.get(f"/api/manufacturers/by-name/{canonical}/documents").json()["documents"]]
    staged = conn.execute(
        "SELECT doc_id FROM item_document WHERE item_ref='CAT-DSTG'").fetchone()
    assert staged["doc_id"] not in ids


# -------------------------------------------------------------------- items #

def test_manufacturer_items_paginate(client, conn, canonical):
    for n in range(5):
        _seed_item_with_doc(conn, f"CAT-P{n}", manufacturer_raw=MFR)
    body = client.get(
        f"/api/manufacturers/by-name/{canonical}/items?page=1&per_page=2").json()
    assert len(body["items"]) == 2
    assert body["page"] == 1 and body["per_page"] == 2
    assert body["total"] >= 5
    assert body["pages"] >= 3
    page2 = client.get(
        f"/api/manufacturers/by-name/{canonical}/items?page=2&per_page=2").json()
    assert {i["item_ref"] for i in body["items"]} & {
        i["item_ref"] for i in page2["items"]} == set(), "pages overlap"


def test_per_page_is_capped(client, conn, canonical):
    """~16k items today, ~100k at target. An uncapped per_page is a way to ask
    this endpoint for the whole catalogue in one request."""
    body = client.get(
        f"/api/manufacturers/by-name/{canonical}/items?per_page=99999").json()
    assert body["per_page"] <= 500


def test_page_beyond_the_end_is_empty_not_an_error(client, conn, canonical):
    _seed_item_with_doc(conn, "CAT-END", manufacturer_raw=MFR)
    body = client.get(
        f"/api/manufacturers/by-name/{canonical}/items?page=9999&per_page=50").json()
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

@pytest.mark.parametrize("path,shop", [
    ("/api/manufacturers", True),
    ("/api/manufacturers/12", True),
    ("/api/manufacturers/12/documents", True),
    # the supplier map: staff only since 2026-11-23 (webshop API spec § 5.1)
    ("/api/manufacturers/12/items", False),
    # addressed by name: staff only, the webstore uses the stable id
    ("/api/manufacturers/by-name/X", False),
    ("/api/manufacturers/by-name/X/documents", False),
    ("/api/manufacturers/X", False),
    ("/api/items/documents", True),
])
def test_every_new_endpoint_inherits_the_api_policy(path, shop):
    """Under /api/, therefore never bc, never anonymous; the webstore key only
    where the webstore needs it. Asserted, not assumed."""
    from web.access import SERVICE, STAFF, allowed_classes
    expected = {SERVICE, STAFF} if shop else {STAFF}
    assert allowed_classes(path) == frozenset(expected)


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

    body = client.get(f"/api/manufacturers/by-name/{canonical}/documents").json()
    assert doc_id not in [d["doc_id"] for d in body["documents"]]

    widened = client.get(
        f"/api/manufacturers/by-name/{canonical}/documents?include_unlinked=true").json()
    assert doc_id in [d["doc_id"] for d in widened["documents"]]


def test_a_link_only_document_still_resolves_with_no_binding(client, conn, canonical):
    """The fallback half of "column wins, links fill gaps": nothing that works
    today may stop working. This document has production links and a NULL
    canonical_manufacturer, which is every document in the registry before the
    backfill runs."""
    _seed_item_with_doc(conn, "CAT-FALLBACK", manufacturer_raw=MFR)
    conn.commit()

    body = client.get(f"/api/manufacturers/by-name/{canonical}/documents").json()
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
        f"/api/manufacturers/by-name/{canonical}/documents?include_unlinked=true").json()
    ids = [d["doc_id"] for d in body["documents"]]
    assert ids.count(doc_id) == 1


# ------------------------------------------------- batch: superseded chain #
def test_batch_passes_include_superseded_through(client, conn):
    """An order page reconciling old paperwork asks once, not once per line."""
    old = _seed_item_with_doc(conn, "BAT-SUP", validity_to="2025-01-01",
                              validity_from="2020-01-01")
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


# --------------------------------------------------------------------------- #
# The webstore key (2026-11-23 webshop API spec § 5.2): forced customer view,
# real-expired documents dropped, the two widening flags refused. Staff and the
# BC card are unchanged.
# --------------------------------------------------------------------------- #

SHOP_KEY = "shop-key-1"
BC_KEY = "bc-shared"
KEY = {"X-API-Key": SHOP_KEY}
STAFF = {"X-Forwarded-User": "admin"}


@pytest.fixture
def guarded(test_db_url, tmp_path):
    return TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        require_authenticated_user=True, api_keys=SHOP_KEY, bc_link_key=BC_KEY,
    )), raise_server_exceptions=False)


def _doc_ids(payload):
    return {d["doc_id"] for d in payload["documents"]}


def test_shop_view_drops_real_expired_on_every_route(guarded, conn, mfr_id):
    expired = _seed_item_with_doc(conn, "SHOP-EXP", manufacturer_raw=MFR,
                                  validity_from="2000-01-01", validity_to="2001-01-01")
    valid = _seed_item_with_doc(conn, "SHOP-OK", manufacturer_raw=MFR,
                                validity_to="2099-01-01")
    for headers, sees_expired in ((KEY, False), (STAFF, True)):
        one = guarded.get("/api/items/SHOP-EXP/documents", headers=headers).json()
        assert (expired in _doc_ids(one)) is sees_expired
        batch = guarded.get("/api/items/documents?ref=SHOP-EXP&ref=SHOP-OK",
                            headers=headers).json()
        assert (expired in _doc_ids(batch["items"][0])) is sees_expired
        assert valid in _doc_ids(batch["items"][1])
        line = guarded.get(f"/api/manufacturers/{mfr_id}/documents",
                           headers=headers).json()
        assert (expired in _doc_ids(line)) is sees_expired
        assert valid in _doc_ids(line)


def test_view_full_is_ignored_for_the_key(guarded, conn, mfr_id):
    _seed_item_with_doc(conn, "SHOP-FULL", manufacturer_raw=MFR, validity_to="2099-01-01")
    for path in ("/api/items/SHOP-FULL/documents?view=full",
                 "/api/items/documents?ref=SHOP-FULL&view=full",
                 f"/api/manufacturers/{mfr_id}/documents?view=full"):
        body = guarded.get(path, headers=KEY).json()
        docs = body["items"][0]["documents"] if "items" in body else body["documents"]
        assert docs, path
        assert all("match_basis" not in d and "expiry_basis" not in d for d in docs), path


@pytest.mark.parametrize("path", [
    "/api/items/SHOP-FLAG/documents?include_superseded=true",
    "/api/items/documents?ref=SHOP-FLAG&include_superseded=true",
    "/api/manufacturers/{m}/documents?include_unlinked=true",
])
def test_the_key_cannot_widen_the_row_set(guarded, conn, mfr_id, path):
    _seed_item_with_doc(conn, "SHOP-FLAG", manufacturer_raw=MFR, validity_to="2099-01-01")
    assert guarded.get(path.format(m=mfr_id), headers=KEY).status_code == 400
    # staff keeps both flags
    assert guarded.get(path.format(m=mfr_id), headers=STAFF).status_code == 200


def test_bc_card_is_unchanged_by_the_shop_view(guarded, conn):
    """The BC card shares `item_documents` but is not the webstore: it still
    lists the expired document, in the view it asked for."""
    expired = _seed_item_with_doc(conn, "SHOP-BC", validity_from="2000-01-01",
                                  validity_to="2001-01-01")
    body = guarded.get(f"/item/SHOP-BC?format=json&view=customer&k={BC_KEY}").json()
    assert expired in _doc_ids(body)



# --------------------------------------------------------------------------- #
# Manufacturers by stable id (2026-11-23 webshop API spec § 5.3)
# --------------------------------------------------------------------------- #

def test_manufacturer_by_id(guarded, conn, canonical, mfr_id):
    _seed_item_with_doc(conn, "ID-DET", manufacturer_raw=MFR, validity_to="2099-01-01")
    body = guarded.get(f"/api/manufacturers/{mfr_id}", headers=KEY).json()
    assert body["id"] == mfr_id
    assert body["canonical_name"] == canonical
    assert body["items"] >= 1
    assert "codes" not in body


def test_documents_by_id(guarded, conn, canonical, mfr_id):
    doc = _seed_item_with_doc(conn, "ID-DOCS", manufacturer_raw=MFR, validity_to="2099-01-01")
    body = guarded.get(f"/api/manufacturers/{mfr_id}/documents", headers=KEY).json()
    assert body["id"] == mfr_id and body["canonical_name"] == canonical
    assert doc in _doc_ids(body)


def test_unknown_id_is_404(guarded):
    assert guarded.get("/api/manufacturers/999999999/documents", headers=KEY).status_code == 404


def test_by_name_paths_are_staff_only(guarded, conn, canonical, mfr_id):
    _seed_item_with_doc(conn, "ID-BYNAME", manufacturer_raw=MFR, validity_to="2099-01-01")
    path = f"/api/manufacturers/by-name/{canonical}/documents"
    assert guarded.get(path, headers=KEY).status_code == 403
    assert guarded.get(path, headers=STAFF).status_code == 200


def test_id_survives_a_rename_without_playbooks_sync(guarded, conn):
    """The UI rename writes `manufacturer` and `manufacturer_name`, not
    `manufacturer_alias`, which keeps the old name until `playbooks sync`.
    The id must still find the codes, and answer with the NEW name."""
    import uuid

    from web import registry
    tag = uuid.uuid4().hex[:6]
    code, old, new = f"R{tag}", f"Old Maker {tag}", f"New Maker {tag}"
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'self-seed')", (code, old))
    mid = conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) RETURNING id",
                       (old,)).fetchone()["id"]
    conn.execute("INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
                 "VALUES (%s,%s,%s,'canonical')", (old.casefold(), old, mid))
    conn.commit()
    doc = _seed_item_with_doc(conn, f"ID-REN-{tag}", manufacturer_raw=code,
                              validity_to="2099-01-01")
    registry.rename_manufacturer(conn, canonical_name=old, new_name=new,
                                 note="test rename", user="t", confirm=True)
    conn.commit()
    body = guarded.get(f"/api/manufacturers/{mid}/documents", headers=KEY).json()
    assert body["canonical_name"] == new
    assert doc in _doc_ids(body)
    listed = guarded.get("/api/manufacturers", headers=KEY).json()["manufacturers"]
    row = next(m for m in listed if m["id"] == mid)
    assert row["canonical_name"] == new and row["documents"] >= 1


def test_shop_list_has_ids_no_codes_and_no_empty_rows(guarded, conn, mfr_id):
    _seed_item_with_doc(conn, "ID-LIST", manufacturer_raw=MFR, validity_to="2099-01-01")
    rows = guarded.get("/api/manufacturers", headers=KEY).json()["manufacturers"]
    assert rows and all("codes" not in r for r in rows)
    assert all(isinstance(r["id"], int) and r["documents"] >= 1 for r in rows)
    assert mfr_id in {r["id"] for r in rows}


def test_shop_list_drops_a_manufacturer_whose_only_document_expired(guarded, conn):
    import uuid
    tag = uuid.uuid4().hex[:6]
    code, name = f"E{tag}", f"Expired Only {tag}"
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'self-seed')", (code, name))
    mid = conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) RETURNING id",
                       (name,)).fetchone()["id"]
    conn.execute("INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
                 "VALUES (%s,%s,%s,'canonical')", (name.casefold(), name, mid))
    conn.commit()
    _seed_item_with_doc(conn, f"ID-EXPONLY-{tag}", manufacturer_raw=code,
                        validity_from="2000-01-01", validity_to="2001-01-01")
    shop = {r["id"] for r in guarded.get("/api/manufacturers", headers=KEY).json()["manufacturers"]}
    assert mid not in shop
    staff = guarded.get("/api/manufacturers", headers=STAFF).json()["manufacturers"]
    assert any(r["canonical_name"] == name for r in staff)


def test_staff_list_keeps_codes_and_gains_ids(guarded, conn, canonical, mfr_id):
    _seed_item_with_doc(conn, "ID-STAFF", manufacturer_raw=MFR, validity_to="2099-01-01")
    rows = guarded.get("/api/manufacturers", headers=STAFF).json()["manufacturers"]
    row = next(r for r in rows if r["canonical_name"] == canonical)
    assert MFR in row["codes"] and row["id"] == mfr_id
