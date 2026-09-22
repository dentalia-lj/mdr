"""`playbook` rung — the crawl recipe (S2.1).

Design: docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md §3
(the 8 ordered steps this test file pins), §4.1 (robots), §4.3 (cap defences),
§4.6 (never-silent counting), §4.7 (group attribution), §4.8 (starvation —
fixed upstream; this is the rung that benefits from it).

Real Postgres (CLAUDE.md: no mocking). The network is faked throughout via an
injected `ScriptedFetcher` keyed on exact URL — no real HTTP anywhere in this
file, and an un-scripted URL raises loudly rather than reaching out.

Kept separate from tests/test_discover_handler.py (962 lines already, and
other sessions are live in this tree) rather than extending it.
"""

from __future__ import annotations

import pytest

from app import queue
from app.adapters.fetcher import FetchResult
from app.handlers import discover as dh
from app.playbooks import Crawl, DocSource, Playbook
from app.workers import runner  # noqa: F401 - registers real handlers over no-ops


# --- fakes -------------------------------------------------------------------

class ScriptedFetcher:
    """Exact-URL-keyed fake transport. Raises on anything not scripted, so a
    branch that fetches when it should not (e.g. after a robots refusal, or
    past a held lease) fails the test loudly instead of silently succeeding."""

    def __init__(self, responses: dict[str, FetchResult]):
        self._responses = dict(responses)
        self.calls: list[str] = []

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:
        self.calls.append(url)
        if url not in self._responses:
            raise AssertionError(f"unscripted fetch: {url}")
        return self._responses[url]


class ScriptedRenderer:
    """Exact-URL-keyed fake for the one-method `Renderer` protocol
    (`render(url) -> str`, design §4.2, ruled 2026-08-24). Mirrors
    `ScriptedFetcher`'s discipline: an unscripted URL raises loudly rather
    than reaching for a real browser. `raises` names URLs where `render`
    itself raises (timeout / crash), to test the degrade-to-static path
    (§4.2 "a render failure ... must never dead-letter the job")."""

    def __init__(self, responses: dict[str, str] | None = None,
                 *, raises: frozenset[str] = frozenset()):
        self._responses = dict(responses or {})
        self._raises = raises
        self.calls: list[str] = []

    def render(self, url: str) -> str:
        self.calls.append(url)
        if url in self._raises:
            raise RuntimeError(f"render failed for {url}")
        if url not in self._responses:
            raise AssertionError(f"unscripted render: {url}")
        return self._responses[url]


def _robots_allow(status=404):
    """`app.robots` reads 404/410 as 'no robots.txt' -> full allow (§4.1)."""
    return FetchResult(status=status, body=None, etag=None, last_modified=None,
                       content_type=None, final_url="")


def _robots_disallow():
    return FetchResult(
        status=200, body=b"User-agent: *\nDisallow: /\n",
        etag=None, last_modified=None, content_type="text/plain", final_url="",
    )


def _html(status=200, body=b"", content_type="text/html"):
    return FetchResult(status=status, body=body, etag=None, last_modified=None,
                       content_type=content_type, final_url="")


# --- seed helpers (mirrors tests/test_discover_handler.py's, kept local so
# this file has no import-order dependency on it) ----------------------------

def _seed_group(conn, *, canonical="CRAWLco", label="Widget"):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,%s) "
        "RETURNING group_id",
        (canonical, label),
    ).fetchone()["group_id"]


def _seed_member(conn, gid, item_ref, *, mfr_ref=None, basis="udi"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, product_class, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'Widget','011',%s,true,'IIa','LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, mfr_ref),
    )
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
        "VALUES (%s,%s,%s,%s)",
        (gid, item_ref, mfr_ref, basis),
    )


def _job(gid, jid=1, **extra):
    return {"id": jid, "type": "discover.group", "payload": {"group_id": gid, **extra}}


def _rank_all():
    return lambda facts, candidates: [(c, 0.99) for c in candidates]


def _disc_log(conn, gid):
    return conn.execute(
        "SELECT source, outcome, detail FROM discovery_log WHERE group_id=%s ORDER BY id",
        (gid,),
    ).fetchall()


def _fetch_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='fetch.url' ORDER BY id"
    ).fetchall()


def _crawl_ladder_cfg(monkeypatch, manufacturer):
    """`playbook` first, `manual` as the only fallback, so every test's ladder
    behaviour is unambiguous."""
    import dataclasses
    from app.config import load_config

    cfg = dataclasses.replace(
        load_config(), source_priority={manufacturer: ["playbook", "manual"]}
    )
    monkeypatch.setattr(dh, "load_config", lambda: cfg)
    return cfg


def _seed_crawl_playbook(monkeypatch, manufacturer, crawl, *, doc_sources=()):
    # `Playbook.crawl` is a TUPLE of recipes (ruled 2026-09-03). The helper
    # accepts None / one Crawl / an iterable so each test states only what it
    # is about.
    if crawl is None:
        recipes = ()
    elif isinstance(crawl, Crawl):
        recipes = (crawl,)
    else:
        recipes = tuple(crawl)
    pb = Playbook(slug=manufacturer.lower(), manufacturer=manufacturer,
                  doc_sources=doc_sources, crawl=recipes)
    monkeypatch.setattr(dh.playbooks_mod, "for_manufacturer", lambda _m: pb)
    return pb


# --- 1. no crawl block -> today's exact behaviour, pinned -------------------

class _BoomFetcher:
    """Asserts DISCOVER never makes a network call on the no-crawl path — it
    only ever enqueues `fetch.url` for FETCH to execute."""

    def get(self, *a, **k):  # noqa: ANN002
        raise AssertionError("discover.group must not fetch when playbook.crawl is None")


def test_no_crawl_block_still_emits_authored_direct_urls_with_zero_network(conn, monkeypatch):
    manufacturer = "DIRECTONLYco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    _seed_crawl_playbook(
        monkeypatch, manufacturer, crawl=None,
        doc_sources=(DocSource(doc_type="DoC", kind="direct",
                               url="https://directonly.example/a.pdf"),),
    )
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    res = dh.handle_discover_group(conn, _job(g), fetcher=_BoomFetcher())

    assert res["terminal_source"] == "playbook"
    assert res["outcome"] == "fetch"
    assert res["emitted_fetch"] == 1
    urls = [r["payload"]["url"] for r in _fetch_jobs(conn)]
    assert "https://directonly.example/a.pdf" in urls
    assert ("playbook", "hit") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]


def test_no_crawl_block_and_no_direct_source_falls_through_to_manual(conn, monkeypatch):
    """The rung must never error for an unauthored manufacturer (design §3
    step 2) — a playbook with neither `crawl` nor a `direct` doc_source is a
    plain miss, exactly as before this slice."""
    manufacturer = "NOTHINGco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=None)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    res = dh.handle_discover_group(conn, _job(g), fetcher=_BoomFetcher())

    assert res["outcome"] == "manual"
    assert ("playbook", "miss") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert _fetch_jobs(conn) == []


# --- 2. robots refusal --------------------------------------------------------

def test_crawl_robots_refusal_is_logged_and_index_is_never_fetched(conn, monkeypatch):
    manufacturer = "REFUSEDco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://refused.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://refused.example/robots.txt": _robots_disallow(),
        # NOTHING scripted for the index URL — if the rung fetches it anyway,
        # ScriptedFetcher raises and the test fails.
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["outcome"] == "manual"          # fell through the ladder
    assert fetcher.calls == ["https://refused.example/robots.txt"]
    logs = [(r["source"], r["outcome"], r["detail"]) for r in _disc_log(conn, g)]
    skip = [d for (s, o, d) in logs if s == "playbook" and o == "skipped"]
    assert len(skip) == 1
    assert skip[0]["reason"] == "robots-disallow"
    assert skip[0]["host"] == "refused.example"
    assert _fetch_jobs(conn) == []


# --- 3. domain lease held -> the whole job defers ----------------------------

def test_crawl_defers_the_whole_job_when_the_domain_lease_is_held(conn, monkeypatch):
    manufacturer = "LEASEDco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://leased.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    # Hold the lease exactly as a concurrent FETCH job would.
    conn.execute(
        "INSERT INTO domain_lease (domain, leased_until) "
        "VALUES ('leased.example', now() + interval '1 hour')"
    )

    # NOTHING is scripted: a held lease must stop the rung before ANY request
    # to that host -- robots.txt included. The lease is acquired before the
    # robots check (2026-09-03) precisely because robots.txt is itself a
    # request, and an unleased one would be the one unspaced call we make to
    # a host we have been asked to wait on.
    fetcher = ScriptedFetcher({})

    jid = queue.enqueue(conn, "discover.group", {"group_id": g}, dedupe_key=f"discover:{g}:t")
    res = dh.handle_discover_group(conn, _job(g, jid=jid), fetcher=fetcher)

    assert res.get("_deferred") is True
    assert fetcher.calls == []
    row = conn.execute(
        "SELECT status, run_after > now() AS in_future FROM job WHERE id=%s", (jid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["in_future"] is True
    assert _fetch_jobs(conn) == []


# --- 4. happy path: N links emitted, counts correct --------------------------

_LISTING_HTML = b"""
<html><body>
  <a href="/docs/a.pdf">A</a>
  <a href="/docs/b.pdf">B</a>
  <a href="/docs/c.pdf">C</a>
  <a href="/about">About</a>
  <a href="/docs/notes.txt">Notes</a>
</body></html>
"""


def test_crawl_happy_path_emits_matched_links_with_correct_breakdown(conn, monkeypatch):
    manufacturer = "HAPPYco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://happy.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://happy.example/robots.txt": _robots_allow(),
        "https://happy.example/downloads": _html(body=_LISTING_HTML),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["terminal_source"] == "playbook"
    assert res["outcome"] == "fetch"
    assert res["emitted_fetch"] == 3
    urls = sorted(r["payload"]["url"] for r in _fetch_jobs(conn))
    assert urls == [
        "https://happy.example/docs/a.pdf",
        "https://happy.example/docs/b.pdf",
        "https://happy.example/docs/c.pdf",
    ]
    assert all(r["payload"].get("group_id") == g for r in _fetch_jobs(conn))

    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"]
    assert len(hit) == 1
    d = hit[0]["detail"]
    assert d["anchors_seen"] == 5
    assert d["links_matched"] == 3
    assert d["links_capped"] == 0
    assert d["off_host"] == 0
    assert d["pages_fetched"] == 1
    assert d["emitted"] == 3
    assert d["deduped"] == 0
    # §4.2 (ruled 2026-08-21): every rung records which tier produced the
    # links -- the happy static path never touches the browser.
    assert d["tier"] == "static"


def test_crawl_dedupes_against_an_already_active_fetch_job(conn, monkeypatch):
    """`deduped` (§4.6) must reflect real `_emit_fetch` collisions, not just
    be reported as 0 forever."""
    from app.urls import normalize_url

    manufacturer = "DEDUPco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://dedup.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    already = "https://dedup.example/docs/a.pdf"
    queue.enqueue(conn, "fetch.url", {"url": already, "domain": "dedup.example"},
                 dedupe_key=f"fetch:{normalize_url(already)}")

    fetcher = ScriptedFetcher({
        "https://dedup.example/robots.txt": _robots_allow(),
        "https://dedup.example/downloads": _html(body=_LISTING_HTML),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["emitted_fetch"] == 2   # b.pdf, c.pdf — a.pdf already active
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["emitted"] == 2
    assert hit["detail"]["deduped"] == 1


# --- 5. anchors present, zero matches: the "your pattern is wrong" signal ---

def test_crawl_anchors_present_but_zero_matches_is_a_distinct_miss(conn, monkeypatch):
    manufacturer = "WRONGPATTERNco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    # The listing has real anchors, but the pattern is authored wrong
    # (.docx instead of .pdf) — this must read differently from zero anchors.
    crawl = Crawl(index_url="https://wrongpattern.example/downloads", link_pattern=r"\.docx$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://wrongpattern.example/robots.txt": _robots_allow(),
        "https://wrongpattern.example/downloads": _html(body=_LISTING_HTML),
    })
    # Nothing scripted -- §4.2 is explicit that the escalation trigger is
    # ZERO ANCHORS, not zero matches. Anchors are present here (5 of them),
    # so the renderer must never be touched; an unscripted call raises.
    renderer = ScriptedRenderer()

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher, renderer=renderer)

    assert res["outcome"] == "manual"
    miss = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "miss"]
    assert len(miss) == 1
    assert miss[0]["detail"]["anchors_seen"] == 5
    assert miss[0]["detail"]["links_matched"] == 0
    assert miss[0]["detail"]["tier"] == "static"
    assert renderer.calls == []
    assert _fetch_jobs(conn) == []


_RENDERED_LISTING_HTML = """
<html><body>
  <a href="/docs/x.pdf">X</a>
  <a href="/docs/y.pdf">Y</a>
</body></html>
"""


def test_crawl_zero_anchors_escalates_once_and_uses_rendered_links(conn, monkeypatch):
    """Design §4.2, ruled 2026-08-24: zero `<a href>` on the page (a listing
    built client-side) escalates to `Renderer.render` exactly once, and the
    RENDERED harvest -- not the static one -- is what gets emitted and
    logged. This is the browser rung landing; a page with matching links but
    parsed from a JS-built DOM must now produce `fetch.url` jobs."""
    manufacturer = "JSLISTINGco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://jslisting.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://jslisting.example/robots.txt": _robots_allow(),
        "https://jslisting.example/downloads": _html(
            body=b"<html><body>no links here -- rendered by JS</body></html>"),
    })
    renderer = ScriptedRenderer({
        "https://jslisting.example/downloads": _RENDERED_LISTING_HTML,
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher, renderer=renderer)

    assert res["outcome"] == "fetch"
    assert res["emitted_fetch"] == 2
    urls = sorted(r["payload"]["url"] for r in _fetch_jobs(conn))
    assert urls == [
        "https://jslisting.example/docs/x.pdf",
        "https://jslisting.example/docs/y.pdf",
    ]

    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"]
    assert len(hit) == 1
    d = hit[0]["detail"]
    assert d["tier"] == "rendered"
    assert d["anchors_seen"] == 2
    assert d["links_matched"] == 2
    assert d["emitted"] == 2

    # Escalated exactly ONCE for this page -- never twice.
    assert renderer.calls == ["https://jslisting.example/downloads"]
    assert fetcher.calls == [
        "https://jslisting.example/robots.txt",
        "https://jslisting.example/downloads",
    ]


def test_crawl_render_yields_nothing_is_a_miss_logged_with_rendered_tier(conn, monkeypatch):
    """§4.2's never-silent requirement: a render that was actually attempted
    and still found nothing must read differently in the log from a render
    that was never tried -- `tier: "rendered"` on an otherwise-identical
    zero-anchors miss says exactly that."""
    manufacturer = "STILLEMPTYco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://stillempty.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://stillempty.example/robots.txt": _robots_allow(),
        "https://stillempty.example/downloads": _html(body=b"<html><body>nothing</body></html>"),
    })
    renderer = ScriptedRenderer({
        "https://stillempty.example/downloads": "<html><body>still nothing after render</body></html>",
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher, renderer=renderer)

    assert res["outcome"] == "manual"
    miss = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "miss"]
    assert len(miss) == 1
    assert miss[0]["detail"]["anchors_seen"] == 0
    assert miss[0]["detail"]["links_matched"] == 0
    assert miss[0]["detail"]["tier"] == "rendered"
    assert renderer.calls == ["https://stillempty.example/downloads"]
    assert _fetch_jobs(conn) == []


def test_crawl_render_failure_degrades_to_static_miss_without_failing_the_job(conn, monkeypatch):
    """§4.2: a render failure (exception, timeout) must degrade to the
    static (zero-anchor) result and a logged miss -- never dead-letter
    `discover.group`. A slow/broken manufacturer site must not kill the
    whole group's discovery run."""
    manufacturer = "BROKENRENDERco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://brokenrender.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://brokenrender.example/robots.txt": _robots_allow(),
        "https://brokenrender.example/downloads": _html(body=b"<html><body>nothing</body></html>"),
    })
    renderer = ScriptedRenderer(
        raises=frozenset({"https://brokenrender.example/downloads"}))

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher, renderer=renderer)

    # The job completed -- no exception escaped, no dead letter.
    assert res["outcome"] == "manual"
    miss = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "miss"]
    assert len(miss) == 1
    assert miss[0]["detail"]["anchors_seen"] == 0
    assert miss[0]["detail"]["links_matched"] == 0
    assert renderer.calls == ["https://brokenrender.example/downloads"]
    assert _fetch_jobs(conn) == []
    # No exception escaped `handle_discover_group` -- reaching this line at
    # all is the "does not fail the job" assertion; nothing here goes through
    # the runner/queue apparatus (this test calls the handler directly, like
    # every other test in this file), so there is no `job` row to re-check.


# --- 6. cap truncation is counted, never a silent slice ----------------------

_MANY_LINKS_HTML = b"".join(
    f'<a href="/docs/{i}.pdf">{i}</a>\n'.encode() for i in range(5)
)


def test_crawl_cap_truncation_is_counted(conn, monkeypatch):
    manufacturer = "CAPPEDco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://capped.example/downloads", link_pattern=r"\.pdf$",
                  max_links=2)
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://capped.example/robots.txt": _robots_allow(),
        "https://capped.example/downloads": _html(body=_MANY_LINKS_HTML),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher,
                                   crawl_rank=_judge_all_other())

    assert res["emitted_fetch"] == 2
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["links_matched"] == 5
    assert hit["detail"]["links_capped"] == 3
    assert hit["detail"]["emitted"] == 2


# --- a collection is judged, and the budget spent in that order ------------
# Ruled 2026-09-04 ([crawl-ai-link-triage], docs/decisions.md): deterministic triage
# first; a library page with `crawl_rank_floor` or more surviving links is
# judged by the T1 link ranker and the budget is spent (1) links naming one of
# the group's own article numbers, (2) DoC > EC > ISO > IFU > other, (3)
# confidence. A ranking, never a filter.

from app.extract.ranking import LinkJudgement

_RANKED_HTML = b"".join(
    f'<a href="/docs/{name}.pdf">{title}</a>\n'.encode()
    for name, title in [
        ("ifu-1", "Instructions for use"), ("ifu-2", "Gebrauchsanweisung"),
        ("doc-mdr", "Declaration of Conformity MDR"), ("msds", "Safety data sheet"),
        ("ec-cert", "EC certificate 2024"), ("ifu-3", "IFU implants"),
        ("ifu-ref", "IFU 196.644.050 handpiece"),
    ]
)
_FEW_LINKS_HTML = b"".join(
    f'<a href="/docs/{i}.pdf">doc {i}</a>\n'.encode() for i in range(3)
)


def _judge_by_title():
    """A scripted link judge: the type and article numbers a person would read
    off the anchor, and a record of what it was shown."""
    seen = []

    def judge(manufacturer, candidates):
        seen.append(list(candidates))
        out = []
        for c in candidates:
            t = c.title.casefold()
            kind = ("DoC" if "declaration" in t else "EC" if "certificate" in t
                    else "IFU" if ("ifu" in t or "instructions" in t or "gebrauchs" in t)
                    else "other")
            refs = tuple(w for w in c.title.split() if w.count(".") == 2)
            out.append(LinkJudgement(c.url, kind, refs, 0.9 if kind != "other" else 0.1))
        return out
    judge.seen = seen
    return judge


def _judge_all_other():
    return lambda manufacturer, candidates: [
        LinkJudgement(c.url, "other", (), 0.1) for c in candidates]


def _ranked_site(body=_RANKED_HTML, host="ranked.example"):
    return ScriptedFetcher({
        f"https://{host}/robots.txt": _robots_allow(),
        f"https://{host}/downloads": _html(body=body),
    })


def _ranked_group(conn, monkeypatch, manufacturer, *, max_links, doc_type_from=None):
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1", mfr_ref="196.644.050")
    crawl = Crawl(index_url="https://ranked.example/downloads", link_pattern=r"\.pdf$",
                  max_links=max_links, doc_type_from=doc_type_from)
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    cfg = _crawl_ladder_cfg(monkeypatch, manufacturer)
    return g, cfg


def test_over_budget_the_budget_goes_to_our_article_then_doc_then_ec(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "RANKco", max_links=3)
    judge = _judge_by_title()

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=judge)

    assert res["emitted_fetch"] == 3
    urls = [j["payload"]["url"] for j in _fetch_jobs(conn)]
    assert urls == ["https://ranked.example/docs/ifu-ref.pdf",   # names our REF
                    "https://ranked.example/docs/doc-mdr.pdf",
                    "https://ranked.example/docs/ec-cert.pdf"]
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    d = hit["detail"]
    assert d["links_matched"] == 7 and d["links_capped"] == 4
    assert d["rank"]["judged"] == 7 and d["rank"]["kept"] == 3
    assert d["rank"]["ref_matches"] == 1
    assert d["rank"]["by_type"] == {"IFU": 4, "DoC": 1, "EC": 1, "other": 1}
    assert d["emitted"] == 3


def test_under_budget_a_collection_is_still_ordered_and_nothing_is_dropped(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "ORDERco", max_links=20)
    judge = _judge_by_title()

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=judge)

    assert res["emitted_fetch"] == 7
    urls = [j["payload"]["url"] for j in _fetch_jobs(conn)]
    assert urls[:3] == ["https://ranked.example/docs/ifu-ref.pdf",
                        "https://ranked.example/docs/doc-mdr.pdf",
                        "https://ranked.example/docs/ec-cert.pdf"]
    assert urls[-1] == "https://ranked.example/docs/msds.pdf"      # `other` last
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["links_capped"] == 0
    assert hit["detail"]["rank"]["kept"] == 7


def test_a_library_is_judged_once_and_every_later_group_reads_the_answer(conn, monkeypatch):
    """NSK has 136 groups and one library. Migration 061: the judgement is a
    property of the library version, the ORDER (which of OUR articles a link
    names) is per group and computed at read time."""
    import dataclasses
    g1, cfg = _ranked_group(conn, monkeypatch, "ONCEco", max_links=3)
    g2 = _seed_group(conn, canonical="ONCEco", label="Other widget")
    _seed_member(conn, g2, "IT-2", mfr_ref="999.000.001")
    # no politeness wait between the two crawls of one host in a test
    cfg = dataclasses.replace(cfg, fetch=dataclasses.replace(cfg.fetch, politeness_ms=0))
    monkeypatch.setattr(dh, "load_config", lambda: cfg)
    judge = _judge_by_title()

    dh.handle_discover_group(conn, _job(g1, jid=1), fetcher=_ranked_site(), crawl_rank=judge)
    dh.handle_discover_group(conn, _job(g2, jid=2), fetcher=_ranked_site(), crawl_rank=judge)

    assert len(judge.seen) == 1                                # one LLM call, two groups
    first = [r for r in _disc_log(conn, g1) if r["outcome"] == "hit"][0]["detail"]["rank"]
    second = [r for r in _disc_log(conn, g2) if r["outcome"] == "hit"][0]["detail"]["rank"]
    assert first["cached"] is False and second["cached"] is True
    # the ORDER is still per group: g2 owns no article the page names
    assert first["ref_matches"] == 1 and second["ref_matches"] == 0
    assert second["kept_links"][0][0] == "https://ranked.example/docs/doc-mdr.pdf"
    assert conn.execute("SELECT count(*) AS n FROM crawl_link_rank").fetchone()["n"] == 1


def test_the_judge_is_shown_the_link_text_not_just_the_url(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "TEXTco", max_links=1)
    judge = _judge_by_title()

    dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=judge)

    shown = {c.url: c.title for batch in judge.seen for c in batch}
    assert shown["https://ranked.example/docs/doc-mdr.pdf"] == "Declaration of Conformity MDR"
    assert shown["https://ranked.example/docs/ifu-ref.pdf"] == "IFU 196.644.050 handpiece"


def test_below_the_floor_nothing_is_judged(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "SMALLco", max_links=10)

    def boom(manufacturer, candidates):
        raise AssertionError("three links are not a collection")

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(_FEW_LINKS_HTML),
                                   crawl_rank=boom)

    assert res["emitted_fetch"] == 3
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert "rank" not in hit["detail"]


def test_a_judge_failure_falls_back_to_page_order_and_says_so(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "FAILco", max_links=2)

    def broken(manufacturer, candidates):
        raise RuntimeError("no api key")

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=broken)

    assert res["emitted_fetch"] == 2
    urls = [j["payload"]["url"] for j in _fetch_jobs(conn)]
    assert urls == ["https://ranked.example/docs/ifu-1.pdf",
                    "https://ranked.example/docs/ifu-2.pdf"]
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert "no api key" in hit["detail"]["rank"]["error"]
    assert hit["detail"]["links_capped"] == 5


def test_a_harvest_at_the_ceiling_is_not_judged_but_named_too_broad(conn, monkeypatch):
    import dataclasses
    g, cfg = _ranked_group(conn, monkeypatch, "BROADco", max_links=2)
    cfg = dataclasses.replace(
        cfg, discovery=dataclasses.replace(cfg.discovery, crawl_rank_max=4))
    monkeypatch.setattr(dh, "load_config", lambda: cfg)

    def boom(manufacturer, candidates):
        raise AssertionError("a harvest at the ceiling must not be judged")

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=boom)

    assert res["emitted_fetch"] == 2
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["rank"]["skipped"] == "pattern-too-broad"
    assert hit["detail"]["rank"]["ceiling"] == 4
    assert hit["detail"]["links_capped"] == 5


def test_authored_exclusions_do_not_consume_the_budget(conn, monkeypatch):
    g, _ = _ranked_group(conn, monkeypatch, "EXCLco", max_links=3,
                         doc_type_from={"ifu": None, "msds": None})

    def boom(manufacturer, candidates):
        raise AssertionError("two survivors are below the floor; nothing to judge")

    res = dh.handle_discover_group(conn, _job(g), fetcher=_ranked_site(), crawl_rank=boom)

    assert res["emitted_fetch"] == 2
    urls = sorted(j["payload"]["url"] for j in _fetch_jobs(conn))
    assert urls == ["https://ranked.example/docs/doc-mdr.pdf",
                    "https://ranked.example/docs/ec-cert.pdf"]
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["excluded"] == 5


# --- fetch-failure of the index page is a miss, not a crash ------------------

def test_crawl_index_fetch_failure_is_a_logged_miss_not_a_job_failure(conn, monkeypatch):
    manufacturer = "DOWNco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://down.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://down.example/robots.txt": _robots_allow(),
        "https://down.example/downloads": _html(status=500, body=None),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["outcome"] == "manual"
    miss = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "miss"][0]
    assert miss["detail"]["reason"] == "fetch-status"
    assert miss["detail"]["status"] == 500
    assert _fetch_jobs(conn) == []


# --- pagination smoke test (§4.4) — inert in production, no playbook authors
# it yet, but the rung must not crash a paginated recipe and must stop on the
# configured page cap -----------------------------------------------------

def test_crawl_pagination_stops_at_max_pages_and_aggregates_counts(conn, monkeypatch):
    manufacturer = "PAGEDco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(
        index_url="https://paged.example/downloads", link_pattern=r"\.pdf$",
        pagination={"param": "page", "max_pages": 2},
    )
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    page1 = b'<a href="/docs/a.pdf">A</a><a href="/docs/b.pdf">B</a>'
    page2 = b'<a href="/docs/c.pdf">C</a>'

    fetcher = ScriptedFetcher({
        "https://paged.example/robots.txt": _robots_allow(),
        "https://paged.example/downloads": _html(body=page1),
        "https://paged.example/downloads?page=2": _html(body=page2),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["emitted_fetch"] == 3
    hit = [r for r in _disc_log(conn, g) if r["source"] == "playbook" and r["outcome"] == "hit"][0]
    assert hit["detail"]["pages_fetched"] == 2
    assert hit["detail"]["anchors_seen"] == 3


# --- crawl and direct are ADDITIVE, not exclusive ----------------------------
#
# Ruled by Denis 2026-09-03, correcting a literal reading of design §3 step 2.
# A crawl block adds reach; it never removes any. The exclusive reading set a
# silent trap: CARL MARTIN, GC, NSK, RENFERT and ULTRADENT all author `direct`
# PDFs a human has already verified, and adding a crawl block to any of them
# would have dropped those declarations out of discovery with nothing in any
# log to say so.

def test_a_playbook_with_both_crawl_and_direct_emits_both(conn, monkeypatch):
    manufacturer = "BOTHco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://both.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(
        monkeypatch, manufacturer, crawl=crawl,
        doc_sources=(DocSource(url="https://both.example/hand-verified.pdf",
                               kind="direct", doc_type="DoC"),),
    )
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://both.example/robots.txt": _robots_allow(),
        "https://both.example/downloads": _html(body=_LISTING_HTML),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["outcome"] == "fetch"
    # 1 authored direct + 3 harvested, not 3 and not 1.
    assert res["emitted_fetch"] == 4
    urls = sorted(r["payload"]["url"] for r in _fetch_jobs(conn))
    assert "https://both.example/hand-verified.pdf" in urls
    assert "https://both.example/docs/a.pdf" in urls
    assert len(urls) == 4


def test_a_crawl_miss_does_not_cancel_an_authored_direct_hit(conn, monkeypatch):
    """The whole point of the ruling: a manufacturer whose library moved must
    still get the PDF someone pinned by hand."""
    manufacturer = "SURVIVEco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    # robots forbids the index, so the crawl half misses outright.
    crawl = Crawl(index_url="https://survive.example/downloads", link_pattern=r"\.pdf$")
    _seed_crawl_playbook(
        monkeypatch, manufacturer, crawl=crawl,
        doc_sources=(DocSource(url="https://survive.example/pinned.pdf",
                               kind="direct", doc_type="DoC"),),
    )
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://survive.example/robots.txt": _robots_disallow(),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    assert res["outcome"] == "fetch"
    assert res["emitted_fetch"] == 1
    assert [r["payload"]["url"] for r in _fetch_jobs(conn)] == [
        "https://survive.example/pinned.pdf"]
    # The index was never fetched — only robots.txt was asked.
    assert fetcher.calls == ["https://survive.example/robots.txt"]


def test_a_crawl_miss_logs_exactly_one_row_not_two(conn, monkeypatch):
    """`_crawl_playbook` logs its own counted miss. The rung's bare fallback
    miss must not fire as well, or the empty row is the one a reader takes as
    the explanation."""
    manufacturer = "ONELOGco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url="https://onelog.example/downloads", link_pattern=r"\.zip$")
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)

    fetcher = ScriptedFetcher({
        "https://onelog.example/robots.txt": _robots_allow(),
        "https://onelog.example/downloads": _html(body=_LISTING_HTML),
    })

    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    miss = [r for r in _disc_log(conn, g)
            if r["source"] == "playbook" and r["outcome"] == "miss"]
    assert len(miss) == 1
    assert miss[0]["detail"] is not None      # the counted row survived
    assert miss[0]["detail"]["anchors_seen"] == 5


# --- the 2026-09-03 re-fetch loop, pinned -----------------------------------
#
# Found by running Edenta against the live site, not by a test: its two
# recipes share edenta.com, so a per-recipe lease meant recipe 2 always found
# the lease recipe 1 had just taken, deferred the whole job, and the retry
# re-ran recipe 1 from the top. 56 live fetches of one document list before it
# was stopped by hand. The lease is now taken ONCE per host, for the whole
# crawl, before any recipe fetches.

def test_two_recipes_on_one_host_do_not_deadlock_each_other(conn, monkeypatch):
    manufacturer = "TWOLIBco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    recipes = (
        Crawl(index_url="https://twolib.example/ifu", link_pattern=r"\.pdf$"),
        Crawl(index_url="https://twolib.example/certs", link_pattern=r"\.pdf$"),
    )
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=recipes)
    _crawl_ladder_cfg(monkeypatch, manufacturer)
    # No politeness pause in the test: the spacing is real behaviour, but a 2s
    # sleep per recipe is not something a unit test should pay for.
    monkeypatch.setattr(dh.time, "sleep", lambda _s: None)

    fetcher = ScriptedFetcher({
        "https://twolib.example/robots.txt": _robots_allow(),
        "https://twolib.example/ifu": _html(body=_LISTING_HTML),
        "https://twolib.example/certs": _html(body=_LISTING_HTML),
    })

    res = dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    # BOTH libraries ran in one pass. Before the fix the second deferred the
    # job and neither this assertion nor the run ever completed.
    assert res.get("_deferred") is not True
    assert res["outcome"] == "fetch"
    assert "https://twolib.example/ifu" in fetcher.calls
    assert "https://twolib.example/certs" in fetcher.calls
    # Each index page fetched exactly once -- the loop's signature was the
    # same URL fetched over and over.
    assert fetcher.calls.count("https://twolib.example/ifu") == 1

    hits = [r for r in _disc_log(conn, g)
            if r["source"] == "playbook" and r["outcome"] == "hit"]
    assert len(hits) == 2


def test_recipes_on_one_host_are_spaced_by_the_politeness_interval(conn, monkeypatch):
    """The lease bought the right to crawl the host, not the right to hit it
    as fast as the loop turns."""
    manufacturer = "SPACEDco"
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    recipes = (
        Crawl(index_url="https://spaced.example/a", link_pattern=r"\.pdf$"),
        Crawl(index_url="https://spaced.example/b", link_pattern=r"\.pdf$"),
    )
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=recipes)
    _crawl_ladder_cfg(monkeypatch, manufacturer)
    slept: list[float] = []
    monkeypatch.setattr(dh.time, "sleep", slept.append)

    fetcher = ScriptedFetcher({
        "https://spaced.example/robots.txt": _robots_allow(),
        "https://spaced.example/a": _html(body=_LISTING_HTML),
        "https://spaced.example/b": _html(body=_LISTING_HTML),
    })
    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    # One pause, before the SECOND recipe only -- never before the first.
    assert len(slept) == 1
    assert slept[0] > 0


# --- authored exclusions actually exclude ---------------------------------- #
#
# `doc_type_from`'s `null` value was read by the PROBE and by nothing else
# until 2026-09-04: `discover.py` had zero references to it, so a recipe's
# exclusions changed the number on the operator's screen and nothing about
# what the pipeline fetched. On NSK that is 446 safety data sheets and ~$20 of
# extraction the screen said would not happen.
# `[crawl-doc-type-from-is-probe-only]`; the ruling it implements is Denis's
# of 2026-09-03, quoted in `app/playbooks.py`.

_MIXED = (b"<html><body>"
          b"<a href='/d/ifu_handpiece.pdf'>1</a>"
          b"<a href='/d/sds_cleaner.pdf'>2</a>"
          b"<a href='/d/sds_lubricant.pdf'>3</a>"
          b"<a href='/d/konformitat_2026.pdf'>4</a>"
          b"</body></html>")


def _excluding_playbook(conn, monkeypatch, manufacturer, doc_type_from):
    g = _seed_group(conn, canonical=manufacturer)
    _seed_member(conn, g, "IT-1")
    crawl = Crawl(index_url=f"https://{manufacturer.lower()}.example/lib",
                  link_pattern=r"\.pdf$", doc_type_from=doc_type_from)
    _seed_crawl_playbook(monkeypatch, manufacturer, crawl=crawl)
    _crawl_ladder_cfg(monkeypatch, manufacturer)
    return g


def test_an_excluded_family_is_never_fetched(conn, monkeypatch):
    m = "EXCLUDEco"
    g = _excluding_playbook(conn, monkeypatch, m, {"sds_": None, "ifu": "IFU"})
    fetcher = ScriptedFetcher({
        f"https://{m.lower()}.example/robots.txt": _robots_allow(),
        f"https://{m.lower()}.example/lib": _html(body=_MIXED),
    })

    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    urls = sorted(j["payload"]["url"].rsplit("/", 1)[-1] for j in _fetch_jobs(conn))
    assert urls == ["ifu_handpiece.pdf", "konformitat_2026.pdf"]
    assert not any("sds_" in u for u in urls), "an excluded family was fetched anyway"


def test_the_counts_still_describe_the_whole_library(conn, monkeypatch):
    """"The files still appear in the crawl's counts" -- the other half of the
    ruling. `anchors_seen` and `links_matched` are untouched by an exclusion,
    which is what stops a library being silently under-reported; the drop is
    reported separately and PER NEEDLE, so an author can see which rule did the
    work and a rule that matched nothing is visible by its absence."""
    m = "COUNTco"
    g = _excluding_playbook(conn, monkeypatch, m, {"sds_": None, "never": None})
    fetcher = ScriptedFetcher({
        f"https://{m.lower()}.example/robots.txt": _robots_allow(),
        f"https://{m.lower()}.example/lib": _html(body=_MIXED),
    })

    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    row = _disc_log(conn, g)[0]
    d = row["detail"]
    assert d["anchors_seen"] == 4
    assert d["links_matched"] == 4          # the library, whole
    assert d["excluded"] == 2
    assert d["excluded_by"] == {"sds_": 2}  # `never` matched nothing, so it is absent
    assert d["emitted"] == 2


def test_a_recipe_with_no_exclusions_reports_none(conn, monkeypatch):
    """A count that is always present teaches nobody anything. `excluded` shows
    up only when something was."""
    m = "PLAINco"
    g = _excluding_playbook(conn, monkeypatch, m, {"ifu": "IFU"})
    fetcher = ScriptedFetcher({
        f"https://{m.lower()}.example/robots.txt": _robots_allow(),
        f"https://{m.lower()}.example/lib": _html(body=_MIXED),
    })

    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    d = _disc_log(conn, g)[0]["detail"]
    assert "excluded" not in d and "excluded_by" not in d
    assert d["emitted"] == 4


def test_a_crawl_whose_every_link_is_excluded_is_a_hit_not_a_miss(conn, monkeypatch):
    """The recipe worked and the library was read; we chose not to fetch any of
    it. Logging a miss would send the ladder on to SEARCH for documents we have
    just decided we do not want."""
    m = "ALLOUTco"
    g = _excluding_playbook(conn, monkeypatch, m, {".pdf": None})
    fetcher = ScriptedFetcher({
        f"https://{m.lower()}.example/robots.txt": _robots_allow(),
        f"https://{m.lower()}.example/lib": _html(body=_MIXED),
    })

    dh.handle_discover_group(conn, _job(g), fetcher=fetcher)

    row = _disc_log(conn, g)[0]
    assert row["outcome"] == "hit"
    assert row["detail"]["excluded"] == 4
    assert row["detail"]["emitted"] == 0
    assert _fetch_jobs(conn) == []


def test_a_positive_rule_does_not_change_what_is_fetched():
    """The positive half is a probe hint and nothing more: T0/T1 type a
    document from its own content, and overriding a reading of the page with a
    substring of a URL would be the opposite of invariant 2."""
    assert dh._excluded_by_type_rule("/d/ifu_x.pdf", {"ifu": "IFU"}) is None
    assert dh._excluded_by_type_rule("/d/sds_x.pdf", {"sds_": None}) == "sds_"
    assert dh._excluded_by_type_rule("/d/SDS_UPPER.pdf", {"sds_": None}) == "sds_"
    assert dh._excluded_by_type_rule("/d/x.pdf", None) is None
