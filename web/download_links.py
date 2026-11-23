"""Signed download links, issued on click (2026-11-23 webshop API spec § 5.4).

The webstore page never holds one of these. It links to the webstore's own
URL; on click the webstore's server calls `GET /api/documents/{id}/link` with
its key and answers the customer's browser with a 302 to the `url` it gets
back. The browser then downloads straight from here with no credential: the
token is the credential.

The token names one document and an expiry and is signed with
`web.download_link_secret`. Nothing is stored -- the web process stays a
reader -- so a token cannot be revoked early; it simply dies after
`download_link_ttl_s` (5 minutes), which only has to cover the redirect hop and
a slow first byte. A page left open all day still works: every click issues a
new one.

Visibility is checked when the link is ISSUED and again when it is USED: a
document unpublished between the two is not served. The rule is the webstore's
(`item_docs.shop_visible_document`), not the staff UI's.

An empty secret switches both routes off (404). It never means "unsigned".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from web import access

from web.item_docs import local_today, shop_visible_document

#: Both refusals one constant each, so the route, the tests and the handover
#: doc cannot word them three ways.
LINK_GONE = "this download link has expired or is not valid; open the document again"
NOT_AVAILABLE = "document not available"


def _sig(secret: str, doc_id: int, exp: int) -> str:
    mac = hmac.new(secret.encode(), f"{doc_id}.{exp}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()


def sign(doc_id: int, secret: str, now: int, ttl_s: int) -> tuple[str, int]:
    """`(token, exp)`: `"{doc_id}.{exp}.{sig}"`, valid up to and including
    `exp` (unix seconds)."""
    exp = int(now) + int(ttl_s)
    return f"{doc_id}.{exp}.{_sig(secret, doc_id, exp)}", exp


def verify(token: str, secret: str, now: int) -> int | None:
    """The document id, or None for anything malformed, altered, signed with
    another secret, or past its expiry. Constant-time on the signature."""
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    doc_id, exp = int(parts[0]), int(parts[1])
    if not hmac.compare_digest(parts[2], _sig(secret, doc_id, exp)):
        return None
    if int(now) > exp:
        return None
    return doc_id


def register_routes(app, conn_factory, cfg) -> None:
    from web.app import _document_file_response  # cycle, as in web/item_link.py

    @app.get("/api/documents/{doc_id:int}/link")
    def api_document_link(request: Request, doc_id: int):
        if not cfg.download_link_secret:
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        with conn_factory() as conn:
            row = shop_visible_document(conn, doc_id, local_today())
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        if access.caller(request) == access.SERVICE:
            # Counted, never blocked (spec § 5.5): one key serves every customer.
            request.app.state.limiter.note_link(access.key_id(request), doc_id)
        token, exp = sign(doc_id, cfg.download_link_secret, now=int(time.time()),
                          ttl_s=cfg.download_link_ttl_s)
        return {
            "url": f"{cfg.public_base_url}/d/{token}",
            "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat(),
        }

    @app.get("/d/{token}", include_in_schema=False)
    def download(request: Request, token: str):
        if not cfg.download_link_secret:
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        doc_id = verify(token, cfg.download_link_secret, now=int(time.time()))
        if doc_id is None:
            raise HTTPException(status_code=410, detail=LINK_GONE)
        with conn_factory() as conn:
            row = shop_visible_document(conn, doc_id, local_today())
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        retry = request.app.state.limiter.check_download(access.client_ip(request), doc_id)
        if retry is not None:
            raise HTTPException(status_code=429, detail=access.RATE_LIMITED,
                                headers={"Retry-After": str(retry)})
        # Bytes only, never a redirect: `_document_file_response` hands an
        # http(s) archive_url to the browser as a 307, which here would turn a
        # five-minute link into a permanent storage address.
        if urlsplit(row["archive_url"] or "").scheme in ("http", "https"):
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        return _document_file_response(row, cfg)
