"""FETCH transport tier (S1.4) — the pure I/O boundary the handler orchestrates.

FETCH is AI:none (invariant 12): fetching is httpx, with a Playwright fallback
tier for bot-walled domains. Both transports are dumb "get bytes" objects behind
the `Fetcher` protocol; transport selection, bot-wall detection, and the
`needs_playwright` flag decision live in the handler (testable with fakes —
docs/specs/fetch.md §3.1/§3.3).

URL normalization for the ledger PK / dedupe key lives once in `app/urls.py`
(shared with DISCOVER, S1.3) so the `fetch:{url_normalized}` dedupe key and this
handler's `fetch_log.url_normalized` PK can never diverge (spec §3.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "FetchResult",
    "Fetcher",
    "Renderer",
    "HttpxFetcher",
    "PlaywrightFetcher",
    "make_fetcher",
]

_USER_AGENT = "DentaliaComplianceBot/1.0 (+compliance archival; polite-rate)"


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes | None
    etag: str | None
    last_modified: str | None
    content_type: str | None
    final_url: str

    @property
    def bot_wall(self) -> bool:
        """403 = blocked; the handler retries via Playwright. 429/5xx/timeouts
        are transient (queue backoff), NOT a Playwright trigger (spec §3.3)."""
        return self.status == 403


@runtime_checkable
class Fetcher(Protocol):
    def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult: ...


@runtime_checkable
class Renderer(Protocol):
    """The DISCOVER crawl rung's browser-escalation seam (playbook-crawl-recipe
    design §4.2, ruled 2026-08-24). Deliberately NOT a `Fetcher` method: widening
    `Fetcher` would mean every FETCH-side caller acquires a `render` obligation
    it never asked for, breaking invariant 11 (nothing downstream may know which
    adapter implementation is live) for a capability only the crawl rung uses.
    One method, rendered DOM out -- the crawl handler re-harvests the returned
    HTML with `app.crawl.harvest`, exactly as it does the static body."""

    def render(self, url: str) -> str: ...


class HttpxFetcher:
    """Default transport: conditional GET via httpx (If-None-Match /
    If-Modified-Since). `transport` is injectable for tests (httpx.MockTransport)."""

    def __init__(self, *, transport=None, timeout: float = 30.0) -> None:
        import httpx

        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        resp = self._client.get(url, headers=headers)
        return FetchResult(
            status=resp.status_code,
            body=resp.content if resp.status_code == 200 else None,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            content_type=resp.headers.get("Content-Type"),
            final_url=str(resp.url),
        )


class PlaywrightFetcher:
    """Render-tier fallback for bot-walled domains. Real-browser I/O — not
    unit-tested (handler tests inject a fake); playwright is imported lazily so
    this module stays importable without the optional dep."""

    def __init__(self, *, timeout_ms: int = 30000) -> None:
        self._timeout_ms = timeout_ms

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:  # pragma: no cover - browser I/O
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                resp = page.goto(
                    url, timeout=self._timeout_ms, wait_until="networkidle"
                )
                if resp is None:
                    return FetchResult(0, None, None, None, None, page.url)
                status = resp.status
                headers = resp.headers
                return FetchResult(
                    status=status,
                    body=resp.body() if status == 200 else None,
                    etag=headers.get("etag"),
                    last_modified=headers.get("last-modified"),
                    content_type=headers.get("content-type"),
                    final_url=page.url,
                )
            finally:
                browser.close()

    def render(self, url: str) -> str:  # pragma: no cover - browser I/O
        """The crawl rung's browser-escalation tier (design §4.2, ruled
        2026-08-24): returns `page.content()` -- the RENDERED DOM after
        `networkidle`, as opposed to `get()`'s `resp.body()`, which is only
        ever the initial HTTP response and never sees JS-built markup. Reuses
        the same per-call launch/goto/close lifecycle `get()` already uses
        rather than inventing a second one; no persistent browser/context is
        kept between calls, matching this class's existing shape."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(url, timeout=self._timeout_ms, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()


def make_fetcher(cfg=None) -> Fetcher:
    """Default (httpx) transport. The Playwright fallback is constructed by the
    handler only when a domain is flagged / bot-walled."""
    return HttpxFetcher()
