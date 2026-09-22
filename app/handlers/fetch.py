"""fetch.url — S1.4 FETCH (spec: docs/specs/fetch.md, contract: PRD §4 / handbook §4).

Fetches one URL under fetch-ledger discipline and archives new content under our
control (PRD §9), then hands the spine forward:

    new content hash   -> archive + extract.doc {archive_url, content_hash, group_id, source_url}
    304 Not Modified   -> touch last_checked_at, emit nothing
    same hash, new URL -> validate.doc against the existing extraction (C3 — no re-fetch/-extract)
    recency hit        -> skip the network; if already extracted, still link the requesting
                          group (recency-C3-parity, spec §3.2)
    bot-wall (403)     -> retry via the Playwright render tier; persistent -> dead

FETCH is AI:none (invariant 12) and a producer only — it writes fetch_log + the
archived file and emits jobs, never document/item_document/evidence (invariant 1).
Politeness is one fetch per domain per interval via the domain lease. The
transport (httpx / Playwright) and StorageAdapter are injected for tests; only
Postgres is real (CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import logging

from app import queue
from app import robots as robots_mod
from app.adapters.fetcher import make_fetcher
from app.urls import normalize_url
from app.adapters.storage import make_storage_adapter
from app.config import load_config
from app.handlers import register
from app.handlers import archiving

log = logging.getLogger("dentalia.handler.fetch")


class FetchError(Exception):
    """A fetch that should fail the job (persistent bot-wall / transient HTTP /
    unexpected status) -> queue backoff -> dead-letter."""


# --- ledger -----------------------------------------------------------------

def _ledger_get(conn, url_norm):
    return conn.execute(
        "SELECT url_normalized, etag, last_modified, content_hash, doc_id, "
        "last_checked_at FROM fetch_log WHERE url_normalized=%s",
        (url_norm,),
    ).fetchone()


def _fresh(conn, url_norm, days):
    """True if the ledger row was checked within the recency window (DB clock)."""
    row = conn.execute(
        "SELECT last_checked_at IS NOT NULL "
        "AND last_checked_at >= now() - make_interval(days => %s) AS fresh "
        "FROM fetch_log WHERE url_normalized=%s",
        (days, url_norm),
    ).fetchone()
    return bool(row and row["fresh"])


def _ledger_touch(conn, url_norm):
    conn.execute(
        "UPDATE fetch_log SET last_checked_at=now() WHERE url_normalized=%s",
        (url_norm,),
    )


# The document gate moved to app.handlers.archiving on 2026-09-02
# (`[gate-covers-only-fetch]`): FETCH was one of four producers that can write
# into the archive and the only one that checked. Alias kept so this module and
# its tests read as before.

_is_document = archiving.is_document


# --- domain / playwright flag ----------------------------------------------

def _domain_needs_playwright(conn, domain):
    row = conn.execute(
        "SELECT needs_playwright FROM domain_lease WHERE domain=%s", (domain,)
    ).fetchone()
    return bool(row and row["needs_playwright"])


def _flag_domain_playwright(conn, domain):
    conn.execute(
        "UPDATE domain_lease SET needs_playwright=true WHERE domain=%s", (domain,)
    )


# --- archive path / ledger-upsert / emits — moved to app.handlers.archiving.
# Thin aliases below keep tests/test_fetch_handler.py's direct fh._* references
# working (CLAUDE.md: refactor only, no behavior change, don't edit test intent).

_guess_type_dir = archiving._guess_type_dir
_archive_path = archiving.archive_path
_filename = archiving._filename


# --- transport selection + bot-wall fallback (spec §3.1/§3.3) ---------------

def _fetch(conn, url, domain, ledger, fetcher, playwright_fetcher):
    etag = ledger["etag"] if ledger else None
    last_modified = ledger["last_modified"] if ledger else None
    if _domain_needs_playwright(conn, domain):
        return playwright_fetcher.get(url, etag=etag, last_modified=last_modified), "playwright"
    resp = fetcher.get(url, etag=etag, last_modified=last_modified)
    if resp.bot_wall:
        pw = playwright_fetcher.get(url, etag=etag, last_modified=last_modified)
        if pw.bot_wall or pw.status not in (200, 304):
            raise FetchError(
                f"bot-wall persists for {url} (httpx {resp.status}, playwright {pw.status})"
            )
        _flag_domain_playwright(conn, domain)  # persists with finish (success path only)
        return pw, "playwright"
    return resp, "httpx"


class _RobotsTransport:
    """robots.txt over the same ladder `_fetch` uses for the document.

    A bot-walled host answers plain httpx with a challenge page, usually a 403,
    and `app.robots` reads any non-200 other than 404/410 as a refusal. Asking
    through httpx alone would therefore refuse exactly the hosts FETCH already
    reaches through Playwright (manualzz, medicalexpo, coltene, straumann on
    2026-09-11). So: Playwright for a flagged domain, and a fall back to it when
    httpx meets a wall, the same answer a browser gets. Flags nothing itself --
    the document request that follows decides whether the domain is flagged."""

    def __init__(self, conn, domain, fetcher, playwright_fetcher):
        self._conn, self._domain = conn, domain
        self._fetcher, self._pw = fetcher, playwright_fetcher

    def get(self, url, **kw):
        if _domain_needs_playwright(self._conn, self._domain):
            return self._pw.get(url)
        resp = self._fetcher.get(url)
        return self._pw.get(url) if resp.bot_wall else resp


# --- entry ------------------------------------------------------------------

def handle_fetch_url(conn, job, *, fetcher=None, playwright_fetcher=None, store=None):
    cfg = load_config()
    p = job["payload"]
    url = p["url"]                      # missing -> KeyError -> fail (loud)
    domain = p["domain"]
    group_id = p.get("group_id")
    url_norm = normalize_url(url)

    if fetcher is None:
        fetcher = make_fetcher(cfg)
    if playwright_fetcher is None:
        from app.adapters.fetcher import PlaywrightFetcher
        playwright_fetcher = PlaywrightFetcher()
    if store is None:
        store = make_storage_adapter(cfg)

    # 1. politeness lease — one fetch per domain per interval
    if not queue.try_domain_lease(conn, domain, cfg.fetch.politeness_ms):
        queue.defer(conn, job["id"], max(1, cfg.fetch.politeness_ms // 1000))
        return {"outcome": "lease-wait", "_deferred": True, "group_id": group_id}

    ledger = _ledger_get(conn, url_norm)

    # 2. recency skip (no network) — link the requesting group if already extracted.
    # `ignore_recency` (admin refetch, [admin-refetch-item]) bypasses ONLY this
    # check — the conditional GET below still runs, so a forced re-fetch of
    # unchanged bytes still ends up deduped via C3 hash-dedupe, never duplicated.
    if (not p.get("ignore_recency")
            and ledger and ledger["content_hash"]
            and _fresh(conn, url_norm, cfg.fetch.recency_window_days)):
        h = ledger["content_hash"]
        rev = archiving.hash_extracted_rev(conn, h)
        if rev is not None and group_id is not None:
            archiving.emit_validate(conn, h, rev, group_id)
            return {"outcome": "recency-skip-linked", "content_hash": h,
                    "emitted": "validate.doc", "group_id": group_id}
        return {"outcome": "recency-skip", "content_hash": h, "group_id": group_id}

    # 2a. robots.txt -- ask before the one request that matters
    # ([robots-reader-unwired]; ruled 2026-08-21, "runtime check AND authoring
    # guard"). Until 2026-09-11 only the crawl rung and the probe asked, so a
    # URL from search, a known URL or an authored direct source was fetched
    # whatever the host said. Placed after the recency skip, which makes no
    # request, and inside the lease, so the robots.txt request is itself
    # polite; the same transports, per invariant 11, over the same ladder
    # (`_RobotsTransport`). A refusal finishes the job
    # with its reason, the shape `not-a-document` already has: nothing was
    # fetched, so nothing is ledgered and nothing is retried. The one exception
    # is a transport failure, which is the absence of an answer rather than a
    # refusal: it goes to the queue's backoff (the module caches it for 5
    # minutes, `robots.UNREACHABLE_TTL_MINUTES`), so one DNS blip cannot drop a
    # document for good.
    decision = robots_mod.check(conn, url,
                                fetcher=_RobotsTransport(conn, domain, fetcher,
                                                         playwright_fetcher),
                                ttl_hours=cfg.fetch.robots_ttl_hours)
    if not decision.allowed:
        if decision.reason == "unreachable" and decision.status is None:
            raise FetchError(f"robots.txt unreachable for {decision.host}; retrying")
        log.info("fetch: robots.txt refuses %s (%s, status %s)",
                 url, decision.reason, decision.status)
        return {"outcome": "robots-refused", "reason": decision.reason,
                "host": decision.host, "robots_status": decision.status,
                "group_id": group_id}

    # 3. fetch (transport choice + bot-wall -> Playwright fallback)
    resp, via = _fetch(conn, url, domain, ledger, fetcher, playwright_fetcher)

    # 4. 304 — content unchanged
    if resp.status == 304:
        _ledger_touch(conn, url_norm)
        return {"outcome": "not-modified", "via": via, "group_id": group_id}

    # transient / unexpected — backoff via the queue
    if resp.status != 200 or resp.body is None:
        raise FetchError(f"unexpected status {resp.status} for {url}")

    # 5. hash + ledger
    h = hashlib.sha256(resp.body).hexdigest()
    archiving.ledger_upsert(conn, url_norm, etag=resp.etag, last_modified=resp.last_modified,
                            content_hash=h, source="live")

    # 5a. is it a document at all?
    #
    # A server answering 200 says nothing about what it returned.
    # `www.bredent.com/.../Englisch_Declaration_of_Conformity_54001167.pdf`
    # answers `200 text/html`, 301.268 bytes of site navigation -- and until
    # 2026-09-02 that was archived, extracted, typed `IFU` at T0 confidence 1.00
    # off the words "Instructions for use" in the menu, staged, and approved
    # into production by a human who saw plausible metadata. PyMuPDF opens HTML
    # and paginates it, so nothing downstream noticed either.
    #
    # Measured over 24 real search candidates the same day: of the 18 that
    # answered 200, 5 were HTML (28%).
    #
    # Bytes, not the header, in both directions: that bredent URL sends
    # `application/pdf`-shaped expectations and `text/html` truth, while other
    # servers send `application/octet-stream` for genuine PDFs.
    #
    # Ledgered above, deliberately, BEFORE this returns: the URL was fetched and
    # its hash is known, so invariant 6 should stop us paying for it again. What
    # must not happen is an archive write or an `extract.doc`.
    if not _is_document(resp.body):
        magic = archiving.magic_of(resp.body)
        log.info("fetch: refusing non-document body from %s (magic=%r, %d bytes, ct=%s)",
                 url, magic, len(resp.body), resp.content_type)
        return {"outcome": "not-a-document", "content_hash": h, "via": via,
                "magic": magic, "bytes": len(resp.body),
                "content_type": resp.content_type, "group_id": group_id}

    # 6. C3 hash-dedupe — content already extracted: link the group, no re-archive
    rev = archiving.hash_extracted_rev(conn, h)
    if rev is not None:
        archiving.link_existing_doc(conn, url_norm, h)
        if group_id is not None:
            archiving.emit_validate(conn, h, rev, group_id)
        return {"outcome": "hash-dedupe", "content_hash": h, "via": via,
                "emitted": "validate.doc", "group_id": group_id}

    # 7. new content — archive + extract
    manufacturer = archiving.manufacturer_for_group(conn, group_id)
    archive_url = store.put(resp.body, archiving.archive_path(manufacturer, h, url, resp.content_type))
    archiving.emit_extract(conn, archive_url, h, group_id, url)
    return {"outcome": "fetched", "content_hash": h, "archive_url": archive_url,
            "via": via, "emitted": "extract.doc", "bytes": len(resp.body),
            "group_id": group_id}


register("fetch.url", handle_fetch_url)
