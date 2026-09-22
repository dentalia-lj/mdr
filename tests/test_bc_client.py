"""The one thing that talks to Business Central.

Network is faked with `httpx.MockTransport`, the same way `tests/test_fetcher.py`
does it -- no socket is opened by this suite.

**Unverified until access opens:** the auth scheme. BC on-premises commonly takes
Basic or NTLM and nobody has told us which, so this sends Basic when credentials
are configured and nothing when they are not. That is recorded in
`docs/dev/limits.md` rather than guessed at silently.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.bc_client import BcClient


def _client(handler, **kw):
    return BcClient("https://bc/api", transport=httpx.MockTransport(handler), **kw)


def test_it_returns_the_decoded_body():
    def handler(request):
        return httpx.Response(200, json={"value": [{"no": "A1"}]})

    assert _client(handler).get("https://bc/api/allitems") == {"value": [{"no": "A1"}]}


def test_credentials_are_sent_when_configured():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"value": []})

    _client(handler, username="u", password="p").get("https://bc/api/allitems")

    assert seen["auth"] is not None and seen["auth"].startswith("Basic ")


def test_no_credentials_means_no_authorization_header():
    """An unconfigured install must not send an empty Basic header, which some
    servers accept as an anonymous identity rather than rejecting."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"value": []})

    _client(handler).get("https://bc/api/allitems")

    assert seen["auth"] is None


def test_an_error_status_raises_rather_than_returning_a_body():
    """A 401 page decoded as JSON would be an empty `value` -- an empty
    catalogue, which INGEST reads as "nothing changed"."""
    def handler(request):
        return httpx.Response(401, json={"error": "denied"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get("https://bc/api/allitems")


def test_it_asks_for_json():
    seen = {}

    def handler(request):
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"value": []})

    _client(handler).get("https://bc/api/allitems")

    assert "application/json" in seen["accept"]
