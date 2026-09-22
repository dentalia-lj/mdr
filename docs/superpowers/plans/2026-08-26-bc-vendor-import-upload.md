# BC vendor import upload (Slice B) — Implementation Plan

> **STATUS: DONE 2026-08-26.** Shipped to `master` and pushed. Browser-verified against the real 390-row `Proizvajalci.xlsx` and a copy with one code re-pointed: preview names the rename, Apply unticked refuses and holds the spool, Apply ticked writes. Two things this plan did not foresee: the `job_type` enum has FIVE hand-maintained mirrors (three with guard tests, two without — the two without were the ones still wrong), and §4.5's previewed-vs-applied was missing from Task 4 until a spec re-read caught it.

**Goal:** Upload `Proizvajalci.xlsx` through `/import`, preview the four-category diff, tick a box if it re-points any code, confirm, and have the BC manufacturer master mirrored into `vendor_master`.

**Architecture:** A new queue tag `vendor.import` over the `import_inbox` spool that Slice A built. The handler is thin over `app/vendor_master.py`, which already owns reading, diffing, applying and the rename refusal. The web layer reuses `/import` and its preview/apply route pair, dispatching on `job["type"]`.

**Tech Stack:** Python 3.12 sync, psycopg 3, raw SQL, pandas for the export, FastAPI + Jinja + HTMX, pytest against real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-26-bc-vendor-import-upload-design.md`

## Global Constraints

- **Invariant 1:** only GATE handlers write `document` / `item_document` / `evidence`. This slice writes `vendor_master` only, worker-side. `dentalia_api` has `SELECT` on `vendor_master` and no write grant — do not add one.
- **Invariant 7:** job types are a closed Postgres enum. `vendor.import` does not exist until Task 1's migration lands. No task before Task 1 may reference the string.
- **Invariant 9:** payloads are immutable after enqueue. The apply is a NEW job with a NEW payload, never the preview's mutated. Fields added at enqueue time are allowed.
- **Invariant 12:** no LLM anywhere in this slice.
- `app/workers/runner.py:83` gates on `if result.get("anomalies"):`. This handler must **not** emit an `anomalies` key at all.
- **No ZG, no catalogue parameter.** One Business Central, one article numbering (Denis 2026-08-19, restated 2026-08-26). Nothing in this slice takes, renders, or stores a catalogue or `code_source` choice.
- Run tests as `python -m pytest -q -n 4 --dist loadfile`. `--dist loadfile` is not optional. Do not run the full suite in the implementation loop — run the files covering what you touched, and the full suite once before the final commit.
- Commit messages: lowercase `prefix: statement`.
- This is a shared checkout. Before every `git add`, check `git status` — stage only your own files.

## Depends on

The single-catalogue collapse slice lands **first** and removes the `code_source` keyword from `vendor_master.diff()` / `.apply()`. This plan is written against that post-collapse state. If the collapse has not landed when you start, **stop and say so** — do not thread a `code_source` you will delete.

## File Structure

| File | Responsibility |
|---|---|
| `migrations/041_vendor_import.sql` | add `vendor.import` to the `job_type` enum |
| `app/adapters/source.py` | promote `_read_frame` → public `read_frame` (no behaviour change) |
| `app/vendor_master.py` | `read_bytes(content, filename)` beside `read_file(path)`, both over `read_frame` |
| `app/handlers/vendor_import.py` | the handler: spool → diff → report, or apply → consume → gc |
| `app/handlers/__init__.py` | register the tag |
| `web/app.py` | vendor upload section, `POST /import/vendors`, preview dispatch, apply checkbox |
| `web/templates/import.html` | second upload section |
| `web/templates/_vendor_preview.html` | the four-category diff, rename block, checkbox |
| `tests/test_vendor_master.py`, `tests/test_vendor_import_handler.py`, `tests/test_web.py` | coverage |

---

### Task 1: The job type

**Files:**
- Create: `migrations/041_vendor_import.sql`
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (§0 dedupe table, §1 stage table)
- Test: `tests/test_migrations.py` (if a tag-enumeration test exists there; otherwise none)

Nothing else in this plan may reference `vendor.import` until this is applied — the enum rejects the string and every insert fails.

- [x] **Step 1: Write the migration**

```sql
-- vendor.import: a BC manufacturer master (Proizvajalci.xlsx) handed to us
-- through the browser rather than `python -m app.cli vendor-master`. Preview
-- and apply are two jobs over one `import_inbox` row (kind='vendors'), same
-- two-phase shape as the item import in 040.
--
-- Not a reuse of `ingest.run`: this writes `vendor_master`, its diff has four
-- categories rather than two, one of them (rename) is refused by default, and
-- it emits no downstream job. Invariant 7 — a new tag is a PRD change plus a
-- migration, never a string.
ALTER TYPE job_type ADD VALUE 'vendor.import';
```

- [x] **Step 2: Apply it and verify the enum accepts the value**

Run:
```bash
python -m app.cli migrate
python -c "
from app import db
with db.connect() as c:
    print([r['e'] for r in c.execute(\"SELECT unnest(enum_range(NULL::job_type))::text AS e\")])
"
```
Expected: the list contains `vendor.import`.

- [x] **Step 3: Add the two PRD rows**

In §0's dedupe-key table, after the two `ingest.run` upload rows:

| vendor.import | web UI (vendor import, preview) | `vendor.import:upload:{upload_id}:preview` |
| vendor.import | web UI (vendor import, apply) | `vendor.import:upload:{upload_id}:apply` *(added 2026-08-26, BC vendor import upload slice B — `upload_id` is the `import_inbox` row id at `kind='vendors'`. Preview and apply are two jobs with two immutable payloads (invariant 9). C2 active-scope dedupe means a terminal preview never blocks its own apply, while a double-clicked Apply dedupes.)*

In §1, a new stage row for VENDOR-IMPORT: Consumes `vendor.import` — payload `{upload_id, filename, dry_run, allow_renames, preview_job_id?}`; Emits **nothing**; Writes `vendor_master` only.

- [x] **Step 4: Commit**

```bash
git add migrations/041_vendor_import.sql docs/dentalia-pipeline-contract-prd-v3.md
git commit -m "vendor.import: the job type, and the contract rows for it"
```

---

### Task 2: Reading spooled bytes

**Files:**
- Modify: `app/adapters/source.py` (rename `_read_frame` → `read_frame`, update its callers in this file)
- Modify: `app/vendor_master.py` (`read_bytes`, `read_file` both over `read_frame`)
- Test: `tests/test_vendor_master.py`, `tests/test_source_adapter.py`

**Interfaces:**
- Produces: `app.adapters.source.read_frame(source, name) -> pandas.DataFrame` where `source` is a path or a file-like; `app.vendor_master.read_bytes(content: bytes, filename: str) -> tuple[VendorRow, ...]`

Slice A extracted `_read_frame` so the path reader and the bytes reader could not drift, and `docs/code-map.md` records that as the reason. Reuse it rather than writing a second copy.

- [x] **Step 1: Write the failing tests**

```python
def test_read_bytes_matches_read_file_on_the_same_export(tmp_path):
    """One reader, two entry points. If these ever disagree, the browser
    import and the CLI import are silently importing different things."""
    import pandas as pd
    path = tmp_path / "Proizvajalci.xlsx"
    pd.DataFrame([{"Šifra": "001", "Ime": "IVOCLAR VIVADENT"},
                  {"Šifra": "002", "Ime": "KOMET"}]).to_excel(path, index=False)

    assert vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx") == vm.read_file(path)


def test_read_bytes_treats_a_blank_name_as_absent_exactly_as_read_file_does(tmp_path):
    """`read_frame` passes keep_default_na=False, which `read_file` did not.
    A blank Ime therefore arrives as "" rather than NaN -- `_clean` maps both to
    None, and one of the 390 delivered rows is a real nameless code, so this
    equivalence is load-bearing rather than incidental."""
    import pandas as pd
    path = tmp_path / "Proizvajalci.xlsx"
    pd.DataFrame([{"Šifra": "003", "Ime": None}]).to_excel(path, index=False)

    rows = vm.read_bytes(path.read_bytes(), "Proizvajalci.xlsx")
    assert rows == (vm.VendorRow(code="003", name=None),)
    assert rows == vm.read_file(path)


def test_read_bytes_rejects_a_file_missing_the_bc_columns():
    import io, pandas as pd
    buf = io.BytesIO()
    pd.DataFrame([{"nope": "1"}]).to_excel(buf, index=False)
    with pytest.raises(ValueError, match="missing column"):
        vm.read_bytes(buf.getvalue(), "Proizvajalci.xlsx")
```

- [x] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q tests/test_vendor_master.py -k read_bytes`
Expected: FAIL, `module 'app.vendor_master' has no attribute 'read_bytes'`.

- [x] **Step 3: Promote `_read_frame`**

In `app/adapters/source.py`, rename `_read_frame` to `read_frame`, update the two call sites in that file, and add one line to its docstring: it is shared with `app/vendor_master.py`, so a change to suffix handling or the pandas options changes both the item import and the vendor import.

- [x] **Step 4: Rewrite `vendor_master`'s reader over it**

```python
def _rows_from_frame(df, label: str) -> tuple[VendorRow, ...]:
    missing = {CODE_COLUMN, NAME_COLUMN} - set(df.columns)
    if missing:
        raise ValueError(
            f"{label}: missing column(s) {sorted(missing)}; found {list(df.columns)}"
        )
    out = []
    for record in df.to_dict("records"):
        code = _clean(record.get(CODE_COLUMN))
        if code is None:
            continue
        out.append(VendorRow(code=code, name=_clean(record.get(NAME_COLUMN))))
    return tuple(out)


def read_file(path: pathlib.Path | str) -> tuple[VendorRow, ...]:
    """Parse the BC export from disk. See `_rows_from_frame` for the row rules:
    codeless rows dropped, nameless codes KEPT -- one of the 390 delivered rows
    is exactly that.
    """
    path = pathlib.Path(path)
    return _rows_from_frame(read_frame(path, path.name), str(path))


def read_bytes(content: bytes, filename: str) -> tuple[VendorRow, ...]:
    """Parse the same export from spooled bytes. `filename` is what picks the
    engine, so it must be the uploaded name and not a placeholder."""
    import io
    return _rows_from_frame(read_frame(io.BytesIO(content), filename), filename)
```

Keep the `dtype=str` note from the old docstring somewhere reachable: without it pandas reads `001` as the integer 1 and the code stops matching `item_mirror.manufacturer_raw`.

- [x] **Step 5: Run the tests**

Run: `python -m pytest -q tests/test_vendor_master.py tests/test_source_adapter.py`
Expected: PASS, including the pre-existing tests — `read_file`'s signature and behaviour are unchanged.

- [x] **Step 6: Commit**

```bash
git add app/adapters/source.py app/vendor_master.py tests/test_vendor_master.py
git commit -m "vendor_master: read the export from bytes, over the same frame reader as items"
```

---

### Task 3: The handler

**Files:**
- Create: `app/handlers/vendor_import.py`
- Modify: `app/handlers/__init__.py` (register), `app/handlers/noop.py` if it lists tags
- Test: `tests/test_vendor_import_handler.py`

**Interfaces:**
- Consumes: `vendor_master.read_bytes` (Task 2), `import_spool.read/consume/gc`
- Produces: `handle_vendor_import(conn, job) -> dict` — the report of §4.4 of the spec

- [x] **Step 1: Write the failing tests**

```python
def test_dry_run_reports_the_diff_and_writes_nothing(conn):
    uid = _spool_vendors(conn, [("001", "IVOCLAR"), ("999", "NEW MAKER")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": True, "allow_renames": False})

    report = vendor_import.handle_vendor_import(conn, job)

    assert report["added"] == 2
    assert report["written"] is False
    assert conn.execute("SELECT count(*) c FROM vendor_master").fetchone()["c"] == 0
    # the apply still needs these bytes -- two-phase spool, invariant of 040
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 1


def test_apply_writes_the_mirror_and_consumes_the_spool(conn):
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": False, "allow_renames": False})

    report = vendor_import.handle_vendor_import(conn, job)

    assert report["written"] is True and report["added"] == 1
    assert conn.execute("SELECT name FROM vendor_master WHERE code='001'"
                        ).fetchone()["name"] == "IVOCLAR"
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_a_rename_is_refused_and_nothing_is_written_or_consumed(conn):
    """canonical_manufacturer is effectively write-once, so re-pointing a code
    after items are grouped splits that manufacturer with no repair path. The
    refusal is all-or-nothing: the added row does not sneak in alongside."""
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW"), ("002", "ALSO NEW")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": False, "allow_renames": False})

    report = vendor_import.handle_vendor_import(conn, job)

    assert report["outcome"] == "rename-refused"
    assert report["written"] is False
    assert report["renamed"] == [["001", "OLD", "NEW"]]
    assert conn.execute("SELECT name FROM vendor_master WHERE code='001'"
                        ).fetchone()["name"] == "OLD"
    assert conn.execute("SELECT count(*) c FROM vendor_master").fetchone()["c"] == 1
    # spool intact, so the operator can re-apply with the box ticked
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 1


def test_allow_renames_lets_the_rename_through(conn):
    vendor_master.apply(conn, (vendor_master.VendorRow("001", "OLD"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "NEW")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": False, "allow_renames": True})

    report = vendor_import.handle_vendor_import(conn, job)

    assert report["written"] is True and report["renamed"] == [["001", "OLD", "NEW"]]
    assert conn.execute("SELECT name FROM vendor_master WHERE code='001'"
                        ).fetchone()["name"] == "NEW"


def test_a_disappeared_code_is_reported_and_kept(conn):
    """Never deleted: the row is a fact to report, and deleting it would strand
    any alias already derived from it."""
    vendor_master.apply(conn, (vendor_master.VendorRow("277", "GONE SOON"),), batch="b0")
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": True, "allow_renames": False})

    report = vendor_import.handle_vendor_import(conn, job)

    assert report["disappeared"] == ["277"]
    assert conn.execute("SELECT count(*) c FROM vendor_master WHERE code='277'"
                        ).fetchone()["c"] == 1


def test_a_wrong_kind_spool_row_is_a_dead_job_not_a_silent_zero_import(conn):
    uid = conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) "
        "VALUES ('items','Artikli.xlsx','\\x00') RETURNING id").fetchone()["id"]
    job = _job(payload={"upload_id": uid, "filename": "Artikli.xlsx",
                        "dry_run": True, "allow_renames": False})

    with pytest.raises(ValueError, match="kind"):
        vendor_import.handle_vendor_import(conn, job)


def test_the_report_carries_no_anomalies_key(conn):
    """app/workers/runner.py gates on `if result.get("anomalies")`. A vendor
    import produces none, so the key is absent rather than an empty list."""
    uid = _spool_vendors(conn, [("001", "IVOCLAR")])
    job = _job(payload={"upload_id": uid, "filename": "Proizvajalci.xlsx",
                        "dry_run": True, "allow_renames": False})

    assert "anomalies" not in vendor_import.handle_vendor_import(conn, job)
```

`_spool_vendors(conn, pairs)` writes a real `.xlsx` into `import_inbox` at `kind='vendors'` with filename `Proizvajalci.xlsx`. **The filename must match the bytes' format** — Slice A lost half a task to a helper that wrote CSV under an `.xlsx` name and watched pandas choke.

- [x] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q tests/test_vendor_import_handler.py`
Expected: FAIL at import — the module does not exist.

- [x] **Step 3: Write the handler**

```python
"""vendor.import — a BC manufacturer master handed to us via /import.

The browser counterpart to `python -m app.cli vendor-master`. Thin over
`app/vendor_master.py`, which owns reading, diffing, applying and the rename
refusal; this module owns only the spool lifecycle and the report shape.

Two jobs over one spool row, same as the item import: a preview that reads and
LEAVES the row, then an apply that reads the same bytes and consumes it.

Writes `vendor_master` only (invariant 1 — never document/item_document/
evidence). Emits nothing: `playbooks sync` stays a deliberate CLI operation,
see the spec's ruling. AI:none (invariant 12).
"""
from __future__ import annotations

import logging

from app import import_spool, vendor_master
from app.handlers import register

log = logging.getLogger("dentalia.handler.vendor_import")


def handle_vendor_import(conn, job) -> dict:
    payload = job["payload"]
    upload_id = payload["upload_id"]
    dry_run = bool(payload.get("dry_run"))
    allow_renames = bool(payload.get("allow_renames"))

    spool = import_spool.read(conn, upload_id)
    if spool["kind"] != "vendors":
        raise ValueError(
            f"import_inbox row {upload_id} has kind={spool['kind']!r}, not "
            "'vendors'. A wrong-kind row would import zero manufacturers and "
            "report it as success."
        )

    rows = vendor_master.read_bytes(spool["content"], spool["filename"])
    d = vendor_master.diff(conn, rows)

    report = {
        "seen": len(rows),
        "added": len(d.added),
        "renamed": [list(t) for t in d.renamed],
        "disappeared": list(d.disappeared),
        "unchanged": d.unchanged,
        "dry_run": dry_run,
        "allow_renames": allow_renames,
        "written": False,
    }
    if dry_run:
        return report

    try:
        vendor_master.apply(conn, rows, batch=payload["filename"],
                            allow_renames=allow_renames)
    except vendor_master.RenameRefused as exc:
        # Expected, not exceptional: the operator previewed a clean diff and
        # something re-pointed a code in between. Dead-lettering would bury the
        # reason and strand the spool row; report it and leave both intact so
        # the apply can be repeated with the box ticked.
        log.info("vendor.import %s refused: %s", upload_id, exc)
        report["outcome"] = "rename-refused"
        report["error"] = str(exc)
        return report

    import_spool.consume(conn, upload_id)
    report["written"] = True
    report["spool_gc"] = import_spool.gc(conn)
    return report


register("vendor.import", handle_vendor_import)
```

- [x] **Step 4: Register it**

Add the import to `app/handlers/__init__.py` alongside the others, following the existing pattern exactly. Check `app/handlers/noop.py` — it `setdefault`s a placeholder per tag, and a placeholder raises `UnimplementedStage` and dead-letters, so confirm the real handler wins registration order.

- [x] **Step 5: Run the tests**

Run: `python -m pytest -q tests/test_vendor_import_handler.py tests/test_runner.py tests/test_handlers.py`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add app/handlers/vendor_import.py app/handlers/__init__.py tests/test_vendor_import_handler.py
git commit -m "vendor.import: the handler, and a refused rename that reports instead of dying"
```

---

### Task 4: The web

**Files:**
- Modify: `web/app.py` (vendor section context, `POST /import/vendors`, preview dispatch, apply checkbox)
- Modify: `web/templates/import.html`
- Create: `web/templates/_vendor_preview.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `vendor.import` (Task 1), the report shape (Task 3)

Reuse, do not duplicate: `GET /import/{job_id}/preview` and `POST /import/{job_id}/apply` already exist and already carry the `preview_job_id` previewed-vs-applied mechanism. They gain a dispatch on `job["type"]`, not a parallel copy.

- [x] **Step 1: Write the failing tests**

```python
def test_vendor_upload_spools_at_kind_vendors_and_enqueues_a_preview(client, conn):
    resp = client.post("/import/vendors",
                       files={"file": ("Proizvajalci.xlsx", _vendor_xlsx_bytes(),
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       data={"priority": "interactive"})
    assert resp.status_code == 200

    spool = conn.execute("SELECT kind, filename FROM import_inbox").fetchone()
    assert spool["kind"] == "vendors"
    job = conn.execute("SELECT type, payload, dedupe_key FROM job").fetchone()
    assert job["type"] == "vendor.import"
    assert job["dedupe_key"].endswith(":preview")
    assert job["payload"]["dry_run"] is True
    assert job["payload"]["allow_renames"] is False
    assert job["payload"]["filename"] == "Proizvajalci.xlsx"


def test_the_preview_page_dispatches_on_job_type(client, conn):
    """One URL, two report shapes. A vendor job rendered through the item
    template would show blank counts and an Apply button for the wrong thing."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 390, "added": 4, "renamed": [], "disappeared": [],
        "unchanged": 386, "dry_run": True, "written": False})
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "390" in resp.text
    assert "Rows in file" not in resp.text     # the item template's row label


def test_the_rename_checkbox_appears_only_when_there_are_renames(client, conn):
    clean = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 1, "renamed": [], "disappeared": [],
        "unchanged": 0, "dry_run": True, "written": False})
    assert 'name="allow_renames"' not in client.get(f"/import/{clean}/preview").text

    dirty = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})
    page = client.get(f"/import/{dirty}/preview").text
    assert 'name="allow_renames"' in page
    assert "OLD" in page and "NEW" in page and "001" in page


def test_ticking_the_box_sets_allow_renames_on_the_apply_payload(client, conn):
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})

    client.post(f"/import/{jid}/apply", data={"allow_renames": "on"})

    payload = conn.execute(
        "SELECT payload FROM job WHERE dedupe_key LIKE '%%:apply'").fetchone()["payload"]
    assert payload["allow_renames"] is True
    assert payload["dry_run"] is False
    assert payload["preview_job_id"] == jid


def test_not_ticking_the_box_leaves_allow_renames_false(client, conn):
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})

    client.post(f"/import/{jid}/apply")

    payload = conn.execute(
        "SELECT payload FROM job WHERE dedupe_key LIKE '%%:apply'").fetchone()["payload"]
    assert payload["allow_renames"] is False


def test_the_disappeared_block_says_the_rows_are_kept(client, conn):
    """"1 disappeared" unlabelled reads as "1 deleted". Nothing is ever
    deleted -- the row keeps its stale import_batch."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [], "disappeared": ["277"],
        "unchanged": 1, "dry_run": True, "written": False})
    page = client.get(f"/import/{jid}/preview").text
    assert "277" in page
    assert "kept" in page.lower()


def test_a_refused_rename_is_shown_with_a_way_back(client, conn):
    jid = _seed_vendor_preview(conn, dry_run=False, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": False, "written": False,
        "outcome": "rename-refused", "error": "1 code(s) would be re-pointed"})
    page = client.get(f"/import/{jid}/preview").text
    assert "refused" in page.lower()
    assert "001" in page


def test_the_vendor_preview_page_works_standalone(client, conn):
    """Slice A shipped an Apply button that was inert when the page was opened
    directly, because the partial does not extend base.html and base.html is
    what loads htmx. Same wrapper, same trap."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 1, "renamed": [], "disappeared": [],
        "unchanged": 0, "dry_run": True, "written": False})
    page = client.get(f"/import/{jid}/preview").text
    assert "htmx" in page
    assert "#result" not in page
```

- [x] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q tests/test_web.py -k "vendor"`
Expected: FAIL — 404 on `/import/vendors`.

- [x] **Step 3: Widen the appliable check and the preview dispatch**

`_is_appliable_preview` currently hardcodes `job["type"] == "ingest.run"`. Widen to `in ("ingest.run", "vendor.import")`, keeping its other three conditions (`status == "done"`, upload-sourced, `dry_run is True`). Note that `vendor.import` payloads have no `source` key — the upload-sourced condition must not reject them; a `vendor.import` job is upload-sourced by construction.

In `import_preview`, pick the partial by job type, then apply the existing `HX-Request` wrapper choice on top:

```python
partial = ("_vendor_preview.html" if job["type"] == "vendor.import"
           else "_import_preview.html")
```

The standalone wrapper `import_preview_page.html` includes a fixed partial name today — parameterise it via a context variable so both kinds get a real page when opened directly.

- [x] **Step 4: Add the route and the form section**

`POST /import/vendors` mirrors `POST /import` — same suffix and size validation via `IMPORT_SUFFIXES` and `cfg.upload_max_mb`, same `currval('import_inbox_id_seq')` (no `RETURNING`: migration 040 grants INSERT and sequence USAGE only, no SELECT) — with `kind='vendors'`, no `catalogue`, and:

```python
payload = {"upload_id": uid, "filename": filename,
           "dry_run": True, "allow_renames": False}
dedupe_key = f"vendor.import:upload:{uid}:preview"
```

`import_apply` gains `allow_renames: bool = Form(False)` and sets it on the apply payload only for `vendor.import` jobs — an `ingest.run` payload must not grow a field it has no meaning for.

Guard `queue.enqueue` returning `None` (C2 dedupe) before building the onward link, exactly as `/import` does — otherwise the page renders `/import/None/preview`.

- [x] **Step 5: Write `_vendor_preview.html`**

Same skeleton as `_import_preview.html`: a `.vendor-preview` wrapper, the pending/running poll, the failed branch, then the report. Counts table (seen / added / renamed / disappeared / unchanged), reusing the previewed-vs-applied two-column treatment when `compared` is set. Then the rename block (one row per triple), the disappeared block with the "kept, nothing deleted" sentence, the checkbox — rendered only when `result.renamed` is non-empty — and the Apply button inside a form that posts both.

Target `closest .vendor-preview`, swap `outerHTML`. **Never `#result`** — that id exists only on `/import`, and the page has to survive a bookmark.

- [x] **Step 6: Run the tests**

Run: `python -m pytest -q tests/test_web.py`
Expected: PASS, all of it — the item import tests must be untouched.

- [x] **Step 7: Drive it in a real browser**

Not optional, and not satisfied by the assertions above. Start the app against a throwaway database, upload the real `imports/Proizvajalci.xlsx`, read the preview, **click Apply**, and confirm from the database that `vendor_master` gained the rows and the spool row is gone. Then repeat with a file that renames a code and confirm the refusal path and the checkbox.

```bash
python - <<'PY'
import psycopg
with psycopg.connect("postgresql://dentalia:dentalia@localhost:5432/postgres", autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS dentalia_vendorcheck")
    c.execute("CREATE DATABASE dentalia_vendorcheck OWNER dentalia")
PY
DATABASE_URL=postgresql://dentalia:dentalia@localhost:5432/dentalia_vendorcheck python -m app.cli migrate
```

Tear the database and the processes down when done.

- [x] **Step 8: Commit**

```bash
git add web/app.py web/templates/import.html web/templates/_vendor_preview.html web/templates/import_preview_page.html tests/test_web.py
git commit -m "web: upload the manufacturer master, and tick a box before re-pointing a code"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/runbook.md`, `docs/code-map.md`, `docs/dentalia-schema-sketch.md`, `docs/dentalia-job-type-handbook.md`, `tasks/followups.md`

- [x] **Step 1: Runbook**

Extend the `/import` route row and the "Importing an item catalogue export without shell access" section with the vendor half: which file, the four categories, that a rename needs the checkbox and why, that disappeared codes are kept, and — stated plainly — that `manufacturer_alias` is now stale and `playbooks sync` is the command to run, before the next ingest rather than after.

- [x] **Step 2: Code map**

Add `app/handlers/vendor_import.py` and `web/templates/_vendor_preview.html`; extend the `web/app.py` and `app/vendor_master.py` rows.

- [x] **Step 3: Job type handbook**

Add the `vendor.import` section: payload, pseudo-code, the report shape, and that it emits nothing.

- [x] **Step 4: Followup**

Append: `- [x] 2026-08-26 [vendor-import-alias-sync] ...` — a vendor import leaves `manufacturer_alias` stale and the confirm screen only says so. Whether an explicit "sync aliases" action belongs in the UI is a decision for after this ships.

- [x] **Step 5: Full suite, then commit**

Run: `python -m pytest -q -n 4 --dist loadfile`
Expected: 0 failures. Say which numbers you got.

```bash
git add docs/ tasks/followups.md
git commit -m "docs: the vendor import, and the alias sync it leaves for you to run"
```

---

## Self-review notes

- Every spec section maps to a task: §4.1→T1, §4.2→T2, §4.3/§4.4→T3, §4.5→T4, §5→T5 followup, §6 spread across T2–T4.
- Names used consistently: `read_frame`, `read_bytes`, `_rows_from_frame`, `handle_vendor_import`, `allow_renames`, `preview_job_id`.
- The two traps Slice A actually hit are called out where they recur: the fixture filename must match the fixture bytes (T3 Step 1), and the preview partial must survive being opened directly (T4 Steps 3, 5, and the standalone test).
