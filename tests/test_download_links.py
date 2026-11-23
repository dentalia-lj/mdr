"""Signed download links, issued on click (2026-11-23 webshop API spec § 5.4).

The webstore's server asks `/api/documents/{id}/link` with its key and sends
the customer's browser a 302 to the `/d/{token}` it gets back. The browser
carries no credential: the token is the credential, it names one document, it
dies after `download_link_ttl_s`, and visibility is checked again when it is
used.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web import download_links
from web.app import create_app

KEY = {"X-API-Key": "shop-key-1"}
SECRET = "test-download-secret"
BASE = "https://api.cw.dentalia.si"


def _client(tmp_path, **over):
    cfg = dict(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
               archive_root=str(tmp_path), require_authenticated_user=True,
               api_keys="shop-key-1", bc_link_key="bc-shared",
               public_base_url=BASE, download_link_secret=SECRET)
    cfg.update(over)
    return TestClient(create_app(Web(**cfg)), raise_server_exceptions=False)


@pytest.fixture
def guarded(test_db_url, tmp_path):
    return _client(tmp_path)


def _with_file(conn, tmp_path, doc_id):
    """Point the seeded document at a real PDF the archive root can serve."""
    pdf = tmp_path / f"doc-{doc_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test\n")
    conn.execute("UPDATE document SET archive_url=%s WHERE doc_id=%s",
                 (str(pdf), doc_id))
    conn.commit()


def _path(url):
    assert url.startswith(BASE)
    return url[len(BASE):]


# ------------------------------------------------------------------ token #

def test_sign_verify_roundtrip():
    token, exp = download_links.sign(19, SECRET, now=1000, ttl_s=300)
    assert exp == 1300
    assert download_links.verify(token, SECRET, now=1299) == 19
    # the last second of the lifetime still counts
    assert download_links.verify(token, SECRET, now=1300) == 19


@pytest.mark.parametrize("mangle", [
    lambda t: t,                                   # valid token, but expired below
    lambda t: "20" + t[t.index("."):],             # another doc_id
    lambda t: t.replace(".1300.", ".9999."),       # a later expiry
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),  # signature
    lambda t: "abc",
    lambda t: "",
    lambda t: "19.1300",
    lambda t: "x.y.z",
])
def test_verify_rejects(mangle):
    token, _ = download_links.sign(19, SECRET, now=1000, ttl_s=300)
    now = 1301 if mangle(token) == token else 1000
    assert download_links.verify(mangle(token), SECRET, now=now) is None


def test_verify_rejects_another_secret():
    token, _ = download_links.sign(19, SECRET, now=1000, ttl_s=300)
    assert download_links.verify(token, "other", now=1000) is None


# ------------------------------------------------------------------- issue #

def test_link_for_a_visible_document(guarded, conn):
    doc = _seed_item_with_doc(conn, "DL-OK", validity_to="2099-01-01")
    resp = guarded.get(f"/api/documents/{doc}/link", headers=KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith(f"{BASE}/d/{doc}.")
    expires = datetime.fromisoformat(body["expires_at"])
    left = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 280 < left <= 300


@pytest.mark.parametrize("seed", [
    dict(doc_status="staged", link_status="staged"),
    dict(doc_status="rejected", link_status="rejected"),
    dict(doc_status="filed", link_status="staged"),
    dict(link_status="staged"),  # published document, unpublished link
    dict(validity_from="2000-01-01", validity_to="2001-01-01"),  # expired
])
def test_no_link_for_an_invisible_document(guarded, conn, seed):
    doc = _seed_item_with_doc(conn, f"DL-NO-{len(str(seed))}-{seed.get('doc_status', 'p')}",
                              **{"validity_to": "2099-01-01", **seed})
    assert guarded.get(f"/api/documents/{doc}/link", headers=KEY).status_code == 404


def test_no_link_for_an_unknown_document(guarded):
    assert guarded.get("/api/documents/999999999/link", headers=KEY).status_code == 404


# ---------------------------------------------------------------- download #

def test_download_needs_no_credential(guarded, conn, tmp_path):
    doc = _seed_item_with_doc(conn, "DL-GET", validity_to="2099-01-01")
    _with_file(conn, tmp_path, doc)
    url = guarded.get(f"/api/documents/{doc}/link", headers=KEY).json()["url"]
    resp = guarded.get(_path(url))  # the customer's browser: nothing attached
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_download_rechecks_visibility(guarded, conn, tmp_path):
    doc = _seed_item_with_doc(conn, "DL-RECHECK", validity_to="2099-01-01")
    _with_file(conn, tmp_path, doc)
    url = guarded.get(f"/api/documents/{doc}/link", headers=KEY).json()["url"]
    conn.execute("UPDATE item_document SET status='staged' WHERE doc_id=%s", (doc,))
    conn.commit()
    assert guarded.get(_path(url)).status_code == 404


def test_download_expired_link_is_410(guarded, conn, tmp_path):
    doc = _seed_item_with_doc(conn, "DL-OLD", validity_to="2099-01-01")
    _with_file(conn, tmp_path, doc)
    token, _ = download_links.sign(doc, SECRET, now=1000, ttl_s=300)
    assert guarded.get(f"/d/{token}").status_code == 410


def test_download_altered_link_is_410(guarded, conn, tmp_path):
    doc = _seed_item_with_doc(conn, "DL-ALT", validity_to="2099-01-01")
    _with_file(conn, tmp_path, doc)
    url = guarded.get(f"/api/documents/{doc}/link", headers=KEY).json()["url"]
    assert guarded.get(_path(url)[:-3] + "xyz").status_code == 410


def test_download_never_redirects_to_storage(guarded, conn):
    doc = _seed_item_with_doc(conn, "DL-HTTP", validity_to="2099-01-01")
    conn.execute("UPDATE document SET archive_url='https://storage.example/x.pdf' "
                 "WHERE doc_id=%s", (doc,))
    conn.commit()
    url = guarded.get(f"/api/documents/{doc}/link", headers=KEY).json()["url"]
    resp = guarded.get(_path(url), follow_redirects=False)
    assert resp.status_code == 404


def test_empty_secret_switches_downloads_off(test_db_url, tmp_path, conn):
    client = _client(tmp_path, download_link_secret="")
    doc = _seed_item_with_doc(conn, "DL-OFF", validity_to="2099-01-01")
    assert client.get(f"/api/documents/{doc}/link", headers=KEY).status_code == 404
    token, _ = download_links.sign(doc, "", now=10**10, ttl_s=300)
    assert client.get(f"/d/{token}").status_code == 404


# ------------------------------------------------------------ shop payload #

def test_shop_urls_point_at_the_link_route(guarded, conn):
    doc = _seed_item_with_doc(conn, "DL-URL", validity_to="2099-01-01")
    shop = guarded.get("/api/items/DL-URL/documents", headers=KEY).json()
    assert shop["documents"][0]["url"] == f"{BASE}/api/documents/{doc}/link"
    staff = guarded.get("/api/items/DL-URL/documents",
                        headers={"X-Forwarded-User": "admin"}).json()
    assert staff["documents"][0]["url"] == f"{BASE}/documents/{doc}/file"
