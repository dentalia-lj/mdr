"""DISCOVER search adapters (S1.3) — PRD §3, handbook §3.

The DISCOVER `search` rung asks a web-search provider for candidate document
URLs and hands the handler a uniform `list[SearchCandidate]`. Nothing downstream
knows which provider is live (invariant 11): the handler ranks and threshold-
filters candidates identically regardless of source.

    BraveSearchAdapter   (default) — Brave Web Search API.
    SerperSearchAdapter  (fallback, config-selectable) — Serper.dev / Google.
    FakeSearchAdapter    (test/dev) — injected canned candidates; never HTTP.

Provider selection is config-switched by `adapters.search` via
`make_search_adapter(cfg)`. httpx is lazy-imported inside the live request path
only, so the fake-adapter tests (and the web container) never import it.

Invariant 12: the adapter only FETCHES search results; it never parses documents
and never decides fetch/no-fetch — the handler's T1 ranking + threshold rule owns
that. LLM use stays out of this module entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SearchCandidate",
    "SearchNotConfigured",
    "BraveSearchAdapter",
    "SerperSearchAdapter",
    "FakeSearchAdapter",
    "make_search_adapter",
]


class SearchNotConfigured(RuntimeError):
    """Raised when the selected search adapter has no API key. Distinct from a
    live HTTP failure: DISCOVER treats "not configured" as a skipped rung (the
    operator hasn't enabled search) and degrades to manual, while a genuine
    request error propagates and dead-letters. In Phase-1 backfill-first this
    lets the whole catalogue flow to manual with no Brave key set, instead of a
    wall of dead jobs."""


@dataclass(frozen=True)
class SearchCandidate:
    """One search result — the uniform shape every provider maps into.

    `rank` is the provider's 0-based result position (a weak prior the T1 ranker
    may use); the fetch/no-fetch decision is the handler's, never the adapter's.
    """

    url: str
    title: str
    snippet: str
    rank: int


def _live_http(method, url, *, headers=None, params=None, json=None):
    """Default transport — a real httpx call. Lazy-imported so tests and the web
    container never pull httpx in; the live DISCOVER worker installs `.[discover]`.
    """
    import httpx

    resp = httpx.request(
        method, url, headers=headers, params=params, json=json, timeout=15.0
    )
    resp.raise_for_status()
    return resp.json()


class BraveSearchAdapter:
    """Brave Web Search API. GET, `X-Subscription-Token` auth; results at
    `web.results[]` with `url` / `title` / `description` (verified against the
    Brave API docs, 2026-07). `http` is an injectable transport for tests."""

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, count: int = 10, http=None):
        self.api_key = api_key
        self.count = count
        self._http = http or _live_http

    def query(self, q: str, *, count: int | None = None) -> list[SearchCandidate]:
        if not self.api_key:
            raise SearchNotConfigured(
                "BRAVE_API_KEY not set — DISCOVER search rung not configured "
                "(set the key or remove `search` from the ladder)"
            )
        data = self._http(
            "GET",
            self.ENDPOINT,
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            params={"q": q, "count": count or self.count},
        )
        results = ((data or {}).get("web") or {}).get("results") or []
        return [
            SearchCandidate(
                url=r["url"],
                title=r.get("title", ""),
                snippet=r.get("description", ""),
                rank=i,
            )
            for i, r in enumerate(results)
            if r.get("url")
        ]


class SerperSearchAdapter:
    """Serper.dev Google Search API (config-selectable fallback). POST,
    `X-API-KEY` auth, body `{"q": ...}`; results at `organic[]` with
    `link` / `title` / `snippet`. Same SearchCandidate output as Brave."""

    ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: str, *, count: int = 10, http=None):
        self.api_key = api_key
        self.count = count
        self._http = http or _live_http

    def query(self, q: str, *, count: int | None = None) -> list[SearchCandidate]:
        if not self.api_key:
            raise SearchNotConfigured(
                "SERPER_API_KEY not set — DISCOVER search rung not configured "
                "(set the key or remove `search` from the ladder)"
            )
        data = self._http(
            "POST",
            self.ENDPOINT,
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": q, "num": count or self.count},
        )
        results = (data or {}).get("organic") or []
        return [
            SearchCandidate(
                url=r["link"],
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                rank=i,
            )
            for i, r in enumerate(results)
            if r.get("link")
        ]


class FakeSearchAdapter:
    """Test/dev double: returns injected candidates, or raises `error` to
    exercise the handler's search-infra-error path. Never touches the network."""

    def __init__(self, candidates: list[SearchCandidate] | None = None, *, error: Exception | None = None):
        self.candidates = candidates or []
        self.error = error

    def query(self, q: str, *, count: int | None = None) -> list[SearchCandidate]:
        if self.error is not None:
            raise self.error
        return self.candidates


def make_search_adapter(cfg):
    """Config-switched adapter over the closed `search` enum. `cfg` is the full
    composed `Config`. Keys may be empty — `query()` raises loudly if so, so a
    misconfigured live sweep dead-letters with an actionable message rather than
    silently returning zero candidates."""
    name = cfg.adapters.search
    count = cfg.discovery.max_search_candidates
    if name == "brave":
        return BraveSearchAdapter(cfg.connection.brave_api_key, count=count)
    if name == "serper":
        return SerperSearchAdapter(cfg.connection.serper_api_key, count=count)
    raise ValueError(f"unknown search adapter {name!r} (closed enum: brave | serper)")
