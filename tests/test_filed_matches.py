"""Filed, but maybe ours (Today card, 2026-11-13): `web/filed_matches.py`.

A filed document has no item link: VALIDATE found no Business Central number
of its manufacturer in what it prints. This check looks again, more loosely,
and only suggests. Three kinds, strongest first: the same number once spaces,
dots and dashes are dropped; the same number once leading zeros are dropped;
one number is the other with something in front.
"""
from __future__ import annotations

import json
import uuid

import pytest

from web import filed_matches
from web.filed_matches import EXACT, PREFIX, ZEROS


@pytest.mark.parametrize("printed, item_ref, mfr_ref, kind", [
    # the same number
    ("541440", "541440", None, EXACT),
    ("606.20.110", "60620110", None, EXACT),
    ("25881E", "25881 E", None, EXACT),
    ("lup0499000", "LUP0499000", None, EXACT),
    ("Ti250PLT-2", "X-1", "TI250PLT-2", EXACT),
    # leading zeros, on either side
    ("000529397", "529397", None, ZEROS),
    ("12792", "012792", None, ZEROS),
    ("017", "17", None, ZEROS),
    # a prefix, on either side
    ("0541", "6050541", None, PREFIX),
    ("1101-", "UP-1101", None, PREFIX),
    ("151SS", "02T151SS", None, PREFIX),
    ("60111101", "11101", None, PREFIX),
    ("25002", "X-2", "60625002", PREFIX),
    # not a match
    ("541", "6050541", None, None),          # printed side too short for a prefix
    ("60111101", "1101", None, None),        # BC side too short for a prefix
    ("0000", "0", None, None),               # nothing left once zeros go
    ("", "541440", None, None),
    ("541440", "541441", None, None),
])
def test_classify(printed, item_ref, mfr_ref, kind):
    assert filed_matches.classify(printed, item_ref, mfr_ref) == kind


def _maker(conn, canonical, code):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (canonical,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'vendor-master') ON CONFLICT DO NOTHING", (code, canonical))


def _item(conn, ref, code, *, name=None, mfr_ref=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES (%s,%s,%s,%s,true,'LJ',now())",
        (ref, name or f"widget {ref}", code, mfr_ref))


def _doc(conn, manufacturer, *, status="filed", doc_type="DoC"):
    return conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "  coverage_scope, canonical_manufacturer) "
        "VALUES (%s,'MDR',%s,%s,'/a.pdf','group',%s) RETURNING doc_id",
        (doc_type, status, f"h-fm-{uuid.uuid4().hex[:12]}", manufacturer),
    ).fetchone()["doc_id"]


def _refs(conn, doc_id, refs, *, rev=1):
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "  confidence, extracted_at, extract_rev) "
        "VALUES (%s,'ref_list',%s,'/a.pdf',1,'refs','T1',0.9,now(),%s)",
        (doc_id, json.dumps(refs), rev))


def test_a_filed_document_is_listed_under_its_strongest_kind(conn):
    _maker(conn, "ACMECO", "901")
    _item(conn, "6050541", "901", name="BASE PLATES PINK")
    _item(conn, "541440", "901", name="TRIPLEX")
    doc = _doc(conn, "ACMECO")
    _refs(conn, doc, ["0541", "541440"])

    found = filed_matches.find(conn)
    assert [(r.doc_id, r.kind) for r in found.rows] == [(doc, EXACT)]
    row = found.rows[0]
    assert (row.printed, row.item_ref, row.item_name, row.more) == ("541440", "541440", "TRIPLEX", 0)
    assert found.counts == {EXACT: 1, PREFIX: 0, ZEROS: 0}


def test_only_the_manufacturers_own_items_are_compared(conn):
    _maker(conn, "ACMECO", "901")
    _maker(conn, "OTHERCO", "902")
    _item(conn, "541440", "902")
    _refs(conn, _doc(conn, "ACMECO"), ["541440"])

    assert filed_matches.find(conn).rows == []


def test_a_document_with_an_item_or_another_status_is_left_out(conn):
    _maker(conn, "ACMECO", "901")
    _item(conn, "541440", "901")
    linked = _doc(conn, "ACMECO")
    _refs(conn, linked, ["541440"])
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('541440', %s, 'ref-item', 'staged')", (linked,))
    for status in ("staged", "production", "rejected"):
        _refs(conn, _doc(conn, "ACMECO", status=status), ["541440"])
    _refs(conn, _doc(conn, None), ["541440"])

    assert filed_matches.find(conn).rows == []


def test_only_the_latest_reading_of_the_numbers_counts(conn):
    _maker(conn, "ACMECO", "901")
    _item(conn, "541440", "901")
    doc = _doc(conn, "ACMECO")
    _refs(conn, doc, ["541440"], rev=1)
    _refs(conn, doc, ["999999"], rev=2)

    assert filed_matches.find(conn).rows == []


def test_more_counts_the_other_items_of_the_same_kind(conn):
    _maker(conn, "ACMECO", "901")
    # Two prefix matches and one that is no match at all: "+1 more".
    for ref in ("XX-9331", "UP-9331", "UP-9331-R"):
        _item(conn, ref, "901")
    _refs(conn, _doc(conn, "ACMECO"), ["9331"])

    [row] = filed_matches.find(conn).rows
    assert (row.kind, row.item_ref, row.more) == (PREFIX, "UP-9331", 1)


def test_more_leaves_out_items_of_a_weaker_kind(conn):
    _maker(conn, "ACMECO", "901")
    _item(conn, "742011", "901")
    _item(conn, "UP-742011", "901")
    _refs(conn, _doc(conn, "ACMECO"), ["742011"])

    [row] = filed_matches.find(conn).rows
    assert (row.kind, row.item_ref, row.more) == (EXACT, "742011", 0)


def test_rows_are_ordered_by_kind_then_manufacturer(conn):
    _maker(conn, "BCO", "901")
    _maker(conn, "ACO", "902")
    _item(conn, "6050541", "901")
    _item(conn, "529397", "902")
    _item(conn, "742011", "901")
    _item(conn, "742012", "902")
    prefix = _doc(conn, "BCO")
    _refs(conn, prefix, ["0541"])
    zeros = _doc(conn, "ACO")
    _refs(conn, zeros, ["000529397"])
    exact_b = _doc(conn, "BCO")
    _refs(conn, exact_b, ["742011"])
    exact_a = _doc(conn, "ACO")
    _refs(conn, exact_a, ["742012"])

    found = filed_matches.find(conn)
    assert [r.doc_id for r in found.rows] == [exact_a, exact_b, prefix, zeros]
    assert found.counts == {EXACT: 2, PREFIX: 1, ZEROS: 1}
    assert found.total == 4


def test_a_reading_that_is_not_a_list_is_skipped(conn):
    _maker(conn, "ACMECO", "901")
    _item(conn, "541440", "901")
    doc = _doc(conn, "ACMECO")
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "  confidence, extracted_at) "
        "VALUES (%s,'ref_list','not json','/a.pdf',1,'refs','T1',0.9,now())", (doc,))

    assert filed_matches.find(conn).rows == []


def test_the_index_agrees_with_classify():
    # Every pair from a set of numbers built to hit each kind and each length
    # boundary: the lookup and the plain comparison must give the same answer.
    numbers = ["541440", "0541440", "6050541", "0541", "541", "UP-1101", "1101-",
               "02T151SS", "151SS", "11101", "60111101", "1101", "000529397",
               "529397", "017", "17", "0", "0000", "606.20.110", "60620110",
               "S-tip", "STIP", "A12345", "12345"]
    items = [{"item_ref": n, "mfr_ref": None, "name": None,
              "k_item": filed_matches._key(n), "k_mfr": ""}
             for n in numbers]
    keys = {filed_matches._key(n) for n in numbers}
    for idx in (filed_matches._Index(items), filed_matches._Index(items, keys)):
        for p in numbers:
            got = dict(idx.match(filed_matches._key(p)))
            want = {n: k for n in numbers if (k := filed_matches.classify(p, n))}
            assert got == want, p


@pytest.mark.parametrize("raw", ["606.20.110", "25881 E", "lup-0499/000", "ČŠŽ-12", "", "a_b"])
def test_the_sql_key_is_key(conn, raw):
    row = conn.execute("SELECT upper(regexp_replace(%s, '[^0-9A-Za-z]', '', 'g')) AS k",
                       (raw,)).fetchone()
    assert row["k"] == filed_matches._key(raw)


@pytest.mark.parametrize("printed, item_ref, mfr_ref, want", [
    # (kind, printed head, printed match, BC head, BC match, matched on the maker's number)
    ("606.20.110", "60620110", None, (EXACT, "", "606.20.110", "", "60620110", False)),
    ("10003274", "003274", None, (PREFIX, "10", "003274", "", "003274", False)),
    ("0541", "6050541", None, (PREFIX, "", "0541", "605", "0541", False)),
    ("000529397", "529397", None, (ZEROS, "000", "529397", "", "529397", False)),
    ("12792", "012792", None, (ZEROS, "", "12792", "0", "12792", False)),
    ("1101-", "UP-1101", "1101", (EXACT, "", "1101-", "", "1101", True)),
    ("9331", "UP-9331", None, (PREFIX, "", "9331", "UP-", "9331", False)),
])
def test_explain_shows_which_part_matched(printed, item_ref, mfr_ref, want):
    m = filed_matches.explain(printed, item_ref, mfr_ref, "NAME")
    assert (m.kind, m.printed_head, m.printed_match, m.bc_head, m.bc_match, m.via_maker) == want
    assert m.item_ref == item_ref and m.item_name == "NAME"
