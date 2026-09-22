# BC import upload (items + manufacturers) via the web UI — design spec

- **Date:** 2026-08-25
- **Status:** approved by Denis 2026-08-25; all open questions resolved → implementation plan
- **Owner:** agent (brainstormed with Denis)
- **Normative change:** yes — widens `ingest.run`'s `source` enum (PRD v3 §1) and adds one job type to the closed enum (PRD v3 §0). This spec is the PRD-change proposal.

## Problem

BC data enters the pipeline only through a file that is already on the server. `/ingest` ([web/app.py:2785](../../../web/app.py#L2785)) offers a dropdown built by `_list_import_files(cfg.imports_dir)` plus a free-text path — both name a file under `/imports`, which is bind-mounted **read-only** into `web` ([docker-compose.yml:238](../../../docker-compose.yml#L238)) and `worker` ([:153](../../../docker-compose.yml#L153)). Putting a newer `Artikli 3.7.2026.xlsx` or `Proizvajalci.xlsx` into the system therefore requires filesystem access to the host, out of band from the UI.

The manufacturer file is worse off: it has no pipeline path at all. `vendor_master` is populated only by `python -m app.cli vendor-master --apply` ([app/cli.py:388](../../../app/cli.py#L388)).

We do not yet know what BC will expose for automation, or in what form. Until that settles, an operator holding a newer export — full, or trimmed to changed rows — needs to upload it, see what it would change, and confirm.

**The existing `/upload` does not serve this.** It rejects non-PDF at [web/app.py:2893](../../../web/app.py#L2893); `upload.ingest` treats the bytes as a compliance document (sha256 → `fetch_log` → `store.put(..., archive_path(..., "application/pdf"))` → emit `extract.doc`); and `upload_inbox` is document-shaped (`target_group_id`, `catalogue NOT NULL`, row deleted on success). Its **transport** — bytea spool plus enqueue — is exactly right and is reused here. Its **destination** is wrong. This is a sibling of `upload.ingest`, not a widening of it.

## Decisions (locked in brainstorming, 2026-08-25)

1. **Preview then confirm, both file kinds.** Upload runs a job that writes nothing and reports the diff; the operator confirms; a second job writes. Mirrors the posture `vendor-master` already takes on the CLI (dry-run by default, renames refused without `--allow-renames`).
2. **Items reuse `ingest.run`; only vendors get a new job type.** `source: "upload"` joins the `csv | bc_odata` enum, with `ref: {upload_id}` and a `dry_run` payload flag. Vendors get `vendor.import`. Contract cost: one enum value, one PRD §1 edit.
3. **New `import_inbox` table** for the spool, not a `kind` column on `upload_inbox`. Preview reads and keeps the row; apply reads and deletes it.
4. **Two slices.** A = items (ships and is usable alone). B = vendors (follows A's pattern).
5. **Full suite before each slice's commit.** Both slices touch `migrations/`.

## Why this shape

The web role `dentalia_api` holds `GRANT SELECT ON ALL TABLES` plus `GRANT INSERT, UPDATE ON job` and nothing else ([007_roles_grants.sql:35-41](../../../migrations/007_roles_grants.sql)); a table created after 007 gets no grant at all until one is written. So parsing, diffing and upserting **must** run in a worker. The web's job is: accept the file, stash it where a worker can read it, enqueue. Invariant 1 forcing the boundary, not a limitation to fight — the same reasoning that produced `upload_inbox`.

**The preview needs no new storage.** Every handler returns a report into `job.result` (jsonb, migration 019), `dentalia_api` can read it (`/jobs/{id}` does `SELECT * FROM job`), and `/ingest` already renders `_recent_runs` from it. So "show me the diff" is: poll the preview job's result. And `Result.sample()` ([app/results.py:73](../../../app/results.py#L73)) already caps its bucket at `SAMPLE_CAP` while keeping counts exact and emitting `{key}_sampled_of` — a 16k-row diff preview costs exact counts plus a bounded sample, with truncation never silent. Nothing to build for either.

**A delta file already works.** `ingest.run` diffs against `item_mirror` and never deletes rows absent from the export, so "only changed items" is structurally indistinguishable from a full export. This spec adds no logic for that case; it is already the behaviour.

**Separate spool table, not a `kind` column.** `upload.ingest` does a bare `SELECT ... FROM upload_inbox WHERE id=%s` and treats a missing row as "already processed" — a clean idempotency contract that a two-phase (preview keeps / apply deletes) lifecycle would make ambiguous for a second consumer sharing the table. A wrong-kind row reaching `upload.ingest` would archive an xlsx as a compliance document.

## Architecture

```
/import  (web, producer-only)
   │  INSERT import_inbox {kind, filename, content, catalogue, uploaded_by}
   │  enqueue PREVIEW job, payload {..., dry_run: true}, priority=interactive
   ▼
PREVIEW job (worker)                      ← writes NOTHING
   │  read spool row (kept)
   │  parse → diff → report counts + capped samples into job.result
   │  opportunistic GC of spool rows older than SPOOL_RETENTION_DAYS
   ▼
/import/{job_id}/preview  (HTMX poll)
   │  render added / changed / renamed / disappeared / unchanged
   │  [ ] allow renames        (vendors only)
   │  → POST /import/{job_id}/apply
   ▼
APPLY job (worker)                        ← writes
   │  read spool row → re-diff → upsert → emit resolve.group (items only)
   │  DELETE the spool row
   ▼
job.result: applied counts, shown beside the previewed counts
```

Two jobs, two immutable payloads. No payload mutation (invariant 9); the operator's `allow_renames` decision rides in the apply job's own payload.

## Contract changes

### `ingest.run` — PRD §1 payload (slice A)

```
{source: "csv" | "bc_odata" | "upload", ref: file_path | delta_window | {upload_id}, catalogue, dry_run?: bool}
```

`make_source_adapter` ([app/adapters/source.py:255](../../../app/adapters/source.py#L255)) gains an `upload` branch reading the spool bytes through `io.BytesIO`. Verified 2026-08-25 against pandas 3.0.5: `read_excel(BytesIO(...), dtype=str)` reads both delivered files and preserves leading zeros (`'000'` stays a string).

This is slightly more than an added branch. `CsvExportAdapter._frame()` derives its file-type branch from `self.path.suffix` and its schema-drift error from `self.path.name`, so `_frame()` must be refactored to take the reader source and a filename explicitly rather than deriving both from a path. The **options must carry verbatim** — the items adapter reads with `dtype=str, keep_default_na=False` (blank cells arrive as `''`), and that pairing is what protects the REF-gate key from a coerced leading zero. Both adapters must still produce byte-identical downstream payloads (invariant 11): nothing after INGEST learns the bytes came from a spool rather than a path.

### `vendor.import` — new job type (slice B)

```
{upload_id, dry_run: bool, allow_renames: bool}
```

`ALTER TYPE job_type ADD VALUE 'vendor.import'` runs inside the migration's transaction — [db.py:84](../../../app/db.py#L84) wraps each file, and PostgreSQL forbids only *using* a new value in the same transaction, which the migration does not. Confirmed empirically 2026-08-25 rather than assumed: the live database is PG 16.14 and `schema_migrations` records both `013_scheduler.sql` and `014_upload.sql`, with `report.weekly` and `upload.ingest` present in `pg_enum`.

### dedupe_key conventions — PRD §0 table

| tag | producer | dedupe_key |
|---|---|---|
| `ingest.run` | web UI (import upload, preview) | `ingest.run:upload:{upload_id}:preview` |
| `ingest.run` | web UI (import upload, apply) | `ingest.run:upload:{upload_id}:apply` |
| `vendor.import` | web UI (import upload) | `vendor.import:{upload_id}:{preview\|apply}` |

Keyed on `upload_id`, so a double-clicked Apply dedupes while a fresh upload of the same file gets a fresh row and a fresh key. C2 active-scope dedupe means a terminal preview never blocks its own apply.

## Data model — migration 040 (slice A)

```sql
CREATE TABLE import_inbox (
  id           bigserial PRIMARY KEY,
  kind         text NOT NULL CHECK (kind IN ('items','vendors')),
  filename     text NOT NULL,
  content      bytea NOT NULL,
  catalogue    text,               -- items: always 'LJ' (one BC). vendors: null
  uploaded_by  text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

GRANT INSERT ON import_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE import_inbox_id_seq TO dentalia_api;
```

INSERT-only, matching `upload_inbox`'s posture: the producer spools and never reads the spool back. The preview page needs the filename, which rides in the **job payload** (readable via the already-granted `SELECT` on `job`), so no SELECT grant is needed here. The handlers read and delete the spool as the owning `dentalia` role, which these grants do not constrain. Same `RETURNING`-is-unavailable consequence as `upload_inbox`: the row id comes from `currval('import_inbox_id_seq')`, which needs only the granted sequence USAGE.

`kind` is a CHECK-constrained text rather than an enum: it is a spool-routing discriminator internal to this feature, not a pipeline contract, and CHECK is cheaper to extend than an enum type.

Migration 041 (slice B) is the `ALTER TYPE job_type ADD VALUE 'vendor.import'` alone.

## Handlers

**Items** — changes inside `app/handlers/ingest.py`. `dry_run` guards exactly three write points: the two `conn.execute(_UPSERT, ...)` calls (the non-MD reclassify branch and the main branch) and the `queue.enqueue("resolve.group", ...)`. Counting, sampling and diffing are untouched, so the preview's numbers are produced by the same code that will produce the apply's.

**Vendors** — new `app/handlers/vendor_import.py`, thin by construction: read the spool row, `vendor_master.read_bytes(...)`, `vendor_master.diff(conn, rows, code_source=...)`, then either report the `Diff` (preview) or call `vendor_master.apply(..., allow_renames=...)` (apply). `app/vendor_master.py` gains `read_bytes(content, filename)` beside `read_file(path)`, both delegating to one parser — the `.xlsx`/`.csv` branch and the `dtype=str` rule (load-bearing: without it `001` reads as integer `1` and stops matching `item_mirror.manufacturer_raw`) live in the shared core. Note the two readers in this repo use **different** pandas conventions and must not be merged: the items adapter passes `keep_default_na=False`, while `vendor_master.read_file` does not and instead absorbs pandas' NaN in `_clean` via a `text.lower() == "nan"` check. `read_bytes` keeps `read_file`'s convention.

Both handlers delete spool rows older than `SPOOL_RETENTION_DAYS` (module constant, 7) on every run. This is opportunistic GC in the worker rather than a scheduler cron because [scheduler.py:2](../../../app/scheduler.py#L2) is explicit that a `_tick_*` enqueues and never otherwise writes — a DELETE tick would violate the one rule that module has. Config key is the upgrade path if the constant ever needs tuning.

## The four traps

**Anomaly double-write.** [runner.py:83](../../../app/workers/runner.py#L83) calls `record_anomalies` whenever `result["anomalies"]` is non-empty. A dry-run returning anomalies would write the standing `data_anomaly` ledger for a run that changed nothing, and the apply would then count the same observations again. **Fix:** in dry-run, the report carries them under `anomalies_preview` and leaves `anomalies` empty. Silent and permanent if missed — this is the single highest-value test in slice A.

**TOCTOU between preview and apply.** `item_mirror` / `vendor_master` can move between the two jobs (another ingest, a CLI run). Both handlers re-diff before writing, so the *write* is always correct against current state; what can go stale is the number the operator looked at. **Fix:** the apply reports its own counts, and the preview screen renders previewed-vs-applied side by side rather than assuming they match.

**`RenameRefused` must not surface as a dead job.** `vendor_master.apply` raises and writes nothing when a code would be re-pointed without `allow_renames` — correct, because `item_group.canonical_manufacturer` is effectively write-once (RESOLVE short-circuits on `_existing_link`), so a re-point after grouping splits a manufacturer with no repair path. **Fix:** the preview screen lists every rename explicitly and the Apply button carries an `allow renames` checkbox; the apply payload records the operator's answer. A refusal that still reaches the handler is a genuine race and dead-letters normally.

**`playbooks sync` ordering.** The runbook is explicit ([runbook.md:142](../../../docs/runbook.md#L142)): run `sync` *before* an ingest, because re-pointing an alias does not retro-fix `item_group` rows already resolved under the old value. "Upload vendors, then upload items" is precisely the order that manufactures orphaned groups. **Fix:** the vendor apply reports its new-code count and the UI states the ordering rule. Running `sync` from the UI is out of scope — it is a write the producer role cannot make, and it would need its own job type.

## Web — `/import`

One page, two forms (Items | Manufacturers), following `upload.html`'s HTMX shape.

| Route | Method | Does |
|---|---|---|
| `/import` | GET | the form, plus recent import runs from `job.result` |
| `/import` | POST | validate kind/catalogue/size → INSERT spool → enqueue preview → return the poll target |
| `/import/{job_id}/preview` | GET | render the preview job's result, or "still running" |
| `/import/{job_id}/apply` | POST | read the preview job's payload → enqueue the apply job |

Validation mirrors `/upload`: extension must be one of `.xlsx`/`.xlsm`/`.xls`/`.csv` — the exact set `CsvExportAdapter._frame()` accepts, so the web never rejects a file the adapter would have read, nor accepts one it would raise on, size against `cfg.upload_max_mb` (25; the current `Artikli 3.7.2026.xlsx` is 2.2 MB over 19,091 rows × 25 columns, measured 2026-08-25 — so roughly 11.5 MB at the 100k-item horizon, still inside the limit). `uploaded_by` from `_authenticated_user(request)`.

**No catalogue picker, and no `code_source` picker.** There is one Business Central, one article numbering, one pipeline — the Zagreb operation adds items, not a second integration (Denis, 2026-08-19, closing PHASES.md G17/G11; stated in CLAUDE.md). `/import` sets `catalogue` from the existing `scheduler.ingest_catalogue` config key (default `"LJ"`, already a single value rather than a list) and `vendor.import` uses `vendor_master.DEFAULT_CODE_SOURCE`. The CLI keeps its `--source` flag as the escape hatch if a second issuer ever appears; the UI does not invent a choice that does not exist.

**Pre-existing drift, adjacent but out of this scope:** `CATALOGUES = ["LJ", "ZG"]` ([web/app.py:116](../../../web/app.py#L116)) still renders a two-option picker on `/ingest` and `/upload`, and two comments still name ZG ([002_ingest.sql:21](../../../migrations/002_ingest.sql#L21), [source.py:138](../../../app/adapters/source.py#L138)). `/import` simply does not use `CATALOGUES`. Correcting the other two screens touches routes this spec does not otherwise open, so it is logged in `tasks/followups.md` rather than folded in here.

`/ingest` is left alone. It keeps serving server-side paths and the `bc_odata` stub; `/import` is the browser-upload sibling.

## Invariant check

| Invariant | Holds because |
|---|---|
| 1 — only GATE writes registry | Neither handler touches `document` / `item_document` / `evidence`. `item_mirror` and `vendor_master` are mirrors, not the registry. |
| 7 — closed job-type enum | `vendor.import` lands as a migration + PRD change first, never a runtime string. `noop.py`'s `JOB_TYPES` tuple is updated in the same slice. |
| 9 — payloads immutable | Preview and apply are two jobs with two payloads. Nothing is mutated. |
| 11 — no adapter leakage | `source: "upload"` feeds the same `normalize_record` core; downstream payloads are unchanged. |
| 12 — LLM use | None. Both handlers are deterministic parse + diff. |
| CLAUDE.md — skips counted | Reuses `Result`; the existing `skipped` / `missing_mfr_ref` / `mfr_ref_prose` counters carry through both phases. |

## Testing (pytest, real Postgres, table-driven)

Slice A: dry-run writes nothing (assert `item_mirror` and `job` row counts unchanged); dry-run returns empty `anomalies` and populated `anomalies_preview`, and `record_anomalies` therefore does not fire; apply after preview produces the counts the preview predicted on an unchanged mirror; apply deletes the spool row and a re-run is a no-op; a delta file (subset of rows) leaves absent items untouched; upload branch and csv branch produce identical `NormalizedRow`s from the same bytes; `/import` rejects a PDF, an oversized file, and an unknown catalogue; GC drops rows past retention and spares fresh ones.

Slice B: `read_bytes` and `read_file` agree on the same file; preview reports added/renamed/disappeared/unchanged without writing; apply without `allow_renames` on a renaming file refuses and writes nothing; apply with it writes; `dtype=str` preserved through the bytes path (`001` stays `"001"`).

Full suite before each slice's commit — both touch `migrations/`.

## Normative + documentation impact (same session as each slice)

Slice A: PRD v3 §0 dedupe table (2 rows) and §1 payload/adapter rows; `docs/dentalia-schema-sketch.md` (`import_inbox` + access matrix); `docs/runbook.md` and `docs/code-map.md` (the UI import path beside the CLI).

Slice B: PRD v3 §0 (1 row) and a VENDOR section; `docs/dentalia-job-type-handbook.md` (`vendor.import` pseudo-code); runbook (`vendor-master` CLI and the UI path are two doors to one operation).

## Out of scope (YAGNI)

- `playbooks sync` / `reconcile` from the UI — producer role cannot write `manufacturer_alias`; needs its own job type. The ordering rule is surfaced as text instead.
- Any webhook or inbound API endpoint. `BcApiAdapter` stays a stub. Noted below as the intended successor.
- Archiving the uploaded xlsx. Provenance is the filename, carried into `vendor_master.import_batch` and the job payload.
- A per-row changed-items table. Capped samples in `job.result` are the record.
- Deleting `item_mirror` rows absent from an upload. Ingest has never done this and a delta upload makes it actively wrong.

## Relationship to the eventual API/webhook path

Less speculative than it looks. `source: "bc_odata"` is already normative (PRD §1), `/ingest` already builds that payload with `company` + `delta_since`, and `LJ_ODATA_PROFILE` already maps the OData property names — including a `UDI` column the CSV export lacks. What is missing is the HTTP client inside `BcApiAdapter`, which raises `NotImplementedError` and accepts injected `records` for tests.

A webhook would land bytes exactly where this spec lands them — spool, then enqueue — because the producer-role constraint is the same. So the transport built here is the transport a webhook would reuse; only the trigger differs.

## Open questions for the plan — all resolved 2026-08-25

1. ~~Preview freshness~~ — **decided 2026-08-25: no expiry.** The apply re-diffs against current state before writing, so the write is always correct; only the number the operator looked at can go stale. `/import` renders previewed and applied counts side by side and lets the drift be visible. No window, no forced re-preview.
2. ~~`SPOOL_RETENTION_DAYS` as constant or config key~~ — **decided 2026-08-25: module constant, 7 days.** It is a GC window for abandoned uploads, not per-environment policy. Keeps slice A off `app/config.py`; a one-line move if it ever needs tuning.
3. ~~Whether slice A ships the Manufacturers form disabled or omitted~~ — **decided 2026-08-25: omitted until slice B.** No dead controls; the page grows its second section in B.
