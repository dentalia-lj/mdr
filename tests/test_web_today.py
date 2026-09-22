"""Today, and the office menu (office UI redesign spec § 4 and § 7).

`/` stops being the KPI board and becomes the office's first screen: four
lists of what is waiting for a person, the failures they can act on, the
coverage headline and the week's report. The board itself moves to `/status`
and is named "System status" in the operator group of the menu.

The menu is the second half of the same change. The top pulse strip goes: its
counts move into the four "Your work" entries, loaded the way `/_pulse` loaded
the strip, and the health signal becomes one line at the bottom of the sidebar.

Every count here is live. Nothing on this page is a write except the two
Failed buttons, which post to the routes `/dead` already owns.
"""

from __future__ import annotations

import html as html_mod
import pathlib
import re
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _text(page: str) -> str:
    """The page as a reader sees it: tags dropped, entities decoded,
    whitespace folded."""
    t = re.sub(r"<[^>]+>", " ", page)
    t = html_mod.unescape(t)
    return re.sub(r"\s+", " ", t)


def _section(page: str, section_id: str) -> str:
    """One `<section id=…>` of the page, markup and all."""
    m = re.search(r'<section\b[^>]*\bid="' + re.escape(section_id) + r'".*?</section>',
                  page, re.S)
    assert m, f"no section #{section_id} on the page"
    return m.group(0)


def _sidebar(page: str) -> str:
    m = re.search(r"<aside\b.*?</aside>", page, re.S)
    assert m, "no sidebar on the page"
    return m.group(0)


def _nav_links(page: str) -> list[tuple[str, str]]:
    """(href, label) for every link in the sidebar nav, in document order."""
    nav = re.search(r"<nav\b.*?</nav>", _sidebar(page), re.S)
    assert nav, "no <nav> in the sidebar"
    return [(href, _text(inner).strip())
            for href, inner in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                          nav.group(0), re.S)]


# --------------------------------------------------------------------------- #
# seeds
# --------------------------------------------------------------------------- #

def _item(conn, ref, *, manufacturer="IVOCLAR", md_flag=True):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES (%s,%s,%s,%s,'LJ',now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (ref, f"widget {ref}", manufacturer, md_flag))


def _doc(conn, *, status="staged", doc_type="DoC", validity_to=None,
         created_at=None):
    return conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "  coverage_scope, validity_to, created_at) "
        "VALUES (%s,'MDR',%s,%s,'/a.pdf','group',%s, coalesce(%s::timestamptz, now())) "
        "RETURNING doc_id",
        (doc_type, status, f"h-today-{uuid.uuid4().hex[:12]}", validity_to, created_at),
    ).fetchone()["doc_id"]


def _link(conn, ref, doc_id, basis="ref-list", status="staged"):
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,%s,%s)", (ref, doc_id, basis, status))


def _staged(conn, manufacturer: str, *, created_at: str | None = None) -> int:
    """One staged document linked to one item of `manufacturer`."""
    ref = f"TODAY-{uuid.uuid4().hex[:8]}"
    _item(conn, ref, manufacturer=manufacturer)
    doc_id = _doc(conn, created_at=created_at)
    _link(conn, ref, doc_id)
    return doc_id


def _dead_end(conn, manufacturer: str, *, label="WIDGET 5KOS") -> int:
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,%s) "
        "RETURNING group_id", (manufacturer, label)).fetchone()["group_id"]
    return conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status) "
        "VALUES ('discovery-dead-end', %s, %s, 'open') RETURNING id",
        (gid, Json({"label": label, "manufacturer": manufacturer})),
    ).fetchone()["id"]


def _expiring(conn, *, days: int) -> str:
    """A production certificate `days` from today, on one device item."""
    ref = f"TODAY-EXP-{uuid.uuid4().hex[:8]}"
    _item(conn, ref)
    cert = f"TODAYCERT-{uuid.uuid4().hex[:8]}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "  content_hash, archive_url, coverage_scope, cert_number) VALUES "
        "('EC','MDR','2022-01-01', current_date + %s, 'production', %s, '/a.pdf', "
        "  'group', %s) RETURNING doc_id",
        (days, f"h-todayexp-{uuid.uuid4().hex[:12]}", cert),
    ).fetchone()["doc_id"]
    _link(conn, ref, doc_id, "ref-list", "production")
    return cert


def _draft(conn, *, status="draft") -> int:
    return conn.execute(
        "INSERT INTO email_draft (kind, status, subject, body, to_addrs) "
        "VALUES ('request', %s, 'Renewal', 'body', ARRAY['a@b.si']) RETURNING id",
        (status,)).fetchone()["id"]


def _dead_job(conn, *, job_type, error, key, payload=None) -> int:
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority, attempts, "
        "                 max_attempts, last_error, finished_at) "
        "VALUES (%s::job_type, %s, %s, 'dead', 'sweep', 5, 5, %s, now()) RETURNING id",
        (job_type, Json(payload or {}), key, error)).fetchone()["id"]


def _timeout(conn, host="slow.example") -> int:
    url = f"https://{host}/{uuid.uuid4().hex[:6]}.pdf"
    return _dead_job(conn, job_type="fetch.url", error="ConnectTimeout: timed out",
                     key=f"fetch:{url}",
                     payload={"url": url, "domain": host, "group_id": 11})


def _gone(conn, host="gone.example") -> int:
    url = f"https://{host}/{uuid.uuid4().hex[:6]}.pdf"
    return _dead_job(conn, job_type="fetch.url",
                     error=f"FetchError: unexpected status 404 for {url}",
                     key=f"fetch:{url}",
                     payload={"url": url, "domain": host, "group_id": 21})


def _developer_fault(conn) -> int:
    key = f"extract:{uuid.uuid4().hex[:8]}"
    return _dead_job(conn, job_type="extract.doc", error="KeyError: 'archive_url'",
                     key=key, payload={"content_hash": key})


# --------------------------------------------------------------------------- #
# 1. The four lists
# --------------------------------------------------------------------------- #

def test_today_shows_the_four_lists_with_live_counts(client, conn):
    """Each list carries its count, its one sentence and its one button."""
    for _ in range(3):
        _staged(conn, "IVOCLAR", created_at="2026-08-20 09:00+00")
    _staged(conn, "KERR", created_at="2026-09-02 09:00+00")
    for _ in range(2):
        _dead_end(conn, "DURR DENTAL")
    _dead_end(conn, "NSK")
    _expiring(conn, days=10)
    _expiring(conn, days=-20)
    _draft(conn)
    _draft(conn, status="ready")
    _draft(conn, status="sent")        # already sent: not work to do
    conn.commit()

    page = client.get("/")
    assert page.status_code == 200
    body = page.text
    assert "<h1>Today</h1>" in body

    review = _text(_section(body, "today-review"))
    assert "Documents to review" in review
    assert "4" in review
    assert "IVOCLAR 3" in review and "KERR 1" in review
    assert "20 Aug 2026" in review                       # the oldest waiting
    assert 'href="/staging"' in _section(body, "today-review")
    assert "Start reviewing" in review

    missing = _text(_section(body, "today-missing"))
    assert "Missing documents" in missing
    assert "3" in missing
    assert "DURR DENTAL 2" in missing and "NSK 1" in missing
    assert 'href="/missing"' in _section(body, "today-missing")
    assert "Open the list" in missing

    expiring = _text(_section(body, "today-expiring"))
    assert "Expiring certificates" in expiring
    assert "1 lapses in the next 30 days" in expiring
    assert "1 lapsed in the last 180 days" in expiring
    assert 'href="/expiry"' in _section(body, "today-expiring")
    assert "See which" in expiring

    drafts = _text(_section(body, "today-drafts"))
    assert "Renewal emails to send" in drafts
    assert "2" in drafts
    assert ("Written by the system from what is expiring. It never sends them: read, "
            "edit, send from your own mailbox, then mark as sent.") in drafts
    assert 'href="/drafts"' in _section(body, "today-drafts")
    assert "Open drafts" in drafts


def test_today_renders_on_an_empty_database(client):
    """A fresh `docker compose up`: four lists at zero, no exception, and no
    figure whose definition is missing."""
    page = client.get("/")
    assert page.status_code == 200
    body = _text(page.text)
    assert "Today" in body
    assert "Nothing waiting" in body


def test_large_counts_carry_a_thousands_separator(client, conn):
    """"1460 of 4265" is a figure an office reader has to count digits in.

    Seeded past a thousand so the assertion is about what the page printed:
    below 1,000 the separated and unseparated forms are the same string, which
    is how the status board printed "1460 of 4265" for a week under a test
    that passed.
    """
    from web.app import coverage_headline

    # generate_series rather than 1,200 round trips: this test is about
    # rendering, and the seed should not be the slow part of it.
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "  catalogue, updated_at) "
        "SELECT 'TODAY-BIG-' || g, 'widget ' || g, 'IVOCLAR', TRUE, 'LJ', now() "
        "FROM generate_series(1, 1200) g")
    conn.commit()

    head = coverage_headline(conn)
    assert head["total"] == 1200
    assert "1,200" in _text(_section(client.get("/").text, "today-coverage"))
    # and the board, which renders the same sentence through the same macro
    assert "1,200" in client.get("/status").text


def test_the_review_list_names_the_top_three_manufacturers_only(client, conn):
    for name, n in (("A-MFR", 4), ("B-MFR", 3), ("C-MFR", 2), ("D-MFR", 1)):
        for _ in range(n):
            _staged(conn, name)
    conn.commit()

    review = _text(_section(client.get("/").text, "today-review"))
    for name in ("A-MFR", "B-MFR", "C-MFR"):
        assert name in review
    assert "D-MFR" not in review


# --------------------------------------------------------------------------- #
# 2. The failed lines
# --------------------------------------------------------------------------- #

def test_today_carries_the_three_failed_lines_with_their_buttons(client, conn):
    _timeout(conn)
    _timeout(conn, host="slower.example")
    _gone(conn)
    _developer_fault(conn)
    conn.commit()

    body = client.get("/").text
    text = _text(body)
    assert "2 supplier websites took too long to answer" in text
    assert "Try the 2 again" in text
    assert 'hx-post="/dead/retry-timeouts"' in body

    assert "1 document address no longer works" in text
    assert "Search again" in text
    assert 'hx-post="/dead/search-again"' in body

    assert "1 technical fault is waiting for the developer" in text
    assert "Nothing to do here." in text
    assert "What are they?" in text
    assert 'href="/dead"' in body


def test_a_failed_line_whose_count_is_zero_is_not_rendered(client, conn):
    _timeout(conn)
    conn.commit()

    body = client.get("/").text
    text = _text(body)
    assert "1 supplier website took too long to answer" in text
    assert "address no longer works" not in text
    assert "addresses no longer work" not in text
    assert "waiting for the developer" not in text


def test_the_office_failed_figure_is_what_still_needs_someone(client, conn):
    """The header strip counted every dead job, 87 of them on dev. The office
    number is what `failed_counts` still counts: a dead job whose work is
    queued again is somebody's work in progress, not a task waiting."""
    from app import queue
    from web.failures import failed_counts

    jid = _timeout(conn)
    key = conn.execute("SELECT dedupe_key, payload FROM job WHERE id=%s",
                       (jid,)).fetchone()
    _timeout(conn, host="still-waiting.example")
    queue.enqueue(conn, "fetch.url", key["payload"], key["dedupe_key"],
                  priority="interactive")
    conn.commit()

    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE status='dead'").fetchone()["n"] == 2
    assert failed_counts(conn)["timeout"] == 1
    assert "1 supplier website took too long to answer" in _text(client.get("/").text)


# --------------------------------------------------------------------------- #
# 3. Coverage headline and the weekly summary
# --------------------------------------------------------------------------- #

def test_today_carries_the_coverage_headline_and_its_definition(client, conn):
    from web.app import coverage_headline

    ref = "TODAY-COV-1"
    _item(conn, ref)
    _link(conn, ref, _doc(conn, status="production"), "ref-list", "production")
    _item(conn, "TODAY-COV-2")
    _item(conn, "TODAY-COV-3", md_flag=None)
    conn.commit()

    head = coverage_headline(conn)
    assert (head["covered"], head["total"], head["unclassified"]) == (1, 2, 1)

    body = client.get("/").text
    assert (f"Declarations on file for {head['covered']} of {head['total']} "
            f"medical-device items ({head['pct']}%)") in body
    assert "Counts the items Business Central marks as medical devices." in body
    assert "Another 1 item has no device class in BC and is not counted." in body
    assert 'href="/coverage?gap=doc"' in body


def test_today_and_the_board_render_the_identical_coverage_sentence(client, conn):
    """The sentence quoted to the client cannot depend on which screen it was
    read from. It was written out twice until this slice, and the two had
    already drifted: Today printed "1,460 of 4,265" and the board "1460 of
    4265". One macro now, so this compares the rendered text, not the source.
    """
    ref = "TODAY-SAME-1"
    _item(conn, ref)
    _link(conn, ref, _doc(conn, status="production"), "ref-list", "production")
    _item(conn, "TODAY-SAME-2")
    _item(conn, "TODAY-SAME-3", md_flag=None)
    conn.commit()

    def sentence(page: str) -> str:
        m = re.search(r'<p class="metric">.*?</p>\s*<p class="hint">.*?</p>', page, re.S)
        assert m, "no coverage sentence on the page"
        return _text(m.group(0))

    today = sentence(_section(client.get("/").text, "today-coverage"))
    board = sentence(client.get("/status").text)
    assert today == board
    assert "Declarations on file for 1 of 2 medical-device items (50.0%)" in today
    assert "Another 1 item has no device class in BC and is not counted." in today


def test_today_links_the_latest_two_weekly_reports(client, tmp_path, monkeypatch):
    """A week reads as a week, not as `report-2026-W37.html`."""
    (tmp_path / "report-2026-W35.html").write_text("<h1>35</h1>")
    (tmp_path / "report-2026-W36.html").write_text("<h1>36</h1>")
    (tmp_path / "report-2026-W37.html").write_text("<h1>37</h1>")
    import web.app as wa
    cfg = wa.load_config()
    monkeypatch.setattr(
        wa, "load_config",
        lambda: cfg.__class__(**{**cfg.__dict__,
                                 "web": type(cfg.web)(**{**cfg.web.__dict__,
                                                         "reports_dir": str(tmp_path)})}))

    body = client.get("/").text
    weekly = _text(_section(body, "today-weekly"))
    assert "Week 37 (7–13 Sep)" in weekly
    assert "Week 36 (31 Aug–6 Sep)" in weekly
    assert "Week 35" not in weekly                      # two, not the whole list
    assert 'href="/reports/report-2026-W37.html"' in _section(body, "today-weekly")
    assert 'href="/reports"' in _section(body, "today-weekly")


def test_a_week_label_reads_as_a_week():
    from web.app import report_week_label as week_label

    assert week_label("report-2026-W37.html") == "Week 37 (7–13 Sep)"
    assert week_label("report-2026-W36.html") == "Week 36 (31 Aug–6 Sep)"
    # An unexpected name is passed through rather than guessed at.
    assert week_label("report-nonsense.html") == "report-nonsense"


def test_today_says_so_when_no_report_has_been_written(client):
    weekly = _text(_section(client.get("/").text, "today-weekly"))
    assert "No weekly report yet" in weekly


# --------------------------------------------------------------------------- #
# 4. The status board moved to /status
# --------------------------------------------------------------------------- #

def test_the_kpi_board_is_at_status_and_not_on_today(client, conn):
    board = client.get("/status")
    assert board.status_code == 200
    assert "KPI board" in board.text

    today = client.get("/").text
    assert "KPI board" not in today


def test_the_status_board_keeps_its_job_filter_on_status(client, conn):
    from app import queue

    queue.enqueue(conn, "backfill.scan", {}, "today-status-probe")
    conn.commit()

    board = client.get("/status?tab=jobs&type=backfill.scan").text
    assert "today-status-probe" in board
    # the filter form posts back to the board, not to Today
    assert 'action="/status"' in board
    assert 'action="/"' not in board


def test_no_template_still_points_at_the_old_board_address(client, conn):
    """Every link and form that meant "the status board" now says `/status`.

    Swept over the template directory rather than over one fetched page: the
    board was reachable from several of them, and a page nobody thought to
    fetch in a test is exactly where a stale `href="/?tab=jobs"` survives. `/`
    is Today now, so only the logo and the menu's own entry may point at it,
    and nothing may submit a form there.
    """
    import web.app

    root = pathlib.Path(web.app.__file__).resolve().parent / "templates"
    # error.html joins them deliberately (slice P7a): its whole job is to put
    # the way back on screen, and the way back is Today.
    allowed = {("base.html", "href"), ("_pulse.html", "href"),
               ("error.html", "href")}
    offenders = []
    for f in sorted(root.glob("*.html")):
        for attr, href in re.findall(r'(href|action)="(/(?:\?[^"]*)?)"', f.read_text()):
            if href == "/" and (f.name, attr) in allowed:
                continue
            offenders.append(f"{f.name}: {attr}=\"{href}\"")
    assert not offenders, offenders

    # and the one page that linked to it by name says so in words
    from app import queue
    jid = queue.enqueue(conn, "backfill.scan", {}, "today-link-probe")
    conn.commit()
    detail = client.get(f"/jobs/{jid}").text
    assert 'href="/status"' in detail
    assert "back to System status" in _text(detail)


def test_the_renewal_count_is_on_the_page_it_links_to(client, conn):
    """A menu number a person cannot find after clicking it is a number they
    have to take on trust. `/drafts` leads with the same figure, from the same
    helper: `draft` + `ready`, never the grand total, which counts archived
    drafts nobody is going to send."""
    from web.app import _drafts_waiting, _menu_counts

    _draft(conn)
    _draft(conn, status="ready")
    _draft(conn, status="sent")
    _draft(conn, status="cancelled")
    conn.commit()

    assert _drafts_waiting(conn) == 2
    assert _menu_counts(conn)["drafts"] == 2

    board = _text(client.get("/drafts").text)
    assert "2 renewal emails to send" in board
    assert "4" in board                      # the four per-status tiles remain
    assert "2" in _text(_section(client.get("/").text, "today-drafts"))


def test_one_renewal_email_reads_as_one(client, conn):
    _draft(conn)
    conn.commit()
    assert "1 renewal email to send" in _text(client.get("/drafts").text)


# --------------------------------------------------------------------------- #
# 5. The office menu (§ 7)
# --------------------------------------------------------------------------- #

#: The menu table of spec § 7, in order.
MENU = [
    ("/", "Today"),
    ("/staging", "Review"),
    ("/missing", "Missing documents"),
    ("/expiry", "Expiring"),
    ("/drafts", "Renewal emails"),
    ("/items", "Items"),
    ("/documents", "Documents"),
    ("/manufacturers", "Manufacturers"),
    ("/manufacturers/eudamed", "EUDAMED checks"),
    ("/emails", "Emails received"),
    ("/upload", "Upload a document"),
    ("/import", "Import from Business Central"),
    ("/status", "System status"),
    ("/dead", "Failed tasks"),
    ("/pipeline", "Queues & health"),
    ("/playbooks", "Playbooks"),
    ("/data-quality", "Data quality"),
    ("/audit", "Decisions log"),
    ("/scheduler", "Scheduler"),
    ("/bc-push", "Business Central push"),
    ("/api-reference", "API"),
]


def test_the_menu_is_the_spec_table_in_order(client):
    links = _nav_links(client.get("/items").text)
    assert [href for href, _ in links] == [href for href, _ in MENU]
    for (href, label), (_, want) in zip(links, MENU):
        assert label.startswith(want), (href, label, want)


def test_the_menu_names_its_three_office_groups(client):
    nav = _text(_sidebar(client.get("/items").text))
    for label in ("Your work", "Records", "Add"):
        assert label in nav


def test_the_operator_group_is_one_collapsed_block_at_the_bottom(client):
    sidebar = _sidebar(client.get("/items").text)
    m = re.search(r"<details\b[^>]*>(.*?)</details>", sidebar, re.S)
    assert m, "the operator group is not a <details>"
    assert "open" not in m.group(0).split(">")[0], "the operator group starts expanded"
    inside = m.group(1)
    for href, _ in MENU[12:]:
        assert f'href="{href}"' in inside, href
    for href, _ in MENU[:12]:
        assert f'href="{href}"' not in inside, href


def test_the_operator_group_is_hidden_from_an_office_login(client, tmp_path):
    """`is_operator` decides only what the menu shows. The pages stay
    reachable by URL (D3); a later slice adds the server-side refusals."""
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True, operator_users=("ops",))
    app = TestClient(create_app(cfg))

    office = app.get("/items", headers={"X-Forwarded-User": "mojca"}).text
    assert 'href="/scheduler"' not in _sidebar(office)
    operator = app.get("/items", headers={"X-Forwarded-User": "ops"}).text
    assert 'href="/scheduler"' in _sidebar(operator)


def test_the_logo_goes_to_today(client):
    brand = re.search(r'<a class="brand"[^>]*href="([^"]+)"', client.get("/items").text)
    assert brand and brand.group(1) == "/"


# --------------------------------------------------------------------------- #
# 6. Pages that left the menu stay reachable
# --------------------------------------------------------------------------- #

def test_coverage_and_discovery_are_reachable_from_items(client):
    page = client.get("/items").text
    row = re.search(r'<nav class="link-row".*?</nav>', page, re.S)
    assert row, "no link row on Items"
    assert 'href="/coverage"' in row.group(0)
    assert 'href="/discovery"' in row.group(0)
    text = _text(row.group(0))
    assert "All items" in text
    assert "Without a declaration" in text
    assert "Never searched" in text


def test_the_eudamed_queues_are_reachable_from_the_eudamed_page(client):
    page = client.get("/manufacturers/eudamed").text
    assert 'href="/manufacturers/srn-queue"' in page
    assert 'href="/manufacturers/sweep-due"' in page


def test_onboarding_is_reachable_from_playbooks(client):
    assert 'href="/onboarding"' in client.get("/playbooks").text


def test_the_weekly_reports_are_reachable_from_today(client):
    assert 'href="/reports"' in client.get("/").text


# --------------------------------------------------------------------------- #
# 7. The menu counts and the health line
# --------------------------------------------------------------------------- #

def test_the_top_pulse_strip_is_gone(client):
    page = client.get("/items").text
    assert 'class="pulse"' not in page
    assert "pulse-item" not in page
    assert 'aria-label="Queue pulse"' not in page


def test_the_menu_counts_come_from_the_pulse_endpoint(client, conn):
    _staged(conn, "IVOCLAR")
    _dead_end(conn, "NSK")
    _expiring(conn, days=10)
    _draft(conn)
    conn.commit()

    page = client.get("/items").text
    assert 'hx-get="/_pulse"' in page

    fragment = client.get("/_pulse")
    assert fragment.status_code == 200
    assert "<html" not in fragment.text          # a fragment, not a page
    counts = {href: m.group(1)
              for href, inner in re.findall(
                  r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', fragment.text, re.S)
              if (m := re.search(r'<span class="nav-n">(\d+)</span>', inner))}
    assert counts["/staging"] == "1"
    assert counts["/missing"] == "1"
    assert counts["/expiry"] == "1"
    assert counts["/drafts"] == "1"


def test_the_menu_shows_a_dash_until_the_counts_load(client):
    """With JS off (or before the first swap) the menu is still the menu: the
    counts read as not-loaded rather than as a silent zero."""
    nav = _sidebar(client.get("/items").text)
    assert 'href="/staging"' in nav
    assert "&ndash;" in nav or "–" in nav


def test_the_health_line_says_the_system_is_working(client):
    line = _text(client.get("/_pulse").text)
    assert "System working" in line


def test_the_health_line_names_website_timeouts(client, conn):
    _timeout(conn)
    _timeout(conn, host="slower.example")
    conn.commit()

    fragment = client.get("/_pulse").text
    assert "2 websites timed out" in _text(fragment)
    assert 'href="/dead"' in fragment


def test_the_health_line_is_quiet_when_no_website_timed_out(client, conn):
    _developer_fault(conn)
    conn.commit()

    assert "timed out" not in _text(client.get("/_pulse").text)


def test_the_health_line_reports_a_service_that_is_not_running(client, conn):
    """Rule 8: one line for the office. A worker that has stopped beating is
    not "System working"."""
    conn.execute(
        "INSERT INTO service_heartbeat (service, instance, last_seen) "
        "VALUES ('worker','w1', now() - interval '10 minutes') "
        "ON CONFLICT (service, instance) DO UPDATE SET last_seen = EXCLUDED.last_seen")
    conn.commit()

    line = _text(client.get("/_pulse").text)
    assert "System working" not in line
    assert "worker" in line


# --------------------------------------------------------------------------- #
# 8. Today is a producer page like every other
# --------------------------------------------------------------------------- #

def test_today_writes_nothing(client, conn):
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    client.get("/")
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == before


def test_try_the_timeouts_again_from_today_queues_the_same_work(client, conn):
    """The button posts to the route `/dead` owns, so one implementation
    decides what is retried."""
    _timeout(conn)
    conn.commit()

    resp = client.post("/dead/retry-timeouts")
    assert resp.status_code == 200
    assert "Trying the 1 again" in resp.text
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='fetch.url' AND status='pending'"
    ).fetchone()["n"] == 1


def test_the_oldest_waiting_date_is_a_date_a_person_reads(client, conn):
    _staged(conn, "IVOCLAR", created_at="2026-08-03 07:30+00")
    conn.commit()

    review = _text(_section(client.get("/").text, "today-review"))
    assert "3 Aug 2026" in review
    assert "2026-08-03" not in review


def test_expiring_states_both_windows(client, conn):
    """D7: every expiring figure states its window."""
    _expiring(conn, days=5)
    conn.commit()

    expiring = _text(_section(client.get("/").text, "today-expiring"))
    assert "next 30 days" in expiring
    assert "last 180 days" in expiring


def test_the_expiring_figure_agrees_with_the_expiry_board(client, conn):
    _expiring(conn, days=10)
    _expiring(conn, days=-20)
    _expiring(conn, days=-300)          # long expired: neither window
    conn.commit()

    from web.app import _expiring_counts
    counts = _expiring_counts(conn)
    assert (counts["soon"], counts["recent"]) == (1, 1)

    board = client.get("/expiry").text
    assert "Expiring in the next 30 days (1)" in board
    assert "Expired in the last 180 days (1)" in board


def test_today_is_reachable_when_the_reports_directory_is_missing(client, monkeypatch):
    """A machine told nowhere to write reports still renders Today."""
    import web.app as wa
    cfg = wa.load_config()
    monkeypatch.setattr(
        wa, "load_config",
        lambda: cfg.__class__(**{**cfg.__dict__,
                                 "web": type(cfg.web)(**{**cfg.web.__dict__,
                                                         "reports_dir": "/nope/not/here"})}))
    assert client.get("/").status_code == 200


def test_the_day_the_page_is_read_is_not_baked_into_it(client, conn):
    """The lists are live: a document staged today is on it today."""
    _staged(conn, "IVOCLAR", created_at=str(date.today() - timedelta(days=1)))
    conn.commit()

    review = _text(_section(client.get("/").text, "today-review"))
    assert "1" in review


def test_only_a_control_that_changes_something_is_a_button(client, conn):
    """Denis, 2026-09-14. Four pill buttons down the page made four ordinary
    destinations look like four decisions. The way into a list is a text link;
    a button on Today means the press changes something, and the only two that
    do are on Failed tasks, which POST."""
    _staged(conn, "IVOCLAR")
    _expiring(conn, days=10)
    _draft(conn)
    _timeout(conn)
    _gone(conn)
    conn.commit()

    body = client.get("/").text

    for section in ("today-review", "today-missing", "today-expiring",
                    "today-drafts"):
        html = _section(body, section)
        assert "<button" not in html, f"{section} may not carry a button"
        assert 'class="btn"' not in html, f"{section} may not carry a button"
        assert 'class="today-go"' in html, f"{section} needs its way in"

    failed = _section(body, "today-failed")
    assert failed.count("<button") == 2, "the two retries stay buttons"
    assert "hx-post=" in failed


def test_the_today_card_takes_the_shared_padding_and_no_margin():
    """Today was the first screen condensed, on 2026-09-14, and carried its own
    tighter padding for a day. On 2026-09-15 the whole sheet moved onto one
    scale and `--pad-card` became that same tight value everywhere, so the
    override went: one card is one card.

    The bottom margin is the part that stays overridden, because the four sit
    in a grid whose `gap` already spaces them and a margin on top of it double
    spaces. It is written `.card.today-list` rather than `.today-list` because
    `.card` is declared further down the file and would win on source order at
    equal specificity -- that exact mistake shipped once and the page looked
    unchanged on dev."""
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "static" / "css" / "style.css").read_text()

    m = re.search(r"\.card\.today-list \{([^}]*)\}", css)
    assert m, "the Today card must out-specify .card, not merely precede it"
    assert "margin-bottom: 0" in m.group(1)
    assert "padding" not in m.group(1), (
        "the shared --pad-card is the tight value now; a local override here "
        "means the scale moved and this page was left behind")

    generic = re.search(r"^\.card \{([^}]*)\}", css, re.M)
    assert generic and "padding: var(--pad-card)" in generic.group(1)
    assert css.index(m.group(0)) < css.index(generic.group(0)), (
        "the override sits before .card, so specificity is the only thing "
        "making it win -- that is what this test pins")


def test_the_four_lists_sit_side_by_side(client, conn):
    """Denis, 2026-09-15: a grid, not one below the other. Stacked, the four
    pushed the coverage figure and the week's report below the fold.

    The three blocks under them stay full width: Failed tasks carries forms,
    and the coverage sentence and the week's report are single statements, not
    peers of the four."""
    _staged(conn, "IVOCLAR")
    _expiring(conn, days=10)
    _draft(conn)
    _timeout(conn)
    conn.commit()

    body = client.get("/").text

    grid = re.search(r'<div class="today-lists">(.*?)\n</div>', body, re.S)
    assert grid, "the four lists need one grid container"
    for section in ("today-review", "today-missing", "today-expiring",
                    "today-drafts"):
        assert f'id="{section}"' in grid.group(1), f"{section} belongs in the grid"
    for section in ("today-failed", "today-coverage", "today-weekly"):
        assert f'id="{section}"' not in grid.group(1), (
            f"{section} is a single block, not one of the four peers")

    css = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "static" / "css" / "style.css").read_text()
    rule = re.search(r"\.today-lists\s*\{([^}]*)\}", css)
    assert rule, "the container needs its own rule"
    assert "display: grid" in rule.group(1)
    # auto-fit folds to one column on a narrow window with no breakpoint
    assert "auto-fit" in rule.group(1) and "minmax" in rule.group(1)


def test_the_type_scale_is_the_only_source_of_size():
    """One scale, added 2026-09-15.

    Before it the sheet carried ten distinct font sizes -- 10, 11, 12, 13, 14,
    15, 16, 20, 21 and 22px -- five radii and about twenty-four padding pairs.
    None of that was a scale; it was what each screen happened to need on the
    day it was written, and it is why the app read as three different products.

    A raw px font size outside `:root` is how that came back the last time, so
    it fails here rather than in someone's eye six weeks later."""
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "static" / "css" / "style.css").read_text()

    root = re.search(r":root \{(.*?)\n\}", css, re.S)
    assert root, "the token block is the thing this test is about"
    after = css[root.end():]

    raw = re.findall(r"font-size: *([0-9.]+(?:px|em|rem))", after)
    assert raw == [], (
        "a size outside the scale: use one of --fs-title, --fs-figure, "
        "--fs-section, --fs-body, --fs-meta, --fs-micro, --fs-label -- got "
        + ", ".join(sorted(set(raw))))

    # every token the sheet reaches for must actually be declared somewhere:
    # `--review-gutter` is deliberately scoped to the Review panel rather than
    # global, so this looks at the whole sheet, not just the root block
    declared = set(re.findall(r"(--[a-z0-9-]+): *[^;]", css))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", after))
    assert used <= declared, (
        "a rule reads a token nothing declares: "
        + ", ".join(sorted(used - declared)))
