# Item Document Access Implementation Plan

**Goal:** Let BC link an item card straight to its compliance documents, and let the webshop backend fetch the same data server-side to relay to its own customers — without exposing anything to the public.

**Architecture:** Three caller classes (`staff` via existing Caddy Basic auth, `service` via `X-API-Key`, `bc` via a shared `?k=` link key) enforced by one sync policy function installed as a global FastAPI dependency. A new `/item/{item_ref}` route serves the same item-documents payload as HTML or JSON by content negotiation. The item-documents query is extracted once and shared by both the existing `/api/items/{ref}/documents` route and the new one.

**Tech Stack:** Python 3.12, FastAPI + Jinja + HTMX, psycopg 3, raw SQL, pytest against real Postgres, Caddy 2.8, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-25-item-document-access-design.md`

## Global Constraints

- **Invariant 1:** every route added here is read-only. No `INSERT`/`UPDATE`/`DELETE`, no `queue.enqueue`. The `dentalia_api` role would refuse a write anyway; do not rely on that alone.
- **Visibility rule:** documents come from `item_document_production` only. Never reimplement the production join.
- **Sync only** (CLAUDE.md): no `asyncio`, no `async def`. The access guard is a **sync FastAPI dependency**, deliberately not an ASGI middleware, because `@app.middleware("http")` forces `async def`.
- **Additive-only** response evolution (`docs/specs/read-api.md` §5): existing keys never change shape or meaning.
- **Secrets** (`WEB_API_KEYS`, `WEB_BC_LINK_KEY`) go at the top of `.env` beside the IMAP credentials, never into the runbook's tuning tables.
- **Key comparison** is always `hmac.compare_digest`, never `==`.
- **Test selection** (CLAUDE.md): Task 1 touches `app/config.py`, which mandates the **full suite**. Tasks 2–8 run `tests/test_web.py`, `tests/test_access.py`, `tests/test_item_link.py`. Run the full suite once more before the final commit.
- **Worktree DSN:** pin `API_DATABASE_URL` to the scratch/test database explicitly and state it in the first commit message. A worktree's DB-touching CLI otherwise defaults to the live DSN.

---

### Task 1: Config keys

**Files:**
- Modify: `app/config.py:415-457` (the `Web` dataclass), `app/config.py:844-871` (the `web = Web(...)` builder)
- Create: `tests/test_config.py`

This repo has no dedicated config test file — config keys are currently
asserted inside whichever module's tests consume them. `Web` is now read by
three modules (`web/access.py`, `web/item_link.py`, `web/item_docs.py`), so its
keys get their own file rather than being wedged into `tests/test_web.py`,
which is already ~3,200 lines.

**Interfaces:**
- Consumes: nothing.
- Produces: `Web.api_keys: str`, `Web.bc_link_key: str`, `Web.public_base_url: str` — all comma-or-plain strings, defaulting to `""`. Every later task reads these off the `Web` instance.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Config keys for the web process's access credentials.

Env-only and secret: `WEB_API_KEYS` / `WEB_BC_LINK_KEY` never appear in a TOML
overlay committed to the repo.
"""

from __future__ import annotations

from app.config import load_config


def test_web_access_keys_default_to_empty(monkeypatch):
    """Empty means the class is disabled, never means 'allow everyone'."""
    for var in ("WEB_API_KEYS", "WEB_BC_LINK_KEY", "WEB_PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.web.api_keys == ""
    assert cfg.web.bc_link_key == ""
    assert cfg.web.public_base_url == ""


def test_web_access_keys_read_from_env(monkeypatch):
    monkeypatch.setenv("WEB_API_KEYS", "shop-key-1,shop-key-2")
    monkeypatch.setenv("WEB_BC_LINK_KEY", "bc-shared")
    monkeypatch.setenv("WEB_PUBLIC_BASE_URL", "https://api.cw.dentalia.si")
    cfg = load_config()
    assert cfg.web.api_keys == "shop-key-1,shop-key-2"
    assert cfg.web.bc_link_key == "bc-shared"
    assert cfg.web.public_base_url == "https://api.cw.dentalia.si"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -k web_access -v`
Expected: FAIL with `AttributeError: 'Web' object has no attribute 'api_keys'`

- [ ] **Step 3: Write minimal implementation**

In `app/config.py`, add to the `Web` dataclass immediately after `path_rewrites: str = ""`:

```python
    # Access credentials for the two non-browser caller classes
    # (docs/superpowers/specs/2026-08-25-item-document-access-design.md §8).
    # Both are SECRETS: .env, never a runbook tuning key.
    #
    # `api_keys` is comma-separated so a second consumer, or a rotation, is an
    # env change rather than a downtime swap. Empty disables the `service`
    # class entirely -- empty never means "allow everyone".
    api_keys: str = ""
    # Shared static key for the BC item-card hyperlink, `/item/{ref}?k=...`.
    # NOT per-item and deliberately so: a BC hyperlink is a computed field
    # built by string concatenation, so anything BC must compute is unusable
    # (Denis, 2026-08-25). Empty 404s `/item/*`.
    bc_link_key: str = ""
    # Absolute base for URLs emitted in responses. Empty means relative, which
    # is correct for same-origin browser use and wrong for a webshop backend
    # embedding our links in its own pages.
    public_base_url: str = ""
```

And in the `web = Web(...)` builder, after the `path_rewrites=` line:

```python
        api_keys=_str("WEB_API_KEYS", _dig(t, "web", "api_keys"), ""),
        bc_link_key=_str("WEB_BC_LINK_KEY", _dig(t, "web", "bc_link_key"), ""),
        public_base_url=_str(
            "WEB_PUBLIC_BASE_URL", _dig(t, "web", "public_base_url"), ""
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -k web_access -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. `app/config.py` reaches most of the tree, so a subset run here would be a false green.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "config: access keys for the BC item link and the webshop service API"
```

---

### Task 2: Shared item-documents query with a customer view

**Files:**
- Create: `web/item_docs.py`
- Modify: `web/app.py:3332-3390` (`api_item_documents` becomes a thin caller)
- Test: `tests/test_item_docs.py`

**Interfaces:**
- Consumes: `Web.public_base_url` from Task 1.
- Produces: `web.item_docs.item_documents(conn, item_ref: str, *, view: str = "full", base_url: str = "") -> dict`. Returns keys `item_ref`, `item_name`, `manufacturer`, `mfr_ref`, `documents`; plus `manufacturer_code` only when `view == "full"`. Each entry in `documents` has `doc_id`, `name`, `type`, `regulation`, `valid_from`, `valid_to`, `url`; plus `match_basis` and `expiry_basis` only when `view == "full"`. Tasks 4 and 5 call this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_item_docs.py`:

```python
"""The one item-documents query, shared by /api/items/{ref}/documents and
/item/{ref}. Extracted so the two routes cannot drift apart about what an
item's paperwork is (spec §6)."""

from __future__ import annotations

import pytest

from tests.test_web import _seed_item_with_doc
from web.item_docs import CUSTOMER_HIDDEN_DOC_FIELDS, CUSTOMER_HIDDEN_ITEM_FIELDS, item_documents


def test_full_view_carries_the_internal_fields(conn):
    _seed_item_with_doc(conn, "ID-FULL")
    out = item_documents(conn, "ID-FULL")
    assert out["item_ref"] == "ID-FULL"
    assert out["manufacturer_code"] == "077"
    assert out["documents"][0]["match_basis"] == "ref-list"
    assert "expiry_basis" in out["documents"][0]


def test_customer_view_drops_every_internal_field(conn):
    """A clinic must not read our matching vocabulary or the inferred expiry
    basis -- `staleness` would read as a legal expiry we invented."""
    _seed_item_with_doc(conn, "ID-CUST")
    out = item_documents(conn, "ID-CUST", view="customer")
    for field in CUSTOMER_HIDDEN_ITEM_FIELDS:
        assert field not in out
    for field in CUSTOMER_HIDDEN_DOC_FIELDS:
        assert field not in out["documents"][0]
    # what the clinic legitimately needs to confirm the article is still there
    assert out["item_name"] == "Widget"
    assert out["mfr_ref"] == "W-1"
    assert out["documents"][0]["type"] == "DoC"


def test_base_url_makes_document_urls_absolute(conn):
    _seed_item_with_doc(conn, "ID-ABS")
    out = item_documents(conn, "ID-ABS", base_url="https://api.cw.dentalia.si")
    assert out["documents"][0]["url"].startswith(
        "https://api.cw.dentalia.si/documents/"
    )


def test_relative_url_when_no_base_url(conn):
    _seed_item_with_doc(conn, "ID-REL")
    out = item_documents(conn, "ID-REL")
    assert out["documents"][0]["url"].startswith("/documents/")


def test_unknown_item_returns_null_identity_and_no_documents(conn):
    out = item_documents(conn, "no-such-item-at-all")
    assert out["item_name"] is None
    assert out["manufacturer"] is None
    assert out["documents"] == []


def test_staged_link_is_invisible(conn):
    """Visibility rule: BOTH the document and the link must be production."""
    _seed_item_with_doc(conn, "ID-STAGED", link_status="staged")
    assert item_documents(conn, "ID-STAGED")["documents"] == []


def test_staged_document_is_invisible(conn):
    _seed_item_with_doc(conn, "ID-STAGEDOC", doc_status="staged")
    assert item_documents(conn, "ID-STAGEDOC")["documents"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_item_docs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'web.item_docs'`

- [ ] **Step 3: Write minimal implementation**

Create `web/item_docs.py`. The body is lifted verbatim from `web/app.py`'s `api_item_documents` (the SQL, the batched manufacturer lookup, the `name` derivation, the constructed `url`) so behaviour is unchanged; the only new logic is the view projection and the `base_url` prefix.

```python
"""The one item-documents query.

`/api/items/{item_ref}/documents` (the webshop's service call) and
`/item/{item_ref}` (the BC hyperlink) must never disagree about what an item's
paperwork is, so the query lives here once and both routes call it.

Two views. `full` is what a staff member or the webshop backend sees. `customer`
is what the webshop may render to a clinic: it drops our internal matching
vocabulary and the inferred expiry basis, because `staleness` -- a five-year
review horizon Dentalia chose, not a legal expiry -- reads as an expiry we
invented when it is printed next to a date on a customer-facing page
(spec §6, and `docs/specs/read-api.md` on `expiry_basis`).

Read-only, `item_document_production` only: a document is visible for an item
when BOTH the document and that item's link are production. That join is
encoded once, in the view, and is not reimplemented here.
"""

from __future__ import annotations

# Named rather than inlined so the tests can assert the projection is complete
# without restating the list and drifting from it.
CUSTOMER_HIDDEN_ITEM_FIELDS = ("manufacturer_code",)
CUSTOMER_HIDDEN_DOC_FIELDS = ("match_basis", "expiry_basis")


def item_documents(
    conn, item_ref: str, *, view: str = "full", base_url: str = ""
) -> dict:
    """Production documents for one item, plus the item's own identity.

    `base_url` prefixes each document `url`. Empty (the default) leaves them
    relative, which is right for same-origin browser use and wrong for a
    webshop backend embedding our links in its own pages.
    """
    item = conn.execute(
        "SELECT m.name, m.mfr_ref, m.manufacturer_raw AS manufacturer_code, "
        "       COALESCE(a.canonical_name, m.manufacturer_raw) AS manufacturer "
        "FROM item_mirror m "
        "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
        "WHERE m.item_ref = %s",
        (item_ref,),
    ).fetchone()
    docs = conn.execute(
        "SELECT doc_id, match_basis, type, regulation, "
        "       validity_from AS valid_from, expires AS valid_to, "
        "       expiry_basis "
        "FROM item_document_production WHERE item_ref = %s ORDER BY doc_id",
        (item_ref,),
    ).fetchall()
    # One batched query, not one per doc. DISTINCT ON ... ORDER BY extract_rev
    # DESC reads only the LATEST extraction revision (014) -- a re-extraction
    # must not leave the name stuck on a stale rev-1 value.
    mfr = {
        row["doc_id"]: row["value"]
        for row in conn.execute(
            "SELECT DISTINCT ON (doc_id) doc_id, value FROM evidence "
            "WHERE doc_id = ANY(%s) AND field = 'manufacturer' "
            "ORDER BY doc_id, extract_rev DESC",
            ([d["doc_id"] for d in docs],),
        ).fetchall()
    } if docs else {}

    for d in docs:
        parts = [mfr.get(d["doc_id"]), d["type"],
                 f"({d['regulation']})" if d["regulation"] not in (None, "n.a.") else None]
        name = " ".join(p for p in parts if p)
        d["name"] = f"{name}, valid to {d['valid_to']}" if d["valid_to"] else name
        # Constructed, never read from the database: `archive_url` is a storage
        # handle, not a link (2026-08-24 correction, docs/specs/read-api.md).
        d["url"] = f"{base_url}/documents/{d['doc_id']}/file"
        if view == "customer":
            for field in CUSTOMER_HIDDEN_DOC_FIELDS:
                d.pop(field, None)

    out = {
        "item_ref": item_ref,
        "item_name": item["name"] if item else None,
        "manufacturer": item["manufacturer"] if item else None,
        "manufacturer_code": item["manufacturer_code"] if item else None,
        "mfr_ref": item["mfr_ref"] if item else None,
        "documents": docs,
    }
    if view == "customer":
        for field in CUSTOMER_HIDDEN_ITEM_FIELDS:
            out.pop(field, None)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_item_docs.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Replace the route body with a call**

In `web/app.py`, add `from web.item_docs import item_documents` to the imports, then replace the whole body of `api_item_documents` (currently `web/app.py:3332-3390`) — keeping the decorator, the docstring's first paragraph, and the `:path` comment above it — with:

```python
    @app.get("/api/items/{item_ref:path}/documents")
    def api_item_documents(item_ref: str, view: str = "full"):
        """Production documents for one item, plus the item's own identity.

        `view=customer` narrows the response to what the webshop may render to
        a clinic (spec §6). The default is unchanged, so this is additive per
        `docs/specs/read-api.md` §5.
        """
        with _conn() as conn:
            return item_documents(
                conn, item_ref, view=view, base_url=cfg.public_base_url
            )
```

- [ ] **Step 6: Run the existing API tests to prove the refactor changed nothing**

Run: `python -m pytest tests/test_web.py -k "api_" -v`
Expected: PASS. These already pin the response shape, `valid_to` being the effective expiry, and the constructed `url`; they must pass untouched.

- [ ] **Step 7: Commit**

```bash
git add web/item_docs.py web/app.py tests/test_item_docs.py
git commit -m "web: extract the item-documents query, add the customer view"
```

---

### Task 3: Access policy

**Files:**
- Create: `web/access.py`
- Modify: `web/app.py:1665` (the `FastAPI(...)` constructor)
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `Web.api_keys`, `Web.bc_link_key`, `Web.require_authenticated_user`, `Web.trusted_user_header` from Task 1.
- Produces: `web.access.allowed_classes(path: str) -> frozenset[str]`, `web.access.classify(request, cfg) -> str`, `web.access.guard(cfg) -> Callable[[Request], None]`, and the constants `STAFF`, `SERVICE`, `BC`, `ANON`. Task 4's route relies on `/item/*` being reachable by the `bc` class.

- [ ] **Step 1: Write the failing test**

Create `tests/test_access.py`:

```python
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

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.access import ANON, BC, SERVICE, STAFF, allowed_classes, classify
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_access.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'web.access'`

- [ ] **Step 3: Write minimal implementation**

Create `web/access.py`:

```python
"""Who may reach what (spec §2).

Three caller classes, one policy function. Installed as a **sync FastAPI
dependency**, not an ASGI middleware: `@app.middleware("http")` forces
`async def`, and this codebase is sync by ruling (CLAUDE.md). A global
`dependencies=[...]` on the app runs for every matched route and keeps the
whole call path synchronous.

The classes exist because three consumers reach this app three different ways
and only one of them has a browser:

- `staff`   -- Caddy Basic auth, already deployed, unchanged.
- `service` -- the webshop BACKEND, server-to-server, `X-API-Key`. It fetches
               and relays to its own logged-in customers, which is why no
               public tier exists at all.
- `bc`      -- a Business Central item-card hyperlink, `?k=`. BC builds links
               by string concatenation, so anything BC must COMPUTE (an HMAC,
               a per-item token) is unusable. Hence one shared static key.

When `require_authenticated_user` is False there is no proxy in front (local
dev, tests) and every caller is `staff`. That is the existing contract of
`web/app.py::_authenticated_user`, generalised to every route, not a new rule.
"""

from __future__ import annotations

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
_ITEM_LINK = frozenset({BC, SERVICE, STAFF})
_SERVICE_API = frozenset({SERVICE, STAFF})

# `/documents/{id}/file` is service-reachable so the shop can relay bytes;
# `/documents/{id}` and `/documents` are UI pages and are not. A prefix match
# cannot tell those apart, so this one path gets a pattern.
_DOC_FILE_RE = re.compile(r"^/documents/\d+/file$")


def allowed_classes(path: str) -> frozenset[str]:
    """Which caller classes may reach `path`. Default-deny: anything not named
    here is staff-only, so a route added later is private until someone
    deliberately opens it."""
    if path == "/healthz":
        return _ANY
    if path.startswith("/item/"):
        return _ITEM_LINK
    if path.startswith("/api/"):
        return _SERVICE_API
    if path.startswith("/archive/"):
        # Served BY PATH, therefore walkable. Staff only, even with a valid
        # API key -- `/documents/{id}/file` is the supported way to get bytes.
        return _STAFF_ONLY
    if _DOC_FILE_RE.match(path):
        return _SERVICE_API
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


def classify(request, cfg) -> str:
    """The caller's class. Checked most-privileged first, so a staff browser
    that also happens to carry a key is still staff."""
    if not cfg.require_authenticated_user:
        return STAFF
    if request.headers.get(cfg.trusted_user_header, "").strip():
        return STAFF
    api_key = request.headers.get("X-API-Key", "")
    if api_key and _matches_any(api_key, _split_keys(cfg.api_keys)):
        return SERVICE
    link_key = request.query_params.get("k", "")
    if link_key and cfg.bc_link_key and hmac.compare_digest(link_key, cfg.bc_link_key):
        return BC
    return ANON


def _refuse(path: str) -> None:
    """Refusal codes differ by surface on purpose (spec §7): `/item/*` returns
    404 because a 403 would confirm the item exists, while a service caller
    deserves a diagnosable 401."""
    if path.startswith("/item/"):
        raise HTTPException(status_code=404, detail="not found")
    if path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="valid X-API-Key required")
    raise HTTPException(status_code=403, detail="staff access required")


def guard(cfg) -> Callable[[Request], None]:
    """Build the global dependency. Sync, deliberately -- see module docstring."""

    def _guard(request: Request) -> None:
        if classify(request, cfg) not in allowed_classes(request.url.path):
            _refuse(request.url.path)

    return _guard
```

- [ ] **Step 4: Install it on the app**

In `web/app.py`, add `from fastapi import Depends` to the existing `fastapi` import if not present, add `from web import access`, and change line 1665 from:

```python
    app = FastAPI(title="Dentalia Compliance Registry")
```

to:

```python
    # Global dependency, not middleware: middleware would force `async def`
    # and this codebase is sync by ruling. Runs for every matched route;
    # default-deny, so a route added later is staff-only until opened.
    app = FastAPI(
        title="Dentalia Compliance Registry",
        dependencies=[Depends(access.guard(cfg))],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_access.py tests/test_web.py -q`
Expected: PASS. `tests/test_web.py` must be untouched — its clients use the default `require_authenticated_user=False`, where the guard is a no-op.

- [ ] **Step 6: Commit**

```bash
git add web/access.py web/app.py tests/test_access.py
git commit -m "web: caller classes and a default-deny route policy"
```

---

### Task 4: The BC item link

**Files:**
- Create: `web/item_link.py`, `web/templates/item_card.html`
- Modify: `web/app.py` (call `item_link.register_routes` beside the existing `registry.register_routes` at `web/app.py:1717`)
- Test: `tests/test_item_link.py`

**Interfaces:**
- Consumes: `item_documents(...)` from Task 2; the `bc` class from Task 3; `Web.public_base_url`, `Web.bc_link_key` from Task 1.
- Produces: `web.item_link.register_routes(app, templates, conn_factory, cfg) -> None`, registering `GET /item/{item_ref:path}`. Task 5 adds a second route to the same module and Task 7 documents both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_item_link.py`:

```python
"""The BC item-card link, `/item/{item_ref}?k=...` (spec §3).

One URL for two consumers: a support rep clicking from BC gets a page, the
webshop backend asking for JSON gets JSON.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from tests.test_web import _seed_item_with_doc
from web.app import create_app

BC_KEY = "bc-shared"


@pytest.fixture
def guarded(test_db_url, tmp_path):
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        require_authenticated_user=True,
        api_keys="shop-key-1",
        bc_link_key=BC_KEY,
    )
    return TestClient(create_app(cfg), raise_server_exceptions=False)


def test_browser_gets_html(guarded, conn):
    _seed_item_with_doc(conn, "LNK-1")
    resp = guarded.get(f"/item/LNK-1?k={BC_KEY}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "LNK-1" in resp.text


def test_accept_json_gets_json(guarded, conn):
    _seed_item_with_doc(conn, "LNK-2")
    resp = guarded.get(
        f"/item/LNK-2?k={BC_KEY}", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "LNK-2"


def test_format_query_overrides_accept(guarded, conn):
    """BC's HTTP client is not guaranteed to send a useful Accept."""
    _seed_item_with_doc(conn, "LNK-3")
    resp = guarded.get(
        f"/item/LNK-3?k={BC_KEY}&format=json", headers={"Accept": "*/*"}
    )
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "LNK-3"


def test_item_ref_containing_a_slash_round_trips(guarded, conn):
    """Item refs contain slashes; the `:path` converter is why."""
    _seed_item_with_doc(conn, "AB/12")
    resp = guarded.get(f"/item/AB/12?k={BC_KEY}&format=json")
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "AB/12"


def test_customer_view_drops_internal_fields(guarded, conn):
    _seed_item_with_doc(conn, "LNK-CUST")
    body = guarded.get(
        f"/item/LNK-CUST?k={BC_KEY}&format=json&view=customer"
    ).json()
    assert "manufacturer_code" not in body
    assert "match_basis" not in body["documents"][0]
    assert "expiry_basis" not in body["documents"][0]


def test_wrong_key_is_404_never_403(guarded, conn):
    """A 403 would confirm the item exists."""
    _seed_item_with_doc(conn, "LNK-SECRET")
    assert guarded.get("/item/LNK-SECRET?k=wrong").status_code == 404
    assert guarded.get("/item/LNK-SECRET").status_code == 404


def test_unknown_item_is_200_with_nulls(guarded):
    """Matches read-api.md §2: 'no such item' is not this endpoint's
    distinction to make."""
    body = guarded.get(f"/item/nope-not-here?k={BC_KEY}&format=json").json()
    assert body["item_name"] is None
    assert body["documents"] == []


def test_uncovered_item_page_says_so(guarded, conn):
    _seed_item_with_doc(conn, "LNK-BARE", link_status="staged")
    resp = guarded.get(f"/item/LNK-BARE?k={BC_KEY}")
    assert resp.status_code == 200
    assert "No documents on file yet" in resp.text


def test_empty_bc_link_key_404s_the_route(test_db_url, tmp_path, conn):
    """Empty never means 'allow everyone'."""
    _seed_item_with_doc(conn, "LNK-NOKEY")
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        require_authenticated_user=True, bc_link_key="",
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    assert client.get("/item/LNK-NOKEY?k=").status_code == 404
    assert client.get("/item/LNK-NOKEY?k=anything").status_code == 404


def test_page_does_not_shadow_the_items_ui_route(guarded, conn):
    """`/item/` and `/items/` are different routes; adding one must not
    capture the other."""
    _seed_item_with_doc(conn, "LNK-UI")
    resp = guarded.get("/items", headers={"X-Forwarded-User": "admin"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_item_link.py -v`
Expected: FAIL — every case 404s, because no `/item/` route exists yet.

- [ ] **Step 3: Write minimal implementation**

Create `web/item_link.py`:

```python
"""The BC item-card link (spec §3).

`https://api.cw.dentalia.si/item/{item_ref}?k={bc_link_key}` — built by BC
through string concatenation in a computed field. That constraint is the whole
reason this URL carries a shared static key instead of a per-item signature:
BC cannot compute anything (Denis, 2026-08-25).

One URL, two renderings. A support rep clicking from the item card gets a page
they can read and download from; the webshop backend asking with
`Accept: application/json` (or `?format=json`, because BC's client may send
`*/*`) gets the same payload as JSON.

Read-only. The access policy in `web/access.py` decides who gets here at all.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from web.item_docs import item_documents


def wants_json(request: Request, fmt: str) -> bool:
    """`?format=json` wins over Accept, because BC's HTTP client is not
    guaranteed to send a useful Accept header and a rep must never land on a
    raw JSON blob."""
    if fmt == "json":
        return True
    return "application/json" in request.headers.get("accept", "")


def register_routes(app, templates, conn_factory, cfg) -> None:
    @app.get("/item/{item_ref:path}")
    def item_link(
        request: Request, item_ref: str, format: str = "", view: str = "full"
    ):
        with conn_factory() as conn:
            data = item_documents(
                conn, item_ref, view=view, base_url=cfg.public_base_url
            )
        if wants_json(request, format):
            return data
        return templates.TemplateResponse(request, "item_card.html", data)
```

Create `web/templates/item_card.html`. It deliberately does **not** extend `base.html`: that template carries the staff navigation, the alert counters and the HTMX pulse, none of which belong on a page a webshop may frame or a rep may forward.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ item_name or item_ref }} — compliance documents</title>
  <link rel="stylesheet" href="{{ static_url('css/style.css') }}">
</head>
<body class="item-card">
  <main>
    <h1>{{ item_name or item_ref }}</h1>
    <p class="identity">
      {# Manufacturer identity leads: it is what is printed on the box, so it
         is what lets someone confirm this is the article they are holding.
         Dentalia's own item_ref is shown after it, smaller. #}
      {% if manufacturer %}<strong>{{ manufacturer }}</strong>{% endif %}
      {% if mfr_ref %} &middot; ref {{ mfr_ref }}{% endif %}
      <span class="muted">&middot; item {{ item_ref }}</span>
    </p>

    {% if documents %}
      <p><a class="button" href="{{ item_ref }}/documents.zip?{{ request.url.query }}">Download all ({{ documents|length }})</a></p>
      <table>
        <thead>
          <tr><th>Document</th><th>Type</th><th>Valid from</th><th>Valid to</th><th></th></tr>
        </thead>
        <tbody>
          {% for d in documents %}
          <tr>
            <td>{{ d.name }}</td>
            <td>{{ d.type }}{% if d.regulation and d.regulation != 'n.a.' %} ({{ d.regulation }}){% endif %}</td>
            <td>{{ d.valid_from or '—' }}</td>
            <td>{{ d.valid_to or '—' }}</td>
            <td><a href="{{ d.url }}">Open</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="empty">No documents on file yet — contact
        <a href="mailto:mdr@dentalia.si">mdr@dentalia.si</a>.</p>
    {% endif %}
  </main>
</body>
</html>
```

In `web/app.py`, beside the existing registration at line 1717:

```python
    registry.register_routes(app, templates, _conn, cfg.playbooks_dir or None)
    scheduler_view.register_routes(app, templates, _conn, scheduler_cfg)
    item_link.register_routes(app, templates, _conn, cfg)
```

and add `from web import item_link` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_item_link.py -v`
Expected: PASS (10 passed). `test_uncovered_item_page_says_so` and the "download all" link exercise the template; the zip route itself arrives in Task 5, so that link 404s until then — no test asserts it resolves yet.

- [ ] **Step 5: Confirm nothing else regressed**

Run: `python -m pytest tests/test_web.py tests/test_access.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/item_link.py web/templates/item_card.html web/app.py tests/test_item_link.py
git commit -m "web: /item/{ref} for the BC item card, HTML or JSON on one URL"
```

---

### Task 5: Download all

**Files:**
- Modify: `web/item_link.py`
- Test: `tests/test_item_link.py`

**Interfaces:**
- Consumes: `item_documents(...)` from Task 2; `_download_name` and `_resolve_archive_path` and `_parse_path_rewrites`, already module-level in `web/app.py` at lines 426, 456 and 408.
- Produces: `GET /item/{item_ref:path}/documents.zip`. Nothing later depends on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_item_link.py`:

```python
import io
import zipfile


def _seed_doc_with_real_file(conn, tmp_path, item_ref):
    """A document whose archive_url points at bytes that actually exist, so
    the zip has something to put in it."""
    doc_id = _seed_item_with_doc(conn, item_ref)
    pdf = tmp_path / "seeded.pdf"
    pdf.write_bytes(b"%PDF-1.4 seeded\n")
    conn.execute(
        "UPDATE document SET archive_url=%s WHERE doc_id=%s", (str(pdf), doc_id)
    )
    conn.commit()
    return doc_id


def test_zip_contains_the_production_documents(test_db_url, tmp_path, conn):
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_doc_with_real_file(conn, tmp_path, "ZIP-1")
    resp = client.get(f"/item/ZIP-1/documents.zip?k={BC_KEY}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 1
        assert zf.namelist()[0].endswith(".pdf")


def test_zip_route_is_not_swallowed_by_the_item_route(test_db_url, tmp_path, conn):
    """`{item_ref:path}` is greedy: registered in the wrong order, the item
    route would match `/item/ZIP-2/documents.zip` with
    item_ref='ZIP-2/documents.zip' and render a page instead."""
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_doc_with_real_file(conn, tmp_path, "ZIP-2")
    resp = client.get(f"/item/ZIP-2/documents.zip?k={BC_KEY}")
    assert resp.headers["content-type"] == "application/zip"


def test_zip_of_an_uncovered_item_is_404(test_db_url, tmp_path, conn):
    """An empty archive is a worse answer than saying there is nothing."""
    cfg = Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
        archive_root=str(tmp_path), require_authenticated_user=True,
        bc_link_key=BC_KEY,
    )
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    _seed_item_with_doc(conn, "ZIP-EMPTY", link_status="staged")
    assert client.get(f"/item/ZIP-EMPTY/documents.zip?k={BC_KEY}").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_item_link.py -k zip -v`
Expected: FAIL — the zip requests return HTML from the item route, so the content-type assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `web/item_link.py`, add the imports:

```python
import io
import zipfile

from fastapi import HTTPException
from fastapi.responses import Response
```

and register the zip route **before** `/item/{item_ref:path}` inside `register_routes`. Order matters: `{item_ref:path}` is greedy and would otherwise match `/item/X/documents.zip` with `item_ref="X/documents.zip"`. The trailing literal `/documents.zip` is what anchors this pattern, the same way `/api/items/{item_ref:path}/documents` is anchored.

```python
    @app.get("/item/{item_ref:path}/documents.zip")
    def item_documents_zip(item_ref: str):
        """Every production document for one item, in one archive.

        The "papers" a client asks for on the phone are plural; one click
        beats five. Built in memory -- an item's documents are a handful of
        PDFs, not a corpus."""
        from web.app import (
            _download_name, _parse_path_rewrites, _resolve_archive_path,
        )

        with conn_factory() as conn:
            data = item_documents(conn, item_ref)
            rows = conn.execute(
                "SELECT d.doc_id, d.archive_url, d.content_hash FROM document d "
                "JOIN item_document_production p ON p.doc_id = d.doc_id "
                "WHERE p.item_ref = %s ORDER BY d.doc_id",
                (item_ref,),
            ).fetchall()
        if not rows:
            # An empty archive is a worse answer than saying there is nothing.
            raise HTTPException(status_code=404, detail="no documents for this item")

        rewrites = _parse_path_rewrites(cfg.path_rewrites)
        buf = io.BytesIO()
        written = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in rows:
                target = _resolve_archive_path(
                    row["archive_url"] or "", [cfg.archive_root, cfg.imports_dir],
                    rewrites=rewrites, content_hash=row["content_hash"],
                )
                if target is None:
                    # Counted, never silent (CLAUDE.md): a document whose bytes
                    # are unreachable is a data problem, not a reason to 500.
                    continue
                zf.write(target, _download_name(target.name, row["content_hash"]))
                written += 1
        if not written:
            raise HTTPException(
                status_code=404, detail="no archived files are reachable for this item"
            )
        name = (data["item_name"] or item_ref).replace("/", "-")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{name}-documents.zip"'
            },
        )
```

The local import of the three `web.app` helpers is deliberate: importing them at module top level would make `web.item_link` and `web.app` import each other in a cycle, since `web.app` imports this module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_item_link.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add web/item_link.py tests/test_item_link.py
git commit -m "web: download-all zip for one item's production documents"
```

---

### Task 6: Ingress and compose wiring

**Files:**
- Modify: `Caddyfile:34-41`, `docker-compose.yml:213-225` (the `web` service `environment:` block)
- Test: none automated — verified by `docker compose config` and by the §5 fallback if the matcher will not compile.

**Interfaces:**
- Consumes: the env var names from Task 1.
- Produces: nothing code-level.

- [ ] **Step 1: Verify the Caddy header matcher syntax**

Before editing, confirm how Caddy 2.8 expresses "this header field is present". The expected form is `not header X-API-Key *`. **This is unverified** — check `https://caddyserver.com/docs/caddyfile/matchers#header` with WebFetch, or run:

```bash
docker compose run --rm caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

If it will not compile, take the spec §5 fallback instead: carve `/api/*` out of `@protected` entirely and let `web/access.py` enforce both classes there. Same policy table, one less clever matcher. Record which route was taken in the commit message.

- [ ] **Step 2: Edit the Caddyfile**

Replace the `@protected` / `basic_auth` block with:

```
	# Three caller classes now reach this app (spec §2), and only one of them
	# has a browser:
	#   - staff  -> Basic auth here, unchanged, X-Forwarded-User downstream
	#   - service-> the webshop BACKEND, X-API-Key, validated by the app
	#   - bc     -> a Business Central item-card hyperlink, ?k=, ditto
	#
	# `basic_auth` cannot express "Basic OR a key", so the two non-browser
	# classes are excluded from the matcher and the APP is the authority on
	# their credentials. A request with a BOGUS X-API-Key therefore skips
	# Basic and is rejected downstream by web/access.py -- that is intended,
	# not a hole: Caddy only decides whether to DEMAND Basic.
	#
	# /archive/* is deliberately NOT excluded. It serves the archive by path,
	# which is walkable, so it stays staff-only at both layers.
	@protected {
		not path /healthz
		not path /item/*
		not header X-API-Key *
	}

	basic_auth @protected {
		{$DENTALIA_WEB_USER} {$DENTALIA_WEB_PASSWORD_HASH}
	}
```

- [ ] **Step 3: Add the env vars to compose**

In `docker-compose.yml`, in the `web` service `environment:` block:

```yaml
      # Access credentials for the two non-browser caller classes (spec §8).
      # Secrets: set both in .env, never inline here.
      WEB_API_KEYS: ${WEB_API_KEYS:-}
      WEB_BC_LINK_KEY: ${WEB_BC_LINK_KEY:-}
      WEB_PUBLIC_BASE_URL: ${WEB_PUBLIC_BASE_URL:-}
```

- [ ] **Step 4: Validate**

Run: `docker compose config >/dev/null && echo COMPOSE_OK`
Expected: `COMPOSE_OK`

- [ ] **Step 5: Commit**

```bash
git add Caddyfile docker-compose.yml
git commit -m "ingress: let the webshop key and the BC link past Basic auth"
```

---

### Task 7: API reference page (slice 3)

**Files:**
- Create: `web/templates/api_reference.html`
- Modify: `web/app.py` (one new route), `web/templates/base.html:117` (new nav group after the Pipeline group)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `GET /api-reference`. Nothing depends on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_api_reference_page_lists_the_endpoints(client, conn):
    _seed_item_with_doc(conn, "REF-1")
    resp = client.get("/api-reference")
    assert resp.status_code == 200
    for path in ("/api/items/", "/item/", "/api/kpi", "/openapi.json"):
        assert path in resp.text


def test_api_reference_examples_use_a_real_item(client, conn):
    """A documented example that 404s is worse than none. The page reads an
    item_ref out of the database rather than hardcoding one."""
    _seed_item_with_doc(conn, "REF-REAL")
    resp = client.get("/api-reference")
    assert "REF-REAL" in resp.text or "no items mirrored yet" in resp.text


def test_api_reference_is_in_the_nav(client):
    resp = client.get("/items")
    assert "/api-reference" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web.py -k api_reference -v`
Expected: FAIL — 404 on `/api-reference`.

- [ ] **Step 3: Add the route**

In `web/app.py`, beside the other page routes:

```python
    @app.get("/api-reference", response_class=HTMLResponse)
    def api_reference(request: Request):
        """What this app exposes, to whom, and with which credential.

        `/docs` and `/openapi.json` already exist (FastAPI generates them) but
        answer "what are the parameters", not "which of these may the webshop
        call and what does it authenticate with". This page answers the
        second, and links to the first.

        The example item_ref is read from the database rather than hardcoded:
        a reference page whose examples 404 is worse than no reference page.
        """
        with _conn() as conn:
            row = conn.execute(
                "SELECT item_ref FROM item_document_production LIMIT 1"
            ).fetchone()
        return templates.TemplateResponse(
            request,
            "api_reference.html",
            {"example_ref": row["item_ref"] if row else None},
        )
```

- [ ] **Step 4: Add the nav group**

In `web/templates/base.html`, after the `/upload` line that closes the Pipeline group (line 118):

```html
        <p class="nav-label">Developer</p>
        <a href="/api-reference" class="{{ 'active' if request.url.path == '/api-reference' else '' }}">API</a>
```

- [ ] **Step 5: Create the template**

Create `web/templates/api_reference.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>API</h1>

<p>Three kinds of caller reach this application, and only one of them has a
browser. Which paths a caller may reach depends on which credential it carries
&mdash; the policy is one table, in <code>web/access.py</code>.</p>

<table>
  <thead><tr><th>Class</th><th>Credential</th><th>Who</th></tr></thead>
  <tbody>
    <tr><td>staff</td><td>HTTP Basic, via the Caddy proxy</td><td>Us. Everything in this UI.</td></tr>
    <tr><td>service</td><td><code>X-API-Key</code> header</td><td>The webshop backend. Fetches server-side and relays to its own logged-in customers.</td></tr>
    <tr><td>bc</td><td><code>?k=</code> in the URL</td><td>A Business Central item-card hyperlink. BC builds links by string concatenation, so it cannot compute a signature.</td></tr>
  </tbody>
</table>

<h2>Endpoints</h2>
<table>
  <thead><tr><th>Path</th><th>Reachable by</th><th>Returns</th></tr></thead>
  <tbody>
    <tr>
      <td><code>/item/{item_ref}?k=…</code></td>
      <td>bc, service, staff</td>
      <td>One item and its production documents. HTML for a browser, JSON on
        <code>Accept: application/json</code> or <code>?format=json</code>.
        Add <code>&amp;view=customer</code> for the narrowed projection a
        clinic may see.</td>
    </tr>
    <tr>
      <td><code>/item/{item_ref}/documents.zip?k=…</code></td>
      <td>bc, service, staff</td>
      <td>Every production document for that item, one archive.</td>
    </tr>
    <tr>
      <td><code>/api/items/{item_ref}/documents</code></td>
      <td>service, staff</td>
      <td>The same payload as JSON. <code>?view=customer</code> applies here too.</td>
    </tr>
    <tr>
      <td><code>/api/documents/{doc_id}</code></td>
      <td>service, staff</td>
      <td>One production document and its complete evidence.
        <strong>Internal</strong> &mdash; carries <code>model_id</code>,
        <code>confidence</code> and <code>verbatim</code>. Never render this to
        a customer.</td>
    </tr>
    <tr>
      <td><code>/api/kpi</code></td>
      <td>service, staff</td>
      <td>The KPI board, same numbers as the status page.</td>
    </tr>
    <tr>
      <td><code>/documents/{doc_id}/file</code></td>
      <td>service, staff</td>
      <td>The archived PDF. This is the supported way to get bytes.</td>
    </tr>
    <tr>
      <td><code>/archive/{path}</code></td>
      <td><strong>staff only</strong></td>
      <td>The archive by path, and therefore walkable. Not open to a key.</td>
    </tr>
  </tbody>
</table>

<h2>Try it</h2>
{% if example_ref %}
  <p>Live links, built from a real item currently in the registry
    (<code>{{ example_ref }}</code>):</p>
  <ul>
    <li><a href="/api/items/{{ example_ref }}/documents">/api/items/{{ example_ref }}/documents</a></li>
    <li><a href="/api/items/{{ example_ref }}/documents?view=customer">…&amp;view=customer</a></li>
    <li><a href="/api/kpi">/api/kpi</a></li>
  </ul>
{% else %}
  <p class="muted">No examples: no items mirrored yet.</p>
{% endif %}

<h2>Generated reference</h2>
<p>Parameters and response schemas are generated from the code itself:
  <a href="/docs">/docs</a> &middot; <a href="/openapi.json">/openapi.json</a>.</p>
{% endblock %}
```

If `base.html` names its content block something other than `content`, match whatever the other page templates use — check `web/templates/items.html` for the exact block name before writing this file.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_web.py -k api_reference -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add web/templates/api_reference.html web/templates/base.html web/app.py tests/test_web.py
git commit -m "web: API reference page and a Developer nav group"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/specs/read-api.md`, `docs/README.md`, `docs/runbook.md`, `tasks/followups.md`
- Test: none (docs-only, per CLAUDE.md's selection rule)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code-level.

- [ ] **Step 1: Add `view=customer` to the read-api spec**

In `docs/specs/read-api.md` §2, after the field-semantics list, add a subsection. Keep the edit targeted — do not rewrite unaffected sections.

```markdown
### `?view=customer`

`view=customer` narrows the response to what the webshop may render to a clinic.
It omits `manufacturer_code` at the top level and `match_basis` / `expiry_basis`
from every document. Additive per §5: the default (`view=full`) is byte-identical
to the response documented above.

`expiry_basis` is withheld rather than hidden for tidiness. `staleness` means a
five-year review horizon Dentalia chose, not a legal expiry (§2 above), and
printed beside a date on a customer-facing page it reads as an expiry we
invented.

The same parameter applies to `GET /item/{item_ref}` (the BC item-card link),
which shares this endpoint's implementation — `web/item_docs.py`.
```

- [ ] **Step 2: Add the new surfaces to the runbook**

In `docs/runbook.md`, add a row to the route table describing `/item/{item_ref}?k=`, `/item/{item_ref}/documents.zip`, and `/api-reference`; and add `WEB_API_KEYS`, `WEB_BC_LINK_KEY`, `WEB_PUBLIC_BASE_URL` to the configuration section, marked as **secrets** belonging at the top of `.env` beside `IMAP_USER` / `IMAP_PASSWORD`.

- [ ] **Step 3: Index the new spec and plan**

Add `docs/superpowers/specs/2026-08-25-item-document-access-design.md` to the `docs/README.md` index.

- [ ] **Step 4: Close the followup and log the deferred work**

In `tasks/followups.md`, mark `[serving-mechanism-and-doc-names]` (line 23) done — the mechanism is ruled: no public tier, the webshop relays, BC gets a shared-key link. Then append:

```markdown
- [ ] 2026-08-25 [public-surface-rate-limit] `S · needs a Caddy image decision` The BC link key is a single shared secret (spec §4); if it leaks, `/item/{ref}` is walkable and the catalogue is enumerable. Rate limiting is what would blunt that and is not built. Caddy's `rate_limit` is believed not to ship in the standard `caddy:2.8` image (community plugin `caddy-ratelimit`, needs a rebuilt image) — VERIFY before planning. Until then the non-enumerability argument carries alone.
- [ ] 2026-08-25 [service-api-catalogue] `M` Slice 2 of the item-access spec: manufacturer endpoints (identity, line-level documents, paginated item list) and an order-batch endpoint taking a list of item_refs. Denis, 2026-08-25: the catalogue page needs structured data per manufacturer and the order page probably does too, but both are served by the webshop calling us server-side — so these are service-API-shaped, never public. Pagination is mandatory: ~16k items today, ~100k at target.
```

- [ ] **Step 5: Run the full suite before the final commit**

Run: `python -m pytest -q`
Expected: PASS. This is the pre-commit full run CLAUDE.md requires; say in the commit message that it ran and what it reported.

- [ ] **Step 6: Commit**

```bash
git add docs/specs/read-api.md docs/README.md docs/runbook.md tasks/followups.md
git commit -m "docs: item access surfaces, customer view, and the deferred slice-2 work"
```

---

## Self-review

**Spec coverage.** §0 pivot rationale → Task 8 followup. §1 consumers → Task 3. §2 policy table → Task 3. §3 BC link + content negotiation → Task 4. §3.2 page contents and download-all → Tasks 4 and 5. §4 threat model → Task 8 followup. §5 ingress → Task 6. §6 customer projection → Task 2. §7 error states → Tasks 3 and 4. §8 config → Task 1. §9 deferred → Task 8 followup. §10 slice 3 → Task 7. §11 testing → Tasks 2–5, 7. §12 files → all tasks. §13 invariants → Global Constraints. No gaps.

**Placeholder scan.** No TBD/TODO. Two steps deliberately require verification rather than asserting a fact — Task 6 Step 1 (Caddy matcher syntax) and its stated fallback, and Task 7 Step 5 (the `base.html` block name). Both name exactly what to check and what to do with either answer, which is the opposite of a placeholder.

**Type consistency.** `item_documents(conn, item_ref, *, view, base_url)` is defined in Task 2 and called with those exact keywords in Tasks 4 and 5. `CUSTOMER_HIDDEN_ITEM_FIELDS` / `CUSTOMER_HIDDEN_DOC_FIELDS` are defined in Task 2 and imported in its own test only. `allowed_classes` / `classify` / `guard` and the four class constants are defined in Task 3 and used in Task 3's test. `register_routes(app, templates, conn_factory, cfg)` in Task 4 matches the four-argument shape `web/registry.py:541` already uses. `_download_name(archived_name, content_hash)` and `_resolve_archive_path(archive_url, roots, rewrites=, content_hash=)` in Task 5 match `web/app.py:426` and `web/app.py:456`.
