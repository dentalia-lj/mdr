"""The BC item-card link, `/item/{item_ref}?k=...` (spec §3).

One URL for two consumers: a support rep clicking from BC gets a page, the
webshop backend asking for JSON gets JSON.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.app import create_app

BC_KEY = "bc-shared"


@pytest.fixture
def guarded(test_db_url, tmp_path):
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        require_authenticated_user=True,
        api_keys="shop-key-1",
        bc_link_key=BC_KEY,
    )
    return TestClient(create_app(cfg), raise_server_exceptions=False)


def test_browser_gets_html(guarded, conn):
    _seed_item_with_doc(conn, "LNK-1")
    resp = guarded.get(f"/item/LNK-1?k={BC_KEY}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "LNK-1" in resp.text


def test_accept_json_gets_json(guarded, conn):
    _seed_item_with_doc(conn, "LNK-2")
    resp = guarded.get(
        f"/item/LNK-2?k={BC_KEY}", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "LNK-2"


def test_format_query_overrides_accept(guarded, conn):
    """BC's HTTP client is not guaranteed to send a useful Accept."""
    _seed_item_with_doc(conn, "LNK-3")
    resp = guarded.get(
        f"/item/LNK-3?k={BC_KEY}&format=json", headers={"Accept": "*/*"}
    )
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "LNK-3"


def test_item_ref_containing_a_slash_round_trips(guarded, conn):
    """Item refs contain slashes; the `:path` converter is why."""
    _seed_item_with_doc(conn, "AB/12")
    resp = guarded.get(f"/item/AB/12?k={BC_KEY}&format=json")
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "AB/12"


def test_customer_view_drops_internal_fields(guarded, conn):
    _seed_item_with_doc(conn, "LNK-CUST")
    body = guarded.get(
        f"/item/LNK-CUST?k={BC_KEY}&format=json&view=customer"
    ).json()
    assert "manufacturer_code" not in body
    assert "match_basis" not in body["documents"][0]
    assert "expiry_basis" not in body["documents"][0]


def test_wrong_key_is_404_never_403(guarded, conn):
    """A 403 would confirm the item exists."""
    _seed_item_with_doc(conn, "LNK-SECRET")
    assert guarded.get("/item/LNK-SECRET?k=wrong").status_code == 404
    assert guarded.get("/item/LNK-SECRET").status_code == 404


def test_unknown_item_is_200_with_nulls(guarded):
    """Matches read-api.md §2: 'no such item' is not this endpoint's
    distinction to make."""
    body = guarded.get(f"/item/nope-not-here?k={BC_KEY}&format=json").json()
    assert body["item_name"] is None
    assert body["documents"] == []


def test_uncovered_item_page_says_so(guarded, conn):
    _seed_item_with_doc(conn, "LNK-BARE", link_status="staged")
    resp = guarded.get(f"/item/LNK-BARE?k={BC_KEY}")
    assert resp.status_code == 200
    assert "No documents on file yet" in resp.text


def test_empty_bc_link_key_404s_the_route(test_db_url, tmp_path, conn):
    """Empty never means 'allow everyone'."""
    _seed_item_with_doc(conn, "LNK-NOKEY")
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        require_authenticated_user=True, bc_link_key="",
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    assert client.get("/item/LNK-NOKEY?k=").status_code == 404
    assert client.get("/item/LNK-NOKEY?k=anything").status_code == 404


def test_page_does_not_shadow_the_items_ui_route(guarded, conn):
    """`/item/` and `/items/` are different routes; adding one must not
    capture the other."""
    _seed_item_with_doc(conn, "LNK-UI")
    resp = guarded.get("/items", headers={"X-Forwarded-User": "admin"})
    assert resp.status_code == 200


import io
import zipfile


def _seed_doc_with_real_file(conn, tmp_path, item_ref):
    """A document whose archive_url points at bytes that actually exist, so
    the zip has something to put in it."""
    doc_id = _seed_item_with_doc(conn, item_ref)
    pdf = tmp_path / "seeded.pdf"
    pdf.write_bytes(b"%PDF-1.4 seeded\n")
    conn.execute(
        "UPDATE document SET archive_url=%s WHERE doc_id=%s", (str(pdf), doc_id)
    )
    conn.commit()
    return doc_id


def test_zip_contains_the_production_documents(test_db_url, tmp_path, conn):
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_doc_with_real_file(conn, tmp_path, "ZIP-1")
    resp = client.get(f"/item/ZIP-1/documents.zip?k={BC_KEY}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 1
        assert zf.namelist()[0].endswith(".pdf")


def test_zip_route_is_not_swallowed_by_the_item_route(test_db_url, tmp_path, conn):
    """`{item_ref:path}` is greedy: registered in the wrong order, the item
    route would match `/item/ZIP-2/documents.zip` with
    item_ref='ZIP-2/documents.zip' and render a page instead."""
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_doc_with_real_file(conn, tmp_path, "ZIP-2")
    resp = client.get(f"/item/ZIP-2/documents.zip?k={BC_KEY}")
    assert resp.headers["content-type"] == "application/zip"


def test_zip_of_an_uncovered_item_is_404(test_db_url, tmp_path, conn):
    """An empty archive is a worse answer than saying there is nothing."""
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_item_with_doc(conn, "ZIP-EMPTY", link_status="staged")
    assert client.get(f"/item/ZIP-EMPTY/documents.zip?k={BC_KEY}").status_code == 404


def test_bc_card_never_serves_the_supersession_chain(guarded, conn):
    """The card is paperwork for a person handling one item today. The history
    flag is an API affordance; BC must not reach it by appending a query
    parameter to the link it builds by string concatenation."""
    old = _seed_item_with_doc(conn, "LNK-SUP", validity_to="2025-01-01")
    new = _seed_item_with_doc(conn, "LNK-SUP", validity_to="2027-01-01")
    conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
        (new, old))
    conn.commit()

    body = guarded.get(
        f"/item/LNK-SUP?k={BC_KEY}&format=json&include_superseded=true"
    ).json()
    assert {d["doc_id"] for d in body["documents"]} == {new}
