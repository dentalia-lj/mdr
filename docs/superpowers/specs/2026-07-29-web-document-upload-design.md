# Web document upload → `upload.ingest` — design spec

- **Date:** 2026-07-29
- **Status:** approved design, pending spec review → implementation plan
- **Owner:** agent (brainstormed with Denis)
- **Normative change:** yes — adds one job type to the closed enum (PRD v3 §8). This spec is the PRD-change proposal.

## Problem

An admin has no way to put a document into the pipeline through the web UI. Today documents enter three ways: automated web fetch (DISCOVER → FETCH), the Phase-2 automated mailbox poll (`email.poll`, unbuilt), and CLI `backfill.scan`. When DISCOVER dead-ends during a sweep — or a manufacturer emails a certificate to a person — the admin already holds the PDF but must drop to a terminal (`app/cli.py enqueue backfill.scan`) to feed it in. The generic `/enqueue` form was removed in S1.6 (G16), so there is no human upload entry at all.

This is needed **now** (dead-end resolution during the S1.7 Ljubljana sweep) and is the manual precursor to the Phase-2 `email.poll` flow (both are "a document was handed to us; enter it at `extract.doc`").

## Decisions (locked in brainstorming)

1. **Purpose: both.** One upload path serves dead-end resolution (target group prefilled) *and* standalone inbound filing (self-identifies via its REF list, like backfill).
2. **Gate: same as any source.** The upload flows extract → validate → gate with no special trust. It is still extracted, so it carries per-value evidence (invariant 2); the admin never hand-keys fields.
3. **Mechanism: new `upload.ingest` job type, DB handoff.** The web inserts a transient `upload_inbox` row (bytes + metadata) and enqueues `upload.ingest`; a worker archives/hashes/emits. The web stays a pure DB producer and never touches a filesystem.

## Why this shape

The web role `dentalia_api` can write **only the `job` table** ([007_roles_grants.sql](../../../migrations/007_roles_grants.sql): `GRANT INSERT, UPDATE ON job`; explicit SELECT-only everywhere else) and mounts `/imports` read-only. So the archive + hash + `fetch_log` write **must** run in a worker — invariant 1 forcing the boundary, not a limitation to fight. The web's whole job is: accept the file, stash it where a worker can read it, enqueue.

`upload.ingest` is **`fetch.url` minus the HTTP fetch.** FETCH's post-download tail already does exactly what upload needs — archive bytes, upsert the ledger, and emit `extract.doc` (new hash) or `validate.doc` (seen hash). Upload reuses that tail.

## Architecture

```
/upload (web, producer-only)
   │  INSERT upload_inbox {bytes, filename, target_group_id?, catalogue, uploaded_by}
   │  queue.enqueue("upload.ingest", {upload_id}, dedupe="upload:{upload_id}", priority=interactive)
   ▼
upload.ingest  (app/handlers/upload.py, worker)
   │  load row → content_hash = sha256(bytes)
   ├─ hash NOT in fetch_log (new):
   │     archive_url = storage.put(bytes, archive_path(hash, filename))
   │     ledger upsert (source='upload')
   │     emit extract.doc {archive_url, content_hash, group_id?, source_url="upload:{filename}"}
   ├─ hash in fetch_log AND already extracted (rev present):
   │     group set → emit validate.doc {content_hash, group_id, extract_rev}   (C3 link-candidate)
   │     no group  → skip (already known, nothing to link)
   └─ hash in fetch_log but never extracted (rev None):
   │     emit extract.doc {…, group_id?}   (extract it, carrying the group)
   │  DELETE upload_inbox row (reclaim bytes)
   ▼
extract → validate → gate     (normal spine)
```

Downstream sees an ordinary `extract.doc` / `validate.doc` — nothing knows it came from an upload (invariant 11). `source_url="upload:{filename}"` is the only tell, mirroring backfill's `source_url` and email.poll's planned `email_ref`.

## Gate behavior (state plainly — no surprise)

- **Group-targeted upload** (dead-end case): the group assertion is `match_basis="fetch-context"`, which invariant 3 / C5 **caps at `staged`** ([validate.py:266](../../../app/handlers/validate.py#L266)). So the admin uploads, then approves once in `/staging` → `gate.apply` writes the registry. This is "same as any source" working, not a bug.
- **Standalone upload** that self-identifies: validate scopes candidate groups by the document's own manufacturer and links on `ref-list` / `basic-udi-di`. If the REF gate independently passes ((manufacturer, mfr_ref) overlap or Basic UDI-DI), it **auto-writes**; otherwise it stages.

## Data model — migration 014

```sql
-- transient spool: holds the uploaded bytes until a worker archives them
CREATE TABLE upload_inbox (
  id              bigserial PRIMARY KEY,
  filename        text        NOT NULL,
  content         bytea       NOT NULL,
  target_group_id bigint,                 -- soft ref (no FK): may be null (standalone)
  catalogue       text        NOT NULL,
  uploaded_by     text,                   -- from the auth header (decided_by shape)
  created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TYPE job_type ADD VALUE 'upload.ingest';   -- closed-enum extension (invariant 7)

GRANT INSERT ON upload_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE upload_inbox_id_seq TO dentalia_api;
-- worker (schema owner) holds SELECT/DELETE; dentalia_api never reads/deletes the spool
```

Notes:
- `bytea` is a **transient** handoff (KB–MB PDFs), deleted after archiving. The permanent home is the StorageAdapter archive, never Postgres.
- `ALTER TYPE ... ADD VALUE` cannot run in the same transaction that uses the new value (PG16) — same constraint migration 013 already handled; the migration adds the value, the handler is registered separately.
- No `status` column — the `job` row tracks lifecycle; the spool row is pure payload.

## Handler — `app/handlers/upload.py`

`handle_upload_ingest(conn, job)`:
1. `upload_id = job["payload"]["upload_id"]`; load the `upload_inbox` row. Missing → fail (already consumed / vanished; not retryable-forever).
2. `content_hash = sha256(row.content)`.
3. Branch on `fetch_log` (hash-seen) and `extraction_attempt` (extracted?) exactly as the topology above — reusing FETCH's helpers.
4. `DELETE FROM upload_inbox WHERE id=%s`.
5. Return `{archived, linked, duplicate, extracted_rev}` counts (never-silent).

Emits (PRD Emits row): `extract.doc`, `validate.doc` — both existing tags.

### Shared helpers (targeted refactor)

FETCH owns the archive/ledger/emit logic upload must not diverge from: `_archive_path`, `_ledger_upsert`, `_emit_extract`, `_emit_validate`, `_hash_extracted_rev`. Duplicating them would let the archive layout or the `validate:{hash}:{rev}:{group}` dedupe key drift between fetch and upload. **Extract these into a shared module** (`app/handlers/_archive.py` or similar) and refactor `fetch.py` to import from it — same rationale as `app/urls.py` being shared by DISCOVER and FETCH so the fetch-dedupe key can't diverge. The plan must treat the fetch.py refactor carefully (its tests must stay green, no behavior change).

## Web — `/upload`

- `GET /upload`: form — file input, catalogue select (`LJ`/`ZG`), **optional** group picker (typeahead over `item_group` by `canonical_manufacturer` / member name), priority (default `interactive`). `uploaded_by` from the forwarded auth header (G3 v0, same source as staging `decided_by`).
- `POST /upload`: validate (see guardrails) → read bytes → `INSERT upload_inbox` → `queue.enqueue("upload.ingest", {"upload_id": id}, f"upload:{id}", priority)` in the same transaction → return the shared `_result` partial with the enqueued job id (same UX as `/ingest`).
- Nav link added to `base.html`.

## `/manual` dead-end integration (the "both" prefill)

`manual_task` already carries `group_id` ([009_manual_task.sql:17](../../../migrations/009_manual_task.sql#L17), soft ref). On a `discovery-dead-end` task, add an **"Upload document"** action that opens `/upload` with `target_group_id` prefilled. A successful upload marks the task resolved (the document is now in flight); if extraction/validation later fails, the normal dead/staging surfaces catch it. Reuses the existing `/manual/{task_id}/resolve` machinery.

## Guardrails & config

- **PDF only** (reject non-`application/pdf` with a clear message).
- **Size cap** `UPLOAD_MAX_MB` (default 25) — reject larger with a clear message.
- Optional `UPLOAD_ENABLED` (default true) to gate the route.

## Invariant check

| # | Invariant | How this respects it |
|---|-----------|----------------------|
| 1 | Only gate handlers write the registry | web writes only `job` + `upload_inbox`; worker writes `fetch_log`/archive; gate writes `document`/`item_document`/`evidence` |
| 2 | Every production value carries full evidence | upload is extracted like any doc; no hand-keyed fields |
| 3 | name-family/fetch-context capped at `staged` | group-targeted upload → `fetch-context` → staged, structurally |
| 7 | Job types are a closed enum | `upload.ingest` added via this PRD change + migration 014 |
| 8 | Dedupe scoped to active jobs | `upload:{upload_id}` unique per upload; content-hash dedupe via `fetch_log` |
| 11 | Nothing downstream knows the adapter | downstream sees an ordinary `extract.doc`/`validate.doc` |

## Normative + documentation impact (same session as the build)

- **PRD v3** (normative): `upload.ingest` in the §8 job-type table (with Emits `extract.doc`/`validate.doc`), the topology diagram (`↘ /upload → upload.ingest` entering at `extract.doc` alongside `backfill.scan`/`email.poll`), and the dedupe-key conventions (`upload:{upload_id}`).
- **schema-sketch**: `upload_inbox` table + the tag→table access matrix (`dentalia_api` INSERT).
- **job-type-handbook**: `upload.ingest` row + handler pseudo-code + payload example.
- **code-map / runbook / architecture**: new `/upload` route, `app/handlers/upload.py`, `UPLOAD_MAX_MB`, migration 014, the shared archive helper.
- **PHASES**: its own slice (usable before the S1.7 sweep; manual precursor to S2.4 `email.poll`).

## Testing (pytest, real Postgres, table-driven)

- **Handler**: new-hash → `extract.doc` emitted + `fetch_log` upserted + archive written + row deleted; seen-hash+extracted+group → `validate.doc {…, extract_rev}`; seen-hash+extracted+no-group → skip; seen-hash+never-extracted → `extract.doc`; missing row → fail.
- **Web**: GET renders; POST inserts the spool row + enqueues (as the INSERT-only role); bad MIME / oversize rejected; dead-end prefill carries `target_group_id`.
- **Grant test**: `dentalia_api` can `INSERT upload_inbox` but cannot write `document`/`item_document`/`evidence`.
- **Shared-helper refactor**: fetch.py's existing tests stay green (no behavior change).

## Out of scope (YAGNI)

- No multi-file / zip upload — one PDF per submit.
- No manual field entry / correction UI — extraction owns fields.
- No new auth roles — reuse G3 v0 forwarded-user.
- Not the Phase-2 `email.poll` mailbox integration — this is its manual sibling only.
- No long-term blob storage in Postgres — `upload_inbox` is transient.

## Open question for the plan

Shared-helper home: extract fetch.py's `_archive_path`/`_ledger_upsert`/`_emit_*`/`_hash_extracted_rev` into a new `app/handlers/_archive.py` (cleanest), or a thinner reuse. Decide during planning; either way upload must not copy-paste them.
