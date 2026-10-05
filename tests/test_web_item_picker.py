"""The Review item picker over HTTP (spec §3, §4, §5.1)."""
from __future__ import annotations

import html
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

M = "STRAUMANN"


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path))))


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _mfr(conn, name=M, raw=None):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'vendor-master') ON CONFLICT (raw_name) DO NOTHING", (raw or name, name))


def _item(conn, ref, name, raw=M):
    conn.execute("INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
                 "mirror_rev, updated_at) VALUES (%s,%s,%s,NULL,'LJ',1,now())", (ref, name, raw))


def _doc(conn, key, *, status="staged", scope="group", canonical=M, issued="2026-04-01"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status, "
        "validity_from, canonical_manufacturer) VALUES ('IFU','MDR',%s,%s,%s,%s,%s,%s) "
        "RETURNING doc_id",
        (scope, f"wip-{key}", f"file:///archive/{key}__varibase.pdf", status, issued, canonical),
    ).fetchone()["doc_id"]


def _jobs(conn):
    return conn.execute("SELECT payload, dedupe_key FROM job WHERE type='gate.apply' ORDER BY id").fetchall()


def test_the_picker_opens_locked_on_the_documents_manufacturer(client, conn):
    _mfr(conn)
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    doc_id = _doc(conn, "lock")
    conn.commit()
    text = _text(client.get(f"/picker/{doc_id}").text)
    assert f"Manufacturer: {M} locked, from the document. Only its items can be found." in text
    assert "Done" in text and "Cancel" in text


def test_the_picker_asks_for_a_manufacturer_when_the_document_has_none(client, conn):
    _mfr(conn)
    _item(conn, "010.6042", "NC VARIOBASE")
    doc_id = _doc(conn, "nomfr", canonical=None)
    conn.commit()
    body = client.get(f"/picker/{doc_id}").text
    assert "Which manufacturer made these items?" in _text(body)
    assert 'name="q"' not in body          # no search before a choice
    chosen = _text(client.get(f"/picker/{doc_id}", params={"manufacturer": M}).text)
    assert f"Manufacturer: {M} chosen by you." in chosen


def test_a_whole_range_document_has_no_picker(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "range", scope="manufacturer")
    conn.commit()
    assert client.get(f"/picker/{doc_id}").status_code == 404


def test_results_search_and_cap(client, conn):
    _mfr(conn)
    for n in range(205):
        _item(conn, f"V{n:04d}", f"VARIOBASE {n}")
    doc_id = _doc(conn, "cap")
    conn.commit()
    text = _text(client.get(f"/picker/{doc_id}/results",
                            params={"manufacturer": M, "q": "variobase"}).text)
    assert "205 items match. Showing the closest 200; use a narrower word." in text


def test_a_number_search_lists_nearby_numbers_without_tick_all(client, conn):
    _mfr(conn)
    for n in range(1, 31):
        _item(conn, f"100.{n:03d}", f"PART {n}")
    doc_id = _doc(conn, "near")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "100.015"}).text
    text = _text(body)
    assert '1 item number contains "100.015".' in text
    assert "Nearby item numbers" in text and "100.005" in text and "100.025" in text
    nearby = body[body.index("Nearby item numbers"):]
    assert "data-picker-tick-all" not in nearby


def test_a_refused_row_cannot_be_ticked(client, conn):
    _mfr(conn)
    _item(conn, "R1", "VARIOBASE R")
    doc_id = _doc(conn, "ref")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "variobase"}).text
    assert re.search(r'<input type="checkbox" data-pick="R1"[^>]*disabled', body)
    assert "Refused earlier" in body


def test_the_manufacturer_param_cannot_unlock_a_locked_picker(client, conn):
    _mfr(conn)
    _mfr(conn, "OTHER")
    _item(conn, "X1", "VARIOBASE X", raw="OTHER")
    doc_id = _doc(conn, "unlock")
    conn.commit()
    r = client.get(f"/picker/{doc_id}/results", params={"manufacturer": "OTHER", "q": "variobase"})
    assert r.status_code == 200
    assert "X1" not in r.text
