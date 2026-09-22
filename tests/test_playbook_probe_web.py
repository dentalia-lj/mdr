"""The probe's UI half: enqueue, poll, render, save (S2.2, slices 3 and 4).

Spec: docs/superpowers/specs/2026-09-03-playbook-probe-wizard-design.md §3.3.

What these pin is mostly the SEPARATION. The web process is a producer
(CLAUDE.md invariant 1): it enqueues `playbook.reonboard` and reads back what
the worker wrote, and it must never fetch, never write the registry, and never
save a recipe on its own. A route that quietly did the fetch itself would look
identical on screen and would bypass robots, the politeness lease and the
worker's retry budget all at once -- so "the job was enqueued and nothing else
happened" is the assertion, not an implementation detail.

Real Postgres, no mocks (CLAUDE.md). Nothing here reaches the network: no test
runs a worker.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app import manufacturer_seed
from app import playbooks as pb
from app.config import Web
from tests.conftest import TEST_API_URL
from web import registry
from web.app import create_app

INDEX = "https://alpha.example/library"


@pytest.fixture(autouse=True)
def _clean(conn):
    def _wipe():
        conn.execute("DELETE FROM playbook_probe")
        conn.execute("DELETE FROM job")
        conn.execute("DELETE FROM manufacturer_playbook_revision")
        conn.execute("DELETE FROM manufacturer_name")
        conn.execute("DELETE FROM manufacturer_bc_code")
        conn.execute("DELETE FROM manufacturer")
        conn.commit()

    _wipe()
    pb.set_source(None)
    pb.clear_cache()
    yield
    pb.set_source(None)
    pb.clear_cache()
    _wipe()


@pytest.fixture
def seeded(conn, tmp_path):
    """One playbook, in a test-owned directory and in the database.

    Both halves matter: `playbook_detail` reads the FILE store, the tier-B
    context and the save path read the ROW, and the page is only editable once
    the row exists.
    """
    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "001"}],
        "domains": ["alpha.example"],
    }))
    conn.execute(
        "INSERT INTO vendor_master (code_source, code, name, import_batch) "
        "VALUES ('LJ','001','ALPHA GMBH','probe-test')")
    conn.commit()

    original = pb.load_robots_refused
    pb.load_robots_refused = lambda *a, **k: frozenset()
    try:
        stats = manufacturer_seed.seed(conn, dir_path=tmp_path)
    finally:
        pb.load_robots_refused = original
    assert stats.clean, stats.conflicts
    conn.commit()
    return tmp_path


@pytest.fixture
def client(seeded, tmp_path):
    return TestClient(create_app(Web(
        api_database_url=TEST_API_URL,
        imports_dir=str(tmp_path),
        playbooks_dir=str(seeded))))


def _jobs(conn):
    return conn.execute(
        "SELECT id, type, payload, priority, dedupe_key FROM job ORDER BY id"
    ).fetchall()


def _rev(conn, slug="alpha"):
    return conn.execute(
        "SELECT playbook_rev FROM manufacturer WHERE slug=%s", (slug,)
    ).fetchone()["playbook_rev"]


def _body(conn, slug="alpha"):
    return conn.execute(
        "SELECT body FROM manufacturer WHERE slug=%s", (slug,)).fetchone()["body"]


def _probe_row(conn, job_id, **cols):
    """Stand in for the worker: write the row a probe would have produced."""
    keys = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO playbook_probe (slug, index_url, via_job, {keys}) "
        f"VALUES ('alpha', %s, %s, {marks}) RETURNING id",
        (INDEX, job_id, *cols.values()),
    ).fetchone()["id"]


DONE = dict(
    status="done", robots_verdict="allow", tier="static",
    counts=Json({"anchors_seen": 40, "links_matched": 12, "links_capped": 0,
                 "off_host": 1, "links_kept": 12, "sample_shown": 2,
                 "by_type": {"DoC": 5, "unclassified": 7}}),
    sample=Json([{"url": "https://alpha.example/d/konformitat.pdf",
                  "reads_as": "DoC"},
                 {"url": "https://alpha.example/d/price-list.pdf",
                  "reads_as": None}]),
)


# --- the form ---------------------------------------------------------------

def test_the_detail_page_offers_a_probe_and_lists_existing_recipes(client, conn):
    conn.execute("UPDATE manufacturer SET body = body || %s::jsonb WHERE slug='alpha'",
                 (Json({"crawl": [{"index_url": INDEX,
                                   "link_pattern": r"\.pdf$"}]}),))
    conn.commit()
    resp = client.get("/playbooks/alpha")
    assert resp.status_code == 200
    assert 'id="crawl-probe-form"' in resp.text
    assert "/playbooks/alpha/probe" in resp.text
    assert INDEX in resp.text                      # the recipe already stored


# --- enqueue ----------------------------------------------------------------

def test_a_probe_enqueues_the_job_and_the_web_process_fetches_nothing(client, conn):
    resp = client.post("/playbooks/alpha/probe",
                       data={"index_url": INDEX, "link_pattern": r"\.pdf$",
                             "max_links": "50",
                             "doc_type_from": "konformit=DoC\nsds_="})
    assert resp.status_code == 200

    jobs = _jobs(conn)
    assert [j["type"] for j in jobs] == ["playbook.reonboard"]
    payload = jobs[0]["payload"]
    assert payload["slug"] == "alpha"
    assert payload["index_url"] == INDEX
    assert payload["link_pattern"] == r"\.pdf$"
    assert payload["max_links"] == 50
    # `null` is a MEANING here, not a missing value: it excludes a family from
    # the registry while still counting it.
    assert payload["doc_type_from"] == {"konformit": "DoC", "sds_": None}
    assert payload["requested_by"]
    # Never authored from the form, and never off: the crawl design widens the
    # host set only by naming a host, never by flipping this.
    assert payload["same_host_only"] is True
    # A human is watching, so it is not queued behind the sweep backlog.
    assert jobs[0]["priority"] == "interactive"
    # Nothing was probed, because the web process does not fetch.
    assert conn.execute("SELECT count(*) AS n FROM playbook_probe"
                        ).fetchone()["n"] == 0
    # And it emits nothing else -- no fetch.url, no discover.group.
    assert len(jobs) == 1


def test_a_second_press_joins_the_probe_already_running(client, conn):
    first = client.post("/playbooks/alpha/probe",
                        data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    second = client.post("/playbooks/alpha/probe",
                         data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    assert first.status_code == second.status_code == 200
    jobs = _jobs(conn)
    assert len(jobs) == 1, "a double-click asked the manufacturer's server twice"
    # Both responses poll the SAME job, so the second press shows the first
    # press's result rather than reporting nothing.
    assert f"/playbooks/alpha/probe/{jobs[0]['id']}" in first.text
    assert f"/playbooks/alpha/probe/{jobs[0]['id']}" in second.text


@pytest.mark.parametrize("data, expect", [
    ({"link_pattern": r"\.pdf$"}, "library URL is required"),
    ({"index_url": "alpha.example/x", "link_pattern": r"\.pdf$"}, "absolute"),
    ({"index_url": INDEX, "link_pattern": ""}, "link pattern is required"),
    ({"index_url": INDEX, "link_pattern": "([unclosed"}, "not a valid regex"),
    ({"index_url": INDEX, "link_pattern": r"\.pdf$", "max_links": "99999"},
     "between 1 and"),
    ({"index_url": INDEX, "link_pattern": r"\.pdf$", "doc_type_from": "ifu=NOPE"},
     "not a document type"),
    ({"index_url": INDEX, "link_pattern": r"\.pdf$", "doc_type_from": "ifu"},
     "missing the = sign"),
])
def test_an_unusable_recipe_is_refused_before_anything_is_queued(
        client, conn, data, expect):
    """Refused at 422, in front of the person who typed it. The alternative is
    a job that dead-letters in a queue nobody is watching, or -- worse for the
    two-line cases -- a probe that runs against the wrong pattern and reports a
    confident zero."""
    resp = client.post("/playbooks/alpha/probe", data=data)
    assert resp.status_code == 422, resp.text
    assert expect in resp.text
    assert _jobs(conn) == []


# --- polling ----------------------------------------------------------------

def test_the_partial_keeps_polling_until_there_is_a_result(client, conn):
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    job_id = _jobs(conn)[0]["id"]

    waiting = client.get(f"/playbooks/alpha/probe/{job_id}")
    assert "hx-trigger=\"every 2s\"" in waiting.text

    _probe_row(conn, job_id, **DONE)
    conn.commit()
    done = client.get(f"/playbooks/alpha/probe/{job_id}")
    assert "hx-trigger=\"every 2s\"" not in done.text, "a finished probe kept asking"


def test_a_dead_job_says_so_instead_of_spinning(client, conn):
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    job_id = _jobs(conn)[0]["id"]
    conn.execute("UPDATE job SET status='dead', last_error='no worker' WHERE id=%s",
                 (job_id,))
    conn.commit()
    resp = client.get(f"/playbooks/alpha/probe/{job_id}")
    assert "hx-trigger=\"every 2s\"" not in resp.text
    assert "died without writing a result" in resp.text
    assert "no worker" in resp.text


def test_an_unknown_probe_is_a_404(client):
    assert client.get("/playbooks/alpha/probe/999999").status_code == 404


# --- rendering (spec §3.3) --------------------------------------------------

def test_the_result_leads_with_the_verdict_and_never_hides_the_unclassified(
        client, conn):
    """§3.3: the verdict before any number, and the unclassified count shown
    rather than folded away -- a thin harvest must not read as a complete one.
    """
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    job_id = _jobs(conn)[0]["id"]
    _probe_row(conn, job_id, **DONE)
    conn.commit()

    body = client.get(f"/playbooks/alpha/probe/{job_id}").text
    assert "robots.txt allows this" in body
    assert body.index("robots.txt allows this") < body.index("12")
    assert "unclassified" in body
    assert "7" in body                                     # the unclassified count
    # A guess, labelled as one.
    assert "is a guess" in body
    # The sample is a sample, and says how many of how many.
    assert "First 2 of 12" in body
    assert "price-list.pdf" in body


def test_a_robots_refusal_renders_as_a_result_not_an_error(client, conn):
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    job_id = _jobs(conn)[0]["id"]
    _probe_row(conn, job_id, status="refused", robots_verdict="disallow (Disallow: /)")
    conn.commit()
    body = client.get(f"/playbooks/alpha/probe/{job_id}").text
    assert "Refused" in body
    assert "Disallow: /" in body
    assert "Nothing was fetched" in body
    # No save offered on a refusal: there is nothing proved to save.
    assert "Save this recipe" not in body


def test_a_probe_that_matched_nothing_says_the_page_loaded(client, conn):
    """Zero matches and zero anchors mean different things and must not render
    the same: one is a wrong pattern, the other is a JavaScript-drawn list or
    the wrong page entirely."""
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.docx$"})
    job_id = _jobs(conn)[0]["id"]
    _probe_row(conn, job_id, status="done", robots_verdict="allow", tier="static",
               counts=Json({"anchors_seen": 40, "links_matched": 0,
                            "links_capped": 0, "off_host": 0, "links_kept": 0,
                            "by_type": {}, "sample_shown": 0}),
               sample=Json([]))
    conn.commit()
    body = client.get(f"/playbooks/alpha/probe/{job_id}").text
    assert "Nothing matched" in body
    assert "so it\n      loaded" in body
    # NOT a confident "the pattern is wrong": measured against NSK on
    # 2026-09-04, a page with 5 anchors and 1.362 PDFs held every document on a
    # `<tr data-href>` the harvester cannot read. The pattern was correct.
    assert "cannot follow" in body
    assert "Save this recipe" not in body


def test_a_capped_harvest_reports_what_it_dropped(client, conn):
    client.post("/playbooks/alpha/probe",
                data={"index_url": INDEX, "link_pattern": r"\.pdf$"})
    job_id = _jobs(conn)[0]["id"]
    _probe_row(conn, job_id, status="done", robots_verdict="allow", tier="static",
               counts=Json({"anchors_seen": 1329, "links_matched": 1329,
                            "links_capped": 1129, "off_host": 0,
                            "links_kept": 200, "by_type": {"IFU": 200},
                            "sample_shown": 0}),
               sample=Json([]))
    conn.commit()
    body = client.get(f"/playbooks/alpha/probe/{job_id}").text
    assert "1129" in body
    assert "dropped at" in body


# --- saving -----------------------------------------------------------------

def test_saving_writes_the_recipe_through_the_normal_revision_path(client, conn):
    before = _rev(conn)
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": before, "index_url": INDEX, "link_pattern": r"\.pdf$",
        "max_links": "50", "doc_type_from": "konformit=DoC\nsds_=",
        "note": "probed 2026-09-04"})
    assert resp.status_code == 200, resp.text

    body = _body(conn)
    assert body["crawl"] == [{
        "index_url": INDEX, "link_pattern": r"\.pdf$", "max_links": 50,
        "doc_type_from": {"konformit": "DoC", "sds_": None},
        "same_host_only": True}]
    assert _rev(conn) == before + 1
    # The history the editor already keeps, not a side channel of its own.
    note = conn.execute(
        "SELECT r.note FROM manufacturer_playbook_revision r "
        "JOIN manufacturer m ON m.id = r.manufacturer_id "
        "WHERE m.slug='alpha' ORDER BY r.rev DESC LIMIT 1").fetchone()["note"]
    assert note == "probed 2026-09-04"


def test_saving_from_a_stale_page_is_refused(client, conn):
    """The same optimistic lock every other save on this page has. Two people
    probing one manufacturer must not have the second silently discard the
    first."""
    client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$"})
    stale = client.post("/playbooks/alpha/crawl", data={
        "rev": 0, "index_url": INDEX, "link_pattern": r"\.htm$"})
    assert stale.status_code == 422
    assert "changed since" in stale.text or "revision" in stale.text
    assert _body(conn)["crawl"][0]["link_pattern"] == r"\.pdf$"


def test_re_saving_the_same_library_replaces_the_recipe_and_keeps_its_extra_keys(
        client, conn):
    """`crawl` is a list, so an unmatched save would append a near-duplicate
    that fetches the same page twice. And this form has no control for
    `pagination` -- a save that serialised the form alone would turn a working
    two-page recipe into a broken one-page one with no error anywhere. Same for
    `allow_hosts` when the field is ABSENT from the post (an older form, or the
    Tier A save): absent means unsaid, and unsaid never deletes. An empty box
    is a different thing and clears it -- the test below.
    """
    conn.execute("UPDATE manufacturer SET body = body || %s::jsonb WHERE slug='alpha'",
                 (Json({"crawl": [{
                     "index_url": INDEX, "link_pattern": r"\.pdf$",
                     "allow_hosts": ["cdn.alpha.example"],
                     "pagination": {"param": "page", "max_pages": 5}}]}),))
    conn.commit()

    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf|\.PDF$"})
    assert resp.status_code == 200, resp.text

    recipes = _body(conn)["crawl"]
    assert len(recipes) == 1, "the same library was authored twice"
    assert recipes[0]["link_pattern"] == r"\.pdf|\.PDF$"
    assert recipes[0]["allow_hosts"] == ["cdn.alpha.example"]
    assert recipes[0]["pagination"] == {"param": "page", "max_pages": 5}


# allow_hosts on the form (2026-09-07)
# --------------------------------------------------------------------------- #
# The one key a crawl recipe needs that the browser could not author. Ultradent
# lists on ultradent.com and serves every PDF from assets.ctfassets.net: with
# `same_host_only` pinned True and no way to name that host, the form could
# only ever save a recipe that harvests nothing.

def test_allow_hosts_is_saved_from_the_form(client, conn):
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$",
        "allow_hosts": "assets.ctfassets.net\ncdn.alpha.example"})
    assert resp.status_code == 200, resp.text
    assert _body(conn)["crawl"][0]["allow_hosts"] == [
        "assets.ctfassets.net", "cdn.alpha.example"]


def test_an_empty_allow_hosts_box_clears_the_key(client, conn):
    """Unlike `same_host_only`, an empty textarea is unambiguous: the operator
    saw the field and named no other host. That is why this one is offered and
    the checkbox is not."""
    conn.execute("UPDATE manufacturer SET body = body || %s::jsonb WHERE slug='alpha'",
                 (Json({"crawl": [{"index_url": INDEX, "link_pattern": r"\.pdf$",
                                   "allow_hosts": ["cdn.alpha.example"]}]}),))
    conn.commit()
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$",
        "allow_hosts": ""})
    assert resp.status_code == 200, resp.text
    assert "allow_hosts" not in _body(conn)["crawl"][0]


def test_allow_hosts_refuses_a_path_rather_than_storing_a_dead_value(client, conn):
    """`playbooks._normalize_domain` keeps a path scope, because `domains`
    needs one. `app.crawl` matches a bare hostname, so a path would widen
    nothing and say nothing -- the failure this refusal exists to prevent."""
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$",
        "allow_hosts": "https://assets.ctfassets.net/j8tkpy1gjhi5/"})
    assert resp.status_code == 422, resp.text
    assert "assets.ctfassets.net" in resp.text
    assert "crawl" not in _body(conn) or not _body(conn)["crawl"]


def test_allow_hosts_accepts_a_full_url_for_the_host_itself(client, conn):
    """An operator pastes what the browser gave them. A URL with no path is a
    host, and it is STORED folded, so the recipe table shows the value the
    crawl actually compares against. Only a PATH is refused."""
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$",
        "allow_hosts": "https://assets.ctfassets.net/"})
    assert resp.status_code == 200, resp.text
    assert _body(conn)["crawl"][0]["allow_hosts"] == ["assets.ctfassets.net"]


def test_the_probe_and_the_save_see_the_same_allow_hosts(client, conn):
    """The divergence `crawl_from_form` exists to prevent: probing with a CDN
    named and saving without it would store a recipe whose harvest is nothing
    like the one the operator just approved on screen."""
    probed = registry.crawl_from_form(
        {"index_url": INDEX, "link_pattern": r"\.pdf$",
         "allow_hosts": "assets.ctfassets.net"})
    saved = registry.crawl_from_form(
        {"index_url": INDEX, "link_pattern": r"\.pdf$",
         "allow_hosts": "assets.ctfassets.net"}, dict(probed))
    assert probed["allow_hosts"] == saved["allow_hosts"] == ["assets.ctfassets.net"]


def test_a_second_library_is_added_rather_than_replacing_the_first(client, conn):
    client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": INDEX, "link_pattern": r"\.pdf$"})
    client.post("/playbooks/alpha/crawl", data={
        "rev": _rev(conn), "index_url": "https://alpha.example/ifu",
        "link_pattern": r"\.pdf$"})
    assert [r["index_url"] for r in _body(conn)["crawl"]] == [
        INDEX, "https://alpha.example/ifu"]


def test_saving_an_unusable_recipe_is_refused_without_touching_the_body(client, conn):
    before_rev, before_body = _rev(conn), _body(conn)
    resp = client.post("/playbooks/alpha/crawl", data={
        "rev": before_rev, "index_url": INDEX, "link_pattern": "([unclosed"})
    assert resp.status_code == 422
    assert _rev(conn) == before_rev
    assert _body(conn) == before_body


def test_saving_into_a_playbook_with_no_row_is_a_404(client, conn):
    """A file that `manufacturers seed` has not imported has no row to lock
    against and no revision to record. The editor already refuses to save one;
    so does this."""
    assert client.post("/playbooks/nope/crawl", data={
        "rev": 0, "index_url": INDEX, "link_pattern": r"\.pdf$"}).status_code == 404


# --- the pure helpers -------------------------------------------------------

def test_the_dedupe_key_normalises_the_url_so_one_library_is_one_probe():
    """Invariant 8 scopes this to ACTIVE jobs, so re-probing after a result is
    in is allowed -- which is exactly what an author adjusting a pattern does.
    What it must collapse is the same library spelled two ways."""
    a = registry.probe_dedupe_key("alpha", "https://alpha.example/library")
    b = registry.probe_dedupe_key("alpha", "https://alpha.example/library#docs")
    assert a == b
    assert registry.probe_dedupe_key("beta", "https://alpha.example/library") != a


def test_an_empty_type_rule_is_an_exclusion_not_a_dropped_line():
    assert registry.doc_type_from_lines("ifu=IFU\nsds_=\n\n") == {
        "ifu": "IFU", "sds_": None}
