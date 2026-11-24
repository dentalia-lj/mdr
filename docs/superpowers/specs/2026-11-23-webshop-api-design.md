# Webshop API for Erpium: close the archive, sign the downloads, cap the rate

**Status:** approved 2026-11-23 (Denis). Builder check done, its answers folded in.
Uncommitted by Denis's choice.
**Builds on:** `docs/specs/read-api.md` (the response contract) and
`docs/superpowers/specs/2026-08-25-item-document-access-design.md` (caller classes,
`web/access.py`). This spec changes both; it does not restate them.

## 1. Why

Erpium (Žiga) builds the Dentalia webstore. After the call on 2026-11-23 the agreed
scope is: finish the APIs the webstore will use. The webstore needs three things:

1. the **manufacturer tree** (loaded once, to list manufacturers),
2. **documents per manufacturer** (on opening a manufacturer page),
3. **documents per item** (loaded automatically on every item page, not cached).

A file download is the deepest call: a customer clicks one document.

All three endpoints exist and are live (`web/catalogue.py`, `web/app.py`), with a key
configured on production. What is missing is access control fit for an outside
consumer. The intent: **moderate use by one webstore; no one, with or without
Erpium's key, can harvest the archive.**

## 2. What production does today (measured 2026-11-23, prod on 10f9378)

- **One API key** in `WEB_API_KEYS`. In the last 200 `/api/` proxy log lines, the only
  key-bearing requests came from the server itself. Erpium is the only external
  consumer; the BC card uses the separate `?k=` link key.
- Any key holder reaches **every** `/api/*` route in the **full** view: `/api/kpi`,
  `/api/documents/{id}` (all evidence), `/api/manufacturers/{name}/items` (the supplier
  map), and `?include_unlinked=true` (bound documents of any status, see 5.2).
- **`/documents/{id}/file` serves any document by sequential id, any status**
  (`web/app.py:3319`). Verified with the key: staged #382, rejected #52 and filed #5
  each returned their PDF. Production holds 1,017 production, 75 staged, 29 rejected,
  985 filed, 6 superseded documents. `/item/{ref}/documents/{id}` (BC) already refuses
  this; the service route never did.
- **Expired documents are returned**, by design (`read-api.md`: "Neither view filters
  on expiry"). Of 557 documents the item view serves, 127 are past `expires`: 53
  `stated`, 19 `inherited`, 55 `staleness`.
- `/api/manufacturers` lists 384 manufacturers, 58 with documents, and carries BC
  vendor codes (`codes`). Manufacturers are addressed by `canonical_name` in the path,
  which contains spaces and dots and changes on a UI rename.
- No rate limiting anywhere (`docs/dev/limits.md`, `[public-surface-rate-limit]`).
- Latency is fine for uncached live calls: item documents 34–120 ms, manufacturer
  documents ~150 ms (largest: IVOCLAR, 87 documents, 17 KB), manufacturer list ~190 ms
  (31 KB).

## 3. Goals, non-goals, anti-goals

**Goals**
- A key caller (Erpium) reaches only the webstore routes, only in the customer view,
  and only sees documents a clinic may be shown: published, current, not expired.
- Files reach the customer's browser **straight from our server** without the API key
  or a reusable link ever reaching the browser.
- Rate limits that never touch normal webstore use but cap what a leaked key or a
  scraper on Erpium's pages can extract.

**Non-goals**
- Free-text search (by name, supplier article number). The webstore looks items up by
  BC item number. Separate task if Erpium needs it.
- `?since=` deltas and ETags.
- Limiting the BC link key surface (`/item/*`). Real, separate risk: one followup.
- Scoped or named keys. With one consumer, "service class" **is** the webstore.

**Anti-goals** (failure even if it technically works)
- The webstore shows a staged, rejected, filed or expired document, or one that is not
  current for the item (`item_document_production` already guarantees currency; a
  `superseded`-status document that is still current for an item stays visible, by
  invariant 5. Measured 2026-11-23: all 557 visible documents have status
  `production`).
- A customer clicks a document in the afternoon on a page loaded in the morning and
  gets an error.
- Erpium hits a rate limit in normal use.
- A rate-limit or link check that fails open.

## 4. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| Files | **Erpium proxies bytes** through its server | No work for us; Erpium carries the bandwidth; our key stays server-side | Rejected: Denis wants files streamed from our server |
| Files | **Signed links embedded in API responses** (TTL ~1 h) | Simplest for Erpium; anyone reading the page harvests working links for an hour | Rejected |
| Files | **Link issued on click** (Erpium's own URL → Erpium asks us for a 5-min signed link → 302) | One extra call per click for Erpium; our link never sits in a page | **Chosen** (Denis, 2026-11-23) |
| Access | Named keys with scopes | Flexible; config and code for a second consumer that does not exist | Rejected: one consumer |
| Access | **Narrow the `service` class to the webstore routes, force the customer view** | No new config; a future second consumer with different needs reopens it | **Chosen** |
| Limits | Caddy rate-limit plugin | Needs a custom Caddy image (xcaddy) | Rejected for now |
| Limits | **In-app, in-memory limiter** | `web` is one container, one uvicorn process (`web/app.py` `uvicorn.run` with no workers); counters reset on deploy | **Chosen** |

## 5. Design

### 5.1 Who reaches what (`web/access.py`)

`service` (an `X-API-Key` caller) is allowed exactly these routes, all `GET`:

| Route | Purpose |
|---|---|
| `/api/manufacturers` | manufacturer tree |
| `/api/manufacturers/{id}` | one manufacturer |
| `/api/manufacturers/{id}/documents` | documents per manufacturer |
| `/api/items/{item_ref}/documents` | documents per item |
| `/api/items/documents?ref=…` | several items (max 100) |
| `/api/documents/{doc_id}/link` | **new**: issue a download link |

Everything else under `/api/` becomes **staff-only**: `/api/kpi`,
`/api/documents/{doc_id}`, `/api/manufacturers/{id}/items`. A key caller gets `403`
there (distinguishable from the `401` for a bad key). `/documents/{id}/file` leaves the
`service` class entirely (staff only).

**`/item/*` leaves the `service` class too** (BC + staff keep it). With the key it
returns the full view (expired documents, `match_basis`), and
`/item/{ref}/documents/{id}` and `/documents.zip` hand out PDF bytes outside the link
flow and every limit. Erpium has no reason to call it: it uses `/api/`. The proxy logs
show no key-bearing `/item/` request in the retained window, and no consumer is live.
`web/item_link.py`'s docstring, which still names the webshop as a JSON caller, is
corrected. *[builder-check call, 2026-11-23]*

**Caddy follows `allowed_classes`.** `tests/test_access.py` requires the API-host
allowlist in `Caddyfile` to equal what `allowed_classes` opens to non-staff callers,
so the `^/documents/[0-9]+/file$` exception in `@api_host_refused` goes, and `/d/*`
comes in (5.4).

`/d/{token}` (5.4) is reachable by **anyone**: the token is the credential and the
handler checks it.

The default-deny rule stays: a route not named is staff-only.

### 5.2 Customer view is forced for key callers

For a `service` caller:
- `view` is always `customer`; a passed `view=full` is ignored (not an error, so a
  stray parameter cannot break the webstore).
- `include_unlinked` and `include_superseded` are refused with `400`.

**Visibility for service callers** (every route they reach; see "Keyed on the caller"
below for why this is not tied to `view=customer`):
- Unchanged: only `item_document_production` (published document, production link,
  current per item and type).
- **New:** drop a document whose `expires < current_date` **and** `expiry_basis IN
  ('stated', 'inherited')`. Keep `staleness`-based ones: by the 2026-09-11 ruling a
  class I DoC past issue + 5 years is "review due, not expired".
- Effect, measured 2026-11-23: about 250 items go from "expired documents only" to
  "no documents". Re-measure at build time and put the figure in the results.
- **`staleness` rows show no date** (Denis, 2026-11-23). For a document whose
  `expiry_basis = 'staleness'`, the service view returns `valid_to: null` and a `name`
  without ", valid to …", whether the review date has passed or not. That date is
  Dentalia's five-year review horizon, not a legal expiry, and printed next to a
  document on a clinic's page it reads as one. That is the same reason
  `expiry_basis` is already hidden. The staff view keeps the date. About 80 items
  carry a past-review document today.

The staff `full` view is unchanged and keeps returning expired documents, so the
office still sees what needs renewing.

**Keyed on the caller, not on `view=customer`.** The BC card's `/item/{ref}?view=customer`
calls the same `item_documents()`. The BC card is out of scope (section 3), so the
expiry filter and the new `url` apply when the **caller is `service`**: an explicit
`for_shop` argument passed by the `/api/` routes, never inferred from `view`. The BC
card's output does not change. *[builder-check call]*

**`?include_unlinked` returns more than filed documents.** Its bound-document branch
(`web/catalogue.py:111-122`) has no status filter, so staged and rejected documents
come back too. Refusing the flag for `service` closes it for Erpium. The staff
behaviour is a followup, not this spec.

### 5.3 Manufacturer identity and list

- Every manufacturer payload gains **`id`** = `manufacturer.id` (bigserial, already
  present, unchanged by a UI rename since the rename writes `canonical_name` in place).
  Measured: all 384 alias canonical names have a `manufacturer` row except one blank
  `''` name with no documents.
- **Paths take the id only:** `/api/manufacturers/{id:int}`, `…/{id}/documents`,
  `…/{id}/items`. The name-addressed forms move to `/api/manufacturers/by-name/{name}/…`
  for staff. A "numeric means id" rule is not safe: RESOLVE stores an unmapped BC code
  as its own canonical name (`app/handlers/resolve.py:76-80`), and codes look like
  `077`. There are 0 numeric canonical names today, but the rule would break the day one
  appears. No external consumer uses the name paths yet, so moving them costs nothing.
  *[builder-check call]*
- **Resolving an id survives a rename.** The UI rename writes `manufacturer` and
  `manufacturer_name` (old name demoted to `kind='alias'`, `web/registry.py:879-893`)
  but not `manufacturer_alias`, which keeps the old canonical name until
  `playbooks sync`. So id → codes goes `manufacturer.id` → every `manufacturer_name.name`
  of that id (canonical and alias) → `manufacturer_alias.raw_name WHERE canonical_name =
  ANY(those names)`. The response's `canonical_name` is `manufacturer.canonical_name`
  (the new name). The list endpoint joins the same way. *[builder-check call]*
- Customer view of `/api/manufacturers`: drop `codes`; return only manufacturers with
  at least one visible document (58 today). `q=` still filters by name.

### 5.4 Download links

**Issue:** `GET /api/documents/{doc_id}/link` (service + staff).
- `404` unless the document is visible in the customer view (5.2): it must appear in
  `item_document_production` and must not be real-expired.
- Returns `{ "url": "<public_base_url>/d/<token>", "expires_at": "<ISO timestamp>" }`.
- No database write. The web process stays a reader.

**Token:** `<doc_id>.<exp>.<sig>`. `exp` = unix seconds, now + 300. `sig` = base64url
HMAC-SHA256 over `"<doc_id>.<exp>"` with a new secret, truncated to 32 bytes or more.
Stateless.

**Serve:** `GET /d/{token}`.
- Altered or malformed token → `410`. Past `exp` → `410`. Constant-time comparison.
- **Visibility re-checked at serve time** (same rule as issue): a document unpublished
  between click and download → `404`.
- Bytes through the existing `_document_file_response` (inline PDF, original
  filename). Production `archive_url`s are all local paths, so its redirect branch is
  not reached. The new route must still refuse to redirect (`404`), so a future
  `storage.base_url` cannot leak a storage URL through it.
- `X-Robots-Tag: noindex` already comes from Caddy.
- Byte-range requests within the link's lifetime work (Starlette `FileResponse`) and
  are free under 5.5. A viewer that comes back for more ranges after the link expired
  gets `410`. Accepted at 5 minutes: browsers' built-in viewers fetch a PDF within
  seconds. If it shows up in practice, raise `download_link_ttl_s`.

**Why 5 minutes:** the link is issued at the click, so its lifetime only covers the
302 hop, slow connections, and PDF viewers re-requesting byte ranges. A page left open
all day still works: every click issues a new link. A harvested link dies within
5 minutes.

**Caddy** (`Caddyfile`):
- add `/d/*` to the `@api_host_refused` exceptions;
- add `not path /d/*` to `@protected`, or the browser request (no key) falls into
  `basic_auth`.

### 5.5 Rate limits (in-app, in-memory)

One small module (e.g. `web/limits.py`), sync, thread-safe (FastAPI runs sync
dependencies in a threadpool), applied as part of the global dependency.

**Block per customer IP, alert per key** (Denis, 2026-11-23). Erpium has one key for
all its customers, so a block on the key would lock every customer out at once: an
anti-goal. Blocking therefore happens on `/d/*`, where the request comes from the
customer's own browser. The visible set is small (557 documents, measured 2026-11-23),
so a per-key cap high enough never to hurt Erpium would protect nothing; the key gets
an alert instead.

| Limit | Scope | Default | On breach |
|---|---|---|---|
| API ceiling | per key, all `/api/*` | 1,200 requests / minute | `429` + `Retry-After`. Guards against an Erpium loop only; normal use never reaches it |
| Downloads per minute | per client IP, `/d/*`, **distinct** `doc_id`s | 30 | `429` + `Retry-After` |
| Downloads per day | per client IP, `/d/*`, **distinct** `doc_id`s per calendar day (Europe/Ljubljana) | 100 | `429` until midnight |
| **Key alert** | per key, distinct `doc_id`s linked per day | 200 | **No block.** WARNING log once on crossing, and the status board line turns amber |

- Staff callers are exempt.
- **Distinct documents, not requests**, on every `/d/*` limit: PDF viewers fetch one
  file in several byte-range requests, and one clinic behind NAT shares an IP. A
  second request for a document already counted today is free. *[builder-check call]*
- Harvest cost: the whole visible set takes 6 days per IP and trips the key alert on
  day one. Accepted residual: a scraper with many IPs is slowed, not stopped.
  Closing that needs Erpium to forward its own customer id (rejected for now: work on
  their side, and we would have to trust their ids).
- No separate per-key limit on link issuing. The API ceiling covers it, and a
  block there would hit all customers.
- Counters reset on a process restart (deploy). Accepted.
- All numbers are config keys (5.6) so they can change without a code change.
- **Client IP: compose Caddy must trust the host Caddy.** The chain is host Caddy →
  `127.0.0.1:8000` (compose `caddy`, loopback-published, `caddy:2.8`) → `web`. The
  compose Caddy logs show the real client in the incoming `X-Forwarded-For`, from peer
  `172.18.0.1`. Caddy's reverse_proxy docs: "by default, the proxy will ignore their
  values from incoming requests, to prevent spoofing". So without a trusted proxy,
  `web` sees every customer as `172.18.0.1`, which would make the per-IP limit one
  global limit. Fix: a global `servers { trusted_proxies static private_ranges }` in
  the compose `Caddyfile`. (First built as `172.16.0.0/12`; the final review widened
  it because Docker falls back to `192.168.0.0/16`. The site is loopback-only, so only
  the host and our containers are trusted.) Then `web` reads the client IP as the **first** `X-Forwarded-For`
  entry. That entry is safe only because host Caddy itself ignores client-supplied
  XFF.
- **Verify on the server before relying on it** (task 3, step 1): one request from a
  known outside address, with `web` logging the XFF it receives, before and after the
  change. Caddy's docs do not say since which version the ignore default holds, so
  2.8 is checked empirically, not assumed. If the measured result differs, stop and
  re-plan the per-IP limit.
- Docker's userland proxy is what makes the peer `172.18.0.1`. Only processes on the
  host can reach the loopback port, so trusting that peer trusts the host, not the
  internet.
- Visibility: the current day's distinct-document count per key and the alert
  threshold go into `/api/kpi` (staff), and onto the status board as one line (amber
  past the threshold). The status board line is
  a UI change, so the guide pages follow (section 7).

### 5.6 Config (`app/config.py`, class `Web`)

| Key | Env | Default | Meaning |
|---|---|---|---|
| `download_link_secret` | `WEB_DOWNLOAD_LINK_SECRET` | `""` | HMAC secret. **Empty disables `/api/documents/{id}/link` and `/d/*` (404)**, never "unsigned". A secret: top of `.env` |
| `download_link_ttl_s` | `WEB_DOWNLOAD_LINK_TTL_S` | `300` | Link lifetime |
| `rate_api_per_min` | `WEB_RATE_API_PER_MIN` | `1200` | 5.5 |
| `rate_downloads_per_ip_min` | `WEB_RATE_DOWNLOADS_PER_IP_MIN` | `30` | 5.5, distinct documents |
| `rate_downloads_per_ip_day` | `WEB_RATE_DOWNLOADS_PER_IP_DAY` | `100` | 5.5, distinct documents |
| `alert_docs_per_key_day` | `WEB_ALERT_DOCS_PER_KEY_DAY` | `200` | 5.5, alert only |

Rotating the secret invalidates links issued in the last 5 minutes. Harmless.

**`.env` alone does not reach the container.** The `web` service lists each variable
under `environment:` (`docker-compose.yml`, e.g. `WEB_API_KEYS: ${WEB_API_KEYS:-}`), with
no `env_file`. All six keys get a line there. `docker-compose.yml` is in task 2's and
task 3's file lists. *[builder-check finding]*

### 5.7 Response types (customer view, what Erpium receives)

```ts
type DocType    = "DoC" | "EC" | "IFU" | "ISO" | "SPP" | "other"   // all six in production today
type Regulation = "MDR" | "MDD" | "n.a."

interface Document {
  doc_id:     number
  type:       DocType
  regulation: Regulation
  valid_from: string | null   // "2026-06-25"
  valid_to:   string | null   // legal expiry, never in the past; null = no stated expiry
                              // (incl. class I DoCs, whose review date is not shown)
  name:       string          // "CARL MARTIN EC (MDR), valid to 2031-06-29" (English);
                              // no ", valid to" when valid_to is null
  url:        string          // "<base>/api/documents/19/link" (key required, server-side)
}

// GET /api/manufacturers?q=
interface ManufacturerList {
  manufacturers: { id: number; canonical_name: string; items: number; documents: number }[]
  total: number
}

// GET /api/manufacturers/{id}/documents
interface ManufacturerDocuments { id: number; canonical_name: string; documents: Document[] }

// GET /api/items/{item_ref}/documents
interface ItemDocuments {
  item_ref: string
  item_name: string | null      // null = item not known to us
  manufacturer: string | null
  mfr_ref: string | null
  documents: Document[]
}

// GET /api/items/documents?ref=A&ref=B   (max 100, order kept, unknown refs kept as nulls)
interface ItemDocumentsBatch { items: ItemDocuments[]; total: number }

// GET /api/documents/{doc_id}/link
interface DownloadLink { url: string; expires_at: string }   // url = "<base>/d/<token>"

// Every error
interface ApiError { detail: string }
// 400 refused parameter · 401 no/bad key · 403 route not for key callers
// 404 unknown manufacturer, or document not visible · 410 link expired/altered
// 429 rate limit (Retry-After header)
```

**`url` changes meaning in the customer view**, from `/documents/{id}/file` to the
link-issuing route. This is the second deliberate exception to the additive-only rule
in `read-api.md` §5. It is allowed for the same reason as the first: no external
consumer is live yet. The `full` view keeps `/documents/{id}/file`.

### 5.8 The click flow (for the handover doc)

```
customer clicks   → https://<webstore>/dokumentacija/19          (Erpium URL, permanent)
Erpium server     → GET https://api.cw.dentalia.si/api/documents/19/link   X-API-Key
                  ← 200 { url: "https://api.cw.dentalia.si/d/19.1763…", expires_at }
Erpium            → 302 Location: <url>
browser           → GET /d/19.1763…  → 200 application/pdf (inline)
```

Erpium must not put our `url` values, or the key, into any page.

### 5.9 Telling documents apart: `file_name` and linked items (Denis, 2026-11-24)

Measured 2026-11-24: DENTAL WORLD has 8 IFUs, one per product, and the derived
`name` ("manufacturer type (regulation), valid to …") is the same for all 8.
Erpium's current FTP tree shows file names (`CreaFILL_Flow_IFU.pdf`). Every
webstore document therefore gains:

- **`file_name`**: the original file name, the basename of `archive_url` with the
  archive's `{hash12}__` prefix removed. Never a path, never the storage handle.
- **`item_count`** and **`items`** (manufacturer documents only): how many of
  this manufacturer's items the document is current for, and `[{item_ref,
  item_name}]` of them, **capped at 50**, with `items_truncated: true` past the
  cap. Measured: 528 of 557 documents cover 1–39 items; 9 cover 202–2,581 (ISO
  and range certificates), which uncapped would make one manufacturer page
  hundreds of KB. Not on the item routes: there the item is already the context,
  and the count would cost a currency computation per document (~30 ms measured)
  on every item page and up to 100 times per batch. *[agent's call, 2026-11-24]*

### 5.10 Search: `GET /api/search?q=` (Denis, 2026-11-24)

The third webstore call: one query box, documents of the matching items.

- `q`: 2–100 characters after trimming, else `400`. Two characters minimum so a
  single letter cannot list the catalogue.
- Matches, in this order, an item kept at its first match (`matched_on`):
  1. `item_ref`, exact (case-insensitive);
  2. `mfr_ref`, exact (case-insensitive);
  3. Basic UDI-DI on a document (`document.basic_udi_di`, spaces ignored), giving
     the items that document is current for. BC items carry no UDI (0 of 20,405,
     measured 2026-11-24), so this is the only UDI path;
  4. item name, fuzzy (`pg_trgm`, the existing `item_mirror_name_trgm` index,
     3–4 ms measured), best first.
- **Only items with at least one webstore-visible document**, so search cannot
  walk the catalogue. At most 20 items.
- Each item comes back as the item-documents payload (§ 5.7) plus `matched_on`.
- Manufacturer names matching `q` (substring, canonical or alias) come back
  separately as `manufacturers: [{id, canonical_name, documents}]`, from the
  webstore tree (§ 5.3): never their items, which can run to 2,581.
- Response: `{"query", "manufacturers": [...], "items": [...]}`.
- Open to the key (`SERVICE_ROUTES`, Caddy API-host rule) and staff.

### 5.11 Other documents of the manufacturer (Denis, 2026-11-24)

Measured 2026-11-24 against Erpium's FTP tree as copied to the server
(`/imports/dentalia-web`, 2026-09-22; 2,579 files without MSDS, hashed and
matched to `document.content_hash`): the webstore view showed **493**. 1,004 are
`filed` (held and read, covering no item Dentalia sells), 387 published with no
published item link, 416 never registered, the rest older versions, expired,
staged or rejected.

`GET /api/manufacturers/{id}/documents` therefore also returns, for the key,
**`other_documents`**: documents bound to any name the manufacturer has had
(`canonical_manufacturer`) that are `filed`, or `production` with no production
link (`item_docs.OTHER_DOCUMENT_SQL`), not already in `documents`, not past their
legal expiry. Same document object, no `items`. A link may be issued for them.
The tree and the detail carry an `other_documents` count, and a manufacturer
with only other documents is in the tree. Staff are unchanged (they have
`include_unlinked`). With this the webstore shows **1,661 of 2,579** files, and
folders with nothing to show drop from 23 to 10 of 70. The 416 unregistered
files are a followup (`[ftp-unregistered-rerun]`).

## 6. Tasks (ship in this order; each ships alone)

| # | Task | Files (main) | Human h | CC h |
|---|---|---|---|---|
| 1 | Narrow `service` to 5.1's routes (incl. dropping `/item/*`), force customer view, refuse the two flags, hide real-expired documents for service callers | `web/access.py`, `web/item_docs.py`, `web/catalogue.py`, `web/app.py`, `web/item_link.py` (docstring) | 3 | 1 |
| 2 | Download links: secret config, issue route, `/d/{token}`, serve-time re-check, no-redirect guard, `/documents/{id}/file` closed to service, customer `url` → link route, Caddyfile | new `web/download_links.py`, `web/access.py`, `web/item_docs.py`, `app/config.py`, `Caddyfile`, `docker-compose.yml` | 5 | 1.25 |
| 3 | Rate limits: verify the client IP chain on the server first, `trusted_proxies`, then the limiter (API ceiling, per-IP distinct-document limits, key alert), 429s, `/api/kpi` + status board line | new `web/limits.py`, `web/access.py`, `app/config.py`, `docker-compose.yml`, `Caddyfile`, status template | 4 | 1 |
| 4 | Manufacturer `id` in payloads and paths (name paths to `by-name/`), rename-safe id resolution, customer list without `codes`, only with documents | `web/catalogue.py`, `web/templates/api_reference.html` | 4 | 1 |
| 5 | Docs (section 7) and the Erpium handover doc | docs | 3 | 0.75 |
| 6 | Deploy (Denis's yes) and live check (section 8) | — | 2 | 0.5 |
| | **Total** | | **21** | **5.5** |

Tasks 1 and 2 close the live archive leak and go first. Task 2 touches more than three
files but is one coherent unit (a link route is useless without its serve route and
the Caddy rule), so it is not split.

## 7. Docs to update in the same session

- `docs/specs/read-api.md`: caller rules, forced customer view, expiry filter, `id`,
  `url` meaning, link + `/d/` routes, error codes, §5 exception.
- `docs/dev/config-reference.md`: the six keys, with blast radius (empty secret =
  webstore downloads off; a per-IP cap set too low refuses real customers; the API
  ceiling set too low refuses every customer at once, since it is per key).
- `docs/dev/limits.md`: rewrite the rate-limit row (in-app, in-memory, resets on
  deploy; `/item/*` still unlimited).
- `docs/dev/web.md` and `docs/architecture.md`: caller classes, `/d/*`.
- `docs/runbook.md`: set the secret on deploy, read the daily count, raise a cap.
- `docs/troubleshooting.md`: "webstore downloads return 429 / 410 / 404".
- `web/templates/api_reference.html` (`/api-reference`): Erpium section, the click
  flow, the response types.
- Status board line → `docs/guide/pages/<screen>.md` + `.sl.md`, then
  `python3 scripts/build-guide.py`.
- `docs/decisions.md`: five rulings dated 2026-11-23 (service class = webstore; link on
  click, 5 min; service view hides real-expired, keeps staleness; staleness rows show
  no date to the webstore; block per customer IP, alert per key).
- Handover for Žiga: `docs/handover/erpium-webshop-api.md` (EN), built from 5.7 + 5.8
  plus the vocabulary note that `name` is English and Erpium labels its own.
- `tasks/followups.md`: `[bc-link-key-rate-limit]` for `/item/*`;
  `[include-unlinked-status-filter]` (staff `?include_unlinked` returns staged and
  rejected bound documents); close `[public-surface-rate-limit]` for `/api/*` and
  `/d/*`.

## 8. Verification

**Tests** (table-driven where a rule has cases):
- `tests/test_access.py`: every route × {staff, service, bc, anon} → expected status,
  including the 403s and `/d/*` open to anon.
- `tests/test_catalogue_api.py`, `tests/test_item_docs.py`: forced customer view,
  refused flags, expiry filter by basis (stated, inherited hidden; staleness kept
  with `valid_to` null and no date in `name`; undated kept), BC card output unchanged,
  `id` paths, `by-name/` paths, id resolution after a rename without `playbooks sync`,
  list without `codes` and without zero-document rows.
- New `tests/test_download_links.py`: issue for visible / staged / rejected / filed /
  superseded / expired documents; token tampering (doc_id, exp, sig), expiry,
  empty secret → 404, serve-time re-check, no redirect.
- New `tests/test_limits.py`: each limit at N and N+1, repeat download of a counted
  document is free, day rollover in Europe/Ljubljana, key alert never blocks, staff
  exempt.
- Selection rule: `app/config.py` changes → **full suite** before commit.

**Live, after deploy** (read-only, from the server, with the real key):
1. Count up through `/documents/{id}/file` with the key → all refused.
2. Link for a staged, rejected and filed id → `404`; for a published id → a link
   that downloads once and returns `410` after 5 minutes.
3. Altered token → `410`.
4. `/api/kpi`, `/api/documents/1`, `/api/manufacturers/{id}/items` with the key → plain
   `404 Not found` on `api.cw.dentalia.si` (Caddy's API-host rule answers first), `403`
   on the office name. (Corrected by the final review.)
5. Per-IP limit from one address → `429` on the 31st distinct document within a
   minute. A repeat of an already-downloaded document still succeeds.
6. Expired-filter figure re-measured and recorded.

## 9. Left to the builder

- Exact token encoding details within 5.4 (base64url padding, separator).
- Limiter algorithm (fixed or sliding window) as long as the numbers in 5.5 hold.
- Wording of `detail` strings (one constant each, as `item_docs` does).
- Where in the status board the cap line sits, within the existing card style (no
  new card, so no mockup).
