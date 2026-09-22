"""P4 Review with the document and its items in view (spec § 6).

The Review list is grouped by manufacturer, oldest first, and filtered by
reason on the server. A row names the document before anything else. The open
panel shows page 1 of the PDF beside the facts, lists the items an approval
makes the document count for, and says so on the button.

Every write here is still a `gate.apply` job; nothing in this file asserts on
the registry except through what the page renders.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web import words
from web.app import create_app

#: Spec § 6, in order. Restated rather than imported so the test fails when the
#: module drifts from the spec instead of agreeing with itself.
SPEC_CHIPS = ("All reasons", "Which items is unclear", "Manufacturer unclear",
              "Covers a whole range", "Expired", "Other")


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #
def _manufacturer(conn, canonical: str, raw: str | None = None) -> None:
    """A catalogue manufacturer: the `manufacturer` row a document's confirmed
    name points at, and the alias its BC code resolves through."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (canonical,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s, %s, 'vendor-master') ON CONFLICT (raw_name) DO NOTHING",
                 (raw or canonical, canonical))


def _item(conn, ref: str, *, raw: str, name: str = "Seed item", md: bool = True) -> str:
    from tests.fixtures import seed_ui

    return seed_ui.seed_item(conn, ref, name=name, manufacturer_raw=raw, md_flag=md)


def _link(conn, ref: str, doc_id: int, basis: str = "ref-list",
          status: str = "staged") -> None:
    from tests.fixtures import seed_ui

    seed_ui.seed_link(conn, ref, doc_id, match_basis=basis, status=status)


def _task(conn, doc_id: int, **payload) -> int:
    from tests.fixtures import seed_ui

    return seed_ui.seed_manual_task(conn, kind="gate-manual", doc_id=doc_id,
                                    group_id=None, payload=payload)


def _doc(conn, key: str, *, doc_type: str = "DoC", regulation: str = "MDR",
         scope: str = "group", validity_to: str | None = "2028-01-01",
         validity_from: str | None = "2024-01-01", source_url: str | None = None,
         days_ago: float = 0, canonical: str | None = None,
         archive_url: str | None = None, content_hash: str | None = None,
         basic_udi_di: str | None = None, printed: str | None = None) -> int:
    from tests.fixtures import seed_ui

    content_hash = content_hash or f"h-rctx-{key}"
    fields = None
    if printed:
        fields = [{"field": "manufacturer", "value": printed, "verbatim": printed,
                   "tier": "T1", "model_id": "claude-haiku-4-5", "confidence": 0.9,
                   "page": 1}]
    doc_id = seed_ui.seed_document(
        conn, content_hash=content_hash,
        archive_url=archive_url or f"/archive/X/doc/{content_hash[:12]}__{key}.pdf",
        doc_type=doc_type, regulation=regulation, coverage_scope=scope,
        validity_to=validity_to, basic_udi_di=basic_udi_di, evidence_fields=fields)
    conn.execute(
        "UPDATE document SET source_url=%s, validity_from=%s::date, "
        "created_at = now() - make_interval(secs => %s), canonical_manufacturer=%s "
        "WHERE doc_id=%s",
        (source_url, validity_from, days_ago * 86400, canonical, doc_id))
    return doc_id


def _ids(text: str) -> list[int]:
    """Document rows in the order the list renders them."""
    return [int(i) for i in re.findall(r'<div class="stage-row[^"]*" id="doc-(\d+)"', text)]


def _text(markup: str) -> str:
    """What a reader sees: tags dropped, entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _row(text: str, doc_id: int) -> str:
    m = re.search(rf'<div class="stage-row[^"]*" id="doc-{doc_id}">.*?</details>',
                  text, re.S)
    assert m, f"row for doc {doc_id} not rendered"
    return m.group(0)


def _summary(text: str, doc_id: int) -> str:
    m = re.search(r"<summary>(.*?)</summary>", _row(text, doc_id), re.S)
    assert m
    return _text(m.group(1))


def _badge(text: str) -> str:
    """What the count beside the list's heading reads."""
    m = re.search(r'<h2>Documents to review\s*<span class="badge[^"]*">(.*?)</span>',
                  text, re.S)
    assert m, "the list should head with a count"
    return _text(m.group(1))


# --------------------------------------------------------------------------- #
# 1. grouped by manufacturer, oldest first, "Manufacturer not known" last
# --------------------------------------------------------------------------- #
def test_the_list_is_grouped_by_manufacturer_oldest_first(client, conn):
    _manufacturer(conn, "ALPHA")
    _manufacturer(conn, "BETA")
    _item(conn, "alpha-1", raw="ALPHA")
    _item(conn, "alpha-2", raw="ALPHA")
    # ALPHA through its linked items, BETA through the document's own
    # confirmed manufacturer (no links at all).
    a_new = _doc(conn, "a-new", days_ago=1)
    a_old = _doc(conn, "a-old", days_ago=3)
    _link(conn, "alpha-1", a_new)
    _link(conn, "alpha-2", a_old)
    b_old = _doc(conn, "b-old", days_ago=5, canonical="BETA")
    # the oldest document of all, but nobody knows whose it is: still last
    unknown = _doc(conn, "u-oldest", days_ago=10)
    conn.commit()

    text = client.get("/staging/docs").text

    assert _ids(text) == [b_old, a_old, a_new, unknown]
    seen = _text(text)
    assert "BETA 1 document" in seen
    assert "ALPHA 2 documents" in seen
    assert "Manufacturer not known 1 document" in seen
    # one header per group, in list order
    assert seen.index("BETA 1 document") < seen.index("ALPHA 2 documents") \
        < seen.index("Manufacturer not known 1 document")


def test_a_group_header_counts_the_whole_group_not_the_page(client, conn):
    """A group cut by the pager still says how many documents it holds, and
    the next page says the group continues rather than starting it again."""
    from web.app import STAGING_PAGE_SIZE

    _manufacturer(conn, "GAMMA")
    _item(conn, "gamma-1", raw="GAMMA")
    n = STAGING_PAGE_SIZE + 5
    for i in range(n):
        d = _doc(conn, f"g-{i:03d}", days_ago=100 - i * 0.01)
        _link(conn, "gamma-1", d)
    conn.commit()

    first = client.get("/staging/docs", params={"page": 0}).text
    second = client.get("/staging/docs", params={"page": 1}).text

    assert f"GAMMA {n} documents" in _text(first)
    assert f"GAMMA {n} documents" in _text(second)
    assert "continued" in _text(second)
    assert "continued" not in _text(first)


@pytest.mark.parametrize("params,left", [
    ({"reason": "manufacturer"}, 1),
    ({"q": "c-mfr"}, 1),
    ({"reason": "items", "q": "c-notask"}, 1),
])
def test_every_count_on_the_screen_says_what_it_counts(client, conn, params, left):
    """Spec rule 7. Three counts share this screen: the heading badge, each
    group header and the pager. The badge counts the whole queue whatever the
    filter, and says so; a filter in force says how much of the queue it left
    and that the counts under it follow the filter (fix round 2, m1)."""
    ids = _seed_chip_board(conn)
    n = len(ids)

    plain = client.get("/staging/docs").text
    filtered = client.get("/staging/docs", params=params).text

    assert _badge(plain) == f"{n} waiting in all"
    # a chip or a search never moves the badge: it counts everything waiting
    assert _badge(filtered) == f"{n} waiting in all"
    seen = _text(filtered)
    assert f"This filter leaves {left} of the {n} documents waiting for review" in seen
    assert "The counts below count only these" in seen
    # nothing to explain when the list is the whole queue
    assert "This filter leaves" not in _text(plain)


def test_a_deep_link_lands_on_the_page_of_the_new_order(client, conn):
    """`?doc=N` must count rows the way the list orders them. Under the old
    `ORDER BY doc_id` the lowest id was always on page 0; grouped and oldest
    first, a document with no manufacturer sorts behind every known group."""
    from web.app import STAGING_PAGE_SIZE

    target = _doc(conn, "deep-unknown", days_ago=50)      # lowest id, no maker
    _manufacturer(conn, "DELTA")
    _item(conn, "delta-1", raw="DELTA")
    for i in range(STAGING_PAGE_SIZE + 3):
        d = _doc(conn, f"deep-{i:03d}", days_ago=1)
        _link(conn, "delta-1", d)
    conn.commit()

    assert target not in _ids(client.get("/staging").text)

    resp = client.get("/staging", params={"doc": target})

    assert target in _ids(resp.text)
    assert re.search(r"<details[^>]*\bopen\b", _row(resp.text, target))


# --------------------------------------------------------------------------- #
# 2. reason chips, filtered on the server
# --------------------------------------------------------------------------- #
def test_the_chips_are_the_six_from_the_spec_in_order(client, conn):
    from web.app import REVIEW_REASON_CHIPS

    assert tuple(label for _, label in REVIEW_REASON_CHIPS) == SPEC_CHIPS
    _doc(conn, "chip-any")
    conn.commit()

    text = client.get("/staging/docs").text
    rendered = re.findall(r'class="reason-chip(?: is-active)?"[^>]*>([^<]+)<', text)
    assert tuple(s.strip() for s in rendered) == SPEC_CHIPS


def test_every_existing_reason_code_maps_to_a_chip():
    """Every flag GATE classifies, every flag the review page explains, and the
    two task codes that make a document a whole-range approval."""
    from app.handlers import gate
    from web.app import FLAG_EXPLANATIONS, REASON_CODE_CHIP, REVIEW_REASON_CHIPS

    codes = (set(gate.BLOCKING_FLAGS) | set(gate.CAPPING_FLAGS)
             | set(gate.INFORMATIONAL_FLAGS) | set(FLAG_EXPLANATIONS)
             | {"mfr-binding", "md-class-unknown"})
    assert codes - set(REASON_CODE_CHIP) == set()
    chip_keys = {key for key, _ in REVIEW_REASON_CHIPS if key}
    assert set(REASON_CODE_CHIP.values()) <= chip_keys


def _seed_chip_board(conn) -> dict:
    _manufacturer(conn, "CHIPCO")
    _item(conn, "chip-1", raw="CHIPCO")
    ids = {}
    ids["items"] = _doc(conn, "c-items")
    _link(conn, "chip-1", ids["items"])
    _task(conn, ids["items"], flags=["no-item-identifier"])
    ids["no_task"] = _doc(conn, "c-notask")          # no task: "which items"
    _link(conn, "chip-1", ids["no_task"])
    ids["mfr"] = _doc(conn, "c-mfr")
    _task(conn, ids["mfr"], flags=["manufacturer-unresolved"])
    ids["range"] = _doc(conn, "c-range", scope="manufacturer", validity_to=None)
    _task(conn, ids["range"], route="mfr-binding", manufacturer=None)
    ids["expired"] = _doc(conn, "c-expired", validity_to="2020-01-01")
    _task(conn, ids["expired"], flags=["cert-unresolved"])
    ids["unknown"] = _doc(conn, "c-unknown")
    _task(conn, ids["unknown"], flags=["brand-new-flag"])
    # Expired AND a whole-range candidate. Expired is the document's own date,
    # not a reason code, so it crosses every reason chip -- this one is under
    # both "Covers a whole range" and "Expired" on purpose (fix round 2, m2).
    ids["range_expired"] = _doc(conn, "c-range-exp", scope="manufacturer",
                                validity_to="2019-06-01")
    _task(conn, ids["range_expired"], route="mfr-binding", manufacturer=None)
    conn.commit()
    return ids


@pytest.mark.parametrize("chip,expected", [
    ("", {"items", "no_task", "mfr", "range", "expired", "unknown", "range_expired"}),
    ("items", {"items", "no_task"}),
    ("manufacturer", {"mfr"}),
    ("range", {"range", "range_expired"}),
    ("expired", {"expired", "range_expired"}),
    # cert-unresolved maps to Other, and a code nobody mapped lands there too
    ("other", {"expired", "unknown"}),
])
def test_a_chip_filters_the_list_on_the_server(client, conn, chip, expected):
    ids = _seed_chip_board(conn)

    text = client.get("/staging/docs", params={"reason": chip}).text

    assert set(_ids(text)) == {ids[k] for k in expected}


def test_the_active_chip_is_marked_and_kept_by_search_and_pager(client, conn):
    from web.app import STAGING_PAGE_SIZE

    for i in range(STAGING_PAGE_SIZE + 2):
        _doc(conn, f"keep-{i:03d}")                   # no task: "which items"
    conn.commit()

    text = client.get("/staging/docs", params={"reason": "items", "q": "keep"}).text

    assert re.search(r'class="reason-chip is-active"[^>]*aria-pressed="true"[^>]*>'
                     r'\s*Which items is unclear', text)
    # the search form sends the chip back, and a chip sends the search back
    assert '<input type="hidden" name="reason" value="items">' in text
    assert re.search(r'hx-get="/staging/docs\?reason=manufacturer&amp;q=keep"', text)
    # Next keeps both
    nxt = re.search(r'hx-get="(/staging/docs\?page=1[^"]*)"', text)
    assert nxt, "a second page should be offered"
    assert "reason=items" in nxt.group(1) and "q=keep" in nxt.group(1)


def test_an_unknown_chip_value_shows_everything(client, conn):
    ids = _seed_chip_board(conn)

    text = client.get("/staging/docs", params={"reason": "no-such-chip"}).text

    assert set(_ids(text)) == set(ids.values())
    assert re.search(r'class="reason-chip is-active"[^>]*>\s*All reasons', text)


#: The mapping, restated: which question each review reason asks of a person.
#: "Which items" = the document's product scope is in doubt; "Manufacturer" =
#: whose it is is in doubt (`ref-catalogue` matched a number with no maker
#: confirmed, which the guide tells the reviewer to settle first); everything
#: else is about the paper itself and lands in Other.
EXPECTED_CHIP = {
    "mfr-binding": "range", "md-class-unknown": "range",
    "no-item-identifier": "items", "no-ref-overlap": "items",
    "multi-group-match": "items", "ref-unscoped": "items",
    "ref-list-possibly-truncated": "items", "device-enumeration": "items",
    "manufacturer-unresolved": "manufacturer", "multi-manufacturer-ref": "manufacturer",
    "ref-catalogue": "manufacturer",
    "not-a-device-document": "other", "cert-unresolved": "other",
    "date-insane": "other", "older-than-current": "other",
    "same-date-revision": "other", "downgrade-uncomparable": "other",
    "expiry-on-certless-doc": "other", "auto-superseded": "other",
    "evidence-page-missing": "other", "iso-without-standard-number": "other",
    "date-label-not-adjacent": "other",
}


def test_the_mapping_is_the_one_restated_here():
    from web.app import REASON_CODE_CHIP

    assert REASON_CODE_CHIP == EXPECTED_CHIP


@pytest.mark.parametrize("code,chip", sorted(
    (c, k) for c, k in EXPECTED_CHIP.items() if c not in ("mfr-binding", "md-class-unknown")))
def test_each_flag_is_found_under_its_own_chip_only(client, conn, code, chip):
    """Table-driven against the mapping itself, so the SQL filter and the
    mapping cannot drift apart for any code."""
    _manufacturer(conn, "FLAGCO")
    _item(conn, "flag-1", raw="FLAGCO")
    doc_id = _doc(conn, f"flag-{code}")
    _link(conn, "flag-1", doc_id)
    _task(conn, doc_id, flags=[code])
    conn.commit()

    for other in ("items", "manufacturer", "other"):
        text = client.get("/staging/docs", params={"reason": other}).text
        assert (doc_id in _ids(text)) == (other == chip), (code, other)


@pytest.mark.parametrize("payload", [
    {"route": "mfr-binding", "manufacturer": None},
    {"route": "mfr-binding", "manufacturer": None, "reason": "md-class-unknown"},
])
def test_a_whole_range_candidate_is_under_covers_a_whole_range(client, conn, payload):
    doc_id = _doc(conn, "range-" + payload.get("reason", "plain"),
                  scope="manufacturer", validity_to=None)
    _task(conn, doc_id, **payload)
    conn.commit()

    assert doc_id in _ids(client.get("/staging/docs", params={"reason": "range"}).text)
    assert doc_id not in _ids(client.get("/staging/docs", params={"reason": "items"}).text)


def test_a_whole_range_shape_without_a_task_is_under_covers_a_whole_range(client, conn):
    """The panel offers the whole-range approval for any manufacturer-scope
    document with nothing linked, whatever its task says (doc 657). The list
    must file that same document under the same chip, with the same sentence,
    or the chip misses documents whose panel is a whole-range approval: two
    such documents, with no open task at all, were waiting on dev on
    2026-09-11."""
    from web.app import MFR_BINDING_REASON

    _manufacturer(conn, "PICKME")
    _item(conn, "pickme-1", raw="PICKME")      # something for the picker to offer
    doc_id = _doc(conn, "range-notask", scope="manufacturer", validity_to=None)
    conn.commit()

    ranged = client.get("/staging/docs", params={"reason": "range"}).text
    assert doc_id in _ids(ranged)
    assert html.escape(MFR_BINDING_REASON, quote=True) in ranged or \
        MFR_BINDING_REASON in _text(ranged)
    assert doc_id not in _ids(client.get("/staging/docs", params={"reason": "items"}).text)
    assert 'value="bind-manufacturer"' in client.get(f"/staging/{doc_id}/detail").text


def test_the_expired_chip_crosses_every_other_chip(client, conn):
    """Expired is not a reason code: it is the document's own `validity_to`.
    It therefore answers a different question from the other five and overlaps
    all of them, whole-range included (fix round 2, m2). The row it returns
    always carries the expired badge, so the list says why it is there."""
    ids = _seed_chip_board(conn)

    expired = client.get("/staging/docs", params={"reason": "expired"}).text
    other = client.get("/staging/docs", params={"reason": "other"}).text
    ranged = client.get("/staging/docs", params={"reason": "range"}).text

    # the same document under a reason chip and under Expired, both times
    assert ids["expired"] in _ids(expired) and ids["expired"] in _ids(other)
    assert ids["range_expired"] in _ids(expired) and ids["range_expired"] in _ids(ranged)
    # each badge carries its own date, read as a person reads it (spec § 9)
    assert "expired 1 Jan 2020" in _summary(expired, ids["expired"])
    assert "expired 1 Jun 2019" in _summary(expired, ids["range_expired"])


# --------------------------------------------------------------------------- #
# 3. the row names the document
# --------------------------------------------------------------------------- #
def test_a_row_reads_manufacturer_type_word_and_regulation(client, conn):
    _manufacturer(conn, "ALPHA")
    _item(conn, "alpha-1", raw="ALPHA")
    doc_id = _doc(conn, "title", doc_type="DoC", regulation="MDR", days_ago=2,
                  source_url="https://example.com/docs/ALPHA_DoC_2024.pdf?download=1")
    _link(conn, "alpha-1", doc_id)
    conn.commit()

    summary = _summary(client.get("/staging/docs").text, doc_id)

    # dates a person reads, not ISO stamps (spec § 9, P7a)
    waited = words.day_text((datetime.now(timezone.utc) - timedelta(days=2)).date())
    assert summary.startswith("ALPHA · Declaration of Conformity (MDR)")
    assert f"ALPHA_DoC_2024.pdf · issued 1 Jan 2024 · waiting since {waited}" in summary


@pytest.mark.parametrize("source_url,archive_url,content_hash,expected", [
    ("/imports/dentalia-sftp/KOMET/DOC/533173 RA 812 Liste.pdf", None, None,
     "533173 RA 812 Liste.pdf"),
    ("https://example.com/a/b/Cert%20EC%20123.pdf#page=2", None, None,
     "Cert EC 123.pdf"),
    # an email attachment has no path of its own: the archive keeps its name
    ("email:c8cbc6bd01b1b08cc7caa246877560533903efe0", "/archive/X/doc/0123456789ab__Attached.pdf",
     "0123456789abcdef", "Attached.pdf"),
    (None, "/archive/X/doc/fedcba987654__From archive.pdf", "fedcba9876543210",
     "From archive.pdf"),
])
def test_the_subline_holds_the_file_name(client, conn, source_url, archive_url,
                                         content_hash, expected):
    doc_id = _doc(conn, "fname", source_url=source_url, archive_url=archive_url,
                  content_hash=content_hash)
    conn.commit()

    assert expected in _summary(client.get("/staging/docs").text, doc_id)


def test_a_row_with_no_known_manufacturer_names_what_the_document_printed(client, conn):
    doc_id = _doc(conn, "printed", doc_type="EC", regulation="MDD",
                  printed="Inter-Med, Inc.")
    bare = _doc(conn, "bare", doc_type="ISO", regulation="n.a.")
    conn.commit()

    text = client.get("/staging/docs").text

    assert _summary(text, doc_id).startswith("Inter-Med, Inc. · EC certificate (MDD)")
    assert "as printed" in _summary(text, doc_id)
    # nothing to name it by: the type leads, and n.a. adds no brackets
    assert _summary(text, bare).startswith("ISO certificate ·") or \
        _summary(text, bare).startswith("ISO certificate ")
    assert "(n.a.)" not in _summary(text, bare)


def test_a_row_whose_items_belong_to_another_maker_says_so_in_words(client, conn):
    """The "+N" marker carried its meaning in a `title=` tooltip, which a
    keyboard and a touch screen never show (fix round 2, m4). It reads in
    words now, on the row itself."""
    _manufacturer(conn, "OWNCO")
    _manufacturer(conn, "OTHERCO")
    doc_id = _doc(conn, "spans", canonical="OWNCO")
    _item(conn, "span-1", raw="OTHERCO")
    _link(conn, "span-1", doc_id)
    conn.commit()

    row = _row(client.get("/staging/docs").text, doc_id)

    assert "+1 other manufacturer among its items" in _summary(
        client.get("/staging/docs").text, doc_id)
    marker = re.search(r'<span class="hint-inline"[^>]*>\s*\+1[^<]*</span>', row)
    assert marker, "the marker should still be there"
    assert "title=" not in marker.group(0), "its meaning must not live in a tooltip"


def test_an_expired_document_says_so_on_its_row(client, conn):
    doc_id = _doc(conn, "lapsed", validity_to="2020-01-01")
    conn.commit()

    assert "expired 1 Jan 2020" in _summary(client.get("/staging/docs").text, doc_id)


# --------------------------------------------------------------------------- #
# 4. the open panel: the PDF beside the facts, and the items it will count for
# --------------------------------------------------------------------------- #
def _seed_linked(conn, n: int, *, basis: str = "ref-list", key: str = "lk") -> int:
    _manufacturer(conn, "LINKCO")
    doc_id = _doc(conn, key)
    for i in range(n):
        ref = f"{key}-{i:02d}"
        _item(conn, ref, raw="LINKCO", name=f"Product {key} {i:02d}")
        _link(conn, ref, doc_id, basis=basis)
    conn.commit()
    return doc_id


def test_the_panel_shows_page_one_of_the_pdf_and_a_full_size_link(client, conn):
    doc_id = _seed_linked(conn, 1)

    text = client.get(f"/staging/{doc_id}/detail").text

    assert re.search(rf'<iframe[^>]*\bsrc="/documents/{doc_id}/file[^"]*"', text)
    m = re.search(rf'<a[^>]*href="/documents/{doc_id}/file"[^>]*>([^<]*)</a>', text)
    assert m and "Open full size" in m.group(1)
    assert 'target="_blank"' in m.group(0)


def test_the_panel_lists_each_item_by_number_and_name(client, conn):
    doc_id = _seed_linked(conn, 3)

    text = _text(client.get(f"/staging/{doc_id}/detail").text)

    assert "Approving makes it count for these 3 items" in text
    for i in range(3):
        assert f"lk-{i:02d} · Product lk {i:02d}" in text
    assert "Matched through the manufacturer's article numbers on the document" in text


def test_twelve_items_show_ten_then_two_more(client, conn):
    doc_id = _seed_linked(conn, 12, key="tw")

    text = client.get(f"/staging/{doc_id}/detail").text

    block = re.search(r'<ul class="approve-items">(.*?)</ul>\s*'
                      r'<details class="more-items">\s*<summary>([^<]*)</summary>(.*?)</details>',
                      text, re.S)
    assert block, "the first ten, then a fold holding the rest"
    assert len(re.findall(r"<li", block.group(1))) == 10
    assert block.group(2).strip() == "+ 2 more"
    assert len(re.findall(r"<li", block.group(3))) == 2
    for i in range(12):
        assert f"Product tw {i:02d}" in text


def test_the_panel_states_the_facts_in_plain_words(client, conn):
    doc_id = _doc(conn, "facts", doc_type="EC", regulation="MDR", validity_to=None,
                  validity_from="2023-05-04", basic_udi_di="4049381ABC",
                  printed="GC Europe N.V.")
    conn.commit()

    text = _text(client.get(f"/staging/{doc_id}/detail").text)

    assert "Manufacturer GC Europe N.V." in text
    assert "as printed on the document" in text
    assert "Rules MDR, the current EU rules" in text
    assert "Issued 4 May 2023" in text
    assert "Valid until Not stated" in text
    assert "Basic UDI-DI 4049381ABC" in text


def test_technical_details_stay_collapsed(client, conn):
    doc_id = _seed_linked(conn, 1)

    text = client.get(f"/staging/{doc_id}/detail").text

    tech = re.search(r'<details class="technical"[^>]*>', text)
    assert tech and "open" not in tech.group(0)
    assert "fetch-context" not in _text(text.split('<details class="technical"')[0])


# --------------------------------------------------------------------------- #
# 5. the button says how many items
# --------------------------------------------------------------------------- #
def _approve_label(text: str) -> str:
    m = re.search(r'<button[^>]*name="decision" value="approve"[^>]*>(.*?)</button>',
                  text, re.S)
    assert m, "the panel should offer an approval"
    return _text(m.group(1))


def test_the_approve_button_names_three_items(client, conn):
    doc_id = _seed_linked(conn, 3)

    assert _approve_label(client.get(f"/staging/{doc_id}/detail").text) \
        == "Approve for these 3 items"


def test_the_approve_button_names_one_item(client, conn):
    doc_id = _seed_linked(conn, 1)

    text = client.get(f"/staging/{doc_id}/detail").text

    assert _approve_label(text) == "Approve for this item"
    assert "Approving makes it count for this item" in _text(text)


def test_links_an_approval_does_not_publish_are_not_promised(client, conn):
    """`fetch-context`, `name-family` and `ref-catalogue` links stay staged when
    the document is approved (invariant 3; `gate._promote_pending_links`), so
    the document does not count for those items afterwards. 53 of 234 staged
    documents on dev carried only such links on 2026-09-11."""
    doc_id = _seed_linked(conn, 2, basis="fetch-context", key="fc")

    text = client.get(f"/staging/{doc_id}/detail").text
    seen = _text(text)

    assert _approve_label(text) == "Approve"
    assert "count for these" not in seen
    assert "will not count for any item yet" in seen
    assert "fc-00 · Product fc 00" in seen and "fc-01 · Product fc 01" in seen
    assert "where the file was found" in seen


def test_a_mixed_document_promises_only_what_approval_publishes(client, conn):
    doc_id = _seed_linked(conn, 2, key="mx")
    _item(conn, "mx-weak", raw="LINKCO", name="Weak match")
    _link(conn, "mx-weak", doc_id, basis="name-family")
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text

    assert _approve_label(text) == "Approve for these 2 items"
    assert "It is also linked to 1 more item" in _text(text)


def test_a_document_linked_to_nothing_promises_nothing(client, conn):
    doc_id = _doc(conn, "nolinks")
    _task(conn, doc_id, flags=["no-item-identifier"])
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text

    assert _approve_label(text) == "Approve"
    assert "makes it count for no items" in _text(text)


def test_the_publishing_bases_mirror_gate():
    from app.handlers.gate import TRUSTED_BASES
    from web.app import PUBLISHING_BASES

    assert set(PUBLISHING_BASES) == set(TRUSTED_BASES)


# --------------------------------------------------------------------------- #
# 6. a whole-range document
# --------------------------------------------------------------------------- #
def _seed_whole_range(conn) -> int:
    _manufacturer(conn, "RWIDE")
    for ref in ("rwide-1", "rwide-2", "rwide-3"):
        _item(conn, ref, raw="RWIDE")
    _item(conn, "rwide-4", raw="RWIDE", md=False)
    doc_id = _doc(conn, "rwide", doc_type="EC", scope="manufacturer", validity_to=None)
    _task(conn, doc_id, route="mfr-binding", manufacturer="RWIDE")
    conn.commit()
    return doc_id


def test_a_whole_range_document_counts_for_all_the_manufacturers_items(client, conn):
    doc_id = _seed_whole_range(conn)

    text = client.get(f"/staging/{doc_id}/detail").text

    assert "Approving makes it count for all 3 RWIDE items" in _text(text)
    # the P1a confirm still guards it
    assert 'value="bind-manufacturer"' in text
    assert 'hx-target="next .bind-confirm"' in text


def test_a_whole_range_document_with_one_item_says_the_one_item(client, conn):
    """"the 1 RWIDE item" is not a sentence a person writes (fix round 2, m3)."""
    _manufacturer(conn, "ONECO")
    _item(conn, "one-1", raw="ONECO")
    doc_id = _doc(conn, "one", doc_type="EC", scope="manufacturer", validity_to=None)
    _task(conn, doc_id, route="mfr-binding", manufacturer="ONECO")
    conn.commit()

    seen = _text(client.get(f"/staging/{doc_id}/detail").text)

    assert "Approving makes it count for the one ONECO item" in seen
    assert "the 1 ONECO item" not in seen


def test_a_whole_range_document_whose_maker_has_no_device_items_offers_the_picker(
        client, conn):
    """The reachable zero: every item under the supplier's codes has a blank
    device class. `_mfr_bind_target` answers None on that count, so the panel
    shows the picker rather than a one-click approval, and never a count."""
    _manufacturer(conn, "ZEROCO")
    _item(conn, "zero-1", raw="ZEROCO", md=False)
    doc_id = _doc(conn, "zero", doc_type="EC", scope="manufacturer", validity_to=None)
    _task(conn, doc_id, route="mfr-binding", manufacturer="ZEROCO")
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text

    assert "all 0" not in _text(text)
    assert 'value="bind-manufacturer"' in text


def test_a_whole_range_approval_reaching_no_item_has_its_own_sentence(
        client, conn, monkeypatch):
    """The other zero, forced. `_mfr_bind_target` cannot hand the panel a count
    of 0 today (it answers None and the picker takes over), so this drives the
    branch directly: whatever puts a 0 there, the panel must not read "for all
    0 NILCO items" over a hint about every NILCO device item (fix round 2, m3)."""
    from web import app as web_app

    _manufacturer(conn, "NILCO")
    _item(conn, "nil-1", raw="NILCO", md=False)
    doc_id = _doc(conn, "nil", doc_type="EC", scope="manufacturer", validity_to=None)
    _task(conn, doc_id, route="mfr-binding", manufacturer="NILCO")
    conn.commit()
    monkeypatch.setattr(web_app, "_mfr_bind_target",
                        lambda conn, name: {"label": "NILCO", "items": 0})

    seen = _text(client.get(f"/staging/{doc_id}/detail").text)

    assert "Approving records the supplier, but it counts for no items yet." in seen
    assert "Business Central marks no NILCO item as a medical device" in seen
    assert "all 0" not in seen
    assert "Every NILCO item Business Central marks as a medical device" not in seen


def test_a_whole_range_document_offers_no_corrections(client, conn):
    """Controller ruling (2026-09-11): `gate.apply`'s bind path drops edits, so
    a correction offered next to a whole-range approval would be silently lost."""
    doc_id = _seed_whole_range(conn)

    text = client.get(f"/staging/{doc_id}/detail").text

    assert "Correct a fact first" not in text
    assert 'name="edit_' not in text


def test_an_ordinary_document_offers_correct_a_fact_first_collapsed(client, conn):
    doc_id = _seed_linked(conn, 1)

    text = client.get(f"/staging/{doc_id}/detail").text

    m = re.search(r'<details class="corrections"[^>]*>\s*<summary>([^<]*)</summary>', text)
    assert m and m.group(1).strip() == "Correct a fact first"
    assert "open" not in re.search(r'<details class="corrections"[^>]*>', text).group(0)
    assert 'name="edit_validity_to"' in text


# --------------------------------------------------------------------------- #
# 7. the PDF is cached by the browser, never an error
# --------------------------------------------------------------------------- #
def test_the_file_answers_with_a_long_private_cache(test_db_url, tmp_path, conn):
    client = TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                       imports_dir=str(tmp_path))))
    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"%PDF-1.4 cached")
    doc_id = _doc(conn, "cached", archive_url=str(pdf))
    gone = _doc(conn, "gone", archive_url=str(tmp_path / "nowhere.pdf"))
    conn.commit()

    ok = client.get(f"/documents/{doc_id}/file")
    missing_file = client.get(f"/documents/{gone}/file")
    missing_doc = client.get("/documents/999999/file")

    assert ok.status_code == 200
    cache = ok.headers.get("cache-control", "")
    assert "immutable" in cache and "private" in cache
    assert "max-age=31536000" in cache
    for resp in (missing_file, missing_doc):
        assert resp.status_code == 404
        assert "immutable" not in resp.headers.get("cache-control", "")


# --------------------------------------------------------------------------- #
# 8. the list itself loads no PDF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/staging", "/staging/docs"])
def test_the_list_holds_no_iframe(client, conn, path):
    _seed_linked(conn, 2)
    _seed_whole_range(conn)

    assert "<iframe" not in client.get(path).text


def test_dates_compare_against_today(client, conn):
    """Guard for the chip and the badge sharing one idea of today."""
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    doc_id = _doc(conn, "fresh", validity_to=tomorrow)
    conn.commit()

    assert doc_id not in _ids(client.get("/staging/docs", params={"reason": "expired"}).text)


# --------------------------------------------------------------------------- #
# 3b. the row and the document page name one document ONE way
# --------------------------------------------------------------------------- #
# The seam no single slice could see. `_decorate_review_title` built the row
# from `words.doc_type_subject` -- the SENTENCE-SUBJECT form T8 wrote for the
# whole-range confirm ("this instructions-for-use document says …") -- while
# every other screen, `/documents/{id}` included, names a document through
# `words.doc_display_name`, which uses the LABEL. So Review and the page it
# opens disagreed one click apart. The label is the row's; the subject stays
# where a sentence needs one.
def test_the_row_names_the_document_the_way_every_other_screen_does(client, conn):
    _manufacturer(conn, "ALPHA")
    _item(conn, "alpha-ifu", raw="ALPHA")
    doc_id = _doc(conn, "ifu-row", doc_type="IFU", regulation="MDR",
                  printed="ALPHA")
    _link(conn, "alpha-ifu", doc_id)
    conn.commit()

    name = words.doc_display_name(
        {"manufacturer": "ALPHA", "type": "IFU", "regulation": "MDR"})
    assert name == "ALPHA · Instructions for use (MDR)"

    assert _summary(client.get("/staging/docs").text, doc_id).startswith(name)
    assert name in _text(client.get(f"/documents/{doc_id}").text)


def test_the_sentence_subject_stays_in_the_sentence_that_needs_one(client, conn):
    """Moved, not deleted. The whole-range confirm is the one place a document
    type has to read inside a sentence, and it still does."""
    from web.app import _bind_confirm_lines

    lines = _bind_confirm_lines("ALPHA", 3, "IFU")
    assert "This instructions-for-use document says it covers everything" in lines[1]


def test_a_row_with_no_manufacturer_names_the_type_as_a_label(client, conn):
    """The third spelling `_decorate_review_title` produced: with no name to
    lead, it capitalised the sentence subject ("Instructions-for-use
    document"). A label is a label whether or not a name precedes it."""
    bare = _doc(conn, "bare-ifu", doc_type="IFU", regulation="MDD")
    conn.commit()

    assert _summary(client.get("/staging/docs").text, bare).startswith(
        "Instructions for use (MDD)")
