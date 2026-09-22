# Item document access for BC and the webshop — design

**Date:** 2026-08-25
**Status:** proposed, awaiting Denis review
**Closes:** the mechanism half of `[serving-mechanism-and-doc-names]` (`tasks/followups.md:23`)
**Scope:** slices 1 and 3. Slice 2 (manufacturer catalogue, order batch) deferred.

Normative sources this spec defers to and does not restate:
- CLAUDE.md invariant 1 (producer-only web process) and the visibility rule.
- `docs/specs/read-api.md` — the authenticated read API contract. Unchanged by this spec
  except one additive query parameter (§6).
- `migrations/005_registry.sql` (`item_document_production`), `migrations/027_effective_expiry.sql`.

## 0. What changed from the first draft, and why

The first version of this spec proposed a public per-item capability URL,
`/d/{item_ref}/{sig}` signed with HMAC. **Denis rejected it on 2026-08-25, correctly, on
two grounds:**

1. **BC cannot compute a signature.** A BC item-card hyperlink is a computed field built
   by string concatenation. Having BC first call the API to *learn* its own link is
   technically possible and practically useless. Any per-item secret is therefore out.
2. **The webshop can relay.** The shop already authenticates the clinic and already knows
   their order lines. It can fetch from us server-side and proxy the bytes under its own
   origin. Nothing needs to be reachable by the public at all.

(2) dissolves the problem (1) made unsolvable. The capability URL existed to prevent
enumeration of a public surface; with no public surface, it is dead weight.

**There is no public, unauthenticated tier in this design.** Everything below is
authenticated, by one of three credentials.

## 1. Consumers

| # | Consumer | Reaches us how | Credential |
|---|---|---|---|
| 1 | **Webshop backend** | Server-to-server; renders and relays to its own logged-in customers | `X-API-Key` header |
| 2 | **BC item card** | A staff member clicks a hyperlink field | Shared static key in the URL, `?k=` |
| 3 | **Staff / the UI** | Browser | Existing Caddy Basic auth, unchanged |

A clinic never touches this system. They see `shop.dentalia.si`, which fetches from us and
serves the bytes itself. That is what removes the catalogue-enumeration problem entirely:
manufacturer listings and order batches never leave a server-to-server call.

## 2. Access policy

One table, one place: `web/access.py`, applied as middleware. The project already prefers
table-driven tests for anything with dispositions; this is that shape.

Caller classification, in order:

| Class | Established by |
|---|---|
| `staff` | Valid `X-Forwarded-User` (set by Caddy after Basic auth, `Caddyfile:40`) |
| `service` | `X-API-Key` matching one of `web.api_keys` |
| `bc` | Request to `/item/*` with `?k=` matching `web.bc_link_key` |
| `anon` | None of the above |

Route policy:

| Path | Allowed classes |
|---|---|
| `/healthz` | any |
| `/item/*` | `bc`, `service`, `staff` |
| `/api/*` | `service`, `staff` |
| `/documents/{id}/file` | `service`, `staff` |
| `/archive/*` | **`staff` only** |
| everything else | `staff` only |

`/archive/{path:path}` (`web/app.py:1741`) is deliberately staff-only even for a valid API
key: it serves the archive **by path**, which is walkable. The shop has no reason to want
it — `/documents/{id}/file` resolves the handle server-side and is the supported way to
get bytes.

Comparison is `hmac.compare_digest` for both key checks, never `==`.

`web.api_keys` is a **list**, so a second consumer or a rotation does not require a
downtime swap.

One consequence worth naming: because Caddy skips Basic auth on `/item/*` (§5), a staff
browser hitting that path carries no `X-Forwarded-User` and classes as `anon`, so the
`staff` cell in the `/item/*` row is unreachable in the compose deployment. It is kept in
the table because it is reachable in local dev (`require_authenticated_user=false`) and in
any deploy that does not take §5's exclusion. Staff browsing the registry use
`/items/{item_ref}` in the UI, which is unaffected.

## 3. The BC link

```
https://api.cw.dentalia.si/item/{item_ref}?k={bc_link_key}
```

BC builds this by concatenation in a computed field. No round trip, no computation, no
per-item state — the requirement that killed the first draft.

`item_ref` uses the `:path` converter (item refs contain `/`); with no trailing literal
segment the greedy match simply runs to the end of the path, so no backtracking subtlety
arises here — unlike the first draft's `/d/{ref}/{sig}`, which needed it.

`/item/` does not collide with the existing `/items/` HTML route (`web/app.py:1940`) —
different path, different handler.

### 3.1 Content negotiation

One URL for both BC and the shop (Denis's ruling, 2026-08-25):

- `Accept: application/json` → JSON
- `?format=json` → JSON regardless of `Accept`
- anything else → an HTML page

The explicit query override exists because BC's HTTP client is not guaranteed to send a
useful `Accept`, and a rep clicking from the item card must never land on a raw JSON blob.

### 3.2 What the page shows

Item identity, manufacturer, and the item's production documents with type, regulation,
validity dates, and a download link each. Plus **"download all"** — the "papers" a client
asks for on the phone are plural, and one click beats five.

## 4. Threat model, stated plainly

The shared BC key is all-or-nothing: one leaked URL leaks the credential for every item.
That is the accepted cost of BC being unable to compute anything, and it is bounded by
three things:

- It is **one secret**, rotated by changing one env var — unlike a per-item scheme, where
  rotation invalidates every link ever printed.
- The link is used by **staff**, inside BC, not handed to customers.
- What it protects is the **catalogue** (which items, from whom), not the documents. The
  documents are manufacturer declarations that exist to be handed out.

**Rate limiting is not resolved in this slice** and is the thing that would actually blunt
enumeration if the key leaked. **Unverified claim, check before relying on it:** Caddy's
`rate_limit` is believed not to ship in the standard `caddy:2.8` image (it is a community
plugin, `caddy-ratelimit`, needing a rebuilt image). I have not checked this against
Caddy's docs. Recorded in `tasks/followups.md` either way.

## 5. Ingress

Caddy currently Basic-auths everything except `/healthz` (`Caddyfile:35`). Two classes of
caller must now reach the app without Basic credentials, so the matcher gains two
exclusions:

- `/item/*` — always skips Basic; the app validates `?k=`.
- any request carrying an `X-API-Key` header — skips Basic; the app validates the key.

The second is what gives "Basic **or** API key" semantics, which `basic_auth` alone cannot
express. A request with a *bogus* `X-API-Key` therefore reaches the app and is rejected
there — the app is the authority on keys, Caddy only decides whether to demand Basic.

**Unverified, confirm during implementation:** the exact matcher syntax for "header field
is present" (expected `not header X-API-Key *`). If Caddy will not express it, the
fallback is to carve `/api/*` out of `@protected` entirely and let the app enforce both
classes — same policy table, one less clever matcher.

`/archive/*` stays inside `@protected` regardless, belt and braces with §2's app-side rule.

## 6. Customer-safe projection

The shop needs to know what it may render to a clinic. Additive query parameter on the
existing item endpoint, no new route:

```
GET /api/items/{item_ref}/documents?view=customer
GET /item/{item_ref}?k=...&view=customer
```

| Field | Default view | `view=customer` | Why |
|---|---|---|---|
| `item_name`, `manufacturer`, `mfr_ref` | yes | yes | The clinic confirms it is the article they hold; `mfr_ref` is on the box |
| `item_ref` | yes | yes | The shop keys on it |
| `manufacturer_code` | yes | **no** | BC vendor code, internal join key |
| `documents[].match_basis` | yes | **no** | Internal matching vocabulary; reveals link confidence |
| `documents[].expiry_basis` | yes | **no** | `staleness` would read as a legal expiry we invented, rather than the internal 5-year review horizon it is |
| evidence of any kind | never | never | Invariant-2 record; not on this endpoint at all |

Masking, per Denis: a customer-facing rendering should lead with **manufacturer identity**
(`VOCO Grandio Flow, ref 2691`) rather than Dentalia's catalogue identity. That is a
presentation rule for the shop, encoded here as field availability rather than enforced —
we cannot police the shop's templates.

Visibility stays `item_document_production` (both document and link production), unchanged
and not reimplemented.

## 7. Error and empty states

| Case | Response |
|---|---|
| Missing/wrong `?k=` on `/item/*` | `404`, not `403` — a `403` confirms the item exists |
| Missing/wrong `X-API-Key` on `/api/*` | `401` — a service caller deserves a diagnosable error |
| Item not in `item_mirror` | `200`, null identity fields, empty `documents` (matches `read-api.md` §2) |
| Mirrored, no production documents | `200`, empty `documents`; page shows "No documents on file yet" |
| `web.bc_link_key` empty | `/item/*` returns `404`; the route refuses to run unguarded |

No request from any class enqueues anything. These surfaces are read-only.

## 8. Config

Three keys on `class Web` (`app/config.py:416`):

| Key | Env | Default | Meaning |
|---|---|---|---|
| `api_keys` | `WEB_API_KEYS` | `""` | Comma-separated; empty disables the `service` class entirely |
| `bc_link_key` | `WEB_BC_LINK_KEY` | `""` | Shared `?k=` value; empty 404s `/item/*` |
| `public_base_url` | `WEB_PUBLIC_BASE_URL` | `""` | Absolute base for emitted URLs; empty means relative |

Both keys are **secrets**: top of `.env` beside the API keys and IMAP credentials, never
tuning keys in the runbook's config tables.

## 9. Deferred

- Slice 2: manufacturer endpoints (identity, line-level documents, paginated item list)
  and the order batch endpoint. Denis: *"in catalogue they may need more structured data
  per mfr, and in order maybe too — but these can be handled there."* Real needs, and now
  unambiguously service-API-shaped, but not this slice.
- Rate limiting (§4).
- `?since=` deltas and ETags — matter at 100k items, not at 16k.
- Any genuinely public tier. If links must later survive outside the webshop (emailed to a
  clinic, printed on a delivery note), that is a new surface with its own per-item guard,
  and the HMAC design from draft 1 is the starting point. Explicitly ruled out for now.

## 10. Slice 3 — API reference page

The complaint: only the items endpoints are known; the rest are obscure.

`FastAPI(title=...)` (`web/app.py:1665`) passes no `docs_url`, so `/docs` and
`/openapi.json` already exist behind Basic auth. Slice 3 adds the orientation they cannot:

- A **Developer** nav group in `base.html`, one entry, **API**, at `/api-reference`.
- The page states the three caller classes (§1), which paths each may reach (§2), and
  where each credential lives.
- A table of every endpoint, with **live example links built from a real `item_ref` read
  from the database**, so the page cannot drift into documenting an example that 404s.
- Links out to `/docs` and `/openapi.json`.

No new dependency, no build step.

## 11. Testing

Real Postgres, per CLAUDE.md. Table-driven where the policy is table-driven.

`tests/test_access.py` — the §2 matrix, every (class, path) cell:
- each credential establishes its class; a bogus one does not
- `service` is refused `/archive/*` and every UI path
- `bc` is refused `/api/*`
- `anon` is refused everything but `/healthz`
- both key comparisons use `compare_digest`
- empty `api_keys` disables `service`; empty `bc_link_key` 404s `/item/*`

`tests/test_item_link.py`:
- slash-bearing `item_ref` round-trips
- content negotiation: `Accept: application/json`, `?format=json`, browser default
- `view=customer` omits `match_basis`, `expiry_basis`, `manufacturer_code`
- a staged document and a staged link are each invisible
- unknown item → `200` with nulls; mirrored-but-uncovered → `200`, empty documents
- wrong `?k=` → `404`, never `403`
- "download all" contains exactly the production documents, named as `_download_name` produces

**Test selection:** this touches `app/config.py`, so CLAUDE.md's rule mandates the **full
suite** before commit, not a subset.

## 12. Files

| File | Change |
|---|---|
| `app/config.py` | 3 keys on `Web` + docstring |
| `web/access.py` | new — caller classification, policy table, middleware |
| `web/app.py` | wire middleware; add `/item/{item_ref:path}` route; `view=customer` on the item endpoint |
| `web/templates/item_card.html` | new — the BC/shop-facing page |
| `web/templates/api_reference.html` | new — slice 3 |
| `web/templates/base.html` | Developer nav group — slice 3 |
| `Caddyfile` | two matcher exclusions (§5) |
| `docker-compose.yml` | env wiring |
| `tests/test_access.py`, `tests/test_item_link.py` | new |
| `docs/specs/read-api.md` | `view=customer` entry (additive) |
| `docs/README.md`, `docs/runbook.md` | index + operational entry, per CLAUDE.md |

## 13. Invariants

Untouched. Every surface here is read-only, holds no write grant, enqueues nothing, and
reads `item_mirror`, `document`, and `item_document_production` as the `dentalia_api` role
— the same role and the same view the authenticated API already uses. Invariant 1 is
unaffected because nothing writes; the visibility rule is unaffected because nothing
reimplements the join.
