# BC Import Upload — Slice A (items) Implementation Plan

> **STATUS: DONE 2026-08-25/26.** Shipped to `master` and pushed. Browser-verified end to end against the real `Artikli 3.7.2026.xlsx`: preview 19.091 seen / 15.958 changed, apply wrote 15.958 `item_mirror` rows and emitted 15.958 `resolve.group`. Two defects found after the plan's own review passed and fixed on top: the standalone preview page loaded no htmx so Apply was inert, and the previewed-vs-applied counts this plan downgraded to a followup were built after all.

**Goal:** Let an operator upload a BC item export (`Artikli 3.7.2026.xlsx`, full or trimmed to changed rows) through the web UI, see exactly what it would change, and confirm — without touching the host filesystem.

**Architecture:** The web process is a job producer only and `/imports` is mounted read-only, so the browser upload lands in a new `import_inbox` spool table and a worker does the parsing. Two jobs, never one: a PREVIEW `ingest.run` with `dry_run: true` writes nothing and reports the diff into `job.result`; the operator confirms; an APPLY `ingest.run` with `dry_run: false` writes and consumes the spool row. Items reuse `ingest.run` rather than getting a new job type — INGEST has no business knowing whether the bytes came from a path or a spool.

**Tech Stack:** Python 3.12, FastAPI + Jinja + HTMX (no build step), psycopg 3 with raw SQL, pandas for the export reader, pytest against a real Postgres.

**Spec:** [docs/superpowers/specs/2026-08-25-bc-import-upload-design.md](../specs/2026-08-25-bc-import-upload-design.md) — read it before starting; it carries the four traps and the verification behind each decision.

## Global Constraints

- **Invariant 1:** nothing here writes `document`, `item_document` or `evidence`. `item_mirror` is a mirror, not the registry.
- **Invariant 7:** job types are a closed enum. Slice A adds **no** job type — it widens `ingest.run`'s `source` field, which is a PRD §1 edit only.
- **Invariant 9:** job payloads are immutable after enqueue. Preview and apply are two jobs with two payloads; never mutate one into the other.
- **Invariant 11:** nothing after INGEST may detect which adapter is live. The upload path and the file path must produce identical `NormalizedRow`s.
- **Invariant 12:** no LLM use anywhere in this slice.
- **No ZG.** One Business Central, one article numbering (Denis, 2026-08-19, closing PHASES.md G17/G11). `/import` renders **no** catalogue picker and does **not** use `web/app.py`'s stale `CATALOGUES = ["LJ", "ZG"]`. It reads `scheduler_cfg.ingest_catalogue` (already a single `"LJ"`).
- **Reader options are load-bearing:** the items reader uses `dtype=str, keep_default_na=False`. Without `dtype=str` a leading-zero code becomes a float and corrupts the REF-gate key.
- **Test selection (CLAUDE.md):** per task, run only the named test files. Task 7 runs the **full suite** — `migrations/` and `tests/conftest.py`-adjacent changes make a subset run a false green. Always say which subset ran.
- **Commit messages:** repo style is `prefix: specific declarative statement`, lowercase prefix. No AI/assistant attribution of any kind.
- **Shared checkout:** other sessions commit to `master` in this working tree. Before each commit, `git status --short` and stage **only** the files the task names.

---

## File Structure

| File | Responsibility |
|---|---|
| `migrations/040_import_inbox.sql` | The spool table + the web role's INSERT grant. No enum change. |
| `app/import_spool.py` | **New unit.** The spool's whole lifecycle: `read` / `consume` / `gc`. Nothing else knows the table exists. |
| `app/adapters/source.py` | One reader shared by path and bytes; `UploadExportAdapter` beside `CsvExportAdapter`; `upload` joins the source enum. |
| `app/handlers/ingest.py` | `dry_run` guards on the three write points; spool read/consume/gc on the upload path; `anomalies_preview` on dry runs. |
| `web/app.py` | `/import` GET+POST, `/import/{job_id}/preview` GET, `/import/{job_id}/apply` POST. Producer only. |
| `web/templates/import.html` + `_import_preview.html` | The form and the pollable preview partial. |
| `tests/test_migration_import_inbox.py` | Table shape + grants, mirroring `tests/test_migration_upload.py`. |
| `tests/test_import_spool.py` | The spool unit in isolation. |
| `tests/test_source_adapter.py` | Byte-identity between the path reader and the bytes reader. |
| `tests/test_ingest_handler.py` | `dry_run` writes nothing; anomaly routing; spool consume/gc. |
| `tests/test_web.py` | The four routes. |

---

### Task 1: The spool table

**Files:**
- Create: `migrations/040_import_inbox.sql`
- Test: `tests/test_migration_import_inbox.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `import_inbox (id bigserial, kind text, filename text, content bytea, catalogue text, uploaded_by text, created_at timestamptz)`; `GRANT INSERT` + sequence `USAGE` to `dentalia_api`.

- [x] **Step 1: Write the failing test**

Create `tests/test_migration_import_inbox.py`:

```python
"""Migration 040: the import_inbox spool that carries an uploaded BC export
from the web producer to the worker that parses it.

Distinct from upload_inbox on purpose (spec 2026-08-25): this spool is
two-phase -- a preview reads and leaves the row, an apply reads and consumes
it -- whereas upload_inbox's "the row is gone" means "already processed".
"""
from __future__ import annotations

import pytest


def _cols(conn, table):
    return {r["column_name"]: {"data_type": r["data_type"],
                               "is_nullable": r["is_nullable"]}
            for r in conn.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_name = %s",
                (table,)).fetchall()}


def test_import_inbox_has_expected_shape(conn):
    cols = _cols(conn, "import_inbox")
    assert cols["content"]["data_type"] == "bytea"
    assert {"id", "kind", "filename", "catalogue", "uploaded_by", "created_at"} <= set(cols)
    # kind/filename/content are the row's identity -- a spool row without any
    # of them cannot be parsed or routed.
    assert cols["kind"]["is_nullable"] == "NO"
    assert cols["filename"]["is_nullable"] == "NO"
    assert cols["content"]["is_nullable"] == "NO"
    # catalogue is nullable because slice B's vendor rows carry no catalogue.
    assert cols["catalogue"]["is_nullable"] == "YES"
    assert cols["uploaded_by"]["is_nullable"] == "YES"


def test_kind_is_constrained_to_the_two_spool_kinds(conn):
    conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) VALUES ('items','a.xlsx','\\x00')")
    conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) VALUES ('vendors','b.xlsx','\\x00')")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO import_inbox (kind, filename, content) "
            "VALUES ('documents','c.pdf','\\x00')")


def test_web_role_may_insert_but_not_read_or_delete_the_spool(conn):
    def _has(priv):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', 'import_inbox', %s) AS ok",
            (priv,)).fetchone()["ok"]

    # INSERT-only, same posture as upload_inbox: the producer spools and never
    # reads the spool back. The preview page gets the filename from job.payload.
    assert _has("INSERT") is True
    assert _has("SELECT") is False
    assert _has("DELETE") is False


def test_no_new_job_type_was_added(conn):
    """Slice A widens ingest.run's `source` field; it adds no queue tag.
    `vendor.import` arrives in slice B's migration 041, not here."""
    labels = {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'job_type'").fetchall()}
    assert "vendor.import" not in labels
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_migration_import_inbox.py -v`
Expected: FAIL — `relation "import_inbox" does not exist` (the migration isn't written yet).

- [x] **Step 3: Write the migration**

Create `migrations/040_import_inbox.sql`:

```sql
-- 040_import_inbox.sql
-- The spool that carries an uploaded BC export (items now, manufacturers in
-- slice B) from the web producer to the worker that parses it.
-- Design: docs/superpowers/specs/2026-08-25-bc-import-upload-design.md
--
-- WHY NOT upload_inbox. That table's consumer treats a missing row as "already
-- processed" -- one read is the whole lifecycle. This spool is TWO-PHASE: a
-- preview job reads the row and LEAVES it so the apply job can read the same
-- bytes, and only the apply consumes it. Sharing one table would make
-- upload.ingest's idempotency contract ambiguous, and a wrong-kind row
-- reaching it would archive an xlsx as a compliance document.
--
-- `kind` is CHECK-constrained text rather than an enum: it routes a spool row
-- inside this feature, it is not a pipeline contract, and CHECK is cheaper to
-- extend than an enum type. Both values exist from the start so slice B needs
-- no migration for the discriminator.
--
-- `catalogue` is nullable: an items row carries 'LJ' (there is one Business
-- Central -- Denis 2026-08-19, closing G17/G11), a vendors row carries none.
--
-- No job_type change here. Items reuse `ingest.run` with source='upload';
-- slice B's 041 adds `vendor.import`.

CREATE TABLE import_inbox (
  id           bigserial   PRIMARY KEY,
  kind         text        NOT NULL CHECK (kind IN ('items','vendors')),
  filename     text        NOT NULL,
  content      bytea       NOT NULL,
  catalogue    text,
  uploaded_by  text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Abandoned previews are collected by the handler (app/import_spool.gc), which
-- scans by age on every import run.
CREATE INDEX import_inbox_created_at_idx ON import_inbox (created_at);

-- New table after 007's blanket GRANT, so grant explicitly. INSERT only, the
-- same posture as upload_inbox: the producer spools and never reads it back.
-- Deliberately no SELECT -- the preview page reads the filename out of
-- job.payload, which the web role can already read.
GRANT INSERT ON import_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE import_inbox_id_seq TO dentalia_api;
```

- [x] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_migration_import_inbox.py -v`
Expected: PASS (4 tests). The `test_db_url` fixture runs every pending migration before yielding, so 040 applies automatically.

- [x] **Step 5: Commit**

```bash
git status --short
git add migrations/040_import_inbox.sql tests/test_migration_import_inbox.py
git commit -m "migrations: the import spool, two-phase because a preview must not consume it"
```

---

### Task 2: The spool unit

**Files:**
- Create: `app/import_spool.py`
- Test: `tests/test_import_spool.py`

**Interfaces:**
- Consumes: `import_inbox` from Task 1.
- Produces:
  - `SPOOL_RETENTION_DAYS: int = 7`
  - `class SpoolMissing(Exception)`
  - `read(conn, upload_id: int) -> dict` — keys `id, kind, filename, content, catalogue`; raises `SpoolMissing`
  - `consume(conn, upload_id: int) -> None`
  - `gc(conn, *, retention_days: int = SPOOL_RETENTION_DAYS) -> int` — rows deleted

- [x] **Step 1: Write the failing test**

Create `tests/test_import_spool.py`:

```python
"""app/import_spool.py — the two-phase spool's whole lifecycle.

Real Postgres, no mocks (CLAUDE.md). The `conn` fixture never commits, so
inserts roll back on close.
"""
from __future__ import annotations

import pytest

from app import import_spool


def _spool(conn, *, kind="items", filename="Artikli.xlsx", content=b"\x01\x02",
           catalogue="LJ", age_days=0):
    return conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue, created_at) "
        "VALUES (%s,%s,%s,%s, now() - make_interval(days => %s)) RETURNING id",
        (kind, filename, content, catalogue, age_days),
    ).fetchone()["id"]


def test_read_returns_the_row(conn):
    uid = _spool(conn, content=b"BYTES", filename="Artikli 3.7.2026.xlsx")
    row = import_spool.read(conn, uid)
    assert row["kind"] == "items"
    assert row["filename"] == "Artikli 3.7.2026.xlsx"
    assert bytes(row["content"]) == b"BYTES"
    assert row["catalogue"] == "LJ"


def test_read_leaves_the_row_so_an_apply_can_read_it_again(conn):
    """The whole reason this is not upload_inbox: a preview must not consume."""
    uid = _spool(conn)
    import_spool.read(conn, uid)
    import_spool.read(conn, uid)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 1


def test_read_raises_when_the_row_is_gone(conn):
    with pytest.raises(import_spool.SpoolMissing):
        import_spool.read(conn, 999999)


def test_consume_deletes_and_is_idempotent(conn):
    uid = _spool(conn)
    import_spool.consume(conn, uid)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0
    import_spool.consume(conn, uid)  # at-least-once delivery: a retry is a no-op


def test_gc_drops_abandoned_rows_and_spares_fresh_ones(conn):
    old = _spool(conn, age_days=import_spool.SPOOL_RETENTION_DAYS + 1)
    fresh = _spool(conn, age_days=1)
    assert import_spool.gc(conn) == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM import_inbox").fetchall()}
    assert old not in remaining
    assert fresh in remaining


def test_gc_retention_is_overridable_for_tests(conn):
    uid = _spool(conn, age_days=2)
    assert import_spool.gc(conn, retention_days=30) == 0
    assert import_spool.gc(conn, retention_days=1) == 1
    assert uid not in {r["id"] for r in conn.execute("SELECT id FROM import_inbox").fetchall()}
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_import_spool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.import_spool'`.

- [x] **Step 3: Write the implementation**

Create `app/import_spool.py`:

```python
"""The transient spool carrying an uploaded BC export from the web producer to
the worker that parses it (migration 040).

TWO-PHASE, and that is the whole point. A PREVIEW job reads the row and leaves
it; an APPLY job reads the same bytes and consumes it. `upload_inbox`, the
document spool this is modelled on, is single-phase -- there "the row is gone"
means "already processed" -- which is why these are separate tables.

The web role holds INSERT only; every function here runs as the schema owner
inside a worker's claiming transaction. Nothing outside this module knows the
table exists.
"""

from __future__ import annotations

#: Abandoned previews -- uploaded, looked at, never applied -- are collected
#: after this many days. A GC window, not per-environment policy, so it stays a
#: constant rather than a config key (spec open question 2, decided 2026-08-25).
SPOOL_RETENTION_DAYS = 7


class SpoolMissing(Exception):
    """The spool row is gone: already applied, or garbage-collected."""


def read(conn, upload_id: int) -> dict:
    """The spool row, WITHOUT consuming it.

    Raises rather than returning None: every caller needs the bytes to do any
    work at all, so a missing row is a dead job with an actionable message, not
    a silent zero-row import that would read as "0 items changed".
    """
    row = conn.execute(
        "SELECT id, kind, filename, content, catalogue FROM import_inbox WHERE id=%s",
        (upload_id,),
    ).fetchone()
    if row is None:
        raise SpoolMissing(
            f"import_inbox row {upload_id} is gone: already applied, or collected "
            f"after {SPOOL_RETENTION_DAYS} days. Re-upload the file."
        )
    return row


def consume(conn, upload_id: int) -> None:
    """Delete the row an apply has finished with. Idempotent — a retry after a
    committed delete is a no-op (at-least-once delivery)."""
    conn.execute("DELETE FROM import_inbox WHERE id=%s", (upload_id,))


def gc(conn, *, retention_days: int = SPOOL_RETENTION_DAYS) -> int:
    """Collect spool rows nobody applied. Returns how many were dropped.

    Opportunistic, run by the import handlers rather than by a scheduler cron:
    `app/scheduler.py` is explicit that a `_tick_*` enqueues and never otherwise
    writes, and this is a DELETE.
    """
    cur = conn.execute(
        "DELETE FROM import_inbox WHERE created_at < now() - make_interval(days => %s)",
        (retention_days,),
    )
    return cur.rowcount
```

- [x] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_import_spool.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git status --short
git add app/import_spool.py tests/test_import_spool.py
git commit -m "import: the spool's lifecycle in one place, preview reads and apply consumes"
```

---

### Task 3: One reader for a path and for bytes

**Files:**
- Modify: `app/adapters/source.py:176-220` (`CsvExportAdapter`) and `:255-279` (`make_source_adapter`)
- Test: `tests/test_source_adapter.py`

**Interfaces:**
- Consumes: `import_spool.read`'s row shape (only `content` and `filename`).
- Produces:
  - `EXPORT_SUFFIXES: tuple[str, ...] = (".xlsx", ".xlsm", ".xls", ".csv")`
  - `UploadExportAdapter(content: bytes, filename: str, catalogue: str, cfg: Ingest, profile: dict | None = None)` with `.read() -> Iterator[NormalizedRow]`
  - `make_source_adapter(payload, cfg, *, imports_dir=None, records=None, spool=None)` — new `spool` kwarg, `"upload"` in the source enum

- [x] **Step 1: Write the failing test**

Append to `tests/test_source_adapter.py`:

```python
def test_upload_adapter_is_byte_identical_to_the_path_adapter(tmp_path):
    """Invariant 11: nothing after INGEST may detect which source is live.
    The same bytes read from a file and from a spool must normalize the same."""
    import dataclasses
    import pandas as pd
    from app.adapters import source as src
    from app.config import Ingest

    rows = [
        {"Št.": "001234", "Opis": "Composite syringe",
         "Dobaviteljeva št. artikla": "0450", "Opis za iskanje": "",
         "Šifra proizvajalca": "011", "Razred medicinskega pripomočka": "RAZRED IIA"},
        {"Št.": "005678", "Opis": "Bur", "Dobaviteljeva št. artikla": "",
         "Opis za iskanje": "", "Šifra proizvajalca": "011",
         "Razred medicinskega pripomočka": ""},
    ]
    path = tmp_path / "Artikli.xlsx"
    pd.DataFrame(rows, columns=list(src.LJ_CSV_PROFILE.values())[:1] + [
        "Opis", "Dobaviteljeva št. artikla", "Opis za iskanje",
        "Šifra proizvajalca", "Razred medicinskega pripomočka"]).to_excel(
            path, index=False)

    cfg = Ingest()
    from_path = list(src.CsvExportAdapter(str(path), "LJ", cfg).read())
    from_bytes = list(src.UploadExportAdapter(
        path.read_bytes(), "Artikli.xlsx", "LJ", cfg).read())

    assert [dataclasses.astuple(r) for r in from_path] == \
           [dataclasses.astuple(r) for r in from_bytes]


def test_upload_adapter_preserves_leading_zeros(tmp_path):
    """dtype=str is load-bearing: '001234' read as an int stops matching
    item_mirror.manufacturer_raw and corrupts the REF-gate key."""
    import pandas as pd
    from app.adapters import source as src
    from app.config import Ingest

    path = tmp_path / "Artikli.xlsx"
    pd.DataFrame([{
        "Št.": "001234", "Opis": "X", "Dobaviteljeva št. artikla": "0450",
        "Opis za iskanje": "", "Šifra proizvajalca": "011",
        "Razred medicinskega pripomočka": "RAZRED IIA"}]).to_excel(path, index=False)

    row = next(iter(src.UploadExportAdapter(
        path.read_bytes(), "Artikli.xlsx", "LJ", Ingest()).read()))
    assert row.item_ref == "001234"
    assert row.mfr_ref == "0450"


def test_upload_adapter_names_the_file_in_a_schema_drift_error(tmp_path):
    """The error must name the UPLOADED filename -- there is no path to fall
    back on, and 'missing column' with no file named is unactionable."""
    import pandas as pd
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    path = tmp_path / "wrong.xlsx"
    pd.DataFrame([{"Nope": "1"}]).to_excel(path, index=False)
    with pytest.raises(ValueError, match="wrong.xlsx"):
        list(src.UploadExportAdapter(path.read_bytes(), "wrong.xlsx", "LJ", Ingest()).read())


def test_upload_adapter_rejects_an_unsupported_suffix():
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    with pytest.raises(ValueError, match="unsupported export file type"):
        list(src.UploadExportAdapter(b"%PDF-", "cert.pdf", "LJ", Ingest()).read())


def test_make_source_adapter_routes_upload_to_the_bytes_reader():
    from app.adapters import source as src
    from app.config import Ingest

    payload = {"source": "upload", "ref": {"upload_id": 7}, "catalogue": "LJ"}
    adapter = src.make_source_adapter(
        payload, Ingest(), spool={"content": b"x", "filename": "a.csv"})
    assert isinstance(adapter, src.UploadExportAdapter)


def test_make_source_adapter_refuses_upload_without_a_spool_row():
    import pytest
    from app.adapters import source as src
    from app.config import Ingest

    payload = {"source": "upload", "ref": {"upload_id": 7}, "catalogue": "LJ"}
    with pytest.raises(ValueError, match="spool"):
        src.make_source_adapter(payload, Ingest())
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_source_adapter.py -v -k "upload"`
Expected: FAIL with `AttributeError: module 'app.adapters.source' has no attribute 'UploadExportAdapter'`.

- [x] **Step 3: Refactor the reader and add the bytes adapter**

In `app/adapters/source.py`, add `io` to the imports, extend `__all__` with `"UploadExportAdapter"` and `"EXPORT_SUFFIXES"`, and replace the `CsvExportAdapter` class with this base-plus-two-subclasses form:

```python
#: Export file types the reader accepts. The web upload form validates against
#: this exact tuple, so it can never reject a file the adapter would have read
#: nor accept one it would raise on.
EXPORT_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv")


def _read_frame(source, name: str):
    """Read a BC export into a DataFrame. `source` is a path or a binary
    file-like; `name` supplies the suffix and the error text.

    ONE reader for both routes, so the upload path cannot drift from the file
    path (invariant 11). Everything is read as strings (`dtype=str`,
    `keep_default_na=False`): BC item numbers and vendor article numbers are
    codes -- a leading-zero or long numeric silently coerced to a float would
    corrupt the REF-gate key. Blank cells arrive as '' and normalize to None.
    """
    import pandas as pd

    suffix = pathlib.Path(name).suffix.lower()
    opts = {"dtype": str, "keep_default_na": False}
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(source, **opts)
    if suffix == ".csv":
        return pd.read_csv(source, **opts)
    raise ValueError(f"unsupported export file type: {suffix!r}")


class _ExportAdapter:
    """Shared core: checked frame -> NormalizedRows. Subclasses supply only
    where the bytes come from, which is the one thing that differs."""

    profile: dict
    catalogue: str
    cfg: Ingest

    def _frame(self):
        raise NotImplementedError

    def _checked(self, df, name: str):
        """Fail loudly on schema drift: a renamed/missing BC column would
        otherwise map every row to None and silently skip the whole export (or,
        worse, fan every row into RESOLVE as md_unknown). A hard error
        dead-letters the job with an actionable message instead."""
        missing = [col for col in self.profile.values() if col not in df.columns]
        if missing:
            raise ValueError(
                f"export {name} missing expected column(s) {missing}; "
                f"got {list(df.columns)}"
            )
        return df

    def read(self) -> Iterator[NormalizedRow]:
        for record in self._frame().to_dict("records"):
            raw = _raw_from_profile(record, self.profile)
            yield normalize_record(raw, self.catalogue, self.cfg)


class CsvExportAdapter(_ExportAdapter):
    """Reads a BC export file (.xlsx / .xlsm / .xls / .csv) from a path."""

    def __init__(self, path: str, catalogue: str, cfg: Ingest, profile: dict | None = None):
        self.path = pathlib.Path(path)
        self.catalogue = catalogue
        self.cfg = cfg
        self.profile = profile or LJ_CSV_PROFILE

    def _frame(self):
        return self._checked(_read_frame(self.path, self.path.name), self.path.name)


class UploadExportAdapter(_ExportAdapter):
    """The same BC export, handed to us as bytes through the web /import form
    instead of sitting under /imports.

    Exists because /imports is mounted read-only in both web and worker, so a
    browser upload cannot become a file. Downstream this is indistinguishable
    from CsvExportAdapter -- same reader, same profile, same NormalizedRows
    (invariant 11).
    """

    def __init__(self, content: bytes, filename: str, catalogue: str,
                 cfg: Ingest, profile: dict | None = None):
        self.content = content
        self.filename = filename
        self.catalogue = catalogue
        self.cfg = cfg
        self.profile = profile or LJ_CSV_PROFILE

    def _frame(self):
        return self._checked(
            _read_frame(io.BytesIO(self.content), self.filename), self.filename)
```

Then extend `make_source_adapter`:

```python
def make_source_adapter(
    payload: dict,
    cfg: Ingest,
    *,
    imports_dir: str | None = None,
    records: list[dict] | None = None,
    spool: dict | None = None,
):
    """Config-switched adapter factory over the closed `source` enum (PRD §1).

    A relative csv `ref` is resolved against `imports_dir` (the operator picks a
    filename from the import directory in the UI); an absolute path is used as-is.
    `records` forwards injected OData rows to the BC stub (tests/dev). `spool`
    forwards an `import_inbox` row to the upload reader — injected by the handler
    for the same reason `records` is, because this factory has no connection.
    """
    source = payload.get("source")
    catalogue = payload["catalogue"]
    ref = payload["ref"]

    if source == "csv":
        path = pathlib.Path(ref)
        if not path.is_absolute() and imports_dir:
            path = pathlib.Path(imports_dir) / ref
        return CsvExportAdapter(str(path), catalogue, cfg)
    if source == "bc_odata":
        return BcApiAdapter(ref, catalogue, cfg, records=records)
    if source == "upload":
        if spool is None:
            raise ValueError(
                "source='upload' needs the spool row: the handler reads it from "
                "import_inbox and passes it as `spool=`"
            )
        return UploadExportAdapter(spool["content"], spool["filename"], catalogue, cfg)
    raise ValueError(
        f"unknown ingest source {source!r} (closed enum: csv | bc_odata | upload)")
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_source_adapter.py -v`
Expected: PASS — the six new cases **and** every pre-existing case, which is what proves the `_frame()` refactor did not change the path reader's behaviour.

- [x] **Step 5: Commit**

```bash
git status --short
git add app/adapters/source.py tests/test_source_adapter.py
git commit -m "ingest: one export reader for a path and for bytes, so upload cannot drift"
```

---

### Task 4: `dry_run` and the upload path in the handler

**Files:**
- Modify: `app/handlers/ingest.py` — the module docstring, `handle_ingest_run`'s adapter construction, the two `_UPSERT` calls, the `queue.enqueue`, and the report tail
- Test: `tests/test_ingest_handler.py`

**Interfaces:**
- Consumes: `import_spool.read/consume/gc` (Task 2), `make_source_adapter(..., spool=)` (Task 3).
- Produces: `ingest.run` accepts `payload["dry_run"]` (default false) and `payload["source"] == "upload"` with `payload["ref"] == {"upload_id": N}`. Report gains `dry_run: bool`, `spool_gc: int` (upload runs only), and on a dry run carries anomalies under `anomalies_preview` with `anomalies` empty.

- [x] **Step 1: Write the failing test**

Append to `tests/test_ingest_handler.py`:

```python
def _spool_items(conn, rows, *, filename="Artikli.xlsx", age_days=0):
    """Put a BC export into the import spool the way the web form does."""
    import io
    buf = io.StringIO()
    pd.DataFrame(rows, columns=LJ_HEADERS).to_csv(buf, index=False)
    return conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue, created_at) "
        "VALUES ('items', %s, %s, 'LJ', now() - make_interval(days => %s)) RETURNING id",
        (filename, buf.getvalue().encode(), age_days),
    ).fetchone()["id"]


def _run_upload(conn, upload_id, *, dry_run, priority="interactive"):
    job = {"id": 1, "priority": priority, "payload": {
        "source": "upload", "ref": {"upload_id": upload_id},
        "catalogue": "LJ", "dry_run": dry_run}}
    return ih.handle_ingest_run(conn, job)


def test_dry_run_reports_the_diff_and_writes_nothing(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    before_mirror = conn.execute("SELECT count(*) c FROM item_mirror").fetchone()["c"]
    before_jobs = conn.execute("SELECT count(*) c FROM job").fetchone()["c"]

    res = _run_upload(conn, uid, dry_run=True)

    assert res["dry_run"] is True
    assert res["seen"] == 2
    assert res["changed"] == 2          # both are new against an empty mirror
    assert conn.execute("SELECT count(*) c FROM item_mirror").fetchone()["c"] == before_mirror
    assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == before_jobs


def test_dry_run_leaves_the_spool_row_for_the_apply(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    _run_upload(conn, uid, dry_run=True)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 1


def test_dry_run_routes_anomalies_away_from_the_standing_ledger(conn):
    """runner.py records data_anomaly whenever result["anomalies"] is non-empty.
    A preview that changed nothing must not write the ledger, or the apply
    double-counts every observation."""
    uid = _spool_items(conn, [_lj("A1", "Composite", dobav="")])   # missing mfr_ref
    res = _run_upload(conn, uid, dry_run=True)

    assert res["anomalies"] == []
    kinds = {a["kind"] for a in res["anomalies_preview"]}
    assert "mfr_ref_missing" in kinds


def test_apply_writes_the_mirror_emits_resolve_and_consumes_the_spool(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    res = _run_upload(conn, uid, dry_run=False)

    assert res["dry_run"] is False
    assert res["changed"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE item_ref='A1'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='resolve.group'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0


def test_apply_reports_anomalies_normally(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite", dobav="")])
    res = _run_upload(conn, uid, dry_run=False)
    assert {a["kind"] for a in res["anomalies"]} >= {"mfr_ref_missing"}
    assert "anomalies_preview" not in res


def test_preview_then_apply_agree_on_an_unchanged_mirror(conn):
    """The number the operator confirmed is the number the apply produces,
    when nothing else moved in between."""
    uid = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    preview = _run_upload(conn, uid, dry_run=True)
    applied = _run_upload(conn, uid, dry_run=False)
    assert (preview["seen"], preview["changed"], preview["unchanged"]) == \
           (applied["seen"], applied["changed"], applied["unchanged"])


def test_a_delta_file_leaves_absent_items_untouched(conn):
    """An export trimmed to changed rows must not retire anything: INGEST has
    never deleted rows absent from a file, and that is what makes a delta
    upload structurally indistinguishable from a full one."""
    full = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    _run_upload(conn, full, dry_run=False)

    delta = _spool_items(conn, [_lj("A1", "Composite RENAMED")])
    res = _run_upload(conn, delta, dry_run=False)

    assert res["seen"] == 1
    assert res["changed"] == 1
    names = {r["item_ref"]: r["name"] for r in conn.execute(
        "SELECT item_ref, name FROM item_mirror").fetchall()}
    assert names == {"A1": "Composite RENAMED", "A2": "Bur"}


def test_upload_run_collects_abandoned_spool_rows(conn):
    from app import import_spool

    stale = _spool_items(conn, [_lj("OLD", "x")],
                         age_days=import_spool.SPOOL_RETENTION_DAYS + 1)
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    res = _run_upload(conn, uid, dry_run=True)

    assert res["spool_gc"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (stale,)).fetchone()["c"] == 0


def test_a_missing_spool_row_dead_letters_rather_than_importing_nothing(conn):
    from app import import_spool

    with pytest.raises(import_spool.SpoolMissing):
        _run_upload(conn, 999999, dry_run=False)


def test_csv_path_runs_are_unaffected_and_default_to_writing(tmp_path, conn):
    """No dry_run key in the payload means the existing /ingest form and the
    scheduler keep their fire-and-report behaviour."""
    ref = _write(tmp_path / "e.csv", [_lj("A1", "Composite")])
    res = _run(conn, ref)
    assert res["dry_run"] is False
    assert conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE item_ref='A1'").fetchone()["c"] == 1
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_ingest_handler.py -v -k "dry_run or upload or delta or spool"`
Expected: FAIL — `KeyError: 'dry_run'` on the report, and `ValueError: unknown ingest source 'upload'` before Task 3 is picked up.

- [x] **Step 3: Wire the handler**

In `app/handlers/ingest.py`, add `from app import import_spool` to the imports and append this paragraph to the module docstring:

```
Two payload shapes reach this handler. The scheduler and the /ingest form send
`{source: 'csv'|'bc_odata', ref, catalogue}` and it writes. The /import upload
form sends `{source: 'upload', ref: {upload_id}, catalogue, dry_run}` TWICE --
once with `dry_run: true` to report the diff and write nothing, then, after the
operator confirms, once with `dry_run: false` to write and consume the spool
row. Both runs diff against the live mirror, so the apply is always correct
against current state; what can go stale is only the number the operator looked
at, which is why both counts are reported rather than compared.

Known and deliberate: a dry run over an export containing the SAME item_ref
twice counts it changed twice, where an apply counts it changed once and then
unchanged, because the apply's first upsert is visible to the second row's diff
and a dry run writes nothing to see. A duplicated key in one export is a defect
in the export; the counts differing is how it becomes visible.
```

Then in `handle_ingest_run`, replace the adapter construction:

```python
    cfg = cfg or load_config()
    payload = job["payload"]
    priority = job.get("priority", "sweep")
    dry_run = bool(payload.get("dry_run"))

    # The factory has no connection, so an upload's bytes are injected the same
    # way the OData stub's `records` are.
    spool = None
    if payload.get("source") == "upload":
        spool = import_spool.read(conn, payload["ref"]["upload_id"])

    adapter = src.make_source_adapter(
        payload, cfg.ingest, imports_dir=cfg.web.imports_dir, records=records,
        spool=spool,
    )
```

Guard the non-MD reclassify upsert:

```python
            if tuple(current[c] for c in _DIFF_COLS) == new:
                r.count("unchanged")
                continue
            if not dry_run:
                conn.execute(_UPSERT, (row.item_ref, *new))
            r.count("reclassified_non_md")
```

Guard the main upsert and the emit:

```python
        new = _mirror_tuple(row)
        current = _current(row.item_ref)
        if current is not None and tuple(current[c] for c in _DIFF_COLS) == new:
            r.count("unchanged")
            continue

        if not dry_run:
            rev = conn.execute(_UPSERT, (row.item_ref, *new)).fetchone()["mirror_rev"]
            queue.enqueue(
                conn, "resolve.group",
                {"item_ref": row.item_ref},
                dedupe_key=f"resolve:{row.item_ref}:{rev}",
                priority=priority,
            )
        r.count("changed")
```

And replace the report tail (from `out = r.as_dict()` up to the `log.info` call):

```python
    if spool is not None and not dry_run:
        import_spool.consume(conn, payload["ref"]["upload_id"])

    out = r.as_dict()
    # Anomalies are NOT recorded here. app/workers/runner.py's generic
    # post-finish hook is the single mechanism that writes data_anomaly, for
    # every handler, uniformly -- that is the whole point of standardizing on
    # Result (Task 3). "anomalies" stays in the returned report so job.result
    # (migration 019) shows what this run found, same as any other handler.
    report = out["counts"]
    report["skipped_sample"] = out["samples"].get("skipped", [])
    report["dry_run"] = dry_run
    if dry_run:
        # The runner keys on exactly "anomalies". A preview changed nothing, so
        # it must not write the standing ledger -- otherwise the apply counts
        # every observation a second time. The findings are still shown.
        report["anomalies"] = []
        report["anomalies_preview"] = out["anomalies"]
    else:
        report["anomalies"] = out["anomalies"]

    if spool is not None:
        # Opportunistic GC of previews nobody applied. Here rather than in a
        # scheduler cron because app/scheduler.py's ticks enqueue and never
        # otherwise write, and this is a DELETE.
        report["spool_gc"] = import_spool.gc(conn)
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_ingest_handler.py tests/test_source_adapter.py tests/test_import_spool.py -v`
Expected: PASS — including every pre-existing `test_ingest_handler.py` case, which is what proves the `dry_run` guards left the csv path alone.

- [x] **Step 5: Commit**

```bash
git status --short
git add app/handlers/ingest.py tests/test_ingest_handler.py
git commit -m "ingest: a dry run that reports the diff and keeps its anomalies out of the ledger"
```

---

### Task 5: `/import` — the upload form

**Files:**
- Modify: `web/app.py` — add `IMPORT_SUFFIXES` near `CATALOGUES` (`:115-117`), and the two routes after `upload_submit` (`:2867-2930`)
- Create: `web/templates/import.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `import_inbox` (Task 1), `ingest.run` `source: "upload"` payload (Task 4).
- Produces: `GET /import` (200, the form) and `POST /import` (200 with a job id, or 422). Enqueues `ingest.run` with `{"source": "upload", "ref": {"upload_id": N}, "catalogue": <ingest_catalogue>, "dry_run": True}`, dedupe `ingest.run:upload:{N}:preview`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
_XLSX_NAME = "Artikli 3.7.2026.xlsx"


def _export_csv_bytes():
    """A minimal BC item export, as the bytes a browser would post."""
    import io
    import pandas as pd
    return pd.DataFrame([{
        "Št.": "A1", "Opis": "Composite", "Dobaviteljeva št. artikla": "0450",
        "Opis za iskanje": "", "Šifra proizvajalca": "011",
        "Razred medicinskega pripomočka": "RAZRED IIA",
    }]).to_csv(index=False).encode()


def test_import_form_renders_without_a_catalogue_picker(client):
    """There is one Business Central (Denis 2026-08-19, closing G17/G11), so
    /import must not offer a choice that does not exist -- and must not reuse
    web/app.py's stale CATALOGUES list to do it."""
    resp = client.get("/import")
    assert resp.status_code == 200
    assert "ZG" not in resp.text


def test_import_spools_the_file_and_enqueues_a_preview(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 200

    spool = conn.execute(
        "SELECT id, kind, filename, catalogue FROM import_inbox").fetchall()
    assert len(spool) == 1
    assert spool[0]["kind"] == "items"
    assert spool[0]["filename"] == "Artikli.csv"
    assert spool[0]["catalogue"] == "LJ"

    job = conn.execute(
        "SELECT payload, dedupe_key, type FROM job WHERE type='ingest.run'").fetchone()
    assert job["payload"] == {
        "source": "upload", "ref": {"upload_id": spool[0]["id"]},
        "catalogue": "LJ", "dry_run": True}
    assert job["dedupe_key"] == f"ingest.run:upload:{spool[0]['id']}:preview"


def test_import_rejects_a_pdf(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("cert.pdf", b"%PDF-1.4", "application/pdf")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_rejects_an_oversized_file(client, conn):
    from app.config import Web
    from fastapi.testclient import TestClient
    from web.app import create_app

    small = TestClient(create_app(Web(api_database_url=API_TEST_URL, upload_max_mb=0)))
    resp = small.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_rejects_an_unknown_priority(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "urgent"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_accepts_every_suffix_the_adapter_reads(client, conn):
    """The form's accepted set and the adapter's must be the same set, or the
    web rejects files the worker would have read."""
    from app.adapters.source import EXPORT_SUFFIXES
    from web.app import IMPORT_SUFFIXES

    assert set(IMPORT_SUFFIXES) == set(EXPORT_SUFFIXES)
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web.py -v -k "import_"`
Expected: FAIL — 404 on `/import`, and `ImportError` on `IMPORT_SUFFIXES`.

- [x] **Step 3: Add the routes and the template**

In `web/app.py`, add beside the other module constants (near `:115`):

```python
# Export file types /import accepts. Mirrors the adapter's own set exactly, so
# the form can never reject a file the worker would have read.
from app.adapters.source import EXPORT_SUFFIXES as IMPORT_SUFFIXES  # noqa: E402
```

(If a module-level import from `app.adapters` is awkward where the constants sit, place it with the other `from app...` imports at the top of the file and keep the alias.)

Add the routes immediately after `upload_submit`:

```python
    # ----------------------------------------------------------------------- #
    # /import — a BC export handed to us through the browser rather than
    # dropped under /imports (which is mounted read-only in both web and
    # worker). Two jobs, never one: a preview that writes nothing, then an
    # apply the operator confirms.
    #
    # NO catalogue picker. There is one Business Central, one article
    # numbering — the Zagreb operation adds items, not a second integration
    # (Denis, 2026-08-19, closing PHASES.md G17/G11). `CATALOGUES` above still
    # lists ZG for /ingest and /upload; that is drift, logged as
    # [zg-catalogue-picker-drift], and this route deliberately does not use it.
    # ----------------------------------------------------------------------- #
    @app.get("/import", response_class=HTMLResponse)
    def import_form(request: Request):
        with _conn() as conn:
            runs = _recent_runs(conn)
        for r in runs:
            r["result_pretty"] = json.dumps(r["result"], indent=2, ensure_ascii=False)
        return templates.TemplateResponse(request, "import.html", {
            "request": request,
            "priorities": PRIORITIES,
            "catalogue": scheduler_cfg.ingest_catalogue,
            "suffixes": ", ".join(IMPORT_SUFFIXES),
            "max_mb": cfg.upload_max_mb,
            "runs": runs,
        })

    @app.post("/import", response_class=HTMLResponse)
    def import_submit(
        request: Request,
        file: UploadFile = File(...),
        priority: str = Form("interactive"),
    ):
        error = None
        if priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        filename = (file.filename or "").strip()
        content = file.file.read()
        if error is None:
            if pathlib.Path(filename).suffix.lower() not in IMPORT_SUFFIXES:
                error = (f"only BC export files are accepted "
                         f"({', '.join(IMPORT_SUFFIXES)}) — got {filename!r}")
            elif len(content) > cfg.upload_max_mb * 1024 * 1024:
                error = f"file exceeds the {cfg.upload_max_mb} MB limit"

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        catalogue = scheduler_cfg.ingest_catalogue
        with _conn() as conn:
            # No `RETURNING id`: migration 040 grants dentalia_api INSERT only
            # (it must never read the spool back), and Postgres requires SELECT
            # on any column named in RETURNING. `currval()` needs only the
            # already-granted sequence USAGE and reflects this INSERT's nextval.
            conn.execute(
                "INSERT INTO import_inbox (kind, filename, content, catalogue, uploaded_by) "
                "VALUES ('items', %s, %s, %s, %s)",
                (filename, content, catalogue, _authenticated_user(request)),
            )
            uid = conn.execute("SELECT currval('import_inbox_id_seq') AS id").fetchone()["id"]
            dedupe_key = f"ingest.run:upload:{uid}:preview"
            jid = queue.enqueue(
                conn, "ingest.run",
                {"source": "upload", "ref": {"upload_id": uid},
                 "catalogue": catalogue, "dry_run": True},
                dedupe_key, priority=priority,
            )
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        # `queue.enqueue` returns None when an active job already holds the key
        # (C2). Only link onward to a preview that actually exists — otherwise
        # this renders `/import/None/preview`.
        if jid is not None:
            ctx["preview_url"] = f"/import/{jid}/preview"
        return templates.TemplateResponse(request, "_result.html", ctx)
```

Create `web/templates/import.html`:

```html
{% extends "base.html" %}
{% block title %}Import BC export — Dentalia Compliance Registry{% endblock %}
{% block content %}
<h1>Import a BC export</h1>
<p>
  Upload a newer item export — the full catalogue, or trimmed to the rows that
  changed. Nothing is written until you have seen the diff and confirmed it:
  items absent from the file are never retired, so a delta file is safe.
  Catalogue <code>{{ catalogue }}</code>. Accepted: {{ suffixes }}, up to {{ max_mb }} MB.
</p>
<form hx-post="/import" hx-encoding="multipart/form-data" hx-target="#result">
  <label>Export file <input type="file" name="file" required></label>
  <label>Priority
    <select name="priority">
      {% for p in priorities %}<option value="{{ p }}">{{ p }}</option>{% endfor %}
    </select>
  </label>
  <button type="submit">Upload and preview</button>
</form>
<div id="result"></div>

<h2>Recent runs</h2>
{% for r in runs %}
  <details>
    <summary>#{{ r.id }} {{ r.type }} — {{ r.status }}</summary>
    <pre>{{ r.result_pretty }}</pre>
  </details>
{% endfor %}
{% endblock %}
```

Add one block to `web/templates/_result.html` so a successful upload links onward. The file is a single `{% if error %}…{% elif %}…{% else %}…{% endif %}` chain; append this **after** the closing `{% endif %}`, changing nothing above it:

```html
{% if preview_url %}
<a class="result-link" href="{{ preview_url }}"
   hx-get="{{ preview_url }}" hx-target="#result">See what this would change</a>
{% endif %}
```

`preview_url` is undefined on every other caller of this partial, so the block is inert for `/ingest`, `/upload`, `/staging` and the dead-jobs board. It is also unset when `queue.enqueue` deduped, which is why the routes above guard it.

- [x] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web.py -v -k "import_"`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git status --short
git add web/app.py web/templates/import.html web/templates/_result.html tests/test_web.py
git commit -m "web: /import takes a BC export from the browser and previews it first"
```

---

### Task 6: The preview screen and the apply

**Files:**
- Modify: `web/app.py` — two routes after `import_submit`
- Create: `web/templates/_import_preview.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: the preview job's `payload` and `result` (Task 4).
- Produces: `GET /import/{job_id}/preview` (200 with the diff or a still-running notice, 404 on an unknown job), `POST /import/{job_id}/apply` (200 with the apply job id, 422 when the preview is not an applicable one). Enqueues `ingest.run` with the preview's payload and `dry_run: False`, dedupe `ingest.run:upload:{N}:apply`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def _seed_preview(conn, *, status="done", result=None, dry_run=True,
                  source="upload", upload_id=None):
    """A finished preview job, as the worker would have left it."""
    if upload_id is None:
        upload_id = conn.execute(
            "INSERT INTO import_inbox (kind, filename, content, catalogue) "
            "VALUES ('items','Artikli.csv','\\x00','LJ') RETURNING id").fetchone()["id"]
    payload = {"source": source, "ref": {"upload_id": upload_id},
               "catalogue": "LJ", "dry_run": dry_run}
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('ingest.run', %s, %s, %s, %s) RETURNING id",
        (Json(payload), f"ingest.run:upload:{upload_id}:preview", status,
         Json(result if result is not None else
              {"seen": 2, "changed": 1, "unchanged": 1, "dry_run": True})),
    ).fetchone()["id"]
    conn.commit()
    return jid, upload_id


def test_preview_renders_the_diff(client, conn):
    jid, _ = _seed_preview(conn, result={
        "seen": 19091, "changed": 12, "unchanged": 19079, "dry_run": True})
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "19091" in resp.text or "19,091" in resp.text
    assert "12" in resp.text


def test_preview_of_an_unfinished_job_says_so_and_offers_no_apply(client, conn):
    jid, _ = _seed_preview(conn, status="running", result=None)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "/apply" not in resp.text


def test_preview_of_an_unknown_job_is_404(client):
    assert client.get("/import/999999/preview").status_code == 404


def test_apply_enqueues_the_write_run_from_the_previews_payload(client, conn):
    jid, uid = _seed_preview(conn)
    resp = client.post(f"/import/{jid}/apply")
    assert resp.status_code == 200

    job = conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{uid}:apply",)).fetchone()
    assert job is not None
    assert job["payload"] == {"source": "upload", "ref": {"upload_id": uid},
                              "catalogue": "LJ", "dry_run": False}


def test_apply_does_not_mutate_the_preview_job(client, conn):
    """Invariant 9: payloads are immutable after enqueue. The apply is a second
    job, never the preview rewritten."""
    jid, uid = _seed_preview(conn)
    client.post(f"/import/{jid}/apply")
    preview = conn.execute("SELECT payload FROM job WHERE id=%s", (jid,)).fetchone()
    assert preview["payload"]["dry_run"] is True


def test_apply_refuses_a_job_that_is_not_a_finished_upload_preview(client, conn):
    running, _ = _seed_preview(conn, status="running")
    assert client.post(f"/import/{running}/apply").status_code == 422

    already_applied, _ = _seed_preview(conn, dry_run=False)
    assert client.post(f"/import/{already_applied}/apply").status_code == 422

    path_run, _ = _seed_preview(conn, source="csv")
    assert client.post(f"/import/{path_run}/apply").status_code == 422


def test_apply_twice_dedupes_on_the_active_job(client, conn):
    """A double-clicked Apply must not enqueue two writing runs."""
    jid, uid = _seed_preview(conn)
    client.post(f"/import/{jid}/apply")
    client.post(f"/import/{jid}/apply")
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{uid}:apply",)).fetchone()["c"] == 1
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web.py -v -k "preview or apply_enqueues or apply_does_not or apply_refuses or apply_twice"`
Expected: FAIL — 404 on `/import/{id}/preview`.

- [x] **Step 3: Add the routes and the partial**

In `web/app.py`, after `import_submit`:

```python
    @app.get("/import/{job_id:int}/preview", response_class=HTMLResponse)
    def import_preview(request: Request, job_id: int):
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        result = job["result"] or {}
        return templates.TemplateResponse(request, "_import_preview.html", {
            "request": request,
            "job": job,
            "result": result,
            "result_pretty": json.dumps(result, indent=2, ensure_ascii=False),
            "appliable": _is_appliable_preview(job),
            "filename": (job["payload"].get("ref") or {}).get("filename", ""),
        })

    def _is_appliable_preview(job: dict) -> bool:
        """Only a FINISHED upload DRY RUN can be applied. A running preview has
        no diff yet, a csv/odata run was never a preview, and a job whose
        payload already says dry_run=false is the apply itself."""
        payload = job["payload"] or {}
        return bool(
            job["type"] == "ingest.run"
            and job["status"] == "done"
            and payload.get("source") == "upload"
            and payload.get("dry_run") is True
        )

    @app.post("/import/{job_id:int}/apply", response_class=HTMLResponse)
    def import_apply(request: Request, job_id: int):
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            if not _is_appliable_preview(job):
                return templates.TemplateResponse(
                    request, "_result.html",
                    {"request": request,
                     "error": f"job {job_id} is not a finished import preview "
                              f"(type={job['type']}, status={job['status']})"},
                    status_code=422)

            uid = job["payload"]["ref"]["upload_id"]
            # A NEW payload, never the preview's mutated (invariant 9). The
            # operator's confirmation is what this second job carries.
            payload = {**job["payload"], "dry_run": False}
            dedupe_key = f"ingest.run:upload:{uid}:apply"
            jid = queue.enqueue(
                conn, "ingest.run", payload, dedupe_key, priority="interactive")
            conn.commit()

        ctx = {"request": request, "job_id": jid, "dedupe_key": dedupe_key}
        if jid is not None:      # None means deduped — a double-clicked Apply
            ctx["preview_url"] = f"/import/{jid}/preview"
        return templates.TemplateResponse(request, "_result.html", ctx)
```

Create `web/templates/_import_preview.html`:

```html
<div class="import-preview">
  <h3>Job #{{ job.id }} — {{ job.status }}</h3>
  {% if job.status in ("pending", "running") %}
    <p hx-get="/import/{{ job.id }}/preview" hx-trigger="every 2s" hx-target="#result">
      Reading the export…
    </p>
  {% elif job.status in ("failed", "dead") %}
    <p class="error">The preview failed. {{ job.last_error }}</p>
  {% else %}
    <table>
      <tr><th>Rows in file</th><td>{{ result.seen }}</td></tr>
      <tr><th>New or changed</th><td>{{ result.changed }}</td></tr>
      <tr><th>Unchanged</th><td>{{ result.unchanged }}</td></tr>
      <tr><th>Non-MD</th><td>{{ result.non_md }}</td></tr>
      <tr><th>Device class blank</th><td>{{ result.md_unknown }}</td></tr>
      <tr><th>No article number</th><td>{{ result.missing_mfr_ref }}</td></tr>
      <tr><th>Skipped</th><td>{{ result.skipped }}</td></tr>
    </table>
    {% if result.dry_run %}
      <p>
        Nothing has been written. Items absent from this file are left alone.
        Applying re-reads the export and diffs it against the catalogue as it is
        <em>now</em> — the applied counts are reported next to these, so any
        drift since this preview is visible rather than assumed away.
      </p>
      {% if appliable %}
        <button hx-post="/import/{{ job.id }}/apply" hx-target="#result">
          Apply — write {{ result.changed }} item(s)
        </button>
      {% endif %}
    {% else %}
      <p>Applied. {{ result.changed }} item(s) written, {{ result.spool_gc | default(0) }} abandoned upload(s) collected.</p>
    {% endif %}
    <details><summary>Full report</summary><pre>{{ result_pretty }}</pre></details>
  {% endif %}
</div>
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web.py -v -k "import_ or preview or apply_"`
Expected: PASS (13 tests across Tasks 5 and 6).

- [x] **Step 5: Commit**

```bash
git status --short
git add web/app.py web/templates/_import_preview.html tests/test_web.py
git commit -m "web: the import preview, and an apply that is a second job not a rewritten one"
```

---

### Task 7: Documentation, and the full suite

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (§0 dedupe table, §1 Consumes + Adapter rows)
- Modify: `docs/dentalia-schema-sketch.md` (`import_inbox` + the tag→table access matrix)
- Modify: `docs/runbook.md` (the `/import` route in the route table, and the import procedure beside the CLI one)
- Modify: `docs/code-map.md` (`app/import_spool.py`, the new routes, the new templates)
- Test: the full suite

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [x] **Step 1: Add the two dedupe rows to PRD §0**

In the `dedupe_key conventions` table (after the `upload.ingest` row), add:

```markdown
| ingest.run | web UI (import upload, preview) | `ingest.run:upload:{upload_id}:preview` |
| ingest.run | web UI (import upload, apply) | `ingest.run:upload:{upload_id}:apply` |
```

With this note appended to the second row: *(added 2026-08-25, BC import upload slice A — `upload_id` is the `import_inbox` row id. Preview and apply are two jobs with two immutable payloads, so the operator's confirmation is carried rather than a payload mutated (invariant 9). C2 active-scope dedupe means a terminal preview never blocks its own apply, while a double-clicked Apply dedupes.)*

- [x] **Step 2: Amend PRD §1's Consumes and Adapter rows**

Replace the Consumes row:

```markdown
| **Consumes** | `ingest.run` — payload: `{source: "csv" \| "bc_odata" \| "upload", ref: file_path \| delta_window \| {upload_id}, catalogue, dry_run?: bool}` *(`upload` and `dry_run` added 2026-08-25: `/imports` is mounted read-only in both web and worker, so a browser upload lands in `import_inbox` and the bytes are injected into the adapter the same way the OData stub's `records` are. `dry_run` defaults false — the scheduler and the `/ingest` form are unchanged — and when true INGEST diffs and reports but performs no upsert and emits no `resolve.group`.)* |
```

Replace the Adapter row:

```markdown
| **Adapter** | `CsvExportAdapter` (v1, path) / `UploadExportAdapter` (v1, spooled bytes) / `BcApiAdapter` (v2, OData). Config-switched; identical output contract — the path and bytes readers share one `_read_frame`, so they cannot drift. |
```

- [x] **Step 3: Update the schema sketch and the operational docs**

- `docs/dentalia-schema-sketch.md`: add `import_inbox` beside `upload_inbox`, noting the two-phase lifecycle and that `dentalia_api` holds INSERT only (no SELECT — the preview reads `job.payload`).
- `docs/runbook.md`: add `/import` to the route table; beside the existing `vendor-master` / `playbooks` CLI procedures, describe upload → preview → apply, and state that a delta file is safe because absent items are never retired.
- `docs/code-map.md`: add `app/import_spool.py`, `UploadExportAdapter`, the four routes, and the two templates.

Make targeted edits only — never rewrite unaffected sections (CLAUDE.md).

- [x] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS. This is required, not optional — `migrations/` changed, and CLAUDE.md's selection rule says a subset run is a false green there. Report the actual count and say the full suite ran. If anything fails, fix it before committing rather than committing a known-red tree.

- [x] **Step 5: Commit**

```bash
git status --short
git add docs/dentalia-pipeline-contract-prd-v3.md docs/dentalia-schema-sketch.md docs/runbook.md docs/code-map.md
git commit -m "contract, docs: ingest.run reads spooled bytes, and previews before it writes"
```

---

## Self-Review

**Spec coverage.** Every slice-A section of the spec maps to a task: the flow diagram → Tasks 4–6; contract changes (`ingest.run` payload, dedupe rows) → Tasks 4 and 7; migration 040 → Task 1; the items handler → Task 4; anomaly double-write (trap 1) → Task 4 Step 1 `test_dry_run_routes_anomalies_away_from_the_standing_ledger`; preview size (trap 2) → no work needed, `Result.sample`'s cap already handles it; TOCTOU (trap 3) → Task 6's preview copy plus `test_preview_then_apply_agree_on_an_unchanged_mirror`; `playbooks sync` ordering (trap 4) → slice B's concern, since it is the vendor apply that adds codes; `/import` routes → Tasks 5–6; the no-picker ruling → Global Constraints, Task 5's route comment and `test_import_form_renders_without_a_catalogue_picker`; testing list → distributed across tasks; documentation impact → Task 7. `RenameRefused` and `vendor.import` are correctly absent — they are slice B.

**Placeholders.** None. Every code step carries the actual code; every test step carries the actual test.

**Type consistency.** `import_spool.read/consume/gc` and `SpoolMissing` are named identically in Tasks 2 and 4. `UploadExportAdapter(content, filename, catalogue, cfg, profile=None)` matches between Task 3's definition and Task 3's tests. `EXPORT_SUFFIXES` is defined in Task 3 and aliased as `IMPORT_SUFFIXES` in Task 5, with `test_import_accepts_every_suffix_the_adapter_reads` pinning them equal. The payload `{"source": "upload", "ref": {"upload_id": N}, "catalogue": ..., "dry_run": bool}` is identical in Tasks 4, 5 and 6. Dedupe keys `ingest.run:upload:{N}:preview` / `:apply` match between Tasks 5, 6 and 7.

**Verified while writing, not left to the implementer:** `job.last_error` is the real column name ([migrations/001_queue.sql:44](../../../migrations/001_queue.sql#L44)), so `_import_preview.html`'s failure branch is correct as written. And `queue.enqueue` returns `None` on a C2 dedupe, so both routes guard `preview_url` rather than rendering `/import/None/preview` — `test_apply_twice_dedupes_on_the_active_job` is the case that would otherwise have shipped that link.
