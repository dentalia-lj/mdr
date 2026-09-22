# BC vendor import upload — design (Slice B)

**Date:** 2026-08-26
**Status:** spec, awaiting review
**Predecessor:** `docs/superpowers/specs/2026-08-25-bc-import-upload-design.md` (Slice A, items — shipped)
**Depends on:** the single-catalogue collapse slice (see §9), which lands FIRST

Upload `Proizvajalci.xlsx` through the web UI, preview the diff, confirm, and
have the BC manufacturer master mirrored into `vendor_master` — the browser
counterpart to `python -m app.cli vendor-master --apply`.

---

## 1. Why this is not Slice A again

Slice A reused `ingest.run` because a browser-uploaded item export *is* an
ingest: same adapter, same diff, same `resolve.group` emission. A vendor import
shares none of that. It writes a different table, its diff has four categories
instead of two, one of those categories is refused by default, and it emits no
downstream job at all.

Verified differences, read out of the code rather than recalled:

| | Slice A (items) | Slice B (vendors) |
|---|---|---|
| Job type | reused `ingest.run` | **new `vendor.import`** — enum, so migration first |
| Reader | `UploadExportAdapter` over spooled bytes | `vendor_master.read_file` is **path-only** today |
| Diff shape | changed / unchanged | **added / renamed / disappeared / unchanged** |
| Guarded outcome | none | **`RenameRefused`** unless `allow_renames` |
| Emits | `resolve.group` per changed item | **nothing** |
| Spool | new `import_inbox` table | already supports `kind='vendors'` |

## 2. What already exists (verified 2026-08-26)

- `import_inbox.kind` is `CHECK (kind IN ('items','vendors'))` —
  `migrations/040_import_inbox.sql:26`. **No schema change to the spool.**
- `app/import_spool.read()` returns `kind` alongside the bytes
  (`app/import_spool.py:34`), so a handler can assert it was handed the right
  kind. `read` / `consume` / `gc` are kind-agnostic.
- `app/vendor_master.py` already owns `read_file`, `diff`, `apply`,
  `RenameRefused`, and the `Diff` dataclass with all four categories.
- `apply(..., batch=)` — the CLI defaults it to the filename
  (`app/cli.py:423`), and the Slice A payload already carries `filename`.
- `dentalia_api` holds `SELECT` on `vendor_master` and nothing else
  (`migrations/016_vendor_master.sql:54`). The web role structurally cannot
  write the mirror; the worker does. **No new grants.**
- `ALTER TYPE job_type ADD VALUE` works in a migration transaction on this
  cluster — migrations 013 and 014 both do it and are applied.

## 3. Why renames are guarded

From `app/vendor_master.py`'s own module docstring, and it is the reason this
slice is not a thin wrapper:

> `item_group.canonical_manufacturer` is effectively write-once, because
> RESOLVE's ladder short-circuits on `_existing_link` and never re-derives it
> for an item already in a group. So silently re-pointing a code whose old name
> is already baked into existing groups produces a split-brain that no later
> import can repair.

`apply` raises `RenameRefused` and writes **nothing** when the incoming file
would re-point any code and `allow_renames` is not set. That is an
all-or-nothing gate, not a per-row skip.

## 4. Design

### 4.1 Job type and payload

Migration `041_vendor_import.sql`:

```sql
ALTER TYPE job_type ADD VALUE 'vendor.import';
```

Payload — flat, like `upload.ingest`'s, NOT nested under `ref` (that shape
belongs to `ingest.run`):

```json
{
  "upload_id": 7,
  "filename": "Proizvajalci.xlsx",
  "dry_run": true,
  "allow_renames": false,
  "preview_job_id": 12
}
```

`allow_renames` is present and `false` on the preview; set from the operator's
checkbox on the apply. `preview_job_id` appears on the apply only, exactly as
Slice A does it.

Dedupe keys: `vendor.import:upload:{upload_id}:preview` and
`…:apply`. C2 active-scope dedupe means a terminal preview never blocks its own
apply, while a double-clicked Apply dedupes.

### 4.2 Reading spooled bytes

`vendor_master.read_file` calls `pd.read_excel(path)` / `pd.read_csv(path)`
directly. Slice A hit this for items and solved it by extracting a shared
`_read_frame(source, name)` so "the two readers cannot drift" — recorded as the
reason in `docs/code-map.md`.

**Do the same thing, not a second copy of it.** Promote
`app.adapters.source._read_frame` to a public `read_frame(source, name)` and
have `vendor_master` use it for both entry points:

- `read_file(path)` — unchanged signature, CLI keeps working
- `read_bytes(content, filename)` — new, for the handler

`read_frame` passes `keep_default_na=False`, which `read_file` does not today.
Behaviour is preserved because `vendor_master._clean` already treats both the
string `"nan"` and the empty string as absent — but that equivalence is
asserted by a test, not assumed.

### 4.3 Handler

`app/handlers/vendor_import.py`, registered as `vendor.import`:

1. `spool = import_spool.read(conn, payload["upload_id"])`
2. assert `spool["kind"] == "vendors"` — a wrong-kind row is a dead job with an
   actionable message, never a silent zero-row import
3. `rows = vendor_master.read_bytes(spool["content"], spool["filename"])`
4. `d = vendor_master.diff(conn, rows)`
5. build the report (§4.4)
6. if `dry_run`: return. **No write, no consume** — the apply still needs those
   bytes. This is why `import_inbox` is two-phase.
7. else: `vendor_master.apply(conn, rows, batch=payload["filename"], allow_renames=…)`,
   then `import_spool.consume`, then `import_spool.gc`

**`RenameRefused` is caught, not raised.** It is an expected outcome — the
operator previewed a clean diff, someone applied a different vendor file in
between, and the rename appeared. Letting it propagate dead-letters the job and
buries the reason. The handler returns `outcome: "rename-refused"` with the
offending codes and `written: false`, and does not consume the spool so the
operator can re-apply with the box ticked.

**No `anomalies` key.** `app/workers/runner.py:83` gates on
`if result.get("anomalies"):` — a vendor import produces none, so the key is
absent rather than `[]`.

**Emits nothing**, and that is deliberate: see §5.

### 4.4 Report shape

```json
{
  "seen": 390,
  "added": 4,
  "renamed": [["001", "OLD NAME", "NEW NAME"]],
  "disappeared": ["277"],
  "unchanged": 385,
  "dry_run": true,
  "written": false,
  "allow_renames": false
}
```

`renamed` carries the triples, not just a count — the whole point of the
preview is that the operator reads which codes move and to what.
`disappeared` carries the codes for the same reason.

### 4.5 Web

One `/import` page, two upload sections — **Item catalogue** and
**Manufacturer master**. Both spool into `import_inbox` with the matching
`kind`, both land on `/import/{job_id}/preview`.

`import_preview` dispatches on `job["type"]`: `ingest.run` renders
`_import_preview.html`, `vendor.import` renders a new
`_vendor_preview.html`. `_is_appliable_preview` widens to accept both types
(it currently hardcodes `ingest.run`), keeping its existing three conditions:
finished, upload-sourced, `dry_run is True`.

The vendor preview renders:

- the four counts, previewed against applied once an apply exists (the Slice A
  `preview_job_id` mechanism, reused unchanged)
- a **rename block**, one row per `code: old → new`, never folded into a count
- a **disappeared block** stating plainly that those rows are *kept* with a
  stale `import_batch` and nothing is deleted — unlabelled, "1 disappeared"
  reads as "1 deleted"
- an **`allow_renames` checkbox next to Apply**, rendered only when the diff
  actually contains renames, whose label names the consequence rather than the
  flag: *"Re-point N code(s) to a different manufacturer — existing item groups
  keep the old name"*
- when `outcome == "rename-refused"`, the refusal and the codes, plus the way
  back

### 4.6 What the browser cannot do

`dentalia_api` has `SELECT` on `vendor_master` and no write grant. Invariant 1's
posture holds structurally: the web enqueues, the worker writes.

## 5. Ruling: no auto-sync of aliases

`docs/runbook.md` is explicit that `playbooks sync` must run **before** an
ingest, not after: re-pointing an alias does not retro-fix `item_group` rows
already resolved under the old value, so a non-zero `orphaned_groups` count
means those items need re-resolving.

A vendor import that adds codes therefore leaves `manufacturer_alias` stale.
The tempting move is to have the handler emit or call `sync_aliases`.

**It does not.** `playbooks sync` is a CLI operation with its own dry-run /
apply discipline and its own conflict reporting; running it as a side effect of
an upload bypasses that and hides a second write behind one confirmation. The
confirm screen instead states what is now stale and the exact command to run.

Logged as a followup, not built here: an explicit "sync aliases" action once
there is a reason beyond convenience.

## 6. Testing

Table-driven where the existing suite is, against real Postgres, no mocking.

- `read_bytes` == `read_file` on the same file, including the `keep_default_na`
  equivalence for a blank `Ime`
- handler: dry run writes nothing and does not consume the spool; apply writes
  and consumes; wrong `kind` dead-letters; missing spool row dead-letters
- handler: renames present + `allow_renames` false → `rename-refused`, nothing
  written, spool intact; same + true → written
- report carries rename triples and disappeared codes, not just counts
- web: both sections enqueue the right type and dedupe key; preview dispatches
  on job type; checkbox appears only when renames exist; checkbox sets the
  payload field; previewed-vs-applied renders for vendor jobs too
- **a browser check, not only assertions on HTML strings** — Slice A shipped an
  Apply button that was inert when the page was opened directly, and every test
  passed because they asserted the `hx-*` attributes existed rather than
  clicking anything

## 7. Non-goals

- No `Proizvajalci` scheduling or watch — upload is operator-initiated
- No alias sync (§5)
- No rename *repair* — this slice surfaces the split-brain risk, it does not
  fix already-split groups
- No delete of disappeared codes, ever

## 8. Open questions

None blocking. The rename UX was decided (checkbox opt-in), the ordering
against the catalogue collapse was decided (collapse first), and §5 is ruled
here.

## 9. Dependency: the single-catalogue collapse

Denis, 2026-08-26: there is one catalogue, and the ZG dimension is to be
removed rather than preserved under a different name. That slice lands first,
at "collapse the code paths, keep the columns" depth.

**ASSUMING** that means `vendor_master.diff()` and `.apply()` lose their
`code_source` keyword and write `DEFAULT_CODE_SOURCE` internally, with the
`vendor_master.code_source` column left in place. This spec is written against
that post-collapse state: the handler in §4.3 threads no `code_source`, and the
payload in §4.1 has no such field. If the collapse leaves the keyword in place,
the handler gains one line passing the default and nothing else changes.
