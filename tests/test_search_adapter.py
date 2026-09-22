"""DISCOVER SearchAdapter (S1.3, PRD §3 / handbook §3).

The search rung of DISCOVER queries a web-search provider and hands the handler
a uniform `list[SearchCandidate]` — nothing downstream knows which provider is
live (invariant 11). These tests pin request construction, response parsing, the
uniform candidate shape across providers, and the config-switched factory. No
live HTTP: an injected transport returns canned provider JSON (the real httpx
call is lazy-imported and never exercised here).
"""

from __future__ import annotations

import dataclasses

import pytest

from app.adapters import search as S
from app.config import load_config


def _fake_http(canned):
    """A transport double: records calls, returns a canned decoded-JSON dict."""
    calls: list[dict] = []

    def _http(method, url, *, headers=None, params=None, json=None):
        calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        )
        return canned

    _http.calls = calls
    return _http


# --------------------------------------------------------------------------- #
# SearchCandidate — the uniform shape (invariant 11).
# --------------------------------------------------------------------------- #
def test_search_candidate_fields():
    c = S.SearchCandidate(url="https://x/doc.pdf", title="DoC", snippet="declaration", rank=0)
    assert (c.url, c.title, c.snippet, c.rank) == ("https://x/doc.pdf", "DoC", "declaration", 0)


# --------------------------------------------------------------------------- #
# Brave.
# --------------------------------------------------------------------------- #
BRAVE_JSON = {
    "web": {
        "results": [
            {"url": "https://ivoclar.com/a.pdf", "title": "DoC A", "description": "decl A"},
            {"url": "https://ivoclar.com/b.pdf", "title": "DoC B", "description": "decl B"},
        ]
    }
}


def test_brave_parses_web_results_into_candidates():
    http = _fake_http(BRAVE_JSON)
    adapter = S.BraveSearchAdapter("k-brave", http=http)
    out = adapter.query('Ivoclar "Tetric" declaration of conformity pdf')
    assert [(c.url, c.title, c.snippet, c.rank) for c in out] == [
        ("https://ivoclar.com/a.pdf", "DoC A", "decl A", 0),
        ("https://ivoclar.com/b.pdf", "DoC B", "decl B", 1),
    ]


def test_brave_request_shape():
    http = _fake_http(BRAVE_JSON)
    adapter = S.BraveSearchAdapter("k-brave", count=7, http=http)
    adapter.query("q text")
    call = http.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert call["headers"]["X-Subscription-Token"] == "k-brave"
    assert call["params"]["q"] == "q text"
    assert call["params"]["count"] == 7


def test_brave_drops_result_without_url():
    http = _fake_http({"web": {"results": [{"title": "no url", "description": "x"}]}})
    assert S.BraveSearchAdapter("k", http=http).query("q") == []


def test_brave_empty_response_is_empty_list():
    assert S.BraveSearchAdapter("k", http=_fake_http({})).query("q") == []


def test_brave_missing_key_raises():
    with pytest.raises(RuntimeError, match="BRAVE_API_KEY"):
        S.BraveSearchAdapter("", http=_fake_http(BRAVE_JSON)).query("q")


# --------------------------------------------------------------------------- #
# Serper fallback — same SearchCandidate shape from a different JSON.
# --------------------------------------------------------------------------- #
SERPER_JSON = {
    "organic": [
        {"link": "https://voco.com/x.pdf", "title": "EC cert", "snippet": "certificate"},
    ]
}


def test_serper_parses_organic_into_candidates():
    http = _fake_http(SERPER_JSON)
    out = S.SerperSearchAdapter("k-serper", http=http).query("voco q")
    assert [(c.url, c.title, c.snippet, c.rank) for c in out] == [
        ("https://voco.com/x.pdf", "EC cert", "certificate", 0)
    ]


def test_serper_request_shape():
    http = _fake_http(SERPER_JSON)
    S.SerperSearchAdapter("k-serper", count=5, http=http).query("voco q")
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://google.serper.dev/search"
    assert call["headers"]["X-API-KEY"] == "k-serper"
    assert call["json"]["q"] == "voco q"


# --------------------------------------------------------------------------- #
# FakeSearchAdapter — the test/dev double the handler tests inject.
# --------------------------------------------------------------------------- #
def test_fake_adapter_returns_injected_candidates():
    cands = [S.SearchCandidate(url="u", title="t", snippet="s", rank=0)]
    assert S.FakeSearchAdapter(cands).query("anything") == cands


def test_fake_adapter_can_raise():
    boom = RuntimeError("brave down")
    with pytest.raises(RuntimeError, match="brave down"):
        S.FakeSearchAdapter(error=boom).query("q")


# --------------------------------------------------------------------------- #
# Factory — config-switched over the closed `search` enum.
# --------------------------------------------------------------------------- #
def _cfg(search, *, brave="", serper=""):
    base = load_config()
    return dataclasses.replace(
        base,
        adapters=dataclasses.replace(base.adapters, search=search),
        connection=dataclasses.replace(
            base.connection, brave_api_key=brave, serper_api_key=serper
        ),
    )


def test_make_search_adapter_brave():
    a = S.make_search_adapter(_cfg("brave", brave="bk"))
    assert isinstance(a, S.BraveSearchAdapter)
    assert a.api_key == "bk"


def test_make_search_adapter_serper():
    a = S.make_search_adapter(_cfg("serper", serper="sk"))
    assert isinstance(a, S.SerperSearchAdapter)
    assert a.api_key == "sk"


def test_make_search_adapter_uses_max_candidates_count():
    a = S.make_search_adapter(_cfg("brave", brave="bk"))
    assert a.count == load_config().discovery.max_search_candidates


def test_make_search_adapter_unknown_raises():
    with pytest.raises(ValueError, match="unknown search adapter"):
        S.make_search_adapter(_cfg("google"))
