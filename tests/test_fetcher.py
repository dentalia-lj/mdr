"""FETCH transport tier (S1.4) — HttpxFetcher conditional GET.

Spec: docs/specs/fetch.md §1/§3.1/§3.3. Network is faked via httpx.MockTransport
(no real HTTP). The PlaywrightFetcher browser drive is not unit-tested here
(real-browser I/O, same posture as the live-LLM path in extract); the handler
tests inject a fake fetcher for the Playwright ladder. `normalize_url` lives in
app/urls.py (shared with DISCOVER) and is covered by tests/test_urls.py.
"""

from __future__ import annotations

import httpx

from app.adapters.fetcher import FetchResult, HttpxFetcher

# --------------------------------------------------------------------------- #
# FetchResult
# --------------------------------------------------------------------------- #


def test_bot_wall_is_403():
    assert FetchResult(403, None, None, None, None, "u").bot_wall is True
    assert FetchResult(200, b"x", None, None, None, "u").bot_wall is False


# --------------------------------------------------------------------------- #
# HttpxFetcher conditional GET (httpx.MockTransport — no network)
# --------------------------------------------------------------------------- #


def _fetcher(handler) -> HttpxFetcher:
    return HttpxFetcher(transport=httpx.MockTransport(handler))


def test_httpx_get_200_maps_all_fields():
    def handler(req):
        return httpx.Response(
            200,
            headers={
                "ETag": '"abc"',
                "Last-Modified": "Wed, 21 Oct 2024 07:28:00 GMT",
                "Content-Type": "application/pdf",
            },
            content=b"%PDF body",
        )

    r = _fetcher(handler).get("https://x.example/doc.pdf")
    assert r.status == 200
    assert r.body == b"%PDF body"
    assert r.etag == '"abc"'
    assert r.last_modified == "Wed, 21 Oct 2024 07:28:00 GMT"
    assert r.content_type == "application/pdf"


def test_httpx_get_sends_conditional_headers():
    seen = {}

    def handler(req):
        seen["inm"] = req.headers.get("If-None-Match")
        seen["ims"] = req.headers.get("If-Modified-Since")
        return httpx.Response(304)

    _fetcher(handler).get(
        "https://x.example/doc.pdf",
        etag='"abc"',
        last_modified="Wed, 21 Oct 2024 07:28:00 GMT",
    )
    assert seen["inm"] == '"abc"'
    assert seen["ims"] == "Wed, 21 Oct 2024 07:28:00 GMT"


def test_httpx_get_304_has_no_body():
    r = _fetcher(lambda req: httpx.Response(304)).get("https://x.example/d.pdf")
    assert r.status == 304
    assert r.body is None


def test_httpx_get_403_is_bot_wall():
    r = _fetcher(lambda req: httpx.Response(403)).get("https://x.example/d.pdf")
    assert r.status == 403
    assert r.bot_wall is True
    assert r.body is None
