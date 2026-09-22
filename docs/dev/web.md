# The web layer, as built

**Status:** live. Measured against the tree at 2026-09-07.

This file covers the standalone producer UI (`web/`). It does not restate the
route table (`docs/runbook.md`) or the write-path separation (`docs/architecture.md`)
— it says how the module is wired, who is allowed to call it, and what would
surprise you reading the code for the first time.

## Idea

`web/` is a FastAPI + Jinja + HTMX app, physically separate from the worker
(own package, own image `Dockerfile.web`, own `pyproject.toml` extra), that
connects to Postgres as `dentalia_api` and is structurally a job **producer**:
every write path either enqueues a row on `job` or lands in one of a short,
explicitly-granted list of side tables, and it can never write `document` /
`item_document` / `evidence` (Invariant 1). Three non-overlapping consumers
reach it — a Basic-auth browser, the webshop backend over `X-API-Key`, and a
Business Central item-card hyperlink carrying a shared static key — and one
policy module decides who gets which route.

## Where

| Module | Lines | Owns |
|---|---|---|
| `web/app.py` | 4371 | App assembly (`create_app`, `web/app.py:1853`), status/jobs board, ingest/upload/import forms, staging + manual + missing + dead queues, registry read pages (`items`, `documents`, `expiry`, `inflight`, `data-quality`), the `/pipeline` hub, file serving, drafts, the read-only `/api/*` single-item routes |
| `web/registry.py` | 2508 | `/manufacturers*`, `/playbooks*` — manufacturer detail, playbook authoring/editing, SRN confirm queue, EUDAMED sweep release and the cross-manufacturer digest |
| `web/scheduler_view.py` | 479 | `GET /scheduler`, `POST /scheduler/run/{cron}` — cron visibility and hand-run |
| `web/onboarding.py` | 393 | The guided manufacturer-onboarding flow (`/onboarding*`): who, domains, library, done |
| `web/catalogue.py` | 241 | Service-shaped `/api/manufacturers*` and `/api/items/documents` (batch) — the webshop's slice, never reachable by `bc` or anonymous |
| `web/bc_push_view.py` | 157 | `POST /items/{item_ref}/bc-push` (per-item), `GET /bc-push` + `POST /bc-push/apply` (bulk preview-then-apply) — both enqueue `bc.push`, neither speaks to Business Central |
| `web/failures.py` | 277 | The Failed split (added 2026-09-11): `classify_failure()` (timeout / dead-address / developer), `failed_split()` and `failed_counts()`, which count only the dead jobs whose work has not been queued again, and the receipts of `/dead/retry-timeouts` and `/dead/search-again`. Read-only, stdlib only |
| `web/missing.py` | 406 | Missing documents (added 2026-09-11): `missing_view()` for `/missing` (open `discovery-dead-end` tasks, oldest first, 20 a page, manufacturer chips), `missing_summary()` for Today, `upload_context()` for the Upload page's line naming the item and the group it posts, `places_searched()` (the places the group's last `discovery_log` run proves were searched), and the "Search again" receipt. Read-only |
| `web/access.py` | 158 | `allowed_classes()`, `classify()`, `trusted_user()`, `guard()`: the whole access policy. Plus `is_operator()`, which decides what a signed-in login is offered, never whether it gets in |
| `web/item_link.py` | 106 | `GET /item/{item_ref}` (BC hyperlink) and `/item/{item_ref}/documents.zip` |
| `web/item_docs.py` | 101 | `item_documents()` / `decorate_documents()` — the one item-documents query shared by the BC link and the webshop API so the two can never disagree |
| `web/words.py` | 356 | One vocabulary (office UI redesign spec § 9, added 2026-09-14): `WORDS` / `word()` (the § 9 table, unknown values pass through), `DOC_TYPE_WORDS` (which IS `web.app.DOC_TYPE_LABELS`) and `DOC_TYPE_SUBJECTS` (the sentence forms), `doc_display_name()`, `day_text()`, `week_label()`, `num()` and `receipt()`. P7b (2026-09-15) added three more dicts, each in its OWN namespace because `WORDS` is flat and two of their keys already mean something else there (`filed` is a document status *and* an audit event; `manual` is a list *and* a discovery rung): `AUDIT_EVENT_WORDS` / `audit_event()`, `RUNG_WORDS` / `rung()` and `RUNG_OUTCOME_WORDS` / `rung_outcome()`. The branch review (2026-09-15) moved `DRAFT_STATUS_WORDS` / `draft_status()` and `DRAFT_KIND_WORDS` / `draft_kind()` here from `web/app.py` for the same reason: they turned out to be read by four screens, not two, and the two that cross-link to `/drafts` (`/expiry`'s **Chase** column and a document's **Renewal chase**) had no map in scope and printed the stored value. `web.app.DRAFT_STATUS_WORDS` / `DRAFT_KIND_WORDS` now point here, the way `DOC_TYPE_LABELS` does. Pure functions, no database and no request; registered in `create_app` as the Jinja filters `word` / `day` / `week_label` / `num` / `doc_type` / `doc_name` / `audit_event` / `rung` / `rung_outcome` / `draft_status` |

Line counts are as of 2026-09-07 and are here for proportion, not precision —
the point of the table is that `app.py` is an order of magnitude larger than
anything else, and why.

**Registration wiring.** `create_app` (`web/app.py:1853`) builds the `FastAPI` instance with
`docs_url=None, openapi_url=None, redoc_url=None` and one global dependency,
`Depends(access.guard(cfg))` (`web/app.py:1885`). The six sub-registrar
modules are imported once at module scope and each exposes
`register_routes(app, ...)`, called in `create_app` after `templates` and
`_conn` exist, in this order: `registry` first because it needs
`_authenticated_user` as an argument, then `onboarding`, `scheduler_view`,
`item_link`, `bc_push_view`, `catalogue`. Every other route — Today, the status
board,
staging, manual, dead, ingest/upload/import, drafts, file serving — is defined
directly in `create_app`'s body, which is why `app.py` is four thousand lines
and the others are not.

`access.guard` is a **sync FastAPI dependency**, not `@app.middleware("http")`
— middleware forces `async def` and this codebase is sync by ruling
(`web/access.py:1-8`).

## Input → Output

| Direction | Shape |
|---|---|
| In | Staff browser (Caddy Basic auth, unauthenticated at the app layer), webshop backend (`X-API-Key` header), BC item-card GET (`?k=` query param) |
| Out (write) | `job` rows via `app.queue.enqueue` only, plus the narrow exceptions below — never a direct write to the registry |
| Out (read) | Full HTML pages, HTMX partials, the read-only `/api/*` JSON, archived file bytes (`/archive/*`, `/documents/{id}/file`, `/documents/{hash}/text`), a per-item ZIP (`/item/{ref}/documents.zip`) |

## Rules

**Access policy** (`web/access.py`). Four caller classes —
`STAFF`, `SERVICE`, `BC`, `ANON` (`web/access.py:33-36`) — checked
most-privileged first in `classify()` (`web/access.py:109-122`): a staff
browser that also happens to carry an API key still classifies `staff`. Path
matching in `allowed_classes()` (`web/access.py:49-72`) is **default-deny**:
anything not named is staff-only, so a route added later is private until
someone deliberately opens it. `/documents/{id}/file` is service-reachable
(the shop relays bytes) but `/documents/{id}` and `/documents` are not — a
prefix match can't tell those apart, so `_DOC_FILE_RE` (`web/access.py:46`)
carries its own pattern.

Refusal codes differ by surface, deliberately (`web/access.py:125-133`):
`/item/*` returns 404 (a 403 would confirm the item exists to an
unauthenticated caller), `/api/*` returns 401 (a service caller can act on
that), everything else 403.

`require_authenticated_user=False` (no proxy in front — local dev, tests)
makes every caller classify as `staff` (`web/access.py:112-113`); this is the
existing contract of `_authenticated_user`, generalised across the whole app,
not a new rule for `guard`.

**`decided_by` sourcing.** `_authenticated_user` (`web/app.py:1846-1862`)
returns `None` when auth isn't required; otherwise it reads
`access.trusted_user`, and a required-but-empty header is a hard 403 — it
never falls back to a client-supplied value. `staging_apply`
(`web/app.py:3551-3559`) then layers three sources in order: the trusted
header if present, else the submitted form field, else
`DEFAULT_DECIDED_BY = "user:admin"` (`web/app.py:277`).

**Producer-only, and its named exceptions.** Migration `007_roles_grants.sql`
grants `dentalia_api` `SELECT` on every table, `INSERT, UPDATE` on `job`
only, and explicitly nothing on `document` / `item_document` / `evidence` /
`audit_log` — tested by connecting as the real role, not by trusting the app
code's intentions (`tests/test_web.py:652-661`,
`test_dentalia_api_role_cannot_write_registry`). Later migrations open a
short, explicit list of additional write grants for specific UI features,
none of which touch the three registry tables Invariant 1 protects:

| Table | Grant | Migration | Used by |
|---|---|---|---|
| `upload_inbox` | INSERT only | `014_upload.sql:27-28` | manual upload spool (`web/app.py:3068`) |
| `import_inbox` | INSERT only | `040_import_inbox.sql:42-43` | BC/vendor import spool (`web/app.py:3143,3202`) |
| `manufacturer`, `manufacturer_bc_code`, `manufacturer_name` | SELECT, INSERT, UPDATE | `049_manufacturer_entity.sql:129-131` | rename, contact emails, BC-code claim, playbook slug (`web/registry.py:2161-2233`, `2085-2130`, `2344-2395`, `2131-2160`) |
| `manufacturer_playbook_revision` | SELECT, INSERT (no UPDATE/DELETE — append-only by grant) | `050_playbook_body.sql:85` | `save_playbook_body` (`web/registry.py:689-786`) |
| `playbook_epoch` | SELECT, UPDATE | `050_playbook_body.sql:87` | playbook save bookkeeping |
| `email_draft` | SELECT, UPDATE | `029_email_outbound.sql:105` | draft edit/status (`web/app.py:2826`, `2852`) |
| `manufacturer_srn` | INSERT, UPDATE | `042_eudamed_manufacturer.sql:98` | SRN confirm/reject (`web/registry.py:1789-1816`) |
| `eudamed_sweep_state` | INSERT, UPDATE | `044_eudamed_sweep_state.sql:46` | sweep release (`web/registry.py:1856-1900`) |

Every one of these is a registry-adjacent side table for a specific UI
feature (manufacturer identity, playbook authoring, drafts, EUDAMED review) —
none is `document`, `item_document`, or `evidence`, and the app code never
issues its own `INSERT`/`UPDATE` against those three regardless (grep
confirms the only non-GATE writers in the whole tree are two CLI-only repair
scripts, `docs/dev/handlers.md` "Invariant 1" section).

**`/docs` and `/openapi.json` are re-registered through the router**
(`web/app.py:1787-1795`), not left to FastAPI's built-ins, precisely so the
global `dependencies=[Depends(access.guard(cfg))]` applies to them (see
Gotchas).

**Job-type mirror.** `JOB_TYPES` (`web/app.py:111-131`) is a hand-maintained
list mirroring the closed `job_type` enum, used only to populate the status
board's filter — it never defines a new tag, and
`tests/test_web.py::test_job_type_lists_match_db_enum` keeps it honest.

## Limits

- No login system. Identity is whatever Caddy's Basic auth resolves and
  forwards; `web/` trusts the header and nothing more (`web/access.py:90-106`).
- The web role never writes `document` / `item_document` / `evidence` under
  any code path — human decisions are enqueued (`gate.apply`) and applied by
  a worker.
- `web/` never sends email. `email.request`/`email.reminder` only ever reach
  `sent` by a human editing `email_draft.status` by hand
  (`docs/dev/handlers.md` per-tag section).
- `web/` imports nothing from `app.handlers` / `app.workers` — the boundary
  is structural (no import path exists), not just a grant.
- The slim image cannot import the extractor stack at all (see Gotchas) —
  `web/` never parses a PDF and never will without changing `Dockerfile.web`.

## Gotchas

- **FastAPI's own `/docs`/`/openapi.json` bypass router-level dependencies.**
  `add_route()` wires them straight onto the Starlette app, not through the
  `APIRouter`, so the global `dependencies=[...]` never ran for them.
  Measured 2026-08-25: both answered 200 with no credential at all while
  `allowed_classes()` claimed staff-only, and Caddy compounds it by skipping
  Basic auth for any request carrying an `X-API-Key` header, valid or not —
  the app was the only thing standing between a bogus key and the schema
  listing. Fixed by re-declaring both routes through `@app.get(...)`
  (`web/app.py:1767-1795`), which is why they exist as ordinary route
  functions in this file instead of `FastAPI(docs_url="/docs")`.
- **`require_authenticated_user=False` changes where `decided_by` comes
  from**, not just whether auth is checked: with no proxy in front,
  `_authenticated_user` returns `None` unconditionally
  (`web/app.py:1851-1852`), so `staging_apply` falls through to the raw form
  field or `DEFAULT_DECIDED_BY` — an audit trail written in this mode can
  carry whatever a client posts.
- **A trusted-user header containing a brace is treated as empty.** Caddy's
  `header_up X-Forwarded-User {http.auth.user.id}` fires even on requests
  that skipped `basic_auth`, sending the literal unresolved placeholder text.
  Measured 2026-08-25 against a real proxy with an echo upstream: the
  upstream received `X-Forwarded-User='{http.auth.user.id}'` — non-empty, so
  without the guard it would read as a signed-in staff user and any
  `X-API-Key` value would buy full staff access. `trusted_user()`
  (`web/access.py:90-106`) rejects any value containing `{` or `}` as the
  second layer, independent of what the Caddyfile does.
- **A bare partial rendered outside an HTMX swap is dead UI, not a smaller
  page.** `/import/{job_id}/preview` renders `_import_preview.html` or
  `_vendor_preview.html` only when `request.headers.get("HX-Request")` is
  set (`web/app.py:3305-3313`); otherwise it wraps the same partial in
  `import_preview_page.html`. Measured against the running app 2026-08-26: a
  bare partial still renders its `hx-*` attributes as inert markup (`htmx.js`
  is loaded by `base.html`, not by the fragment), and the Apply button
  silently enqueues nothing. Not every partial route re-checks this —
  `/staging/docs` and `/staging/suggestions` (`staging_docs` and `staging_suggestions` in `web/app.py`) always
  return the bare partial, because they are only ever used as HTMX
  pagination targets embedded inside `staging.html`, never linked to
  directly.
- **The `_result.html` pattern**: every enqueuing POST route returns
  `templates.TemplateResponse(request, "_result.html", ctx)`
  (`web/templates/_result.html`), which branches on which of `error` /
  `confirm_needed` / `receipt` / `saved` / `job_id` the context carries. A
  `None` `job_id` means the dedupe key already had an active job, not a
  failure. Since slice P7a (2026-09-14) the receipt is a SENTENCE: a route
  sets `ctx["receipt"] = words.receipt(<what will happen>, jid)`, which
  substitutes "Already in progress." when the enqueue deduped, and passes
  `job_id` / `dedupe_key` alongside it — the template files those under a
  collapsed "Technical details" instead of printing "Enqueued job #123".
  Every receipt is in the future tense, because the work is queued and not
  done.
- **Errors have three shapes, decided in one place.** `_error_response` picks
  the body; two handlers feed it. One is registered on Starlette's
  `HTTPException` (Starlette's, not FastAPI's: the router raises that class for
  an unmatched path and FastAPI's is a subclass, so one handler catches both).
  The other is registered on `Exception`, so a genuine 500 gets the same page
  instead of Starlette's bare `Internal Server Error` text — the case an office
  reader needs it most. `/api/*`, `/static/*`, and any caller
  `access.classify` does not call `staff`, keep the JSON body — the webshop
  backend and the BC item-card link parse it, and a missing stylesheet is a
  fetch, not a navigation. An `HX-Request` gets the `_result.html` error
  partial, boosted requests included, because `base.html` cancels the swap on a
  non-2xx and lifts its banner text out of `.result.error`. Everything else
  gets `error.html`, which extends `base.html` and so carries the menu and a
  **Go to Today** link. The words per status are `ERROR_WORDS` /
  `_error_words`; `_wants_html` checks `text/html` before `*/*`, so
  `application/json, text/plain, */*` is answered as JSON. The exception's own
  headers ride onto whichever body is chosen (a 405 keeps `Allow`), a status
  that may not carry one gets none (`is_body_allowed_for_status_code`), and the
  status code is never rewritten.
  The 500 handler has two rules of its own: the detail it passes on is the
  fixed `INTERNAL_ERROR_DETAIL`, never `str(exc)`, so a stack trace or a
  connection string cannot reach a page (the real exception is logged with its
  traceback); and if rendering the page fails too there is no second handler,
  so it falls back to plain text. `ServerErrorMiddleware` re-raises after the
  handler returns, so uvicorn still logs the 500 and a `TestClient` with
  `raise_server_exceptions=True` still raises.
- **`web/item_link.py` local-imports from `web/app.py` inside a route
  function** (`web/item_link.py:49-54`) specifically to avoid a module-level
  import cycle: `web/app.py` imports `item_link` at module scope, so
  `item_link` importing back from `app` at module scope would deadlock the
  import.
- **The web image cannot import `app.extract.tiers`.** `Dockerfile.web`
  installs `.[web]`, deliberately not `.[extract]` (no PyMuPDF). On
  2026-08-27, `app.playbooks._parse` grew `from app.extract.tiers import
  TARGET`, which chains to `app.extract.pdf` and `import pymupdf` — in the
  web container that raised `ModuleNotFoundError` for every playbook row,
  `/playbooks` rendered "38 playbook file(s) failed to parse" with no
  reachable editor. `TARGET` now lives alone in the dependency-free
  `app/extract/target.py` (imports nothing), re-exported by
  `app/extract/tiers.py:29` for the extractor's own callers.
  `tests/test_web_import_closure.py` runs the web import graph in a
  subprocess with the extract-only dependencies blocked so this can't
  regress silently again.

## Tests

| File | Pins |
|---|---|
| `tests/test_web.py` | `web/app.py` + `web/registry.py` end to end, against the real `dentalia_api` role; includes the Invariant-1 grant-enforcement test |
| `tests/test_access.py` | `web/access.py` — table-driven over the four caller classes |
| `tests/test_web_failed_split.py` | `web/failures.py` and `/dead`: the classifier table from the live error shapes, "retried work stops counting", the two office buttons, operator-only re-runs, `is_operator` |
| `tests/test_web_words.py` | `web/words.py` and slice P7a: the § 9 word list row by row, `doc_display_name`'s three fallbacks, the document-type subject forms, receipts with no job number, the HTML/JSON/HTMX error split, the primary button colour, `week_label` |
| `tests/test_web_vocab_registry.py` | Slice P7b, asserted against the RENDERED page rather than the template: no status pill on any of the thirteen registry screens carries a stored value, every one of them opens with exactly one sentence, none prints a microsecond timestamp or a raw ISO week key, and the catalogue tag, model ids and the internal source keys (match bases, alias sources, audit events) appear only inside a `<details class="technical">` -- and each of those is asserted PRESENT somewhere too, or a needle could be deleted with both halves still green. `RETIRED_WORDS` is the other direction: no page may still say `staged`, `sweep`, `swept` or `production` in its visible text, because a page rendering "Waiting for review" in a pill and "Staged" in a heading three lines down has added a vocabulary rather than adopted one. Also pins the contrast by RECOMPUTING it from `style.css`: `_colour_pairs` walks every rule, resolves `var(--x)` through `:root` and falls back to the white page ground, and nothing whose foreground OR background is a mint may sit below 4.5:1 -- the earlier one-pattern grep could not see white text on a mint BACKGROUND, which is how `.sidebar nav a.active` sat at 1.92:1 |
| `tests/test_web_missing.py` | `web/missing.py`, `/missing` and the Upload page opened for a task: only open dead ends, the pager, the item-number and "Searched …" wording, the chips, the four actions, `missing_summary` |
| `tests/test_catalogue_api.py` | `web/catalogue.py` |
| `tests/test_item_docs.py` | `web/item_docs.py` — the shared query and its `full`/`customer` view projection |
| `tests/test_item_link.py` | `web/item_link.py` — BC link JSON/HTML negotiation, the ZIP route |
| `tests/test_scheduler_web.py` | `web/scheduler_view.py` |
| `tests/test_drafts_web.py` | draft edit/status routes in `web/app.py` |
| `tests/test_web_import_closure.py` | the slim image's import closure — `.[web]` never drags in `.[extract]` |
