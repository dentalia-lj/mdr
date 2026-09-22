"""Missing documents (office UI redesign spec § 5).

`/missing` is the office's list of items the system searched for and found
nothing: open `discovery-dead-end` manual tasks only, oldest first, 20 to a
page, filterable by manufacturer. Review items (`gate-manual`) and
`dead-job-followup` live on the pages that resolve them and never appear here.

The payload is the one `app/handlers/discover.py::_push_manual` writes,
confirmed on three dev rows on 2026-09-11 (tasks 215, 220, 455): `label`,
`manufacturer`, `sources_tried` (every ladder rung walked, `manual` last) and
`prefilled_search_links` (the playbook's portal pages first, then two Google
searches and one Brave search). The seeds below use that shape.
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import date
from urllib.parse import quote_plus, unquote

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from app.playbooks import DocSource, Playbook
from tests.conftest import TEST_API_URL as API_TEST_URL
from web import missing
from web.app import create_app, missing_summary
from web.missing import items_phrase, places_searched, searched_sentence, split_links

LADDER = ["recency", "known_url", "playbook", "eudamed", "search", "email", "manual"]
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _searches(manufacturer: str, label: str) -> list[str]:
    """The three web searches `_prefilled_search_links` appends, same shape."""
    terms = f"{manufacturer} {label} declaration of conformity"
    return [
        f"https://www.google.com/search?q={quote_plus(terms + ' pdf')}",
        f"https://www.google.com/search?q={quote_plus(terms)}+filetype%3Apdf",
        f"https://search.brave.com/search?q={quote_plus(terms + ' pdf')}",
    ]


def _task(conn, *, kind: str, payload: dict, group_id: int | None = None,
          created_at: str | None = None, status: str = "open") -> int:
    return conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status, created_at) "
        "VALUES (%s::manual_kind, %s, %s, %s::manual_status, "
        "        coalesce(%s::timestamptz, now())) RETURNING id",
        (kind, group_id, Json(payload), status, created_at),
    ).fetchone()["id"]


def _group(conn, manufacturer: str, label: str, refs) -> int:
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s, %s) "
        "RETURNING group_id", (manufacturer, label),
    ).fetchone()["group_id"]
    for ref in refs:
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
            "  updated_at) VALUES (%s, %s, '001', TRUE, 'LJ', now()) "
            "ON CONFLICT (item_ref) DO NOTHING", (ref, f"{label} {ref}"))
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s, %s, 'manual')", (gid, ref))
    return gid


def _dead_end(conn, *, manufacturer: str = "ACME", label: str = "WIDGET 5KOS",
              refs=("A-1",), created_at: str = "2026-09-03 10:00+00",
              sources=LADDER, links=None, status: str = "open") -> tuple[int, int]:
    """A discovery dead end as the DISCOVER ladder leaves it. Returns
    `(task_id, group_id)`."""
    gid = _group(conn, manufacturer, label, refs)
    payload = {
        "label": label,
        "manufacturer": manufacturer,
        "sources_tried": list(sources),
        "prefilled_search_links": list(
            links if links is not None else _searches(manufacturer, label)),
    }
    tid = _task(conn, kind="discovery-dead-end", group_id=gid, payload=payload,
                created_at=created_at, status=status)
    return tid, gid


def _cards(page: str) -> list[int]:
    return [int(x) for x in re.findall(r'data-task-id="(\d+)"', page)]


def _card_text(page: str) -> str:
    """The cards only, as `_text` reads them, so a word in the menu cannot
    satisfy or break an assertion about a card."""
    return _text(" ".join(re.findall(r"<article\b.*?</article>", page, re.S)))


def _text(page: str) -> str:
    """The page as a reader sees it: tags dropped, entities decoded,
    whitespace folded."""
    t = re.sub(r"<[^>]+>", " ", page)
    t = html_mod.unescape(t)
    return re.sub(r"\s+", " ", t)


# --------------------------------------------------------------------------- #
# 1. What is listed, and in which order
# --------------------------------------------------------------------------- #

def test_lists_only_open_dead_ends_oldest_first(client, conn):
    newer, _ = _dead_end(conn, label="NEWER PRODUCT", refs=("N-1",),
                         created_at="2026-09-05 09:00+00")
    older, _ = _dead_end(conn, label="OLDER PRODUCT", refs=("O-1",),
                         created_at="2026-09-02 09:00+00")
    _dead_end(conn, label="FOUND ALREADY", refs=("F-1",), status="resolved")
    _task(conn, kind="gate-manual", payload={"label": "REVIEW THING"})
    _task(conn, kind="dead-job-followup", payload={"label": "FAILED THING"})
    conn.commit()

    resp = client.get("/missing")
    assert resp.status_code == 200
    assert _cards(resp.text) == [older, newer]
    for absent in ("FOUND ALREADY", "REVIEW THING", "FAILED THING"):
        assert absent not in resp.text


def test_card_shows_waiting_since_and_the_product_name(client, conn):
    _dead_end(conn, label="KABINET S 7 PREDALI CR515", created_at="2026-09-03 10:38+00")
    conn.commit()
    text = _text(client.get("/missing").text)
    assert "waiting since 3 Sep 2026" in text
    assert "KABINET S 7 PREDALI CR515" in text


def test_nothing_waiting_says_so(client, conn):
    text = _text(client.get("/missing").text)
    assert "No items are waiting for a document." in text


# --------------------------------------------------------------------------- #
# 2. The pager: 20 to a page
# --------------------------------------------------------------------------- #

def test_twenty_to_a_page(client, conn):
    ids = [
        _dead_end(conn, label=f"PRODUCT {i:02d}", refs=(f"R-{i:02d}",),
                  created_at=f"2026-09-03 10:{i:02d}+00")[0]
        for i in range(25)
    ]
    conn.commit()

    first = client.get("/missing").text
    second = client.get("/missing", params={"page": 1}).text
    assert _cards(first) == ids[:20]
    assert _cards(second) == ids[20:]
    assert "1–20 of 25" in first
    assert "21–25 of 25" in second


def test_the_pager_keeps_the_manufacturer_filter(client, conn):
    for i in range(21):
        _dead_end(conn, manufacturer="DENTAL WORLD", label=f"P{i}", refs=(f"DW-{i}",),
                  created_at=f"2026-09-03 10:{i:02d}+00")
    conn.commit()
    page = client.get("/missing", params={"manufacturer": "DENTAL WORLD"}).text
    assert "/missing?manufacturer=DENTAL+WORLD&amp;page=1" in page


# --------------------------------------------------------------------------- #
# 3. Manufacturer and item numbers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("refs, n, expected", [
    ([], 0, ""),
    (["A-1"], 1, "item A-1"),
    (["A-1", "A-2"], 2, "items A-1, A-2"),
    (["A-1", "A-2", "A-3"], 3, "items A-1, A-2, A-3"),
    (["A-1", "A-2", "A-3"], 5, "5 items: A-1, A-2, A-3, +2 more"),
    (["A-1", "A-2", "A-3"], 1234, "1,234 items: A-1, A-2, A-3, +1,231 more"),
])
def test_items_phrase(refs, n, expected):
    assert items_phrase(refs, n) == expected


def test_card_names_the_manufacturer_and_the_item_numbers(client, conn):
    _dead_end(conn, manufacturer="ONE CO", label="SINGLE", refs=("S-1",))
    _dead_end(conn, manufacturer="THREE CO", label="TRIPLE", refs=("T-3", "T-1", "T-2"))
    _dead_end(conn, manufacturer="FIVE CO", label="FIVEFOLD",
              refs=("V-5", "V-4", "V-3", "V-2", "V-1"))
    conn.commit()
    text = _text(client.get("/missing").text)
    assert "ONE CO · item S-1" in text
    assert "THREE CO · items T-1, T-2, T-3" in text
    assert "FIVE CO · 5 items: V-1, V-2, V-3, +2 more" in text


# --------------------------------------------------------------------------- #
# 4. "Searched … Nothing found." names only places the log shows were searched
#
# `sources_tried` lists every rung the ladder walked, including the ones that
# skipped (search not configured) or looked at nothing (no playbook, no Basic
# UDI-DI). The card reads the rungs of the group's LAST run in
# `discovery_log` instead: every row of one `discover.group` run carries the
# same `at` (one transaction, `now()`), so the run is the rows sharing the
# `at` of its `manual` row. The shapes below are the ones on dev, 2026-09-11.
# --------------------------------------------------------------------------- #

SEARCH_RAN = {"query": "ACME WIDGET declaration of conformity pdf",
              "n_ranked": 0, "n_candidates": 10}
CRAWL_READ = {"index_url": "https://acme.example/downloads", "anchors_seen": 40,
              "links_matched": 0, "links_capped": 0, "off_host": 0,
              "pages_fetched": 1, "tier": "static"}

# The latest run of every one of the 240 open dead ends on dev looks like this.
DEV_RUN = [
    ("known_url", "miss", None),
    ("playbook", "miss", None),
    ("eudamed", "miss", None),
    ("search", "miss", SEARCH_RAN),
    ("email", "skipped", {"reason": "email-producer-disabled"}),
    ("manual", "hit", {"new_task": True}),
]


def _run(conn, gid: int, at: str, rows) -> None:
    """One `discover.group` run's `discovery_log` rows, all at the same `at`."""
    for source, outcome, detail in rows:
        conn.execute(
            "INSERT INTO discovery_log (group_id, source, outcome, detail, at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (gid, source, outcome, Json(detail) if detail is not None else None, at))


def _row(source, outcome, detail=None) -> dict:
    return {"source": source, "outcome": outcome, "detail": detail}


@pytest.mark.parametrize("rows, expected", [
    # The two rungs whose log row proves they looked somewhere.
    ([_row("search", "miss", SEARCH_RAN)], ["the web"]),
    ([_row("playbook", "miss", CRAWL_READ)], ["the manufacturer's known pages"]),
    ([_row("playbook", "miss", CRAWL_READ), _row("search", "miss", SEARCH_RAN)],
     ["the manufacturer's known pages", "the web"]),
    # Search skipped (SearchNotConfigured): no web search happened.
    ([_row("search", "skipped", {"reason": "search not configured"})], []),
    # Playbook: a bare miss is a manufacturer with no playbook, or one with
    # only pages for people; robots refused; the index page never loaded.
    ([_row("playbook", "miss")], []),
    ([_row("playbook", "skipped", {"reason": "robots-disallowed"})], []),
    ([_row("playbook", "miss", {"reason": "fetch-error", "url": "https://acme.example/d",
                                "error": "timed out", "pages_fetched": 0})], []),
    # Not derivable from the log, so never named: recency writes no row when
    # it finds nothing, known_url and eudamed write a bare miss whether or not
    # there was an address or a Basic UDI-DI to look up.
    ([_row("known_url", "miss"), _row("eudamed", "miss")], []),
    # Looked nowhere on a dead end: email (off, or nobody to write to), manual
    # (this list), vendor (folded into search, always skipped).
    ([_row("email", "skipped", {"reason": "email-producer-disabled"}),
      _row("email", "miss"), _row("manual", "hit"), _row("vendor", "skipped")], []),
    # A rung added later passes through as stored, once it has really run.
    ([_row("carrier-pigeon", "miss"), _row("search", "miss", SEARCH_RAN)],
     ["carrier-pigeon", "the web"]),
    ([_row("carrier-pigeon", "skipped")], []),
    # The dev shape: only the web search is true.
    ([_row(s, o, d) for s, o, d in DEV_RUN], ["the web"]),
])
def test_places_searched(rows, expected):
    assert places_searched(rows) == expected


def test_searched_sentence():
    assert (searched_sentence(["the manufacturer's known pages", "the web"], date(2026, 9, 3))
            == "Searched the manufacturer's known pages and the web on 3 Sep 2026. Nothing found.")
    assert (searched_sentence(["a", "b", "c"], date(2026, 9, 3))
            == "Searched a, b and c on 3 Sep 2026. Nothing found.")


def test_no_place_proven_claims_no_search():
    assert searched_sentence([], date(2026, 9, 3)) == "Nothing found on 3 Sep 2026."


def test_the_card_names_only_places_the_log_shows_were_searched(client, conn):
    _, plain = _dead_end(conn, label="DEV SHAPED", refs=("D-1",),
                         created_at="2026-09-03 10:00+00")
    _run(conn, plain, "2026-09-03 10:00+00", DEV_RUN)
    _, crawled = _dead_end(conn, label="CRAWLED", refs=("C-1",),
                           created_at="2026-09-04 10:00+00")
    _run(conn, crawled, "2026-09-04 10:00+00",
         [("playbook", "miss", CRAWL_READ), ("search", "miss", SEARCH_RAN),
          ("manual", "hit", {"new_task": True})])
    conn.commit()

    text = _card_text(client.get("/missing").text)
    assert "Searched the web on 3 Sep 2026. Nothing found." in text
    assert ("Searched the manufacturer's known pages and the web on 4 Sep 2026. "
            "Nothing found.") in text
    for never in ("EUDAMED", "our files", "earlier addresses"):
        assert never not in text


def test_a_card_whose_web_search_was_skipped_does_not_claim_one(client, conn):
    _, gid = _dead_end(conn, created_at="2026-09-03 10:00+00")
    _run(conn, gid, "2026-09-03 10:00+00",
         [("search", "skipped", {"reason": "search not configured"}),
          ("manual", "hit", {"new_task": True})])
    conn.commit()
    text = _card_text(client.get("/missing").text)
    assert "Nothing found on 3 Sep 2026." in text
    assert "Searched" not in text


def test_the_card_reads_only_the_latest_run(client, conn):
    """"Search again" that finds nothing again leaves the same task open (no
    second task is written) and logs a whole new run. The card reads that run
    only: its date, and its places. Here the first run searched the web and
    the second could not, so the card must not keep claiming the web."""
    _, gid = _dead_end(conn, created_at="2026-09-03 10:00+00")
    _run(conn, gid, "2026-09-03 10:00+00", DEV_RUN)
    _run(conn, gid, "2026-09-10 08:00+00",
         [("search", "skipped", {"reason": "search not configured"}),
          ("manual", "hit", {"new_task": False})])
    conn.commit()
    text = _card_text(client.get("/missing").text)
    assert "waiting since 3 Sep 2026" in text
    assert "Nothing found on 10 Sep 2026." in text
    assert "the web" not in text.replace("Search the web", "")


# --------------------------------------------------------------------------- #
# 5 and 6. The manufacturer filter and its chips
# --------------------------------------------------------------------------- #

def test_manufacturer_filter(client, conn):
    _dead_end(conn, manufacturer="ALPHA", label="ALPHA THING", refs=("AL-1",))
    beta, _ = _dead_end(conn, manufacturer="BETA", label="BETA THING", refs=("BE-1",))
    conn.commit()
    page = client.get("/missing", params={"manufacturer": "BETA"}).text
    assert _cards(page) == [beta]
    assert "ALPHA THING" not in page


def _chip_names(fragment: str) -> list[str]:
    return [unquote(m) for m in
            re.findall(r'class="chip[^"]*" href="/missing\?manufacturer=([^"&]+)"', fragment)]


def test_chips_show_the_top_six_then_how_many_more(client, conn):
    # MFR 1 has 8 tasks, MFR 2 has 7, ... MFR 8 has 1.
    for i in range(1, 9):
        for j in range(9 - i):
            _dead_end(conn, manufacturer=f"MFR {i}", label=f"P{i}-{j}", refs=(f"R{i}-{j}",))
    conn.commit()

    page = client.get("/missing").text
    top, _, more = page.partition('class="chip-more"')
    assert _chip_names(top) == [f"MFR {i}" for i in range(1, 7)]
    assert "+ 2 more" in page
    assert _chip_names(more)[:2] == ["MFR 7", "MFR 8"]
    assert "All (36)" in _text(page)
    assert "MFR 1 (8)" in _text(page)


def test_a_manufacturer_behind_more_opens_the_list(client, conn):
    for i in range(1, 9):
        for j in range(9 - i):
            _dead_end(conn, manufacturer=f"MFR {i}", label=f"P{i}-{j}", refs=(f"R{i}-{j}",))
    conn.commit()
    page = client.get("/missing", params={"manufacturer": "MFR 8"}).text
    assert '<details class="chip-more" open>' in page
    assert len(_cards(page)) == 1


# --------------------------------------------------------------------------- #
# 7, 8 and 9. The four actions
# --------------------------------------------------------------------------- #

def test_upload_link_carries_group_and_task(client, conn):
    tid, gid = _dead_end(conn)
    conn.commit()
    page = client.get("/missing").text
    assert f'href="/upload?group_id={gid}&amp;manual_task_id={tid}"' in page
    assert "Upload what I found" in page


def test_manufacturer_downloads_and_web_search_links(client, conn):
    portal = "https://www.acme.example/downloads"
    searches = _searches("ACME", "WITH PORTAL")
    _dead_end(conn, manufacturer="ACME", label="WITH PORTAL", links=[portal] + searches)
    conn.commit()
    page = client.get("/missing").text
    assert f'href="{portal}"' in page
    assert "ACME downloads ↗" in page
    assert f'href="{searches[0]}"' in page
    assert "Search the web ↗" in page


def test_no_links_means_no_link_buttons(client, conn):
    tid, _ = _dead_end(conn, links=[])
    conn.commit()
    resp = client.get("/missing")
    assert resp.status_code == 200
    page = resp.text
    assert _cards(page) == [tid]
    assert "downloads ↗" not in page
    assert "Search the web" not in page


def test_only_a_web_search_gives_only_that_button(client, conn):
    _dead_end(conn, manufacturer="NOBODY KNOWN", links=_searches("NOBODY KNOWN", "X"))
    conn.commit()
    page = client.get("/missing").text
    assert "Search the web ↗" in page
    assert "downloads ↗" not in page


def test_downloads_fall_back_to_the_playbooks_portal(client, conn, monkeypatch):
    """A task written before the manufacturer's download page was authored
    still gets the button: the playbook's own portal sources are the other
    source spec § 5 names. `direct` sources are fetch targets, not pages for
    a person, exactly as `_prefilled_search_links` treats them."""
    pb = Playbook(slug="acme", manufacturer="ACME", doc_sources=(
        DocSource(doc_type="DoC", kind="direct", url="https://acme.example/doc.pdf"),
        DocSource(doc_type="DoC", kind="portal", url="https://acme.example/docs"),
    ))
    monkeypatch.setattr(missing.playbooks, "load_playbooks", lambda *a, **k: (pb,))
    _dead_end(conn, manufacturer="ACME", links=_searches("ACME", "WIDGET 5KOS"))
    conn.commit()
    page = client.get("/missing").text
    assert 'href="https://acme.example/docs"' in page
    assert "ACME downloads ↗" in page
    assert "doc.pdf" not in page


def test_unreadable_playbooks_cost_the_button_not_the_page(client, conn, monkeypatch):
    """With the database as the playbook source a read failure raises, by
    design (app/playbooks.py). The fallback is a convenience, so the page
    renders without it."""
    def boom(*_a, **_k):
        raise RuntimeError("playbook source down")
    monkeypatch.setattr(missing.playbooks, "load_playbooks", boom)
    tid, _ = _dead_end(conn, links=_searches("ACME", "WIDGET 5KOS"))
    conn.commit()
    resp = client.get("/missing")
    assert resp.status_code == 200
    assert _cards(resp.text) == [tid]
    assert "downloads ↗" not in resp.text
    assert "Search the web ↗" in resp.text


def test_split_links():
    portal = "https://www.kavo.com/en/services/kavo-download-center"
    searches = _searches("KAVO", "X")
    assert split_links([portal] + searches) == (portal, searches[0])
    assert split_links(searches) == (None, searches[0])
    assert split_links(["javascript:alert(1)"] + searches) == (None, searches[0])
    assert split_links([]) == (None, None)
    assert split_links(None) == (None, None)


def test_search_again_posts_to_the_existing_resolve_and_answers_in_words(client, conn):
    tid, gid = _dead_end(conn)
    conn.commit()
    page = client.get("/missing").text
    assert f'hx-post="/manual/{tid}/resolve"' in page

    first = client.post(f"/manual/{tid}/resolve")
    assert first.status_code == 200
    # DISCOVER closes the task on ANY rung hit, before a document is checked
    # (`_resolve_dead_end_task` after an emitted fetch, validate or email), so
    # the receipt promises no more than that.
    # The receipt is the whole of what a person reads; the job number is under
    # the collapsed "Technical details" the P7a slice put on every receipt.
    assert _text(first.text).split("Technical details")[0].strip() == (
        "The system will search again for this item. If it finds something to "
        "try, the item leaves this list before the document is checked. If it "
        "finds nothing, the item stays here.")
    assert "Enqueued job" not in first.text

    again = client.post(f"/manual/{tid}/resolve")
    assert again.status_code == 200
    assert "Already in progress." in again.text

    jobs = conn.execute(
        "SELECT type, payload, priority FROM job WHERE dedupe_key = %s",
        (f"discover:manual:{tid}",)).fetchall()
    assert len(jobs) == 1
    assert jobs[0]["payload"] == {"group_id": gid}
    assert jobs[0]["priority"] == "interactive"


def test_search_again_on_a_task_closed_meanwhile_says_so(client, conn):
    tid, _ = _dead_end(conn, status="resolved")
    conn.commit()
    resp = client.post(f"/manual/{tid}/resolve")
    assert resp.status_code == 422
    assert f"Task #{tid} is already closed." in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# 10. The Upload page names the item it is for
# --------------------------------------------------------------------------- #

def test_upload_for_a_task_names_the_item(client, conn):
    tid, gid = _dead_end(conn, manufacturer="GC", label="FUJI PLUS CAPSULES",
                         refs=("003234",))
    conn.commit()
    page = client.get("/upload", params={"group_id": gid, "manual_task_id": tid}).text
    assert "Uploading a document for FUJI PLUS CAPSULES (GC, item 003234)" in _text(page)
    assert "Target group id" not in page
    assert "Priority" not in page
    assert 'name="priority"' not in page
    assert f'<input type="hidden" name="target_group_id" value="{gid}">' in page
    assert f'<input type="hidden" name="manual_task_id" value="{tid}">' in page


def test_upload_posts_the_tasks_own_group_not_the_urls(client, conn):
    """The page names the item from the task, and the group field is no longer
    on screen to correct, so the group it posts must be the task's too. A URL
    whose `group_id` disagrees (hand-edited, or stale) must not send the
    document to another item."""
    tid, gid = _dead_end(conn, manufacturer="GC", label="FUJI PLUS CAPSULES",
                         refs=("003234",))
    _, other = _dead_end(conn, manufacturer="KOMET", label="SVEDER", refs=("K-1",))
    conn.commit()
    page = client.get("/upload", params={"group_id": other, "manual_task_id": tid}).text
    assert "Uploading a document for FUJI PLUS CAPSULES (GC, item 003234)" in _text(page)
    assert f'<input type="hidden" name="target_group_id" value="{gid}">' in page
    assert f'value="{other}"' not in page


def test_upload_for_a_group_without_a_task_keeps_the_urls_group(client, conn):
    _, gid = _dead_end(conn, manufacturer="GC", label="FUJI PLUS CAPSULES",
                       refs=("003234",))
    conn.commit()
    page = client.get("/upload", params={"group_id": gid}).text
    assert "Uploading a document for FUJI PLUS CAPSULES (GC, item 003234)" in _text(page)
    assert f'<input type="hidden" name="target_group_id" value="{gid}">' in page


def test_upload_for_a_task_with_many_items(client, conn):
    tid, gid = _dead_end(conn, manufacturer="KOMET", label="SVEDER",
                         refs=("K-5", "K-4", "K-3", "K-2", "K-1"))
    conn.commit()
    page = client.get("/upload", params={"group_id": gid, "manual_task_id": tid}).text
    assert ("Uploading a document for SVEDER (KOMET, 5 items: K-1, K-2, K-3, +2 more)"
            in _text(page))


def test_upload_with_ids_that_resolve_to_nothing_is_the_plain_form(client, conn):
    for params in ({"manual_task_id": "abc", "group_id": "xyz"},
                   {"manual_task_id": "999999", "group_id": "999999"}):
        resp = client.get("/upload", params=params)
        assert resp.status_code == 200
        assert "Uploading a document for" not in resp.text
        assert 'name="file"' in resp.text


def test_upload_without_a_task_still_works(client, conn):
    resp = client.get("/upload")
    assert resp.status_code == 200
    assert 'name="file"' in resp.text
    assert "Uploading a document for" not in resp.text
    assert '<input type="hidden" name="target_group_id" value="">' in resp.text
    assert '<input type="hidden" name="manual_task_id" value="">' in resp.text

    # What the form now posts: the file and the two hidden inputs, no priority.
    posted = client.post("/upload", files={"file": ("doc.pdf", _PDF, "application/pdf")},
                         data={"target_group_id": "", "manual_task_id": ""})
    assert posted.status_code == 200
    job = conn.execute("SELECT priority, payload FROM job WHERE type='upload.ingest'").fetchone()
    assert job["priority"] == "interactive"
    assert set(job["payload"]) == {"upload_id"}


# --------------------------------------------------------------------------- #
# 11. missing_summary, the Today page's reading of this list
# --------------------------------------------------------------------------- #

def test_missing_summary(conn):
    for j in range(3):
        _dead_end(conn, manufacturer="BETA", label=f"B{j}", refs=(f"B-{j}",),
                  created_at=f"2026-09-0{2 + j} 10:00+00")
    for j in range(2):
        _dead_end(conn, manufacturer="ALPHA", label=f"A{j}", refs=(f"AL-{j}",),
                  created_at="2026-09-06 10:00+00")
    _dead_end(conn, manufacturer="GAMMA", label="G", refs=("G-1",))
    _dead_end(conn, manufacturer="DELTA", label="D", refs=("D-1",))
    _dead_end(conn, manufacturer="BETA", label="RESOLVED", refs=("B-R",),
              created_at="2026-08-01 10:00+00", status="resolved")
    _task(conn, kind="gate-manual", payload={}, created_at="2026-08-01 10:00+00")

    assert missing_summary(conn) == {
        "n": 7,
        "top": [("BETA", 3), ("ALPHA", 2), ("DELTA", 1)],
        "oldest": date(2026, 9, 2),
    }


def test_missing_summary_when_nothing_is_missing(conn):
    assert missing_summary(conn) == {"n": 0, "top": [], "oldest": None}
