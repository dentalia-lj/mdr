# Pre-Import Foundations Implementation Plan

**Goal:** Make the corpus import survivable and reviewable — a durable archive, a record of what we parsed, a record of what every job did, declarations linked to the certificates they cite, older versions filed rather than queued, and a UI that can actually show which certificate is on which item.

**Architecture:** Seven slices, in dependency order, splitting into a backend half and a UI half. Task 1 gives the archive a real home and a served URL. Task 2 stores the parsed text of every document in Postgres so extraction, validation and human search stop depending on re-reading PDFs. Task 3 turns handler return values (today computed and discarded) into a persisted, standardized result envelope plus a closed-vocabulary anomaly ledger, with the view that makes it readable. Task 4 resolves the certificate a Declaration of Conformity cites into a real registry link, so DoCs — which under MDR Annex IV carry no expiry of their own — inherit a renewal date from the certificate they reference. Task 5 files an unambiguously older document straight into the superseded chain instead of costing a reviewer a decision, which is what keeps the backfill from flooding the manual queue. Tasks 6 and 7 make the registry visible: item and document browse and detail, an expiry board grouped by manufacturer, and a document-centric in-flight count.

**Scope rule used:** everything here is either destructive if deferred (files lost, diagnostics lost, fetch-order-dependent resolution) or required to verify that the import worked. Anything that is merely useful later is in the follow-on list at the bottom.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL, no ORM), PyMuPDF, FastAPI + Jinja + HTMX, Postgres 16, Docker Compose, pytest against a real Postgres.

## Status

Updated as each task closes. The per-step checkboxes below are the implementer's working list; this block is the durable record. Branch `s1.8-vendor-master`.

| Task | State |
|---|---|
| 1 — Durable archive volume + served `archive_url` | **done**, reviewed clean after 1 fix round |
| 2 — Parsed document text per content hash (migration 018) | **done**, reviewed clean after 1 fix round |
| 3 — Job results + anomaly ledger + `/data-quality` (migration 019) | **done**, reviewed clean after 1 fix round |
| 4 — Cited-certificate resolution, inherited expiry (migration 020) | **done**, reviewed clean, no fix round |
| 5 — Auto-file unambiguously older documents | **done**, reviewed clean after 1 fix round (2 Critical) |
| 6 — Items and documents browse + detail | **done**, re-reviewed clean after 1 fix round (5 Important) |
| 7 — Expiry board + in-flight view | **done**, reviewed clean (1 Important + 1 Minor, both fixed) |
| Final whole-branch review | **done** — 5 parallel reviews + independent verification of both Criticals; 2 write-side fixes landed, the rest deferred to followups |

Final review outcome (2026-08-10, reports in `.superpowers/sdd/.../final-review-*.md`):
- **2 Critical, both CONFIRMED on independent re-review after adversarial refutation, both mis-stated by the first reviewer in ways that changed the fix.** C1 (a DoC can inherit its renewal date from a *rejected* certificate) has four unfiltered joins, not the one reported — `report.py:36` plus `web/app.py:501`, `:533`, `:638` — and the reviewer's proposed handler-side fix was rejected as unsafe because it would destroy fetch-order independence. C2 (a production document stamped `superseded_by`) is reachable by a simpler same-group sequence than the cross-group one claimed, and reaches external consumers through `/api/documents/{doc_id}`; downgraded to Important.
- **Test integrity: clean.** No new fake tests on the branch; the four earlier ones were independently re-verified as genuinely fixed against production code, not just per the ledger.
- **Fixed before the backfill** — the only two findings that write bad data during an import: `date-insane` now blocks the auto-supersede fast path (`validity_from` is excluded from LLM escalation, so T0's flat confidence always cleared the threshold however wrong the value was, and a scanned corpus produces exactly that failure), and the auto-supersede UPDATE now requires `status='superseded'`, with the withheld write reported on the job result.
- **Everything else is read-side or cosmetic** and can be fixed after the import with no data repair: C1's four joins, the `/documents` page disagreements, the API/view mismatch, an unbounded `page` and a `%00` 500, the `text_store` re-parse (a cost multiplier, not a correctness bug), and ~15 minors.

Deferred minors awaiting final-review triage:
- `.env.example` has no `WEB_ARCHIVE_ROOT` line unlike other `Web` fields; compose sets it explicitly, so no functional impact (Task 1).
- `text_store.store_text` returns the same dict for an open-failure and a confirmed scan; only the presence of a DB row disambiguates them. Task 3 folds this dict into the job-result envelope, so decide there (Task 2).
- `tests/conftest.py` `connect_test` does not truncate `document_text`; the web route test commits a row that persists for the rest of the session. Same class as the existing `[playbooks-conftest-truncate]` followup (Task 2).
- `test_document_text_route_404s_on_unknown_hash` passes even when the route is absent, since unmatched routes also 404. Only meaningful paired with its happy-path sibling (Task 2).
- `test_is_idempotent_on_the_same_hash` cannot distinguish `DO UPDATE` from `DO NOTHING`, because both calls use identical input (Task 2).
- `data_anomaly.kind` has no DB-level CHECK; the closed vocabulary is enforced only by `Result.anomaly()` in Python. Inherent to the plan's design (plain text column, not a Postgres enum like `job_type`), not an implementer defect (Task 3).
- `cert_reference_unresolved` is a producer-less member of the closed `ANOMALY_KINDS` vocabulary — the brief promised it, the brief's own code never emitted it. Decide: wire it into `handle_validate_doc`'s return, or drop it. Logged as `[task4-cert-anomaly]` (Task 4).
- `_resolve_cited_certificate`'s `manufacturer` parameter and `cert_from` return value are unused (Task 4).
- `item_document_production` still selects `d.validity_to` raw, not the cert-inherited `COALESCE`. Logged as `[task4-cert-view]`. Task 6 fixed the two UI pages but not this view, so the read API can still surface a null DoC expiry where a cited certificate supplies a date (Task 4).
- `_backresolve_citing_docs` runs regardless of the new document's status, so an EC/ISO candidate landing as `superseded` can still back-resolve citing DoCs to itself. Predates Task 5; Task 5 makes it more reachable (Task 5).
- `documents.html`'s type/status filter dropdowns are hardcoded, mirroring `web/app.py`'s `JOB_TYPES` pattern, but have no equivalent of `test_job_type_lists_match_db_enum` to keep them in sync with the schema (Task 6).
- `/items`' three-bucket partition is not exhaustive over every reachable `(document.status, item_document.status)` pair: a pre-existing resurrection path in `_upsert_document` can, in a narrow sequence, leave a rejected link invisible to all three counts. Not introduced by Task 6 (Task 6 re-review).

Incident on this branch, for the final review to weigh (2026-08-07):
- Task 6's fix was interrupted between breaking and restoring a proof mutation, and a concurrent `add -all` from the shared checkout committed the still-broken tree — so `0 AS item_count` shipped to the branch and `/documents` reported zero covered items for every document until the follow-up fix. Second occurrence of the shared-worktree collision; both commits misdescribe their contents. The preventive: run proof mutations against a scratch copy rather than the working file.

Corpus facts that size this plan's open questions (measured 2026-08-06):
- The corpus is genuinely medical-device material: of ~1,303 PDFs counted, 65% cite MDR or MDD and only 13 (1%) are non-MD only. (Note the count: a case-sensitive `*.pdf` glob yields 1,303 and misses four `.PDF` files; the true figure is **1,307**, per the 2026-07-31 audit which fixed exactly this glob bug in `backfill.scan`.)
- `DENSTPLY/` alone is 405 PDFs, 31% of the corpus, and **201 of them are image-only scans**. That is half of the single largest folder needing T2 vision, and it is the concrete number behind this plan's open question on whether to pay for vision transcripts. Task 2 records each of these as `source='none'`; Task 3 counts them as the `no_text_layer` anomaly.

Known divergence, not yet scheduled (needs Denis):
- PHASES.md has no session for this plan **or** for the vendor-master slice that precedes it on this branch. Its last defined session, S1.8, is the already-merged web-upload work; the branch name is a collision, not that session.
- `vendor_master` (migration 016) is in neither `dentalia-schema-sketch.md` nor its §7 access matrix. `document_text` was added correctly; this one was missed.

## Global Constraints

- **Branch:** `s1.8-vendor-master`. It is the live line of work and already carries migrations 016/017. Do not start from `master`.
- **Migrations:** numbered `.sql` in `migrations/`, applied in ascending filename order by `app/db.py`. Next free numbers are **018, 019, 020**. Never renumber an applied file.
- **New tables need an explicit `GRANT SELECT ... TO dentalia_api;`** — migration 007's blanket grant does not reach tables created later. Precedent: 009, 010, 012, 013, 016.
- **Invariant 1:** only `gate.candidate` / `gate.apply` handlers write `document`, `item_document`, `evidence`. Every other table is fair game for its owning handler.
- **Invariant 6:** unchanged content is never re-fetched or re-parsed.
- **Invariant 7:** job types are a closed enum. **This plan adds no job type.**
- **Invariant 11:** nothing downstream of INGEST/FETCH may know which adapter implementation is live.
- Python is synchronous throughout. No asyncio.
- Tests run against a real Postgres. Never mock Postgres.
- Match the surrounding file's prose and comment style. **There is no em-dash prohibition on code, comments, docs or templates.** An earlier revision of this plan carried one by mistake, promoted from a rule that governs assistant prose rather than the codebase; the tree already holds roughly 1,550 em-dashes written by prior sessions, including in CLAUDE.md and PHASES.md. Reviewers must not raise em-dash findings, and the ones raised against Tasks 1 and 3 are void.
- Commit messages: lowercase `prefix: statement`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `docker-compose.yml` | named `archive_data` volume mounted into `worker` (rw) and `web` (ro) | 1 |
| `app/config.py` | `Web.archive_root`; startup warning on a relative `storage.local_root` | 1 |
| `web/app.py` | `GET /archive/{path}` file serving; `GET /documents/{hash}/text` | 1, 2 |
| `migrations/018_document_text.sql` | `document_text` table | 2 |
| `app/extract/text_store.py` | build and persist parsed text per content hash | 2 |
| `app/handlers/extract.py` | call the text store once per extraction | 2 |
| `migrations/019_job_result.sql` | `job.result` column + `data_anomaly` table | 3 |
| `app/results.py` | the standard result envelope + closed anomaly vocabulary | 3 |
| `app/queue.py` | `finish()` persists the envelope | 3 |
| `app/workers/runner.py` | pass the handler return value into `finish()` | 3 |
| `web/app.py` | `GET /data-quality` anomaly view | 3 |
| `web/templates/data_quality.html` | the question list for BC/IT | 3 |
| `migrations/020_cert_reference.sql` | `document.cert_doc_id` | 4 |
| `app/handlers/validate.py` | resolve a cited certificate; auto-file older candidates | 4, 5 |
| `app/handlers/gate.py` | persist `cert_doc_id`, back-resolve, write the superseded chain | 4, 5 |
| `app/handlers/report.py` | expiry scan follows `cert_doc_id` | 4 |
| `web/app.py` | `/items`, `/items/{ref}`, `/documents`, `/documents/{id}` | 6 |
| `web/templates/items.html`, `item_detail.html`, `documents.html`, `document_detail.html` | registry browse and detail | 6 |
| `web/app.py` | `/expiry`, `/inflight` | 7 |
| `web/templates/expiry.html`, `inflight.html` | renewal front end, document-centric pipeline view | 7 |
| `web/templates/base.html` | nav entries for the new pages | 3, 6, 7 |
| `CLAUDE.md` | the "status board is the whole UI" rule no longer holds | 5, 7 |

---

### Task 1: Give the archive a durable home and a resolvable URL

Today `STORAGE_LOCAL_ROOT` defaults to the relative `./archive` (`app/config.py:129`), **no compose service mounts it**, and `STORAGE_BASE_URL` is empty so `LocalFsStore.put` returns a `file://` URI (`app/adapters/storage.py:59`). Inside the worker that resolves to `/app/archive` on the container's ephemeral layer: every archived file dies with the container while `document.archive_url` rows survive pointing at it. `web` is a different container and cannot read the worker's filesystem at all. Nothing has been fetched yet, so this has never bitten. It must be fixed before anything is.

**Files:**
- Modify: `docker-compose.yml` (worker service volumes, web service volumes, top-level `volumes:` at line 164)
- Modify: `app/config.py` (add `archive_root` to the `Web` section; warn on relative storage root)
- Modify: `web/app.py` (new route)
- Modify: `.env.example`
- Test: `tests/test_storage_adapter.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `LocalFsStore(root, base_url)` and `.put(body, path) -> str` from `app/adapters/storage.py`, unchanged.
- Produces: `archive_url` values of the form `/archive/{layout_path}` instead of `file://...`. Task 2 relies on `Web.archive_root` existing.

- [ ] **Step 1: Write the failing test for a served archive URL**

Add to `tests/test_storage_adapter.py`:

```python
def test_put_with_base_url_returns_served_path(tmp_path):
    store = LocalFsStore(str(tmp_path), base_url="/archive")
    url = store.put(b"%PDF-1.4 body", "VOCO/doc/abc123__x.pdf")
    assert url == "/archive/VOCO/doc/abc123__x.pdf"
    assert (tmp_path / "VOCO/doc/abc123__x.pdf").read_bytes() == b"%PDF-1.4 body"


def test_put_without_base_url_still_returns_file_uri(tmp_path):
    store = LocalFsStore(str(tmp_path), base_url="")
    url = store.put(b"x", "A/doc/h__f.pdf")
    assert url.startswith("file://")
```

- [ ] **Step 2: Run to confirm the first passes and pins current behavior**

Run: `pytest tests/test_storage_adapter.py -k base_url -v`
Expected: PASS both. `LocalFsStore` already supports this; these tests exist to lock the contract before compose starts depending on it. If either fails, stop and read `app/adapters/storage.py:52-59` before continuing.

- [ ] **Step 3: Write the failing test for the web archive route**

Add to `tests/test_web.py`:

**Do not use `monkeypatch.setenv` here.** `web/app.py`'s routes close over the `Web` config object passed to `create_app(cfg)` (see `create_app` at `web/app.py:392` and the existing `client` fixture at `tests/test_web.py:35`), so environment variables never reach them in tests. Build a client with an explicit `archive_root` instead:

```python
def _archive_client(tmp_path):
    """TestClient whose archive root is a temp dir. Mirrors the module's
    `client` fixture, which injects Web(...) directly rather than via env."""
    root = tmp_path / "archive"
    root.mkdir(parents=True, exist_ok=True)
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              archive_root=str(root))
    return TestClient(create_app(cfg)), root


def test_archive_route_serves_an_archived_file(test_db_url, tmp_path):
    client, root = _archive_client(tmp_path)
    (root / "VOCO" / "doc").mkdir(parents=True)
    (root / "VOCO" / "doc" / "abc__x.pdf").write_bytes(b"%PDF-1.4 hello")
    resp = client.get("/archive/VOCO/doc/abc__x.pdf")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 hello"


def test_archive_route_rejects_path_traversal(test_db_url, tmp_path):
    client, root = _archive_client(tmp_path)
    (tmp_path / "secret.txt").write_text("do not serve me")
    resp = client.get("/archive/../secret.txt")
    assert resp.status_code == 404


def test_archive_route_404s_on_missing_file(test_db_url, tmp_path):
    client, _ = _archive_client(tmp_path)
    assert client.get("/archive/nope.pdf").status_code == 404
```

`Web`, `API_TEST_URL`, `TestClient` and `create_app` are all already imported at the top of `tests/test_web.py`. The `test_db_url` parameter is there to guarantee the test database exists, matching what the `client` fixture depends on.

- [ ] **Step 4: Run to verify it fails**

Run: `pytest tests/test_web.py -k archive_route -v`
Expected: FAIL with 404 on the first test (route not registered).

- [ ] **Step 5: Add `archive_root` to the Web config section**

In `app/config.py`, in the `Web` dataclass add the field:

```python
    archive_root: str = "./archive"
```

and in the `Web` loader block alongside the existing `_str("WEB_...")` calls:

```python
        archive_root=_str(
            "WEB_ARCHIVE_ROOT", _dig(t, "web", "archive_root"), "./archive"
        ),
```

- [ ] **Step 6: Implement the route**

In `web/app.py`, near the other `@app.get` routes:

```python
    @app.get("/archive/{path:path}")
    def archive_file(path: str):
        """Serve an archived document. The archive is hash-addressed and
        read-only here; `web` mounts the volume ro (invariant 1 is about the
        registry, but the same posture applies to bytes we did not fetch)."""
        root = pathlib.Path(cfg.archive_root).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)
```

Read `cfg.archive_root`, **not** `load_config()`. `cfg` is the `Web` object `create_app` closes over (`web/app.py:400`), and every other route in the module reads config that way (`cfg.imports_dir`, `cfg.upload_max_mb`). Using `load_config()` would ignore the injected test config and silently read the process environment.

Add the imports `pathlib`, `from fastapi import HTTPException` and `from fastapi.responses import FileResponse` if not already present.

- [ ] **Step 7: Run the route tests to verify they pass**

Run: `pytest tests/test_web.py -k archive_route -v`
Expected: PASS all three.

- [ ] **Step 8: Mount the volume in compose**

In `docker-compose.yml`, add to the `worker` service (which currently has no `volumes:` key at all):

```yaml
    volumes:
      - archive_data:/archive
```

Add to the `web` service's existing `volumes:` list (currently holding the `imports` bind at line 114):

```yaml
      - archive_data:/archive:ro
```

Add to the top-level `volumes:` block (line 164, next to `caddy_data`):

```yaml
  # The document archive. Hash-addressed, append-only, 10-year retention:
  # end-state output 2. Worker writes, web serves read-only.
  archive_data:
```

Set the environment for both services so the container uses the mount and returns served URLs:

```yaml
      STORAGE_LOCAL_ROOT: /archive
      STORAGE_BASE_URL: /archive
      WEB_ARCHIVE_ROOT: /archive
```

(`STORAGE_*` on `worker`, `WEB_ARCHIVE_ROOT` on `web`.)

- [ ] **Step 9: Warn when the archive root is relative**

A relative root silently means "wherever the process happened to start". In `app/config.py`, immediately after the `storage = Storage(...)` block (around line 450, just below where `adapters` is constructed):

```python
    if adapters.storage == "local" and not os.path.isabs(storage.local_root):
        log.warning(
            "storage.local_root %r is relative: archived files land under the "
            "process working directory and are lost on container restart. "
            "Set STORAGE_LOCAL_ROOT to an absolute path backed by a volume.",
            storage.local_root,
        )
```

If `app/config.py` has no module-level logger yet, add `import logging` and `log = logging.getLogger(__name__)` at the top. `os` is already imported.

- [ ] **Step 10: Update `.env.example`**

Replace the `STORAGE_LOCAL_ROOT=./archive` line with:

```
# Absolute path inside the container, backed by the `archive_data` volume.
# A relative value works on the host but is LOST on container restart.
STORAGE_LOCAL_ROOT=/archive
# Serve archived files through the web UI. Empty gives file:// URIs that
# nothing downstream can open.
STORAGE_BASE_URL=/archive
```

- [ ] **Step 11: Run the full suite**

Run: `pytest -q`
Expected: all pass. If `tests/test_storage_adapter.py::test_factory_selects_local_default` fails, the config default was changed by mistake — the code default stays `./archive`, compose supplies the absolute value.

- [ ] **Step 12: Verify the volume end to end**

Run:

```bash
docker compose up -d postgres migrate worker web caddy
docker compose exec worker sh -c 'mkdir -p /archive/T/doc && echo hi > /archive/T/doc/x.pdf'
docker compose restart worker
docker compose exec worker cat /archive/T/doc/x.pdf
curl -u dentalia:dentalia-dev http://127.0.0.1:8000/archive/T/doc/x.pdf
docker compose exec worker rm -rf /archive/T
```

Expected: the file survives the restart, and curl returns `hi`. This is the actual proof the followup asked for; do not skip it.

- [ ] **Step 13: Update the operational docs**

In `docs/architecture.md`, add a row to the compose-services table noting the `archive_data` volume, and correct the data-model heading — it says "Fourteen migrations" and lists 001-014 while `migrations/` will hold 020 after this plan. In `docs/runbook.md`, document `STORAGE_LOCAL_ROOT`, `STORAGE_BASE_URL` and `WEB_ARCHIVE_ROOT`.

- [ ] **Step 14: Close the followups and commit**

In `tasks/followups.md`, move `[archive-not-persisted]` and `[archive-url-unresolvable]` to `## Done` with the date and what closed them.

```bash
git add docker-compose.yml app/config.py web/app.py .env.example tests/test_storage_adapter.py tests/test_web.py docs/architecture.md docs/runbook.md tasks/followups.md
git commit -m "feat: durable archive volume and served archive_url"
```

---

### Task 2: Store the parsed text of every document

`extraction_attempt.fields` holds extracted values, confidence and evidence — never the source text. `pdf.full_text()` is called inline at `app/extract/llm.py:92` and thrown away, so every extraction re-parses the PDF from scratch and no human or query can see what we actually read. Storing it makes re-extraction cheap after a prompt or model change, makes `evidence.verbatim` mechanically checkable against the source, and gives you full-text search over the corpus.

**Files:**
- Create: `migrations/018_document_text.sql`
- Create: `app/extract/text_store.py`
- Create: `tests/test_text_store.py`
- Modify: `app/handlers/extract.py`
- Modify: `web/app.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `app/extract/pdf.py` `open_doc(archive_url)`, `full_text(doc, page_markers=True)`, `is_scan(doc)`. Task 1's served archive.
- Produces: `store_text(conn, content_hash, archive_url) -> dict` returning `{"source": str, "pages": int, "chars": int}`. Task 3 folds that dict into the job result envelope.

- [ ] **Step 1: Write the migration**

Create `migrations/018_document_text.sql`:

```sql
-- Parsed text per archived document, keyed by content hash (not doc_id: text
-- exists from EXTRACT time, before GATE creates a document row). One row per
-- content hash; re-extraction overwrites, since the bytes are immutable.
--
-- `source` records provenance and is load-bearing: 'pdf-text' is a
-- deterministic PyMuPDF read and may be used to verify evidence.verbatim.
-- 'none' means the PDF has no text layer (a scan) and nothing was recovered.
-- Model-produced transcripts are NOT written here today; if that ever changes
-- it gets its own source value and must never be treated as verbatim source.
CREATE TABLE document_text (
  content_hash text PRIMARY KEY,
  source       text NOT NULL CHECK (source IN ('pdf-text', 'none')),
  engine       text,
  pages        int  NOT NULL DEFAULT 0,
  chars        int  NOT NULL DEFAULT 0,
  content      text NOT NULL DEFAULT '',
  built_at     timestamptz NOT NULL DEFAULT now()
);

-- Created after 007's blanket grant, so grant explicitly (precedent 009/012).
GRANT SELECT ON document_text TO dentalia_api;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_text_store.py`:

```python
"""document_text: parsed-text capture per content hash."""
from app.extract.text_store import store_text


def test_stores_text_for_a_text_layer_pdf(conn, fixture_pdf):
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    out = store_text(conn, "hash-komet", str(path))
    assert out["source"] == "pdf-text"
    assert out["chars"] > 0
    row = conn.execute(
        "SELECT source, pages, chars, content FROM document_text WHERE content_hash=%s",
        ("hash-komet",),
    ).fetchone()
    assert row["source"] == "pdf-text"
    assert row["pages"] >= 1
    assert "[[page 1]]" in row["content"]
    assert row["chars"] == len(row["content"])


def test_records_a_scan_as_source_none_rather_than_skipping(conn, fixture_pdf):
    path = fixture_pdf("DENTAURUM/TD_14_Konformitätserklärung_Kl. IIa_2021_05_25.pdf")
    out = store_text(conn, "hash-scan", str(path))
    assert out["source"] == "none"
    assert out["chars"] == 0
    row = conn.execute(
        "SELECT source, content FROM document_text WHERE content_hash=%s", ("hash-scan",)
    ).fetchone()
    assert row is not None, "a scan must leave a row, so we can count how many"
    assert row["content"] == ""


def test_is_idempotent_on_the_same_hash(conn, fixture_pdf):
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    store_text(conn, "hash-idem", str(path))
    store_text(conn, "hash-idem", str(path))
    n = conn.execute(
        "SELECT count(*) AS n FROM document_text WHERE content_hash=%s", ("hash-idem",)
    ).fetchone()["n"]
    assert n == 1
```

Both fixture paths are verified: the KOMET file has a text layer, and the DENTAURUM entry in `tests/fixtures/corpus_manifest.py` carries `"is_scan": True`.

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/test_text_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.extract.text_store'`.

- [ ] **Step 4: Implement the text store**

Create `app/extract/text_store.py`:

```python
"""Parsed-text capture: what EXTRACT actually read out of a PDF.

Written once per content hash at the start of extraction. Three consumers:
re-extraction after a prompt or model change (no re-parse), mechanical
verification of evidence.verbatim against the source, and human/SQL search
over the corpus without opening a file.

Provenance is explicit. 'pdf-text' is a deterministic PyMuPDF read.
'none' is a scan with no text layer: the row is still written, so the count
of unrecoverable documents is a number we have rather than a guess.
"""
from __future__ import annotations

import logging

from app.extract import pdf as pdfutil

log = logging.getLogger(__name__)

ENGINE = "pymupdf"


def store_text(conn, content_hash: str, archive_url: str) -> dict:
    """Parse `archive_url` and upsert its text. Returns a summary dict for the
    job result. Never raises on an unreadable PDF: extraction proper owns that
    failure, and losing the sidecar must not dead-letter a document."""
    try:
        doc = pdfutil.open_doc(archive_url)
    except Exception as exc:
        log.warning("document_text: cannot open %s: %s", archive_url, exc)
        return {"source": "none", "pages": 0, "chars": 0}

    try:
        if pdfutil.is_scan(doc):
            source, content, pages = "none", "", doc.page_count
        else:
            source = "pdf-text"
            content = pdfutil.full_text(doc, page_markers=True)
            pages = doc.page_count
    finally:
        doc.close()

    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (content_hash) DO UPDATE SET "
        "source=EXCLUDED.source, engine=EXCLUDED.engine, pages=EXCLUDED.pages, "
        "chars=EXCLUDED.chars, content=EXCLUDED.content, built_at=now()",
        (content_hash, source, ENGINE, pages, len(content), content),
    )
    return {"source": source, "pages": pages, "chars": len(content)}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_text_store.py -v`
Expected: PASS all three.

- [ ] **Step 6: Call it from the extract handler**

In `app/handlers/extract.py`, inside `handle_extract_doc`, immediately after the payload is read and before the tier ladder runs:

```python
    text_summary = text_store.store_text(conn, p["content_hash"], p["archive_url"])
```

Add `from app.extract import text_store` to the imports, and include `text_summary` in the handler's returned dict under the key `"text"`.

- [ ] **Step 7: Write the failing test for the text view route**

Add to `tests/test_web.py`:

```python
def test_document_text_route_renders_stored_text(client, conn):
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES ('h-web','pdf-text','pymupdf',1,11,'[[page 1]] hi')"
    )
    conn.commit()
    resp = client.get("/documents/h-web/text")
    assert resp.status_code == 200
    assert "[[page 1]] hi" in resp.text


def test_document_text_route_404s_on_unknown_hash(client):
    assert client.get("/documents/nope/text").status_code == 404
```

- [ ] **Step 8: Run to verify it fails**

Run: `pytest tests/test_web.py -k document_text_route -v`
Expected: FAIL with 404 on the first test.

- [ ] **Step 9: Implement the route**

In `web/app.py`:

```python
    @app.get("/documents/{content_hash}/text", response_class=PlainTextResponse)
    def document_text(content_hash: str):
        """What EXTRACT actually read out of this PDF. Plain text so it can be
        searched, diffed and pasted without a viewer."""
        with _conn() as conn:
            row = conn.execute(
                "SELECT content, source FROM document_text WHERE content_hash=%s",
                (content_hash,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no stored text")
        if row["source"] == "none":
            return "(no text layer: this document is a scan)"
        return row["content"]
```

Add `from fastapi.responses import PlainTextResponse` to the imports. `_conn()` is the module's existing connection context manager (`web/app.py:410`).

- [ ] **Step 10: Run the route tests**

Run: `pytest tests/test_web.py -k document_text_route -v`
Expected: PASS both.

- [ ] **Step 11: Run the full suite and commit**

Run: `pytest -q`
Expected: all pass.

```bash
git add migrations/018_document_text.sql app/extract/text_store.py app/handlers/extract.py web/app.py tests/test_text_store.py tests/test_web.py
git commit -m "feat: store parsed document text per content hash"
```

- [ ] **Step 12: Sync the docs**

Add `018_document_text.sql` to the migrations table in `docs/architecture.md`, add `app/extract/text_store.py` to `docs/code-map.md`, and add the `document_text` table plus its access-matrix row to `docs/dentalia-schema-sketch.md` §7. Commit as `docs: document_text table and text store`.

---

### Task 3: Persist what every job did, and log anomalies to a closed vocabulary

Every handler builds a report dict of counts and samples, returns it, and it goes to a log line and vanishes — there is no persisted job-result column anywhere in the codebase (`app/workers/runner.py:63` calls `queue.finish(conn, job["id"])` and discards the return value). That is why the 61% blank device class, the Slovene prose in `mfr_ref`, the odd manufacturer code shapes and the Komet stock discrepancy all had to be found by hand and written into markdown files. This task makes the system report on itself.

**Files:**
- Create: `migrations/019_job_result.sql`
- Create: `app/results.py`
- Create: `tests/test_results.py`
- Modify: `app/queue.py:94-96`
- Modify: `app/workers/runner.py:62-63`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Result` (class), `ANOMALY_KINDS` (frozenset), `record_anomalies(conn, kind_rows)`. `queue.finish(conn, job_id, result=None)` — the added third parameter is keyword-optional so every existing call site keeps working.

- [ ] **Step 1: Write the migration**

Create `migrations/019_job_result.sql`:

```sql
-- What a job actually did. Handlers already compute counts and samples and
-- return them; until now the runner discarded the value. Written in the same
-- transaction that finishes the job, so a result and its terminal status can
-- never disagree.
ALTER TABLE job ADD COLUMN result jsonb;

-- Standing ledger of data weirdness. Append-only in practice: a repeat
-- observation bumps last_seen and the counter rather than inserting again,
-- so the table stays the size of the problem, not the size of the corpus.
--
-- `kind` is a CLOSED vocabulary mirrored in app/results.py ANOMALY_KINDS.
-- Adding a kind is a deliberate act in both places, like a job type.
CREATE TABLE data_anomaly (
  id         bigserial PRIMARY KEY,
  kind       text NOT NULL,
  subject    text NOT NULL,          -- item_ref, manufacturer code, content_hash
  catalogue  text,
  detail     jsonb,
  seen_count int  NOT NULL DEFAULT 1,
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX data_anomaly_key ON data_anomaly (kind, subject);
CREATE INDEX data_anomaly_kind_idx ON data_anomaly (kind);

GRANT SELECT ON data_anomaly TO dentalia_api;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_results.py`:

```python
"""Standard job-result envelope and the anomaly ledger."""
import pytest

from app.results import ANOMALY_KINDS, Result, record_anomalies


def test_envelope_has_a_stable_shape():
    r = Result()
    r.count("seen", 10)
    r.count("seen", 5)
    r.sample("skipped", {"item_ref": "A1"})
    r.note("md-unknown processing was enabled")
    out = r.as_dict()
    assert out["counts"] == {"seen": 15}
    assert out["samples"]["skipped"] == [{"item_ref": "A1"}]
    assert out["notes"] == ["md-unknown processing was enabled"]
    assert out["anomalies"] == []


def test_samples_are_bounded_to_fifty():
    r = Result()
    for i in range(120):
        r.sample("skipped", {"item_ref": f"A{i}"})
    out = r.as_dict()
    assert len(out["samples"]["skipped"]) == 50
    assert out["counts"]["skipped_sampled_of"] == 120


def test_unknown_anomaly_kind_is_rejected():
    r = Result()
    with pytest.raises(ValueError, match="unknown anomaly kind"):
        r.anomaly("something_i_invented", subject="A1")


def test_record_anomalies_upserts_and_counts(conn):
    r = Result()
    r.anomaly("md_class_blank", subject="A1", catalogue="LJ")
    r.anomaly("md_class_blank", subject="A1", catalogue="LJ")
    record_anomalies(conn, r.as_dict()["anomalies"])
    record_anomalies(conn, r.as_dict()["anomalies"])
    row = conn.execute(
        "SELECT kind, subject, seen_count FROM data_anomaly WHERE subject='A1'"
    ).fetchone()
    assert row["kind"] == "md_class_blank"
    assert row["seen_count"] == 4


def test_every_kind_is_snake_case_and_documented():
    assert ANOMALY_KINDS
    for kind in ANOMALY_KINDS:
        assert kind.islower() and " " not in kind
```

Add to `tests/test_queue.py`:

```python
def test_finish_persists_the_result_envelope(conn):
    job_id = queue.enqueue(conn, "noop", {}, dedupe_key="res-1")
    queue.claim(conn, "w1")
    queue.finish(conn, job_id, result={"counts": {"seen": 3}})
    row = conn.execute("SELECT status, result FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["result"]["counts"]["seen"] == 3


def test_finish_without_a_result_leaves_it_null(conn):
    job_id = queue.enqueue(conn, "noop", {}, dedupe_key="res-2")
    queue.claim(conn, "w1")
    queue.finish(conn, job_id)
    row = conn.execute("SELECT result FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["result"] is None
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_results.py tests/test_queue.py -k "result or anomal" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.results'` and a TypeError on the extra `finish` argument.

- [ ] **Step 4: Implement the envelope**

Create `app/results.py`:

```python
"""The one shape every handler returns, and the closed vocabulary of data
anomalies.

Standardization is the point. Before this, each handler invented its own
report dict and the runner threw it away, so 'what went in and what came out'
was only answerable by hand-analysis. Counts roll up, samples are bounded so
a 19,000-row ingest cannot write a 19,000-row blob, and anomalies land in a
standing ledger keyed by (kind, subject).

ANOMALY_KINDS is closed on purpose, exactly like the job-type enum: a new
kind is added here and in migration 019's comment, deliberately, or not at
all. An unrecognised kind raises rather than being silently accepted.
"""
from __future__ import annotations

import json

SAMPLE_CAP = 50

#: Closed vocabulary. Keep in sync with migrations/019_job_result.sql.
ANOMALY_KINDS = frozenset({
    "md_class_blank",         # BC device-class column empty for an item
    "mfr_ref_missing",        # no manufacturer article number: REF gate has no key
    "mfr_ref_prose",          # mfr_ref holds a remark, not a code
    "manufacturer_code_blank",
    "code_shape_unexpected",  # manufacturer code breaks the 3/5-digit pattern
    "reclassified_non_md",    # mirrored item BC later declassified
    "blocked_item",           # Blokirano=1
    "no_text_layer",          # scan: nothing recoverable without vision
    "cert_reference_unresolved",  # DoC cites a certificate we do not hold
})


class Result:
    """Accumulates a job's report. `as_dict()` is what the handler returns."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._samples: dict[str, list] = {}
        self._sample_totals: dict[str, int] = {}
        self._notes: list[str] = []
        self._anomalies: list[dict] = []

    def count(self, key: str, n: int = 1) -> None:
        self._counts[key] = self._counts.get(key, 0) + n

    def sample(self, key: str, row: dict) -> None:
        """Record an example. Bounded: the count is always exact, the sample
        is capped, and the total is reported so a truncation is never silent."""
        self._sample_totals[key] = self._sample_totals.get(key, 0) + 1
        bucket = self._samples.setdefault(key, [])
        if len(bucket) < SAMPLE_CAP:
            bucket.append(row)

    def note(self, text: str) -> None:
        self._notes.append(text)

    def anomaly(self, kind: str, *, subject: str, catalogue: str | None = None,
                detail: dict | None = None) -> None:
        if kind not in ANOMALY_KINDS:
            raise ValueError(
                f"unknown anomaly kind {kind!r}: add it to ANOMALY_KINDS and to "
                "migrations/019_job_result.sql, or use an existing kind"
            )
        self._anomalies.append({
            "kind": kind, "subject": subject,
            "catalogue": catalogue, "detail": detail or {},
        })

    def as_dict(self) -> dict:
        counts = dict(self._counts)
        for key, total in self._sample_totals.items():
            if total > SAMPLE_CAP:
                counts[f"{key}_sampled_of"] = total
        return {
            "counts": counts,
            "samples": self._samples,
            "notes": self._notes,
            "anomalies": self._anomalies,
        }


def record_anomalies(conn, anomalies: list[dict]) -> int:
    """Upsert anomalies into the standing ledger. Repeat observations bump
    seen_count and last_seen instead of inserting, so the table stays the size
    of the problem rather than the size of the corpus."""
    for a in anomalies:
        conn.execute(
            "INSERT INTO data_anomaly (kind, subject, catalogue, detail) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (kind, subject) DO UPDATE SET "
            "seen_count = data_anomaly.seen_count + 1, last_seen = now(), "
            "detail = EXCLUDED.detail",
            (a["kind"], a["subject"], a.get("catalogue"), json.dumps(a.get("detail") or {})),
        )
    return len(anomalies)
```

- [ ] **Step 5: Persist the envelope in `finish`**

In `app/queue.py`, replace `finish` (lines 94-96):

```python
def finish(conn: psycopg.Connection, job_id: int, result: dict | None = None) -> None:
    """Mark a job done (terminal - frees its dedupe key), storing its result
    envelope. Same transaction as the status flip, so a done job and its
    report can never disagree."""
    conn.execute(
        "UPDATE job SET status='done', result=%s WHERE id=%s",
        (json.dumps(result) if result is not None else None, job_id),
    )
```

Add `import json` at the top of `app/queue.py` if absent.

- [ ] **Step 6: Pass the handler's return value through the runner**

In `app/workers/runner.py`, replace line 63:

```python
                queue.finish(conn, job["id"], result=result if isinstance(result, dict) else None)
                if isinstance(result, dict) and result.get("anomalies"):
                    record_anomalies(conn, result["anomalies"])
```

Add `from app.results import record_anomalies` to the imports. Both statements sit inside the existing `with conn.transaction():` block so the result, the anomalies and the status flip commit together.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_results.py tests/test_queue.py -v`
Expected: PASS.

- [ ] **Step 8: Emit the anomalies INGEST already knows about**

In `app/handlers/ingest.py`, replace the ad-hoc report dict with a `Result`. For each row, where the handler already detects the condition, call:

```python
        if md_flag is None:
            r.anomaly("md_class_blank", subject=item_ref, catalogue=catalogue)
        if not mfr_ref:
            r.anomaly("mfr_ref_missing", subject=item_ref, catalogue=catalogue)
        elif "!" in mfr_ref:
            r.anomaly("mfr_ref_prose", subject=item_ref, catalogue=catalogue,
                      detail={"value": mfr_ref})
        if not manufacturer_raw:
            r.anomaly("manufacturer_code_blank", subject=item_ref, catalogue=catalogue)
```

Keep every existing counter by calling `r.count(...)` with the same key names the handler returns today, so nothing downstream that reads those counts breaks. The `"!"` test is the deliberate rule from the vendor-master analysis: 378 values, and nothing cleverer, because 652 legitimate codes contain a space.

In `app/extract/text_store.py`, the `source == "none"` branch cannot reach the `Result` directly; instead have `handle_extract_doc` raise the anomaly from the summary it already receives:

```python
    if text_summary["source"] == "none":
        r.anomaly("no_text_layer", subject=p["content_hash"])
```

- [ ] **Step 9: Write the ingest anomaly test**

Add to `tests/test_ingest_handler.py`:

```python
def test_ingest_records_blank_device_class_as_an_anomaly(conn):
    records = [
        {"No": "U1", "Description": "UNCLASSIFIED", "VendorItemNo": "R3",
         "ManufacturerCode": "011", "MedicalDeviceClass": "", "UDI": None},
        {"No": "K1", "Description": "KNOWN", "VendorItemNo": "R4",
         "ManufacturerCode": "011", "MedicalDeviceClass": "RAZRED IIA", "UDI": None},
    ]
    _run(conn, "odata", source="bc_odata", records=records)
    rows = conn.execute(
        "SELECT kind, subject FROM data_anomaly WHERE kind='md_class_blank'"
    ).fetchall()
    assert [r["subject"] for r in rows] == ["U1"]


def test_ingest_records_prose_in_mfr_ref_as_an_anomaly(conn):
    records = [
        {"No": "P1", "Description": "WIDGET", "VendorItemNo": "NE BO VEC NA ZALOGI!",
         "ManufacturerCode": "011", "MedicalDeviceClass": "RAZRED IIA", "UDI": None},
        {"No": "P2", "Description": "BUR", "VendorItemNo": "104 H251EF 060",
         "ManufacturerCode": "011", "MedicalDeviceClass": "RAZRED IIA", "UDI": None},
    ]
    _run(conn, "odata", source="bc_odata", records=records)
    rows = conn.execute(
        "SELECT subject FROM data_anomaly WHERE kind='mfr_ref_prose'"
    ).fetchall()
    assert [r["subject"] for r in rows] == ["P1"], (
        "P2 is a legitimate Komet ISO bur number containing spaces: a space or "
        "letter heuristic would destroy 652 real codes. Only '!' is safe."
    )
```

`_run(conn, ref, *, priority, cfg, records, source, catalogue)` and the injected-record shape are the file's existing pattern (`tests/test_ingest_handler.py:41` and the OData records at line 205). Injecting records avoids depending on CSV header spelling.

- [ ] **Step 10: Write the failing test for the data-quality view**

A ledger nobody can read is half-built. Add to `tests/test_web.py`:

```python
def test_data_quality_page_lists_anomalies_by_kind(client, conn):
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue, seen_count) VALUES "
        "('md_class_blank','A1','LJ',3), ('mfr_ref_missing','A2','LJ',1)"
    )
    conn.commit()
    resp = client.get("/data-quality")
    assert resp.status_code == 200
    assert "md_class_blank" in resp.text
    assert "A1" in resp.text


def test_data_quality_filters_by_kind(client, conn):
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue) VALUES "
        "('md_class_blank','B1','LJ'), ('mfr_ref_missing','B2','LJ')"
    )
    conn.commit()
    resp = client.get("/data-quality?kind=mfr_ref_missing")
    assert "B2" in resp.text
    assert "B1" not in resp.text
```

- [ ] **Step 11: Run to verify it fails**

Run: `pytest tests/test_web.py -k data_quality -v`
Expected: FAIL with 404.

- [ ] **Step 12: Implement the data-quality view**

In `web/app.py`:

```python
    @app.get("/data-quality", response_class=HTMLResponse)
    def data_quality(request: Request, kind: str = ""):
        """Standing ledger of catalogue weirdness. This page is the question
        list for BC/IT: every row is something the export did that we could not
        interpret, counted rather than guessed at."""
        with _conn() as conn:
            summary = conn.execute(
                "SELECT kind, count(*) AS subjects, sum(seen_count) AS observations "
                "FROM data_anomaly GROUP BY kind ORDER BY subjects DESC"
            ).fetchall()
            rows = conn.execute(
                "SELECT kind, subject, catalogue, detail, seen_count, last_seen "
                "FROM data_anomaly WHERE (%s = '' OR kind = %s) "
                "ORDER BY last_seen DESC LIMIT 500",
                (kind, kind),
            ).fetchall()
        return templates.TemplateResponse(
            "data_quality.html",
            {"request": request, "summary": summary, "rows": rows, "kind": kind},
        )
```

Create `web/templates/data_quality.html` extending `base.html`, rendering the summary as a table of kind / distinct subjects / total observations with each kind linking to `?kind=<kind>`, and the detail rows as kind / subject / catalogue / count / last seen. Follow the markup and CSS classes already used in `web/templates/status.html`. Add a "Data quality" link to the nav in `base.html`.

- [ ] **Step 13: Run the view tests**

Run: `pytest tests/test_web.py -k data_quality -v`
Expected: PASS both.

- [ ] **Step 14: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 15: Commit**

```bash
git add migrations/019_job_result.sql app/results.py app/queue.py app/workers/runner.py app/handlers/ingest.py app/handlers/extract.py web/app.py web/templates/ tests/
git commit -m "feat: persist job results and log data anomalies to a closed vocabulary"
```

- [ ] **Step 16: Sync the docs**

Add migration 019 to `docs/architecture.md`, `app/results.py` to `docs/code-map.md`, and `job.result` + `data_anomaly` to `docs/dentalia-schema-sketch.md` with an access-matrix row. Note in the schema sketch that `ANOMALY_KINDS` is the closed vocabulary and lives in `app/results.py`. Commit as `docs: job result envelope and anomaly ledger`.

---

### Task 4: Resolve the certificate a declaration cites

MDR Annex IV requires only "place and date of issue" on a Declaration of Conformity: **no expiry date is required and most DoCs carry none.** What Annex IV item 8 does require, where a notified body was involved, is identification of the certificate(s) issued — and MDR Article 56 caps those at five years. So a DoC's real renewal date lives on the certificate it cites. We already extract `cert_number` and `referenced_docs[]`, and `document.referenced_doc_id` exists, but it is only ever populated from `related_doc_id`, which is the C6 cross-regulation sibling — a different relationship. Nothing resolves a cited certificate into a registry link.

Best-effort by design: a document whose certificate we do not hold resolves to null, is counted, and nothing fails.

**Files:**
- Create: `migrations/020_cert_reference.sql`
- Modify: `app/handlers/validate.py`
- Modify: `app/handlers/gate.py:103-130`
- Modify: `app/handlers/report.py:19-35`
- Test: `tests/test_validate_handler.py`, `tests/test_gate_candidate_handler.py`, `tests/test_report_handler.py`

**Interfaces:**
- Consumes: `Result.anomaly` from Task 3 for `cert_reference_unresolved`.
- Produces: payload key `cert_doc_id` on `gate.candidate` (additive, per the additive-only payload rule); `document.cert_doc_id` column; `expiring_documents` gains inherited expiries.

- [ ] **Step 1: Write the migration**

Create `migrations/020_cert_reference.sql`:

```sql
-- The notified-body certificate a Declaration of Conformity cites.
--
-- Distinct from referenced_doc_id, which holds the C6 cross-regulation sibling
-- (the MDR and MDD versions of one document, parallel chains, never a
-- supersession). Conflating them would lose one of the two relationships.
--
-- Why it matters: MDR Annex IV requires only a date of ISSUE on a DoC, so most
-- carry no expiry. Article 56 caps notified-body certificates at five years.
-- A DoC therefore inherits its renewal date from the certificate named here.
-- Null is normal and never an error: Class I devices have no certificate at
-- all, and we may simply not hold the cited one yet.
ALTER TABLE document ADD COLUMN cert_doc_id bigint REFERENCES document(doc_id);

CREATE INDEX document_cert_doc_idx ON document (cert_doc_id) WHERE cert_doc_id IS NOT NULL;
CREATE INDEX document_cert_number_idx ON document (cert_number) WHERE cert_number IS NOT NULL;
```

- [ ] **Step 2: Write the failing VALIDATE test**

Add to `tests/test_validate_handler.py`:

```python
def test_doc_citing_a_held_certificate_emits_cert_doc_id(conn):
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','G1 082649 0002','production',"
        "'h-cert','/archive/c.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed(conn, "h-doc", _fields(type="DoC", regulation="MDR",
                                 cert_number="G1 082649 0002",
                                 referenced_docs=["G1 082649 0002"]))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc", "extract_rev": 1,
                                    "group_id": None}})
    payload = _gate_jobs(conn, "h-doc")[0]
    assert payload["cert_doc_id"] == cert_id


def test_doc_citing_an_unheld_certificate_resolves_to_null_and_does_not_fail(conn):
    _seed(conn, "h-doc2", _fields(type="DoC", regulation="MDR",
                                  cert_number="NOT-IN-REGISTRY-1",
                                  referenced_docs=["NOT-IN-REGISTRY-1"]))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc2", "extract_rev": 1,
                                    "group_id": None}})
    payload = _gate_jobs(conn, "h-doc2")[0]
    assert payload["cert_doc_id"] is None
    assert "cert-unresolved" in payload["flags"]
```

`_seed`, `_fields`, `_gate_jobs` and the `vh` module alias are the file's existing helpers (`tests/test_validate_handler.py:17-42`). `_gate_jobs` returns the payloads of the `gate.candidate` jobs the handler emitted. Entry point verified: `handle_validate_doc(conn, job)` at `app/handlers/validate.py:198`.

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/test_validate_handler.py -k cert -v`
Expected: FAIL with `KeyError: 'cert_doc_id'`.

- [ ] **Step 4: Implement resolution in VALIDATE**

In `app/handlers/validate.py`, add the resolver:

```python
def _resolve_cited_certificate(conn, fields, manufacturer):
    """Best-effort: find the certificate this DoC cites, in our own registry.

    MDR Annex IV item 8 names the certificate; Article 56 caps its validity at
    five years. That certificate's expiry is the DoC's real renewal trigger,
    since Annex IV requires no expiry on the declaration itself.

    Returns (doc_id | None, resolved_from | None). Null is an ordinary outcome:
    Class I devices have no certificate, and we may not hold the cited one yet.
    """
    numbers = [n for n in ([_val(fields, "cert_number")] +
                           (_val(fields, "referenced_docs") or [])) if n]
    if not numbers:
        return None, None
    row = conn.execute(
        "SELECT doc_id, cert_number FROM document "
        "WHERE type IN ('EC','ISO') AND cert_number = ANY(%s) "
        "AND status IN ('production','superseded') "
        "ORDER BY doc_id LIMIT 1",
        (numbers,),
    ).fetchone()
    if row is None:
        return None, None
    return row["doc_id"], row["cert_number"]
```

In the main handler body, after the existing supersession block, add:

```python
    cert_doc_id, cert_from = (None, None)
    if doc_type == "DoC":
        cert_doc_id, cert_from = _resolve_cited_certificate(conn, fields, manufacturer)
        if cert_doc_id is None and (_val(fields, "cert_number")
                                    or _val(fields, "referenced_docs")):
            flags.append("cert-unresolved")
```

Add `"cert_doc_id": cert_doc_id` to the emitted payload dict. **Do not add `cert-unresolved` to `BLOCKING_FLAGS`** — a DoC whose certificate we have not fetched yet is normal and must still be able to reach production. It caps at staged through the existing any-flag rule, which is the correct conservative outcome and self-corrects when the certificate arrives (Step 7).

- [ ] **Step 5: Run the VALIDATE tests**

Run: `pytest tests/test_validate_handler.py -k cert -v`
Expected: PASS both.

- [ ] **Step 6: Write the failing GATE persistence test**

Add to `tests/test_gate_candidate_handler.py`:

```python
def test_gate_persists_cert_doc_id(conn):
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','C-1','production','h-c1',"
        "'/archive/c1.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-d1", _fields(type="DoC", regulation="MDR", cert_number="C-1"))
    out = gh.handle_gate_candidate(
        conn, _job("h-d1", cert_doc_id=cert_id, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["cert_doc_id"] == cert_id
```

`_job(content_hash, *, rev=1, group_id=7, archive_url=..., jid=1, **payload)` builds the job envelope and folds extra keyword arguments into the payload, so `cert_doc_id=` reaches the handler as a payload key (`tests/test_gate_candidate_handler.py:50`).

- [ ] **Step 7: Implement persistence and back-resolution in GATE**

In `app/handlers/gate.py`, add `cert_doc_id` to `_upsert_document`'s INSERT column list, its parameter tuple, and its ON CONFLICT clause using the same sticky pattern already used for `referenced_doc_id` at line 124:

```sql
            cert_doc_id = COALESCE(EXCLUDED.cert_doc_id, document.cert_doc_id)
```

Pass `p.get("cert_doc_id")` from `handle_gate_candidate` into the call, alongside the existing `supersedes` and `related_doc_id` arguments.

Then add back-resolution, called from `handle_gate_candidate` after `_upsert_document` when the document being written is itself a certificate:

```python
def _backresolve_citing_docs(conn, doc_id, cert_number):
    """A newly-held certificate retroactively answers every DoC that cited it.
    Without this, resolution would depend on fetch order: a declaration
    processed before its certificate would stay unresolved forever."""
    if not cert_number:
        return 0
    rows = conn.execute(
        "UPDATE document SET cert_doc_id=%s "
        "WHERE type='DoC' AND cert_doc_id IS NULL AND cert_number=%s "
        "RETURNING doc_id",
        (doc_id, cert_number),
    ).fetchall()
    return len(rows)
```

Call it as:

```python
    if fields and _val(fields, "type") in ("EC", "ISO"):
        _backresolve_citing_docs(conn, doc_id, _val(fields, "cert_number"))
```

- [ ] **Step 8: Write the back-resolution test**

Add to `tests/test_gate_candidate_handler.py`:

```python
def test_new_certificate_backresolves_declarations_that_cited_it(conn):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','C-9','staged','h-early',"
        "'/archive/e.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-cert9", _fields(type="EC", regulation="MDR", cert_number="C-9"))
    out = gh.handle_gate_candidate(
        conn, _job("h-cert9", jid=2, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["cert_doc_id"] == out["doc_id"]
```

- [ ] **Step 9: Run the GATE tests**

Run: `pytest tests/test_gate_candidate_handler.py -k cert -v`
Expected: PASS both.

- [ ] **Step 10: Write the failing expiry-inheritance test**

Add to `tests/test_report_handler.py`:

```python
def test_expiring_documents_includes_docs_inheriting_a_cert_expiry(conn):
    import datetime as dt
    cert = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','C-5',"
        "%s,'production','h-c5','/archive/c5.pdf','group') RETURNING doc_id",
        (dt.date(2026, 9, 1),),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES ('DoC','MDR',"
        "'C-5', NULL, 'production','h-d5','/archive/d5.pdf','group',%s)",
        (cert,),
    )
    out = expiring_documents(conn, horizon_days=90, today=dt.date(2026, 8, 6))
    hashes = {r["doc_id"] for r in out}
    assert cert in hashes
    assert len(out) == 2, "the DoC must appear too, via its inherited expiry"
```

- [ ] **Step 11: Run to verify it fails**

Run: `pytest tests/test_report_handler.py -k inherit -v`
Expected: FAIL, only one row returned.

- [ ] **Step 12: Implement inherited expiry in the scan**

In `app/handlers/report.py`, replace the query in `expiring_documents` (lines 27-34):

```sql
        SELECT d.doc_id, d.type, d.regulation,
               COALESCE(d.validity_to, c.validity_to) AS validity_to,
               (d.validity_to IS NULL AND c.validity_to IS NOT NULL) AS inherited
          FROM document d
          LEFT JOIN document c ON c.doc_id = d.cert_doc_id
         WHERE d.status = 'production'
           AND COALESCE(d.validity_to, c.validity_to) IS NOT NULL
           AND COALESCE(d.validity_to, c.validity_to) <= %s
         ORDER BY COALESCE(d.validity_to, c.validity_to)
```

Update the docstring: a null `validity_to` is no longer automatically "never expiring" — it inherits from the cited certificate when one is linked. Class I devices, which have no certificate at all, still legitimately never expire and must never enter the renewal loop.

- [ ] **Step 13: Run the report tests, then the full suite**

Run: `pytest tests/test_report_handler.py -v && pytest -q`
Expected: PASS.

- [ ] **Step 14: Commit**

```bash
git add migrations/020_cert_reference.sql app/handlers/validate.py app/handlers/gate.py app/handlers/report.py tests/
git commit -m "feat: resolve cited certificates so declarations inherit an expiry"
```

- [ ] **Step 15: Sync the docs and the contract**

`docs/dentalia-pipeline-contract-prd-v3.md`: add `cert_doc_id` to the `document` shape in §9 and to VALIDATE's emitted payload in §6, and add a rule-set line recording that a DoC's renewal date is inherited from its cited certificate (Annex IV requires only a date of issue; Article 56 caps certificates at five years). Add the `cert-unresolved` flag to the flag-to-disposition list in §6 as capping at staged, explicitly not blocking. Mirror the column into `docs/dentalia-schema-sketch.md`, add migration 020 to `docs/architecture.md`, and update `docs/dentalia-job-type-handbook.md` §6.

Commit as `docs: certificate reference resolution in the contract`.

---

### Task 5: Auto-file an unambiguously older document instead of queueing it for a human

`older-than-current` is in `BLOCKING_FLAGS` (`app/handlers/gate.py:42`), so a candidate older than the current production document forces disposition `manual` and creates a `manual_task`. The corpus backfill is full of historical versions of the same declaration, so as it stands the import floods the review queue with documents whose correct disposition is obvious.

Note the flag only ever fires when **both** dates are present (`app/handlers/validate.py:309-312`): the null cases already branch to `downgrade-uncomparable`. So every case that raises it is unambiguous and can be filed automatically. The archive requirement is untouched — GATE writes the document row and its evidence whatever the disposition; this changes the status it lands in and removes the human work item.

**Files:**
- Modify: `app/handlers/validate.py:301-317`
- Modify: `app/handlers/gate.py:38-42, 275-315`
- Test: `tests/test_validate_handler.py`, `tests/test_gate_candidate_handler.py`

**Interfaces:**
- Consumes: `_current_production_doc(conn, group_id, doc_type, regulation)`, already in `validate.py`.
- Produces: payload key `superseded_by_doc_id` (additive). GATE writes `document.status='superseded'` and `document.superseded_by` when it is present.

- [ ] **Step 1: Write the failing VALIDATE test**

Add to `tests/test_validate_handler.py`:

```python
def test_unambiguously_older_candidate_emits_superseded_by(conn):
    gid = _seed_group(conn)
    _seed_item(conn, "I1")
    _seed_member(conn, gid, "I1", mfr_ref="R1")
    current = _seed_current_doc(conn, "h-new", ["I1"], doc_type="DoC",
                               validity_from="2025-01-01")
    _seed(conn, "h-old", _fields(type="DoC", regulation="MDR",
                                 validity_from="2020-01-01", ref_list=["R1"]))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-old", "extract_rev": 1,
                                    "group_id": gid}})
    payload = _gate_jobs(conn, "h-old")[0]
    assert payload["superseded_by_doc_id"] == current
    assert "auto-superseded" in payload["flags"]
    assert "older-than-current" not in payload["flags"]


def test_null_dated_candidate_still_caps_at_staged_and_is_not_auto_filed(conn):
    gid = _seed_group(conn)
    _seed_item(conn, "I2")
    _seed_member(conn, gid, "I2", mfr_ref="R2")
    _seed_current_doc(conn, "h-cur2", ["I2"], doc_type="DoC", validity_from="2025-01-01")
    _seed(conn, "h-nulldate", _fields(type="DoC", regulation="MDR",
                                      validity_from=None, ref_list=["R2"]))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-nulldate", "extract_rev": 1,
                                    "group_id": gid}})
    payload = _gate_jobs(conn, "h-nulldate")[0]
    assert payload.get("superseded_by_doc_id") is None
    assert "downgrade-uncomparable" in payload["flags"]
```

Check `_seed_current_doc`'s real signature at `tests/test_validate_handler.py:78` and match its keyword names.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_validate_handler.py -k "older or null_dated" -v`
Expected: FAIL with `KeyError: 'superseded_by_doc_id'`.

- [ ] **Step 3: Implement in VALIDATE**

In `app/handlers/validate.py`, replace the never-downgrade branch (lines 309-312):

```python
            if validity_from is None or current_from is None:
                flags.append("downgrade-uncomparable")
            elif validity_from < current_from:
                # Unambiguously older: both dates present, same coverage subject,
                # type and regulation. Invariant 4 says never downgrade, not never
                # keep: the document is still archived and evidenced, it just files
                # straight into the superseded chain instead of costing a human a
                # review. The null-date cases above stay uncomparable and staged.
                superseded_by_doc_id = current["doc_id"]
                flags.append("auto-superseded")
            elif validity_from > current_from:
                supersedes = current["doc_id"]
```

Initialise `superseded_by_doc_id = None` alongside `supersedes` and `related_doc_id` at line 303, and add `"superseded_by_doc_id": superseded_by_doc_id` to the emitted payload.

- [ ] **Step 4: Run the VALIDATE tests**

Run: `pytest tests/test_validate_handler.py -k "older or null_dated" -v`
Expected: PASS both.

- [ ] **Step 5: Write the failing GATE test**

Add to `tests/test_gate_candidate_handler.py`:

```python
def test_auto_superseded_candidate_files_without_a_manual_task(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer','/archive/n.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older", _fields(type="DoC", regulation="MDR"))
    out = gh.handle_gate_candidate(
        conn, _job("h-older", superseded_by_doc_id=newer,
                   flags=["auto-superseded"], ref_gate=True, links=[]))
    assert out["disposition"] == "superseded"
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] == "superseded"
    assert row["superseded_by"] == newer
    tasks = conn.execute(
        "SELECT count(*) AS n FROM manual_task WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()["n"]
    assert tasks == 0


def test_auto_superseded_still_writes_evidence(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer2','/archive/n2.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older2", _fields(type="DoC", regulation="MDR"))
    out = gh.handle_gate_candidate(
        conn, _job("h-older2", superseded_by_doc_id=newer,
                   flags=["auto-superseded"], ref_gate=True, links=[]))
    n = conn.execute(
        "SELECT count(*) AS n FROM evidence WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()["n"]
    assert n > 0, "an auto-filed document is still archived and evidenced"
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/test_gate_candidate_handler.py -k auto_superseded -v`
Expected: FAIL — disposition is `manual`, not `superseded`.

- [ ] **Step 7: Implement in GATE**

In `app/handlers/gate.py`, in `handle_gate_candidate`, replace the disposition block (lines 287-292):

```python
    auto_superseded = p.get("superseded_by_doc_id")
    if auto_superseded is not None:
        disposition = "superseded"
    elif score >= cfg.gate.high and ref_gate and not flags:
        disposition = "production"
    elif score >= cfg.gate.med and not any(f in BLOCKING_FLAGS for f in flags):
        disposition = "staged"
    else:
        disposition = "manual"
    doc_status = {"production": "production", "superseded": "superseded"}.get(
        disposition, "staged")
```

After `_upsert_document`, record the chain pointer:

```python
    if auto_superseded is not None:
        conn.execute(
            "UPDATE document SET superseded_by=%s WHERE doc_id=%s AND superseded_by IS NULL",
            (auto_superseded, doc_id),
        )
        _audit(conn, "auto-superseded", doc_id, "gate", job)
```

Remove `"older-than-current"` from `BLOCKING_FLAGS` at line 42 and update the comment above it: the flag is no longer produced by VALIDATE, and `auto-superseded` replaces it as a non-blocking marker. Leave `no-item-identifier` blocking.

- [ ] **Step 8: Run the GATE tests, then the full suite**

Run: `pytest tests/test_gate_candidate_handler.py -k auto_superseded -v && pytest -q`
Expected: PASS. If a pre-existing parametrized disposition case asserted `manual` for `older-than-current`, update it to the new behaviour and keep the case.

- [ ] **Step 9: Commit and sync the contract**

Update PRD §6 rule 4 and the flag-to-disposition list, plus handbook §6, to record that an unambiguously older candidate files as `superseded` rather than `manual`, that both dates present is the precondition, and that the null-date path is unchanged. Invariant 4 in `CLAUDE.md` gains a clause noting that never-downgrade means never made current, not never stored.

```bash
git add app/handlers/validate.py app/handlers/gate.py tests/ docs/ CLAUDE.md
git commit -m "feat: auto-file unambiguously older documents into the superseded chain"
```

---

### Task 6: See which certificate is on which item

The registry is currently invisible. `/staging` reads `document WHERE status='staged'` and renders every field with its evidence, so the moment GATE promotes a document it vanishes from the UI entirely; the only remaining access is `GET /api/items/{item_ref}/documents` by exact item_ref. There is no browse, no search, no list — which is why the KOMET result could not be inspected on 2026-08-05. Seventeen routes and none of them lists an item or a document.

No migration: `dentalia_api` holds blanket SELECT from migration 007, and every table involved predates it.

**Files:**
- Modify: `web/app.py`
- Create: `web/templates/items.html`, `web/templates/item_detail.html`, `web/templates/documents.html`, `web/templates/document_detail.html`
- Modify: `web/templates/base.html` (nav)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1's `/archive/{path}` for document links, Task 2's `/documents/{hash}/text` for the text link.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`:

```python
def _seed_item_with_doc(conn, item_ref="X1", link_status="production",
                        doc_status="production", validity_to="2026-12-01"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue) VALUES (%s,'Widget','077','W-1',TRUE,'LJ') "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,),
    )
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('DoC','MDR','2024-01-01',"
        "%s,%s,'h-' || %s,'/archive/x.pdf','group') RETURNING doc_id",
        (validity_to, doc_status, item_ref),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list',%s)",
        (item_ref, doc_id, link_status),
    )
    conn.commit()
    return doc_id


def test_items_page_lists_items_with_document_counts(client, conn):
    _seed_item_with_doc(conn, "X1")
    resp = client.get("/items")
    assert resp.status_code == 200
    assert "X1" in resp.text


def test_items_page_searches_by_item_ref(client, conn):
    _seed_item_with_doc(conn, "X1")
    _seed_item_with_doc(conn, "Y2")
    resp = client.get("/items?q=Y2")
    assert "Y2" in resp.text
    assert "X1" not in resp.text


def test_item_detail_shows_the_linked_certificate_and_its_validity(client, conn):
    _seed_item_with_doc(conn, "X3", validity_to="2027-05-05")
    resp = client.get("/items/X3")
    assert resp.status_code == 200
    assert "2027-05-05" in resp.text
    assert "DoC" in resp.text


def test_item_detail_shows_staged_links_distinctly_from_production(client, conn):
    _seed_item_with_doc(conn, "X4", link_status="staged", doc_status="staged")
    resp = client.get("/items/X4")
    assert "staged" in resp.text.lower()


def test_item_detail_404s_on_unknown_item(client):
    assert client.get("/items/NOPE").status_code == 404


def test_documents_page_lists_documents_with_item_counts(client, conn):
    _seed_item_with_doc(conn, "X5")
    resp = client.get("/documents")
    assert resp.status_code == 200
    assert "DoC" in resp.text


def test_document_detail_shows_covered_items_and_supersession(client, conn):
    old_id = _seed_item_with_doc(conn, "X6")
    new_id = _seed_item_with_doc(conn, "X7")
    conn.execute("UPDATE document SET superseded_by=%s, status='superseded' "
                 "WHERE doc_id=%s", (new_id, old_id))
    conn.commit()
    resp = client.get(f"/documents/{old_id}")
    assert resp.status_code == 200
    assert "X6" in resp.text
    assert str(new_id) in resp.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_web.py -k "items_page or item_detail or documents_page or document_detail" -v`
Expected: FAIL with 404 on every case.

- [ ] **Step 3: Implement the items list**

In `web/app.py`:

```python
    @app.get("/items", response_class=HTMLResponse)
    def items(request: Request, q: str = "", page: int = 0):
        """Browse the catalogue with its compliance coverage. Staged and
        production counts are separate: 'no certificate yet' and 'certificate
        found but not trusted enough to publish' are different problems."""
        like = f"%{_like_escape(q)}%" if q else ""
        with _conn() as conn:
            rows = conn.execute(
                "SELECT m.item_ref, m.name, m.manufacturer_raw, m.md_flag, "
                "  count(*) FILTER (WHERE d.status='production' AND id.status='production') AS production_docs, "
                "  count(*) FILTER (WHERE id.status='staged') AS staged_docs, "
                "  min(d.validity_to) FILTER (WHERE d.status='production' AND id.status='production') AS next_expiry "
                "FROM item_mirror m "
                "LEFT JOIN item_document id ON id.item_ref = m.item_ref "
                "LEFT JOIN document d ON d.doc_id = id.doc_id "
                "WHERE (%s = '' OR m.item_ref ILIKE %s OR m.name ILIKE %s "
                "       OR m.manufacturer_raw ILIKE %s) "
                "GROUP BY m.item_ref, m.name, m.manufacturer_raw, m.md_flag "
                "ORDER BY m.item_ref LIMIT 100 OFFSET %s",
                (q, like, like, like, max(page, 0) * 100),
            ).fetchall()
        return templates.TemplateResponse(
            "items.html",
            {"request": request, "rows": rows, "q": q, "page": max(page, 0)},
        )
```

- [ ] **Step 4: Implement the item detail**

```python
    @app.get("/items/{item_ref}", response_class=HTMLResponse)
    def item_detail(request: Request, item_ref: str):
        """One item and every document linked to it, staged and production
        alike. A consumer sees a document only when BOTH the document and the
        link are production; this page shows the rest too, because 'why is this
        not published' is the question a reviewer actually has."""
        with _conn() as conn:
            item = conn.execute(
                "SELECT item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
                "product_class, catalogue FROM item_mirror WHERE item_ref=%s",
                (item_ref,),
            ).fetchone()
            if item is None:
                raise HTTPException(status_code=404, detail="unknown item")
            docs = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.validity_from, d.validity_to, "
                "d.status AS doc_status, d.archive_url, d.content_hash, d.cert_number, "
                "id.status AS link_status, id.match_basis "
                "FROM item_document id JOIN document d ON d.doc_id = id.doc_id "
                "WHERE id.item_ref=%s "
                "ORDER BY d.type, d.validity_from DESC NULLS LAST",
                (item_ref,),
            ).fetchall()
        return templates.TemplateResponse(
            "item_detail.html", {"request": request, "item": item, "docs": docs},
        )
```

- [ ] **Step 5: Implement the documents list and detail**

```python
    @app.get("/documents", response_class=HTMLResponse)
    def documents(request: Request, type: str = "", status: str = "", q: str = ""):
        like = f"%{_like_escape(q)}%" if q else ""
        with _conn() as conn:
            rows = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.validity_from, d.validity_to, "
                "d.status, d.cert_number, count(id.item_ref) AS item_count "
                "FROM document d LEFT JOIN item_document id ON id.doc_id = d.doc_id "
                "WHERE (%s = '' OR d.type::text = %s) AND (%s = '' OR d.status::text = %s) "
                "AND (%s = '' OR d.cert_number ILIKE %s) "
                "GROUP BY d.doc_id ORDER BY d.doc_id DESC LIMIT 200",
                (type, type, status, status, q, like),
            ).fetchall()
        return templates.TemplateResponse(
            "documents.html",
            {"request": request, "rows": rows, "type": type, "status": status, "q": q},
        )

    @app.get("/documents/{doc_id:int}", response_class=HTMLResponse)
    def document_detail(request: Request, doc_id: int):
        """One document: the items it covers, its evidence, and both directions
        of its supersession chain."""
        with _conn() as conn:
            doc = conn.execute(
                "SELECT doc_id, type, regulation, validity_from, validity_to, status, "
                "cert_number, content_hash, archive_url, coverage_scope, supersedes, "
                "superseded_by, cert_doc_id FROM document WHERE doc_id=%s",
                (doc_id,),
            ).fetchone()
            if doc is None:
                raise HTTPException(status_code=404, detail="unknown document")
            items = conn.execute(
                "SELECT id.item_ref, id.match_basis, id.status, m.name "
                "FROM item_document id LEFT JOIN item_mirror m ON m.item_ref = id.item_ref "
                "WHERE id.doc_id=%s ORDER BY id.item_ref",
                (doc_id,),
            ).fetchall()
            evidence = conn.execute(
                "SELECT field, value, page, verbatim, tier, model_id, confidence, "
                "extract_rev FROM evidence WHERE doc_id=%s ORDER BY extract_rev DESC, field",
                (doc_id,),
            ).fetchall()
            chain = conn.execute(
                "SELECT doc_id, type, regulation, validity_from, status FROM document "
                "WHERE doc_id IN (%s, %s) OR superseded_by = %s ORDER BY validity_from",
                (doc["supersedes"], doc["superseded_by"], doc_id),
            ).fetchall()
        return templates.TemplateResponse(
            "document_detail.html",
            {"request": request, "doc": doc, "items": items,
             "evidence": evidence, "chain": chain},
        )
```

The `{doc_id:int}` path converter is required so this route does not shadow Task 2's `/documents/{content_hash}/text`. Register the text route **before** this one if FastAPI resolves an ambiguity in declaration order.

- [ ] **Step 6: Write the templates**

Create four templates extending `base.html`, following the markup and CSS classes already in `web/templates/staging.html`:

- `items.html` — search box (GET form preserving `q`), table of item_ref (linking to `/items/{item_ref}`), name, manufacturer, MD class, production doc count, staged doc count, next expiry. Previous/next links using `page`.
- `item_detail.html` — item header, then a table of documents: type, regulation, validity from/to, document status, link status, match_basis, a link to `/documents/{doc_id}`, a link to `archive_url`, and a link to `/documents/{content_hash}/text`. Render production and staged rows with visibly different styling.
- `documents.html` — filter form (type, status, cert number), table linking each row to its detail page, showing covered-item count.
- `document_detail.html` — header with type, regulation, validity, status, cert number; the supersession chain; the covered-items table; the per-field evidence table (field, value, page, verbatim, tier, model, confidence, rev); links to the archived file and its stored text.

Add "Items" and "Documents" to the nav in `base.html`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_web.py -k "items_page or item_detail or documents_page or document_detail" -v`
Expected: PASS all eight.

- [ ] **Step 8: Amend the UI-scope rule in CLAUDE.md**

`CLAUDE.md` currently says "Don't gold-plate: no dashboards, no extra UI, no speculative abstraction. The review page and status board are the whole UI." That rule was written on the assumption that the registry is consumed by BC over the API and never read by a human. Denis reversed that on 2026-08-06: the web admin must be able to see which certificate is on which item. Replace the sentence with:

```
- Don't gold-plate: no speculative abstraction. The UI covers exception
  handling (staging, manual, dead jobs), registry visibility (items,
  documents, expiry, in-flight, data quality), and the status board.
  Anything beyond those needs a reason.
```

This must land in the same task as the first registry-visibility routes, not later, so the repo's own rules and its code never disagree.

- [ ] **Step 9: Run the full suite and commit**

Run: `pytest -q`
Expected: all pass.

```bash
git add web/app.py web/templates/ tests/test_web.py CLAUDE.md
git commit -m "feat: browse items and documents with their compliance coverage"
```

---

### Task 7: Expiry board and the document-centric in-flight view

Two remaining questions the UI cannot answer. "What is about to expire, and who do I email about it" is the operational front of the renewal loop and should exist before the email machinery, not after. "How many certificates are being processed right now" is what you asked for, and the status board answers a different question — it counts jobs, not documents.

**Files:**
- Modify: `web/app.py`
- Create: `web/templates/expiry.html`, `web/templates/inflight.html`
- Modify: `web/templates/base.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `expiring_documents(conn, horizon_days, today)` from `app/handlers/report.py`, extended in Task 4 to follow `cert_doc_id`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web.py`:

```python
def test_expiry_board_groups_by_manufacturer(client, conn):
    conn.execute(
        "INSERT INTO document (type, regulation, validity_to, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','2026-09-01','production',"
        "'h-exp','/archive/e.pdf','group')"
    )
    conn.commit()
    resp = client.get("/expiry?days=90")
    assert resp.status_code == 200
    assert "2026-09-01" in resp.text


def test_expiry_board_excludes_documents_outside_the_horizon(client, conn):
    conn.execute(
        "INSERT INTO document (type, regulation, validity_to, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','2099-01-01','production',"
        "'h-far','/archive/f.pdf','group')"
    )
    conn.commit()
    assert "2099-01-01" not in client.get("/expiry?days=30").text


def test_inflight_counts_documents_not_jobs(client, conn):
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority) VALUES "
        "('extract.doc','{\"content_hash\":\"h-a\"}'::jsonb,'k1','pending','sweep'), "
        "('validate.doc','{\"content_hash\":\"h-a\"}'::jsonb,'k2','pending','sweep'), "
        "('extract.doc','{\"content_hash\":\"h-b\"}'::jsonb,'k3','running','sweep')"
    )
    conn.commit()
    resp = client.get("/inflight")
    assert resp.status_code == 200
    assert "2" in resp.text, "two distinct content hashes are in flight, not three jobs"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_web.py -k "expiry_board or inflight" -v`
Expected: FAIL with 404.

- [ ] **Step 3: Implement the expiry board**

```python
    @app.get("/expiry", response_class=HTMLResponse)
    def expiry(request: Request, days: int = 90):
        """What is about to lapse, grouped by manufacturer, because the
        manufacturer is who gets the email. A declaration with no expiry of its
        own inherits one from the certificate it cites (Task 4); a Class I
        device has no certificate at all and legitimately never appears here."""
        with _conn() as conn:
            rows = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.cert_number, "
                "COALESCE(d.validity_to, c.validity_to) AS expires, "
                "(d.validity_to IS NULL AND c.validity_to IS NOT NULL) AS inherited, "
                "COALESCE(g.canonical_manufacturer, 'unknown') AS manufacturer "
                "FROM document d "
                "LEFT JOIN document c ON c.doc_id = d.cert_doc_id "
                "LEFT JOIN item_document id ON id.doc_id = d.doc_id AND id.status='production' "
                "LEFT JOIN item_group_member gm ON gm.item_ref = id.item_ref "
                "LEFT JOIN item_group g ON g.group_id = gm.group_id "
                "WHERE d.status='production' "
                "AND COALESCE(d.validity_to, c.validity_to) IS NOT NULL "
                "AND COALESCE(d.validity_to, c.validity_to) <= current_date + %s "
                "GROUP BY d.doc_id, d.type, d.regulation, d.cert_number, "
                "d.validity_to, c.validity_to, g.canonical_manufacturer "
                "ORDER BY manufacturer, expires",
                (days,),
            ).fetchall()
        return templates.TemplateResponse(
            "expiry.html", {"request": request, "rows": rows, "days": days},
        )
```

Confirm the `item_group_member` column names against `migrations/002_ingest.sql` and `012_resolve.sql` before running; if a member row keys on something other than `item_ref`, adjust the join.

- [ ] **Step 4: Implement the in-flight view**

```python
    @app.get("/inflight", response_class=HTMLResponse)
    def inflight(request: Request):
        """Documents currently moving through the pipeline, counted by content
        hash rather than by job. One PDF can hold several jobs at once; the
        question 'how many certificates are being processed' is about documents."""
        with _conn() as conn:
            stages = conn.execute(
                "SELECT type AS stage, "
                "count(DISTINCT payload->>'content_hash') AS documents, "
                "count(*) AS jobs "
                "FROM job WHERE status IN ('pending','running') "
                "AND payload ? 'content_hash' "
                "GROUP BY type ORDER BY type"
            ).fetchall()
            total = conn.execute(
                "SELECT count(DISTINCT payload->>'content_hash') AS n FROM job "
                "WHERE status IN ('pending','running') AND payload ? 'content_hash'"
            ).fetchone()["n"]
        return templates.TemplateResponse(
            "inflight.html", {"request": request, "stages": stages, "total": total},
        )
```

- [ ] **Step 5: Write the templates**

- `expiry.html` — a horizon selector (30/60/90/180 days) preserving `days`, then rows grouped by manufacturer: document type, cert number, expiry date, an "inherited" marker where the date came from a cited certificate, and a link to `/documents/{doc_id}`.
- `inflight.html` — the total document count as the headline number, then a per-stage table of stage / documents / jobs. State in the template copy that a document can appear in more than one stage.

Add "Expiry" and "In flight" to the nav in `base.html`.

- [ ] **Step 6: Run the tests, then the full suite**

Run: `pytest tests/test_web.py -k "expiry_board or inflight" -v && pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit and sync the docs**

Update `docs/runbook.md` with the new pages (`/items`, `/documents`, `/expiry`, `/inflight`, `/data-quality`, `/archive/{path}`, `/documents/{hash}/text`) and `docs/architecture.md`'s description of the web process, which currently describes the UI as review-only. `CLAUDE.md`'s UI-scope rule was already amended in Task 6 step 8 — do not amend it twice; verify it still reads correctly with `/expiry` and `/inflight` now present.

```bash
git add web/app.py web/templates/ tests/test_web.py docs/
git commit -m "feat: expiry board and document-centric in-flight view"
```

---

## Follow-on plans (not in this plan)

Each is an independent subsystem and gets its own plan. Rough dependency order:

1. **Playbooks in Postgres** — table plus rev history, runtime loader reads the table, JSON files become the seed and export format, editable JSON field in the web UI. No job type: `playbook` is config, not registry, so invariant 1 is untouched and `dentalia_api` can hold a write grant on it the way it already does on `job` and `upload_inbox`. This supersedes the bind-mount workaround entirely.
2. **P2 md-unknown middle path** — per-code gating for unknown-class rows, seeded from the CONFIRMED codes. The `md_class_blank` anomaly from Task 3 tells you how much it is worth before you build it. **Read this before designing it:** an investigation on 2026-08-06 established that BC's device class is *trustworthy when filled and meaningless when empty*. Across the Dentsply family, 181 blank-class items are named in the manufacturer's own MDR/MDD Declaration of Conformity (Biodentine, AH Plus Jet, Dyract Flow, gutta-percha, Ankylos abutments) versus 121 not; the 25 `NI MP` calls are consistent with their documents. So a blank is simply unfilled, never evidence of non-device status, and "not MD therefore no documents" must not be applied to blanks. All 356 Dentsply items are blank or `NI MP` with zero confirmed, so treating blank as non-MD would silently drop at least 181 real devices. Catalogue-wide that is 11,693 unclassified rows, 61%, currently processed on a fail-open `process_md_unknown=True`. The middle path as decided on 2026-07-24 gates on CONFIRMED manufacturer codes, which the Dentsply codes are not, so **as specified it would drop those 181 devices** — resolve that before building.
3. **P3 T1 rankers** — DISCOVER candidate ranker, and a grouping adjudicator that runs as a pass over open `grouping_suggestion` rows rather than inside RESOLVE. Depends on the vendor-master alias seed already on this branch.
4. **P4 threshold calibration** — after P3, not alongside it.
5. **Playbook suggestions** — mined from `discovery_log` convergence, proposed in the UI, never self-applied. Depends on plans 1 and 3.
6. **Brand-to-parent relation (was "Dentsply split")** — **the original proposal here was wrong; this is the settled version.** An earlier revision said "four playbook files by division", inferred from eight legal-entity strings grepped out of page-1 text. That inference was premature. The settled picture, from Nataša Palme's ruling of 2026-08-06 plus a follow-up investigation (both recorded in `tasks/lessons.md`):

   **A BC code is a brand. Brand-to-parent is a separate relation the data model does not have.** Dentsply is a manufacturer in its own right *and* a brand under a parent. Dentsply Sirona is one parent over **7 BC codes, 356 items**: `010` VDW · `012` SIRONA · `022` MAILLEFER · `035` DENTSPLY · `115` RINN · `313` SCHICK · `10153` ANKYLOS. Nataša named VDW, Dentsply, Sirona and Maillefer; the rest were established from corpus documents.

   Three consequences:
   - `playbooks/dentsply-sirona.json` claims only code `035`, so it reaches **53 of 356 items**. The single `doc_sources` portal in the entire repo serves 15% of the products it should.
   - **`app/reconcile.py` is structurally blind to this.** It defines an entity as codes sharing a master name, so a parent recorded under seven different names reports BLOCKING with 0. The same blind spot will hit Straumann/Neodent/Medentika and KaVo/Kerr. Fixing reconcile is a prerequisite for trusting its output on any multi-brand parent.
   - Evidence quality is not uniform across the seven. `115` RINN carries "Dentsply-Rinn Division" on a BSI and an EC certificate; `313` SCHICK has **zero** corpus documents and rests on the client conversation alone. Do not present them as equally evidenced.

   Still not a blocker for the corpus backfill (which walks PDFs, not catalogue items), but it must land before these BC codes are ingested, since `item_group.canonical_manufacturer` is write-once at `resolve.py:179`; `regroup` on this branch is the repair path.
7. **Manufacturer rollup view** — `/manufacturers` with per-manufacturer coverage, discovery hit rate by rung, tier mix and cost per document. This is also the input to plan 5's suggestion mining. Task 6 deliberately ships item-first navigation only.
8. **EMAIL out** — the renewal loop proper (S2.4). Deferred by Denis, 2026-08-06: "not this week". Task 7's expiry board is its operational front end and works without it.

## Open questions for Denis

- **Scanned documents have no stored text.** 199 of 405 Dentsply files and roughly 23% of the corpus have no text layer, so Task 2 records them as `source='none'` and counts them via the `no_text_layer` anomaly. Producing real transcripts means a vision pass over every page, which is a genuine cost. Deliberately not built: decide once the anomaly count is real.
- **Dentsply is not one manufacturer.** Whatever the name's origin, the corpus DoCs name at least eight legal entities (Dentsply DeTrey GmbH, Maillefer Instruments Holding Sàrl, Dentsply IH, Dentsply Implants Manufacturing GmbH, Sirona Dental Systems GmbH and more), split by division. Its documents also divide into 155 device-level documents and about 50 company-level quality-system certificates (ISO 13485, MDSAP, ISO 14001) which are the `coverage_scope = manufacturer` C4 binding case, plus one chemical MSDS. The playbook needs splitting before its BC codes are authored.
