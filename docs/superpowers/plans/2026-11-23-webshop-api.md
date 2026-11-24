# Webshop API for Erpium Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Erpium's key reaches only the webstore routes, sees only documents a clinic may be shown, downloads through 5-minute signed links issued on click, under per-IP limits and a per-key alert.

**Architecture:** The global access dependency (`web/access.py`) records the caller class on `request.state` and enforces a path allowlist for `service`. A `for_shop` flag on the shared document projection (`web/item_docs.py`) applies the webstore visibility rules. Signed links are stateless HMAC tokens (`web/download_links.py`). Limits are in-memory counters in the single web process (`web/limits.py`).

**Tech Stack:** Python 3.12, FastAPI (sync), psycopg 3 raw SQL, Caddy 2.8, pytest against real Postgres via `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-23-webshop-api-design.md`

## Global Constraints

- Sync code only; no asyncio, no middleware (`web/access.py` docstring).
- No new job types, no new tables, no migrations. The web process stays a reader.
- Every test run through `./scripts/test.sh`, never bare `pytest`.
- `app/config.py` and `Caddyfile` are touched → **full suite** once before each commit that touches them.
- Commit messages without any Claude/AI reference and without attribution lines (memory: git-no-claude-attribution). No push, no deploy without Denis's yes.
- Staff `full` view output and the BC card (`/item/*`) output stay byte-for-byte unchanged.
- Seeded test documents use dates far from today (`2099-…` / `2001-…`). `_seed_item_with_doc` defaults to `validity_to="2026-12-01"`, which goes past in a week: never rely on that default in a shop-view test.
- Limits: API ceiling 1,200 req/min per key; per IP on `/d/*` 30 distinct docs/min and 100 distinct docs/day (Europe/Ljubljana day); per-key alert at 200 distinct docs linked/day, never blocks. Link TTL 300 s.
- Errors: 400 refused parameter, 401 no/bad key, 403 route not for key callers, 404 not visible / unknown, 410 link expired or altered, 429 with `Retry-After`.

## Review Focus

1. **Wall-clock dependence in tests.** An "expired" or "valid" fixture that flips with today's date makes the suite go red next week. Every expiry test passes `today` explicitly or uses 2001/2099 dates (Task 1).
2. **`decorate_documents` result ignored.** `item_documents` calls it for its side effect and ignores the return. A filter that drops rows does nothing unless every caller uses the returned list (Task 1 test `test_shop_view_drops_real_expired_on_every_route`).
3. **Caddy `@protected` swallowing `/d/*`.** The browser request carries no key, so without `not path /d/*` it gets a Basic-auth prompt. Covered by a Caddyfile text test (Task 2).
4. **Every customer is one IP.** If `X-Forwarded-For` is not trusted through compose Caddy, the per-IP limit becomes a global limit. Measured on the server before the limiter is wired (Task 3, step 1), and a unit test pins `client_ip` to the first XFF entry.
5. **Byte-range re-requests counted as new downloads.** A PDF viewer fetching ranges of one document must not use up the limit (Task 3 test `test_repeat_document_is_free`).

---

## File Structure

| File | Responsibility |
|---|---|
| `web/access.py` | Caller class on `request.state.caller`; `service` allowlist; 403 for service on staff `/api/`; `client_ip()` |
| `web/item_docs.py` | `for_shop` projection: drop real-expired, blank staleness dates, shop `url`; `shop_visible_document()` |
| `web/catalogue.py` | Manufacturer routes by id + `by-name/`; shop list; pass `for_shop` |
| `web/app.py` | Pass `for_shop` on `/api/items/...`; refuse flags; `/documents/{id}/file` untouched (policy does the closing) |
| `web/download_links.py` (new) | `sign()`, `verify()`, `/api/documents/{id}/link`, `/d/{token}` |
| `web/limits.py` (new) | `Limiter` (API ceiling, per-IP distinct docs, key alert, snapshot) |
| `app/config.py` | Six `Web` keys |
| `docker-compose.yml` | Six `WEB_*` lines under `web.environment` |
| `Caddyfile` | API-host allowlist, `/d/*` past `@protected`, `trusted_proxies` |
| `web/templates/status.html` | One line: today's linked documents vs alert threshold |

---

### Task 1: Service callers get only the webstore routes and the webstore view

**Files:**
- Modify: `web/access.py` (`allowed_classes`, `_refuse`, `guard`; add `caller`, `SERVICE_ROUTES`)
- Modify: `web/item_docs.py` (`decorate_documents`, `item_documents`, add `is_real_expired`)
- Modify: `web/catalogue.py` (`manufacturer_documents`, routes), `web/app.py` (`api_item_documents`)
- Modify: `web/item_link.py` (module docstring only: the webshop is no longer a JSON caller of `/item/*`)
- Modify: `Caddyfile`. `test_api_host_serves_exactly_what_the_app_opens_to_machines` fails the moment `/api/kpi` turns staff-only while Caddy still lets `/api/*` through. `@api_host_refused` therefore moves to `path_regexp` lines mirroring `SERVICE_ROUTES` here, and the `/documents/[0-9]+/file` regexp goes. Task 2 adds `/d/*`.
- Test: `tests/test_access.py`, `tests/test_item_docs.py`, `tests/test_catalogue_api.py`

**Interfaces:**
- Produces: `access.caller(request) -> str` (one of `STAFF`, `SERVICE`, `BC`, `ANON`; set by `guard` on `request.state.caller`).
- Produces: `access.SERVICE_ROUTES: tuple[re.Pattern, ...]`: anchored patterns for `^/api/manufacturers$`, `^/api/manufacturers/[^/]+$`, `^/api/manufacturers/[^/]+/documents$`, `^/api/items/.+/documents$` (covers the batch `/api/items/documents`). Task 2 adds `^/api/documents/\d+/link$`; Task 4 narrows `[^/]+` to `\d+`.
- Produces: `item_docs.is_real_expired(valid_to: date | None, basis: str | None, today: date) -> bool`, true iff `valid_to < today and basis in ("stated", "inherited")`.
- Produces: `decorate_documents(conn, docs, *, view="full", base_url="", for_shop=False, today: date | None = None) -> list`, **returns a new filtered list**. For `for_shop`: drops real-expired rows; for `basis == "staleness"` sets `valid_to = None` and builds `name` without ", valid to"; forces the customer projection. `today` defaults to the date in Europe/Ljubljana.
- Produces: `item_documents(..., for_shop: bool = False)`, and `manufacturer_documents(..., for_shop: bool = False)`. Both use the returned list.

- [ ] **Step 1: Write failing tests**

In `tests/test_access.py`:
- Update the `test_policy_table` rows: `/api/kpi` → `{STAFF}`, `/documents/42/file` → `{STAFF}`, `/item/12345` → `{BC, STAFF}`, `/api/items/12345/documents` stays `{SERVICE, STAFF}`. Add `/api/documents/7` → `{STAFF}` and `/api/manufacturers/VOCO/items` → `{STAFF}`.
- `test_service_key_on_staff_api_route_is_403`: `GET /api/kpi` with `X-API-Key` → 403. `GET /api/kpi` with a bogus key → 401.
- `test_service_key_cannot_open_item_card`: `/item/X?format=json` with the key → 404 (the `/item/*` refusal code).
- `test_service_file_route_refused`: `/documents/{doc}/file` with the key → 403.

In `tests/test_item_docs.py`, table-driven `test_is_real_expired` over `(valid_to, basis, expected)` with `today=date(2050,1,1)`: `(2049-12-31,"stated",True)`, `(2049-12-31,"inherited",True)`, `(2049-12-31,"staleness",False)`, `(2050-01-01,"stated",False)`, `(None,"stated",False)`, `(None,None,False)`.

In `tests/test_catalogue_api.py` (guarded client with the key, as in `test_access.py`):
- `test_shop_view_drops_real_expired_on_every_route`: seed one item with a doc `validity_to="2001-01-01"` (stated) and one with `"2099-01-01"`. The expired doc is absent from `/api/items/{ref}/documents`, the batch, and `/api/manufacturers/{name}/documents`. The future one is present. With the staff client the expired doc is present on all three.
- `test_shop_view_blanks_staleness_date`: a DoC with `validity_from` set, `validity_to=None`, no cited cert (basis `staleness`) → `valid_to is None` and `"valid to" not in name` for the key caller; staff sees the date.
- `test_view_full_is_ignored_for_service`: `?view=full` with the key → no `match_basis` key in any document.
- `test_service_refused_flags`: `include_unlinked=true` and `include_superseded=true` with the key → 400.
- `test_bc_card_unchanged`: `/item/{ref}?format=json&view=customer&k=…` still lists the expired doc (BC is not `for_shop`).

- [ ] **Step 2: Run them, expect failures**

Run: `./scripts/test.sh tests/test_access.py tests/test_item_docs.py tests/test_catalogue_api.py`
Expected: the new tests FAIL (policy rows, 403s, filtering).

- [ ] **Step 3: Implement**

- `allowed_classes`: `/item/*` → `{BC, STAFF}`; `/api/*` → `{SERVICE, STAFF}` only if the path matches `SERVICE_ROUTES`, else `{STAFF}`; `_DOC_FILE_RE` → `{STAFF}`.
- `guard` stores the class on `request.state.caller` before the check. `_refuse(path, cls)`: for `/api/` give 403 `"this route is not available to API keys"` when `cls == SERVICE`, else the existing 401.
- Routes call `access.caller(request) == access.SERVICE` to get `for_shop`. When `for_shop`: `view = "customer"`; a true `include_unlinked`/`include_superseded` → `HTTPException(400, ...)`, one module constant per message.
- Fix `web/item_link.py` docstring lines 9-11.

- [ ] **Step 4: Run, expect pass**

Run: `./scripts/test.sh tests/test_access.py tests/test_item_docs.py tests/test_catalogue_api.py tests/test_item_link.py tests/test_web.py`, then the full suite (`Caddyfile` touched): `./scripts/test.sh`
Expected: PASS. Existing tests expecting a service key on `/api/kpi` or `/item/*` are updated to the new policy, never deleted. The API-host parametrize list is updated here: `/api/documents/7` and `/documents/12/file` refused, `/api/manufacturers` and `/api/items/X/documents` let through.

- [ ] **Step 5: Commit**

```bash
git add web/access.py web/item_docs.py web/catalogue.py web/app.py web/item_link.py Caddyfile tests/test_access.py tests/test_item_docs.py tests/test_catalogue_api.py
git commit -m "access: API keys reach only the webstore routes, in the webstore view"
```

---

### Task 2: Signed download links

**Files:**
- Create: `web/download_links.py`, `tests/test_download_links.py`
- Modify: `app/config.py` (`Web`: `download_link_secret: str = ""`, `download_link_ttl_s: int = 300`, plus env reads beside `WEB_API_KEYS` around line 1172), `docker-compose.yml` (`web.environment`), `web/access.py` (`SERVICE_ROUTES` + `/d/` → `_ANY`), `web/item_docs.py` (shop `url`, `shop_visible_document`), `web/app.py` (register routes), `Caddyfile`
- Test: `tests/test_download_links.py`, `tests/test_access.py`

**Interfaces:**
- Consumes: `access.caller`, `item_docs.is_real_expired`, `app._document_file_response(row, cfg)` (import inside the route, as `web/item_link.py` does, to avoid the import cycle).
- Produces: `download_links.sign(doc_id: int, secret: str, now: int, ttl_s: int) -> tuple[str, int]` (token, exp) and `download_links.verify(token: str, secret: str, now: int) -> int | None` (doc_id, or None when malformed, altered or expired; `hmac.compare_digest`).
- Produces: `item_docs.shop_visible_document(conn, doc_id: int, today: date) -> dict | None`: `archive_url`, `content_hash` for a document present in `item_document_production` and not real-expired.
- Produces: `download_links.register_routes(app, conn_factory, cfg) -> None`.
- Shop `url` becomes `f"{base_url}/api/documents/{doc_id}/link"`.

- [ ] **Step 1: Write failing tests** in `tests/test_download_links.py`

- `test_sign_verify_roundtrip`: `verify(sign(19, "s", 1000, 300)[0], "s", 1299) == 19`.
- `test_verify_rejects`, table over: past exp (`now=1301`), altered doc_id, altered exp, altered sig, wrong secret, garbage `"abc"`, empty → all None.
- `test_link_issue_visibility`, table over seeded statuses: production + future date → 200 with `url` starting `{public_base_url}/d/` and an `expires_at` 300 s ahead. Staged / rejected / filed / production-but-expired (2001, stated) → 404.
- `test_download_serves_pdf`: issue, then `GET` the path of `url` with **no credential** on the guarded client → 200 `application/pdf` (point `archive_root` at `tmp_path` holding a small PDF).
- `test_download_rechecks_visibility`: issue, then set the link `staged` in the DB, then download → 404.
- `test_download_expired_or_altered_is_410`.
- `test_empty_secret_disables`: `download_link_secret=""` → link route 404 and `/d/x.1.y` 404.
- `test_download_never_redirects`: an `http://` `archive_url` → 404, not 307.
- `test_shop_url_points_at_link_route`: item documents with the key → every `url` ends `/api/documents/{doc_id}/link`. Staff still gets `/documents/{id}/file`.

In `tests/test_access.py`:
- Policy rows: `/d/abc` → `_ANY_CLASSES`, `/api/documents/7/link` → `{SERVICE, STAFF}`.
- API-host parametrize list: add `/d/1.2.x` and `/api/documents/7/link`, both let through.
- `test_d_path_skips_basic_auth`: the `@protected` block text contains `not path /d/*`.

- [ ] **Step 2: Run, expect failures**

Run: `./scripts/test.sh tests/test_download_links.py tests/test_access.py`

- [ ] **Step 3: Implement**

- Token format `"{doc_id}.{exp}.{sig}"`, with `sig = base64url(hmac_sha256(secret, f"{doc_id}.{exp}"))` without padding.
- Link route: `GET /api/documents/{doc_id:int}/link`. 404 if the secret is empty or the document is not `shop_visible_document`. Return `{"url", "expires_at"}` (ISO 8601 UTC).
- `/d/{token}`: 404 if no secret; 410 on `verify is None`; 404 if not `shop_visible_document`; 404 if `archive_url` has an http(s) scheme; else `_document_file_response`.
- Caddyfile: add `/d/*` to the `@api_host_refused` `not path` line, a `path_regexp` for the link route, and `not path /d/*` to `@protected`.
- compose: `WEB_DOWNLOAD_LINK_SECRET: ${WEB_DOWNLOAD_LINK_SECRET:-}` and `WEB_DOWNLOAD_LINK_TTL_S: ${WEB_DOWNLOAD_LINK_TTL_S:-300}`.

- [ ] **Step 4: Run the full suite** (config + Caddyfile touched)

Run: `./scripts/test.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/download_links.py web/access.py web/item_docs.py web/app.py app/config.py docker-compose.yml Caddyfile tests/test_download_links.py tests/test_access.py
git commit -m "downloads: five-minute signed links issued on click; file route closed to API keys"
```

---

### Task 3: Rate limits and the key alert

**Files:**
- Create: `web/limits.py`, `tests/test_limits.py`
- Modify: `web/access.py` (`client_ip`, call the limiter from `guard`), `web/download_links.py` (count the link and the download), `app/config.py` (four keys), `docker-compose.yml`, `Caddyfile` (global `servers { trusted_proxies static <peer> }`), `web/app.py` (`api_kpi` adds `webshop`; status ctx), `web/templates/status.html`
- Test: `tests/test_limits.py`, `tests/test_access.py`

**Interfaces:**
- Produces: `class Limiter(api_per_min: int, ip_docs_per_min: int, ip_docs_per_day: int, key_alert_per_day: int, clock: Callable[[], float] = time.time, tz: str = "Europe/Ljubljana")`, thread-safe (`threading.Lock`):
  - `check_api(key_id: str) -> int | None`: seconds to retry, or None.
  - `check_download(ip: str, doc_id: int) -> int | None`: a doc already counted for this IP today is free. Distinct docs per minute and per day; the per-day refusal returns seconds until local midnight.
  - `note_link(key_id: str, doc_id: int) -> None`: distinct per day; logs WARNING once when crossing `key_alert_per_day`.
  - `snapshot() -> dict`: `{"links_today": {key_id: n}, "alert_per_day": n}`.
- Produces: `access.client_ip(request) -> str`: the first entry of `X-Forwarded-For`, stripped; falls back to `request.client.host`.
- `key_id` = the first 8 hex chars of sha256(key). The key itself is never logged.
- `app.state.limiter` is built in `create_app` from config. Staff callers are never checked.

- [ ] **Step 1: Measure the IP chain on the server (read-only, before any code)**

Run on the server: `docker compose logs web --since 10m` while making one request to `https://api.cw.dentalia.si/healthz` from this machine, and compare with the compose Caddy log entry for the same request. Uvicorn logs the peer, not XFF. If needed, read what `web` receives with `docker compose exec web python -c` against a header-echo request to `localhost:8000` inside the network, without starting a new container.
Expected: the compose Caddy log shows the real outside IP in the incoming XFF from peer `172.18.0.1`. Record the peer address to trust.
If the outside IP is already lost at host Caddy (incoming XFF absent or replaced), **stop and re-plan the per-IP limit with Denis.**

- [ ] **Step 2: Write failing tests** in `tests/test_limits.py`, with a fake clock

- `test_api_ceiling`: 1,200 calls pass, the 1,201st returns a positive int; after 60 s of fake time it passes again.
- `test_ip_per_minute_distinct`: 30 distinct docs pass, the 31st is refused.
- `test_repeat_document_is_free`: the same doc 500 times in a minute passes.
- `test_ip_per_day_until_midnight`: doc 101 in a day is refused, retry is the seconds until 00:00 Europe/Ljubljana. At 00:00 it passes.
- `test_key_alert_never_blocks`: 500 distinct `note_link` calls never raise; WARNING logged exactly once (`caplog`).
- `test_client_ip_first_xff_entry`: `"203.0.113.9, 172.18.0.1"` → `"203.0.113.9"`; no header → `request.client.host`.
- In `tests/test_download_links.py`: `test_download_429_has_retry_after` with limits configured to 1/min.
- In `tests/test_access.py`: `test_staff_never_rate_limited`, and `test_trusted_proxies_is_narrow`: the Caddyfile global options contain `trusted_proxies static` and not `private_ranges`.

- [ ] **Step 3: Run, expect failures**

Run: `./scripts/test.sh tests/test_limits.py tests/test_download_links.py tests/test_access.py`

- [ ] **Step 4: Implement**

- Fixed one-minute windows keyed by `int(now // 60)`. The day key is the local date in `tz`. Old windows are discarded on access.
- `guard` calls `check_api` for `SERVICE` on `/api/*` and raises 429 with `Retry-After`.
- The link route calls `note_link`. `/d/` calls `check_download(client_ip, doc_id)` after `verify`.
- Config keys `rate_api_per_min=1200`, `rate_downloads_per_ip_min=30`, `rate_downloads_per_ip_day=100`, `alert_docs_per_key_day=200`, with the env names from spec §5.6 and compose lines.
- Caddyfile global block with the peer measured in step 1.
- `/api/kpi` gains `"webshop": limiter.snapshot()`. The status board shows one line ("Webstore downloads today: N of M before alert"), amber past M, in the existing card style.

- [ ] **Step 5: Full suite, then commit**

Run: `./scripts/test.sh`

```bash
git add web/limits.py web/access.py web/download_links.py web/app.py web/templates/status.html app/config.py docker-compose.yml Caddyfile tests/test_limits.py tests/test_download_links.py tests/test_access.py
git commit -m "limits: per-IP download limits, per-key API ceiling and daily alert"
```

---

### Task 4: Manufacturers by stable id

**Files:**
- Modify: `web/catalogue.py`, `web/access.py` (`SERVICE_ROUTES` manufacturer patterns → `\d+`), `web/templates/api_reference.html` (paths), `Caddyfile` (regexps follow), `web/app.py` (`api_reference` example uses an id)
- Test: `tests/test_catalogue_api.py`, `tests/test_access.py`

**Interfaces:**
- Produces: `catalogue._codes_for_id(conn, mfr_id: int) -> tuple[str, list[str]] | None`: (current `manufacturer.canonical_name`, raw codes). Resolved through every `manufacturer_name.name` of that id (canonical and alias) into `manufacturer_alias.raw_name WHERE canonical_name = ANY(names)`.
- Routes: `/api/manufacturers/{mfr_id:int}`, `/{mfr_id:int}/documents`, `/{mfr_id:int}/items` (staff). The name forms move to `/api/manufacturers/by-name/{canonical_name:path}` (+ `/documents`, `/items`), staff only.
- Every manufacturer payload gains `id`. The shop list drops `codes` and rows with 0 visible documents (visible = Task 1's `for_shop` rule).

- [ ] **Step 1: Write failing tests**

- `test_manufacturer_by_id` and `test_documents_by_id`: payload `id` equals `manufacturer.id`.
- `test_by_name_paths_staff_only`: the key gets 403 on `/api/manufacturers/by-name/X`.
- `test_id_survives_rename_without_sync`: apply the UI rename (`web/registry.py` rename function, `confirm=True`) and leave `manufacturer_alias` untouched. `/api/manufacturers/{id}/documents` still returns the docs, and `canonical_name` is the new name.
- `test_shop_list_has_no_codes_and_no_empty_rows`: for the key, no `codes` key and every `documents >= 1`. A manufacturer whose only document is expired (2001, stated) is absent.
- `test_numeric_canonical_name_is_not_an_id`: a canonical name `"077"` is reachable only via `by-name/077`. `/api/manufacturers/77` resolves the id or 404s.

- [ ] **Step 2: Run, expect failures**

Run: `./scripts/test.sh tests/test_catalogue_api.py tests/test_access.py`

- [ ] **Step 3: Implement** per Interfaces. Shop counts come from the same `for_shop` filtering, not a second SQL rule.

- [ ] **Step 4: Full suite** (`Caddyfile` touched)

Run: `./scripts/test.sh`

- [ ] **Step 5: Commit**

```bash
git add web/catalogue.py web/access.py web/app.py web/templates/api_reference.html Caddyfile tests/test_catalogue_api.py tests/test_access.py
git commit -m "catalogue: manufacturers addressed by stable id; webstore list without codes or empty rows"
```

---

### Task 5: Docs and the Erpium handover

**Files:** every path in spec §7, plus `docs/handover/erpium-webshop-api.md` (new).

- [ ] **Step 1:** Update each doc in spec §7, targeted edits only.
- [ ] **Step 2:** `docs/guide/pages/status.md` + `status.sl.md`: the new line. Then `python3 scripts/build-guide.py`.
- [ ] **Step 3:** Write the handover: spec §5.7 types, §5.8 flow, error table, limits in plain numbers, vocabulary (`type`, `regulation`, English `name`), "never put our `url` or the key in a page".
- [ ] **Step 4:** `docs/decisions.md`: five rulings dated 2026-11-23 (spec §7).
- [ ] **Step 5:** `tasks/followups.md`: add `[bc-link-key-rate-limit]` and `[include-unlinked-status-filter]` under `## Open`, in their topical subsection. Move `[public-surface-rate-limit]` to `tasks/followups-done.md`. Stage only this session's hunks; the file has another session's uncommitted edit.
- [ ] **Step 6:** `./scripts/test.sh tests/test_docs_sets.py`. Expected: PASS.
- [ ] **Step 7:** Commit the docs.

---

### Task 6: Deploy and live check (needs Denis's yes)

- [ ] **Step 1:** Ask Denis to approve the push and deploy, and to put a generated `WEB_DOWNLOAD_LINK_SECRET` in the server `.env`.
- [ ] **Step 2:** After the deploy, run the spec §8 live checks 1-6 from the server with the real key. Record the results and the re-measured expiry figure in `docs/state/2026-11-2x.md` or the build log.


---

### Task 7: `file_name`, `item_count` and linked items on webstore documents (spec § 5.9)

**Files:** `web/item_docs.py` (`decorate_documents` for_shop branch; `file_name_of(archive_url) -> str`), `web/catalogue.py` (`manufacturer_documents` attaches `items` capped at `SHOP_ITEMS_CAP = 50`), `docs/handover/erpium-webshop-api.md`, `docs/specs/read-api.md`, `web/templates/api_reference.html`.
Tests: `tests/test_item_docs.py::test_file_name_of` (table: `/archive/X/ifu/0123456789ab__CreaFILL_Flow_IFU.pdf` → `CreaFILL_Flow_IFU.pdf`; `file:///a/b/x.pdf` → `x.pdf`; empty → `""`), `tests/test_catalogue_api.py::test_shop_documents_carry_file_name_and_items`, `::test_shop_items_are_capped`, `::test_item_routes_carry_count_not_list`.

### Task 8: `GET /api/search` (spec § 5.10)

**Files:** new `web/search.py` (`search(conn, q, *, base_url, today) -> dict`, `register_routes`), `web/access.py` (`r"^/api/search$"`), `Caddyfile` regexp, `tests/test_web_import_closure.py`, docs as Task 7.
Tests in new `tests/test_search.py`: each `matched_on` path; an item with only expired documents is not returned; `q` of 1 and 101 chars → 400; at most 20 items; a manufacturer match returns `manufacturers` and no items for it; the key reaches it, BC and anon do not; API-host allowlist test row.
