# Web Document Upload Implementation Plan

**Goal:** Let an admin upload a compliance PDF through the web UI and have it enter the pipeline at `extract.doc`, linked to a target group when known.

**Architecture:** A new closed-enum job type `upload.ingest`. The producer-only web `/upload` form inserts a transient `upload_inbox` row (the PDF bytes) and enqueues `upload.ingest`; a worker archives + hashes + emits `extract.doc` (new content) or `validate.doc` (seen content, C3 link) exactly like FETCH's post-download tail, then deletes the spool row. Shared archive/ledger/emit helpers are factored out of `fetch.py` so upload and fetch cannot diverge.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL), FastAPI + Jinja + HTMX, pytest against real Postgres, StorageAdapter (PRD §9).

**Spec:** `docs/superpowers/specs/2026-07-29-web-document-upload-design.md`

## Global Constraints

- Python 3.12+, sync only (no asyncio). psycopg 3, raw SQL, no ORM.
- Invariant 1: only GATE handlers write `document`/`item_document`/`evidence`. Web writes only `job` + `upload_inbox`; the worker writes `fetch_log` + archive.
- Invariant 7: job types are a closed enum; a new tag requires a migration (`ALTER TYPE job_type ADD VALUE`). `ALTER TYPE ... ADD VALUE` must not be used in the same transaction that uses the value (PG16); the migration only adds the value + creates the table.
- Invariant 8: dedupe scoped to active jobs. Dedupe keys: `upload:{upload_id}` (enqueue), `extract:{content_hash}` / `validate:{content_hash}:{rev}:{group_id}` (downstream — reused verbatim from fetch).
- Handlers receive the claiming transaction's connection, must not commit, must be idempotent (at-least-once delivery).
- Tests: real Postgres (`conn` fixture, autocommit off, rolled back; `job`/`domain_lease`/`scheduler_run` truncated between tests). No mocking Postgres. Table-driven where dispositions branch.
- Commit messages: plain imperative, NO Claude/AI attribution.

---

### Task 1: Migration 014 — `upload_inbox` table + `upload.ingest` enum value + grants

**Files:**
- Create: `migrations/014_upload.sql`
- Test: `tests/test_migration_upload.py`

**Interfaces:**
- Produces: table `upload_inbox(id bigserial, filename text, content bytea, target_group_id bigint, catalogue text, uploaded_by text, created_at timestamptz)`; enum value `job_type 'upload.ingest'`; `dentalia_api` holds INSERT on `upload_inbox` + USAGE on its sequence, nothing else.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_upload.py
"""Migration 014: upload_inbox spool + upload.ingest enum value + web grants."""
from __future__ import annotations


def _enum_labels(conn, typename):
    return {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = %s", (typename,)).fetchall()}


def test_upload_ingest_is_a_job_type(conn):
    assert "upload.ingest" in _enum_labels(conn, "job_type")


def test_upload_inbox_table_exists_with_expected_columns(conn):
    cols = {r["column_name"]: r["data_type"] for r in conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'upload_inbox'").fetchall()}
    assert cols["content"] == "bytea"
    assert cols["target_group_id"] == "bigint"
    assert {"id", "filename", "catalogue", "uploaded_by", "created_at"} <= set(cols)


def test_web_role_can_insert_upload_inbox_but_not_the_registry(conn):
    def _has(priv, table):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', %s, %s) AS ok",
            (table, priv)).fetchone()["ok"]
    assert _has("INSERT", "upload_inbox") is True
    assert _has("SELECT", "upload_inbox") is False
    assert _has("INSERT", "document") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose --profile test run --rm --build test pytest tests/test_migration_upload.py -v`
Expected: FAIL — `upload.ingest` not in enum / `upload_inbox` missing.

- [ ] **Step 3: Write the migration**

```sql
-- migrations/014_upload.sql
-- Web document upload (manual sibling of the Phase-2 email.poll). The web
-- /upload form inserts one transient upload_inbox row (the PDF bytes) and
-- enqueues upload.ingest; a worker archives + hashes + emits extract.doc /
-- validate.doc, then deletes the row. dentalia_api (web) may INSERT the spool
-- but never reads/deletes it (worker-only) and never touches the registry
-- (invariant 1).
--
-- ALTER TYPE ... ADD VALUE cannot run in the same transaction that USES the new
-- value (PG16); this file only adds the value and creates the table — nothing
-- here inserts an 'upload.ingest' job row. Precedent: 013_scheduler.sql.

ALTER TYPE job_type ADD VALUE 'upload.ingest';

CREATE TABLE upload_inbox (
  id              bigserial PRIMARY KEY,
  filename        text        NOT NULL,
  content         bytea       NOT NULL,
  target_group_id bigint,                 -- soft ref (no FK): null when standalone
  catalogue       text        NOT NULL,
  uploaded_by     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- New table after 007's blanket grant, so grant explicitly. Producer role gets
-- INSERT only: it enqueues uploads but never reads/deletes the spool and never
-- writes the registry.
GRANT INSERT ON upload_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE upload_inbox_id_seq TO dentalia_api;
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose --profile test run --rm --build test pytest tests/test_migration_upload.py -v`
Expected: PASS (3 tests). The conftest applies all migrations once per session, so 014 is now in the test DB.

- [ ] **Step 5: Commit**

```bash
git add migrations/014_upload.sql tests/test_migration_upload.py
git commit -m "migration: upload_inbox spool + upload.ingest job type + web grants"
```

---

### Task 2: Extract shared archive/ledger/emit helpers out of `fetch.py`

Upload needs the exact archive-path, ledger-upsert, and emit logic FETCH already has. Duplicating it would let the archive layout or the `validate:{hash}:{rev}:{group}` dedupe key drift between the two producers. Move them to a shared module and have `fetch.py` import them — same rationale as `app/urls.py` shared by DISCOVER and FETCH. Pure refactor, no behavior change.

**Files:**
- Create: `app/handlers/archiving.py`
- Modify: `app/handlers/fetch.py` (replace local helpers with imports)
- Test: `tests/test_archiving.py` (new unit tests) + `tests/test_fetch_handler.py` (must stay green)

**Interfaces:**
- Produces (all in `app.handlers.archiving`):
  - `archive_path(manufacturer: str, content_hash: str, url: str, content_type: str | None) -> str`
  - `manufacturer_for_group(conn, group_id) -> str`
  - `ledger_upsert(conn, url_norm, *, etag, last_modified, content_hash, source) -> None`
  - `hash_extracted_rev(conn, content_hash) -> int | None`
  - `emit_extract(conn, archive_url, content_hash, group_id, source_url) -> None`
  - `emit_validate(conn, content_hash, rev, group_id) -> None`
  - `link_existing_doc(conn, url_norm, content_hash) -> None`

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_archiving.py
"""Shared FETCH/UPLOAD archive + emit helpers (no HTTP, no LLM)."""
from __future__ import annotations

from app.handlers import archiving


def test_archive_path_is_hash_addressed_and_sanitized():
    p = archiving.archive_path("VOCO GmbH", "abcdef0123456789", "cert.pdf", "application/pdf")
    assert p.startswith("VOCO_GmbH/")
    assert "abcdef012345__" in p          # 12-char hash prefix
    assert p.endswith("cert.pdf")


def test_emit_extract_uses_content_hash_dedupe_key(conn):
    archiving.emit_extract(conn, "file:///a/x.pdf", "hh", 7, "upload:x.pdf")
    row = conn.execute(
        "SELECT type, payload, dedupe_key FROM job WHERE type='extract.doc'").fetchone()
    assert row["dedupe_key"] == "extract:hh"
    assert row["payload"] == {"archive_url": "file:///a/x.pdf", "content_hash": "hh",
                              "group_id": 7, "source_url": "upload:x.pdf"}


def test_emit_validate_key_includes_rev_and_group(conn):
    archiving.emit_validate(conn, "hh", 3, 7)
    row = conn.execute(
        "SELECT dedupe_key FROM job WHERE type='validate.doc'").fetchone()
    assert row["dedupe_key"] == "validate:hh:3:7"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose --profile test run --rm --build test pytest tests/test_archiving.py -v`
Expected: FAIL — `app.handlers.archiving` does not exist.

- [ ] **Step 3: Create `archiving.py` (move the logic verbatim from fetch.py)**

Move `_TYPE_TOKENS`, `_guess_type_dir`, `_sanitize`, `_filename`, `_archive_path`, `_manufacturer_for_group`, `_ledger_upsert`, `_hash_extracted_rev`, `_emit_validate`, `_emit_extract`, `_link_existing_doc` from `fetch.py` into `app/handlers/archiving.py`, renaming the shared ones to public (drop the leading underscore); keep `_TYPE_TOKENS`/`_guess_type_dir`/`_sanitize`/`_filename` private. Header:

```python
# app/handlers/archiving.py
"""Archive-path, fetch-ledger, and spine-emit helpers shared by FETCH and
UPLOAD (and, later, email.poll). One home so the archive layout and the
extract/validate dedupe keys cannot diverge between producers — same rationale
as app/urls.py owning the fetch dedupe key for DISCOVER + FETCH.

Producer-side only: writes fetch_log, emits jobs. Never document/item_document/
evidence (invariant 1).
"""
from __future__ import annotations

import re
import urllib.parse

from app import queue
```

Then paste the moved bodies, e.g.:

```python
def emit_extract(conn, archive_url, content_hash, group_id, source_url):
    queue.enqueue(
        conn, "extract.doc",
        {"archive_url": archive_url, "content_hash": content_hash,
         "group_id": group_id, "source_url": source_url},
        dedupe_key=f"extract:{content_hash}",
    )


def emit_validate(conn, content_hash, rev, group_id):
    queue.enqueue(
        conn, "validate.doc",
        {"content_hash": content_hash, "group_id": group_id, "extract_rev": rev},
        dedupe_key=f"validate:{content_hash}:{rev}:{group_id}",
    )
```

(and `archive_path`, `manufacturer_for_group`, `ledger_upsert`, `hash_extracted_rev`, `link_existing_doc` identically, bodies unchanged from fetch.py).

- [ ] **Step 4: Refactor `fetch.py` to import them**

Add `from app.handlers import archiving`, delete the moved definitions, and update call sites: `_archive_path(...)` → `archiving.archive_path(...)`, `_manufacturer_for_group` → `archiving.manufacturer_for_group`, `_ledger_upsert` → `archiving.ledger_upsert`, `_hash_extracted_rev` → `archiving.hash_extracted_rev`, `_emit_validate` → `archiving.emit_validate`, `_emit_extract` → `archiving.emit_extract`, `_link_existing_doc` → `archiving.link_existing_doc`. Keep `_ledger_get`, `_fresh`, `_ledger_touch`, domain/playwright helpers, `_fetch`, and `handle_fetch_url` in fetch.py. If `tests/test_fetch_handler.py` references any moved private name directly, add a thin alias at the bottom of the moved section (e.g. `_archive_path = archiving.archive_path`) rather than editing the test.

- [ ] **Step 5: Run both test files**

Run: `docker compose --profile test run --rm --build test pytest tests/test_archiving.py tests/test_fetch_handler.py -v`
Expected: PASS — new archiving tests green AND every existing fetch test still green (no behavior change).

- [ ] **Step 6: Commit**

```bash
git add app/handlers/archiving.py app/handlers/fetch.py tests/test_archiving.py
git commit -m "refactor: share FETCH archive/ledger/emit helpers via app.handlers.archiving"
```

---

### Task 3: `upload.ingest` handler

**Files:**
- Create: `app/handlers/upload.py`
- Modify: `app/workers/runner.py` (import the module so it registers — mirror how other handlers are imported)
- Test: `tests/test_upload_handler.py`

**Interfaces:**
- Consumes: `app.handlers.archiving.*` (Task 2); `upload_inbox` table (Task 1).
- Produces: `handle_upload_ingest(conn, job, *, store=None) -> dict`; registered as `upload.ingest`. Emits `extract.doc` or `validate.doc`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_upload_handler.py
"""upload.ingest — fetch.url minus the HTTP fetch. Bytes arrive in an
upload_inbox row; archive + hash + emit extract.doc (new) / validate.doc (seen)
exactly like FETCH's tail, then delete the spool row.
"""
from __future__ import annotations

from app.handlers import upload as uh


class FakeStore:
    def __init__(self):
        self.puts = []

    def put(self, body: bytes, path: str) -> str:
        self.puts.append((body, path))
        return f"file:///archive/{path}"


def _insert_upload(conn, content: bytes, *, filename="cert.pdf", group_id=None, catalogue="LJ"):
    return conn.execute(
        "INSERT INTO upload_inbox (filename, content, target_group_id, catalogue, uploaded_by) "
        "VALUES (%s,%s,%s,%s,'admin') RETURNING id",
        (filename, content, group_id, catalogue),
    ).fetchone()["id"]


def _run(conn, upload_id, store=None):
    return uh.handle_upload_ingest(
        conn, {"payload": {"upload_id": upload_id}}, store=store or FakeStore())


def _seed_extracted(conn, content_hash, rev=1):
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES (%s, 'T0', '{}'::jsonb, %s)", (content_hash, rev))


def test_new_content_archives_and_emits_extract(conn):
    uid = _insert_upload(conn, b"%PDF-NEW")
    store = FakeStore()
    res = _run(conn, uid, store=store)

    assert res["outcome"] == "archived"
    assert res["emitted"] == "extract.doc"
    assert len(store.puts) == 1
    job = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='extract.doc'").fetchone()
    assert job["payload"]["content_hash"] == res["content_hash"]
    assert job["payload"]["group_id"] is None
    assert job["payload"]["source_url"] == "upload:cert.pdf"
    assert job["dedupe_key"] == f"extract:{res['content_hash']}"
    assert conn.execute("SELECT count(*) c FROM fetch_log WHERE source='upload'").fetchone()["c"] == 1
    assert conn.execute("SELECT count(*) c FROM upload_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0


def test_seen_content_with_group_emits_validate_not_extract(conn):
    import hashlib
    body = b"%PDF-SEEN"
    h = hashlib.sha256(body).hexdigest()
    _seed_extracted(conn, h, rev=2)
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (5, 'ACME')")
    uid = _insert_upload(conn, body, group_id=5)

    res = _run(conn, uid)

    assert res["outcome"] == "linked"
    assert res["emitted"] == "validate.doc"
    v = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='validate.doc'").fetchone()
    assert v["payload"] == {"content_hash": h, "group_id": 5, "extract_rev": 2}
    assert v["dedupe_key"] == f"validate:{h}:2:5"
    assert conn.execute("SELECT count(*) c FROM job WHERE type='extract.doc'").fetchone()["c"] == 0


def test_seen_content_without_group_skips(conn):
    import hashlib
    body = b"%PDF-SEEN2"
    h = hashlib.sha256(body).hexdigest()
    _seed_extracted(conn, h)
    uid = _insert_upload(conn, body, group_id=None)

    res = _run(conn, uid)

    assert res["outcome"] == "duplicate"
    assert conn.execute("SELECT count(*) c FROM job WHERE type IN ('extract.doc','validate.doc')").fetchone()["c"] == 0


def test_hash_in_ledger_but_never_extracted_still_emits_extract(conn):
    import hashlib
    body = b"%PDF-ARCHIVED-NOT-EXTRACTED"
    h = hashlib.sha256(body).hexdigest()
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES (%s,%s,'upload', now(), now())", (f"upload:{h}", h))
    uid = _insert_upload(conn, body, group_id=None)

    res = _run(conn, uid)

    assert res["outcome"] == "archived"
    assert conn.execute("SELECT count(*) c FROM job WHERE type='extract.doc'").fetchone()["c"] == 1


def test_missing_row_is_idempotent_noop(conn):
    res = uh.handle_upload_ingest(conn, {"payload": {"upload_id": 999999}}, store=FakeStore())
    assert res["outcome"] == "already-processed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose --profile test run --rm --build test pytest tests/test_upload_handler.py -v`
Expected: FAIL — `app.handlers.upload` does not exist.

- [ ] **Step 3: Write the handler**

```python
# app/handlers/upload.py
"""upload.ingest — a document handed to us via the web /upload form.

fetch.url minus the HTTP fetch: the bytes already sit in a transient
upload_inbox row, so this archives them, upserts the fetch ledger, and hands the
spine forward exactly like FETCH's post-download tail:

    new content              -> archive + extract.doc {archive_url, content_hash, group_id?, source_url}
    seen + extracted + group -> validate.doc (C3 link-candidate vs the existing doc)
    seen + extracted, no grp -> nothing to link, skip
    seen + not-yet-extracted -> extract.doc (carry the group)

Producer only (invariant 1): writes fetch_log + the archived file, emits jobs;
never document/item_document/evidence. AI:none (invariant 12). Idempotent: the
spool row is deleted on success, so a retry after a committed delete is a no-op.
"""
from __future__ import annotations

import hashlib
import logging

from app.adapters.storage import make_storage_adapter
from app.config import load_config
from app.handlers import archiving, register

log = logging.getLogger("dentalia.handler.upload")


def handle_upload_ingest(conn, job, *, store=None):
    upload_id = job["payload"]["upload_id"]
    row = conn.execute(
        "SELECT filename, content, target_group_id, catalogue "
        "FROM upload_inbox WHERE id=%s", (upload_id,),
    ).fetchone()
    if row is None:
        return {"outcome": "already-processed", "upload_id": upload_id}

    group_id = row["target_group_id"]
    content_hash = hashlib.sha256(row["content"]).hexdigest()
    url_norm = f"upload:{content_hash}"

    archiving.ledger_upsert(conn, url_norm, etag=None, last_modified=None,
                            content_hash=content_hash, source="upload")

    rev = archiving.hash_extracted_rev(conn, content_hash)
    if rev is not None:
        if group_id is not None:
            archiving.link_existing_doc(conn, url_norm, content_hash)
            archiving.emit_validate(conn, content_hash, rev, group_id)
            result = {"outcome": "linked", "content_hash": content_hash,
                      "emitted": "validate.doc", "group_id": group_id}
        else:
            result = {"outcome": "duplicate", "content_hash": content_hash, "group_id": None}
    else:
        if store is None:
            store = make_storage_adapter(load_config())
        manufacturer = archiving.manufacturer_for_group(conn, group_id)
        archive_url = store.put(
            row["content"],
            archiving.archive_path(manufacturer, content_hash, row["filename"], "application/pdf"),
        )
        archiving.emit_extract(conn, archive_url, content_hash, group_id,
                               source_url=f"upload:{row['filename']}")
        result = {"outcome": "archived", "content_hash": content_hash,
                  "archive_url": archive_url, "emitted": "extract.doc", "group_id": group_id}

    conn.execute("DELETE FROM upload_inbox WHERE id=%s", (upload_id,))
    return result


register("upload.ingest", handle_upload_ingest)
```

- [ ] **Step 4: Register the module in the runner**

In `app/workers/runner.py`, add `upload` to the block that imports handler modules for their registration side effect (find the existing `from app.handlers import ingest, resolve, ...` import and add `upload`). Confirm the runner-wiring test (whichever asserts every enum tag has a handler) still passes.

- [ ] **Step 5: Run to verify they pass**

Run: `docker compose --profile test run --rm --build test pytest tests/test_upload_handler.py tests/test_runner.py -v`
Expected: PASS (5 handler tests + runner wiring).

- [ ] **Step 6: Commit**

```bash
git add app/handlers/upload.py app/workers/runner.py tests/test_upload_handler.py
git commit -m "feat: upload.ingest handler (archive + emit extract/validate)"
```

---

### Task 4: Web `/upload` route + config + template

**Files:**
- Modify: `app/config.py` (`Web` dataclass field + loader; ~line 242 and ~line 543)
- Modify: `web/app.py` (GET/POST `/upload`; `from fastapi import File, UploadFile`)
- Create: `web/templates/upload.html`
- Modify: `web/templates/base.html` (nav link)
- Test: `tests/test_web.py` (append upload tests)

**Interfaces:**
- Consumes: `queue.enqueue` (existing), `_conn()` context manager (existing), `_authenticated_user(request)` (existing), `upload_inbox` table (Task 1), `cfg.web.upload_max_mb` (this task).
- Produces: `GET /upload` (form), `POST /upload` (inserts `upload_inbox`, enqueues `upload.ingest {upload_id}`, dedupe `upload:{id}`), rendering `_result.html`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_web.py
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_upload_valid_inserts_spool_and_enqueues(client, conn):
    resp = client.post(
        "/upload",
        files={"file": ("cert.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 200

    spool = conn.execute(
        "SELECT id, filename, catalogue, target_group_id FROM upload_inbox").fetchall()
    assert len(spool) == 1
    assert spool[0]["filename"] == "cert.pdf"
    assert spool[0]["target_group_id"] is None

    job = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='upload.ingest'").fetchone()
    assert job["payload"] == {"upload_id": spool[0]["id"]}
    assert job["dedupe_key"] == f"upload:{spool[0]['id']}"


def test_upload_with_target_group_is_recorded(client, conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (9, 'ACME')")
    conn.commit()
    resp = client.post(
        "/upload",
        files={"file": ("d.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive", "target_group_id": "9"},
    )
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT target_group_id FROM upload_inbox").fetchone()["target_group_id"] == 9


def test_upload_rejects_non_pdf(client, conn):
    resp = client.post(
        "/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 0


def test_upload_rejects_oversize(client, conn):
    big = b"%PDF" + b"0" * (26 * 1024 * 1024)   # > 25 MB default
    resp = client.post(
        "/upload",
        files={"file": ("big.pdf", big, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose --profile test run --rm --build test pytest tests/test_web.py -k upload -v`
Expected: FAIL — no `/upload` route (404/405).

- [ ] **Step 3: Add the config field**

In `app/config.py`, `Web` dataclass (~line 242), add after `imports_dir`:

```python
    upload_max_mb: int = 25
```

and in the `Web(...)` loader (~line 545, after `imports_dir=...`):

```python
        upload_max_mb=_int("UPLOAD_MAX_MB", _dig(t, "web", "upload_max_mb"), 25),
```

- [ ] **Step 4: Add the route**

In `web/app.py`, add `File, UploadFile` to the `from fastapi import ...` line, then register the routes (mirror `ingest_submit`'s `_conn()` + `_result.html` idiom, lines 483-542):

```python
    @app.get("/upload", response_class=HTMLResponse)
    def upload_form(request: Request, group_id: str = ""):
        ctx = {"request": request, "catalogues": CATALOGUES, "priorities": PRIORITIES,
               "prefill_group_id": group_id}
        return templates.TemplateResponse(request, "upload.html", ctx)

    @app.post("/upload", response_class=HTMLResponse)
    def upload_submit(
        request: Request,
        file: UploadFile = File(...),
        catalogue: str = Form(...),
        priority: str = Form("interactive"),
        target_group_id: str = Form(""),
    ):
        error = None
        if catalogue not in CATALOGUES:
            error = f"unknown catalogue {catalogue!r}"
        elif priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        content = file.file.read()
        if error is None:
            is_pdf = (file.content_type == "application/pdf"
                      or (file.filename or "").lower().endswith(".pdf"))
            if not is_pdf:
                error = "only PDF uploads are accepted"
            elif len(content) > cfg.web.upload_max_mb * 1024 * 1024:
                error = f"file exceeds the {cfg.web.upload_max_mb} MB limit"

        gid = None
        if error is None and target_group_id.strip():
            try:
                gid = int(target_group_id)
            except ValueError:
                error = f"target_group_id {target_group_id!r} is not a number"

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        with _conn() as conn:
            uid = conn.execute(
                "INSERT INTO upload_inbox (filename, content, target_group_id, catalogue, uploaded_by) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (file.filename, content, gid, catalogue, _authenticated_user(request)),
            ).fetchone()["id"]
            dedupe_key = f"upload:{uid}"
            jid = queue.enqueue(conn, "upload.ingest", {"upload_id": uid}, dedupe_key,
                                priority=priority)
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        return templates.TemplateResponse(request, "_result.html", ctx)
```

Note: `cfg` is the closed-over config in `create_app` (same one `ingest_submit` reads); confirm it is in scope.

- [ ] **Step 5: Create the template + nav link**

`web/templates/upload.html` (mirror `ingest.html`'s structure — extends `base.html`, HTMX-posts to `/upload`):

```html
{% extends "base.html" %}
{% block content %}
<h1>Upload a document</h1>
<form hx-post="/upload" hx-encoding="multipart/form-data" hx-target="#result">
  <label>PDF file <input type="file" name="file" accept="application/pdf" required></label>
  <label>Catalogue
    <select name="catalogue">
      {% for c in catalogues %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
    </select>
  </label>
  <label>Target group id (optional)
    <input type="text" name="target_group_id" value="{{ prefill_group_id }}"
           placeholder="leave blank to self-identify by REF list"></label>
  <label>Priority
    <select name="priority">
      {% for p in priorities %}<option value="{{ p }}">{{ p }}</option>{% endfor %}
    </select>
  </label>
  <button type="submit">Upload</button>
</form>
<div id="result"></div>
{% endblock %}
```

Add a nav link to `base.html` next to the existing Ingest link: `<a href="/upload">Upload</a>`.

- [ ] **Step 6: Run to verify they pass**

Run: `docker compose --profile test run --rm --build test pytest tests/test_web.py -k upload -v`
Expected: PASS (4 upload tests).

- [ ] **Step 7: Commit**

```bash
git add app/config.py web/app.py web/templates/upload.html web/templates/base.html tests/test_web.py
git commit -m "feat: web /upload route enqueues upload.ingest"
```

---

### Task 5: `/manual` discovery-dead-end → "Upload document" prefill

**Files:**
- Modify: `web/templates/manual.html` (add an "Upload document" link on `discovery-dead-end` tasks)
- Modify: `web/app.py` (resolve the manual_task when an upload is submitted with a `manual_task_id`)
- Test: `tests/test_web.py` (dead-end prefill + resolve)

**Interfaces:**
- Consumes: `manual_task(group_id, ...)` (existing), `/upload` (Task 4).
- Produces: `/upload?group_id={gid}&manual_task_id={tid}`; on POST, if `manual_task_id` is present and valid, mark the task resolved after enqueue.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_web.py
def test_upload_from_dead_end_resolves_the_manual_task(client, conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (12, 'ACME')")
    tid = conn.execute(
        "INSERT INTO manual_task (kind, group_id, status, payload) "
        "VALUES ('discovery-dead-end', 12, 'open', '{}'::jsonb) RETURNING id"
    ).fetchone()["id"]
    conn.commit()

    resp = client.post(
        "/upload",
        files={"file": ("d.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive",
              "target_group_id": "12", "manual_task_id": str(tid)},
    )
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT status FROM manual_task WHERE id=%s", (tid,)).fetchone()["status"] == "resolved"
    assert conn.execute("SELECT count(*) c FROM job WHERE type='upload.ingest'").fetchone()["c"] == 1
```

(Confirm the real `manual_task` column names — `kind`/`status`/`payload` — against `migrations/009_manual_task.sql` before writing; adjust the INSERT and the resolved-status string to match the existing resolve path used by `POST /manual/{id}/resolve`.)

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose --profile test run --rm --build test pytest tests/test_web.py -k dead_end -v`
Expected: FAIL — task stays `open` (no `manual_task_id` handling).

- [ ] **Step 3: Implement**

Add `manual_task_id: str = Form("")` to `upload_submit`. After the successful enqueue (inside the `with _conn()` block, before commit), if `manual_task_id.strip()`, run the same resolve UPDATE the existing `/manual/{id}/resolve` handler uses (copy its exact status value and columns):

```python
            if manual_task_id.strip():
                conn.execute(
                    "UPDATE manual_task SET status='resolved', resolved_at=now() WHERE id=%s",
                    (int(manual_task_id),))
```

In `manual.html`, for a `discovery-dead-end` row add:
`<a href="/upload?group_id={{ task.group_id }}&manual_task_id={{ task.id }}">Upload document</a>`.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose --profile test run --rm --build test pytest tests/test_web.py -k "upload or dead_end" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/app.py web/templates/manual.html tests/test_web.py
git commit -m "feat: resolve discovery dead-end via web document upload"
```

---

### Task 6: Documentation (normative + operational)

No TDD — targeted doc edits. The PRD change is the load-bearing one.

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (NORMATIVE)
- Modify: `docs/dentalia-schema-sketch.md`
- Modify: `docs/dentalia-job-type-handbook.md`
- Modify: `docs/code-map.md`, `docs/runbook.md`, `docs/architecture.md`
- Modify: `PHASES.md`

- [ ] **Step 1: PRD v3 (normative)**
  - Add `upload.ingest` to the §8 job-type table with `Emits: extract.doc, validate.doc`.
  - Add to the topology diagram: `↘ /upload → upload.ingest` entering at `extract.doc` alongside `backfill.scan` / `email.poll`.
  - Add the dedupe-key convention `upload:{upload_id}` to the conventions table.

- [ ] **Step 2: schema-sketch** — add `upload_inbox` (transient spool) + the tag→table access matrix row (`dentalia_api` INSERT-only; worker SELECT/DELETE).

- [ ] **Step 3: job-type-handbook** — add the `upload.ingest` row (`{upload_id}` payload) + handler pseudo-code mirroring the new/seen branches.

- [ ] **Step 4: code-map / runbook / architecture** — new `/upload` route + `app/handlers/upload.py` + `app/handlers/archiving.py` (shared helper) + `UPLOAD_MAX_MB` config + migration 014.

- [ ] **Step 5: PHASES.md** — add the slice (usable before the S1.7 sweep for dead-end resolution; manual precursor to S2.4 `email.poll`); note the `upload.ingest` enum addition against invariant 7.

- [ ] **Step 6: Full suite + drift-check, then commit**

Run: `docker compose --profile test run --rm --build test` (full suite green) and the `drift-check` command (emits match PRD §8 for the new tag).

```bash
git add docs/ PHASES.md
git commit -m "docs: upload.ingest job type (PRD, schema, handbook, ops docs, PHASES)"
```

---

## Self-Review

**Spec coverage:** purpose-both (Task 4 optional group + Task 5 dead-end prefill) · same-as-any-source gate (Task 3 emits into the normal spine; no gate special-casing) · new `upload.ingest` via DB handoff (Tasks 1, 3, 4) · seen-hash C3 link (Task 3 tests) · producer-only grants (Task 1 grant test) · shared helpers vs fetch (Task 2) · guardrails PDF/size (Task 4) · PRD + docs (Task 6). All spec sections map to a task.

**Placeholders:** none — every code step carries real code. Two "confirm against existing file" notes (Task 2 fetch-test private references; Task 5 exact `manual_task` columns/resolve status) are verification instructions, not deferred design — the surrounding code is concrete.

**Type consistency:** `handle_upload_ingest(conn, job, *, store=None) -> dict` used identically in Task 3 tests and impl; `archiving.*` signatures match Task 2's Produces block and their Task 3 call sites; `upload_inbox` columns match across Tasks 1/3/4; dedupe keys `upload:{id}` / `extract:{hash}` / `validate:{hash}:{rev}:{group}` consistent across tasks.
