"""`GET /api/search?q=` -- the webstore's query box (2026-11-23 webshop API
spec § 5.10): item number, supplier article number, Basic UDI-DI, item name,
manufacturer name. Only items with a document the webstore may show."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.app import create_app

KEY = {"X-API-Key": "shop-key-1"}


@pytest.fixture
def guarded(test_db_url, tmp_path):
    return TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        require_authenticated_user=True, api_keys="shop-key-1",
        bc_link_key="bc-shared", public_base_url="https://api.example")),
        raise_server_exceptions=False)


def _item(conn, ref, name, *, mfr_ref=None, validity_to="2099-01-01", code="077"):
    doc = _seed_item_with_doc(conn, ref, validity_to=validity_to,
                              validity_from="2000-01-01", manufacturer_raw=code)
    conn.execute("UPDATE item_mirror SET name=%s, mfr_ref=%s WHERE item_ref=%s",
                 (name, mfr_ref, ref))
    conn.commit()
    return doc


def _search(client, q):
    return client.get("/api/search", params={"q": q}, headers=KEY)


def _refs(body):
    return [(i["item_ref"], i["matched_on"]) for i in body["items"]]


def test_exact_item_number(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    _item(conn, f"S-{tag}", "Some Widget")
    body = _search(guarded, f"s-{tag}").json()
    assert _refs(body)[0] == (f"S-{tag}", "item_ref")
    assert body["items"][0]["documents"]


def test_exact_supplier_article_number(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    _item(conn, f"SM-{tag}", "Widget", mfr_ref=f"ART-{tag}")
    assert (f"SM-{tag}", "mfr_ref") in _refs(_search(guarded, f"art-{tag}").json())


def test_basic_udi_di_on_a_document(guarded, conn):
    tag = uuid.uuid4().hex[:8].upper()
    doc = _item(conn, f"SU-{tag}", "Widget")
    conn.execute("UPDATE document SET basic_udi_di=%s WHERE doc_id=%s",
                 (f"4260 1234 {tag}", doc))
    conn.commit()
    assert (f"SU-{tag}", "basic_udi_di") in _refs(_search(guarded, f"42601234{tag}").json())


def test_item_name_fuzzy(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    _item(conn, f"SN-{tag}", f"ORALNI GEL {tag} 3X3ML")
    assert (f"SN-{tag}", "name") in _refs(_search(guarded, f"oralni gel {tag}").json())


def test_an_item_with_only_expired_documents_is_not_returned(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    _item(conn, f"SX-{tag}", "Widget", validity_to="2001-01-01")
    assert _search(guarded, f"SX-{tag}").json()["items"] == []


def test_an_item_with_no_documents_is_not_returned(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    conn.execute("INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
                 "updated_at) VALUES (%s,'Bare',%s,'LJ',now())", (f"SB-{tag}", "077"))
    conn.commit()
    assert _search(guarded, f"SB-{tag}").json()["items"] == []


def test_at_most_twenty_items(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    for n in range(25):
        _item(conn, f"SL-{tag}-{n:02d}", f"MANYWIDGET {tag} {n:02d}")
    assert len(_search(guarded, f"manywidget {tag}").json()["items"]) == 20


def test_a_manufacturer_match_returns_the_manufacturer_not_its_items(guarded, conn):
    tag = uuid.uuid4().hex[:6]
    code, name = f"M{tag}", f"Searchable Maker {tag}"
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'self-seed')", (code, name))
    mid = conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) RETURNING id",
                       (name,)).fetchone()["id"]
    conn.execute("INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
                 "VALUES (%s,%s,%s,'canonical')", (name.casefold(), name, mid))
    conn.commit()
    _item(conn, f"SMF-{tag}", "Unrelated Thing", code=code)
    body = _search(guarded, f"searchable maker {tag}").json()
    assert [m["id"] for m in body["manufacturers"]] == [mid]
    assert "codes" not in body["manufacturers"][0]
    assert all(i["item_ref"] != f"SMF-{tag}" for i in body["items"])


@pytest.mark.parametrize("q", ["", "a", " b ", "x" * 101])
def test_query_length_is_bounded(guarded, q):
    assert _search(guarded, q).status_code == 400


def test_documents_are_in_the_webstore_view(guarded, conn):
    tag = uuid.uuid4().hex[:6].upper()
    doc = _item(conn, f"SV-{tag}", "Widget")
    (item,) = _search(guarded, f"SV-{tag}").json()["items"]
    (d,) = item["documents"]
    assert d["doc_id"] == doc
    assert d["url"].endswith(f"/api/documents/{doc}/link")
    assert "match_basis" not in d and "file_name" in d


def test_bc_key_and_anonymous_cannot_search(guarded):
    assert guarded.get("/api/search?q=abc&k=bc-shared").status_code == 401
    assert guarded.get("/api/search?q=abc").status_code == 401
