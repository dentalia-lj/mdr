"""fetch.url — S1.4 FETCH (spec: docs/specs/fetch.md, contract: PRD §4 / handbook §4).

Real Postgres (CLAUDE.md: no mocking DB); the network is faked via injected
`FakeFetcher`/`BoomFetcher` and archiving via `LocalFsStore(tmp)`. Covers every
row of the PRD §4 outcome table, the domain-lease/recency skips, the
httpx->Playwright bot-wall ladder, C3 hash-dedupe + recency-C3-parity, and the
null/edge/idempotency cases.
"""

from __future__ import annotations

import hashlib
import tempfile

import pytest

from app import queue
# Import runner FIRST so real handlers win over noop placeholders (last
# registration wins) — same rationale as tests/test_resolve_handler.py.
from app.workers import runner  # noqa: F401
from app.adapters.fetcher import FetchResult
from app.adapters.storage import LocalFsStore
from app.handlers import HANDLERS
from app.handlers import fetch as fh
from app.urls import normalize_url


# --- fakes ------------------------------------------------------------------

class FakeFetcher:
    """One canned answer for the document request.

    `robots.txt` is answered 404 -- a host that publishes none, which RFC 9309
    reads as full allow -- and kept out of `calls`, so every test written before
    FETCH read robots.txt (2026-09-11) still asserts on the document request
    alone. `RobotsFetcher` below is the fake for a host that does publish one."""

    def __init__(self, result: FetchResult):
        self.result = result
        self.calls: list[tuple] = []

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:
        if url.endswith("/robots.txt"):
            return FetchResult(status=404, body=None, etag=None, last_modified=None,
                               content_type="text/html", final_url=url)
        self.calls.append((url, etag, last_modified))
        return self.result


class BoomFetcher:
    """Asserts it is never called (proves the skip paths do zero network)."""

    def get(self, *a, **k):  # noqa: ANN002
        raise AssertionError("network transport must not be called on a skip path")


#: A minimal body that passes the document gate. Was `b"BODY"` until 2026-09-02,
#: which encoded the assumption that broke: every fixture handed FETCH something
#: document-shaped, so no test ever asked what happens when a server returns a
#: web page at a .pdf URL. It does, routinely -- bredent.com answers
#: HTTP 200 text/html at `...Declaration_of_Conformity_54001167.pdf`.
_PDF_BODY = b"%PDF-1.4\nBODY"


def _resp(status=200, body=_PDF_BODY, etag=None, last_modified=None,
          content_type="application/pdf", final_url="https://x.example/d.pdf"):
    return FetchResult(
        status=status,
        body=body if status == 200 else None,
        etag=etag,
        last_modified=last_modified,
        content_type=content_type,
        final_url=final_url,
    )


# --- seed helpers -----------------------------------------------------------

def _seed_group(conn, canonical="Ivoclar"):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (canonical,),
    ).fetchone()["group_id"]


def _seed_fetch_log(conn, url_norm, *, etag=None, last_modified=None,
                    content_hash=None, source="live", fresh=True):
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, etag, last_modified, content_hash, "
        "fetched_at, last_checked_at, source) "
        "VALUES (%s,%s,%s,%s, now(), now() - make_interval(days => %s), %s)",
        (url_norm, etag, last_modified, content_hash, 0 if fresh else 60, source),
    )


def _seed_extraction(conn, content_hash, rev=1):
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES (%s,'T1','{}'::jsonb,%s)",
        (content_hash, rev),
    )


def _seed_document(conn, content_hash, status="staged"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('EC','MDR','group',%s,'file:///orig.pdf',%s) "
        "RETURNING doc_id",
        (content_hash, status),
    ).fetchone()["doc_id"]


def _seed_lease(conn, domain, *, busy=False, needs_playwright=False):
    leased = "now() + interval '1 hour'" if busy else "NULL"  # SQL literal, no user input
    conn.execute(
        f"INSERT INTO domain_lease (domain, leased_until, politeness_ms, needs_playwright) "
        f"VALUES (%s, {leased}, 2000, %s)",
        (domain, needs_playwright),
    )


def _job(url, domain="example.com", group_id=None, source_rank="search", jid=1):
    p = {"url": url, "domain": domain, "source_rank": source_rank}
    if group_id is not None:
        p["group_id"] = group_id
    return {"id": jid, "type": "fetch.url", "payload": p}


def _jobs(conn, jtype):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type=%s ORDER BY id", (jtype,)
    ).fetchall()


def _fetch_log_row(conn, url_norm):
    return conn.execute(
        "SELECT * FROM fetch_log WHERE url_normalized=%s", (url_norm,)
    ).fetchone()


def _needs_pw(conn, domain):
    return conn.execute(
        "SELECT needs_playwright FROM domain_lease WHERE domain=%s", (domain,)
    ).fetchone()["needs_playwright"]


def _expire_lease(conn, domain):
    """Simulate the politeness interval (and, for two-cycle tests, the month gap)
    elapsing so the next fetch of the same domain re-acquires the lease."""
    conn.execute(
        "UPDATE domain_lease SET leased_until = now() - interval '1 hour' WHERE domain=%s",
        (domain,),
    )


# --------------------------------------------------------------------------- #
# 1. domain lease busy -> defer, no network
# --------------------------------------------------------------------------- #

def test_lease_busy_defers(conn):
    _seed_lease(conn, "busy.example", busy=True)
    f = FakeFetcher(_resp())
    res = fh.handle_fetch_url(
        conn, _job("https://busy.example/d.pdf", domain="busy.example"),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=LocalFsStore("/tmp/none"),
    )
    assert res["_deferred"] is True
    assert res["outcome"] == "lease-wait"
    assert f.calls == []


# --------------------------------------------------------------------------- #
# 2. recency hit + extraction exists -> recency-skip-linked (C3 parity, no net)
# --------------------------------------------------------------------------- #

def test_recency_skip_linked_emits_validate(conn):
    g = _seed_group(conn)
    _seed_fetch_log(conn, "https://x.example/c.pdf", content_hash="H_REC", fresh=True)
    _seed_extraction(conn, "H_REC", rev=3)

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/c.pdf", group_id=g),
        fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(),
        store=LocalFsStore("/tmp/none"),
    )
    assert res["outcome"] == "recency-skip-linked"
    v = _jobs(conn, "validate.doc")
    assert len(v) == 1
    assert v[0]["payload"] == {"content_hash": "H_REC", "extract_rev": 3, "group_id": g}
    assert v[0]["dedupe_key"] == f"validate:H_REC:3:{g}"


# --------------------------------------------------------------------------- #
# 2b. recency-skip-linked carries the stored copy's handle once the registry
#     holds one (defect fix, 2026-08-24)
# --------------------------------------------------------------------------- #

def test_recency_skip_linked_carries_the_stored_archive_handle(conn):
    # Measured on the RENFERT smoke test (2026-08-24): the recency-skip-linked
    # branch emitted validate.doc with NO archive_url key (job 35563), and the
    # resulting gate.candidate fell back to fetch_log.url_normalized — the
    # remote SOURCE url — refreshing document.archive_url and the evidence rows
    # of doc 843 away from our stored copy (/archive/RENFERT/...). Once the
    # registry holds the handle, every C3 re-entry payload must carry it.
    g = _seed_group(conn)
    _seed_fetch_log(conn, "https://x.example/c.pdf", content_hash="H_HANDLE", fresh=True)
    _seed_extraction(conn, "H_HANDLE", rev=3)
    _seed_document(conn, "H_HANDLE")                # archive_url file:///orig.pdf

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/c.pdf", group_id=g),
        fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(),
        store=LocalFsStore("/tmp/none"),
    )
    assert res["outcome"] == "recency-skip-linked"
    v = _jobs(conn, "validate.doc")
    assert len(v) == 1
    assert v[0]["payload"]["archive_url"] == "file:///orig.pdf"


# --------------------------------------------------------------------------- #
# 3. recency hit, no extraction yet -> plain recency-skip (self-heal next cycle)
# --------------------------------------------------------------------------- #

def test_recency_skip_without_extraction(conn):
    g = _seed_group(conn)
    _seed_fetch_log(conn, "https://x.example/c.pdf", content_hash="H_NX", fresh=True)

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/c.pdf", group_id=g),
        fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(),
        store=LocalFsStore("/tmp/none"),
    )
    assert res["outcome"] == "recency-skip"
    assert _jobs(conn, "validate.doc") == []


# --------------------------------------------------------------------------- #
# 4. 304 Not Modified -> touch last_checked, hash unchanged, no emit
# --------------------------------------------------------------------------- #

def test_304_touches_ledger(conn):
    url = "https://x.example/e.pdf"
    _seed_fetch_log(conn, url, etag='"E1"', content_hash="OLDH", fresh=False)
    f = FakeFetcher(_resp(status=304))

    res = fh.handle_fetch_url(
        conn, _job(url, group_id=None), fetcher=f, playwright_fetcher=BoomFetcher(),
        store=LocalFsStore("/tmp/none"),
    )
    assert res["outcome"] == "not-modified"
    # conditional header carried the stored etag
    assert f.calls[0][1] == '"E1"'
    row = _fetch_log_row(conn, url)
    assert row["content_hash"] == "OLDH"  # unchanged
    assert _jobs(conn, "extract.doc") == []
    assert _jobs(conn, "validate.doc") == []


# --------------------------------------------------------------------------- #
# 5. new content hash -> archive + extract.doc (layout per PRD §9)
# --------------------------------------------------------------------------- #

def test_new_hash_archives_and_emits_extract(conn, tmp_path):
    g = _seed_group(conn, canonical="Ivoclar")
    body = b"%PDF new cert body"
    h = hashlib.sha256(body).hexdigest()
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, etag='"NEW"',
                          final_url="https://x.example/ec_certificate.pdf"))

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/ec_certificate.pdf", group_id=g),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=store,
    )
    assert res["outcome"] == "fetched"
    assert res["via"] == "httpx"
    # ledger
    assert _fetch_log_row(conn, "https://x.example/ec_certificate.pdf")["content_hash"] == h
    # archive on disk at Ivoclar/ec/<h12>__ec_certificate.pdf
    archived = list(tmp_path.rglob("*__ec_certificate.pdf"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == body
    assert archived[0].parent.name == "ec"
    assert archived[0].parts[-3] == "Ivoclar"
    # emit
    e = _jobs(conn, "extract.doc")
    assert len(e) == 1
    assert e[0]["payload"]["content_hash"] == h
    assert e[0]["payload"]["group_id"] == g
    assert e[0]["payload"]["source_url"] == "https://x.example/ec_certificate.pdf"
    assert e[0]["dedupe_key"] == f"extract:{h}"


# --------------------------------------------------------------------------- #
# 6. same hash, new URL, extraction exists -> C3 hash-dedupe (validate, no archive)
# --------------------------------------------------------------------------- #

def test_hash_dedupe_emits_validate_no_archive(conn, tmp_path):
    g = _seed_group(conn)
    body = b"%PDF-1.4\nalready-known-content"
    h = hashlib.sha256(body).hexdigest()
    _seed_extraction(conn, h, rev=2)
    doc_id = _seed_document(conn, h)
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, final_url="https://other.example/same.pdf"))

    res = fh.handle_fetch_url(
        conn, _job("https://other.example/same.pdf", group_id=g),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=store,
    )
    assert res["outcome"] == "hash-dedupe"
    assert _jobs(conn, "extract.doc") == []
    v = _jobs(conn, "validate.doc")
    assert len(v) == 1
    # carries the registry's stored-copy handle (defect fix 2026-08-24) so
    # GATE never falls back to the remote source url for this candidate
    assert v[0]["payload"] == {"content_hash": h, "extract_rev": 2, "group_id": g,
                               "archive_url": "file:///orig.pdf"}
    # no archive written
    assert list(tmp_path.rglob("*")) == []
    # fetch_log row linked to the existing document
    assert _fetch_log_row(conn, "https://other.example/same.pdf")["doc_id"] == doc_id


# --------------------------------------------------------------------------- #
# 6b. ignore_recency forces past a fresh ledger row, but identical bytes still
#     hit the C3 hash-dedupe path -> links the group, never a duplicate doc
# --------------------------------------------------------------------------- #

def test_forced_refetch_of_identical_bytes_links_and_does_not_duplicate(conn, tmp_path):
    g = _seed_group(conn)
    body = b"%PDF-1.4\nalready-known-content"
    h = hashlib.sha256(body).hexdigest()
    _seed_fetch_log(conn, "https://x.example/c.pdf", content_hash=h, fresh=True)
    _seed_extraction(conn, h, rev=4)
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, final_url="https://x.example/c.pdf"))

    job = _job("https://x.example/c.pdf", group_id=g)
    job["payload"]["ignore_recency"] = True

    res = fh.handle_fetch_url(
        conn, job, fetcher=f, playwright_fetcher=BoomFetcher(), store=store,
    )
    assert f.calls, "forced refetch must bypass the recency skip and hit the network"
    assert res["outcome"] == "hash-dedupe"
    v = _jobs(conn, "validate.doc")
    assert len(v) == 1
    assert v[0]["payload"] == {"content_hash": h, "extract_rev": 4, "group_id": g}
    assert list(tmp_path.rglob("*")) == []   # no duplicate archive


# --------------------------------------------------------------------------- #
# 7. bot-wall (403) -> Playwright fallback succeeds -> flag domain, archive
# --------------------------------------------------------------------------- #

def test_bot_wall_playwright_fallback(conn, tmp_path):
    g = _seed_group(conn)
    body = b"%PDF-1.4\nrendered pdf"
    httpx_f = FakeFetcher(_resp(status=403))
    pw_f = FakeFetcher(_resp(body=body, final_url="https://walled.example/d.pdf"))
    store = LocalFsStore(str(tmp_path))

    res = fh.handle_fetch_url(
        conn, _job("https://walled.example/d.pdf", domain="walled.example", group_id=g),
        fetcher=httpx_f, playwright_fetcher=pw_f, store=store,
    )
    assert res["outcome"] == "fetched"
    assert res["via"] == "playwright"
    assert _needs_pw(conn, "walled.example") is True
    assert len(_jobs(conn, "extract.doc")) == 1


# --------------------------------------------------------------------------- #
# 8. domain pre-flagged needs_playwright -> Playwright used directly, httpx skipped
# --------------------------------------------------------------------------- #

def test_preflagged_domain_uses_playwright(conn, tmp_path):
    g = _seed_group(conn)
    _seed_lease(conn, "known.example", needs_playwright=True)
    httpx_f = BoomFetcher()  # must NOT be called
    pw_f = FakeFetcher(_resp(body=b"%PDF-1.4\npdf", final_url="https://known.example/d.pdf"))

    res = fh.handle_fetch_url(
        conn, _job("https://known.example/d.pdf", domain="known.example", group_id=g),
        fetcher=httpx_f, playwright_fetcher=pw_f, store=LocalFsStore(str(tmp_path)),
    )
    assert res["via"] == "playwright"
    assert len(_jobs(conn, "extract.doc")) == 1


# --------------------------------------------------------------------------- #
# 9. bot-wall persists (httpx 403 + Playwright 403) -> raise, NOT flagged
# --------------------------------------------------------------------------- #

def test_bot_wall_persistent_raises_not_flagged(conn, tmp_path):
    g = _seed_group(conn)
    httpx_f = FakeFetcher(_resp(status=403))
    pw_f = FakeFetcher(_resp(status=403))

    with pytest.raises(fh.FetchError):
        fh.handle_fetch_url(
            conn, _job("https://hard.example/d.pdf", domain="hard.example", group_id=g),
            fetcher=httpx_f, playwright_fetcher=pw_f, store=LocalFsStore(str(tmp_path)),
        )
    assert _needs_pw(conn, "hard.example") is False


# --------------------------------------------------------------------------- #
# 10. transient 5xx -> raise (backoff, not a Playwright trigger)
# --------------------------------------------------------------------------- #

def test_transient_5xx_raises(conn, tmp_path):
    f = FakeFetcher(_resp(status=503))
    with pytest.raises(fh.FetchError):
        fh.handle_fetch_url(
            conn, _job("https://x.example/d.pdf", domain="t.example"),
            fetcher=f, playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
        )


# --------------------------------------------------------------------------- #
# 11. new hash, group_id null -> extract.doc group_id null, manufacturer dir "unknown"
# --------------------------------------------------------------------------- #

def test_new_hash_group_none(conn, tmp_path):
    body = b"%PDF-1.4\norphan body"
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, final_url="https://x.example/file.pdf"))

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/file.pdf", group_id=None),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=store,
    )
    assert res["outcome"] == "fetched"
    e = _jobs(conn, "extract.doc")
    assert e[0]["payload"]["group_id"] is None
    assert list(tmp_path.iterdir())[0].name == "unknown"


# --------------------------------------------------------------------------- #
# 12. payload missing url -> loud failure
# --------------------------------------------------------------------------- #

def test_missing_url_raises(conn):
    job = {"id": 1, "type": "fetch.url", "payload": {"domain": "x.example"}}
    with pytest.raises((KeyError, ValueError)):
        fh.handle_fetch_url(
            conn, job, fetcher=FakeFetcher(_resp()),
            playwright_fetcher=BoomFetcher(), store=LocalFsStore("/tmp/none"),
        )


# --------------------------------------------------------------------------- #
# 13. at-least-once redelivery -> no duplicate archive or extract.doc
# --------------------------------------------------------------------------- #

def test_redelivery_is_idempotent(conn, tmp_path):
    g = _seed_group(conn)
    body = b"%PDF-1.4\nredelivered"
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, final_url="https://x.example/r.pdf"))
    j = _job("https://x.example/r.pdf", group_id=g)

    fh.handle_fetch_url(conn, j, fetcher=f, playwright_fetcher=BoomFetcher(), store=store)
    _expire_lease(conn, "example.com")  # politeness interval elapsed
    # redeliver the same job: ledger now fresh, no extraction yet -> recency-skip
    res2 = fh.handle_fetch_url(
        conn, j, fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(), store=store
    )
    assert res2["outcome"] == "recency-skip"
    assert len(_jobs(conn, "extract.doc")) == 1
    assert len(list(tmp_path.rglob("*__r.pdf"))) == 1


# --------------------------------------------------------------------------- #
# 14. two-cycle: after extraction lands, a re-check does zero network / archive
# --------------------------------------------------------------------------- #

def test_two_cycle_recheck_zero_cost(conn, tmp_path):
    g = _seed_group(conn)
    body = b"%PDF-1.4\ncycle body"
    h = hashlib.sha256(body).hexdigest()
    store = LocalFsStore(str(tmp_path))
    f = FakeFetcher(_resp(body=body, final_url="https://x.example/cy.pdf"))
    j = _job("https://x.example/cy.pdf", group_id=g)

    fh.handle_fetch_url(conn, j, fetcher=f, playwright_fetcher=BoomFetcher(), store=store)
    _seed_extraction(conn, h, rev=1)  # extract ran
    _expire_lease(conn, "example.com")  # month gap elapsed

    res2 = fh.handle_fetch_url(
        conn, j, fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(), store=store
    )
    assert res2["outcome"] == "recency-skip-linked"
    assert len(list(tmp_path.rglob("*__cy.pdf"))) == 1  # not re-archived
    assert any(v["payload"]["group_id"] == g for v in _jobs(conn, "validate.doc"))


# --------------------------------------------------------------------------- #
# 15. registration guard
# --------------------------------------------------------------------------- #

def test_registered_in_runner():
    assert HANDLERS["fetch.url"] is fh.handle_fetch_url


# --- pure helpers -----------------------------------------------------------

@pytest.mark.parametrize(
    "url, ctype, expected",
    [
        ("https://x/ifu_en.pdf", "application/pdf", "ifu"),
        ("https://x/declaration_of_conformity.pdf", "application/pdf", "doc"),
        ("https://x/ec_certificate.pdf", "application/pdf", "ec"),
        ("https://x/iso13485.pdf", "application/pdf", "iso"),
        ("https://x/download?id=42", "application/pdf", "unknown"),
    ],
)
def test_guess_type_dir(url, ctype, expected):
    assert fh._guess_type_dir(url, ctype) == expected


def test_archive_path_layout():
    h = "abc123def456ff"
    p = fh._archive_path("Ivoclar Vivadent", h,
                         "https://x/declaration.pdf", "application/pdf")
    assert p == "Ivoclar_Vivadent/doc/abc123def456__declaration.pdf"


def test_filename_falls_back_to_hash_when_empty():
    name = fh._filename("https://x.example/", "application/pdf")
    assert name.endswith(".pdf")


# --- the document gate ------------------------------------------------------
#
# FETCH archives what a server returns. Until 2026-09-02 it never asked whether
# that was a document: `content_type` was passed to `archiving.archive_path` to
# pick a folder and to nothing else. Measured the same day over 24 real search
# candidates, 18 of which answered 200: **5 were HTML** (28%), and four web
# pages had already been archived, extracted, typed and staged as compliance
# documents -- one of them approved into `production` by a human who saw
# plausible metadata. The gate is bytes, not the header: the same bredent URL
# that returns `text/html` here is exactly the case where the header is the
# only thing that told the truth, and other servers send
# `application/octet-stream` for real PDFs.

def _fetch_body(conn, body, url="https://gate.example/d.pdf", **kw):
    """Run one fetch with a given body and return the handler result."""
    g = _seed_group(conn)
    return fh.handle_fetch_url(
        conn, _job(url, domain="gate.example", group_id=g),
        fetcher=FakeFetcher(_resp(body=body, **kw)),
        playwright_fetcher=BoomFetcher(),
        store=LocalFsStore(tempfile.mkdtemp()),
    )


@pytest.mark.parametrize("body,why", [
    (b"<!DOCTYPE html><html>", "plain html"),
    (b"<html><head>", "html without a doctype"),
    (b" <!doctype html>", "leading whitespace -- dentalsky.com answers exactly this"),
    (b"\n\n<!DOCTYPE html>", "leading newlines"),
    (b"{\"error\": \"not found\"}", "a JSON error body"),
    (b"", "an empty body"),
])
def test_a_body_that_is_not_a_document_is_refused_and_never_archived(conn, body, why):
    res = _fetch_body(conn, body)

    assert res["outcome"] == "not-a-document", why
    assert res.get("archive_url") is None
    # Never silent (CLAUDE.md): the reason is on the job result, not just a log.
    assert "magic" in res


def test_a_real_pdf_still_archives(conn):
    res = _fetch_body(conn, b"%PDF-1.7\nreal document bytes")

    assert res["outcome"] == "fetched"
    assert res["archive_url"]


def test_the_gate_reads_bytes_not_the_content_type_header(conn):
    """Both directions. A server that mislabels a real PDF as octet-stream is
    still serving a document; one that answers text/html at a .pdf URL is not,
    and that is the case that actually happened."""
    ok = _fetch_body(conn, b"%PDF-1.4\nx", url="https://gate.example/a.pdf",
                     content_type="application/octet-stream")
    assert ok["outcome"] == "fetched"

    _expire_lease(conn, "gate.example")
    bad = _fetch_body(conn, b"<!DOCTYPE html>", url="https://gate.example/b.pdf",
                      content_type="application/pdf")
    assert bad["outcome"] == "not-a-document"


def test_a_refused_body_is_still_ledgered_so_it_is_not_refetched_forever(conn):
    """Invariant 6 still applies to junk: the URL was fetched and its hash is
    known, so the ledger records it. What must not happen is an archive write
    or an `extract.doc`."""
    res = _fetch_body(conn, b"<!DOCTYPE html>", url="https://gate.example/j.pdf")
    assert res["outcome"] == "not-a-document"
    row = _fetch_log_row(conn, normalize_url("https://gate.example/j.pdf"))
    assert row is not None and row["content_hash"]
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='extract.doc'"
    ).fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# robots.txt, read before the document request ([robots-reader-unwired]).
# Ruled 2026-08-21 ("runtime check AND authoring guard"); wired into the crawl
# rung and the probe on 09-03, never into FETCH until 2026-09-11.
# --------------------------------------------------------------------------- #
class RobotsFetcher(FakeFetcher):
    """A host that publishes a robots.txt. `seen` records every request,
    robots.txt included, so a test can prove the document was never asked for."""

    def __init__(self, result: FetchResult, robots: str | None = None,
                 *, robots_raises: bool = False):
        super().__init__(result)
        self.robots = robots
        self.robots_raises = robots_raises
        self.seen: list[str] = []

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:
        self.seen.append(url)
        if url.endswith("/robots.txt"):
            if self.robots_raises:
                raise ConnectionError("temporary failure in name resolution")
            return FetchResult(status=200, body=self.robots.encode(), etag=None,
                               last_modified=None, content_type="text/plain",
                               final_url=url)
        self.calls.append((url, etag, last_modified))
        return self.result


_ROBOTS = "User-agent: *\nDisallow: /private/\n"


def test_a_url_robots_txt_disallows_is_never_requested(conn, tmp_path):
    g = _seed_group(conn)
    f = RobotsFetcher(_resp(), _ROBOTS)

    res = fh.handle_fetch_url(
        conn, _job("https://robots.example/private/doc.pdf", domain="robots.example",
                   group_id=g),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
    )

    assert res["outcome"] == "robots-refused"
    assert res["reason"] == "disallow"
    assert f.seen == ["https://robots.example/robots.txt"]
    assert _jobs(conn, "extract.doc") == []
    assert _fetch_log_row(conn, normalize_url("https://robots.example/private/doc.pdf")) is None


def test_a_url_robots_txt_allows_is_fetched_as_before(conn, tmp_path):
    g = _seed_group(conn)
    f = RobotsFetcher(_resp(), _ROBOTS)

    res = fh.handle_fetch_url(
        conn, _job("https://robots.example/public/doc.pdf", domain="robots.example",
                   group_id=g),
        fetcher=f, playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
    )

    assert res["outcome"] == "fetched"
    assert f.seen == ["https://robots.example/robots.txt",
                      "https://robots.example/public/doc.pdf"]
    assert len(_jobs(conn, "extract.doc")) == 1


def test_an_unreachable_robots_txt_retries_instead_of_refusing(conn, tmp_path):
    """No answer is not a refusal. The crawl rung logs it as a miss; a FETCH
    job that finished on it would drop a document over one DNS blip (the
    2026-09-07 ultradent.com case), so it fails into the queue's backoff and
    asks again, and the module caches a transport failure for 5 minutes only."""
    f = RobotsFetcher(_resp(), robots_raises=True)

    with pytest.raises(fh.FetchError, match="robots.txt"):
        fh.handle_fetch_url(
            conn, _job("https://robots.example/public/doc.pdf", domain="robots.example"),
            fetcher=f, playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
        )
    assert f.calls == []


def test_the_recency_skip_still_makes_no_request_at_all(conn):
    """Robots is read only when a request is about to be made: the recency skip
    answers from the ledger and must stay network-free (BoomFetcher)."""
    g = _seed_group(conn)
    _seed_fetch_log(conn, "https://x.example/r.pdf", content_hash="H_ROB", fresh=True)
    _seed_extraction(conn, "H_ROB", rev=1)

    res = fh.handle_fetch_url(
        conn, _job("https://x.example/r.pdf", group_id=g),
        fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(),
        store=LocalFsStore("/tmp/none"),
    )
    assert res["outcome"] == "recency-skip-linked"


def test_robots_txt_behind_a_bot_wall_is_read_the_way_a_browser_reads_it(conn, tmp_path):
    """httpx meets a 403 wall on robots.txt too. `app.robots` reads a 403 as a
    refusal, so asking over httpx alone would refuse every host FETCH already
    reaches through Playwright. The browser's answer is the one that counts."""
    g = _seed_group(conn)
    walled = RobotsFetcher(_resp(status=403))
    walled.get = lambda url, **kw: _resp(status=403, final_url=url)  # wall on everything
    browser = RobotsFetcher(_resp(), _ROBOTS)

    res = fh.handle_fetch_url(
        conn, _job("https://walled.example/private/doc.pdf", domain="walled.example",
                   group_id=g),
        fetcher=walled, playwright_fetcher=browser, store=LocalFsStore(str(tmp_path)),
    )

    assert res["outcome"] == "robots-refused"
    assert res["reason"] == "disallow"
    assert browser.seen == ["https://walled.example/robots.txt"]
