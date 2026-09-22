"""discover.group — S1.3 DISCOVER (spec: docs/specs/discover.md, contract: PRD
§3 / handbook §3).

Source-priority ladder: recency guard -> known_url -> eudamed -> search (adapter
+ T1 rank + threshold) -> email -> manual. First hit emits `fetch.url` ×k and
stops; email/manual are terminal; recency/vendor are skips and `playbook`
serves authored `kind:"direct"` sources. Every rung
tried writes a discovery_log row.

Real Postgres (CLAUDE.md: no mocking). The SearchAdapter and the T1 ranker are
injected (FakeSearchAdapter + a stub `rank`) so no live HTTP/LLM call happens.
Import runner FIRST so the real handler wins over the S0.1 no-op (see
test_resolve_handler for the ordering rationale).
"""

from __future__ import annotations

import dataclasses

import pytest
from psycopg.types.json import Json

from app import queue
from app.workers import runner  # noqa: F401 - registers real handlers over no-ops
from app.adapters.search import FakeSearchAdapter, SearchCandidate
from app.config import load_config
from app.handlers import discover as dh
from app.urls import normalize_url


# --- seed helpers -----------------------------------------------------------

def _seed_group(conn, *, canonical="ACME", basic_udi_di=None, label="Widget"):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, basic_udi_di, label) "
        "VALUES (%s,%s,%s) RETURNING group_id",
        (canonical, basic_udi_di, label),
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


def _seed_known_url(conn, gid, item_ref, url, *, fresh, content_hash):
    """A prior fetched production doc for a member, with a fetch_log URL whose
    last_checked_at is either recent (fresh=True) or NULL (stale)."""
    _seed_member(conn, gid, item_ref)
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group',%s,'file:///d.pdf','production') "
        "RETURNING doc_id",
        (content_hash,),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')",
        (item_ref, doc_id),
    )
    checked = "now()" if fresh else "NULL"
    conn.execute(
        f"INSERT INTO fetch_log (url_normalized, content_hash, source, doc_id, last_checked_at) "
        f"VALUES (%s,%s,'live',%s,{checked})",
        (normalize_url(url), content_hash, doc_id),
    )


def _seed_eudamed(conn, basic_udi_di, cert_refs):
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, cert_refs, synced_at) "
        "VALUES (%s,%s,%s, now())",
        (f"udi-{basic_udi_di}", basic_udi_di, Json(cert_refs)),
    )


def _seed_manufacturer(conn, canonical, emails):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, contact_emails) VALUES (%s,%s)",
        (canonical, emails),
    )


# --- query + call helpers ---------------------------------------------------

def _job(gid, **extra):
    return {"id": 1, "type": "discover.group", "payload": {"group_id": gid, **extra}}


def _call(conn, gid, *, search_adapter=None, rank=None, **extra):
    return dh.handle_discover_group(
        conn, _job(gid, **extra), search_adapter=search_adapter, rank=rank
    )


def _fetch_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key, priority FROM job WHERE type='fetch.url' ORDER BY id"
    ).fetchall()


def _email_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='email.request' ORDER BY id"
    ).fetchall()


def _disc_log(conn, gid):
    return conn.execute(
        "SELECT source, outcome, detail FROM discovery_log WHERE group_id=%s ORDER BY id",
        (gid,),
    ).fetchall()


def _manual(conn, gid):
    return conn.execute(
        "SELECT payload, status FROM manual_task "
        "WHERE group_id=%s AND kind='discovery-dead-end' ORDER BY id",
        (gid,),
    ).fetchall()


def _rank_all(score=0.9):
    def _r(facts, candidates):
        return [(c, score) for c in candidates]
    return _r


# --- ladder rungs -----------------------------------------------------------

def test_stale_known_url_emits_fetch(conn):
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=False, content_hash="h1")
    res = _call(conn, g)

    fetches = _fetch_jobs(conn)
    assert len(fetches) == 1
    p = fetches[0]["payload"]
    assert p == {"url": "https://acme.com/doc.pdf", "domain": "acme.com",
                 "group_id": g, "source_rank": "known_url"}
    assert fetches[0]["dedupe_key"] == "fetch:https://acme.com/doc.pdf"
    assert res["terminal_source"] == "known_url" and res["outcome"] == "fetch"
    assert ("known_url", "hit") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]


def test_recency_fresh_falls_through_to_manual(conn):
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")
    res = _call(conn, g)

    logs = [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert ("recency", "skipped") in logs
    assert ("known_url", "miss") in logs   # fresh URL is not re-emitted
    assert _fetch_jobs(conn) == []
    assert len(_manual(conn, g)) == 1
    assert res["outcome"] == "manual"


def _validate_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='validate.doc' ORDER BY id"
    ).fetchall()


def test_recency_rung_links_a_fresh_extracted_known_url_via_validate(conn):
    # Defect fix 2026-08-24: the recency rung emitted neither fetch nor
    # validate, so a group whose document was already fetched, fresh AND
    # extracted ended its ladder with no link candidate — the C3 re-entry
    # existed only inside FETCH. The rung now emits the same validate.doc
    # FETCH's dedupe branch does, without the network, and is terminal.
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES ('h1','T1','{}'::jsonb, 2)")
    # a dead-end task from an earlier exhausted run is stale once the rung links
    conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload) "
        "VALUES ('discovery-dead-end', %s, '{}'::jsonb)", (g,))

    res = _call(conn, g)

    assert res["terminal_source"] == "recency" and res["outcome"] == "validate"
    assert res["emitted_validate"] == 1
    v = _validate_jobs(conn)
    assert len(v) == 1
    # archive_url is the registry's stored-copy handle (same C3 payload FETCH
    # emits since the archive-handle defect fix)
    assert v[0]["payload"] == {"content_hash": "h1", "extract_rev": 2, "group_id": g,
                               "archive_url": "file:///d.pdf"}
    assert v[0]["dedupe_key"] == f"validate:h1:2:{g}"
    assert _fetch_jobs(conn) == []
    assert ("recency", "hit") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert [t["status"] for t in _manual(conn, g)] == ["resolved"]


def test_recency_rung_links_a_group_via_a_ledgered_playbook_direct_url(conn, monkeypatch):
    # Measured 2026-08-24: RENFERT groups 3138/3139/3140/5254 ran discover
    # AFTER the playbook's direct URL was ledgered and extracted (another
    # group's chain archived doc 843). Their members hold no links yet, so
    # the known-url set is empty, the playbook rung's fetch.url deduped
    # against the still-active fetch for the same URL (emitted_fetch 0), and
    # the chain ended silently: no validate.doc, no link to doc 843, no
    # manual task. A ledger-fresh, already-extracted direct URL is C3: emit
    # validate.doc for THIS group and stop.
    from app.playbooks import DocSource, Playbook

    g = _seed_group(conn, canonical="RENFERT")
    _seed_member(conn, g, "IT-R1")
    pb = Playbook(slug="renfert", manufacturer="RENFERT",
                  doc_sources=(DocSource(doc_type="DoC", kind="direct",
                                         url="https://renfert.example/CONF_6100x000.pdf"),))
    monkeypatch.setattr(dh.playbooks_mod, "for_manufacturer", lambda _m: pb)

    handle = "/archive/RENFERT/unknown/d82e8f794d0c__CONF_6100x000.pdf"
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES (%s,'HREN','live', now(), now())",
        (normalize_url("https://renfert.example/CONF_6100x000.pdf"),))
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES ('HREN','T1','{}'::jsonb, 1)")
    conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','HREN',%s,'staged')", (handle,))

    res = _call(conn, g)

    assert res["terminal_source"] == "recency" and res["outcome"] == "validate"
    assert res["emitted_validate"] == 1
    v = _validate_jobs(conn)
    assert len(v) == 1
    assert v[0]["payload"] == {"content_hash": "HREN", "extract_rev": 1, "group_id": g,
                               "archive_url": handle}
    assert _fetch_jobs(conn) == []
    assert _manual(conn, g) == []


def test_recency_rung_still_skips_when_the_fresh_hash_is_unextracted(conn):
    # Fresh but not yet extracted (extract in flight): the rung stays the
    # guard it always was — log `skipped`, fall through, self-heal next cycle.
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")

    res = _call(conn, g)

    assert _validate_jobs(conn) == []
    assert res["outcome"] == "manual"
    assert ("recency", "skipped") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]


def test_ignore_recency_bypasses_the_recency_c3_link(conn):
    # Admin refetch ([admin-refetch-item]) must reach the network even when
    # the fresh ledger row is already extracted: the known row is forced
    # stale and emitted as fetch.url; identical bytes still dedupe inside
    # FETCH (its own C3), never here.
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES ('h1','T1','{}'::jsonb, 1)")

    res = _call(conn, g, ignore_recency=True)

    assert _validate_jobs(conn) == []
    assert res["terminal_source"] == "known_url" and res["outcome"] == "fetch"
    assert len(_fetch_jobs(conn)) == 1


def test_stale_upload_pseudo_url_never_emitted_as_fetch(conn):
    # A web /upload records a synthetic `upload:{hash}` fetch_log row (source='upload')
    # that httpx cannot fetch. Once linked to a group doc and gone stale, it must NOT
    # enter the known_url rung's fetch set — else DISCOVER emits a doomed fetch.url
    # (dead-letters on the unsupported scheme) and short-circuits the ladder on a
    # false "hit". Reachable via the /manual dead-end -> upload-of-an-already-held-doc.
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','h1','file:///d.pdf','staged') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('IT-1',%s,'fetch-context','staged')",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, doc_id, last_checked_at) "
        "VALUES ('upload:h1','h1','upload',%s,NULL)",   # stale (last_checked_at NULL)
        (doc_id,),
    )
    res = _call(conn, g)

    assert _fetch_jobs(conn) == []   # synthetic upload URL never fetched
    logs = [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert ("known_url", "miss") in logs   # upload row excluded from the known set
    assert res["outcome"] == "manual"


def test_eudamed_hit_emits_fetch(conn):
    g = _seed_group(conn, basic_udi_di="BUDI-1")
    _seed_member(conn, g, "IT-1")
    _seed_eudamed(conn, "BUDI-1", ["https://eudamed.eu/cert1.pdf"])
    res = _call(conn, g)

    fetches = _fetch_jobs(conn)
    assert len(fetches) == 1
    assert fetches[0]["payload"]["source_rank"] == "eudamed"
    assert fetches[0]["payload"]["url"] == "https://eudamed.eu/cert1.pdf"
    assert res["terminal_source"] == "eudamed"


def test_search_ranked_hits_emit_topk(conn):
    g = _seed_group(conn, label="Tetric")
    _seed_member(conn, g, "IT-1")
    cands = [
        SearchCandidate(url="https://a.com/1.pdf", title="DoC", snippet="", rank=0),
        SearchCandidate(url="https://a.com/2.pdf", title="DoC", snippet="", rank=1),
        SearchCandidate(url="https://a.com/3.pdf", title="junk", snippet="", rank=2),
    ]

    def _rank_mixed(facts, candidates):
        return list(zip(candidates, [0.9, 0.8, 0.1]))   # 3rd below 0.60 threshold

    res = _call(conn, g, search_adapter=FakeSearchAdapter(cands), rank=_rank_mixed)

    urls = [f["payload"]["url"] for f in _fetch_jobs(conn)]
    assert urls == ["https://a.com/1.pdf", "https://a.com/2.pdf"]
    assert all(f["payload"]["source_rank"] == "search" for f in _fetch_jobs(conn))
    assert res["candidates_seen"] == 3
    detail = [r["detail"] for r in _disc_log(conn, g) if r["source"] == "search"][0]
    assert detail["n_ranked"] == 2


def test_a_failing_rank_degrades_to_manual(conn):
    """The ranker went live 2026-09-02 (`[discover-t1-ranking]`), so this no
    longer exercises a deferred default that raises by design — it exercises the
    degrade path that default used to stand in for, and which now carries every
    real ranker failure: no API key, an HTTP error, a truncated response.

    The failure is INJECTED rather than left to `_default_rank`. Calling the
    default here would reach the live Anthropic API: the test container carries
    a real ANTHROPIC_API_KEY, and this test spent money on Haiku exactly once
    before that was noticed."""
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    cands = [SearchCandidate(url="https://a.com/1.pdf", title="DoC", snippet="", rank=0)]

    def _rank_boom(facts, candidates):
        raise RuntimeError("ranker unavailable")

    res = _call(conn, g, search_adapter=FakeSearchAdapter(cands), rank=_rank_boom)

    assert _fetch_jobs(conn) == []
    search_log = [r for r in _disc_log(conn, g) if r["source"] == "search"][0]
    assert search_log["outcome"] == "miss"
    assert "rank_error" in search_log["detail"]
    assert len(_manual(conn, g)) == 1
    assert res["outcome"] == "manual"


def _enable_email_rung(monkeypatch):
    """The email rung's producer side is gated off by default until the S2.4
    EMAIL handler exists (jobs would land on the no-op and vanish as `done`)."""
    base = load_config()
    cfg = dataclasses.replace(
        base, discovery=dataclasses.replace(base.discovery, email_rung_enabled=True))
    monkeypatch.setattr(dh, "load_config", lambda: cfg)


def test_email_when_contact_known(conn, monkeypatch):
    _enable_email_rung(monkeypatch)
    g = _seed_group(conn, canonical="ACME")
    _seed_member(conn, g, "IT-1")
    _seed_manufacturer(conn, "ACME", ["quality@acme.com"])
    # no known url, empty eudamed, search returns nothing
    res = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    emails = _email_jobs(conn)
    assert len(emails) == 1
    assert emails[0]["payload"]["group_id"] == g
    assert emails[0]["dedupe_key"] == f"email.request:group:{g}"
    assert _manual(conn, g) == []   # email pre-empts manual
    assert res["outcome"] == "email"


def test_all_miss_no_contact_pushes_manual_with_prefilled_links(conn):
    g = _seed_group(conn, canonical="ACME", label="Widget")
    _seed_member(conn, g, "IT-1")
    res = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    tasks = _manual(conn, g)
    assert len(tasks) == 1
    links = tasks[0]["payload"]["prefilled_search_links"]
    assert links and all(l.startswith("http") for l in links)
    assert tasks[0]["payload"]["manufacturer"] == "ACME"
    assert res["outcome"] == "manual" and res["manual_task"] is True


def test_ignore_recency_turns_a_fresh_url_into_a_fetch(conn):
    """A ledger-fresh known URL is normally a recency skip -> manual
    (test_recency_fresh_falls_through_to_manual). With ignore_recency the same
    row must be treated as stale: emitted as fetch.url, and that child payload
    must itself carry the flag so FETCH bypasses its own recency skip too."""
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")
    res = _call(conn, g, ignore_recency=True)

    fetches = _fetch_jobs(conn)
    assert len(fetches) == 1
    p = fetches[0]["payload"]
    assert p["url"] == "https://acme.com/doc.pdf"
    assert p["ignore_recency"] is True
    assert res["terminal_source"] == "known_url" and res["outcome"] == "fetch"
    logs = [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert ("recency", "skipped") not in logs   # forced stale -> no fresh-skip logged


def test_missing_flag_behaves_exactly_as_before(conn):
    """Additive-only pin: omitting ignore_recency must be byte-identical to
    passing it explicitly False -- both leave a fresh known URL as a recency
    skip -> manual, never emitted as fetch.url."""
    g1 = _seed_group(conn, canonical="A1")
    _seed_known_url(conn, g1, "IT-1", "https://acme.com/doc.pdf", fresh=True, content_hash="h1")
    res_missing = _call(conn, g1)

    g2 = _seed_group(conn, canonical="A2")
    _seed_known_url(conn, g2, "IT-2", "https://acme2.com/doc.pdf", fresh=True, content_hash="h2")
    res_explicit = _call(conn, g2, ignore_recency=False)

    assert res_missing["outcome"] == "manual"
    assert res_explicit["outcome"] == "manual"
    assert _fetch_jobs(conn) == []


def test_missing_group_raises_lookup(conn):
    with pytest.raises(LookupError):
        _call(conn, 999999)


def test_extra_payload_fields_tolerated(conn):
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=False, content_hash="h1")
    # RESOLVE emits bare {group_id}; a producer adding manufacturer_id/context must not break it
    res = _call(conn, g, manufacturer_id=7, context={"foo": "bar"})
    assert res["terminal_source"] == "known_url"


def test_redelivery_does_not_duplicate_manual_task(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())
    res2 = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    assert len(_manual(conn, g)) == 1          # guard: no second dead-end task
    assert res2["manual_task"] is False        # reported as not newly written


def test_dead_end_task_stays_open_when_still_manual(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())
    res2 = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    assert res2["outcome"] == "manual"
    assert _manual(conn, g)[0]["status"] == "open"


def test_dead_end_task_resolved_when_later_run_hits_known_url(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())
    assert _manual(conn, g)[0]["status"] == "open"

    # FETCH has since recorded a stale known_url for the group. IT-1 is already
    # a member (item_group_member has no ON CONFLICT), so seed doc/link/fetch_log
    # directly rather than via _seed_known_url (which re-inserts the member).
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','h-dead-end','file:///d.pdf','production') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('IT-1', %s, 'ref-list', 'production')", (doc_id,)
    )
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, doc_id, last_checked_at) "
        "VALUES ('https://acme.com/doc.pdf', 'h-dead-end', 'live', %s, NULL)", (doc_id,)
    )

    res = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    assert res["terminal_source"] == "known_url"
    assert _manual(conn, g)[0]["status"] == "resolved"


def test_dead_end_task_resolved_when_later_run_hits_search(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())
    assert _manual(conn, g)[0]["status"] == "open"

    cands = [SearchCandidate(url="https://a.com/1.pdf", title="DoC", snippet="", rank=0)]
    res = _call(conn, g, search_adapter=FakeSearchAdapter(cands), rank=_rank_all())

    assert res["terminal_source"] == "search"
    assert _manual(conn, g)[0]["status"] == "resolved"


def test_email_rung_disabled_by_default_falls_through_to_manual(conn):
    # No EMAIL handler exists until S2.4 — with the flag at its default (off),
    # a known contact must NOT produce an email.request (it would be swallowed
    # by the no-op); the rung logs `skipped` and the group lands in manual.
    g = _seed_group(conn, canonical="ACME")
    _seed_member(conn, g, "IT-1")
    _seed_manufacturer(conn, "ACME", ["quality@acme.com"])
    res = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    assert _email_jobs(conn) == []
    email_log = [r for r in _disc_log(conn, g) if r["source"] == "email"][0]
    assert email_log["outcome"] == "skipped"
    assert res["outcome"] == "manual"
    assert len(_manual(conn, g)) == 1   # work is never lost


def test_dead_end_task_resolved_when_later_run_hits_email(conn, monkeypatch):
    _enable_email_rung(monkeypatch)
    g = _seed_group(conn, canonical="ACME")
    _seed_member(conn, g, "IT-1")
    _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())
    assert _manual(conn, g)[0]["status"] == "open"

    _seed_manufacturer(conn, "ACME", ["quality@acme.com"])
    res = _call(conn, g, search_adapter=FakeSearchAdapter([]), rank=_rank_all())

    assert res["terminal_source"] == "email"
    assert _manual(conn, g)[0]["status"] == "resolved"


def test_search_adapter_error_propagates(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1")
    boom = FakeSearchAdapter(error=RuntimeError("brave down"))
    with pytest.raises(RuntimeError, match="brave down"):
        _call(conn, g, search_adapter=boom, rank=_rank_all())


def test_same_url_from_two_groups_dedupes_fetch(conn):
    g1 = _seed_group(conn, label="A")
    g2 = _seed_group(conn, label="B")
    _seed_member(conn, g1, "IT-1")
    _seed_member(conn, g2, "IT-2")
    same = [SearchCandidate(url="https://a.com/shared.pdf", title="DoC", snippet="", rank=0)]
    _call(conn, g1, search_adapter=FakeSearchAdapter(same), rank=_rank_all())
    _call(conn, g2, search_adapter=FakeSearchAdapter(same), rank=_rank_all())

    assert len(_fetch_jobs(conn)) == 1   # active-scope dedupe on fetch:{url_normalized}


def test_configured_vendor_rung_is_skipped(conn, monkeypatch):
    """`vendor` stays folded into `search` (G6). `playbook` is no longer a skip --
    it serves authored `kind:"direct"` sources now, and logs hit/miss instead
    (test_playbook_rung_emits_authored_direct_urls)."""
    g = _seed_group(conn, canonical="PBco")
    _seed_member(conn, g, "IT-1")
    base = load_config()
    cfg = dataclasses.replace(base, source_priority={"PBco": ["playbook", "vendor", "manual"]})
    monkeypatch.setattr(dh, "load_config", lambda: cfg)

    res = _call(conn, g)
    logs = [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    assert ("vendor", "skipped") in logs
    assert ("playbook", "miss") in logs
    assert res["outcome"] == "manual"


def test_source_priority_falls_back_to_default(conn):
    cfg = load_config()
    assert dh._source_priority(cfg, "Unknownco") == cfg.discovery.default_source_priority
    cfg2 = dataclasses.replace(cfg, source_priority={"Known": ["search", "manual"]})
    assert dh._source_priority(cfg2, "Known") == ["search", "manual"]


def test_end_to_end_through_runner(conn):
    """End-to-end via run_once with the live HANDLERS registry. No commit —
    same-conn uncommitted visibility keeps it inside the fixture's rolled-back
    transaction (mirrors the RESOLVE e2e test), so nothing leaks to later tests."""
    g = _seed_group(conn)
    _seed_known_url(conn, g, "IT-1", "https://acme.com/doc.pdf", fresh=False, content_hash="h1")
    jid = queue.enqueue(conn, "discover.group", {"group_id": g}, f"discover:{g}:0")

    assert runner.run_once(conn, "w-e2e") is True
    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (jid,)
    ).fetchone()["status"] == "done"
    assert len(_fetch_jobs(conn)) == 1
    assert _disc_log(conn, g)


# --------------------------------------------------------------------------- #
# playbook domains + portal prefill (S1.7)
# --------------------------------------------------------------------------- #
def _candidate(url, rank=0):
    return SearchCandidate(url=url, title="t", snippet="s", rank=rank)


def _voco_playbook():
    from app.playbooks import DocSource, Playbook

    return Playbook(
        slug="voco",
        manufacturer="VOCO GmbH",
        domains=("voco.dental", "voco.de"),
        doc_sources=(
            DocSource(doc_type="IFU", kind="portal", url="https://voco.dental/downloads"),
            DocSource(doc_type="DoC", kind="direct", url="https://voco.dental/a.pdf"),
        ),
    )


def _facts(manufacturer="VOCO GmbH", label="Grandio"):
    from app.handlers.discover import GroupFacts

    return GroupFacts(
        group_id=1, manufacturer=manufacturer, label=label,
        basic_udi_di=None, member_mfr_refs=[],
    )


def test_query_never_quotes_the_label():
    """[discover-query-exact-matches-slovene-label]: quoting facts.label made
    it a required exact phrase. facts.label is Dentalia's internal BC product
    description and never appears verbatim on a manufacturer's site, so the
    query was unsatisfiable and the search rung returned zero every time.
    Never quote it, regardless of what it contains."""
    from app.handlers.discover import _query_for

    q = _query_for(_facts(label="NASTAVEK VARIOS E15D"), None)

    assert '"' not in q


def test_query_drops_a_pure_category_prose_label():
    """A label that is nothing but a Slovene category noun plus generic
    descriptors (size, material, quantity, marketing words) carries no
    distinguishing product name, so it is dropped from the query entirely
    rather than kept as noise."""
    from app.handlers.discover import _query_for

    q = _query_for(
        _facts(manufacturer="HENRY SCHEIN",
               label="ROKAVICE L NEPUD. PREMIUM 100KOS HS"),
        None,
    )

    assert q == "HENRY SCHEIN declaration of conformity pdf"


def test_query_keeps_a_label_carrying_a_real_product_token():
    """A category-noun-led label that also carries a real product/model
    token (brand family name or catalogue code) keeps the whole label,
    unquoted."""
    from app.handlers.discover import _query_for

    q = _query_for(_facts(manufacturer="KAVO", label="NASTAVEK VARIOS E15D"), None)

    assert q == "KAVO NASTAVEK VARIOS E15D declaration of conformity pdf"


def test_query_keeps_a_clean_product_name_label_unquoted():
    from app.handlers.discover import _query_for

    q = _query_for(_facts(manufacturer="VOCO GmbH", label="Grandio"), None)

    assert q == "VOCO GmbH Grandio declaration of conformity pdf"


def test_query_with_no_label():
    from app.handlers.discover import _query_for

    q = _query_for(_facts(manufacturer="ACME", label=None), None)

    assert q == "ACME declaration of conformity pdf"


def test_query_is_site_restricted_when_domains_known():
    from app.handlers.discover import _query_for

    q = _query_for(_facts(), _voco_playbook())

    assert "site:voco.dental" in q
    assert "site:voco.de" in q
    assert "declaration of conformity" in q


def test_query_unchanged_without_a_playbook():
    from app.handlers.discover import _query_for

    assert _query_for(_facts(), None) == "VOCO GmbH Grandio declaration of conformity pdf"


def test_query_unchanged_when_playbook_has_no_domains():
    from app.playbooks import Playbook
    from app.handlers.discover import _query_for

    pb = Playbook(slug="x", manufacturer="VOCO GmbH")

    assert _query_for(_facts(), pb) == "VOCO GmbH Grandio declaration of conformity pdf"


def test_prefill_puts_portal_urls_first():
    from app.handlers.discover import _prefilled_search_links

    links = _prefilled_search_links(_facts(), _voco_playbook())

    assert links[0] == "https://voco.dental/downloads"
    assert "https://voco.dental/a.pdf" not in links   # direct sources are not prefill
    assert any("google.com/search" in u for u in links)


def test_prefill_unchanged_without_a_playbook():
    from app.handlers.discover import _prefilled_search_links

    links = _prefilled_search_links(_facts(), None)

    assert all(u.startswith("https://www.google.com") or u.startswith("https://search.brave.com")
               for u in links)


def test_search_retries_unrestricted_when_restricted_finds_nothing(conn):
    """Compliance PDFs often sit on a CDN or a subsidiary domain, so a
    site:-restricted query that returns nothing must not end the rung."""
    from app.handlers.discover import _search
    from app.config import load_config

    queries = []

    class _Adapter:
        def query(self, q):
            queries.append(q)
            return [] if "site:" in q else [_candidate("https://cdn.example/x.pdf")]

    urls, detail = _search(
        conn, _facts(), load_config(), _Adapter(), lambda f, c: [(c[0], 0.99)],
        playbook=_voco_playbook(),
    )

    assert len(queries) == 2
    assert "site:" in queries[0] and "site:" not in queries[1]
    assert detail["restricted_miss"] is True
    assert urls == ["https://cdn.example/x.pdf"]


def test_search_does_not_retry_when_restricted_query_hits(conn):
    from app.handlers.discover import _search
    from app.config import load_config

    queries = []

    class _Adapter:
        def query(self, q):
            queries.append(q)
            return [_candidate("https://voco.dental/x.pdf")]

    urls, detail = _search(
        conn, _facts(), load_config(), _Adapter(), lambda f, c: [(c[0], 0.99)],
        playbook=_voco_playbook(),
    )

    assert len(queries) == 1
    assert detail.get("restricted_miss") is not True


def _write_playbook(dir_path, filename, data):
    import json

    (dir_path / filename).write_text(json.dumps(data))


def test_handle_discover_group_end_to_end_uses_real_playbook(conn, tmp_path, monkeypatch):
    """Finding 3 (final review): every other test in this file drives
    `_query_for`, `_prefilled_search_links` and `_search` directly with a
    hand-built `Playbook` -- none of them exercise the three call sites inside
    `handle_discover_group` that actually thread the playbook through from
    `playbooks_mod.for_manufacturer(facts.manufacturer)`. Replacing that line
    with `playbook = None` left every other test green (mutation-tested, see
    final-fix-report.md), so this drives the handler itself for a
    manufacturer that HAS a playbook -- loaded from a real, monkeypatched
    `PLAYBOOKS_DIR` (same pattern as `test_playbooks_sync.py`'s
    `cmd_playbooks` tests) -- and asserts both consumers reached production
    behaviour:

      - the site:-restricted query reached the search adapter, persisted in
        the SAME `discovery_log` row as the unrestricted retry (both attempts
        visible in one persisted row, not just `_search`'s in-memory return
        value);
      - the portal URL landed first in the dead-end `manual_task` prefill.
    """
    from app import playbooks as playbooks_mod

    _write_playbook(tmp_path, "acme.json", {
        "manufacturer": "ACME Corp",
        "domains": ["acme.example"],
        "doc_sources": [
            {"doc_type": "DoC", "kind": "portal", "url": "https://acme.example/downloads"}
        ],
    })
    monkeypatch.setattr(playbooks_mod, "PLAYBOOKS_DIR", tmp_path)

    g = _seed_group(conn, canonical="ACME Corp", label="Widget")
    _seed_member(conn, g, "IT-1")

    queries = []

    class _Adapter:
        def query(self, q):
            queries.append(q)
            return []   # both the restricted attempt and the unrestricted retry miss

    res = dh.handle_discover_group(
        conn, _job(g), search_adapter=_Adapter(), rank=_rank_all()
    )

    assert res["outcome"] == "manual"
    assert len(queries) == 2
    assert "site:acme.example" in queries[0]
    assert "site:" not in queries[1]

    search_row = [r for r in _disc_log(conn, g) if r["source"] == "search"][0]
    assert "site:acme.example" in search_row["detail"]["query"]
    assert "site:" not in search_row["detail"]["query_retry"]

    tasks = _manual(conn, g)
    assert len(tasks) == 1
    assert tasks[0]["payload"]["prefilled_search_links"][0] == "https://acme.example/downloads"


def test_playbook_source_priority_wins_over_config():
    """Per-manufacturer data belongs in the per-manufacturer file. config.py has
    described its own `[source_priority]` map as a placeholder for this since it
    was written."""
    from app.playbooks import Playbook

    cfg = dataclasses.replace(load_config(), source_priority={"VOCO": ["eudamed", "manual"]})
    pb = Playbook(slug="voco", manufacturer="VOCO",
                  source_priority=("known_url", "manual"))

    assert dh._source_priority(cfg, "VOCO", pb) == ["known_url", "manual"]


def test_config_map_still_serves_a_manufacturer_with_no_playbook_entry():
    from app.playbooks import Playbook

    cfg = dataclasses.replace(load_config(), source_priority={"VOCO": ["eudamed", "manual"]})
    pb = Playbook(slug="voco", manufacturer="VOCO")     # no source_priority authored

    assert dh._source_priority(cfg, "VOCO", pb) == ["eudamed", "manual"]


def test_default_ladder_when_neither_names_the_manufacturer():
    cfg = load_config()

    assert dh._source_priority(cfg, "UNKNOWN AG", None) == \
        cfg.discovery.default_source_priority


def test_playbook_rung_emits_authored_direct_urls(conn, monkeypatch):
    """`kind:"direct"` was parsed since S1.7 and read by nothing. It is the
    cheapest discovery there is: an operator already wrote down where the
    document lives."""
    from app.playbooks import DocSource, Playbook

    g = _seed_group(conn, canonical="DIRECTco")
    _seed_member(conn, g, "IT-D1")
    pb = Playbook(
        slug="directco", manufacturer="DIRECTco",
        doc_sources=(
            DocSource(doc_type="DoC", kind="direct", url="https://directco.example/a.pdf"),
            DocSource(doc_type="IFU", kind="portal", url="https://directco.example/downloads"),
        ),
    )
    monkeypatch.setattr(dh.playbooks_mod, "for_manufacturer", lambda _m: pb)
    cfg = dataclasses.replace(load_config(), source_priority={"DIRECTco": ["playbook", "manual"]})
    monkeypatch.setattr(dh, "load_config", lambda: cfg)

    res = _call(conn, g)

    assert res["terminal_source"] == "playbook"
    assert res["emitted_fetch"] == 1
    assert ("playbook", "hit") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]
    urls = [r["payload"]["url"] for r in conn.execute(
        "SELECT payload FROM job WHERE type='fetch.url'").fetchall()]
    assert "https://directco.example/a.pdf" in urls


def test_the_playbook_rung_never_fetches_a_portal(conn, monkeypatch):
    """A portal is a LISTING page. Fetching one archives HTML that no extractor
    reads, and for the four robots-refused manufacturers `kind` is the whole
    enforcement mechanism (docs/2026-08-20-robots-blocked-manufacturers.md):
    portal means a human clicks it, and nothing else may."""
    from app.playbooks import DocSource, Playbook

    g = _seed_group(conn, canonical="PORTALco")
    _seed_member(conn, g, "IT-P1")
    pb = Playbook(slug="portalco", manufacturer="PORTALco",
                  doc_sources=(DocSource(doc_type="DoC", kind="portal",
                                         url="https://portalco.example/downloads"),))
    monkeypatch.setattr(dh.playbooks_mod, "for_manufacturer", lambda _m: pb)
    cfg = dataclasses.replace(load_config(), source_priority={"PORTALco": ["playbook", "manual"]})
    monkeypatch.setattr(dh, "load_config", lambda: cfg)

    res = _call(conn, g)

    assert res["outcome"] == "manual"          # fell through to the terminal rung
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='fetch.url'").fetchone()["n"] == 0
    assert ("playbook", "miss") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]


def test_no_playbook_leaves_the_rung_a_miss(conn, monkeypatch):
    g = _seed_group(conn, canonical="NOPBco")
    _seed_member(conn, g, "IT-N1")
    monkeypatch.setattr(dh.playbooks_mod, "for_manufacturer", lambda _m: None)
    cfg = dataclasses.replace(load_config(), source_priority={"NOPBco": ["playbook", "manual"]})
    monkeypatch.setattr(dh, "load_config", lambda: cfg)

    res = _call(conn, g)

    assert res["outcome"] == "manual"
    assert ("playbook", "miss") in [(r["source"], r["outcome"]) for r in _disc_log(conn, g)]


def test_a_path_scoped_domain_reaches_the_query_intact():
    """Medentika's documents are at straumann.com/medentika; straumann.com is
    another playbook's domain. Verified against the live Brave API on
    2026-08-21: the path-scoped token is honoured, and it surfaces
    .../medentika/en/dental-professionals/downloads/certificates.html, which the
    cad.medentika.com-only query never returns."""
    from app.playbooks import Playbook

    class _Facts:
        manufacturer, label, group_id = "MEDENTIKA", "Ti-Base", 1

    pb = Playbook(slug="medentika", manufacturer="MEDENTIKA",
                  domains=("cad.medentika.com", "straumann.com/medentika"))

    q = dh._query_for(_Facts(), pb)

    assert "site:straumann.com/medentika" in q
    assert "site:straumann.com)" not in q and "site:straumann.com " not in q
