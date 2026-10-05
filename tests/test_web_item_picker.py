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


def test_approve_with_items_enqueues_them(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "A2", "VARIOBASE B")
    doc_id = _doc(conn, "appr")
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply",
                    data={"decision": "approve", "items": ["A2", "A1", "A2"]})
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["payload"]["items"] == ["A1", "A2"]
    assert "manufacturer" not in job["payload"]           # the document's own is locked


def test_approve_with_items_sends_the_chosen_manufacturer_when_the_document_has_none(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "chosen", canonical=None)
    conn.commit()
    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "approve", "items": ["A1"], "manufacturer": M})
    (job,) = _jobs(conn)
    assert job["payload"]["manufacturer"] == M


@pytest.mark.parametrize("case", ["other", "refused", "range", "nomfr", "conflict", "unknown"])
def test_a_bad_tick_set_is_refused_and_nothing_is_enqueued(client, conn, case):
    _mfr(conn)
    _mfr(conn, "OTHER")
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "X1", "VARIOBASE X", raw="OTHER")
    doc_id = _doc(conn, f"bad-{case}",
                  scope="manufacturer" if case == "range" else "group",
                  canonical=None if case == "nomfr" else M)
    data = {"decision": "approve", "items": ["A1"]}
    if case == "other":
        data["items"] = ["X1"]
    if case == "unknown":
        data["items"] = ["GONE"]
    if case == "refused":
        conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                     "VALUES ('A1', %s, 'name-family', 'rejected')", (doc_id,))
    if case == "conflict":
        data["manufacturer"] = "OTHER"
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data=data)
    assert r.status_code == 422
    assert "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []


def test_reject_carries_no_items(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "rej")
    conn.commit()
    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "reject", "reason": "Other", "items": ["A1"]})
    (job,) = _jobs(conn)
    assert "items" not in job["payload"]


def test_the_summary_follows_the_ticks_and_relabels_approve(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "A2", "VARIOBASE B")
    doc_id = _doc(conn, "sum")
    old = _doc(conn, "older-ifu", status="production", issued="2021-03-02")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('A1', %s, 'ref-list', 'production')", (old,))
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary",
                      params={"manufacturer": M, "items": ["A1", "A2"]}).text
    text = _text(body)
    assert "Approving makes it count for these 2 items" in text
    assert "1 of them already holds an IFU (1 older); this one is added beside it." in text
    assert re.search(rf'<button[^>]*id="approve-{doc_id}"[^>]*hx-swap-oob="true"', body)
    assert "Approve for these 2 items" in text


def test_add_items_enqueues_with_a_key_per_item_set(client, conn):
    _mfr(conn)
    for ref in ("A1", "A2"):
        _item(conn, ref, f"VARIOBASE {ref}")
    doc_id = _doc(conn, "add", status="production")
    conn.commit()
    client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})
    client.post(f"/documents/{doc_id}/items", data={"items": ["A2"]})   # a second batch, still queued
    client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})   # the same batch again
    jobs = _jobs(conn)
    assert [j["payload"]["items"] for j in jobs] == [["A1"], ["A2"]]
    assert all(j["payload"]["decision"] == "add-items" for j in jobs)
    assert len({j["dedupe_key"] for j in jobs}) == 2


def test_add_items_refuses_a_document_on_review(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "addstaged")
    conn.commit()
    r = client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})
    assert r.status_code == 422 and _jobs(conn) == []


def test_the_review_panel_offers_the_picker_only_for_specific_items(client, conn):
    _mfr(conn)
    group_doc = _doc(conn, "panel-g")
    range_doc = _doc(conn, "panel-r", scope="manufacturer")
    conn.commit()
    g = client.get(f"/staging/{group_doc}/detail").text
    assert "Find items this document covers" in _text(g)
    assert f'id="picker-items-{group_doc}"' in g and f'id="approve-{group_doc}"' in g
    assert "Find items this document covers" not in _text(client.get(f"/staging/{range_doc}/detail").text)


def test_the_page_loads_the_picker_script(client, conn):
    assert "item_picker.js" in client.get("/staging").text


def test_a_published_document_offers_add_items(client, conn):
    _mfr(conn)
    pub = _doc(conn, "page-pub", status="production")
    rng = _doc(conn, "page-rng", status="production", scope="manufacturer")
    conn.commit()
    page = client.get(f"/documents/{pub}").text
    assert "Add items this document covers" in _text(page)
    assert f'data-picker-url="/picker/{pub}"' in page
    assert "Add items this document covers" not in _text(client.get(f"/documents/{rng}").text)
    body = _text(client.get(f"/picker/{pub}").text)
    assert "Add these 0 items" in body and "Done" not in body


def test_a_manufacturer_gate_would_refuse_is_not_offered_or_accepted(client, conn):
    """Final review: `_mfr_binding_options` lists a bare BC code that has no
    `manufacturer` row; GATE (`_bindable_manufacturer`) refuses it, so the
    picker must neither offer nor accept it."""
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "B1", "VARIOBASE B", raw="BARECODE")       # no manufacturer row, no alias
    doc_id = _doc(conn, "bare", canonical=None)
    conn.commit()
    body = client.get(f"/picker/{doc_id}").text
    assert 'value="BARECODE"' not in body and f'value="{M}"' in body
    assert "Which manufacturer made these items?" in _text(
        client.get(f"/picker/{doc_id}", params={"manufacturer": "BARECODE"}).text)
    r = client.post(f"/staging/{doc_id}/apply",
                    data={"decision": "approve", "items": ["B1"], "manufacturer": "BARECODE"})
    assert r.status_code == 422 and _jobs(conn) == []
