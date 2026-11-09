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


def _refused(conn, doc_id, ref="R1"):
    _item(conn, ref, f"VARIOBASE {ref}")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s, %s, 'name-family', 'rejected')", (ref, doc_id))


def test_a_refused_row_can_be_ticked_on_purpose(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "ref")
    _refused(conn, doc_id)
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "variobase"}).text
    tag = re.search(r'<input type="checkbox" data-pick="R1"[^>]*>', body).group(0)
    assert "data-refused" in tag and "disabled" not in tag
    assert "Refused earlier" in body


def test_only_the_refused_row_is_marked_for_tick_all(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "A2", "VARIOBASE B")
    doc_id = _doc(conn, "ref-all")
    _refused(conn, doc_id)
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "variobase"}).text
    assert re.findall(r'data-pick="([^"]+)"[^>]*data-refused', body) == ["R1"]


def test_the_script_skips_refused_rows_in_tick_all():
    with open("web/static/item_picker.js", encoding="utf-8") as f:
        script = f.read()
    assert script.count('input[data-pick]:not(:disabled):not([data-refused])') == 2


def test_approve_with_reopen_enqueues_it(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "re-appr")
    _refused(conn, doc_id)
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply",
                    data={"decision": "approve", "items": ["R1"], "reopen": ["R1"]})
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["payload"]["items"] == ["R1"] and job["payload"]["reopen"] == ["R1"]


def test_add_items_with_reopen_enqueues_it(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "re-add", status="production")
    _refused(conn, doc_id)
    conn.commit()
    r = client.post(f"/documents/{doc_id}/items", data={"items": ["R1"], "reopen": ["R1"]})
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["payload"]["reopen"] == ["R1"]


def test_a_refused_item_without_reopen_is_refused(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "re-no")
    _refused(conn, doc_id)
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data={"decision": "approve", "items": ["R1"]})
    assert r.status_code == 422 and _jobs(conn) == []
    assert "refused earlier" in _text(r.text)


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
    assert "Approving publishes it for these 2 items" in text
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


# --- taking an item off on Review (spec 2026-11-09 §5.1) ------------------------

def _linked(conn, doc_id, ref, status="staged", basis="ref-list", name=None):
    _item(conn, ref, name or f"VARIOBASE {ref}")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s, %s, %s, %s::link_status)", (ref, doc_id, basis, status))


def test_approve_with_remove_enqueues_it(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "rm")
    _linked(conn, doc_id, "A1"); _linked(conn, doc_id, "A2")
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data={"decision": "approve", "remove": ["A1", "A1"]})
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["payload"]["remove"] == ["A1"]
    assert job["dedupe_key"] == f"apply:{doc_id}:approve"


@pytest.mark.parametrize("case", ["no-link", "also-ticked", "not-on-review"])
def test_a_bad_remove_is_refused_and_nothing_is_enqueued(client, conn, case):
    _mfr(conn)
    doc_id = _doc(conn, f"bad-{case}", status="production" if case == "not-on-review" else "staged")
    _linked(conn, doc_id, "A1")
    conn.commit()
    data = {"decision": "approve", "remove": ["A9" if case == "no-link" else "A1"]}
    if case == "also-ticked":
        data["items"] = ["A1"]
    r = client.post(f"/staging/{doc_id}/apply", data=data)
    assert r.status_code == 422
    assert "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []


def test_the_summary_moves_a_removed_item_and_recounts(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "moved")
    _linked(conn, doc_id, "A1"); _linked(conn, doc_id, "A2")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary", params={"remove": ["A1"]}).text
    text = _text(body)
    assert "Approving publishes it for this item" in text
    assert "Will not count (1)" in text
    assert "A later re-read of this document will not link them again." in text
    assert 'data-undo="A1"' in body and 'data-remove="A1"' not in body
    assert 'data-remove="A2"' in body
    assert "Approve for this item" in text


def test_remove_is_offered_only_on_what_approve_publishes(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "offer")
    _linked(conn, doc_id, "A1")
    _linked(conn, doc_id, "W1", basis="fetch-context")
    whole = _doc(conn, "offer-whole", scope="manufacturer")
    _linked(conn, whole, "A3")
    conn.commit()
    body = client.get(f"/staging/{doc_id}/detail").text
    assert 'data-remove="A1"' in body and 'data-remove="W1"' not in body
    assert f'id="picker-removed-{doc_id}"' in body
    assert "data-remove=" not in client.get(f"/staging/{whole}/detail").text


def test_removing_everything_reads_as_no_items(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "all-gone")
    _linked(conn, doc_id, "A1")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary", params={"remove": ["A1"]}).text
    text = _text(body)
    assert "Nothing links it to an item yet" in text
    assert "Will not count (1)" in text
    assert "Approve for" not in text


def test_a_ticked_item_is_tagged_and_has_no_remove(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "tick-rm")
    _linked(conn, doc_id, "A1")
    _item(conn, "T1", "VARIOBASE T")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary",
                      params={"manufacturer": M, "items": ["T1"], "remove": ["A1"]}).text
    text = _text(body)
    assert "you chose" in text
    assert 'data-remove="T1"' not in body
    assert "Approving publishes it for this item" in text
    assert "Will not count (1)" in text


def test_a_slashed_item_number_survives_the_summary(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "slash")
    _linked(conn, doc_id, "AB/1 2")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary", params={"remove": ["AB/1 2"]}).text
    assert 'data-undo="AB/1 2"' in body
    assert "Will not count (1)" in _text(body)


def test_the_summary_says_when_the_document_left_review(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "gone", status="production")
    _linked(conn, doc_id, "A1", status="production")
    conn.commit()
    r = client.get(f"/picker/{doc_id}/summary", params={"remove": ["A1"]})
    assert r.status_code == 404 and "no longer on Review" in r.text


# --- taking an item off a published document (spec 2026-11-09 §5.2) ------------

def _ask(client, doc_id, ref="A1", row="3", **extra):
    return client.post(f"/links/{doc_id}/decide",
                       data={"item_ref": ref, "decision": "remove-link", "row": row, **extra})


def _published(conn, key, ref="A1", **kw):
    _mfr(conn)
    doc_id = _doc(conn, key, status="production", **kw)
    _linked(conn, doc_id, ref, status="production")
    return doc_id


def test_remove_link_first_step_asks_and_writes_nothing(client, conn):
    doc_id = _published(conn, "ask")
    conn.commit()
    r = _ask(client, doc_id)
    text = _text(r.text)
    assert r.status_code == 200
    assert "Remove A1 from this document?" in text and "Yes, remove A1" in text
    assert f'hx-target="#link-slot-{doc_id}-3"' in r.text
    assert _jobs(conn) == []


def test_remove_link_warns_only_when_it_is_the_last(client, conn):
    doc_id = _published(conn, "last")
    conn.commit()
    assert "A1 will have no IFU left." in _text(_ask(client, doc_id).text)

    other = _doc(conn, "last-other", status="production")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('A1', %s, 'manual', 'production')", (other,))
    conn.commit()
    assert "will have no IFU left" not in _text(_ask(client, doc_id).text)


def test_remove_link_with_confirm_enqueues_and_clears_the_button(client, conn):
    doc_id = _published(conn, "yes")
    conn.commit()
    r = _ask(client, doc_id, confirm="1")
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["dedupe_key"] == f"apply:{doc_id}:A1:remove-link"
    assert job["payload"]["decision"] == "remove-link" and job["payload"]["item_ref"] == "A1"
    assert re.search(rf'id="link-cell-{doc_id}-3"[^>]*hx-swap-oob="innerHTML"', r.text)


def test_remove_link_target_is_the_row_not_the_item_number(client, conn):
    doc_id = _published(conn, "slash-pub", ref="AB/1 2")
    conn.commit()
    r = _ask(client, doc_id, ref="AB/1 2")
    assert r.status_code == 200
    assert "Yes, remove AB/1 2" in _text(r.text)
    assert all("AB/1" not in t for t in re.findall(r'hx-target="([^"]+)"', r.text))


@pytest.mark.parametrize("confirm", ["", "1"])
@pytest.mark.parametrize("case", ["already-refused", "doc-on-review", "whole-range", "no-link"])
def test_remove_link_is_refused_on_a_stale_page(client, conn, case, confirm):
    _mfr(conn)
    doc_id = _doc(conn, f"stale-{case}-{confirm}",
                  status="staged" if case == "doc-on-review" else "production",
                  scope="manufacturer" if case == "whole-range" else "group")
    if case != "no-link":
        _linked(conn, doc_id, "A1", status="rejected" if case == "already-refused" else "production",
                basis="mfr-scope" if case == "whole-range" else "ref-list")
    conn.commit()
    r = _ask(client, doc_id, confirm=confirm)
    assert r.status_code == 422 and "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []


def test_remove_link_needs_a_row_number(client, conn):
    doc_id = _published(conn, "norow")
    conn.commit()
    assert _ask(client, doc_id, row="x'y").status_code == 422
    assert _jobs(conn) == []


def test_covered_items_shows_actions(client, conn):
    doc_id = _published(conn, "page")
    _linked(conn, doc_id, "A2", status="rejected")
    _linked(conn, doc_id, "A3", status="staged", basis="fetch-context")
    conn.commit()
    body = client.get(f"/documents/{doc_id}").text
    assert 'value="remove-link"' in body
    assert f'id="link-slot-{doc_id}-1"' in body
    assert 'value="reopen-link"' in body and 'value="confirm-link"' in body


def test_the_covered_items_heading_leaves_out_refused_rows(client, conn):
    doc_id = _published(conn, "heading")
    _linked(conn, doc_id, "A2")
    _linked(conn, doc_id, "A3", status="rejected")
    conn.commit()
    assert "Covered items (2)" in _text(client.get(f"/documents/{doc_id}").text)


def test_the_items_page_does_not_count_a_refused_link_as_replaced(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "replaced", status="superseded")
    _linked(conn, doc_id, "SUP1", status="rejected")
    conn.commit()
    body = client.get("/items", params={"q": "SUP1"}).text
    assert re.search(r"<td>0</td>\s*<td>0</td>\s*<td>0</td>", body)


def test_a_whole_range_document_has_no_remove(client, conn):
    doc_id = _published(conn, "page-whole", scope="manufacturer")
    conn.commit()
    body = client.get(f"/documents/{doc_id}").text
    assert 'value="remove-link"' not in body and "link-slot-" not in body


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


def test_approve_is_refused_until_a_date_is_entered(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "web-nodate", issued=None)
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data={"decision": "approve"})
    assert r.status_code == 422 and "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []
    ok = client.post(f"/staging/{doc_id}/apply", data={
        "decision": "approve", "edit_validity_from": "2026-04-01",
        "edit_baseline": '{"validity_from": ""}'})
    assert ok.status_code == 200 and len(_jobs(conn)) == 1


def test_the_panel_says_the_date_is_needed(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "web-fact", issued=None)
    conn.commit()
    assert "Not stated. Enter it before approving." in _text(client.get(f"/staging/{doc_id}/detail").text)


def _bc_brands(conn, canonical, codes_names):
    """The Chemichl shape: one legal entity owning BC codes whose BC names are
    brand labels (10127 MIYO, 10128 JENSEN)."""
    mid = conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                       "ON CONFLICT (canonical_name) DO UPDATE SET canonical_name=EXCLUDED.canonical_name "
                       "RETURNING id", (canonical,)).fetchone()["id"]
    for code, name in codes_names:
        conn.execute("INSERT INTO vendor_master (code_source, code, name, import_batch) "
                     "VALUES ('LJ',%s,%s,'t')", (code, name))
        conn.execute("INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
                     "VALUES ('LJ',%s,%s,'playbook')", (code, mid))
        conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                     "VALUES (%s,%s,'playbook') ON CONFLICT (raw_name) DO NOTHING", (code, canonical))
        _item(conn, f"{code}-1", f"{name} ITEM", raw=code)


def test_the_item_picker_names_the_bc_brands_of_a_legal_manufacturer(client, conn):
    """Natasa, 2026-11-05: MIYO / JENSEN were "not selectable" because the list
    shows only the legal name, Chemichl AG. The BC names ride along in the
    label (which the select filter searches); the value stays the legal name."""
    _bc_brands(conn, "Chemichl AG", [("10127", "MIYO"), ("10128", "JENSEN")])
    _mfr(conn)                                    # STRAUMANN: no BC name differs
    _item(conn, "010.6042", "NC VARIOBASE")
    doc_id = _doc(conn, "brands", canonical=None)
    conn.commit()
    body = client.get(f"/picker/{doc_id}").text
    assert re.search(r'<option value="Chemichl AG">\s*Chemichl AG · BC: JENSEN, MIYO\s*</option>', body)
    assert re.search(rf'<option value="{M}">\s*{M}\s*</option>', body)


def test_the_whole_range_picker_finds_a_manufacturer_by_its_bc_name(client, conn):
    from tests.fixtures import seed_ui

    _bc_brands(conn, "Chemichl AG", [("10127", "MIYO"), ("10128", "JENSEN")])
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-brands-range", archive_url="/archive/h-brands-range.pdf",
        doc_type="EC", regulation="MDR", coverage_scope="manufacturer")
    seed_ui.seed_manual_task(conn, kind="gate-manual", doc_id=doc_id,
                             payload={"route": "mfr-binding", "manufacturer": "Unknown Name"})
    conn.commit()
    body = client.get(f"/staging/{doc_id}/detail").text
    assert 'data-search="Chemichl AG JENSEN MIYO"' in body
    assert "Chemichl AG · BC: JENSEN, MIYO (0 medical-device items)" in _text(body)


def test_remove_link_is_offered_on_a_superseded_document_that_still_answers(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "sup-rm", status="superseded")
    _linked(conn, doc_id, "A1", status="production")
    conn.commit()
    r = _ask(client, doc_id)
    assert r.status_code == 200
    assert "Yes, remove A1" in _text(r.text)


def test_remove_link_counts_an_older_document_as_left(client, conn):
    """Under per-item currency the older document takes over: the item is not
    left without one."""
    doc_id = _published(conn, "newer-one")
    older = _doc(conn, "older-one", status="production", issued="2020-01-01")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('A1', %s, 'manual', 'production')", (older,))
    conn.commit()
    assert "will have no IFU left" not in _text(_ask(client, doc_id).text)
