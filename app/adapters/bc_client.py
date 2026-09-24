"""The one thing in this repo that talks to Business Central.

Deliberately small: a GET that returns decoded JSON and a PATCH of one item
that returns `(status, body)`. Paging, profiles and field rules live above it --
this layer knows about HTTP and credentials and nothing else, so the read
adapter and the `bc.push` handler can both be tested against a fake without
either of them learning what a header is.

**The login is NTLM carried inside `Negotiate`**, measured against the real
server on 2026-09-24: BC answers `401` with `WWW-Authenticate: Negotiate` only,
refuses Basic, and ignores a bare `Authorization: NTLM`, but accepts an NTLM
token under the `Negotiate` scheme. Kerberos is out of reach because the server
we run on is not in Dentalia's domain. NTLM authenticates the TCP connection, so
the handshake runs on one kept-alive connection -- httpx reads each 401 to the
end before sending the next leg, which is what keeps the connection reusable.
Transport is plain HTTP by ruling (docs/decisions.md, 2026-09-24); NTLM never
puts the password on the wire.

No credentials configured means no `Authorization` header at all -- an empty
credential is worse than none, because some servers read it as an anonymous
identity rather than rejecting it.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote

import httpx

#: Long enough for a full page from an on-premises ERP over a VPN, short enough
#: that a wedged endpoint fails a job rather than holding a worker forever.
DEFAULT_TIMEOUT_S = 30.0

#: Credentials BC has refused in this process, as digests. The account is a
#: Windows domain account, and a domain locks an account after a few failed
#: logins -- job retries and the hourly drift cron would reach that on their
#: own, and a locked account also stops whatever else Dentalia uses it for. So
#: a refusal is remembered until the process restarts (which is also the only
#: way new credentials arrive) and the same pair is never sent again.
_REJECTED: set[str] = set()


class BcAuthRejected(Exception):
    """BC completed an NTLM handshake and refused the credentials."""


def _digest(username: str, password: str) -> str:
    return hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()


def _negotiate_token(response: httpx.Response) -> bytes | None:
    """The server's token from `WWW-Authenticate: Negotiate <b64>`, `b""` for
    a bare `Negotiate` offer, `None` when Negotiate is not offered at all."""
    for value in response.headers.get_list("www-authenticate"):
        scheme, _, rest = value.strip().partition(" ")
        if scheme.lower() == "negotiate":
            return base64.b64decode(rest) if rest.strip() else b""
    return None


class NtlmNegotiateAuth(httpx.Auth):
    """NTLM inside the `Negotiate` scheme, only when the server asks.

    The request first goes out bare. A server that kept the connection
    authenticated answers it directly; otherwise its `401 Negotiate` starts the
    three-leg handshake: our NEGOTIATE, its CHALLENGE, our AUTHENTICATE.
    """

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._key = _digest(username, password)

    def auth_flow(self, request):
        if self._key in _REJECTED:
            raise BcAuthRejected(
                f"BC refused {self._username!r} earlier in this process; not "
                f"retrying, so the domain account is not locked out")
        response = yield request
        if response.status_code != 401 or _negotiate_token(response) is None:
            return

        import spnego  # only a worker that talks to BC needs it

        ctx = spnego.client(self._username, self._password,
                            hostname=request.url.host, service="HTTP",
                            protocol="ntlm")
        request.headers["Authorization"] = "Negotiate " + base64.b64encode(ctx.step()).decode()
        response = yield request
        challenge = _negotiate_token(response)
        if response.status_code != 401 or not challenge:
            return

        request.headers["Authorization"] = "Negotiate " + base64.b64encode(ctx.step(challenge)).decode()
        response = yield request
        if response.status_code == 401:
            _REJECTED.add(self._key)
            raise BcAuthRejected(
                f"BC refused the login for {self._username!r}: check BC_USERNAME "
                f"(DOMAIN\\user) and BC_PASSWORD. Not retried in this process")


class BcClient:
    """HTTP against one Business Central company endpoint."""

    def __init__(self, base_url: str, *, username: str = "", password: str = "",
                 transport=None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        auth = NtlmNegotiateAuth(username, password) if username or password else None
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout_s,
            auth=auth,
            headers={"Accept": "application/json"},
        )

    def get(self, url: str) -> dict:
        """One page. Raises on any non-2xx.

        Raising matters more than it looks: a 401 page decoded as JSON has no
        `value` key, which reads as an empty catalogue, which INGEST reports as
        "every item unchanged".
        """
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def patch_item(self, no: str, fields: dict) -> tuple[int, str]:
        """One item's writable fields on `dataitems`. Returns `(status, body)`
        rather than raising: `bc.push` counts a refusal per item and carries on,
        and it needs the status to decide whether the value was actually taken.

        The key is `no` (BC `$metadata`, 2026-09-24): the OData string literal
        doubles a quote, then the whole value is percent-encoded, `/` included --
        verified live for space, `/`, `#` and `Č`. `If-Match: *` because our
        ledger is the diff source and BC is never read back, so there is no ETag
        to hold; these three fields are ours to own.
        """
        key = quote(no.replace("'", "''"), safe="")
        resp = self._client.patch(f"{self.base_url}/dataitems('{key}')",
                                  json=fields, headers={"If-Match": "*"})
        return resp.status_code, resp.text[:1000]

    def close(self) -> None:
        self._client.close()
