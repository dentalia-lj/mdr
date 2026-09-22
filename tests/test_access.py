"""Who may reach what (spec §2).

Table-driven, because the policy is a table: the project's convention is that
anything with dispositions gets a table-driven test rather than one function
per cell.

Every client here sets `require_authenticated_user=True`. With it False -- the
local-dev and default-test posture -- there is no proxy in front, every caller
classes as `staff`, and the guard is a no-op. That is the existing contract of
`_authenticated_user` (web/app.py), generalised, not a new rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.access import (
    ANON, BC, SERVICE, STAFF, allowed_classes, classify,
)
from web.access import _ANY as _ANY_CLASSES
from web.app import create_app

API_KEY = "shop-key-1"
BC_KEY = "bc-shared"


@pytest.fixture
def guarded(test_db_url, tmp_path):
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        require_authenticated_user=True,
        api_keys=f"{API_KEY},shop-key-2",
        bc_link_key=BC_KEY,
    )
    return TestClient(create_app(cfg), raise_server_exceptions=False)


@pytest.mark.parametrize("path,expected", [
    ("/healthz", {STAFF, SERVICE, BC, ANON}),
    ("/item/12345", {BC, SERVICE, STAFF}),
    ("/api/items/12345/documents", {SERVICE, STAFF}),
    ("/api/kpi", {SERVICE, STAFF}),
    ("/documents/42/file", {SERVICE, STAFF}),
    ("/documents/42", {STAFF}),
    ("/documents", {STAFF}),
    ("/archive/ab/cd/x.pdf", {STAFF}),
    ("/items", {STAFF}),
    ("/staging", {STAFF}),
    ("/", {STAFF}),
])
def test_policy_table(path, expected):
    assert allowed_classes(path) == expected


def test_archive_is_staff_only_even_for_a_valid_key(guarded):
    """`/archive/{path}` serves the archive BY PATH, which is walkable. The
    shop has no reason to want it -- `/documents/{id}/file` resolves the handle
    server-side and is the supported way to get bytes."""
    resp = guarded.get("/archive/ab/cd/x.pdf", headers={"X-API-Key": API_KEY})
    assert resp.status_code == 403


def test_service_key_reaches_the_api(guarded, conn):
    _seed_item_with_doc(conn, "ACC-1")
    resp = guarded.get(
        "/api/items/ACC-1/documents", headers={"X-API-Key": API_KEY}
    )
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "ACC-1"


def test_second_key_in_the_list_also_works(guarded, conn):
    """Rotation must not need a downtime swap."""
    _seed_item_with_doc(conn, "ACC-ROT")
    resp = guarded.get(
        "/api/items/ACC-ROT/documents", headers={"X-API-Key": "shop-key-2"}
    )
    assert resp.status_code == 200


def test_bogus_key_is_refused_with_401(guarded):
    resp = guarded.get("/api/kpi", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_no_credential_at_all_is_refused(guarded):
    assert guarded.get("/api/kpi").status_code == 401


def test_service_key_cannot_reach_the_ui(guarded):
    assert guarded.get("/items", headers={"X-API-Key": API_KEY}).status_code == 403


def test_bc_key_cannot_reach_the_api(guarded):
    """The BC link key is for one path. It is not an API credential."""
    assert guarded.get(f"/api/kpi?k={BC_KEY}").status_code == 401


def test_healthz_needs_nothing(guarded):
    """The container healthcheck must not carry a credential."""
    assert guarded.get("/healthz").status_code == 200


def test_staff_header_still_works(guarded):
    assert guarded.get("/items", headers={"X-Forwarded-User": "admin"}).status_code == 200


def test_empty_api_keys_disables_the_service_class(test_db_url, tmp_path):
    """Empty never means 'allow everyone'."""
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        require_authenticated_user=True, api_keys="", bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    assert client.get("/api/kpi", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/api/kpi", headers={"X-API-Key": "anything"}).status_code == 401


def test_guard_is_a_noop_without_a_proxy_in_front(test_db_url, tmp_path):
    """require_authenticated_user=False means no proxy, i.e. local dev. Every
    caller is staff, exactly as `_authenticated_user` already behaves."""
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    client = TestClient(create_app(cfg))
    assert client.get("/items").status_code == 200
    assert client.get("/api/kpi").status_code == 200


class _FakeRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


def test_classify_prefers_staff_over_a_key():
    cfg = Web(require_authenticated_user=True, api_keys=API_KEY, bc_link_key=BC_KEY)
    req = _FakeRequest(headers={"X-Forwarded-User": "admin", "X-API-Key": API_KEY})
    assert classify(req, cfg) == STAFF


def test_classify_returns_anon_with_nothing():
    cfg = Web(require_authenticated_user=True, api_keys=API_KEY, bc_link_key=BC_KEY)
    assert classify(_FakeRequest(), cfg) == ANON


def test_classify_bc_from_query_key():
    cfg = Web(require_authenticated_user=True, api_keys=API_KEY, bc_link_key=BC_KEY)
    assert classify(_FakeRequest(query={"k": BC_KEY}), cfg) == BC


# --------------------------------------------------------------------------- #
# Routes FastAPI registers itself (2026-08-25).
#
# `/docs`, `/openapi.json` and the `/static` mount are NOT registered through
# the APIRouter, so the app-level `dependencies=[...]` never ran for them: the
# policy table claimed staff-only and did not govern them. Measured, not
# reasoned -- all three answered 200 to no credential at all, which also meant
# Caddy's `X-API-Key` skip handed the schema listing to anyone sending that
# header with any value.
#
# `/static` is now deliberately PUBLIC at both layers rather than locked down:
# it is CSS, a logo and no data, and the BC item card cannot render without it
# (the card skips Basic auth, so its stylesheet request carries no credential).
# --------------------------------------------------------------------------- #


def test_openapi_is_staff_only_in_the_policy():
    assert allowed_classes("/openapi.json") == frozenset({STAFF})
    assert allowed_classes("/docs") == frozenset({STAFF})


def test_static_is_public_in_the_policy():
    """Deliberately open: the BC item card's stylesheet carries no credential."""
    assert allowed_classes("/static/css/style.css") == _ANY_CLASSES


def test_openapi_refuses_an_uncredentialled_request(guarded):
    assert guarded.get("/openapi.json").status_code == 403
    assert guarded.get("/docs").status_code == 403


def test_openapi_refuses_a_bogus_api_key(guarded):
    """Caddy skips Basic for anything carrying this header, so the app is the
    only thing standing between a bogus key and the schema listing."""
    assert guarded.get("/openapi.json", headers={"X-API-Key": "nope"}).status_code == 403


def test_openapi_still_works_for_staff(guarded):
    """The /api-reference page links out to these; they must not be dead."""
    resp = guarded.get("/openapi.json", headers={"X-Forwarded-User": "admin"})
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "Dentalia Compliance Registry"
    assert guarded.get("/docs", headers={"X-Forwarded-User": "admin"}).status_code == 200


def test_static_is_served_without_a_credential(guarded):
    assert guarded.get("/static/css/style.css").status_code == 200


# --------------------------------------------------------------------------- #
# Defence in depth against an unresolved proxy placeholder (2026-08-25).
#
# Caddy's `header_up X-Forwarded-User {http.auth.user.id}` fires even on a
# request that SKIPPED basic_auth, and sends the literal text
# `{http.auth.user.id}`. Measured against a real proxy with an echo upstream.
# Non-empty, so `classify` read it as a signed-in staff user: any request
# carrying any `X-API-Key` value got full staff access to the whole UI, and
# `gate.apply` would have recorded `{http.auth.user.id}` as `decided_by`.
#
# The Caddyfile now strips the header on unauthenticated paths, which is the
# real fix. This is the second layer: a username is never a placeholder, so the
# app refuses one regardless of what any proxy in front of it does.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [
    "{http.auth.user.id}",
    "{env.SOMETHING}",
    "admin}",
    "{admin",
])
def test_placeholder_shaped_user_is_not_staff(value):
    cfg = Web(require_authenticated_user=True, api_keys=API_KEY, bc_link_key=BC_KEY)
    assert classify(_FakeRequest(headers={"X-Forwarded-User": value}), cfg) == ANON


def test_a_real_username_is_still_staff():
    cfg = Web(require_authenticated_user=True, api_keys=API_KEY, bc_link_key=BC_KEY)
    assert classify(_FakeRequest(headers={"X-Forwarded-User": "admin"}), cfg) == STAFF


def test_bogus_key_does_not_become_staff_via_a_placeholder(guarded):
    """The end-to-end shape of the hole: a garbage key plus a placeholder
    header must not reach a staff-only page."""
    resp = guarded.get("/items", headers={
        "X-API-Key": "BOGUS", "X-Forwarded-User": "{http.auth.user.id}",
    })
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# The machine-facing hostname (2026-09-18).
#
# api.cw.dentalia.si reaches the same Caddy and the same app as the office name
# cw.dentalia.si, so without a rule it would serve the whole office UI behind
# Basic auth. The Caddyfile answers 404 on that host for every path except the
# ones this module opens to a non-staff caller. The two lists must stay one
# list: a route opened to the shop here and not there is unreachable on the API
# host, and a path let through there that stays staff-only here is office UI on
# a name that is meant to carry none.
# --------------------------------------------------------------------------- #

_CADDYFILE = Path(__file__).resolve().parents[1] / "Caddyfile"
API_HOST = "api.cw.dentalia.si"


def _api_host_rule():
    text = _CADDYFILE.read_text()
    block = text.split("@api_host_refused {", 1)[1].split("\n\t}", 1)[0]
    assert f"host {API_HOST}" in block
    paths = re.search(r"not path ([^\n]+)", block).group(1).split()
    regexes = re.findall(r"not path_regexp (\S+)", block)
    return paths, regexes


def _api_host_lets_through(path: str) -> bool:
    """Caddy's `path` semantics for what the rule uses: a trailing `*` is a
    prefix, anything else is exact. `path_regexp` is an unanchored search."""
    paths, regexes = _api_host_rule()
    for p in paths:
        if (p.endswith("*") and path.startswith(p[:-1])) or path == p:
            return True
    return any(re.search(rx, path) for rx in regexes)


@pytest.mark.parametrize("path", [
    # opened to machines by allowed_classes
    "/api/items", "/api/documents/7", "/item/1.02.003", "/static/css/style.css",
    "/healthz", "/documents/12/file",
    # staff-only, so office UI
    "/", "/items", "/documents", "/documents/12", "/documents/12/file/x",
    "/archive/GC/a.pdf", "/api-reference", "/staging", "/import", "/scheduler",
])
def test_api_host_serves_exactly_what_the_app_opens_to_machines(path):
    opened = allowed_classes(path) != frozenset({STAFF})
    assert _api_host_lets_through(path) == opened, path


def test_api_host_refusal_is_written_before_the_login_handle():
    """Caddy keeps `handle` blocks whose matchers are named sets in the order
    they are written, and runs the first that matches. Written after
    `handle @protected`, the refusal would never run: a staff path on the API
    host would get the login prompt instead of a 404."""
    text = _CADDYFILE.read_text()
    # The directive lines, not the words: the comments name both handles too.
    assert (text.index("\n\thandle @api_host_refused {")
            < text.index("\n\thandle @protected {"))
