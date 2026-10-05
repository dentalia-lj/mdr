"""The picker's reads (spec §4): one manufacturer's items, fuzzy and capped."""
from __future__ import annotations

from datetime import date

import pytest

from web import item_picker as ip

S = "STRAU"


def _item(conn, ref, name, raw=S, md=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,%s,%s,%s,'LJ',1,now())", (ref, name, raw, md))


def _group(conn, label, refs):
    gid = conn.execute("INSERT INTO item_group (canonical_manufacturer, label) "
                       "VALUES (%s,%s) RETURNING group_id", (S, label)).fetchone()["group_id"]
    for r in refs:
        conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                     "VALUES (%s,%s,'name-family')", (gid, r))
    return gid


def _document(conn, key, *, doc_type="IFU", status="staged", issued=None, archive=None):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, validity_from) VALUES (%s,'MDR','group',%s,%s,%s,%s) RETURNING doc_id",
        (doc_type, f"ip-{key}", archive or f"file:///{key}.pdf", status, issued),
    ).fetchone()["doc_id"]


def _link(conn, ref, doc_id, status="production", basis="ref-list"):
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s,%s,%s,%s::link_status)", (ref, doc_id, basis, status))


def _doc(conn, key="this", issued=date(2026, 4, 1), file_name="varibase novo.pdf"):
    doc_id = _document(conn, key, issued=issued)
    return {"doc_id": doc_id, "type": "IFU", "validity_from": issued,
            "content_hash": f"ip-{key}", "file_name": file_name}


def _refs(result):
    return [r.item_ref for g in result.groups for r in g.rows]


def test_search_lists_only_the_manufacturers_items(conn):
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "X-1", "NC VARIOBASE FOR CROWN AS", raw="OTHER")
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert _refs(res) == ["010.6042"]


def test_search_finds_a_misspelling(conn):
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "011.1", "BONE LEVEL IMPLANT")
    assert _refs(ip.search(conn, _doc(conn), S, "varibase")) == ["010.6042"]


def test_an_item_number_prefix_returns_those_items(conn):
    _item(conn, "010.6042", "A")
    _item(conn, "010.6054", "B")
    _item(conn, "022.0000", "C 010.6")
    res = ip.search(conn, _doc(conn), S, "010.60")
    assert res.mode == "number"
    assert _refs(res) == ["010.6042", "010.6054"]


def test_a_number_found_inside_is_listed_after_those_that_start_with_it(conn):
    _item(conn, "6042.1", "A")
    _item(conn, "010.6042", "B")
    assert _refs(ip.search(conn, _doc(conn), S, "6042")) == ["6042.1", "010.6042"]


def test_nearby_numbers_are_the_ten_before_and_after(conn):
    for n in range(1, 31):
        _item(conn, f"100.{n:03d}", f"PART {n}")
    _item(conn, "100.0155", "OTHER MAKER", raw="OTHER")
    res = ip.search(conn, _doc(conn), S, "100.015")
    assert _refs(res) == ["100.015"]
    assert [r.item_ref for r in res.nearby.rows] == (
        [f"100.{n:03d}" for n in range(5, 15)] + [f"100.{n:03d}" for n in range(16, 26)])
    assert res.nearby.label == "Nearby item numbers"


def test_no_nearby_numbers_without_a_number_match(conn):
    _item(conn, "010.6042", "NC VARIOBASE")
    assert ip.search(conn, _doc(conn), S, "variobase").nearby is None


def test_a_number_that_matches_nothing_is_not_searched_as_a_name(conn):
    _item(conn, "010.6042", "KIT 999.9 PARTS")
    res = ip.search(conn, _doc(conn), S, "999.9")
    assert (res.mode, res.total) == ("number", 0)


def test_punctuation_in_names_is_ignored(conn):
    """Measured 2026-10-05: "emax" scored 0.40 against "E.MAX", the same as
    against "EMPRESS"; without the dot it finds every E.MAX item and no other."""
    _item(conn, "596839", "E.MAX CERAM ZIRLINER 5G 1")
    _item(conn, "554034", "EMPRESS KIVETE 100G")
    assert _refs(ip.search(conn, _doc(conn), S, "emax")) == ["596839"]


def test_search_is_capped_and_says_how_many_matched(conn):
    for n in range(ip.CAP + 5):
        _item(conn, f"V{n:04d}", f"VARIOBASE {n}")
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert (res.shown, res.total, res.capped) == (ip.CAP, ip.CAP + 5, True)


@pytest.mark.parametrize("q", ["", "   ", "%", "_", "%%", "-.-"])
def test_text_without_a_word_lists_nothing(conn, q):
    """Review Focus 1: never the whole catalogue."""
    _item(conn, "010.6042", "NC VARIOBASE")
    res = ip.search(conn, _doc(conn), S, q)
    assert (res.shown, res.total) == (0, 0)


def test_a_percent_sign_is_not_a_wildcard(conn):
    _item(conn, "50%-A", "X")
    _item(conn, "50-B", "Y")
    assert _refs(ip.search(conn, _doc(conn), S, "50%")) == ["50%-A"]


def test_a_manufacturer_with_no_codes_lists_nothing(conn):
    """Review Focus 2."""
    _item(conn, "010.6042", "NC VARIOBASE")
    assert ip.search(conn, _doc(conn), "NOBODY", "variobase").total == 0


def test_an_item_in_two_groups_is_listed_once_under_the_lower_one(conn):
    _item(conn, "A1", "VARIOBASE A")
    low = _group(conn, "LOW", ["A1"])
    _group(conn, "HIGH", ["A1"])
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert [(g.group_id, g.label, [r.item_ref for r in g.rows]) for g in res.groups] == [
        (low, "LOW", ["A1"])]


def test_rows_carry_device_flag_documents_and_the_same_type_comparison(conn):
    _item(conn, "A1", "VARIOBASE A", md=True)
    _item(conn, "A2", "VARIOBASE B", md=None)
    doc = _doc(conn)
    old_ifu = _document(conn, "old", status="production", issued=date(2021, 3, 2))
    new_ifu = _document(conn, "new", status="production", issued=date(2025, 7, 14))
    a_doc = _document(conn, "doc", doc_type="DoC", status="production", issued=date(2024, 1, 18))
    _link(conn, "A1", old_ifu)
    _link(conn, "A1", new_ifu)
    _link(conn, "A1", a_doc)
    rows = {r.item_ref: r for g in ip.search(conn, doc, S, "variobase").groups for r in g.rows}
    a1, a2 = rows["A1"], rows["A2"]
    assert a1.md_flag is True and a2.md_flag is None
    assert a1.has == {"DoC": True, "EC": False, "IFU": True, "ISO": False}
    assert a1.same_type == {"doc_id": new_ifu, "validity_from": date(2025, 7, 14), "compare": "older"}
    assert a2.same_type is None


@pytest.mark.parametrize("theirs, ours, word", [
    (date(2021, 1, 1), date(2026, 4, 1), "older"),
    (date(2027, 1, 1), date(2026, 4, 1), "newer"),
    (date(2026, 4, 1), date(2026, 4, 1), "same date"),
    (None, date(2026, 4, 1), "no date"),
    (date(2021, 1, 1), None, "no date"),
])
def test_compare(theirs, ours, word):
    assert ip.compare(theirs, ours) == word


def test_refused_linked_and_proposed_rows(conn):
    for ref in ("R1", "L1", "P1"):
        _item(conn, ref, f"VARIOBASE {ref}")
    doc = _doc(conn)
    _link(conn, "R1", doc["doc_id"], status="rejected", basis="name-family")
    _link(conn, "L1", doc["doc_id"], status="production", basis="ref-list")
    _link(conn, "P1", doc["doc_id"], status="staged", basis="name-family")
    rows = {r.item_ref: r for g in ip.search(conn, doc, S, "variobase").groups for r in g.rows}
    assert (rows["R1"].refused, rows["L1"].linked, rows["P1"].proposed) == (True, True, "name-family")
    assert _refs(ip.proposed(conn, doc, S)) == ["P1"]


def test_similar_excludes_the_example_and_other_manufacturers(conn):
    _item(conn, "A1", "RB/WB VARIOBASE XC FOR CROWN AS 4.5")
    _item(conn, "A2", "RB/WB VARIOBASE XC FOR CROWN 3.8")
    _item(conn, "A3", "BONE LEVEL IMPLANT")
    _item(conn, "X1", "RB/WB VARIOBASE XC FOR CROWN 3.8", raw="OTHER")
    res = ip.similar(conn, _doc(conn), S, "A1")
    assert _refs(res) == ["A2"] and res.example == "A1"


def test_items_of_a_published_document(conn):
    _item(conn, "A1", "X")
    _item(conn, "A2", "Y")
    src = _document(conn, "src", doc_type="DoC", status="production")
    _link(conn, "A1", src)
    assert _refs(ip.items_of(conn, _doc(conn), S, src)) == ["A1"]


def test_selected_returns_the_ticked_rows_in_item_order(conn):
    _item(conn, "B1", "Y")
    _item(conn, "A1", "X")
    assert [r.item_ref for r in ip.selected(conn, _doc(conn), S, ["B1", "A1"])] == ["A1", "B1"]


def test_suggestions_come_from_the_file_name_and_page_one(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('STRAUMANN')")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s, 'STRAUMANN', 'vendor-master')", (S,))
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "011.1", "ABUTMENTS SET")
    doc = _doc(conn, file_name="navodila za uporabo varibase Straumann novo.pdf")
    conn.execute(
        "INSERT INTO document_text (content_hash, source, pages, chars, content) "
        "VALUES (%s, 'pdf-text', 2, 60, %s)",
        (doc["content_hash"], "Straumann Variobase Abutments 701593/M/12 [[page 2]] Implantat"))
    src = _document(conn, "decl", doc_type="DoC", status="production",
                    archive="file:///izjava o skladnosti za varibase XC novo.pdf")
    _link(conn, "010.6042", src)
    got = ip.suggestions(conn, doc, "STRAUMANN", lambda d: d["archive_url"].rsplit("/", 1)[-1])
    # "navodila", "uporabo", "straumann" are stop words or the manufacturer;
    # "implantat" is on page 2; every chip matches at least one item.
    assert got["words"] == ["varibase", "variobase", "abutments"]
    assert got["docs"] == [{"doc_id": src, "file_name": "izjava o skladnosti za varibase XC novo.pdf",
                            "items": 1}]


def test_equal_scores_put_the_closest_whole_name_first(conn):
    """Found on dev doc 51 (PrograMill PM3 DoC), 2026-10-05: every name
    containing "programill" scores 1.0, so item order decided, and the machine
    itself sat below 50 milling blocks."""
    _item(conn, "686490", "EMPRESS CAD PROGRAMILL MULTI LT A2 C14 5KOS")
    _item(conn, "686509", "E.MAX CAD PROGRAMILL MO 2 C14 5KOS")
    _item(conn, "689238", "PROGRAMILL PM3 SYSTEM")
    assert _refs(ip.search(conn, _doc(conn), S, "programill"))[0] == "689238"


def test_model_codes_are_suggested(conn):
    """"PM3" names the machine; a letters-only word list dropped it."""
    _item(conn, "689238", "PROGRAMILL PM3 SYSTEM")
    doc = _doc(conn, file_name="PrograMill PM3.pdf")
    got = ip.suggestions(conn, doc, S, lambda d: None)
    assert got["words"] == ["programill", "pm3"]
