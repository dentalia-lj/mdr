"""P6: figures that agree (office UI redesign spec § 8, decisions D4 and D7).

Four figures used to disagree with the screens they link to, or to count
something other than what their label said:

* the status board's "in flight" tile counted every pending or running job,
  and ten of the eleven on the dev database were perpetual `scheduler.tick`
  jobs, while the header strip and /inflight count documents being read;
* the status board's coverage headline was 99.7% of items holding *any*
  production paper, a figure that cannot be defended in front of Dentalia
  (docs/decisions.md, 2026-09-11: "Declarations on file for X of Y
  medical-device items (Z%)", where Y - X is Coverage gaps' "no declaration");
* "expiring" meant 180 days ahead on the strip and on /expiry, and the
  renewal horizon (30 days) in the weekly report (D7: one 30-day forward
  window, stated wherever an expiring figure appears; the 180-day lookback
  for recent lapses is unchanged and named too);
* Queues & health showed 19,345 "waiting" on Data quality, which is a count of
  standing notes about catalogue items and documents, not work waiting.

Each test below seeds data where the old definitions and the new one give
different numbers, so a regression shows as a wrong number, not as a
missing word.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import queue
from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _tile(html: str, label: str) -> str | None:
    """The number in the status board's stat tile labelled exactly `label`."""
    m = re.search(
        r'<div class="n">\s*([^<]*?)\s*</div>\s*<div class="label">\s*'
        + re.escape(label) + r"\s*</div>",
        html,
    )
    return m.group(1) if m else None


def _row_with(html: str, needle: str) -> str:
    """The one table row that contains `needle`."""
    rows = [r for r in re.findall(r"<tr[^>]*>.*?</tr>", html, re.S) if needle in r]
    assert len(rows) == 1, f"expected one row holding {needle!r}, got {len(rows)}"
    return rows[0]


# --------------------------------------------------------------------------- #
# 1. Documents being read: `_in_flight_docs`, on the status board
# --------------------------------------------------------------------------- #
def test_documents_being_read_counts_documents_not_every_open_job(client, conn):
    """Ten perpetual `scheduler.tick` jobs and one document being read is one
    document being read. The old tile said 11."""
    for i in range(10):
        queue.enqueue(conn, "scheduler.tick", {"cron": f"figures-cron-{i}"},
                      f"figures-tick-{i}")
    jid = queue.enqueue(conn, "extract.doc", {"content_hash": "figures-hash-1"},
                        "figures-extract-1")
    conn.execute(
        "UPDATE job SET status='running', claimed_by='w1', claimed_at=now() "
        "WHERE id=%s", (jid,))
    conn.commit()

    html = client.get("/status").text

    assert _tile(html, "Documents being read") == "1"
    # the old label is gone rather than kept beside the new one
    assert _tile(html, "in flight") is None
    # and it is the same number /pipeline and /inflight show
    from web.app import _pulse_counts
    assert _pulse_counts(conn)["in_flight_docs"] == 1


# --------------------------------------------------------------------------- #
# 2. Coverage headline (D4): the client's acceptance metric, AC1
# --------------------------------------------------------------------------- #
def _item(conn, ref, *, md_flag=True):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES (%s,%s,'FigMfr',%s,'LJ',now())",
        (ref, f"widget {ref}", md_flag))


def _doc(conn, *, doc_type="DoC", status="production", validity_to=None):
    return conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "  coverage_scope, validity_to) VALUES (%s,'MDR',%s,%s,'/a.pdf','group',%s) "
        "RETURNING doc_id",
        (doc_type, status, f"h-fig-{uuid.uuid4().hex[:12]}", validity_to),
    ).fetchone()["doc_id"]


def _link(conn, ref, doc_id, basis, status="production"):
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,%s,%s)", (ref, doc_id, basis, status))


def _seed_coverage(conn):
    """Ten medical-device items, five of which hold a declaration on file.

    "On file" is AC1 (docs/decisions.md 2026-09-11): a published Declaration
    of Conformity linked to the item itself, by the supplier's article number
    (`ref-list`), our item number (`ref-item`), Basic UDI-DI, or the
    supplier's own coverage map (`map-supplier`). Expiry does not enter it:
    the same row records 1,460 on file and "488 of those unexpired"."""
    covered = {"FIG-REFLIST": "ref-list", "FIG-REFITEM": "ref-item",
               "FIG-UDI": "basic-udi-di", "FIG-MAP": "map-supplier"}
    for ref, basis in covered.items():
        _item(conn, ref)
        _link(conn, ref, _doc(conn), basis)
    # An expired declaration is still a declaration on file.
    _item(conn, "FIG-EXPIRED")
    _link(conn, "FIG-EXPIRED", _doc(conn, validity_to="2020-01-01"), "ref-list")

    # Not on file, each for a different reason:
    _item(conn, "FIG-WHOLE-RANGE")     # a declaration for the manufacturer's whole range
    _link(conn, "FIG-WHOLE-RANGE", _doc(conn), "mfr-scope")
    _item(conn, "FIG-BY-HAND")         # a link a person confirmed; not in AC1's list
    _link(conn, "FIG-BY-HAND", _doc(conn), "manual")
    _item(conn, "FIG-CERT")            # a certificate, not a declaration
    _link(conn, "FIG-CERT", _doc(conn, doc_type="EC"), "ref-list")
    _item(conn, "FIG-STAGED")          # a declaration still waiting for review
    _link(conn, "FIG-STAGED", _doc(conn, status="staged"), "ref-list", status="staged")
    _item(conn, "FIG-NONE")            # nothing at all

    # Outside the denominator either way.
    _item(conn, "FIG-UNCLASSIFIED", md_flag=None)
    _item(conn, "FIG-NOT-MD", md_flag=False)
    conn.commit()


def test_coverage_headline_counts_declarations_on_file_for_device_items(conn):
    from web.app import coverage_headline

    _seed_coverage(conn)

    assert coverage_headline(conn) == {
        "covered": 5, "total": 10, "pct": 50.0, "unclassified": 1}


def test_coverage_headline_agrees_with_coverage_gaps(client, conn):
    """`covered + gaps == total`, where gaps is the number Coverage gaps shows
    against "No Declaration of Conformity". The two screens used different
    predicates: the gaps page counted any production DoC link, so a
    whole-range or hand-confirmed declaration closed a gap there while the
    acceptance metric did not count it."""
    from web.app import coverage_headline

    _seed_coverage(conn)
    head = coverage_headline(conn)

    page = client.get("/coverage").text
    m = re.search(r'gap=doc">[^<]*</a></td>\s*<td>(\d+)</td>', page)
    assert m, page
    assert head["covered"] + int(m.group(1)) == head["total"]

    # and the list behind that number is exactly the five not on file
    listed = client.get("/coverage?gap=doc").text
    for ref in ("FIG-WHOLE-RANGE", "FIG-BY-HAND", "FIG-CERT", "FIG-STAGED", "FIG-NONE"):
        assert ref in listed, ref
    for ref in ("FIG-REFLIST", "FIG-REFITEM", "FIG-UDI", "FIG-MAP", "FIG-EXPIRED"):
        assert ref not in listed, ref


def test_status_board_leads_with_the_declarations_sentence(client, conn):
    from web.app import coverage_headline

    _seed_coverage(conn)
    h = coverage_headline(conn)

    html = client.get("/status").text

    assert (f"Declarations on file for {h['covered']} of {h['total']} "
            f"medical-device items ({h['pct']}%)") in html
    # the definition is on screen, with the items it leaves out
    assert "Counts the items Business Central marks as medical devices." in html
    assert "Another 1 item has no device class in BC and is not counted." in html
    # one click to the items behind the gap
    assert 'href="/coverage?gap=doc"' in html
    # the any-paper tile it replaces is gone
    assert _tile(html, "covered") is None
    assert not re.search(r'<div class="label">covered', html)


def test_coverage_headline_on_an_empty_registry(client, conn):
    """A fresh machine holds no device items. The board says so rather than
    printing "0 of 0 (0.0%)", and the helper still returns a float."""
    from web.app import coverage_headline

    assert coverage_headline(conn) == {
        "covered": 0, "total": 0, "pct": 0.0, "unclassified": 0}
    html = client.get("/status").text
    assert "No medical-device items yet" in html


# --------------------------------------------------------------------------- #
# 3. One expiring window (D7): 30 days, stated wherever the figure appears
# --------------------------------------------------------------------------- #
def _seed_expiring(conn, *, days: int) -> tuple[int, str]:
    """A production certificate `days` from today, linked to one device item
    of a real group so the manufacturer is resolved on every screen."""
    ref = f"FIG-EXP-{uuid.uuid4().hex[:8]}"
    _item(conn, ref)
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('FIGEXP') "
        "RETURNING group_id").fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual')", (gid, ref))
    cert = f"FIGCERT-{uuid.uuid4().hex[:8]}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "  content_hash, archive_url, coverage_scope, cert_number) VALUES "
        "('EC','MDR','2022-01-01', current_date + %s, 'production', %s, '/a.pdf', "
        "  'group', %s) RETURNING doc_id",
        (days, f"h-figexp-{uuid.uuid4().hex[:12]}", cert),
    ).fetchone()["doc_id"]
    _link(conn, ref, doc_id, "ref-list")
    conn.commit()
    return doc_id, cert


def _weekly_report(conn, tmp_path) -> tuple[dict, str]:
    from app.handlers.report import _write_html, handle_report_weekly

    jid = queue.enqueue(conn, "report.weekly", {"period_key": "2026-W37"},
                        "report:figures")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()
    result = handle_report_weekly(conn, job)
    _write_html(str(tmp_path), result)
    return result, (tmp_path / "report-2026-W37.html").read_text()


def test_the_window_is_thirty_days_and_is_the_renewal_clock():
    """One constant, and not a second 30: the worker's report cannot import
    `web`, so the web constant IS `app.compliance.EXPIRY_HORIZON_DAYS`, which
    is guarded equal to the scheduler's renewal horizon on its default. D7
    decided only the forward window; the lookback keeps its 180 days."""
    from app.compliance import EXPIRY_HORIZON_DAYS
    from web.app import EXPIRED_LOOKBACK_DAYS, EXPIRING_WINDOW_DAYS

    assert EXPIRING_WINDOW_DAYS == 30
    assert EXPIRING_WINDOW_DAYS == EXPIRY_HORIZON_DAYS
    assert EXPIRED_LOOKBACK_DAYS == 180


def test_one_window_for_the_strip_the_expiry_page_and_the_weekly_report(
        client, conn, tmp_path):
    """One certificate 20 days out and one 60 days out. Every screen counts
    one, and every label says 30 days."""
    _, soon_cert = _seed_expiring(conn, days=20)
    _, far_cert = _seed_expiring(conn, days=60)

    from web.app import _expiring_counts, _pulse_counts
    assert _pulse_counts(conn)["expiring"] == 1
    assert _expiring_counts(conn) == {"soon": 1, "recent": 0, "review": 0,
                                      "total": 1}
    # The menu carries the count; Today, which the menu's Expiring entry sits
    # beside, is where the window is named (the strip that named it on every
    # page went on 2026-09-14).
    menu = client.get("/_pulse").text
    entry = re.search(r'<a href="/expiry"[^>]*>(.*?)</a>', menu, re.S)
    assert entry, menu
    assert re.search(r'<span class="nav-n">1</span>', entry.group(1))
    today = client.get("/").text
    assert "in the next 30 days" in today
    assert "in the last 180 days" in today

    board = client.get("/expiry").text
    assert "Expiring in the next 30 days (1)" in board
    assert soon_cert in board
    assert far_cert not in board          # beyond the default window, counted
    assert "1 further certificate expires beyond this window" in board

    result, report = _weekly_report(conn, tmp_path)
    assert len(result["expiring"]) == 1
    assert "Expiring in the next 30 days (1)" in report


def test_the_strip_counts_recent_lapses_over_the_180_day_lookback(
        client, conn, tmp_path):
    """The strip counts "expired recently" beside "expiring soon" (the board's
    two working tables). D7 fixed only the forward window at 30 days; the
    lookback keeps 180. Applying 30 backwards took the dev strip from 6 to 0
    and folded four certificates that lapsed 31-180 days ago out of sight, so
    a lapse 60 days ago must still be on the strip and in the recent table.
    A lapse 200 days ago is long expired. The weekly report keeps listing
    every lapse however old, which it states."""
    _, recent_cert = _seed_expiring(conn, days=-10)
    _, sixty_cert = _seed_expiring(conn, days=-60)
    _, old_cert = _seed_expiring(conn, days=-200)

    from web.app import _pulse_counts
    assert _pulse_counts(conn)["expiring"] == 2

    board = client.get("/expiry").text
    assert "Expired in the last 180 days (2)" in board
    assert "Long expired (1)" in board
    for cert in (recent_cert, sixty_cert):
        assert board.index(cert) < board.index("Long expired")
    assert board.index("Long expired") < board.index(old_cert)

    result, report = _weekly_report(conn, tmp_path)
    assert len(result["lapsed"]) == 3
    assert "Already expired (3)" in report


# --------------------------------------------------------------------------- #
# 4. Queues & health: each figure says what it counts
# --------------------------------------------------------------------------- #
def test_queues_and_health_names_what_data_quality_counts(client, conn):
    """The Data quality figure is `count(*) FROM data_anomaly`: one row per
    (kind, subject), where the subject is a catalogue item or a document
    (dev, 2026-09-11: 19,345 rows, 19,142 about items and 194 about
    documents). They are standing notes, not work waiting for anyone, and the
    column header used to call every figure "Waiting"."""
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue) VALUES "
        "('md_class_blank', 'FIG-DQ-1', 'LJ'), "
        "('mfr_ref_missing', 'FIG-DQ-1', 'LJ'), "
        "('no_text_layer', 'h-fig-scan', NULL)")
    conn.commit()

    html = client.get("/pipeline").text

    row = _row_with(html, 'href="/data-quality"')
    assert re.search(r">\s*3\s*<", row), row
    assert "notes on items and documents" in row
    thead = re.search(r"<thead>(.*?)</thead>", html, re.S)
    assert thead, html
    assert "Waiting" not in thead.group(1)
    assert "How many" in thead.group(1)


def test_queues_and_health_labels_every_count_with_its_unit(client, conn):
    queue.enqueue(conn, "extract.doc", {"content_hash": "figures-hash-pipe"},
                  "figures-pipe-1")
    conn.commit()

    html = client.get("/pipeline").text

    assert "documents being read" in _row_with(html, 'href="/inflight"')


def _seed_review_due(conn, *, years_ago: int = 6) -> tuple[int, str]:
    """A class I declaration past Dentalia's own five-year review point.

    A DoC with an issue date and no stated expiry and no cited certificate is
    exactly what `document_effective_expiry` gives `basis='staleness'`
    (migration 027). It has not lapsed in any legal sense -- Article 19(1) sets
    no validity period -- which is the whole of F34.
    """
    ref = f"FIG-REV-{uuid.uuid4().hex[:8]}"
    _item(conn, ref)
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('FIGREV') "
        "RETURNING group_id").fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual')", (gid, ref))
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, "
        "  content_hash, archive_url, coverage_scope) VALUES "
        "('DoC','MDR', current_date - make_interval(years => %s), 'production', "
        "  %s, '/r.pdf', 'group') RETURNING doc_id",
        (years_ago, f"h-figrev-{uuid.uuid4().hex[:12]}"),
    ).fetchone()["doc_id"]
    _link(conn, ref, doc_id, "ref-list")
    conn.commit()
    return doc_id, ref


def test_a_five_year_review_is_not_an_expiry_on_any_surface(client, conn, tmp_path):
    """F34. The item card has said "review due" since 2026-08-24; the board, the
    menu count and the weekly report said "expired" about the same document.
    Reporting a house rule as a regulatory breach is the one error this page
    cannot afford, so all four now draw the line in the same place."""
    doc_id, _ref = _seed_review_due(conn)

    from web.app import _expiring_counts
    counts = _expiring_counts(conn)
    assert counts["review"] == 1
    assert counts["recent"] == 0          # not expired
    assert counts["total"] == 0           # and not this week's renewal work

    board = client.get("/expiry").text
    assert "Review due (1)" in board
    assert "Expired in the last 180 days (0)" in board

    result, report = _weekly_report(conn, tmp_path)
    assert [d["doc_id"] for d in result["review_due"]] == [doc_id]
    assert result["lapsed"] == []
    assert "Due review (1)" in report


def test_a_stated_expiry_is_still_an_expiry(client, conn, tmp_path):
    """The other half of the same line: a document that states its own date, or
    inherits one from a certificate, is expired when that date passes."""
    _seed_expiring(conn, days=-10)

    counts = __import__("web.app", fromlist=["_expiring_counts"])._expiring_counts(conn)
    assert counts["recent"] == 1 and counts["review"] == 0
    assert "Expired in the last 180 days (1)" in client.get("/expiry").text


# --------------------------------------------------------------------------- #
# A figure a person reads: the pager, on every page that has one
# --------------------------------------------------------------------------- #
# `words.num` is rule 7's other half, and `_pager.html` is the one shared strip
# on /items, /documents, /missing, /coverage, /discovery, /audit, /data-quality
# and /staging. It printed `{{ p.total }}` raw, so all eight read
# "1-50 of 16091" while every other figure on the same page read "16,091".
def _many_items(conn, n: int = 1001) -> None:
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) "
        "SELECT 'PAGER-' || g, 'Pager item ' || g, 'PAGERCO', TRUE, 'LJ', 1, now() "
        "FROM generate_series(1, %s) g", (n,))
    conn.commit()


def _many_staged_documents(conn, n: int = 1001) -> None:
    conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, validity_from) "
        "SELECT 'DoC','MDR','group', 'pager-hash-' || g, "
        "       '/archive/pager-' || g || '.pdf', 'staged', DATE '2024-01-01' "
        "FROM generate_series(1, %s) g", (n,))
    conn.commit()


def test_the_plain_pager_reads_its_total_as_a_person_reads_it(client, conn):
    _many_items(conn)

    text = client.get("/items").text

    assert "of 1,001" in text
    assert "of 1001" not in text


def test_the_htmx_pager_reads_its_total_as_a_person_reads_it(client, conn):
    _many_staged_documents(conn)

    text = client.get("/staging/docs").text

    assert "of 1,001" in text
    assert "of 1001" not in text


def test_the_manufacturer_page_counts_items_the_way_it_counts_documents(
        client, conn):
    """Two "showing the first N of M" lines twenty-one lines apart, one of them
    separated and the other not."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('PAGERCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES ('PAGERCO','PAGERCO','vendor-master') ON CONFLICT DO NOTHING")
    _many_items(conn)

    text = client.get("/manufacturers/PAGERCO").text

    assert "of 1,001 items" in text
    assert "of 1001 items" not in text


def test_the_queues_hub_reads_its_counts_as_a_person_reads_them(client, conn):
    """The Data quality cell is the biggest figure the office ever sees on this
    hub: 19,345 standing notes on dev. "19345" is read one digit at a time."""
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue) "
        "SELECT 'md_class_blank', 'FIG-NUM-' || g, 'LJ' "
        "FROM generate_series(1, 1001) g")
    conn.commit()

    row = _row_with(client.get("/pipeline").text, 'href="/data-quality"')

    assert "1,001" in row
    assert ">1001<" not in row.replace(" ", "")
