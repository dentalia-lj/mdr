"""Who may reach what (spec §2).

Three caller classes, one policy function. Installed as a **sync FastAPI
dependency**, not an ASGI middleware: `@app.middleware("http")` forces
`async def`, and this codebase is sync by ruling (CLAUDE.md). A global
`dependencies=[...]` on the app runs for every matched route and keeps the
whole call path synchronous.

The classes exist because three consumers reach this app three different ways
and only one of them has a browser:

- `staff`   -- Caddy Basic auth, already deployed, unchanged.
- `service` -- the webshop BACKEND (Erpium), server-to-server, `X-API-Key`.
               Only the webstore routes (`SERVICE_ROUTES`), always in the
               webstore view (2026-11-23 webshop API spec).
- `bc`      -- a Business Central item-card hyperlink, `?k=`. BC builds links
               by string concatenation, so anything BC must COMPUTE (an HMAC,
               a per-item token) is unusable. Hence one shared static key.

When `require_authenticated_user` is False there is no proxy in front (local
dev, tests) and every caller is `staff`. That is the existing contract of
`web/app.py::_authenticated_user`, generalised to every route, not a new rule.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable

from fastapi import HTTPException, Request

STAFF = "staff"
SERVICE = "service"
BC = "bc"
ANON = "anon"

_ANY = frozenset({STAFF, SERVICE, BC, ANON})
_STAFF_ONLY = frozenset({STAFF})
# The BC card's surface. NOT the webstore's: with a key, `/item/*` would hand
# out the full view and PDF bytes outside the download links and every limit
# (2026-11-23 webshop API spec § 5.1). Erpium uses `/api/`.
_ITEM_LINK = frozenset({BC, STAFF})
_SERVICE_API = frozenset({SERVICE, STAFF})

#: The only `/api/` routes an `X-API-Key` caller may reach: the webstore's
#: (2026-11-23 webshop API spec § 5.1). Everything else under `/api/` is staff
#: only -- the KPI board, a document's full evidence, the item list per
#: manufacturer (the supplier map). Anchored, because a prefix would open
#: whatever is mounted under it later. `Caddyfile`'s API-host allowlist mirrors
#: this tuple line for line (`tests/test_access.py`).
SERVICE_ROUTES = tuple(re.compile(p) for p in (
    r"^/api/manufacturers$",
    # By stable `manufacturer.id` only; the name-addressed forms live under
    # `/by-name/` and are staff's (2026-11-23 webshop API spec § 5.3).
    r"^/api/manufacturers/\d+$",
    r"^/api/manufacturers/\d+/documents$",
    # The single item (`:path`, refs contain `/`) and, separately, the batch:
    # `.+` needs at least one character between, which the batch has not.
    r"^/api/items/.+/documents$",
    r"^/api/items/documents$",
    r"^/api/documents/\d+/link$",
))


def allowed_classes(path: str) -> frozenset[str]:
    """Which caller classes may reach `path`. Default-deny: anything not named
    here is staff-only, so a route added later is private until someone
    deliberately opens it."""
    if path == "/healthz":
        return _ANY
    if path.startswith("/static/"):
        # Deliberately public, at BOTH layers. It is CSS, a logo and no data,
        # and the BC item card cannot render without it: that card skips Basic
        # auth (Caddyfile), so the stylesheet request the page then makes
        # carries no credential of any kind. Locking this down would serve an
        # unstyled page to the one consumer this whole surface exists for.
        return _ANY
    if path.startswith("/d/"):
        # A signed download link (`web/download_links.py`). The customer's
        # browser follows a 302 here with nothing attached: the token is the
        # credential, checked by the route.
        return _ANY
    if path.startswith("/item/"):
        return _ITEM_LINK
    if path.startswith("/api/"):
        if any(rx.match(path) for rx in SERVICE_ROUTES):
            return _SERVICE_API
        return _STAFF_ONLY
    if path.startswith("/archive/"):
        # Served BY PATH, therefore walkable. Staff only, even with a valid
        # API key. So is `/documents/{id}/file` (default below): it serves
        # ANY document by sequential id, staged and rejected included, and was
        # the key's way into the whole archive until 2026-11-23.
        return _STAFF_ONLY
    return _STAFF_ONLY


def _split_keys(raw: str) -> tuple[str, ...]:
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def _matches_any(candidate: str, keys: tuple[str, ...]) -> bool:
    """True if `candidate` equals any key. Iterates the whole list rather than
    returning early, so the number of comparisons does not depend on which key
    matched, and uses compare_digest for each."""
    found = False
    for key in keys:
        if hmac.compare_digest(candidate, key):
            found = True
    return found


def trusted_user(request, cfg) -> str:
    """The proxy-supplied username, or "" if there isn't a credible one.

    A username never contains a brace. Caddy's `header_up X-Forwarded-User
    {http.auth.user.id}` fires even on requests that skipped basic_auth and
    sends the literal, unresolved placeholder text -- measured 2026-08-25
    against a real proxy with an echo upstream, which received
    `X-Forwarded-User='{http.auth.user.id}'`. Non-empty, so it read as a
    signed-in staff user and any `X-API-Key` value bought full staff access.

    The Caddyfile now strips the header on unauthenticated paths; this is the
    second layer, so the app is correct regardless of what fronts it.
    """
    value = request.headers.get(cfg.trusted_user_header, "").strip()
    if "{" in value or "}" in value:
        return ""
    return value


def is_operator(user: str | None, operator_users: tuple[str, ...]) -> bool:
    """Whether a proxy login counts as an operator (office UI redesign spec
    § 2, D6). A second axis, inside `staff`: it decides what a signed-in
    person is offered, never whether they get in.

    An EMPTY list means every login is an operator, so today's single shared
    login keeps every control it has until named logins exist (slice P5b fills
    the list). Otherwise it is exact membership: case-sensitive, like Caddy's
    own `basic_auth` usernames, and `None` (no proxy in front) is never in it.
    """
    if not operator_users:
        return True
    return user is not None and user in operator_users


#: What the refused person is told. It reaches the P7a error page as the
#: exception's `detail`, so it renders under "Technical details" on the HTML
#: page and is the whole body for a JSON caller. One string, so the page, the
#: API and the runbook cannot word this three ways.
OPERATOR_REFUSAL = "This action is for operators."


def operator_guard(cfg, user_of: Callable[[Request], str | None]):
    """The route dependency behind D3's four refused writes.

    A DEPENDENCY rather than a check inside each handler, and that is the point:
    it runs before the handler body, so a refused request never reaches the
    lookup, the enqueue or the commit. "Nothing was changed" on the error page
    is then structurally true rather than a claim each handler has to keep.

    It is narrow on purpose. D3 refuses the playbook edit and code claim, the BC
    push apply, the scheduler run and arm, and `/ingest` -- what changes how the
    machine runs or spends money. The PAGES those live on stay open to every
    staff login, and so does `/import` apply, which is the office's own work.

    `user_of` is `web/app.py::_authenticated_user`, passed in rather than
    re-implemented, so one function decides who the proxy says you are. With no
    proxy in front it returns None and `is_operator` reads the empty list as
    "everyone" -- see the caution in docs/dev/config-reference.md for the one
    combination (a non-empty list and no proxy) that makes nobody an operator.
    """

    def _require_operator(request: Request) -> None:
        if not is_operator(user_of(request), cfg.operator_users):
            raise HTTPException(status_code=403, detail=OPERATOR_REFUSAL)

    return _require_operator


def classify(request, cfg) -> str:
    """The caller's class. Checked most-privileged first, so a staff browser
    that also happens to carry a key is still staff."""
    if not cfg.require_authenticated_user:
        return STAFF
    if trusted_user(request, cfg):
        return STAFF
    api_key = request.headers.get("X-API-Key", "")
    if api_key and _matches_any(api_key, _split_keys(cfg.api_keys)):
        return SERVICE
    link_key = request.query_params.get("k", "")
    if link_key and cfg.bc_link_key and hmac.compare_digest(link_key, cfg.bc_link_key):
        return BC
    return ANON


#: A valid key on an `/api/` route that is not the webstore's. 403, not 401,
#: so the caller can tell "wrong route" from "wrong key".
SERVICE_ROUTE_REFUSAL = "this route is not available to API keys"


def _refuse(path: str, cls: str = ANON) -> None:
    """Refusal codes differ by surface on purpose (spec §7): `/item/*` returns
    404 because a 403 would confirm the item exists, while a service caller
    deserves a diagnosable 401 -- or, holding a valid key on a staff-only
    `/api/` route, a 403."""
    if path.startswith("/item/"):
        raise HTTPException(status_code=404, detail="not found")
    if path.startswith("/api/"):
        if cls == SERVICE:
            raise HTTPException(status_code=403, detail=SERVICE_ROUTE_REFUSAL)
        raise HTTPException(status_code=401, detail="valid X-API-Key required")
    raise HTTPException(status_code=403, detail="staff access required")


def client_ip(request) -> str:
    """The customer's address: the FIRST `X-Forwarded-For` entry. Safe only
    because the host Caddy sets that entry itself and ignores what the client
    sent, and the compose Caddy trusts the host Caddy (`trusted_proxies`).
    Without a proxy (local dev, tests without the header) it is the peer."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    first = forwarded.split(",", 1)[0].strip()
    if first:
        return first
    return request.client.host if request.client else ""


def key_id(request) -> str:
    """A fingerprint of the caller's key for counters and logs: the key itself
    is a secret and never leaves `classify`."""
    key = request.headers.get("X-API-Key", "")
    return hashlib.sha256(key.encode()).hexdigest()[:8]


#: Every 429 says the same thing; `Retry-After` carries the number.
RATE_LIMITED = "too many requests; retry after the number of seconds in Retry-After"


def caller(request: Request) -> str:
    """The caller class `guard` recorded for this request. Routes read it to
    decide the webstore view (`for_shop`) rather than classifying again."""
    return getattr(request.state, "caller", STAFF)


def guard(cfg) -> Callable[[Request], None]:
    """Build the global dependency. Sync, deliberately -- see module docstring.
    Records the class on `request.state.caller` (read back by `caller()`)."""

    def _guard(request: Request) -> None:
        cls = classify(request, cfg)
        request.state.caller = cls
        if cls not in allowed_classes(request.url.path):
            _refuse(request.url.path, cls)
        limiter = getattr(request.app.state, "limiter", None)
        if cls == SERVICE and limiter is not None and request.url.path.startswith("/api/"):
            retry = limiter.check_api(key_id(request))
            if retry is not None:
                raise HTTPException(status_code=429, detail=RATE_LIMITED,
                                    headers={"Retry-After": str(retry)})

    return _guard
