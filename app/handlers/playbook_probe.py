"""`playbook.reonboard` — look at a manufacturer's document library, write down
what is there, and emit nothing.

Spec: `docs/superpowers/specs/2026-09-03-playbook-probe-wizard-design.md`.

This is the one consumer of `app.crawl` that produces no `fetch.url`, and that
asymmetry is the whole point. An operator authoring a crawl recipe has to be
able to SEE what a library yields before committing the pipeline to fetch it --
NSK's index alone is 1.329 PDFs. Authoring blind and finding out afterwards
spends a manufacturer's goodwill and our extraction budget on a guess.

Why this tag rather than a new one: `playbook.reonboard` has been in the closed
enum since `migrations/001_queue.sql` and was the last unbuilt one. The design
always meant one path with two triggers -- the ground-truth doc says a frontier
agent "explores each site once at onboarding, drafts the playbook, human
approves... Runs at onboarding and on reonboard trigger". So invariant 7 is
satisfied by construction: no new tag, no PRD change, no migration for the enum.

Why not a `fetch.url` with a flag: that handler writes a fetch-ledger row, and
invariant 6 would then treat the index URL as already fetched -- silently
suppressing the real crawl later. A probe must leave no trace in the ledger and
nothing in the archive.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Json

from app import crawl as crawl_mod
from app import playbooks as playbooks_mod
from app import queue
from app import robots as robots_mod
from app.adapters.fetcher import make_fetcher
from app.config import load_config
from app.handlers import register
from app.urls import domain_of

log = logging.getLogger("dentalia.handler.playbook_probe")

#: How many resolved links a probe row keeps. A probe of a 1.329-link index must
#: not put 1.329 of someone else's URLs in our database to show an operator
#: twenty. The COUNTS are complete; the sample is a sample, and the UI says so.
SAMPLE_LIMIT = 20


def _finish(conn, probe_id: int, status: str, **cols) -> dict:
    sets = ", ".join(f"{k}=%s" for k in cols)
    conn.execute(
        f"UPDATE playbook_probe SET status=%s, finished_at=now()"
        + (f", {sets}" if cols else "")
        + " WHERE id=%s",
        (status, *cols.values(), probe_id),
    )
    # `getattr(v, "obj", v)` unwraps psycopg's `Json` adapter. The runner
    # serialises whatever a handler returns into `job.result`, and a `Json`
    # instance raises `TypeError: Object of type Json is not JSON
    # serializable` there -- AFTER the handler has done its work, so the
    # transaction rolls back, the probe row vanishes and the job retries,
    # fetching the manufacturer's page again on every attempt. Caught live
    # against NSK on 2026-09-04 (job 37728, three real fetches).
    #
    # `sample` is dropped rather than unwrapped: it is up to SAMPLE_LIMIT of
    # somebody else's URLs and belongs in the row, not in every job result.
    return {"probe_id": probe_id, "status": status,
            **{k: getattr(v, "obj", v) for k, v in cols.items() if k != "sample"}}


def _classify(url: str, doc_type_from: dict | None) -> str | None:
    """What a harvested link READS AS, from its URL alone.

    A guess, and the UI must present it as one -- filenames lie. `None` means
    either "no rule matched" or an authored EXCLUSION (`{"sds_": null}`), and
    the two are deliberately not distinguished here: both mean "do not claim a
    type", and the counts show the unclassified bucket either way.
    """
    if not doc_type_from:
        return None
    low = url.casefold()
    for needle, doc_type in doc_type_from.items():
        if needle.casefold() in low:
            return doc_type
    return None


def handle_playbook_reonboard(conn, job: dict, *, fetcher=None, renderer=None) -> dict:
    """Probe one index URL. Writes a `playbook_probe` row and emits nothing.

    Branches on the payload: `index_url` means probe. The failure-monitor
    reonboard path (payload `manufacturer` alone) is still unbuilt and RAISES
    rather than reporting success -- a half-built handler that silently no-ops
    is worse than the placeholder it replaced.
    """
    p = job["payload"]
    if not p.get("index_url"):
        raise NotImplementedError(
            "playbook.reonboard: the failure-monitor path is not built. This "
            "handler serves the UI probe only (payload needs `index_url`); a "
            "reonboard job enqueued by the scheduler has nothing to run yet, "
            "and dead-lettering says so instead of reporting success."
        )

    slug = p["slug"]                      # missing -> KeyError -> fail (loud)
    index_url = p["index_url"]
    cfg = load_config()

    # One row per JOB, not one per attempt. The lease branch below defers, and
    # the runner commits a deferred handler's writes (that is what keeps
    # `batch_ref` honest) -- so a plain INSERT here left a second `pending` row
    # behind on every politeness deferral, and the UI polling by `via_job`
    # would have had two rows to choose between. Reused rather than
    # ON CONFLICT because `via_job` is a soft ref (invariant 10) and must not
    # grow a unique constraint that a future non-probe writer would trip over.
    existing = conn.execute(
        "SELECT id FROM playbook_probe WHERE via_job=%s ORDER BY id LIMIT 1",
        (job["id"],)).fetchone()
    if existing:
        probe_id = existing["id"]
    else:
        probe_id = conn.execute(
            "INSERT INTO playbook_probe (slug, index_url, requested_by, via_job, status) "
            "VALUES (%s,%s,%s,%s,'pending') RETURNING id",
            (slug, index_url, p.get("requested_by"), job["id"]),
        ).fetchone()["id"]

    live_fetcher = fetcher if fetcher is not None else make_fetcher(cfg)

    # A probe is a fetch, so every fetch rule binds. Robots FIRST and refuse
    # rather than fetch -- and the authoring guard applies to `index_url` too: a
    # refused host cannot be probed, not merely cannot be saved.
    if playbooks_mod.is_refused(domain_of(index_url),
                               playbooks_mod.load_robots_refused()):
        return _finish(conn, probe_id, "refused", robots_verdict="disallow",
                       error="host is on playbooks/robots_refused.txt")

    decision = robots_mod.check(conn, index_url, fetcher=live_fetcher)
    if not decision.allowed:
        return _finish(conn, probe_id, "refused",
                       robots_verdict=f"disallow ({decision.reason})")

    # The domain lease, exactly as FETCH and the crawl rung take it. A human
    # watching a spinner is not a reason to jump the politeness queue.
    domain = domain_of(index_url)
    if not queue.try_domain_lease(conn, domain, cfg.fetch.politeness_ms):
        queue.defer(conn, job["id"], max(1, cfg.fetch.politeness_ms // 1000))
        # The row stays `pending`: the job will run again and finish it.
        return {"probe_id": probe_id, "status": "pending", "_deferred": True}

    try:
        resp = live_fetcher.get(index_url)
        body = getattr(resp, "body", b"") or b""
        html = body.decode("utf-8", "replace")
    except Exception as exc:                        # noqa: BLE001
        # A manufacturer's site being down is a result, not a crash. The
        # operator needs to see "we asked and could not reach it".
        return _finish(conn, probe_id, "failed", robots_verdict="allow",
                       error=f"{type(exc).__name__}: {exc}"[:400])

    harvest_args = dict(
        link_pattern=p.get("link_pattern") or r"\.pdf$",
        same_host_only=p.get("same_host_only", True),
        allow_hosts=tuple(p.get("allow_hosts") or ()),
        max_links=p.get("max_links") or 200,
    )
    result = crawl_mod.harvest(html, base_url=index_url, **harvest_args)
    tier = "static"

    # Browser escalation, the SAME trigger the crawl rung uses (§4.2, ruled
    # 2026-08-24, `discover._crawl_playbook`): zero ANCHORS, never zero
    # matches. The distinction is the whole point. A page that has anchors and
    # matched none of them has a wrong `link_pattern`, and a browser renders
    # the same anchors and still matches none -- 30s spent to reprint the same
    # number. A page with NO anchors at all is the one that might be a
    # JavaScript shell, and until now the probe could only ever report
    # `tier="static"`, so an operator staring at "0 anchors, 0 kept" had no way
    # to learn that a browser was the missing thing -- which is what
    # `playbook_probe.tier` exists to make measured rather than guessed
    # (`[probe-browser-tier]`, UI audit 2026-09-04).
    #
    # `_probe.html` has read `probe.tier` since S2.2 and says "Read with a
    # browser" or "a plain request"; it simply could never say the first.
    if result.anchors_seen == 0:
        live_renderer = renderer
        if live_renderer is None:
            # Constructed here, on escalation, exactly as the crawl rung and
            # app/handlers/fetch.py do it: `PlaywrightFetcher()` performs no
            # I/O, only `.render()` launches a browser. Nothing downstream
            # learns which implementation is live (invariant 11).
            from app.adapters.fetcher import PlaywrightFetcher
            live_renderer = PlaywrightFetcher()
        try:
            rendered_html = live_renderer.render(index_url)
        except Exception:                           # noqa: BLE001
            # A render fault degrades to the static result already computed.
            # `tier` stays "static" because the answer on screen IS the static
            # one -- claiming "rendered" would tell the operator we looked with
            # a browser when we did not.
            pass
        else:
            result = crawl_mod.harvest(rendered_html, base_url=index_url,
                                       **harvest_args)
            # "rendered" even when the browser also found nothing: "we tried a
            # browser and there is still nothing there" is a different, and
            # more useful, answer than "we never tried".
            tier = "rendered"

    doc_type_from = p.get("doc_type_from")
    sample = [{"url": u, "reads_as": _classify(u, doc_type_from)}
              for u in result.links[:SAMPLE_LIMIT]]
    by_type: dict[str, int] = {}
    for u in result.links:
        key = _classify(u, doc_type_from) or "unclassified"
        by_type[key] = by_type.get(key, 0) + 1

    counts = {
        "anchors_seen": result.anchors_seen,
        "links_matched": result.links_matched,
        "links_capped": result.links_capped,
        "off_host": result.off_host,
        "links_kept": len(result.links),
        "by_type": by_type,
        "sample_shown": len(sample),
    }
    log.info("probe %s: %s -> %d anchors, %d kept (%s)", slug, index_url,
             result.anchors_seen, len(result.links), tier)
    return _finish(conn, probe_id, "done", robots_verdict="allow",
                   tier=tier, counts=Json(counts), sample=Json(sample))


register("playbook.reonboard", handle_playbook_reonboard)
