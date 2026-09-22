# Read API Contract Implementation Plan

**Goal:** Make `GET /api/items/{item_ref}/documents` a contract a consumer (BC, webshop, support) can integrate against — effective expiry instead of a misleading raw date, a human-readable document name, a written response contract, and a one-click way to eyeball the JSON from the web UI.

**Architecture:** The `item_document_production` view gains two **appended** columns (`expires`, `expiry_basis`) by joining `document_effective_expiry` — the single expiry definition (migration 027) — while `validity_to` keeps its raw meaning so no existing consumer shifts. The API switches from `SELECT *` to an explicit column list (view drift can no longer silently change the response), derives `name` from latest-rev manufacturer evidence, and the shape gets a spec file plus a UI link on the item page.

**Deliberately out of scope** (blocked on the `[serving-mechanism-and-doc-names]` ruling — direct exposure vs relay through webshop/BC): consumer auth beyond the Caddy ingress, absolute/durable archive URLs, any stored title column. The response shape below survives that ruling either way.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL), FastAPI + Jinja (no build step), pytest against a real Postgres.

**Verified against:** HEAD on 2026-08-21. Key references: current view definition `migrations/005_registry.sql:58-61` (defined once, never redefined since — grepped all migrations); `document_effective_expiry` `migrations/027_effective_expiry.sql:42-80` (LEAST of stated / cert-inherited / DoC 5-year staleness, with `basis`); API route `web/app.py:3049` (`SELECT * FROM item_document_production`); evidence columns `migrations/005_registry.sql:64-76` + `extract_rev` from `014_evidence_rev.sql:11`; view consumers: `web/app.py:1340` (ANY_PAPER), `:1354` (DEVICE_PAPER), `:1399` (match_basis counts), `web/registry.py:39,177,187`; API test patterns `tests/test_web.py:2106,2127,2158`.

**Spec:** conversation-approved design, Denis 2026-08-21, plus the open defect it closes: `tasks/followups.md` `[task4-cert-view]` — "the JSON API already serves a null expiry for a DoC whose renewal date sits on its cited certificate... the UI and the API now disagree about the same document. Re-triaged fix-now."

## Global Constraints

- **View change is ADDITIVE only.** `CREATE OR REPLACE VIEW` in Postgres may only append columns — keep the existing column list byte-identical and add `expires`, `expiry_basis` at the end. `validity_to` keeps its raw (stated) meaning; only the API's explicit SELECT decides what `valid_to` means to consumers.
- Production-only visibility is untouchable: both `document.status` and `item_document.status` must remain `'production'` filters (invariant C5).
- No auth logic in `web/app.py` — Caddy stays the sole ingress (G3 v0).
- Migration number 034 assumes plan `2026-08-21-stated-class-extraction.md` lands first (it takes 032/033); take the next free number at execution time otherwise. The two plans are otherwise independent — neither reads the other's columns.
- Each task touches at most 3 files, tests excluded.
- Isolated test env, once per shell:
  ```bash
  export WT='COMPOSE_PROJECT_NAME=dentalia_wt POSTGRES_PORT=5433 PGDATA_HOST=/srv/pgdata/dentalia_wt'
  ```

---

### Task 1: View gains effective expiry — migration 034

**Files:**
- Create: `migrations/034_item_document_production_expiry.sql`
- Modify: `docs/dentalia-schema-sketch.md` (§5 — replace the stale view definition; it currently shows the 005 shape)
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `item_document_production` with two new trailing columns: `expires date` (the 027 definition) and `expiry_basis text` (`stated|inherited|staleness`, NULL when no date exists).

- [x] **Step 1: Write the failing test.** Grep `tests/test_web.py` for how existing tests seed a DoC citing a certificate (`cert_doc_id` — the migration-020 tests or the `/expiry` tests do this; clone that arrangement):

  ```python
  def test_production_view_serves_the_effective_expiry(db):
      # seed: production cert with validity_to=2027-05-01; production DoC with
      # validity_to=NULL, cert_doc_id -> cert; production link on the DoC
      row = db.execute(
          "SELECT validity_to, expires, expiry_basis FROM item_document_production "
          "WHERE doc_id = %s", (doc_id,)).fetchone()
      assert row["validity_to"] is None                # raw meaning unchanged
      assert row["expires"] == date(2027, 5, 1)        # inherited from the cited cert
      assert row["expiry_basis"] == "inherited"
  ```

  DEVIATION: used the real fixtures (`conn`, not the pseudocode `db`; `_seed_item_with_doc_citing_cert` at `tests/test_web.py:2995`, not inline seed SQL) — same arrangement the plan pointed at, written the way this file actually does it.

- [x] **Step 2: Run to verify failure** — expected: FAIL (`column "expires" does not exist`). Confirmed: `psycopg.errors.UndefinedColumn: column "expires" does not exist`.
- [x] **Step 3: Migration**

  ```sql
  -- 034: item_document_production learns the effective expiry.
  -- Closes [task4-cert-view]: the view (and therefore /api) served raw validity_to,
  -- disagreeing with the UI pages that already follow document_effective_expiry (027).
  -- Additive: existing columns keep their order and meaning; expires/expiry_basis appended.
  CREATE OR REPLACE VIEW item_document_production AS
    SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
           ee.expires       AS expires,
           ee.basis         AS expiry_basis
      FROM item_document id
      JOIN document d USING (doc_id)
      LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
     WHERE d.status = 'production' AND id.status = 'production';
  ```

- [x] **Step 4: Run the failing test → PASS, then the full consumer sweep.** The view's other readers must be provably unshifted: `$WT ... pytest tests/test_web.py -v` (covers the KPI subqueries at `web/app.py:1340/1354/1399` and the registry joins at `web/registry.py:39/177/187` — all count- or join-based, none read the new columns).

  New test PASSED. Full sweep: 237 passed, 2 failed, 1 warning — `test_collapsed_row_never_offers_a_bind_it_cannot_submit` and `test_collapsed_row_sends_the_binding_decision_to_the_expanded_panel` (`_staging_decide.html` missing "Open to approve" text). Verified PRE-EXISTING and unrelated: temporarily removed migration 034 from `migrations/` and reran just those two — identical failure, byte-for-byte same assertion diff, with the view back to its 005 shape. Restored 034 immediately after. Zero test edits outside the new test, as required.
- [x] **Step 5: Schema-sketch sync** — replace the §5 view definition with the 034 text, keeping the "Visibility rule for ALL consumers" sentence around it intact.
- [x] **Step 6: Commit**

  ```bash
  git add migrations/034_item_document_production_expiry.sql docs/dentalia-schema-sketch.md tests/test_web.py
  git commit -m "registry: the production view serves the effective expiry, additively"
  ```

- [ ] **Step 7: Close the tracked defect.** Move `[task4-cert-view]` in `tasks/followups.md` to its DONE form in place (the file's convention: flip to `- [x]`, prepend `**CLOSED 2026-08-21 (migration 034).**` with one line of evidence). This is completing a tracked item, not triage.

  DEFERRED — followups.md is a shared live file, closed from the main session at merge. Per dispatch instruction, this agent never edits tasks/followups.md.

---

### Task 2: The API contract — explicit columns + derived name

**Files:**
- Modify: `web/app.py` (`api_item_documents`, `web/app.py:3049`)
- Test: `tests/test_web.py`

**Interfaces:**
- Produces the response contract Task 3 documents:

  ```json
  {
    "item_ref": "12345",
    "documents": [
      {
        "doc_id": 42,
        "name": "VOCO GmbH DoC (MDR), valid to 2027-05-01",
        "type": "DoC",
        "regulation": "MDR",
        "match_basis": "ref-list",
        "valid_from": "2023-04-01",
        "valid_to": "2027-05-01",
        "expiry_basis": "inherited",
        "url": "/archive/ab/cd/abcd....pdf"
      }
    ]
  }
  ```

  `valid_to` is the **effective** expiry (`expires`); `url` is the archive route (relative until the serving-mechanism ruling).

- [x] **Step 1: Write the failing tests** (pattern-match `test_api_item_documents_returns_production_only` at `tests/test_web.py:2106`):

  ```python
  def test_api_documents_carry_name_and_effective_expiry(db, client):
      # seed: production DoC (validity_to NULL, cites a cert expiring 2027-05-01),
      # manufacturer evidence rows: rev 1 value "WRONG GmbH", rev 2 value "VOCO GmbH"
      body = client.get(f"/api/items/{item_ref}/documents").json()
      d = body["documents"][0]
      assert set(d) == {"doc_id", "name", "type", "regulation", "match_basis",
                        "valid_from", "valid_to", "expiry_basis", "url"}
      assert d["valid_to"] == "2027-05-01"             # effective, not the raw NULL
      assert d["name"].startswith("VOCO GmbH DoC (MDR)")   # latest-rev evidence wins
      assert "WRONG" not in d["name"]

  def test_api_name_survives_a_document_with_no_manufacturer_evidence(db, client):
      # name degrades to "DoC (MDR), valid to ..." — never a KeyError, never "None DoC"
  ```

  DEVIATION: real fixtures (`conn`, `client`) and `_seed_item_with_doc_citing_cert`, same as Task 1.

- [x] **Step 2: Run to verify failure.** Confirmed: first test failed on the key-set assert (view still returns `SELECT *`'s raw columns, no `name`); second failed with `KeyError: 'name'`.
- [x] **Step 3: Implement.** Replace the `SELECT *` with the explicit list, then one batched evidence query for names:

  ```python
  docs = conn.execute(
      "SELECT doc_id, match_basis, type, regulation, validity_from, "
      "       expires AS valid_to, expiry_basis, archive_url AS url "
      "FROM item_document_production WHERE item_ref = %s ORDER BY doc_id",
      (item_ref,)).fetchall()
  mfr = dict(conn.execute(
      "SELECT DISTINCT ON (doc_id) doc_id, value FROM evidence "
      "WHERE doc_id = ANY(%s) AND field = 'manufacturer' "
      "ORDER BY doc_id, extract_rev DESC",
      ([d["doc_id"] for d in docs],)).fetchall()) if docs else {}
  for d in docs:
      parts = [mfr.get(d["doc_id"]), d["type"],
               f"({d['regulation']})" if d["regulation"] not in (None, "n.a.") else None]
      name = " ".join(p for p in parts if p)
      d["name"] = f"{name}, valid to {d['valid_to']}" if d["valid_to"] else name
  return {"item_ref": item_ref, "documents": docs}
  ```

  Keep the `{item_ref:path}` converter and the ORDER BY exactly as they are.

  DEVIATION (bugfix to the plan's sample, not to its intent): the sample SQL selected `validity_from` unaliased, which would have produced key `validity_from`, not the contracted `valid_from` — added `AS valid_from`. The sample's `mfr = dict(conn.execute(...).fetchall())` is also wrong against this codebase's connections: `app/db.py` sets `row_factory=dict_row` globally, so each row is already a dict, and `dict()` over a list of 2-key dicts unpacks each dict's KEYS as the pair (`iter(dict)` yields keys), not its values — it would build `{"doc_id": "value"}` once, not a doc_id->name mapping. Replaced with an explicit dict comprehension `{row["doc_id"]: row["value"] for row in ...}`, same query, same intent (one batched lookup, latest extract_rev wins).
- [x] **Step 4: Run** `$WT ... pytest tests/test_web.py -k api -v` — the pre-existing api tests (`:2106/:2127/:2158`) must pass **unmodified except** any that asserted the old `SELECT *` key set — update those to the contract keys in the same commit and say so in the commit message.

  10 passed, 0 failed. None of the pre-existing api tests asserted the old `SELECT *` key set (they checked doc_id membership only), so none needed updating.
- [x] **Step 5: Commit** — `git commit -m "api: item documents become a contract - explicit columns, effective expiry, derived name"`

---

### Task 3: Write the contract down — `docs/specs/read-api.md`

**Files:**
- Create: `docs/specs/read-api.md` (naming convention: lower-case hyphenated, one topic per file — matches `t0-layout.md`)
- Modify: `docs/README.md` (index line for the new spec)

- [x] **Step 1: Write the spec.** Contents, all of it (no stubs): the three endpoints (`/api/items/{item_ref}/documents`, `/api/documents/{doc_id}`, `/api/kpi`); the full response example from Task 2's Interfaces block; field-by-field semantics — `valid_to` = `document_effective_expiry.expires` with the `basis` vocabulary `stated|inherited|staleness` quoted from migration 027; `name` derivation rule (latest-rev manufacturer evidence + type + regulation + valid-to, degrading gracefully); the visibility rule verbatim ("a document is visible for an item only when both the document and that item's link are production"); `url` relativity and the explicit note that auth and absolute URLs await the serving-mechanism ruling (`[serving-mechanism-and-doc-names]`); additive-only evolution rule for the response (consumers tolerate unknown fields, never missing ones — same rule as job payloads).

  Also corrected `match_basis`'s barred-basis list against the live constraint: migration 021 widened `item_document_trusted_basis_ck` to include `ref-catalogue` alongside `name-family`/`fetch-context`; the spec now names all three and notes `ref-item` (023) is deliberately not barred.
- [x] **Step 2: Index it** — one line in `docs/README.md` beside the other spec entries. `docs/README.md`'s specs section only carried a directory-level `specs/` row (no per-file entries existed to "index beside") — added a new row for `specs/read-api.md` immediately under it, matching the file-entry style used elsewhere in that table (e.g. the `flow-map-*.html` rows).
- [x] **Step 3: Commit** — `git commit -m "docs: the read API response is now a written contract"`

---

### Task 4: Eyeball it from the UI

**Files:**
- Modify: `web/templates/item_detail.html`
- Test: `tests/test_web.py`

- [x] **Step 1: Write the failing test:**

  ```python
  def test_item_page_links_the_api_json(db, client):
      html = client.get(f"/items/{item_ref}").text
      assert f"/api/items/{item_ref}/documents" in html
  ```

  DEVIATION: real fixtures (`conn`, `client`) and `_seed_item_with_doc(conn, item_ref)` for the seed, matching the file's convention.

- [x] **Step 2: Run to verify failure.** Confirmed FAILED — the assert string was absent from the rendered page.
- [x] **Step 3: Implement.** In `item_detail.html`, next to the documents-table heading, add a plain anchor (template-only change; same origin, so the Caddy session covers it — a human clicks and sees exactly what a consumer receives):

  ```html
  <a class="muted" href="/api/items/{{ item_ref | urlencode }}/documents">API JSON</a>
  ```

  Match the URL-encoding idiom the template already uses for item links (item_refs contain `/` — the `:path` converter handles it server-side, but the href must encode the same way the other templates do; copy their filter).

  DEVIATION (both against reality, not intent): (1) `.muted` is not a class `web/static/css/style.css` defines — grepped, no match. Used the actual muted-link idiom instead: `<p class="hint"><a href="...">...</a></p>`, the same wrapping `data_quality.html`'s "Clear filter" link uses. (2) No existing template urlencodes an `item_ref` link — grepped all five (`items.html`, `search.html`, `manufacturer_detail.html`, `document_detail.html`, `_staging_doc_detail.html`); every one renders the ref raw, because the `:path` converter accepts a literal `/` server-side and percent-encoding it cannot help (uvicorn decodes `%2F` back to a literal slash before routing, per `test_item_detail_reaches_an_item_ref_containing_a_slash`'s own comment, `[item-link-slash-404]`). Rendered `item.item_ref` raw, matching that established idiom rather than introducing the only urlencoded item-ref link in the app.
- [x] **Step 4: Run** the test → PASS; run the full `tests/test_web.py` once. New test PASSED. Full sweep: 240 passed, 2 failed — the same two pre-existing, unrelated failures from Task 1 Step 4 (`test_collapsed_row_never_offers_a_bind_it_cannot_submit`, `test_collapsed_row_sends_the_binding_decision_to_the_expanded_panel`).
- [x] **Step 5: Docs sync check** — `docs/code-map.md`'s `web/templates/` row: one-line touch only if it enumerates item-page elements (grep first). Grepped: the row lists template/partial NAMES and cross-cutting behavior (staging pagination, the pulse strip, nav sections) — it does not enumerate any individual page's internal elements (no table columns, no per-page links/buttons named for any template, `item_detail` included). No touch needed.
- [x] **Step 6: Commit** — `git commit -m "ui: item page links the API JSON it is backed by"`

---

## Self-review notes

- The one behavioral change consumers can observe is deliberate and singular: the API's `valid_to` becomes the effective expiry. Everything else is additive (view columns appended, `name`/`expiry_basis` are new keys, UI link is new). No external consumer exists yet by ruling ("expose a read API from day one, wire nothing"), so the key-set change in Task 2 breaks nobody.
- Regression sweep is built in: Task 1 Step 4 runs every existing view consumer's tests against the redefined view before anything else changes.
- Type consistency: `expires`/`expiry_basis` (Task 1) are the columns Task 2 SELECTs; the JSON keys in Task 2's code match Task 2 Step 1's asserted key set and Task 3's documented example.
