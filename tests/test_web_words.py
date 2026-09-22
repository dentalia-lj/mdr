"""P7a foundations: one vocabulary, display names, receipts, error pages, buttons.

Spec § 9. Five things that every later P7 slice builds on:

* `web/words.py` is the ONE place a stored value becomes a screen word. The
  table in § 9 is the contract; unknown values pass through unchanged, so a new
  enum label shows as itself rather than disappearing.
* `doc_display_name` names a document the way rule 1 asks (name first, number
  second), falling back to the file name and then to "Document #id".
* Receipts are sentences. The work is queued, not done, so no receipt claims it
  is done and none of them says "Enqueued job #". The job number stays
  available, under a collapsed "Technical details".
* An error outside `/api/` is an HTML page with the menu on it. `/api/*` keeps
  JSON, and an HTMX request keeps the `_result.html` error partial (which is
  also what a boosted form reads its banner out of).
* The primary button is `#2F7A58` — 5.19:1 on white, where the mint it replaces
  was 1.9:1.
"""

from __future__ import annotations

import logging
import pathlib
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web import words
from web.app import create_app

STYLE = (pathlib.Path(__file__).resolve().parents[1]
         / "web" / "static" / "css" / "style.css")

#: Spec § 9's word list, restated here rather than imported from `web.words`,
#: so a drifting mapping fails this test instead of agreeing with itself.
#: One entry per row of the table; rows whose "word" is not a word — the three
#: manual-task kinds "(not shown)", the two removed Upload fields, and the
#: Status row, which has two answers depending on who is reading — are checked
#: separately below.
SPEC_WORDS = [
    ("production", "Published"),
    ("staged", "Waiting for review"),
    ("filed", "On file"),
    ("superseded", "Replaced"),
    ("rejected", "Rejected"),
    ("dead", "Failed"),
    ("dead jobs", "Failed"),
    ("processing", "Being read"),
    ("in flight", "Being read"),
    ("manual", "Missing documents"),
    ("drafts out", "Renewal emails"),
    ("renewal drafts", "Renewal emails"),
    ("chase", "Renewal emails"),
    ("emails in", "Emails received"),
    ("sweep", "EUDAMED check"),
    ("release", "Start the check"),
    ("sweep due", "Check due"),
    ("srn", "EUDAMED ID (SRN)"),
    ("recipe", "Playbook"),
    ("playbook", "Playbook"),
    ("slug", "Short name"),
    ("canonical entity", "Manufacturer"),
    ("alias source", "Name in Business Central"),
    ("vendor-master", "Name in Business Central"),
    ("item", "Item"),
    ("article", "Item"),
    ("product", "Item"),
    ("mfr_ref", "Manufacturer's article no."),
    ("match basis", "How it was matched"),
]


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _demo(conn) -> dict:
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    return ids


# --------------------------------------------------------------------------- #
# 1. the word list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stored,shown", SPEC_WORDS)
def test_every_row_of_the_word_list_has_its_word(stored, shown):
    assert words.word(stored) == shown


def test_the_word_list_is_case_insensitive_over_stored_values():
    """Statuses arrive lower-cased from Postgres and hand-typed in templates."""
    assert words.word("Production") == "Published"
    assert words.word("DEAD") == "Failed"


def test_an_unknown_value_passes_through_unchanged():
    """A new enum label must show as itself. A word list that swallowed it
    would hide the very thing that needs mapping."""
    assert words.word("brand-new-status") == "brand-new-status"
    assert words.word("") == ""


def test_none_becomes_an_empty_string():
    assert words.word(None) == ""


@pytest.mark.parametrize("kind", [
    "gate-manual", "discovery-dead-end", "dead-job-followup",
    "target group id", "priority",
])
def test_the_rows_the_word_list_removes_from_the_screen_are_not_words(kind):
    """§ 9 marks these "(not shown)" and "(removed)": each lives on the page
    that resolves it, or left the form altogether. Mapping them to a word would
    put them back on screen."""
    assert kind not in words.WORDS
    assert words.word(kind) == kind


def test_the_status_board_keeps_its_operator_name():
    """The § 9 row with two answers: the office lands on Today, which is its own
    screen, and the board it replaced is "System status" for operators."""
    assert words.word("status") == "System status"
    assert words.word("kpi board") == "System status"


# --------------------------------------------------------------------------- #
# 2. document display names
# --------------------------------------------------------------------------- #
def test_doc_display_name_is_manufacturer_then_type_then_regulation():
    row = {"doc_id": 7, "manufacturer": "IVOCLAR", "type": "EC",
           "regulation": "MDR", "archive_url": "local://seed/ec-1.pdf"}

    assert words.doc_display_name(row) == "IVOCLAR · EC certificate (MDR)"


def test_doc_display_name_falls_back_to_the_file_name():
    row = {"doc_id": 7, "manufacturer": None, "type": "EC", "regulation": "MDR",
           "archive_url": "local://seed/2025-ivoclar-ec.pdf"}

    assert words.doc_display_name(row) == "2025-ivoclar-ec.pdf"


def test_doc_display_name_falls_back_to_the_document_number():
    assert words.doc_display_name({"doc_id": 7}) == "Document #7"


def test_doc_display_name_with_nothing_at_all_still_names_something():
    assert words.doc_display_name({}) == "Document"


def test_the_document_type_words_read_as_sentence_subjects():
    """The whole-range confirm says "This ⟨word⟩ says it covers…". Lower-casing
    the label gave "This instructions for use says", which does not parse, and
    "MDR art. 22", which is not how the article is written."""
    assert words.doc_type_subject("IFU") == "instructions-for-use document"
    assert words.doc_type_subject("SPP") == \
        "systems/procedure pack statement (MDR Art. 22)"
    assert words.doc_type_subject("DoC") == "declaration of conformity"
    assert words.doc_type_subject("EC") == "EC certificate"
    assert words.doc_type_subject(None) == "document"


# --------------------------------------------------------------------------- #
# 3. receipts in words
# --------------------------------------------------------------------------- #
def test_approve_answers_with_a_sentence_and_no_job_number(client, conn):
    ids = _demo(conn)

    resp = client.post(f"/staging/{ids['staged_doc']}/apply",
                       data={"decision": "approve"})

    assert resp.status_code == 200
    assert "Approval recorded. The registry updates within a minute." in resp.text
    assert "Enqueued job #" not in resp.text


def test_reject_answers_with_a_sentence_and_no_job_number(client, conn):
    ids = _demo(conn)

    resp = client.post(f"/staging/{ids['staged_doc']}/apply",
                       data={"decision": "reject", "reason": "Out of date"})

    assert resp.status_code == 200
    assert "Rejection recorded." in resp.text
    assert "Enqueued job #" not in resp.text


def test_re_running_a_failed_task_answers_with_a_sentence(client, conn):
    ids = _demo(conn)

    resp = client.post(f"/dead/{ids['dead_job']}/rerun")

    assert resp.status_code == 200
    assert "Queued to run again." in resp.text
    assert "Enqueued job #" not in resp.text


def test_search_again_answers_with_a_sentence(client, conn):
    ids = _demo(conn)

    resp = client.post(f"/manual/{ids['dead_end_task']}/resolve")

    assert resp.status_code == 200
    assert "The system will search again for this item." in resp.text
    assert "Enqueued job #" not in resp.text


def test_a_deduped_enqueue_reads_already_in_progress(client, conn):
    ids = _demo(conn)
    first = client.post(f"/staging/{ids['staged_doc']}/apply",
                        data={"decision": "approve"})
    assert "Already in progress." not in first.text

    second = client.post(f"/staging/{ids['staged_doc']}/apply",
                         data={"decision": "approve"})

    assert "Already in progress." in second.text
    assert "Deduped" not in second.text


def test_the_job_number_moves_under_technical_details(client, conn):
    """Rule 5 moves the number off the receipt; it does not delete it. /manual's
    Resolve lost it entirely in the Missing-documents slice."""
    ids = _demo(conn)

    resp = client.post(f"/manual/{ids['dead_end_task']}/resolve")

    assert "Technical details" in resp.text
    job_id = conn.execute(
        "SELECT id FROM job WHERE dedupe_key=%s",
        (f"discover:manual:{ids['dead_end_task']}",),
    ).fetchone()["id"]
    details = re.search(r"<details class=\"technical\">.*?</details>",
                        resp.text, re.S)
    assert details, "the receipt should carry a collapsed Technical details block"
    assert f"#{job_id}" in details.group(0)
    assert f"discover:manual:{ids['dead_end_task']}" in details.group(0)


# --------------------------------------------------------------------------- #
# 4. error pages
# --------------------------------------------------------------------------- #
def test_an_unknown_page_is_an_html_error_page_with_the_menu(client):
    resp = client.get("/no-such-page", headers={"Accept": "text/html"})

    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    # the menu, which is what makes it a page and not a dead end
    assert 'class="sidebar"' in resp.text
    assert 'hx-get="/_pulse"' in resp.text
    # and the way back
    assert "Go to Today" in resp.text


def test_an_unknown_api_path_stays_json(client):
    resp = client.get("/api/no-such", headers={"Accept": "text/html"})

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]


def test_an_htmx_request_gets_the_error_partial(client):
    resp = client.get("/no-such-page", headers={"HX-Request": "true"})

    assert resp.status_code == 404
    assert 'class="result error"' in resp.text
    # a partial, never the whole page: htmx would swap the sidebar into the
    # target it was aimed at
    assert 'class="sidebar"' not in resp.text


def test_a_boosted_request_gets_the_partial_its_banner_reads(client):
    """base.html cancels the swap for a boosted non-2xx and lifts the message
    out of `.result.error`. A whole page in that response would leave the
    banner reading the <title> and the menu."""
    resp = client.get("/no-such-page",
                      headers={"HX-Request": "true", "HX-Boosted": "true",
                               "Accept": "text/html"})

    assert resp.status_code == 404
    assert 'class="result error"' in resp.text
    assert "<html" not in resp.text


def test_a_json_client_outside_the_api_still_gets_json(client):
    resp = client.get("/items/no-such-item",
                      headers={"Accept": "application/json"})

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("accept", [
    # axios's default, and the shape httpx and jQuery send. `*/*` is the tail
    # of every one of them, so testing for it before `text/html` handed a page
    # to a client that had just named `application/json`.
    "application/json, text/plain, */*",
    "application/json",
    "application/json;q=0.9, */*;q=0.1",
])
def test_a_client_that_names_json_gets_json_even_with_a_wildcard(client, accept):
    resp = client.get("/items/no-such-item", headers={"Accept": accept})

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("accept", [
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",  # a browser
    "*/*",                                                             # no opinion
    "",                                                                # no header
])
def test_a_browser_or_an_unopinionated_client_gets_the_page(client, accept):
    resp = client.get("/no-such-page", headers={"Accept": accept} if accept else {})

    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]


def test_a_missing_static_file_is_not_a_whole_page(client):
    """`StaticFiles` raises `HTTPException(404)`, which reaches this handler.
    A missing stylesheet is a fetch the browser made, not a navigation: the
    menu, the fonts and the logo are not an answer to it."""
    resp = client.get("/static/css/gone.css", headers={"Accept": "text/html"})

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert 'class="sidebar"' not in resp.text


def test_a_405_keeps_its_allow_header(client):
    """The exception's own headers travel onto whichever body is chosen. A 405
    without `Allow` is a 405 no client can act on."""
    resp = client.post("/status", headers={"Accept": "text/html"})

    assert resp.status_code == 405
    assert resp.headers.get("allow")


def test_a_500_gets_the_same_friendly_page(test_db_url, tmp_path):
    """Controller ruling 2026-09-14: the page matters MOST when something
    genuinely breaks. Starlette's own answer is `Internal Server Error` as
    bare text, which tells an office person nothing and offers no way back."""
    app = create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path)))

    @app.get("/boom-html")
    def boom():
        raise RuntimeError("a connection string nobody should read")

    resp = TestClient(app, raise_server_exceptions=False).get(
        "/boom-html", headers={"Accept": "text/html"})

    assert resp.status_code == 500
    assert "text/html" in resp.headers["content-type"]
    assert "Something went wrong" in resp.text
    assert "Go to Today" in resp.text
    assert 'class="sidebar"' in resp.text
    # never the exception: an unhandled error's text can carry a query or a
    # connection string, and a browser is the one place that must not see it
    assert "connection string nobody should read" not in resp.text
    assert "RuntimeError" not in resp.text


def test_a_500_on_the_api_stays_json(test_db_url, tmp_path):
    app = create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path)))

    @app.get("/api/boom-json")
    def boom():
        raise RuntimeError("a connection string nobody should read")

    resp = TestClient(app, raise_server_exceptions=False).get("/api/boom-json")

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "internal server error"}


def test_a_500_inside_an_htmx_fragment_gets_the_partial(test_db_url, tmp_path):
    app = create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path)))

    @app.get("/boom-partial")
    def boom():
        raise RuntimeError("kaboom")

    resp = TestClient(app, raise_server_exceptions=False).get(
        "/boom-partial", headers={"HX-Request": "true"})

    assert resp.status_code == 500
    assert 'class="result error"' in resp.text
    assert 'class="sidebar"' not in resp.text


# --------------------------------------------------------------------------- #
# 5. buttons
# --------------------------------------------------------------------------- #
def test_the_primary_button_colour_is_readable():
    """#2F7A58 is 5.19:1 against white. The mint it replaces was 1.9:1, which
    is white text nobody can read."""
    css = STYLE.read_text()

    assert "#2F7A58" in css
    m = re.search(r"button,\s*\.btn\s*\{[^}]*\}", css, re.S)
    assert m, "the base button rule should still exist"
    assert "--dentalia-action" in m.group(0)
    assert re.search(r"--dentalia-action:\s*#2F7A58", css)


def test_every_disabled_button_stays_legible():
    """White on #c9d2ce is 1.6:1 — a label nobody can read is not a state, and
    a disabled button is the one that carries the reason it cannot be pressed.

    The generic rule, not `.btn-approve[disabled]`: the point is that a button
    added later is legible without anyone remembering to say so. #5a6763 on
    #e6eae8 is 4.87:1."""
    css = STYLE.read_text()

    m = re.search(r"button\[disabled\],\s*\.btn\[disabled\]\s*\{[^}]*\}", css, re.S)
    assert m, "every disabled button should be covered by one rule"
    assert "background: var(--dentalia-disabled-bg)" in m.group(0)
    assert "color: var(--dentalia-disabled-ink)" in m.group(0)
    assert re.search(r"--dentalia-disabled-bg:\s*#e6eae8", css)
    assert re.search(r"--dentalia-disabled-ink:\s*#5a6763", css)


# --------------------------------------------------------------------------- #
# 6. week labels
# --------------------------------------------------------------------------- #
def test_a_week_reads_as_a_week():
    assert words.week_label(date(2026, 9, 10)) == "Week 37 (7–13 Sep)"


def test_a_week_spanning_two_months_names_both():
    assert words.week_label(date(2026, 9, 1)) == "Week 36 (31 Aug–6 Sep)"


def test_every_day_of_a_week_gives_the_same_label():
    labels = {words.week_label(date(2026, 9, d)) for d in range(7, 14)}
    assert labels == {"Week 37 (7–13 Sep)"}


# --------------------------------------------------------------------------- #
# numbers and dates, the two shared filters
# --------------------------------------------------------------------------- #
def test_counts_carry_a_thousands_separator():
    assert words.num(4265) == "4,265"
    assert words.num(0) == "0"
    assert words.num(None) == "—"


def test_one_panel_prints_a_count_one_way():
    """The whole-range panel showed "1,069" on the picker option and "1069" on
    the button beside it, the confirm question and the Yes button. Every
    server-rendered count reads through `| num` / `words.num`, and the one
    label the browser rewrites formats it the same way."""
    from web.app import _bind_confirm_lines

    lines = _bind_confirm_lines("IVOCLAR", 1069, "DoC")
    assert "Approve for all 1,069 IVOCLAR items?" == lines[0]
    assert "1,069 items will show this declaration of conformity." in lines

    templates = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"
    decide = (templates / "_staging_decide.html").read_text()
    assert "{{ bind_items | num }}" in decide
    assert "{{ preselect_items | num }}" in decide
    assert "{{ bind_items }}" not in decide
    assert "{{ preselect_items }}" not in decide

    # base.html rewrites that same button when the manufacturer changes; it
    # must not undo the grouping. `data-items` stays raw, because it is data.
    base = (templates / "base.html").read_text()
    assert "dentaliaThousands(items)" in base
    assert 'data-items="{{ opt.md_items }}"' in (
        templates / "_staging_doc_detail.html").read_text()


def test_review_rows_print_a_date_a_person_reads(client, conn):
    """Missing documents already prints "2 Sep 2026"; Review printed
    "2026-08-17" on the most-read screen in the app."""
    from tests.fixtures import seed_ui

    doc_id = seed_ui.seed_document(
        conn, content_hash="seed-hash-dated-1",
        archive_url="local://seed/dated-1.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group",
        validity_to="2028-01-09",
    )
    seed_ui.seed_item(conn, "dated-item-1", manufacturer_raw="Ivoclar")
    seed_ui.seed_link(conn, "dated-item-1", doc_id,
                      match_basis="ref-list", status="staged")
    conn.commit()

    text = client.get("/staging/docs").text

    # The grouped row (T6) carries its dates in the subline; an expired one
    # also gets the badge. Neither may print an ISO stamp.
    assert "issued 1 Jan 2024" in text
    assert "2024-01-01" not in text
    assert "2028-01-09" not in text


def test_the_review_panel_counts_items_not_products(client, conn):
    """§ 9: Item. The word is the same on the row, in the panel, in the button
    and in the guide. The count moved off the row into the panel when Review
    was regrouped by manufacturer, so this asserts where it now lives."""
    from tests.fixtures import seed_ui

    doc_id = seed_ui.seed_document(
        conn, content_hash="seed-hash-word-items",
        archive_url="local://seed/word-items.pdf")
    for n in (1, 2):
        seed_ui.seed_item(conn, f"word-item-{n}", manufacturer_raw="Ivoclar")
        seed_ui.seed_link(conn, f"word-item-{n}", doc_id,
                          match_basis="ref-list", status="staged")
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text

    assert re.search(r"\d+\s*\n?\s*item", text)
    assert "product" not in text
    assert "product" not in client.get("/staging/docs").text


def test_the_500_log_line_carries_the_traceback(test_db_url, tmp_path, caplog):
    """The handler is the only place that names the failing path, so its log
    line is where a developer starts. Starlette runs a SYNC `Exception` handler
    through `run_in_threadpool`, and `sys.exc_info()` is thread-local: a bare
    `log.exception(...)` there finds no active exception and writes
    `NoneType: None` instead of the stack. The exception has to be passed in."""
    app = create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path)))

    @app.get("/boom-logged")
    def boom():
        raise RuntimeError("the failure a developer has to see")

    with caplog.at_level(logging.ERROR, logger="dentalia.web"):
        TestClient(app, raise_server_exceptions=False).get("/boom-logged")

    records = [r for r in caplog.records
               if r.name == "dentalia.web" and "/boom-logged" in r.getMessage()]
    assert records, "the handler logged nothing for the failing path"
    record = records[0]
    assert record.exc_info and record.exc_info[0] is RuntimeError
    assert "the failure a developer has to see" in record.exc_text or \
        "the failure a developer has to see" in str(record.exc_info[1])
