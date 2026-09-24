"""The one thing that talks to Business Central.

Network is faked with `httpx.MockTransport`, the same way `tests/test_fetcher.py`
does it -- no socket is opened by this suite.

The login is NTLM carried inside `WWW-Authenticate: Negotiate`, measured against
the real server on 2026-09-24 (Basic and bare `NTLM` are both refused). The fake
server below plays the server half of that handshake with pyspnego itself, so
the tokens are real and a wrong password is really rejected -- a scripted
header sequence would pass against a client that sends garbage.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import spnego

from app.adapters import bc_client
from app.adapters.bc_client import BcAuthRejected, BcClient

USER, PASSWORD = "DENTALIA3\\svc", "secret"


@pytest.fixture(autouse=True)
def _ntlm_user_file(tmp_path, monkeypatch):
    """pyspnego's NTLM server reads accepted credentials from this file."""
    f = tmp_path / "ntlm_users"
    f.write_text("DENTALIA3:svc:secret\n")
    monkeypatch.setenv("NTLM_USER_FILE", str(f))
    bc_client._REJECTED.clear()
    yield
    bc_client._REJECTED.clear()


class FakeBc:
    """BC's HTTP.sys front: `401 Negotiate` until a handshake completes.

    `persistent` models a server that keeps the connection authenticated after
    a handshake, so the next request needs no header. MockTransport has no
    connection, so "the connection" here is the FakeBc instance.
    """

    def __init__(self, *, persistent=False, body=None):
        self.persistent = persistent
        self.body = body if body is not None else {"value": []}
        self.calls = []
        self._ctx = None
        self._authed = False

    def __call__(self, request):
        auth = request.headers.get("authorization")
        self.calls.append((request.method, auth, request.content))
        if self._authed and self.persistent:
            return self._ok()
        if not auth or not auth.startswith("Negotiate "):
            return httpx.Response(401, headers={"WWW-Authenticate": "Negotiate"})
        token = base64.b64decode(auth.split(" ", 1)[1])
        if self._ctx is None:
            self._ctx = spnego.server(protocol="ntlm")
        try:
            out = self._ctx.step(token)
        except spnego.exceptions.SpnegoError:
            self._ctx = None
            return httpx.Response(401, headers={"WWW-Authenticate": "Negotiate"})
        if not self._ctx.complete:
            challenge = base64.b64encode(out).decode()
            return httpx.Response(401, headers={"WWW-Authenticate": f"Negotiate {challenge}"})
        self._ctx = None
        self._authed = True
        return self._ok()

    def _ok(self):
        return httpx.Response(200, json=self.body)


def _client(handler, **kw):
    return BcClient("http://bc/api", transport=httpx.MockTransport(handler), **kw)


def test_it_logs_in_with_ntlm_inside_negotiate_and_returns_the_body():
    bc = FakeBc(body={"value": [{"no": "A1"}]})

    got = _client(bc, username=USER, password=PASSWORD).get("http://bc/api/allitems")

    assert got == {"value": [{"no": "A1"}]}
    schemes = [a.split(" ", 1)[0] if a else None for _, a, _ in bc.calls]
    assert schemes == [None, "Negotiate", "Negotiate"]


def test_basic_is_never_sent():
    """The server refuses Basic; sending it would also put the password on the
    wire in clear over plain HTTP."""
    bc = FakeBc()

    _client(bc, username=USER, password=PASSWORD).get("http://bc/api/allitems")

    assert not any(a and a.startswith("Basic ") for _, a, _ in bc.calls)


def test_a_persistent_connection_costs_one_round_trip_after_the_first():
    bc = FakeBc(persistent=True)
    c = _client(bc, username=USER, password=PASSWORD)

    c.get("http://bc/api/allitems")
    first = len(bc.calls)
    c.get("http://bc/api/allitems?$skip=100")

    assert first == 3
    assert len(bc.calls) - first == 1


def test_patch_body_survives_the_handshake_resend():
    bc = FakeBc()

    status, _ = _client(bc, username=USER, password=PASSWORD).patch_item(
        "A1", {"pteValidCECertificate": True})

    assert status == 200
    method, _, content = bc.calls[-1]
    assert method == "PATCH" and b"pteValidCECertificate" in content


@pytest.mark.parametrize("no, key", [
    ("0.032.1703", "0.032.1703"),
    ("000 595", "000%20595"),
    ("0800/25", "0800%2F25"),
    ("OBROČKI", "OBRO%C4%8CKI"),
    ("FT-220#A/002", "FT-220%23A%2F002"),
    ("A+B,C*", "A%2BB%2CC%2A"),
    ("O'NEIL", "O%27%27NEIL"),
])
def test_an_item_is_addressed_by_its_number_fully_encoded(no, key):
    """`dataitem`'s key is `no` (BC $metadata, 2026-09-24). Every one of these
    shapes was GET-able live with the value fully percent-encoded, `/` as `%2F`
    included; 3.195 of 15.958 item numbers carry one of these characters. A
    quote is doubled first -- that is the OData string literal, not HTTP."""
    seen = {}

    def handler(request):
        seen["path"] = request.url.raw_path.decode()
        return httpx.Response(200, json={})

    BcClient("http://bc/api/companies(1)", transport=httpx.MockTransport(handler)
             ).patch_item(no, {"pteWarehouseURL": "u"})

    assert seen["path"] == f"/api/companies(1)/dataitems('{key}')"


def test_a_patch_overwrites_whatever_bc_holds():
    """`If-Match: *`: our ledger is the diff source and BC is never read back,
    so there is no ETag to send -- and these three fields are ours to own."""
    seen = {}

    def handler(request):
        seen["if_match"] = request.headers.get("if-match")
        return httpx.Response(200, json={})

    _client(handler).patch_item("A1", {"pteWarehouseURL": "u"})

    assert seen["if_match"] == "*"


def test_a_wrong_password_raises_bc_auth_rejected():
    bc = FakeBc()

    with pytest.raises(BcAuthRejected):
        _client(bc, username=USER, password="wrong").get("http://bc/api/allitems")


def test_a_rejected_login_is_not_tried_again_in_this_process():
    """A domain account locks after a few failed logins. Job retries and the
    hourly drift cron would get there on their own, so once these exact
    credentials are refused, no later client in this process sends them."""
    bc = FakeBc()
    with pytest.raises(BcAuthRejected):
        _client(bc, username=USER, password="wrong").get("http://bc/api/allitems")
    sent = len(bc.calls)

    with pytest.raises(BcAuthRejected):
        _client(bc, username=USER, password="wrong").get("http://bc/api/allitems")

    assert len(bc.calls) == sent


def test_changed_credentials_are_tried_again():
    bc = FakeBc()
    with pytest.raises(BcAuthRejected):
        _client(bc, username=USER, password="wrong").get("http://bc/api/allitems")

    assert _client(bc, username=USER, password=PASSWORD).get("http://bc/api/allitems") == {"value": []}


def test_no_credentials_means_no_authorization_header():
    """An unconfigured install must not send an empty credential, which some
    servers accept as an anonymous identity rather than rejecting."""
    bc = FakeBc()

    with pytest.raises(httpx.HTTPStatusError):
        _client(bc).get("http://bc/api/allitems")

    assert [a for _, a, _ in bc.calls] == [None]


def test_an_error_status_raises_rather_than_returning_a_body():
    """A 401 page decoded as JSON would be an empty `value` -- an empty
    catalogue, which INGEST reads as "nothing changed"."""
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler, username=USER, password=PASSWORD).get("http://bc/api/allitems")


def test_it_asks_for_json():
    seen = {}

    def handler(request):
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"value": []})

    _client(handler).get("http://bc/api/allitems")

    assert "application/json" in seen["accept"]
