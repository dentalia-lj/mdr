"""`playbook.reonboard` — the probe branch (S2.2).

Spec: docs/superpowers/specs/2026-09-03-playbook-probe-wizard-design.md.

Real Postgres (CLAUDE.md: no mocking). The network is faked with an exact-URL
fake, so an unscripted fetch fails loudly rather than reaching out.
"""
from __future__ import annotations

import pytest

from app import queue
from app.adapters.fetcher import FetchResult
from app.handlers import playbook_probe as pp
from app.workers import runner  # noqa: F401 - registers the real handlers


class ScriptedFetcher:
    def __init__(self, responses):
        self._responses = dict(responses)
        self.calls: list[str] = []

    def get(self, url, *, etag=None, last_modified=None):
        self.calls.append(url)
        if url not in self._responses:
            raise AssertionError(f"probe fetched an unscripted URL: {url}")
        return self._responses[url]


def _html(body: bytes):
    return FetchResult(status=200, body=body, content_type="text/html",
                       etag=None, last_modified=None, final_url="x")


def _robots(body=b""):
    return FetchResult(status=404, body=body, content_type="text/plain",
                       etag=None, last_modified=None, final_url="x")


_LISTING = (
    b"<html><body>"
    b"<a href='/docs/a.pdf'>A</a>"
    b"<a href='/docs/sds_b.pdf'>B</a>"
    b"<a href='/docs/ec_doc_c.pdf'>C</a>"
    b"<a href='/about'>About</a>"
    b"</body></html>"
)


def _job(payload, jid=1):
    return {"id": jid, "type": "playbook.reonboard", "payload": payload}


def _row(conn, probe_id):
    return conn.execute(
        "SELECT * FROM playbook_probe WHERE id=%s", (probe_id,)).fetchone()


def test_a_probe_records_what_it_found_and_emits_nothing(conn):
    """The asymmetry the whole design turns on: this is the only consumer of
    app.crawl that produces no fetch.url. An operator must be able to LOOK at a
    library without committing the pipeline to fetch it."""
    f = ScriptedFetcher({
        "https://probe.example/robots.txt": _robots(),
        "https://probe.example/downloads": _html(_LISTING),
    })
    before = conn.execute("SELECT count(*) AS n FROM job WHERE type='fetch.url'").fetchone()["n"]

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "probeco", "index_url": "https://probe.example/downloads",
        "link_pattern": r"\.pdf$", "requested_by": "operator@example.test",
    }), fetcher=f)

    assert res["status"] == "done"
    row = _row(conn, res["probe_id"])
    assert row["counts"]["anchors_seen"] == 4
    assert row["counts"]["links_kept"] == 3
    assert row["requested_by"] == "operator@example.test"
    assert row["tier"] == "static"
    # NOTHING was enqueued.
    after = conn.execute("SELECT count(*) AS n FROM job WHERE type='fetch.url'").fetchone()["n"]
    assert after == before


def test_the_sample_is_bounded_but_the_counts_are_complete(conn):
    """A probe of a 1.329-link index must not put 1.329 of someone else's URLs
    in our database to show an operator twenty."""
    many = b"<html>" + b"".join(
        f"<a href='/d/{i}.pdf'>x</a>".encode() for i in range(60)) + b"</html>"
    f = ScriptedFetcher({
        "https://big.example/robots.txt": _robots(),
        "https://big.example/i": _html(many),
    })
    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "bigco", "index_url": "https://big.example/i",
        "link_pattern": r"\.pdf$"}), fetcher=f)
    row = _row(conn, res["probe_id"])
    assert row["counts"]["links_kept"] == 60
    assert len(row["sample"]) == pp.SAMPLE_LIMIT


def test_classification_is_a_guess_and_null_means_exclude(conn):
    """`doc_type_from` maps a filename substring to a type, and `null` means
    'harvest and count, never claim a type'. Both an unmatched link and an
    excluded one land in the unclassified bucket -- neither asserts anything."""
    f = ScriptedFetcher({
        "https://cls.example/robots.txt": _robots(),
        "https://cls.example/i": _html(_LISTING),
    })
    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "clsco", "index_url": "https://cls.example/i",
        "link_pattern": r"\.pdf$",
        "doc_type_from": {"ec_doc": "DoC", "sds_": None}}), fetcher=f)
    counts = _row(conn, res["probe_id"])["counts"]
    assert counts["by_type"]["DoC"] == 1
    assert counts["by_type"]["unclassified"] == 2


def test_a_robots_disallow_is_a_result_not_an_error(conn):
    f = ScriptedFetcher({
        "https://no.example/robots.txt": FetchResult(
            status=200, body=b"User-agent: *\nDisallow: /",
            content_type="text/plain", etag=None, last_modified=None, final_url="x"),
    })
    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "noco", "index_url": "https://no.example/downloads",
        "link_pattern": r"\.pdf$"}), fetcher=f)
    assert res["status"] == "refused"
    # The index page was never asked for.
    assert f.calls == ["https://no.example/robots.txt"]


def test_a_site_that_will_not_load_is_recorded_not_raised(conn):
    """A manufacturer's site being down is something the operator needs to SEE,
    not a dead-lettered job."""
    class Boom:
        calls: list = []

        def get(self, url, **k):
            if url.endswith("robots.txt"):
                return _robots()
            raise ConnectionError("connection refused")

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "downco", "index_url": "https://down.example/i",
        "link_pattern": r"\.pdf$"}), fetcher=Boom())
    assert res["status"] == "failed"
    assert "ConnectionError" in _row(conn, res["probe_id"])["error"]


def test_a_held_lease_defers_rather_than_jumping_the_queue(conn):
    """A human watching a spinner is not a reason to hit a host we have been
    asked to wait on."""
    conn.execute("INSERT INTO domain_lease (domain, leased_until) "
                 "VALUES ('held.example', now() + interval '1 hour')")
    jid = queue.enqueue(conn, "playbook.reonboard",
                        {"slug": "h", "index_url": "https://held.example/i"},
                        dedupe_key="probe:test")
    f = ScriptedFetcher({"https://held.example/robots.txt": _robots()})
    res = pp.handle_playbook_reonboard(conn, _job(
        {"slug": "h", "index_url": "https://held.example/i",
         "link_pattern": r"\.pdf$"}, jid=jid), fetcher=f)
    assert res.get("_deferred") is True
    assert _row(conn, res["probe_id"])["status"] == "pending"


class ScriptedRenderer:
    """`.render(url)` only -- the same shape `discover._crawl_playbook` uses.
    An unscripted render is an AssertionError, so a test that did not expect
    the browser fails loudly instead of silently passing."""

    def __init__(self, responses):
        self._responses = dict(responses)
        self.calls: list[str] = []

    def render(self, url):
        self.calls.append(url)
        if url not in self._responses:
            raise AssertionError(f"probe rendered an unscripted URL: {url}")
        return self._responses[url]


_SHELL = b"<html><body><div id='app'></div><script src='/app.js'></script></body></html>"


def test_a_page_with_no_anchors_escalates_to_the_browser(conn):
    """The case `playbook_probe.tier` exists for. A JavaScript-drawn library is
    an empty shell to a plain request, and until 2026-09-04 the probe hardcoded
    `tier="static"` -- so an operator staring at "0 anchors, 0 kept" had no way
    to learn a browser was the missing thing (`[probe-browser-tier]`)."""
    f = ScriptedFetcher({
        "https://spa.example/robots.txt": _robots(),
        "https://spa.example/library": _html(_SHELL),
    })
    r = ScriptedRenderer({"https://spa.example/library": _LISTING.decode()})

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "spaco", "index_url": "https://spa.example/library",
        "link_pattern": r"\.pdf$"}), fetcher=f, renderer=r)

    row = _row(conn, res["probe_id"])
    assert row["tier"] == "rendered"
    assert row["counts"]["anchors_seen"] == 4
    assert row["counts"]["links_kept"] == 3
    assert r.calls == ["https://spa.example/library"]


def test_anchors_that_matched_nothing_never_reach_the_browser(conn):
    """The trigger is zero ANCHORS, never zero matches -- the same rule the
    crawl rung uses. A page with anchors that matched none of them has a wrong
    `link_pattern`, and a browser renders the same anchors and matches none
    again: 30s spent to reprint the same number. The renderer here would raise
    on any call, so reaching it fails the test."""
    f = ScriptedFetcher({
        "https://probe.example/robots.txt": _robots(),
        "https://probe.example/downloads": _html(_LISTING),
    })
    r = ScriptedRenderer({})

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "wrongpat", "index_url": "https://probe.example/downloads",
        "link_pattern": r"\.docx$"}), fetcher=f, renderer=r)

    row = _row(conn, res["probe_id"])
    assert row["counts"]["anchors_seen"] == 4
    assert row["counts"]["links_kept"] == 0
    assert row["tier"] == "static"
    assert r.calls == []


def test_a_render_that_fails_leaves_the_static_answer_and_says_static(conn):
    """A render fault degrades to the static result; `tier` must NOT claim
    "rendered", which would tell the operator we looked with a browser when we
    did not. And it must never dead-letter the job."""
    class Boom:
        calls: list[str] = []

        def render(self, url):
            Boom.calls.append(url)
            raise RuntimeError("browser died")

    f = ScriptedFetcher({
        "https://spa.example/robots.txt": _robots(),
        "https://spa.example/library": _html(_SHELL),
    })

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "boomco", "index_url": "https://spa.example/library",
        "link_pattern": r"\.pdf$"}), fetcher=f, renderer=Boom())

    assert res["status"] == "done"
    row = _row(conn, res["probe_id"])
    assert row["tier"] == "static"
    assert row["counts"]["anchors_seen"] == 0
    assert Boom.calls == ["https://spa.example/library"]


def test_a_render_that_also_finds_nothing_still_reports_rendered(conn):
    """"We tried a browser and there is still nothing" is a different, and more
    useful, answer than "we never tried"."""
    f = ScriptedFetcher({
        "https://spa.example/robots.txt": _robots(),
        "https://spa.example/library": _html(_SHELL),
    })
    r = ScriptedRenderer({"https://spa.example/library": "<html><body></body></html>"})

    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "emptyspa", "index_url": "https://spa.example/library",
        "link_pattern": r"\.pdf$"}), fetcher=f, renderer=r)

    row = _row(conn, res["probe_id"])
    assert row["tier"] == "rendered"
    assert row["counts"]["links_kept"] == 0


def test_the_failure_monitor_branch_raises_rather_than_reporting_success(conn):
    """A half-built handler that silently no-ops is worse than the placeholder
    it replaced."""
    with pytest.raises(NotImplementedError, match="not built"):
        pp.handle_playbook_reonboard(conn, _job({"manufacturer": "ACME"}))


def test_a_deferred_probe_reuses_its_row_instead_of_leaving_a_second_pending_one(conn):
    """One row per job, not one per attempt.

    The runner COMMITS a deferred handler's writes -- that is what keeps
    `batch_ref` honest -- so a plain INSERT here left a fresh `pending` row
    behind on every politeness deferral. A busy domain turns one probe into a
    handful of orphan rows, and the UI, which polls by `via_job`, would have
    had several to choose between and no rule for picking.
    """
    conn.execute("INSERT INTO domain_lease (domain, leased_until) "
                 "VALUES ('twice.example', now() + interval '1 hour')")
    jid = queue.enqueue(conn, "playbook.reonboard",
                        {"slug": "t", "index_url": "https://twice.example/i"},
                        dedupe_key="probe:twice")
    payload = {"slug": "t", "index_url": "https://twice.example/i",
               "link_pattern": r"\.pdf$"}
    f = ScriptedFetcher({"https://twice.example/robots.txt": _robots()})

    first = pp.handle_playbook_reonboard(conn, _job(payload, jid=jid), fetcher=f)
    second = pp.handle_playbook_reonboard(conn, _job(payload, jid=jid), fetcher=f)

    assert first["probe_id"] == second["probe_id"]
    assert conn.execute(
        "SELECT count(*) AS n FROM playbook_probe WHERE via_job=%s", (jid,)
    ).fetchone()["n"] == 1

    # And once the lease clears, that SAME row is the one that gets the result.
    conn.execute("DELETE FROM domain_lease WHERE domain='twice.example'")
    f._responses["https://twice.example/i"] = _html(_LISTING)
    third = pp.handle_playbook_reonboard(conn, _job(payload, jid=jid), fetcher=f)
    assert third["probe_id"] == first["probe_id"]
    assert _row(conn, first["probe_id"])["status"] == "done"


def test_the_result_the_runner_records_is_json_serialisable(conn):
    """The runner serialises a handler's return value into `job.result`.

    Calling the handler directly, as every other test here does, never
    exercises that -- so a `psycopg` `Json` adapter left in the returned dict
    passed the whole file and failed the first time a real worker ran it. It
    fails AFTER the work is done, which is the expensive shape: the
    transaction rolls back, the probe row vanishes, and the job retries,
    fetching the manufacturer's page again on every attempt. Measured live
    against NSK, job 37728, three real fetches before it was stopped by hand.
    """
    import json

    f = ScriptedFetcher({"https://ser.example/robots.txt": _robots(),
                         "https://ser.example/i": _html(_LISTING)})
    res = pp.handle_playbook_reonboard(conn, _job({
        "slug": "ser", "index_url": "https://ser.example/i",
        "link_pattern": r"\.pdf$", "doc_type_from": {"sds_": None}}), fetcher=f)

    json.dumps(res)                        # the assertion: it does not raise
    assert res["counts"]["links_kept"] == 3
    # The sample stays in the ROW, not in every job result -- it is up to 20 of
    # somebody else's URLs.
    assert "sample" not in res
    assert _row(conn, res["probe_id"])["sample"]
