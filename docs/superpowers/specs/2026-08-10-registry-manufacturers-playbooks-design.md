# Manufacturers + playbooks registry views (web UI) — design spec

Date: 2026-08-10
Status: approved in brainstorming, ready to plan
Scope: read-only web UI addition. No PRD change, no migration, no grant change.

## Problem

The producer UI has no way to see two things that operators reason about daily:

1. **Manufacturers themselves.** The catalogue resolves to 386 canonical
   entities across 389 BC codes, and none of them are visible anywhere in the
   UI. `/items` shows items; nothing rolls up to the entity that owns them.
2. **Playbooks.** `playbooks/{slug}.json` is the authored home for per-
   manufacturer identity, doc sources, and T0 parse templates. The only way to
   see one is to open the file; the only way to check one is
   `dentalia playbooks validate|reconcile` from a shell.

The consequence is that the open question in `PHASES.md:188` — who authors the
~20 playbook files, and which manufacturers should get one first — has no
answer visible in the system. The data to answer it already exists; it is just
not joined and not rendered.

## Evidence base (verified 2026-08-10 against the live compose DB)

| Fact | Value |
|---|---|
| `manufacturer_alias` rows | 389 (382 `vendor-master`, 7 `playbook`) |
| distinct `canonical_name` | 386 |
| entities spanning >1 BC code | 2 — IVOCLAR (001/005/275), CEFLA (10015/CEFLA) |
| `vendor_master` rows | 390 |
| `manufacturer` table rows | 0 — declared in migration 006, deliberately empty, assigned to S2.4 |
| `item_mirror` rows | 0 — was 322; `tools/cold_start_reset.sql` truncated 23 tables on 2026-08-10 (corpus cold-start session) |
| `document` rows | 0 — same wipe; the 19 were dev-run residue |
| playbook files | 5 (`dentsply-sirona`, `gc`, `ivoclar`, `komet`, `voco`) |
| `dentalia_api` grants | SELECT everywhere, plus INSERT/UPDATE on `job`, INSERT on `upload_inbox` |
| `app.playbooks` importable from web image | yes (`PLAYBOOKS_DIR` resolves to `/app/playbooks`) |
| `/app/playbooks` present in web image | **no** — `Dockerfile.web` copies only `app/` and `web/` |
| `job_type` enum contains a playbook-apply tag | **no** — it has `playbook.reonboard`, nothing that runs `sync` |

## Decisions (locked in brainstorming)

1. **Read-only.** Editing playbooks from the UI is out of scope. Applying an
   edit means writing `manufacturer_alias`, which `dentalia_api` cannot do; the
   correct route is a new `playbook.sync` job type, and Invariant 7 makes that
   a PRD change plus migration, never an in-task decision. Deferred to its own
   slice.
2. **Entity is the unit, not the BC code.** `playbooks reconcile` is already
   entity-grouped for exactly this reason (001/005/275 are one IVOCLAR). The
   list shows one row per `canonical_name` with its codes collapsed into it.
3. **The manufacturers page is a playbook-coverage triage tool.** Default sort
   is item count descending, playbook-less first, so the page answers "which
   manufacturer earns a playbook next" without the operator sorting anything.
4. **Built as one slice**, overriding the >3-files working rule — Denis's
   explicit call on 2026-08-10.

## 1. Data

All reads. One aggregate query backs the list page:

```sql
SELECT a.canonical_name,
       array_agg(DISTINCT a.raw_name)   AS codes,
       array_agg(DISTINCT a.source)     AS sources,
       count(DISTINCT i.item_ref)       AS items,
       count(DISTINCT ip.doc_id)        AS docs
FROM manufacturer_alias a
LEFT JOIN item_mirror i               ON i.manufacturer_raw = a.raw_name
LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref
GROUP BY a.canonical_name
```

`vendor_master.name` is joined on `code = raw_name` for the per-code display
name on the detail page. `item_document_production` is the granted view, so
document counts are production-visibility only — consistent with the rest of
the read API.

Playbook presence is **not** a DB fact. It comes from
`app.playbooks.load_playbooks()`, matched to an entity by `manufacturer ==
canonical_name` first and by `bc_codes` overlap second — the same entity-level
union `playbooks sync` uses, so the UI and the CLI cannot disagree about which
entity a playbook covers.

## 2. Routes

| Route | Renders |
|---|---|
| `GET /manufacturers` | 386 entities. Columns: entity, codes, items, docs, alias source, playbook (yes / gap). Default sort items desc with playbook-less first. Text search over name and code; a "only without playbook" toggle. |
| `GET /manufacturers/{canonical_name}` | Per-code table (code, vendor-master name, alias source), item list linking to `/items/{item_ref}`, production documents linking to `/documents/{doc_id}`, and the linked playbook if one exists. |
| `GET /playbooks` | The authored files: slug, manufacturer, BC codes, domains, doc-source count, validate status. |
| `GET /playbooks/{slug}` | Identity block, `doc_sources` table, `match` anchors, `ref_strategy`; raw JSON in a collapsed `<details>`; any `validate()` conflict shown inline; a line naming the CLI command that edits it. 404 on unknown slug. |

Every route is GET. No form, no POST, no enqueue.

## 3. Code placement

`web/app.py` is 1104 lines. These routes go in a new **`web/registry.py`**
exposing an `APIRouter`, included by `web/app.py` in one line. It reuses the
existing DB-connection helper and Jinja environment.

The module imports `app.playbooks`. That is consistent with the producer-only
boundary asserted in the `web/app.py` docstring: the boundary forbids
`app.handlers` and `app.workers`, and `app.playbooks` is a pure data-loading
module with no DB writes and no queue access. `web/app.py` already imports
`app.queue` on the same reasoning.

`load_playbooks()` never raises by contract (the runtime path); `validate()`
does. The UI calls `load_playbooks()` for rendering and `validate()` inside a
try/except purely to surface conflicts as page content — a broken playbook must
render as a visible error row, never a 500.

## 4. Compose

`playbooks/` is absent from the web image. Add a read-only bind mount to the
`web` service next to the existing `imports` mount:

```yaml
- ./playbooks:/app/playbooks:ro
```

A bind mount rather than a `Dockerfile.web` COPY, so playbook edits appear
without an image rebuild. Read-only enforces decision 1 at the filesystem
level: even a bug cannot write a playbook from the web container.

## 5. What stays out

- Editing, creating, or deleting playbooks.
- Running `validate` / `reconcile` / `sync` as a job.
- Populating the `manufacturer` table (S2.4 owns it, with contacts).
- `playbook.reonboard` triggering (S2.1).
- Any change to `dentalia_api` grants.

## 6. Testing

Added to `tests/test_web.py`, which already connects as the write-less
`dentalia_api` role against a real Postgres:

1. `/manufacturers` returns 200 and renders one row for IVOCLAR, not three —
   the entity-grouping assertion.
2. The "without playbook" filter excludes the 5 entities that have one.
3. Item and document counts on a seeded entity match a direct SQL count.
4. `/manufacturers/{name}` 200s for a real entity, 404s for an unknown one.
5. `/playbooks` lists all files found in the fixture directory.
6. `/playbooks/{slug}` renders a known playbook; unknown slug 404s.
7. A deliberately malformed playbook renders an inline error, not a 500.

## 7. Edge cases carried into implementation

- **Entity names in URLs.** `canonical_name` can contain spaces, `/`, `&`
  (e.g. "3M ESPE"). The detail route must handle path-encoding; if any name
  proves hostile to path segments, fall back to a query parameter rather than
  inventing a slug that would then disagree with the playbook slug.
- **Aliases with no vendor-master row.** 389 aliases vs 390 vendor_master rows —
  the join is not 1:1 in either direction. Both orphan directions render, never
  drop silently (CLAUDE.md: skipped rows are counted and reported).
- **An entity with mixed alias sources** (some codes `vendor-master`, some
  `playbook`) shows both, not the first one.
- **A playbook claiming a BC code no alias knows.** `reconcile` treats
  `unknown-code` as blocking; the viewer surfaces it rather than hiding the
  playbook.
- **Zero-item entities.** Most entities have no items and must render as 0, not
  be filtered out. Post-wipe this is *every* entity: all 386 show 0 items and 0
  docs until the cold-start re-ingest lands, so the triage sort has nothing to
  rank by. That is a data state, not a bug — the page must stay readable and
  correctly ordered (name asc as the tiebreak) when every count is zero, and
  that empty state is worth a test of its own.

## 8. Docs to update in the same session

- `docs/code-map.md` — add `web/registry.py`.
- `docs/runbook.md` — add the two route groups to the UI section, and record
  that `docker compose up -d` does **not** rebuild a changed `web/`; use
  `--build`. Found the hard way on 2026-08-10: a stale `dentalia-web:latest`
  from 2026-08-06 served 404s on `/items`, `/documents`, `/data-quality` and
  `/expiry` while reporting healthy.

Both of these files are also touched by the `corpus-cold-start`
branch (1 line each). Keep edits here surgical and section-local so the two
branches merge without a conflict.

## 9. Coordination (concurrent session, 2026-08-10)

This slice is built in the `registry-views` worktree, never in the shared
checkout — a commit made from the shared checkout landed on another session's
branch on 2026-08-10 and had to be reset out of it.

**Do not run the test suite without checking first.** `tests/conftest.py`
hardcodes `TEST_DB = "dentalia_test"` and opens with
`DROP DATABASE ... WITH (FORCE)` + `CREATE`, so the second run to start kills
the first's connections mid-run and both report phantom failures. Overriding
`DENTALIA_TEST_URL` does not help — the DROP/CREATE uses the constant, so the
override only desynchronises the target. Tracked as `[test-db-collision]` in
`tasks/followups.md`. Note also that `docker compose run` leaves its container
alive when the invoking CLI is killed; verify with `docker ps` and
`pg_stat_activity`, not by assuming the run died with the shell.

## 10. Open items

- The playbook **edit** loop remains unbuilt and unscoped. It needs a
  `playbook.sync` job type (PRD v3 amendment + `ALTER TYPE job_type` migration
  + handler) before any UI work is worth doing. Not blocking this slice.
- Manual verification of the item/doc count columns has to wait for the
  cold-start re-ingest; until then every entity legitimately reads 0. Tests are
  unaffected — they seed their own fixture rows.
