# Document → Manufacturer Binding Implementation Plan

**Goal:** Give every document a stored, curated manufacturer so a reviewer can bind any manufacturer and the 540 link-less documents become queryable.

**Architecture:** One nullable text column on `document`, FK'd to `manufacturer(canonical_name)` with `ON UPDATE CASCADE`. Written at three GATE points, sticky under re-delivery, read in preference to the existing link-derivation which stays as the fallback. Additive throughout: no new job type, no new `match_basis`, no payload change.

**Tech Stack:** Python 3.12 sync, psycopg 3 raw SQL, numbered `.sql` migrations, FastAPI + Jinja + HTMX, pytest against real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-31-document-manufacturer-binding-design.md`

## Global Constraints

- **Every test run goes through `./scripts/test.sh`.** A PreToolUse hook blocks bare `pytest`. Stack must be up: `docker compose up -d` then `docker compose --profile test up -d test`.
- **This plan adds a file under `migrations/`, so the full suite is required before the final commit** — 2646 tests, ~150s at `-n 4 --dist loadfile` (measured in this worktree 2026-08-31; the earlier 2231 came from counting `def test_` lines and missed parametrised expansion). Per-task runs may be scoped; say which subset ran.
- **Invariant 1:** only `gate.candidate` / `gate.apply` handlers write `document`. `dentalia_api` holds SELECT only — verified 2026-08-31. Do not add a write grant.
- **Invariant 7:** job types are a closed enum. This plan adds none. If a task seems to need one, stop.
- **Invariant 2 is not engaged:** the binding is a decision, not a production *value*. Its provenance is the audit entry plus the existing `manufacturer` evidence row. Do not add an `evidence` row for it.
- **`[mfr-bind-empty-class]` (Denis 2026-08-27) stands:** a blank BC device class means UNKNOWN. No task in this plan writes item links for blank-class items.
- **Commit messages must not mention Claude or AI.**
- Naming: the column is `canonical_manufacturer`, matching `item_group.canonical_manufacturer`. Never `manufacturer_id`, never `bound_manufacturer`.

---

### Task 1: Migration and schema documentation

**Files:**
- Create: `migrations/053_document_manufacturer.sql`
- Modify: `docs/dentalia-schema-sketch.md` (the `CREATE TABLE document` block)
- Test: `tests/test_migration_document_manufacturer.py` (house convention is `test_migration_<topic>.py`; there is no `tests/test_migrations.py`)

**Interfaces:**
- Produces: `document.canonical_manufacturer text NULL REFERENCES manufacturer(canonical_name) ON UPDATE CASCADE`, and index `document_manufacturer_idx`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migration_document_manufacturer.py`:

```python
def test_document_carries_a_nullable_manufacturer_fk(conn):
    """053. Nullable and additive: an existing document row is valid with the
    column unset, so the migration needs no backfill to be correct."""
    col = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name='document' AND column_name='canonical_manufacturer'"
    ).fetchone()
    assert col == {"data_type": "text", "is_nullable": "YES"}

    fk = conn.execute(
        "SELECT confupdtype FROM pg_constraint "
        "WHERE conrelid='document'::regclass AND contype='f' "
        "  AND conname LIKE '%canonical_manufacturer%'"
    ).fetchone()
    # 'c' = ON UPDATE CASCADE. A rename renames the thing described (mig 052).
    assert fk["confupdtype"] == "c"

    idx = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname='document_manufacturer_idx'"
    ).fetchone()
    assert "canonical_manufacturer" in idx["indexdef"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_migration_document_manufacturer.py`
Expected: FAIL — `assert None == {...}`, the column does not exist.

- [ ] **Step 3: Write the migration**

Create `migrations/053_document_manufacturer.sql`:

```sql
-- 053_document_manufacturer.sql
-- The edge that never existed: a document's CONFIRMED manufacturer.
--
-- Until now a document reached a manufacturer only by fanning out to catalogue
-- items -- group-scope through `item_group`, manufacturer-scope through
-- `mfr-scope` links -- so a document with no item links reached none at all.
-- Measured 2026-08-31: 540 documents in that state, 443 of them `filed`, which
-- is every filed document by definition (C15 files exactly what we stock
-- nothing for, and the manufacturer path runs through what we stock).
--
-- NOT `evidence.manufacturer` (PRD §201), which is the name as PRINTED, with a
-- confidence and a verbatim. This column is what a human or C16 CONFIRMED: a
-- reading versus a decision. Conflating them is the one way this goes wrong.
--
-- Keyed by NAME, not a surrogate id, because every other manufacturer attribute
-- in this schema is -- `item_group.canonical_manufacturer`,
-- `manufacturer_srn.canonical_name`, `eudamed_sweep_state.canonical_name`.
-- Migration 052 built this exact cascade and its argument transfers verbatim:
-- a rename does not invalidate the attribution, it renames the thing described.
--
-- ON DELETE stays NO ACTION, also per 052: deleting a manufacturer something
-- still references should be refused, never silently cascaded.
--
-- Nullable and additive on purpose: no backfill is needed for this migration to
-- be correct, and every existing reader keeps working untouched.

ALTER TABLE document
  ADD COLUMN canonical_manufacturer text
    REFERENCES manufacturer(canonical_name) ON UPDATE CASCADE;

CREATE INDEX document_manufacturer_idx
  ON document (canonical_manufacturer)
  WHERE canonical_manufacturer IS NOT NULL;

COMMENT ON COLUMN document.canonical_manufacturer IS
  'The CONFIRMED manufacturer (human bind or C16), not the printed name in '
  'evidence. Null means undecided, never "none".';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_migration_document_manufacturer.py`
Expected: PASS.

- [ ] **Step 5: Update the schema sketch**

In `docs/dentalia-schema-sketch.md`, inside the `CREATE TABLE document` block, add after the `cert_number` line:

```
  canonical_manufacturer text                  -- 053: the CONFIRMED manufacturer
    REFERENCES manufacturer(canonical_name)     -- (human bind or C16), NOT the
    ON UPDATE CASCADE,                          -- printed name in evidence.
                                                -- Null = undecided, not "none".
```

- [ ] **Step 6: Commit**

```bash
git add migrations/053_document_manufacturer.sql tests/test_migrations.py docs/dentalia-schema-sketch.md
git commit -m "document: the manufacturer edge that never existed"
```

---

### Task 2: A defensive resolver, and the sticky upsert column

**Files:**
- Modify: `app/handlers/gate.py` (`_upsert_document`, plus a new helper above it)
- Test: `tests/test_gate_candidate_handler.py`

**Interfaces:**
- Produces: `gate._bindable_manufacturer(conn, name: str | None) -> str | None` — returns the canonical name to store, or `None` when it must not be stored. `_upsert_document` gains keyword arg `canonical_manufacturer: str | None = None`.

**Why defensive.** The picker offers `COALESCE(a.canonical_name, m.manufacturer_raw)`, so it can offer a bare BC vendor code that has no `manufacturer` row. All 366 entities have one today (verified 2026-08-31), so this cannot fire on live data — but an unguarded write would raise `ForeignKeyViolation` inside the runner's transaction and take the whole gate decision down with it. CLAUDE.md: skipped rows are counted, never silent.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gate_candidate_handler.py`:

```python
def test_bindable_manufacturer_refuses_a_name_with_no_entity_row(conn):
    """The picker can offer a bare BC code with no `manufacturer` row. Writing
    it would raise ForeignKeyViolation inside the runner's transaction and take
    the whole gate decision with it, so it is refused here instead."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('REAL CO')")

    assert gh._bindable_manufacturer(conn, "REAL CO") == "REAL CO"
    assert gh._bindable_manufacturer(conn, "NO SUCH ENTITY") is None
    assert gh._bindable_manufacturer(conn, None) is None
    assert gh._bindable_manufacturer(conn, "  ") is None


def test_the_binding_survives_a_redelivery_that_cannot_recompute_it(conn):
    """Sticky, for the reason `supersedes` / `cert_doc_id` / `source_url` are:
    a re-delivery that happens not to carry the binding must not erase a
    decision a human already made."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('ACME AG')")
    fields = {"type": "ISO", "regulation": "n.a.", "coverage_scope": "manufacturer"}

    doc_id = gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf",
                                 "staged", canonical_manufacturer="ACME AG")
    assert _bound(conn, doc_id) == "ACME AG"

    # Same content hash, no manufacturer this time.
    again = gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf",
                                "staged", canonical_manufacturer=None)
    assert again == doc_id
    assert _bound(conn, doc_id) == "ACME AG"

    # A NEW non-null binding still wins -- re-binding is the correction path.
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('OTHER AG')")
    gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf", "staged",
                        canonical_manufacturer="OTHER AG")
    assert _bound(conn, doc_id) == "OTHER AG"


def _bound(conn, doc_id):
    return conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["canonical_manufacturer"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py -k "bindable_manufacturer or survives_a_redelivery"`
Expected: FAIL — `AttributeError: module 'app.handlers.gate' has no attribute '_bindable_manufacturer'`.

- [ ] **Step 3: Add the helper**

In `app/handlers/gate.py`, immediately above `_upsert_document`:

```python
def _bindable_manufacturer(conn, name: str | None) -> str | None:
    """The canonical name safe to store on `document`, or None.

    `document.canonical_manufacturer` is an FK to `manufacturer(canonical_name)`
    (migration 053), and the review picker offers
    `COALESCE(a.canonical_name, m.manufacturer_raw)` -- so it can offer a bare BC
    vendor code that has no `manufacturer` row. All 366 offerable entities have
    one as of 2026-08-31, so this cannot fire on live data; it exists because an
    unguarded write would raise ForeignKeyViolation inside the runner's
    transaction and take the whole gate decision down with it. Refusing to store
    a binding is recoverable; losing the decision is not.
    """
    if not name or not name.strip():
        return None
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE canonical_name = %s",
        (name.strip(),),
    ).fetchone()
    return row["canonical_name"] if row else None
```

- [ ] **Step 4: Add the sticky column to `_upsert_document`**

Change the signature:

```python
def _upsert_document(conn, fields, content_hash, archive_url, status, *,
                     supersedes=None, related_doc_id=None, cert_doc_id=None,
                     canonical_manufacturer=None) -> int:
```

Add `canonical_manufacturer` to the INSERT column list (after `source_url`), add one `%s` to the VALUES tuple, and add to the `DO UPDATE SET` block, beside the other sticky columns:

```sql
            -- sticky with supersedes / cert_doc_id / source_url above, and for
            -- the same reason: a CONFIRMED manufacturer is an additive fact, so
            -- a re-delivery that cannot recompute it must not erase it. A newer
            -- non-null binding still wins, which is what makes re-binding the
            -- correction path.
            canonical_manufacturer = COALESCE(EXCLUDED.canonical_manufacturer,
                                              document.canonical_manufacturer)
```

Pass `canonical_manufacturer` through in the parameter tuple in the same position as the column list.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py -k "bindable_manufacturer or survives_a_redelivery"`
Expected: PASS.

- [ ] **Step 6: Run the handler suites for regressions**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py tests/test_gate_apply_handler.py`
Expected: PASS — every existing call site passes `canonical_manufacturer=None` by default, so nothing changes yet.

- [ ] **Step 7: Commit**

```bash
git add app/handlers/gate.py tests/test_gate_candidate_handler.py
git commit -m "gate: carry a confirmed manufacturer, sticky like the other additive facts"
```

---

### Task 3: The human bind writes the binding

**Files:**
- Modify: `app/handlers/gate.py` (`handle_gate_apply`, the `bind-manufacturer` branch)
- Test: `tests/test_gate_apply_handler.py`

**Interfaces:**
- Consumes: `_bindable_manufacturer` from Task 2.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gate_apply_handler.py`:

```python
def test_bind_manufacturer_records_the_binding_even_with_no_md_items(conn):
    """The 3Shape case, end to end. Every item we hold from them has a blank BC
    device class, so zero links are written -- [mfr-bind-empty-class] stands --
    but the DECISION is now recorded, which is the whole point of migration 053.
    """
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('3SHAPE TRIOS A/S')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('10005','3SHAPE TRIOS A/S')")
    doc_id = _seed_staged_doc(conn, "ga-3shape")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "S-1", "10005", md_flag=None)   # blank BC class
    _seed_item(conn, "S-2", "10005", md_flag=None)

    gh.handle_gate_apply(
        conn, _apply(doc_id, "bind-manufacturer", manufacturer="3SHAPE TRIOS A/S"))

    assert _bound(conn, doc_id) == "3SHAPE TRIOS A/S"
    assert conn.execute(
        "SELECT count(*) c FROM item_document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["c"] == 0


def test_bind_manufacturer_still_refuses_a_name_resolving_to_no_codes(conn):
    """[gate-bind-zero-links] is CORRECTED, not reversed. The raise guards an
    unresolvable NAME, which is still a real bug; it never guarded a resolvable
    name holding no MD item, and the previous comments claiming otherwise were
    wrong."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('GHOST AG')")
    doc_id = _seed_staged_doc(conn, "ga-ghost")
    _seed_manual(conn, doc_id)

    with pytest.raises(ValueError, match="resolves to no BC manufacturer codes"):
        gh.handle_gate_apply(
            conn, _apply(doc_id, "bind-manufacturer", manufacturer="GHOST AG"))


def _bound(conn, doc_id):
    return conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["canonical_manufacturer"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./scripts/test.sh tests/test_gate_apply_handler.py -k "records_the_binding or still_refuses_a_name"`
Expected: FAIL — the first because `canonical_manufacturer` is null; the second because `_derive_mfr_scope_links` raises before the binding is written and the message may differ.

- [ ] **Step 3: Write the binding before deriving links**

In `handle_gate_apply`, replace the `bind-manufacturer` branch:

```python
    elif decision == "bind-manufacturer":   # C4 §7b
        # The binding is recorded BEFORE the links are derived, and survives a
        # derivation that produces none. That ordering is the whole of 053: a
        # manufacturer whose every item carries a blank BC device class links
        # nothing today ([mfr-bind-empty-class]) and the decision must still be
        # stored, or the reviewer's answer is discarded the moment they give it.
        bound = _bindable_manufacturer(conn, p.get("manufacturer"))
        if bound:
            conn.execute(
                "UPDATE document SET canonical_manufacturer=%s WHERE doc_id=%s",
                (bound, doc_id))
        _promote(conn, doc_id)
        _derive_mfr_scope_links(conn, doc_id, p.get("manufacturer"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./scripts/test.sh tests/test_gate_apply_handler.py -k "records_the_binding or still_refuses_a_name"`
Expected: PASS.

- [ ] **Step 5: Run the whole apply suite**

Run: `./scripts/test.sh tests/test_gate_apply_handler.py`
Expected: PASS, 23 tests.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/gate.py tests/test_gate_apply_handler.py
git commit -m "gate.apply: record the binding before deriving links, so a zero-link bind still counts"
```

---

### Task 4: The machine bind (C16) writes the binding

**Files:**
- Modify: `app/handlers/gate.py` (`_handle_mfr_binding` — all three `_upsert_document` calls)
- Test: `tests/test_gate_candidate_handler.py`

**Interfaces:**
- Consumes: `_bindable_manufacturer`, and `_upsert_document(..., canonical_manufacturer=)` from Task 2.

- [ ] **Step 1: Write the failing test**

```python
def test_c16_records_the_binding_on_every_disposition_it_reaches(conn):
    """`mfr-bound`, `filed` and the md-class-unknown staging branch all resolved
    the manufacturer -- that is what separates them from the unresolved case --
    so all three must store it. Filed is the one that matters most: 443 of the
    540 link-less documents are filed, and this is what makes them queryable."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('KNOWN AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('K1','KNOWN AG')")
    _seed_item(conn, "K-BLANK", "K1", md_flag=None)   # blank class -> staged

    out = gh.handle_gate_candidate(conn, _candidate(
        content_hash="c16-blank", coverage_scope="manufacturer",
        manufacturer="KNOWN AG"))

    assert out["disposition"] == "mfr-binding"
    assert out["reason"] == "md-class-unknown"
    assert _bound(conn, out["doc_id"]) == "KNOWN AG"
```

Use the file's existing `_candidate` / `_seed_item` helpers; read them before writing the test and match their signatures exactly rather than inventing arguments.

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py::test_c16_records_the_binding_on_every_disposition_it_reaches`
Expected: FAIL — `assert None == 'KNOWN AG'`.

- [ ] **Step 3: Thread the binding through all three call sites**

In `_handle_mfr_binding`, compute once near the top, after `bound` is established:

```python
    storable = _bindable_manufacturer(conn, bound)
```

Then add `canonical_manufacturer=storable` to each of the three `_upsert_document(...)` calls in that function — the `md-class-unknown` staging branch, and the shared `status = "production" if n_items else "filed"` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py::test_c16_records_the_binding_on_every_disposition_it_reaches`
Expected: PASS.

- [ ] **Step 5: Run the candidate suite**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py`
Expected: PASS, 105 tests.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/gate.py tests/test_gate_candidate_handler.py
git commit -m "gate.candidate: C16 stores the manufacturer it resolved, filed included"
```

---

### Task 5: The group-scope path writes the binding

**Files:**
- Modify: `app/handlers/gate.py` (`handle_gate_candidate`, the main `_upsert_document` call)
- Test: `tests/test_gate_candidate_handler.py`

**Interfaces:**
- Consumes: `_bindable_manufacturer`, `manufacturers.resolve_canonicals`.

**Rule:** write only when `resolve_canonicals` returns **exactly one** canonical. Zero or many leaves the column null. This path never guesses — an ambiguous name is precisely the case a human must settle.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_group_scope_document_records_its_resolved_manufacturer(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('GRP AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('Grp A.G.','GRP AG')")

    out = gh.handle_gate_candidate(conn, _candidate(
        content_hash="grp-1", coverage_scope="group", manufacturer="Grp A.G."))

    assert _bound(conn, out["doc_id"]) == "GRP AG"


def test_an_ambiguous_name_leaves_the_binding_null_rather_than_guessing(conn):
    """Two canonicals is the case a human must settle. Picking one here would
    attach a company's certificate to another company's products."""
    for n in ("AMB ONE", "AMB TWO"):
        conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s)", (n,))
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s)",
            ("Ambiguous Ltd", n))

    out = gh.handle_gate_candidate(conn, _candidate(
        content_hash="grp-amb", coverage_scope="group", manufacturer="Ambiguous Ltd"))

    assert _bound(conn, out["doc_id"]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py -k "group_scope_document_records or ambiguous_name_leaves"`
Expected: FAIL on the first (`None != 'GRP AG'`); the second may pass vacuously — that is fine, it is a regression guard.

- [ ] **Step 3: Resolve and pass it through**

In `handle_gate_candidate`, before the main `_upsert_document` call:

```python
    # Only an UNAMBIGUOUS resolution is stored. Zero canonicals means we cannot
    # name the manufacturer; more than one means a human must choose, and
    # guessing here would attach one company's document to another's products.
    _extracted = _val(fields, "manufacturer")
    _canonicals = manufacturers.resolve_canonicals(conn, _extracted) if _extracted else []
    _bound_group = (_bindable_manufacturer(conn, _canonicals[0])
                    if len(_canonicals) == 1 else None)
```

Add `canonical_manufacturer=_bound_group` to that `_upsert_document` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py -k "group_scope_document_records or ambiguous_name_leaves"`
Expected: PASS.

- [ ] **Step 5: Run both handler suites plus validate**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py tests/test_gate_apply_handler.py tests/test_validate_handler.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/gate.py tests/test_gate_candidate_handler.py
git commit -m "gate: a group-scope document records the manufacturer it resolved, when it is unambiguous"
```

---

### Task 6: The picker offers every manufacturer

**Files:**
- Modify: `web/app.py` (`_mfr_binding_options`, delete `_mfr_unbindable_entities`, `_decorate_review_row`)
- Modify: `web/templates/_staging_doc_detail.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `_mfr_binding_options(conn)` returns **all** catalogue entities with `md_items` counts including 0, still ordered by name.

- [ ] **Step 1: Rewrite the two tests that pin the restriction**

Replace `test_picker_offers_only_manufacturers_a_binding_would_link` with:

```python
def test_picker_offers_every_manufacturer_including_those_with_no_md_items(
        client, conn, picker_rows):
    """Denis, 2026-08-31: "reviewer should be able to select what they want to
    select". A blank BC device class is a catalogue gap, not a reason to refuse
    the reviewer a decision -- and since 053 the binding is stored even when it
    links nothing."""
    body = _staging_body(client)
    assert "3SHAPE TRIOS A/S (0 medical-device products)" in body
    assert "CARL MARTIN (2 medical-device products)" in body
    # The old "which leaves N out" prose described a restriction that is gone.
    assert "leaves" not in body
```

Delete `test_picker_reports_a_resolvable_manufacturer_that_would_link_nothing` — the state it describes (a resolvable manufacturer the picker refuses to offer) no longer exists. Note the deletion in the commit message.

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/test.sh tests/test_web.py -k picker_offers_every_manufacturer`
Expected: FAIL — 3SHAPE is absent from the rendered body.

- [ ] **Step 3: Drop the `md_flag` filter**

In `_mfr_binding_options`, remove the `WHERE m.md_flag IS TRUE` clause and count MD items conditionally instead:

```python
    return conn.execute(
        "SELECT COALESCE(a.canonical_name, m.manufacturer_raw) AS canonical_name, "
        "       count(DISTINCT m.item_ref) FILTER (WHERE m.md_flag IS TRUE) AS md_items "
        "FROM item_mirror m "
        "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
        "GROUP BY COALESCE(a.canonical_name, m.manufacturer_raw) "
        "ORDER BY 1"
    ).fetchall()
```

Rewrite its docstring: it previously explained why a zero-item entity was withheld. It now explains that the count is shown so the reviewer knows the volume of what they are approving, and that zero is a legitimate, bindable answer.

Delete `_mfr_unbindable_entities` entirely and its `row["mfr_unbindable"]` assignment in `_decorate_review_row`.

- [ ] **Step 4: Update the template**

In `web/templates/_staging_doc_detail.html`, delete the `{%- if row.mfr_unbindable %}` block and its "which leaves N out" sentence, leaving:

```html
    <p class="hint">Approving links this document to every medical-device product of the
      manufacturer you choose, and records the manufacturer even when there are none
      to link yet.</p>
```

Also delete the `{%- elif s.unbindable %}` branch above it — nothing is unbindable now — and the `suggestion["unbindable"]` assignment in `_decorate_review_row`, so a resolvable manufacturer is simply preselected.

- [ ] **Step 5: Run to verify it passes**

Run: `./scripts/test.sh tests/test_web.py -k "picker or mfr_binding or staging_doc_detail"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/app.py web/templates/_staging_doc_detail.html tests/test_web.py
git commit -m "staging: offer every manufacturer, since a blank device class no longer blocks the decision"
```

---

### Task 7: The review sentence, and the 17 stale tasks

**Files:**
- Modify: `web/app.py` (`MFR_BINDING_UNKNOWN_CLASS_REASON`, and the comment block above it)
- Create: `app/repair_mfr_task_reason.py`
- Test: `tests/test_web.py`, `tests/test_repair_mfr_task_reason.py`

- [ ] **Step 1: Rewrite the sentence test**

Update `test_an_unknown_class_binding_does_not_promise_links_it_cannot_make` — keep the name, change the assertion, since the sentence must still differ from the default but now says something else:

```python
    unknown = _review_reason([], is_mfr_binding=True, has_task=True,
                             task_reason="md-class-unknown")
    assert unknown == MFR_BINDING_UNKNOWN_CLASS_REASON
    assert "blank device class" in unknown
    # It must no longer tell the reviewer to wait -- Denis reversed that
    # 2026-08-31. The binding is recorded now; only the links wait.
    assert "leave this until" not in unknown
    assert "recorded" in unknown
```

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/test.sh tests/test_web.py -k unknown_class_binding`
Expected: FAIL — the current string still contains "leave this until".

- [ ] **Step 3: Rewrite the constant**

```python
MFR_BINDING_UNKNOWN_CLASS_REASON = (
    "This covers a manufacturer's whole range, but every product we hold from "
    "them has a blank device class in Business Central — so there is nothing to "
    "link yet. Approving still records the manufacturer; the product links "
    "follow once the class is populated in BC."
)
```

Replace the comment block above it: the `[gate-bind-zero-links]` claim that `gate.apply` "would refuse it" is wrong (it guards unresolvable names, never zero links) and the refusal no longer applies regardless.

- [ ] **Step 4: Write the backfill tool test**

```python
def test_backfill_stamps_the_reason_on_tasks_that_predate_it(conn):
    """The 17 open mfr-binding tasks were raised before `reason` existed, so
    every one renders the DEFAULT sentence -- the exact sentence the
    md-class-unknown wording was written to replace."""
    from app import repair_mfr_task_reason as r

    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('BLANK AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('B1','BLANK AG')")
    _seed_item(conn, "B-1", "B1", md_flag=None)
    doc_id = _seed_staged_doc(conn, "reason-1")
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) "
        "VALUES ('gate-manual', %s, '{\"route\":\"mfr-binding\",\"manufacturer\":\"BLANK AG\"}')",
        (doc_id,))

    assert r.plan(conn) == [{"doc_id": doc_id, "reason": "md-class-unknown"}]
    assert r.apply(conn) == 1
    assert r.plan(conn) == []      # second run plans zero
```

- [ ] **Step 5: Write the tool**

Create `app/repair_mfr_task_reason.py` with `plan(conn) -> list[dict]` and `apply(conn) -> int`. `plan` selects open `mfr-binding` tasks whose payload has no `reason`, and classifies each by the same tri-state `md_flag` test `_handle_mfr_binding` uses — `md-class-unknown` when the manufacturer's codes hold no `TRUE` and no `FALSE`, otherwise leave alone. `apply` writes `payload = payload || '{"reason": ...}'::jsonb` and inserts one `audit_log` row per task.

- [ ] **Step 6: Run both tests**

Run: `./scripts/test.sh tests/test_repair_mfr_task_reason.py tests/test_web.py -k "unknown_class_binding or backfill_stamps"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/app.py app/repair_mfr_task_reason.py tests/
git commit -m "staging: the blank-class sentence stopped being true, and 17 tasks never carried a reason"
```

---

### Task 8: The read path prefers the column

**Files:**
- Modify: `web/catalogue.py` (`manufacturer_documents`, `manufacturer_summary`)
- Test: `tests/test_catalogue_api.py` (or the file holding the `/api/manufacturers` tests — locate it first with `grep -rl "api/manufacturers" tests/`)

**Interfaces:**
- Produces: `manufacturer_documents(conn, canonical_name, *, view="full", base_url="")` — default payload unchanged (production-only); `view="all"` additionally returns documents bound by column regardless of link status.

**Contract:** the default stays production-only. A filed document is real and held but explicitly *not* coverage; returning it unannounced would change what an existing consumer's payload means.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_filed_document_is_reachable_by_binding_but_not_by_default(conn):
    """Measured 2026-08-31: /api/manufacturers/VOCO/documents returns 32 of the
    270 documents held, because everything without a production link is
    structurally invisible. The column fixes that -- behind `view`, so the
    existing contract holds."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('FILED AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('F1','FILED AG')")
    _seed_item(conn, "F-1", "F1")
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, canonical_manufacturer) "
        "VALUES ('ISO','n.a.','manufacturer','fh','file:///f.pdf','filed','FILED AG') "
        "RETURNING doc_id").fetchone()["doc_id"]

    default = manufacturer_documents(conn, "FILED AG")
    assert [d["doc_id"] for d in default] == []

    widened = manufacturer_documents(conn, "FILED AG", view="all")
    assert [d["doc_id"] for d in widened] == [doc_id]


def test_a_link_only_document_still_resolves_with_a_null_column(conn):
    """The fallback half of "column wins, links fill gaps": nothing that works
    today may stop working."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('LINK AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('L1','LINK AG')")
    doc_id = _seed_production_doc_with_link(conn, "L-1", "L1")

    assert [d["doc_id"] for d in manufacturer_documents(conn, "LINK AG")] == [doc_id]
```

- [ ] **Step 2: Run to verify they fail**

Run: `./scripts/test.sh tests/test_catalogue_api.py -k "filed_document_is_reachable or link_only_document_still"`
Expected: FAIL — `view="all"` is not honoured and returns the same empty list.

- [ ] **Step 3: Add the column branch**

In `manufacturer_documents`, keep the existing production query as the default and union the column-bound set when `view == "all"`:

```python
    if view == "all":
        docs = conn.execute(
            "SELECT DISTINCT ON (d.doc_id) d.doc_id, 'mfr-bound' AS match_basis, "
            "       d.type, d.regulation, d.validity_from AS valid_from, "
            "       d.validity_to AS valid_to, NULL AS expiry_basis "
            "FROM document d WHERE d.canonical_manufacturer = %s "
            "UNION "
            "<the existing production query>"
            "ORDER BY doc_id", (canonical_name, codes)).fetchall()
```

Read the existing query before writing this and keep its column list identical, or the `UNION` will not type-check.

- [ ] **Step 4: Run to verify they pass**

Run: `./scripts/test.sh tests/test_catalogue_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/catalogue.py tests/
git commit -m "api: a bound document is reachable under view=all, default stays production-only"
```

---

### Task 9: `rename_impact` reports the documents it will now touch

**Files:**
- Modify: `app/regroup.py` (`rename_impact`, `RenameImpact`)
- Test: `tests/test_regroup.py` (locate with `grep -rl rename_impact tests/`)

**Why:** with `ON UPDATE CASCADE`, a rename now also rewrites N `document` rows. `rename_impact`'s docstring claims its numbers are "the ones a rename decision turns on". Silently understating the blast radius is the exact failure migration 052 was written to close.

- [ ] **Step 1: Write the failing test**

```python
def test_rename_impact_counts_the_documents_the_cascade_will_rewrite(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('REN AG')")
    for h in ("r1", "r2"):
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status, canonical_manufacturer) "
            "VALUES ('ISO','n.a.','manufacturer',%s,'file:///r.pdf','filed','REN AG')", (h,))

    assert rename_impact(conn, "REN AG").documents == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/test.sh tests/test_regroup.py -k rename_impact_counts_the_documents`
Expected: FAIL — `AttributeError: 'RenameImpact' object has no attribute 'documents'`.

- [ ] **Step 3: Add the field and the count**

Add `documents: int = 0` to `RenameImpact`. In `rename_impact`, count before the early return so a manufacturer with no groups but with documents still reports:

```python
    documents = conn.execute(
        "SELECT count(*) AS n FROM document WHERE canonical_manufacturer = %s",
        (manufacturer,),
    ).fetchone()["n"]
```

Pass it into both `RenameImpact(...)` constructions, and extend the docstring to say the third number exists because 053's FK cascades.

- [ ] **Step 4: Run the regroup suite**

Run: `./scripts/test.sh tests/test_regroup.py`
Expected: PASS, including the test pinning `rename_impact` as a subset of `scope()`.

- [ ] **Step 5: Surface it in the rename UI**

In the rename confirm template, add the document count beside the group and item counts. Locate it with `grep -rn "with_documents" web/templates/`.

- [ ] **Step 6: Commit**

```bash
git add app/regroup.py web/templates/ tests/test_regroup.py
git commit -m "rename: report the documents the new cascade will rewrite"
```

---

### Task 10: Backfill the 481

**Files:**
- Create: `app/repair_document_manufacturer.py`
- Test: `tests/test_repair_document_manufacturer.py`

**Interfaces:**
- Produces: `plan(conn) -> dict` with keys `resolved` (list of `{doc_id, canonical_name}`), `ambiguous` (int), `unresolved` (list of str). `apply(conn) -> int`.

**Measured 2026-08-31:** 531 evidence strings → 481 resolve to exactly one canonical, 0 ambiguous, 50 to none. The 50 stay null; minting entities for them would seed the registry from OCR.

- [ ] **Step 1: Write the failing tests**

```python
def test_backfill_binds_only_unambiguous_names_and_counts_the_rest(conn):
    from app import repair_document_manufacturer as r

    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('BF AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('BF Ltd','BF AG')")
    good = _seed_doc_with_mfr_evidence(conn, "bf-1", "BF Ltd")
    bad = _seed_doc_with_mfr_evidence(conn, "bf-2", "Nobody Ltd")

    out = r.plan(conn)
    assert out["resolved"] == [{"doc_id": good, "canonical_name": "BF AG"}]
    assert out["unresolved"] == ["Nobody Ltd"]
    assert out["ambiguous"] == 0

    assert r.apply(conn) == 1
    assert _bound(conn, good) == "BF AG"
    assert _bound(conn, bad) is None
    assert r.plan(conn)["resolved"] == []      # idempotent


def test_backfill_never_overwrites_a_binding_already_made(conn):
    """A human decision outranks a string read off a page."""
    from app import repair_document_manufacturer as r
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('BF AG'), ('HUMAN AG')")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('BF Ltd','BF AG')")
    doc_id = _seed_doc_with_mfr_evidence(conn, "bf-3", "BF Ltd")
    conn.execute("UPDATE document SET canonical_manufacturer='HUMAN AG' WHERE doc_id=%s",
                 (doc_id,))

    assert r.plan(conn)["resolved"] == []
    assert _bound(conn, doc_id) == "HUMAN AG"
```

- [ ] **Step 2: Run to verify they fail**

Run: `./scripts/test.sh tests/test_repair_document_manufacturer.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repair_document_manufacturer'`.

- [ ] **Step 3: Write the tool**

Model its shape on `app/repair_mfr_overbind.py` — read that file first. `plan` selects documents where `canonical_manufacturer IS NULL` that carry a latest-rev `manufacturer` evidence row, resolves each through `manufacturers.resolve_canonicals`, then filters through `gate._bindable_manufacturer`. `apply` writes each and inserts one self-contained `audit_log` row per document. Both counts go on the return value; nothing is silent.

- [ ] **Step 4: Run to verify they pass**

Run: `./scripts/test.sh tests/test_repair_document_manufacturer.py`
Expected: PASS.

- [ ] **Step 5: Dry-run against the dev database and check the numbers**

Run the tool's `plan` against dev and confirm it reports close to `481 resolved / 0 ambiguous / 50 unresolved`. A materially different split means the resolver changed and the spec's measurement needs re-taking — stop and report rather than applying.

- [ ] **Step 6: Commit**

```bash
git add app/repair_document_manufacturer.py tests/test_repair_document_manufacturer.py
git commit -m "backfill: bind the 481 documents whose manufacturer resolves unambiguously"
```

---

### Task 11: Correct the three statements that describe a guard that does not exist

**Files:**
- Modify: `web/app.py:284-286` (comment), `tests/test_web.py:743-745` (docstring), `app/handlers/gate.py:1171-1180` (comment)

- [ ] **Step 1: Correct the three comments**

`_derive_mfr_scope_links` raises on `if not codes` — an unresolvable *name*. A name resolving to BC codes that hold no MD item does **not** raise. Verified 2026-08-31: `3SHAPE TRIOS A/S` → `['10004','10005']`. Each of the three places currently says it "refuses a zero-link bind". Rewrite each to say it refuses an **unresolvable manufacturer name**, and note that a resolvable manufacturer with no MD items is now a legitimate, recorded binding.

No behaviour change and no new test: Task 3 already pins the raise.

- [ ] **Step 2: Verify nothing changed behaviourally**

Run: `./scripts/test.sh tests/test_web.py tests/test_gate_apply_handler.py`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/app.py tests/test_web.py app/handlers/gate.py
git commit -m "comments: the zero-link guard catches unresolvable names, not zero links"
```

---

### Task 12: Sync the contract, run the full suite

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md`, `docs/dentalia-job-type-handbook.md`, `docs/README.md` (code-map if it lists `document` columns), `PHASES.md` (decision log), `tasks/followups.md`

- [ ] **Step 1: Add the column to the PRD data model**

Add `canonical_manufacturer` to PRD v3's `document` description with a sync-dated note, stating it is the CONFIRMED manufacturer and explicitly not §201's printed `manufacturer` field. Do not restate C4 — C4 already describes the binding; this stores its input.

- [ ] **Step 2: File the deferred work as followups**

Append to `tasks/followups.md`, format `- [ ] YYYY-MM-DD [topic] one-line task`:

- `[item-mirror-manufacturer-raw-unindexed]` — `S` — no index on `item_mirror.manufacturer_raw` though every manufacturer→documents and manufacturer→items query filters on it; measured seq scan removing 15 936 rows to find 22. Independent of 053, ship alone.
- `[mfr-scope-link-loop-is-per-row]` — `M` — `_derive_mfr_scope_links` writes one round-trip per item; measured floor 0.503 ms each, so ≥1.29 s for doc 816 today and ≥7.58 s at ~100k items, inside the runner's transaction. `INSERT ... SELECT` removes the loop.
- `[bound-document-link-rederivation]` — `M` — a bound document does not gain links when BC populates `md_class`. Interacts with `[filed-relink]`; build whichever lands first with the other in mind.

- [ ] **Step 3: Run the full suite**

Run: `./scripts/test.sh`
Expected: PASS, 2646 passed / 5 skipped, ~150s. Required — this plan adds a `migrations/` file, which CLAUDE.md's selection rule puts in the full-suite bucket.

- [ ] **Step 4: Commit**

```bash
git add docs/ PHASES.md tasks/followups.md
git commit -m "docs: sync the contract to the confirmed-manufacturer column"
```

---

## Self-Review

**Spec coverage.** §3.1 → Task 1. §3.2 → Tasks 2–5. §3.3 → Tasks 6–7. §3.4 → Task 8. §3.5 → Task 10. §5 corrections → Tasks 7 and 11. §6 `rename_impact` → Task 9. §7 testing → distributed, full suite in Task 12. §8 slices map: slice 1 = Tasks 1–5, slice 2 = Tasks 6–7, slice 3 = Tasks 8–9, slice 4 = Tasks 10–11. §9 non-goals → filed as followups in Task 12, none implemented. §11 findings → the index and the loop filed in Task 12.

**Type consistency.** `_bindable_manufacturer(conn, name) -> str | None` defined in Task 2, consumed in Tasks 3, 4, 5, 10. `_upsert_document(..., canonical_manufacturer=None)` defined in Task 2, used in Tasks 4 and 5. `_bound(conn, doc_id)` is a test helper defined in each test file that uses it — repeated deliberately, since the files are independent.

**Known soft spots**, flagged rather than papered over: Task 4's test relies on `_candidate`, and Task 8's on `_seed_production_doc_with_link`, whose exact signatures live in files the implementer must read first. Both steps say so. Task 8's UNION needs the existing query's column list copied exactly; the step says so.
