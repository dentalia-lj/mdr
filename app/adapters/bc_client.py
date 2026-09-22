"""The one thing in this repo that talks to Business Central.

Deliberately small: a GET that returns decoded JSON and a PATCH that returns
`(status, body)`. Paging, profiles and field rules live above it -- this layer
knows about HTTP and credentials and nothing else, so the read adapter and the
`bc.push` handler can both be tested against a fake without either of them
learning what a header is.

**The auth scheme is unverified.** BC on-premises commonly takes Basic or NTLM,
b-s.si have not said which, and external access is still blocked, so nobody has
been able to try. This sends Basic when credentials are configured and no
`Authorization` header at all when they are not -- an empty Basic header is
worse than none, because some servers read it as an anonymous identity rather
than rejecting it. Recorded in `docs/dev/limits.md`.
"""

from __future__ import annotations

import httpx

#: Long enough for a full page from an on-premises ERP over a VPN, short enough
#: that a wedged endpoint fails a job rather than holding a worker forever.
DEFAULT_TIMEOUT_S = 30.0


class BcClient:
    """HTTP against one Business Central company endpoint."""

    def __init__(self, base_url: str, *, username: str = "", password: str = "",
                 transport=None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        auth = (username, password) if username or password else None
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

    def patch(self, url: str, fields: dict) -> tuple[int, str]:
        """One item's writable fields. Returns `(status, body)` rather than
        raising: `bc.push` counts a refusal per item and carries on, and it
        needs the status to decide whether the value was actually taken."""
        resp = self._client.patch(url, json=fields)
        return resp.status_code, resp.text[:1000]

    def close(self) -> None:
        self._client.close()
