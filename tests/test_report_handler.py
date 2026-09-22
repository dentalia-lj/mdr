"""report.weekly handler (S1.5): read-only snapshot queries + dispatch.

expiring_documents/job_counts_by_type_status/dead_job_count are shared with
the scheduler's own expiry-scan tick (docs/specs/scheduler.md §3).
"""

from __future__ import annotations

import datetime as dt

from app import queue


def _insert_document(conn, *, status="production", validity_to=None, type_="DoC",
                     validity_from=None, cert_doc_id=None):
    row = conn.execute(
        """
        INSERT INTO document (type, regulation, coverage_scope, content_hash,
                               archive_url, status, validity_to, validity_from,
                               cert_doc_id)
        VALUES (%s, 'MDR', 'group', %s, 'file:///x', %s, %s, %s, %s)
        RETURNING doc_id
        """,
        (type_, f"hash-{id(object())}-{status}-{validity_to}-{validity_from}",
         status, validity_to, validity_from, cert_doc_id),
    ).fetchone()
    return row["doc_id"]


# --------------------------------------------------------------------------- #
# expiring_documents
# --------------------------------------------------------------------------- #
def test_expiring_documents_includes_production_doc_within_horizon(conn):
    from app.handlers.report import expiring_documents

    today = dt.date(2026, 7, 23)
    doc_id = _insert_document(conn, validity_to=today + dt.timedelta(days=10))

    result = expiring_documents(conn, horizon_days=90, today=today)

    assert doc_id in [d["doc_id"] for d in result]


def test_expiring_documents_excludes_non_production_states(conn):
    from app.handlers.report import expiring_documents

    today = dt.date(2026, 7, 23)
    for status in ("staged", "superseded", "rejected"):
        _insert_document(conn, status=status, validity_to=today + dt.timedelta(days=10))

    result = expiring_documents(conn, horizon_days=90, today=today)

    assert result == []


def test_expiring_documents_null_validity_to_excluded(conn):
    from app.handlers.report import expiring_documents

    today = dt.date(2026, 7, 23)
    _insert_document(conn, validity_to=None)

    result = expiring_documents(conn, horizon_days=90, today=today)

    assert result == []


def test_expiring_documents_excludes_outside_horizon(conn):
    from app.handlers.report import expiring_documents

    today = dt.date(2026, 7, 23)
    _insert_document(conn, validity_to=today + dt.timedelta(days=200))

    result = expiring_documents(conn, horizon_days=90, today=today)

    assert result == []


def test_expiring_documents_includes_docs_inheriting_a_cert_expiry(conn):
    from app.handlers.report import expiring_documents

    cert = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','C-5',"
        "%s,'production','h-c5','/archive/c5.pdf','group') RETURNING doc_id",
        (dt.date(2026, 9, 1),),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES ('DoC','MDR',"
        "'C-5', NULL, 'production','h-d5','/archive/d5.pdf','group',%s)",
        (cert,),
    )
    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 8, 6))
    hashes = {r["doc_id"] for r in out}
    assert cert in hashes
    assert len(out) == 2, "the DoC must appear too, via its inherited expiry"


# --------------------------------------------------------------------------- #
# expiring_documents — the five-year staleness horizon (client ruling 2026-08-18)
#
# "Kjer je datum napisan upoštevaj datum — kjer ga ni upoštevaj + 5 let za
# potek." A declaration that states no expiry is reviewed five years after it
# was issued. It is a REVIEW horizon, not a legal expiry (MDR Annex IV requires
# no expiry on a declaration; Article 19(1) requires it be kept up to date), and
# `basis` must say so on every row.
# --------------------------------------------------------------------------- #
def test_a_declaration_with_no_expiry_is_reviewed_five_years_after_issue(conn):
    from app.handlers.report import expiring_documents

    today = dt.date(2026, 7, 23)
    doc_id = _insert_document(conn, validity_to=None,
                              validity_from=dt.date(2021, 8, 1))  # +5y = 2026-08-01

    out = expiring_documents(conn, horizon_days=30, today=today)

    assert [r["doc_id"] for r in out] == [doc_id]
    assert out[0]["validity_to"] == dt.date(2026, 8, 1)
    assert out[0]["basis"] == "staleness"
    assert out[0]["inherited"] is False


def test_a_recent_declaration_is_not_yet_due_for_review(conn):
    from app.handlers.report import expiring_documents

    _insert_document(conn, validity_to=None, validity_from=dt.date(2025, 8, 1))

    assert expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23)) == []


def test_a_declaration_with_no_issue_date_has_nothing_to_count_from(conn):
    """The only DoC that stays out of the loop entirely: no expiry, no cited
    certificate, no issue date. Inventing a date for it would be inventing the
    evidence too."""
    from app.handlers.report import expiring_documents

    _insert_document(conn, validity_to=None, validity_from=None)

    assert expiring_documents(conn, horizon_days=3650, today=dt.date(2026, 7, 23)) == []


def test_the_review_horizon_fires_even_when_the_document_states_a_later_expiry(conn):
    """Both boxes were ticked on 2026-08-18 -- follow the certificate AND
    "osvežite vsako izjavo, starejšo od 5 let" -- so the two rules both apply
    and the EARLIER one fires. "Vsako izjavo" is every declaration, including
    one that dates itself far into the future."""
    from app.handlers.report import expiring_documents

    doc = _insert_document(conn, validity_from=dt.date(2019, 1, 1),
                           validity_to=dt.date(2030, 1, 1))

    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 7, 23))

    assert [r["doc_id"] for r in out] == [doc]
    assert out[0]["validity_to"] == dt.date(2024, 1, 1), "issue + 5 years, not 2030"
    assert out[0]["basis"] == "staleness"


def test_a_stated_expiry_wins_when_it_is_the_earlier_of_the_two(conn):
    from app.handlers.report import expiring_documents

    doc = _insert_document(conn, validity_from=dt.date(2024, 1, 1),
                           validity_to=dt.date(2026, 8, 1))

    out = expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23))

    assert out[0]["doc_id"] == doc
    assert out[0]["validity_to"] == dt.date(2026, 8, 1)
    assert out[0]["basis"] == "stated"


def test_a_five_year_old_declaration_is_due_even_while_its_certificate_runs(conn):
    """The certificate is still valid; the declaration is five years old. What
    needs refreshing is the declaration."""
    from app.handlers.report import expiring_documents

    cert = _insert_document(conn, type_="EC", validity_to=dt.date(2029, 1, 1))
    doc = _insert_document(conn, validity_to=None, validity_from=dt.date(2019, 1, 1),
                           cert_doc_id=cert)

    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 7, 23))

    assert [r["doc_id"] for r in out] == [doc]
    assert out[0]["validity_to"] == dt.date(2024, 1, 1)
    assert out[0]["basis"] == "staleness"
    assert cert not in [r["doc_id"] for r in out], "the cert itself is not due yet"


def test_a_certificate_lapsing_before_the_review_date_is_what_fires(conn):
    """The other direction: a fresh declaration citing a certificate that
    lapses next month is due next month, not in five years."""
    from app.handlers.report import expiring_documents

    cert = _insert_document(conn, type_="EC", validity_to=dt.date(2026, 8, 1))
    doc = _insert_document(conn, validity_to=None, validity_from=dt.date(2025, 1, 1),
                           cert_doc_id=cert)

    out = expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23))

    by_id = {r["doc_id"]: r for r in out}
    assert set(by_id) == {cert, doc}
    assert by_id[doc]["validity_to"] == dt.date(2026, 8, 1)
    assert by_id[doc]["basis"] == "inherited"


def test_a_rejected_certificate_lends_nothing_and_the_review_horizon_applies(conn):
    """Ruled 2026-08-14: a rejected certificate must not lend out its expiry. What was
    previously left with no date at all now falls through to the review
    horizon, which is the point of having one."""
    from app.handlers.report import expiring_documents

    cert = _insert_document(conn, type_="EC", status="rejected",
                            validity_to=dt.date(2029, 1, 1))
    doc = _insert_document(conn, validity_to=None, validity_from=dt.date(2021, 1, 1),
                           cert_doc_id=cert)

    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 7, 23))

    assert [r["doc_id"] for r in out] == [doc]
    assert out[0]["basis"] == "staleness"
    assert out[0]["validity_to"] == dt.date(2026, 1, 1)


def test_a_certificate_without_an_expiry_is_never_given_a_synthetic_one(conn):
    """Article 56 makes an expiry mandatory on a notified-body certificate, so
    a certificate without one is a data defect and must surface as such — not
    be papered over with issue + 5 years, which would hide it forever."""
    from app.handlers.report import expiring_documents

    for type_ in ("EC", "ISO"):
        _insert_document(conn, type_=type_, validity_to=None,
                         validity_from=dt.date(2015, 1, 1))

    assert expiring_documents(conn, horizon_days=3650, today=dt.date(2026, 7, 23)) == []


def test_a_declaration_already_past_its_review_date_is_listed_with_days_overdue(conn):
    from app.handlers.report import expiring_documents

    doc = _insert_document(conn, validity_to=None, validity_from=dt.date(2018, 6, 1))

    out = expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23))

    assert [r["doc_id"] for r in out] == [doc]
    assert out[0]["validity_to"] == dt.date(2023, 6, 1)


def test_the_basis_of_a_stated_expiry_says_stated(conn):
    from app.handlers.report import expiring_documents

    _insert_document(conn, validity_to=dt.date(2026, 8, 1))

    out = expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23))

    assert out[0]["basis"] == "stated"


def test_ordering_is_by_effective_date_across_bases(conn):
    from app.handlers.report import expiring_documents

    late = _insert_document(conn, validity_to=dt.date(2026, 8, 10))
    early = _insert_document(conn, validity_to=None,
                             validity_from=dt.date(2021, 7, 25))  # +5y = 2026-07-25

    out = expiring_documents(conn, horizon_days=30, today=dt.date(2026, 7, 23))

    assert [r["doc_id"] for r in out] == [early, late]


# --------------------------------------------------------------------------- #
# job_counts_by_type_status / dead_job_count
# --------------------------------------------------------------------------- #
def test_job_counts_by_type_status(conn):
    from app.handlers.report import job_counts_by_type_status

    queue.enqueue(conn, "ingest.run", {}, "rep-1")
    queue.enqueue(conn, "ingest.run", {}, "rep-2")
    queue.enqueue(conn, "resolve.group", {}, "rep-3")

    counts = job_counts_by_type_status(conn)
    by_key = {(c["type"], c["status"]): c["count"] for c in counts}

    assert by_key[("ingest.run", "pending")] == 2
    assert by_key[("resolve.group", "pending")] == 1


def test_dead_job_count(conn):
    from app.handlers.report import dead_job_count

    jid = queue.enqueue(conn, "ingest.run", {}, "rep-dead-1", max_attempts=1)
    conn.execute("UPDATE job SET status='dead' WHERE id=%s", (jid,))

    assert dead_job_count(conn) == 1


# --------------------------------------------------------------------------- #
# failure_spikes — shared by SCHEDULER's failure-monitor tick
# --------------------------------------------------------------------------- #
def _seed_discovery_log(conn, manufacturer, outcomes):
    row = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (manufacturer,),
    ).fetchone()
    group_id = row["group_id"]
    for outcome in outcomes:
        conn.execute(
            "INSERT INTO discovery_log (group_id, source, outcome) VALUES (%s, 'search', %s)",
            (group_id, outcome),
        )


def test_failure_spikes_above_threshold_and_sample_floor(conn):
    from app.handlers.report import failure_spikes

    _seed_discovery_log(conn, "ACME", ["miss"] * 4 + ["hit"] * 1)  # 80% miss, 5 samples

    spikes = failure_spikes(
        conn,
        window_days=30,
        miss_rate_threshold=0.5,
        min_sample=5,
        now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc),
    )

    assert any(s["manufacturer"] == "ACME" for s in spikes)


def test_failure_spikes_below_sample_floor_excluded(conn):
    from app.handlers.report import failure_spikes

    _seed_discovery_log(conn, "SMALLCO", ["miss", "miss"])  # 100% miss, only 2 samples

    spikes = failure_spikes(
        conn,
        window_days=30,
        miss_rate_threshold=0.5,
        min_sample=5,
        now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc),
    )

    assert spikes == []


def test_failure_spikes_below_threshold_excluded(conn):
    from app.handlers.report import failure_spikes

    _seed_discovery_log(conn, "GOODCO", ["miss"] * 1 + ["hit"] * 9)  # 10% miss, 10 samples

    spikes = failure_spikes(
        conn,
        window_days=30,
        miss_rate_threshold=0.5,
        min_sample=5,
        now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc),
    )

    assert spikes == []


# --------------------------------------------------------------------------- #
# handle_report_weekly — the queue-dispatched handler
# --------------------------------------------------------------------------- #
def test_handle_report_weekly_returns_snapshot(conn):
    """Relative to the real today, not a frozen date.

    `handle_report_weekly` reads `dt.date.today()` (only `expiring_documents`
    takes an injectable one), so a hardcoded 2026-07-23 + 5 days silently
    became a date 21 days in the PAST — the document was lapsed, not expiring,
    and the assertion only survived because the two were conflated.
    """
    from app.handlers.report import handle_report_weekly

    doc_id = _insert_document(
        conn, validity_to=dt.date.today() + dt.timedelta(days=5)
    )
    jid = queue.enqueue(conn, "report.weekly", {"period_key": "2026-W30"}, "report:2026-W30")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()

    result = handle_report_weekly(conn, job)

    assert result["period_key"] == "2026-W30"
    assert doc_id in [d["doc_id"] for d in result["expiring"]]
    assert "job_counts" in result
    assert "dead_jobs" in result


def test_handle_report_weekly_counts_lapsed_apart_from_expiring(conn):
    """"61 expiring within 30 days" was true of nothing.

    Measured on the live registry 2026-08-18: the scheduler's 30-day scan
    returned 61 production documents and every one had already lapsed, the
    oldest by 2237 days. The rows belong in the scan — a lapsed certificate
    needs chasing more than one lapsing next month — but calling them upcoming
    is how a manufacturer gets told their 2020 certificate is "about to
    expire".
    """
    from app.handlers.report import handle_report_weekly

    upcoming = _insert_document(conn, validity_to=dt.date.today() + dt.timedelta(days=5))
    gone = _insert_document(conn, validity_to=dt.date.today() - dt.timedelta(days=800))
    jid = queue.enqueue(conn, "report.weekly", {"period_key": "2026-W31"}, "report:2026-W31")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()

    result = handle_report_weekly(conn, job)

    assert upcoming in [d["doc_id"] for d in result["expiring"]]
    assert upcoming not in [d["doc_id"] for d in result["lapsed"]]
    # the lapsed one is reported, not dropped — it is still work
    assert gone in [d["doc_id"] for d in result["lapsed"]]
    assert gone not in [d["doc_id"] for d in result["expiring"]]


def test_report_weekly_dispatches_through_real_runner(conn):
    from app.workers.runner import run_once

    import app.handlers.report  # noqa: F401 - registers the real handler

    queue.enqueue(conn, "report.weekly", {"period_key": "2026-W30"}, "report:2026-W30")

    assert run_once(conn, "w") is True
    row = conn.execute(
        "SELECT status FROM job WHERE dedupe_key='report:2026-W30'"
    ).fetchone()
    assert row["status"] == "done"


def test_a_rejected_certificate_does_not_lend_its_expiry(conn):
    """A DoC's renewal date comes from the certificate it cites. The forward
    path (`validate._resolve_cited_certificate`) restricts that to
    production/superseded, but this read followed `cert_doc_id` unfiltered --
    so a certificate a human REJECTED still set the renewal date that drives
    the weekly report and the scheduler's expiry scan.

    Reachable on the normal path, not a corner case: an EC certificate with no
    REF list gets `no-item-identifier` and lands staged by default, and
    rejecting it is exactly what a reviewer does when a notified body withdraws
    it. Inheriting a withdrawn certificate's date is worse than having none."""
    from app.handlers.report import expiring_documents

    cert = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','C-9',"
        "%s,'rejected','h-c9','/archive/c9.pdf','group') RETURNING doc_id",
        (dt.date(2026, 9, 1),),
    ).fetchone()["doc_id"]
    doc = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES ('DoC','MDR',"
        "'C-9', NULL, 'production','h-d9','/archive/d9.pdf','group',%s) RETURNING doc_id",
        (cert,),
    ).fetchone()["doc_id"]

    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 8, 6))
    assert doc not in {r["doc_id"] for r in out}, (
        "a DoC must not inherit a renewal date from a rejected certificate")


def test_a_superseded_certificate_still_lends_its_expiry(conn):
    """The other half, and the reason the filter is not simply `= production`:
    a superseded certificate was real and its dates still describe the period
    it covered. `_resolve_cited_certificate` accepts both, and this read must
    agree with it or the two disagree about the same document."""
    from app.handlers.report import expiring_documents

    cert = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','C-10',"
        "%s,'superseded','h-c10','/archive/c10.pdf','group') RETURNING doc_id",
        (dt.date(2026, 9, 1),),
    ).fetchone()["doc_id"]
    doc = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES ('DoC','MDR',"
        "'C-10', NULL, 'production','h-d10','/archive/d10.pdf','group',%s) RETURNING doc_id",
        (cert,),
    ).fetchone()["doc_id"]

    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 8, 6))
    assert doc in {r["doc_id"] for r in out}


def test_handle_report_weekly_result_survives_the_queue(conn):
    """The gap that let a stage which can never complete ship green.

    Both tests above call the handler and inspect the dict it returns. Neither
    put that dict through `queue.finish`, which is the only thing the worker
    actually does with it -- so `report.weekly` passed its tests while being
    incapable of finishing a single job. The registry held 85 production
    documents with an effective expiry on 2026-08-21 and the job dead-lettered
    on `Object of type date is not JSON serializable`.

    Assert on the row Postgres stores, not on the handler's return value.
    """
    from app.handlers.report import handle_report_weekly

    _insert_document(conn, validity_to=dt.date.today() - dt.timedelta(days=800))
    _insert_document(conn, validity_to=dt.date.today() + dt.timedelta(days=5))
    jid = queue.enqueue(conn, "report.weekly", {"period_key": "2026-W34"}, "report:2026-W34")
    queue.claim(conn, "w-report")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()

    queue.finish(conn, jid, result=handle_report_weekly(conn, job))

    row = conn.execute("SELECT status, result FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["status"] == "done"
    assert row["result"]["period_key"] == "2026-W34"
    # the date came back as a string, and still says which day
    assert row["result"]["lapsed"][0]["validity_to"] == str(
        dt.date.today() - dt.timedelta(days=800)
    )


# --------------------------------------------------------------------------- #
# the HTML copy (2026-09-03) — the report has always existed and has always
# lived in `job.result`, where last week's was readable at /scheduler and the
# week before's was readable nowhere.
# --------------------------------------------------------------------------- #
def test_the_report_writes_an_html_copy_when_a_directory_is_configured(tmp_path):
    """The row dict carries the keys `expiring_documents` returns --
    `manufacturer` and `validity_to`. This test used to feed `expires`, the
    same wrong key the template read, which is how both columns shipped empty
    on every row of every report (W36 and W37: 86 of 86) while it passed."""
    from app.handlers.report import _write_html

    path = _write_html(str(tmp_path), {
        "period_key": "2026-W36",
        "expiring": [{"doc_id": 7, "type": "DoC", "manufacturer": "IVOCLAR",
                      "validity_to": dt.date(2026, 10, 1)}],
        "lapsed": [],
        "dead_jobs": 3,
    })

    assert path is not None
    body = (tmp_path / "report-2026-W36.html").read_text()
    assert "2026-W36" in body
    assert "IVOCLAR" in body
    assert "2026-10-01" in body
    assert 'href="/documents/7"' in body


def _report_rows(body: str) -> list[list[str]]:
    """Every data row of a written report: its cell texts (tags removed), then
    the row's raw HTML as the last element, for asserting on the link."""
    import re

    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cells:
            out.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells] + [row])
    return out


def _run_report(conn, tmp_path, period_key="2026-W37") -> str:
    """The handler on the real query, then its HTML. The test container sets no
    `scheduler.report_dir`, so the handler writes nothing itself."""
    from app.handlers.report import _write_html, handle_report_weekly

    jid = queue.enqueue(conn, "report.weekly", {"period_key": period_key},
                        f"report:{period_key}")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()
    _write_html(str(tmp_path), handle_report_weekly(conn, job))
    return (tmp_path / f"report-{period_key}.html").read_text()


def test_a_report_row_names_the_manufacturer_and_the_date_and_links_the_document(
        conn, tmp_path):
    """`[weekly-report-columns-always-empty]`. Seeded through the real query,
    not a hand-built dict: the bug lived between the two."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('IVOCLAR') "
                 "ON CONFLICT DO NOTHING")
    expires = dt.date.today() + dt.timedelta(days=10)
    lapsed_on = dt.date.today() - dt.timedelta(days=40)
    upcoming = _insert_document(conn, validity_to=expires)
    gone = _insert_document(conn, validity_to=lapsed_on)
    conn.execute("UPDATE document SET canonical_manufacturer='IVOCLAR' "
                 "WHERE doc_id = ANY(%s)", ([upcoming, gone],))

    body = _run_report(conn, tmp_path)

    rows = _report_rows(body)
    for doc_id, date in ((upcoming, expires), (gone, lapsed_on)):
        row = next(r for r in rows if f'href="/documents/{doc_id}"' in r[-1])
        _document, _type, manufacturer, when = row[:4]
        assert manufacturer == "IVOCLAR"
        assert when == str(date)


def test_a_report_row_without_a_confirmed_manufacturer_takes_its_items(conn, tmp_path):
    """`document.canonical_manufacturer` is null until someone confirms it
    (migration 053: null means undecided). Such a document is still reached
    through the items it covers, the path /expiry and the renewal chase use."""
    doc_id = _insert_document(conn, validity_to=dt.date.today() + dt.timedelta(days=5))
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES ('REP-ITEM-1','w','077',TRUE,'LJ',now())")
    gid = conn.execute("INSERT INTO item_group (canonical_manufacturer) "
                       "VALUES ('KOMET') RETURNING group_id").fetchone()["group_id"]
    conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                 "VALUES (%s,'REP-ITEM-1','manual')", (gid,))
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('REP-ITEM-1', %s, 'ref-list', 'production')", (doc_id,))

    body = _run_report(conn, tmp_path)

    row = next(r for r in _report_rows(body) if f'href="/documents/{doc_id}"' in r[-1])
    assert row[2] == "KOMET"


def test_a_report_row_with_no_manufacturer_at_all_says_so(conn, tmp_path):
    doc_id = _insert_document(conn, validity_to=dt.date.today() + dt.timedelta(days=5))

    body = _run_report(conn, tmp_path)

    row = next(r for r in _report_rows(body) if f'href="/documents/{doc_id}"' in r[-1])
    assert row[2] == "not known"


def test_the_report_states_its_window_and_words_the_machine_line_plainly(tmp_path):
    """D7: the forward window is named. The machine line reads as a sentence
    an office reader can take in, not "0 expiring, 86 already lapsed, 81 dead
    jobs"."""
    from app.handlers.report import _write_html

    _write_html(str(tmp_path), {
        "period_key": "2026-W38", "window_days": 30,
        "expiring": [], "lapsed": [], "dead_jobs": 81,
    })
    body = (tmp_path / "report-2026-W38.html").read_text()

    assert "Expiring in the next 30 days (0)" in body
    assert "Already expired (0)" in body
    assert "dead jobs" not in body
    assert "81 failed tasks" in body


def test_no_directory_configured_writes_nothing_and_does_not_fail(tmp_path):
    """The same inert-until-configured shape `ingest_watch_dir` uses: a machine
    that has not been told where to put reports must not fail a cron over it."""
    from app.handlers.report import _write_html

    assert _write_html("", {"period_key": "2026-W36", "expiring": [],
                            "lapsed": [], "dead_jobs": 0}) is None
    assert list(tmp_path.iterdir()) == []


def test_document_names_from_pdfs_we_did_not_write_are_escaped(tmp_path):
    """Every interpolated value is somebody else's text."""
    from app.handlers.report import _write_html

    _write_html(str(tmp_path), {
        "period_key": "2026-W37",
        "expiring": [],
        "lapsed": [{"doc_id": 1, "type": "DoC",
                    "manufacturer": "<script>alert(1)</script>",
                    "validity_to": dt.date(2020, 1, 1)}],
        "dead_jobs": 0,
    })

    body = (tmp_path / "report-2026-W37.html").read_text()
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
