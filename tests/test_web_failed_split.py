"""The Failed split and operator identity (office UI redesign spec § 4, § 2, D6).

`/dead` used to be one list of error groups written for a developer. It now
opens with three sections sorted by what a person can do about each failure:
websites that took too long (try again), addresses that no longer work (search
again), and faults only a developer can fix.

The classifier's table is drawn from the errors standing on the dev database on
2026-09-11 (87 dead jobs), in the exact shape `app/workers/runner.py` writes
them: `f"{type(exc).__name__}: {exc}"`. `tests/fixtures/seed_ui.py` writes its
seeded error module-qualified (`httpx.ConnectError: ...`), so both shapes are
pinned.

The subtle requirement is "retried work stops counting": a dead job whose work
has been queued again drops out of the totals, while its dead row stays exactly
as it was. `dead` is terminal: every `UPDATE job` lives in app/queue.py and
none of them selects a dead row, so nothing moves a job out of it.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web.access import is_operator
from web.app import create_app
from web.failures import classify_failure, failed_counts, failed_split, timeout_count


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _client_with_operators(tmp_path, operator_users):
    """Behind the proxy, the way compose runs it: the login arrives in
    `X-Forwarded-User` (tests/test_web.py's G3 v0 cases use the same shape)."""
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True, operator_users=operator_users)
    return TestClient(create_app(cfg))


def _fetch_payload(url: str, group_id: int | None, domain: str | None = None) -> dict:
    """The payload `discover._emit_fetch` writes."""
    host = domain or re.sub(r"^https?://([^/]+).*$", r"\1", url)
    payload = {"url": url, "domain": host, "source_rank": "search"}
    if group_id is not None:
        payload["group_id"] = group_id
    return payload


def _dead(conn, *, error: str | None, key: str, job_type: str = "fetch.url",
          payload: dict | None = None, finished_at: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority, attempts, "
        "                 max_attempts, last_error, finished_at) "
        "VALUES (%s::job_type, %s, %s, 'dead', 'sweep', 5, 5, %s, "
        "        coalesce(%s::timestamptz, now())) RETURNING id",
        (job_type, Json(payload or {}), key, error, finished_at),
    ).fetchone()["id"]


def _job(conn, *, job_type: str, key: str, status: str, payload: dict | None = None,
         created_at: str | None = None) -> int:
    """Any other job row: the newer work a dead job may or may not have."""
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority, created_at) "
        "VALUES (%s::job_type, %s, %s, %s::job_status, 'interactive', "
        "        coalesce(%s::timestamptz, now())) RETURNING id",
        (job_type, Json(payload or {}), key, status, created_at),
    ).fetchone()["id"]


def _timeout(conn, url: str, group_id: int = 11, error="ConnectTimeout: timed out") -> int:
    return _dead(conn, error=error, key=f"fetch:{url}", payload=_fetch_payload(url, group_id))


def _gone(conn, url: str, group_id: int = 21, status: int = 404) -> int:
    return _dead(conn, error=f"FetchError: unexpected status {status} for {url}",
                 key=f"fetch:{url}", payload=_fetch_payload(url, group_id))


def _no_host(conn, url: str, group_id: int = 21) -> int:
    return _dead(conn, error="ConnectError: [Errno -2] Name or service not known",
                 key=f"fetch:{url}", payload=_fetch_payload(url, group_id))


def _developer(conn, key: str = "extract:abc", error="KeyError: 'archive_url'") -> int:
    return _dead(conn, job_type="extract.doc", error=error, key=key,
                 payload={"content_hash": key})


# --------------------------------------------------------------------------- #
# 1. The classifier, from the live patterns
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("job_type,error,expected", [
    # Timeouts on the three types that contact supplier websites.
    ("fetch.url", "ConnectTimeout: timed out", "timeout"),
    ("fetch.url", "ConnectTimeout: _ssl.c:983: The handshake operation timed out", "timeout"),
    ("fetch.url", "ReadTimeout: The read operation timed out", "timeout"),
    ("fetch.url", "TimeoutError: Page.goto: Timeout 30000ms exceeded.\nCall log:\n"
                  '  - navigating to "https://device.report/m/ec5fdfbb399d0c98", '
                  'waiting until "networkidle"', "timeout"),
    ("fetch.url", "httpx.ConnectTimeout: timed out", "timeout"),
    ("fetch.url", "Error: net::ERR_TIMED_OUT at https://example.com/a.pdf", "timeout"),
    # EAI_AGAIN is the resolver timing out, not a name that does not exist.
    ("fetch.url", "ConnectError: [Errno -3] Temporary failure in name resolution", "timeout"),
    ("discover.group", "ConnectTimeout: timed out", "timeout"),
    ("playbook.reonboard", "ReadTimeout: The read operation timed out", "timeout"),
    # A timeout on a type that never contacts a supplier website.
    ("extract.doc", "TimeoutError: read timed out", "developer"),
    ("eudamed.sweep", "ReadTimeout: The read operation timed out", "developer"),
    # Addresses that no longer work: 404 / 410, or a host name that does not resolve.
    ("fetch.url", "FetchError: unexpected status 404 for https://www.bego.com/media-library/"
                  "downloadcenter/declarations-of-conformity/", "dead-address"),
    ("fetch.url", "FetchError: unexpected status 410 for https://example.com/old.pdf",
     "dead-address"),
    ("fetch.url", "FetchError: bot-wall persists for https://example.com/x.pdf "
                  "(httpx 403, playwright 404)", "dead-address"),
    ("fetch.url", "ConnectError: [Errno -2] Name or service not known", "dead-address"),
    ("fetch.url", "httpx.ConnectError: [Errno -2] Name or service not known", "dead-address"),
    ("fetch.url", "ConnectError: [Errno -5] No address associated with hostname",
     "dead-address"),
    ("fetch.url", "Error: net::ERR_NAME_NOT_RESOLVED at https://gone.example/a.pdf",
     "dead-address"),
    # A 404 on another type, and fetch faults that are neither.
    ("discover.group", "FetchError: unexpected status 404 for https://example.com/", "developer"),
    ("fetch.url", "FetchError: unexpected status 500 for https://example.com/a.pdf", "developer"),
    ("fetch.url", "FetchError: bot-wall persists for https://www.dentalworldofficial.com/"
                  "product/premier-dental-rc-prep-edta/ (httpx 403, playwright 403)", "developer"),
    ("fetch.url", "ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                  "certificate has expired (_ssl.c:1000)", "developer"),
    ("fetch.url", "UnicodeError: label empty or too long", "developer"),
    ("fetch.url", "Error: Page.goto: Download is starting\nCall log:\n"
                  '  - navigating to "https://manuals.plus/m/4a107c9c.pdf"', "developer"),
    # Everything else.
    ("extract.doc", "KeyError: 'archive_url'", "developer"),
    ("extract.doc", "Traceback (most recent call last):\n"
                    '  File "/app/app/handlers/extract.py", line 88, in _dispatch\n'
                    "NameError: name 'log' is not defined", "developer"),
    ("gate.candidate", 'CheckViolation: new row for relation "document" violates check '
                       'constraint "document_coverage_scope_vocabulary"', "developer"),
    ("discover.group", "stopped by hand 2026-09-03: crawl re-fetch loop", "developer"),
    ("fetch.url", "", "developer"),
    ("fetch.url", None, "developer"),
])
def test_classify_failure(job_type, error, expected):
    assert classify_failure(job_type, error) == expected


# --------------------------------------------------------------------------- #
# 2. Three sections, in order, with their counts
# --------------------------------------------------------------------------- #

def test_dead_board_has_three_sections_in_order_with_counts(client, conn):
    _timeout(conn, "https://static.giacomini.com/R140.pdf")
    _timeout(conn, "https://icde-fr.ivoclar.com/c.pdf", error="ReadTimeout: The read operation timed out")
    _gone(conn, "https://www.bego.com/doc/")
    _gone(conn, "https://www.kavo.com/old.pdf", status=410)
    _no_host(conn, "https://archive.amanngirrbach.com/en/")
    _developer(conn)
    conn.commit()

    text = client.get("/dead").text
    heads = ["Websites that took too long (2)",
             "Addresses that no longer work (3)",
             "For the developer (1)"]
    positions = [text.find(h) for h in heads]
    assert -1 not in positions, [h for h, p in zip(heads, positions) if p == -1]
    assert positions == sorted(positions)

    assert "Usually temporary. Trying again often works." in text
    assert "Try the 2 again" in text
    assert "Search again for these 3" in text
    # the per-cause developer groups sit one click down
    assert "Show technical details" in text


def test_dead_board_with_nothing_failed_still_names_the_three_sections(client, conn):
    text = client.get("/dead").text
    for head in ("Websites that took too long (0)", "Addresses that no longer work (0)",
                 "For the developer (0)"):
        assert head in text
    # no count, no button: a button that does nothing is noise
    assert 'hx-post="/dead/retry-timeouts"' not in text
    assert 'hx-post="/dead/search-again"' not in text


def test_dead_addresses_are_sub_grouped_by_kind_with_their_domains(client, conn):
    for i, host in enumerate(["a.example", "b.example", "c.example", "d.example",
                              "e.example", "f.example"]):
        _gone(conn, f"https://{host}/doc-{i}.pdf", group_id=30 + i)
    _no_host(conn, "https://archive.amanngirrbach.com/en/", group_id=40)
    conn.commit()

    section = failed_split(conn)["dead-address"]
    assert section["n"] == 7
    kinds = {k["label"]: k for k in section["kinds"]}
    assert kinds["Page no longer exists"]["n"] == 6
    assert len(kinds["Page no longer exists"]["domains"]) == 6
    assert kinds["Website not found"]["n"] == 1
    assert kinds["Website not found"]["domains"] == ["archive.amanngirrbach.com"]
    assert section["group_ids"] == [30, 31, 32, 33, 34, 35, 40]

    text = client.get("/dead").text
    assert "Page no longer exists (6)" in text
    assert "Website not found (1)" in text
    # the first four domains, then how many more
    assert "+2 more" in text


# --------------------------------------------------------------------------- #
# 3. Operators (D6)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("operator_users,user,sees_rerun", [
    (("denis",), "office1", False),
    (("denis",), "denis", True),
    # An empty list means every login is an operator (spec § 2), so today's
    # single shared login keeps every control it has.
    ((), "office1", True),
])
def test_developer_rerun_controls_are_for_operators(tmp_path, conn, operator_users, user,
                                                    sees_rerun):
    _developer(conn)
    _timeout(conn, "https://static.giacomini.com/R140.pdf")
    conn.commit()

    client = _client_with_operators(tmp_path, operator_users)
    resp = client.get("/dead", headers={"X-Forwarded-User": user})
    assert resp.status_code == 200
    text = resp.text
    assert ('hx-post="/dead/rerun-group"' in text) is sees_rerun
    assert (re.search(r'hx-post="/dead/\d+/rerun"', text) is not None) is sees_rerun
    # the developer section itself is there for everyone, it just has no buttons
    assert "For the developer (1)" in text
    # D6: office logins keep "took too long"
    assert 'hx-post="/dead/retry-timeouts"' in text


# --------------------------------------------------------------------------- #
# 4. Try the timeouts again
# --------------------------------------------------------------------------- #

def test_retry_timeouts_requeues_only_the_timeouts_and_they_stop_counting(client, conn):
    t1 = _timeout(conn, "https://static.giacomini.com/R140.pdf")
    t2 = _timeout(conn, "https://icde-fr.ivoclar.com/c.pdf",
                  error="ReadTimeout: The read operation timed out")
    gone = _gone(conn, "https://www.bego.com/doc/")
    dev = _developer(conn)
    conn.commit()
    assert failed_counts(conn) == {"timeout": 2, "dead-address": 1, "developer": 1}
    dead_before = {r["id"]: r for r in conn.execute(
        "SELECT id, type::text AS type, payload, dedupe_key FROM job").fetchall()}
    top = max(dead_before)

    resp = client.post("/dead/retry-timeouts")
    assert resp.status_code == 200
    assert "Trying the 2 again" in resp.text

    new = conn.execute(
        "SELECT type::text AS type, payload, dedupe_key, priority::text AS priority, "
        "       status::text AS status FROM job WHERE id > %s ORDER BY id", (top,)
    ).fetchall()
    assert sorted(r["dedupe_key"] for r in new) == sorted(
        dead_before[i]["dedupe_key"] for i in (t1, t2))
    for r in new:
        original = next(d for d in dead_before.values() if d["dedupe_key"] == r["dedupe_key"])
        assert (r["type"], r["payload"]) == (original["type"], original["payload"])
        assert (r["priority"], r["status"]) == ("interactive", "pending")

    assert failed_counts(conn) == {"timeout": 0, "dead-address": 1, "developer": 1}
    assert failed_split(conn)["timeout"]["trying_again"] == 2
    # the dead rows are never touched
    for jid in (t1, t2, gone, dev):
        assert conn.execute("SELECT status::text AS s FROM job WHERE id=%s",
                            (jid,)).fetchone()["s"] == "dead"

    # the board says so, in words
    assert "2 trying again" in client.get("/dead").text

    # a second click queues nothing and says why, rather than "nothing waiting"
    total = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    resp = client.post("/dead/retry-timeouts")
    assert resp.status_code == 200
    assert "Already in progress: 2 website timeouts are being tried again." in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == total


def test_retry_timeouts_with_nothing_to_retry_writes_nothing(client, conn):
    _gone(conn, "https://www.bego.com/doc/")
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    resp = client.post("/dead/retry-timeouts")
    assert resp.status_code == 200
    assert "Nothing to try again" in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == before


# --------------------------------------------------------------------------- #
# 5. Search again for the dead addresses
# --------------------------------------------------------------------------- #

def test_search_again_queues_one_discover_per_group(client, conn):
    ids = [
        _gone(conn, "https://www.bego.com/doc/", group_id=101),
        _no_host(conn, "https://archive.amanngirrbach.com/en/", group_id=101),
        _gone(conn, "https://www.kavo.com/old.pdf", group_id=102, status=410),
        # a timeout is not a dead address: its group gets no search
        _timeout(conn, "https://static.giacomini.com/R140.pdf", group_id=103),
    ]
    conn.commit()

    resp = client.post("/dead/search-again")
    assert resp.status_code == 200
    assert "Searching again for 3 addresses" in resp.text

    disc = conn.execute(
        "SELECT payload, dedupe_key, priority::text AS priority FROM job "
        "WHERE type='discover.group' ORDER BY dedupe_key").fetchall()
    assert [r["dedupe_key"] for r in disc] == ["discover:failed:101", "discover:failed:102"]
    assert [r["payload"] for r in disc] == [{"group_id": 101}, {"group_id": 102}]
    assert {r["priority"] for r in disc} == {"interactive"}

    # a second click finds nothing still counted (the searches took the
    # addresses out), queues nothing, and says they are already being searched
    total = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    resp = client.post("/dead/search-again")
    assert resp.status_code == 200
    assert "Already in progress: 3 addresses are being searched for again." in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == total

    counts = failed_counts(conn)
    assert counts["dead-address"] == 0
    assert counts["timeout"] == 1
    assert failed_split(conn)["dead-address"]["searching_again"] == 3
    for jid in ids:
        assert conn.execute("SELECT status::text AS s FROM job WHERE id=%s",
                            (jid,)).fetchone()["s"] == "dead"
    assert "3 searching again" in client.get("/dead").text


def test_search_again_names_addresses_it_cannot_search_for(client, conn):
    """A dead address with no item group has nothing to search for. It is
    counted and said, never dropped silently (CLAUDE.md)."""
    _gone(conn, "https://www.bego.com/doc/", group_id=101)
    _gone(conn, "https://orphan.example/x.pdf", group_id=None)
    conn.commit()

    resp = client.post("/dead/search-again")
    assert resp.status_code == 200
    assert "Searching again for 1 address" in resp.text
    assert "1 address is not linked to an item" in resp.text
    assert failed_counts(conn)["dead-address"] == 1


def test_no_search_button_when_no_address_has_an_item(client, conn):
    """With no item group there is nothing to search for, so the button would
    only offer to start 0 searches."""
    _gone(conn, "https://orphan.example/x.pdf", group_id=None)
    conn.commit()

    text = client.get("/dead").text
    assert "Addresses that no longer work (1)" in text
    assert 'hx-post="/dead/search-again"' not in text
    assert "not linked to an item, so there is nothing to search for" in text


def test_search_again_says_which_searches_were_already_running(client, conn):
    """The dedupe path: group 301's `discover:failed:301` is still queued from
    before its address died, so it does not count as searching again (the
    search predates the failure), and enqueueing it again is a no-op."""
    _job(conn, job_type="discover.group", key="discover:failed:301", status="pending",
         payload={"group_id": 301}, created_at="2026-09-10 08:00+00")
    _dead(conn, error="FetchError: unexpected status 404 for https://a.example/x.pdf",
          key="fetch:https://a.example/x.pdf",
          payload=_fetch_payload("https://a.example/x.pdf", 301),
          finished_at="2026-09-10 09:00+00")
    _gone(conn, "https://b.example/y.pdf", group_id=302)
    conn.commit()
    assert failed_split(conn)["dead-address"]["group_ids"] == [301, 302]
    top = conn.execute("SELECT max(id) AS m FROM job").fetchone()["m"]

    resp = client.post("/dead/search-again")
    assert resp.status_code == 200
    assert ("Searching again for 2 addresses: 1 search queued. "
            "1 search was already running.") in resp.text
    new = conn.execute("SELECT dedupe_key FROM job WHERE id > %s", (top,)).fetchall()
    assert [r["dedupe_key"] for r in new] == ["discover:failed:302"]

    # group 302 is now searching again; group 301 still counts, and its only
    # search is the one already queued, so a second click adds nothing
    assert failed_split(conn)["dead-address"]["group_ids"] == [301]
    resp = client.post("/dead/search-again")
    assert "Nothing new queued: 1 search is already running for this address." in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job WHERE id > %s",
                        (top,)).fetchone()["n"] == 1


def test_retry_timeouts_says_when_the_address_is_already_being_fetched(client, conn):
    """`fetch.url` dedupes on the URL alone, so another group's fetch of the
    same address holds the key: nothing can be queued for this group until it
    finishes, and the receipt says so instead of claiming a retry."""
    url = "https://static.giacomini.com/R140.pdf"
    _timeout(conn, url, group_id=11)
    _job(conn, job_type="fetch.url", key=f"fetch:{url}", status="pending",
         payload=_fetch_payload(url, 12))
    conn.commit()
    assert failed_counts(conn)["timeout"] == 1
    total = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    resp = client.post("/dead/retry-timeouts")
    assert resp.status_code == 200
    assert "Nothing new queued: this address is already in the queue." in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == total


# --------------------------------------------------------------------------- #
# Retried work stops counting (spec § 4), directly
# --------------------------------------------------------------------------- #

def test_newer_work_under_the_same_key_stops_a_dead_job_counting(conn):
    # re-run and succeeded: neither counted nor "trying again"
    _developer(conn, key="extract:done")
    _job(conn, job_type="extract.doc", key="extract:done", status="done")
    # re-run and still queued: shown as trying again
    _developer(conn, key="extract:queued")
    _job(conn, job_type="extract.doc", key="extract:queued", status="pending")
    # died twice: only the newer dead row speaks for the work
    _developer(conn, key="extract:twice")
    _developer(conn, key="extract:twice")
    # never re-run
    _developer(conn, key="extract:alone")
    conn.commit()

    section = failed_split(conn)["developer"]
    assert section["n"] == 2
    assert section["trying_again"] == 1
    assert sorted(j["dedupe_key"] for j in section["jobs"]) == ["extract:alone", "extract:twice"]


def test_a_fetch_for_another_group_is_not_newer_work_for_this_one(client, conn):
    """`fetch.url`'s dedupe key is the URL, its payload carries the group. A
    newer fetch of the same URL for ANOTHER group must not settle this
    group's dead row, or this group is never searched again. Live on dev
    2026-09-11: job 36210 (group 2067) and job 37434 (group 2100), both
    `fetch:https://www.bego.com/media-library/downloadcenter/declarations-of-conformity/`.
    """
    url = "https://www.bego.com/media-library/downloadcenter/declarations-of-conformity/"
    _gone(conn, url, group_id=2067)
    _gone(conn, url, group_id=2100)
    conn.commit()

    section = failed_split(conn)["dead-address"]
    assert section["n"] == 2
    assert section["group_ids"] == [2067, 2100]

    assert client.post("/dead/search-again").status_code == 200
    keys = [r["dedupe_key"] for r in conn.execute(
        "SELECT dedupe_key FROM job WHERE type='discover.group' ORDER BY dedupe_key")]
    assert keys == ["discover:failed:2067", "discover:failed:2100"]
    assert failed_counts(conn)["dead-address"] == 0


def test_one_retry_reads_as_one_trying_again(client, conn):
    """A job that died twice has two dead rows under one key. The older one is
    settled by the newer dead row, so one retry of that work reads as one
    "trying again", matching the receipt."""
    url = "https://static.giacomini.com/R140.pdf"
    _timeout(conn, url, group_id=11)
    _timeout(conn, url, group_id=11)
    conn.commit()
    assert failed_counts(conn)["timeout"] == 1

    resp = client.post("/dead/retry-timeouts")
    assert "Trying the 1 again" in resp.text
    section = failed_split(conn)["timeout"]
    assert (section["n"], section["trying_again"]) == (0, 1)
    text = client.get("/dead").text
    assert "1 trying again" in text
    assert "2 trying again" not in text


def test_only_a_search_started_after_the_address_died_counts(conn):
    # searched before it died: the search that found this address, still counted
    _job(conn, job_type="discover.group", key="discover:7:1", status="done",
         payload={"group_id": 7}, created_at="2026-09-10 08:00+00")
    _dead(conn, error="FetchError: unexpected status 404 for https://a.example/x.pdf",
          key="fetch:https://a.example/x.pdf",
          payload=_fetch_payload("https://a.example/x.pdf", 7),
          finished_at="2026-09-10 09:00+00")
    # searched again after it died, search still running: "searching again"
    _dead(conn, error="FetchError: unexpected status 404 for https://b.example/y.pdf",
          key="fetch:https://b.example/y.pdf",
          payload=_fetch_payload("https://b.example/y.pdf", 8),
          finished_at="2026-09-10 09:00+00")
    _job(conn, job_type="discover.group", key="discover:refetch:8:2026-09-10", status="running",
         payload={"group_id": 8}, created_at="2026-09-10 10:00+00")
    # searched again after it died, search finished: settled, not shown
    _dead(conn, error="ConnectError: [Errno -2] Name or service not known",
          key="fetch:https://gone.example/z.pdf",
          payload=_fetch_payload("https://gone.example/z.pdf", 9),
          finished_at="2026-09-10 09:00+00")
    _job(conn, job_type="discover.group", key="discover:failed:9", status="done",
         payload={"group_id": 9}, created_at="2026-09-10 10:00+00")
    conn.commit()

    section = failed_split(conn)["dead-address"]
    assert section["n"] == 1
    assert section["group_ids"] == [7]
    assert section["searching_again"] == 1


def test_rerun_group_reruns_only_what_still_counts(client, conn):
    """The developer group re-derives its members from the split, so a cause
    whose earlier dead row was already re-run is not re-run twice."""
    error = "KeyError: 'archive_url'"
    _developer(conn, key="extract:twice", error=error)
    _developer(conn, key="extract:twice", error=error)
    _developer(conn, key="extract:other", error=error)
    conn.commit()
    top = conn.execute("SELECT max(id) AS m FROM job").fetchone()["m"]

    text = client.get("/dead").text
    assert "Re-run all 2 (interactive priority)" in text
    digest = re.search(r'name="digest" value="([0-9a-f]+)"', text).group(1)

    resp = client.post("/dead/rerun-group", data={"digest": digest})
    assert resp.status_code == 200
    assert "Re-queued 2 jobs" in resp.text
    assert conn.execute("SELECT count(*) AS n FROM job WHERE id > %s",
                        (top,)).fetchone()["n"] == 2
    assert failed_counts(conn)["developer"] == 0


def test_rerun_group_cannot_reach_a_timeout_group(client, conn):
    """Timeouts have their own button. The developer groups are built from the
    developer section only, so no digest selects a timeout."""
    _timeout(conn, "https://static.giacomini.com/R140.pdf")
    conn.commit()
    assert 'name="digest"' not in client.get("/dead").text


# --------------------------------------------------------------------------- #
# 6. Operator identity
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("user,operator_users,expected", [
    (None, (), True),
    ("office1", (), True),
    ("denis", ("denis",), True),
    ("denis", ("admin", "denis"), True),
    ("office1", ("denis",), False),
    (None, ("denis",), False),
    ("", ("denis",), False),
    ("Denis", ("denis",), False),
])
def test_is_operator(user, operator_users, expected):
    assert is_operator(user, operator_users) is expected


# --------------------------------------------------------------------------- #
# The menu's own reading of one class
# --------------------------------------------------------------------------- #

def test_the_menu_timeout_count_is_the_split_timeout_count(conn):
    """`timeout_count` is what the office menu's health line polls every 15
    seconds, and it must be the number `/dead` heads its first section with.

    It reads a narrower query than `failed_split` -- dead rows of the three
    supplier-website types, and the newer work that settles them -- and then
    classifies in Python with the same `classify_failure`. Seeded with one of
    every case the narrowing could get wrong: a timeout that still counts, a
    retried one that does not, a timeout on a type that never contacts a
    supplier, a dead address, and a developer fault.
    """
    _timeout(conn, "https://slow.example/a.pdf")
    _timeout(conn, "https://slower.example/b.pdf")
    retried = _timeout(conn, "https://sent-back.example/c.pdf")
    row = conn.execute("SELECT dedupe_key, payload FROM job WHERE id=%s",
                       (retried,)).fetchone()
    _job(conn, job_type="fetch.url", key=row["dedupe_key"], status="pending",
         payload=row["payload"])
    # a timeout on a type that never reaches a supplier website: developer's
    _dead(conn, job_type="extract.doc", error="TimeoutError: read timed out",
          key="extract:timeout-not-a-website")
    _gone(conn, "https://gone.example/d.pdf")
    _no_host(conn, "https://nowhere.example/e.pdf")
    _developer(conn, key="extract:timeout-agreement")
    conn.commit()

    assert failed_counts(conn)["timeout"] == 2
    assert timeout_count(conn) == failed_counts(conn)["timeout"]


def test_the_menu_timeout_count_on_an_empty_queue(conn):
    assert timeout_count(conn) == 0


# --------------------------------------------------------------------------- #
# Every dead-job counter is the split's count (T4's ruling, carried out)
# --------------------------------------------------------------------------- #
# T4 ruled that every dead-job counter switches to `failures.failed_counts` in
# T7. Only Today did. `/pipeline` and the status board kept
# `count(*) FROM job WHERE status='dead'`, which counts the rows `/dead` shows
# AND the ones it deliberately drops because newer work is already queued for
# them -- a figure labelled "N failed tasks" linking to a page showing fewer
# (rule 7: a number whose definition is not on screen is not shown).
def _one_settled_and_one_counted(conn) -> tuple[int, int]:
    """Two dead jobs, one of them already retried. `/dead` counts one; the raw
    `status='dead'` count is two. Returns (shown, raw)."""
    _timeout(conn, "https://still.example/a.pdf")
    _timeout(conn, "https://retried.example/b.pdf")
    _job(conn, job_type="fetch.url", status="pending",
         key="fetch:https://retried.example/b.pdf",
         payload=_fetch_payload("https://retried.example/b.pdf", 11))
    conn.commit()
    raw = conn.execute(
        "SELECT count(*) AS n FROM job WHERE status='dead'").fetchone()["n"]
    return sum(failed_counts(conn).values()), raw


def test_the_queues_hub_counts_what_the_failed_page_shows(client, conn):
    shown, raw = _one_settled_and_one_counted(conn)
    assert (shown, raw) == (1, 2), "the fixture should make the two differ"

    row = re.search(r'<a href="/dead">.*?</tr>', client.get("/pipeline").text, re.S)
    assert row, "the hub should carry a row for /dead"

    assert f"<strong>{shown}</strong>" in row.group(0)
    assert f"<strong>{raw}</strong>" not in row.group(0)


def test_the_status_board_counts_what_the_failed_page_shows(client, conn):
    shown, raw = _one_settled_and_one_counted(conn)

    text = client.get("/status").text
    tile = re.search(r'<a class="stat-tile[^"]*" href="/dead">.*?</a>', text, re.S)
    assert tile, "the board should carry a Failed tile"
    assert re.search(rf'<div class="n">\s*{shown}\s*</div>', tile.group(0)), tile.group(0)
    assert not re.search(rf'<div class="n">\s*{raw}\s*</div>', tile.group(0))
    # and the table further down, which prints the same figure a second time
    assert re.search(rf'<a href="/dead">[^<]*</a></td><td>{shown}</td>', text)
