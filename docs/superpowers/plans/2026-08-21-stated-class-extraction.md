# Stated Device Class Extraction Implementation Plan

**Goal:** Extract the MDR risk class a document states (`stated_class`), persist it with evidence, and surface a BC-vs-documents comparison on `/data-quality` — without writing to `item_mirror`, without a single new LLM call, and without changing any disposition.

**Architecture:** `stated_class` becomes the 11th extraction target, **T0-only** — absent from every escalate set, exactly like `referenced_docs` today, so it is captured opportunistically and never triggers T1/T2. GATE persists it as a `document` column plus an `evidence` row (the evidence writer already iterates all fields). History is covered by an append-only repair pass over `document_text` (the `repair_ref_list` shape: new `extract_rev`, old fields win, **no `validate.doc` re-emission**), and the comparison view reads `COALESCE(document.stated_class, latest attempt jsonb)` so the 768 existing documents are compared without ever re-adjudicating them.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL, no ORM), pytest against a real Postgres, no new dependencies, no LLM calls anywhere in this plan.

**Verified against:** HEAD on 2026-08-21 (the playbook-extraction merge is IN — migrations end at `031_extraction_playbook_rev.sql`; `extraction_attempt` carries `playbook_slug`/`playbook_rev`). Key line references: `TARGET` at `app/extract/tiers.py:28-31`; escalate sets `_ESCALATE_ALWAYS`/`_ESCALATE_ITEM_ID`/`_ESCALATE_CERTIFICATE` at `tiers.py:39-77`; `needs_escalation` at `tiers.py:102-124`; `field_ev` at `app/extract/t0_templates.py:41-42`; gate `REQUIRED_FIELDS` at `app/handlers/gate.py:98`, `_score` at `gate.py:247-251`, `_insert_evidence` at `gate.py:523-579`, `_upsert_document` at `gate.py:336-375`; replay precedent `app/repair_ref_list.py:40-110`.

**Spec:** conversation-approved design, Denis 2026-08-21. Decisions restated: (1) BC (`item_mirror.product_class`) stays the source of truth — nothing writes item class from documents; documents *fill blanks and cross-check* via a report. (2) T0-only, no LLM spend. (3) Multi-class documents abstain. (4) Known limit accepted: documents almost never state Ir/Is/Im (10 of 768 measured), so BC `Ir` vs doc `I` is "agree-family", not a conflict.

**Measured baseline (dev DB, 2026-08-21):** 445/694 DoC and 8/23 EC texts state a class with an explicit label ("Class: IIa Rule: 7", "Risk Class (MDR Annex VIII) Class IIa", "Klasse/ Class: Is Regel/ Rule: 6"); zero false positives in sampled contexts. `item_mirror`: 11,693 blank class, Ir 2,569, IIa 1,594, I 69, IIb 33. Today's yield: 1,670 linked items cross-checkable, 130 fillable — grows with every backfill.

## Global Constraints

- **Invariant 1:** only `gate.candidate`/`gate.apply` write `document`/`item_document`/`evidence`. The repair CLI (Task 5) writes only `extraction_attempt` — append-only, same as `repair_ref_list`.
- **Invariant 2:** T0 evidence is legitimately pageless ([evidence-page] ruling 2026-07-31) — `field_ev(..., page=None)` is fine.
- `stated_class` must NEVER: enter `gate.REQUIRED_FIELDS`, appear in any `_ESCALATE_*` set, or add/alter a VALIDATE flag. Tasks 2 and 4 pin each of these with a test.
- Nothing writes `item_mirror.product_class`. Ever.
- No job-type or payload change — the field rides inside `extraction_attempt.fields` (jsonb, schema-free on the write path, `app/handlers/extract.py:131-136`).
- Migration numbers 032/033 assume nothing else lands first; take the next free number at execution time if they collide.
- Each task touches at most 3 files, tests excluded (CLAUDE.md rule 4).
- Tests run in the ISOLATED compose project. Once per shell:
  ```bash
  export WT='COMPOSE_PROJECT_NAME=dentalia_wt POSTGRES_PORT=5433 PGDATA_HOST=/srv/pgdata/dentalia_wt'
  ```
  and prefix test commands with `$WT` as the existing plans do (`$WT docker compose --profile test run --rm test pytest ...`).

  **Execution note (2026-08-21):** `dentalia_wt`/5433 was already taken by a
  concurrent worktree, so this run used `COMPOSE_PROJECT_NAME=dentalia_wta`,
  `POSTGRES_PORT=5434`, `PGDATA_HOST=/srv/pgdata/dentalia_wta`. The
  tracked compose file hardcodes `container_name: dentalia-postgres`, which
  collides across projects; an out-of-tree override renaming it to
  `dentalia-wta-postgres` was used rather than editing the tracked file.
- Nothing skipped is silent: the repair CLI counts scanned / stated / abstained-multi / no-mention / already-has-key on its result.

---

### Task 1: Contract first — PRD, handbook, schema sketch

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (§5 EXTRACT target list; §9 `document` columns)
- Modify: `docs/dentalia-job-type-handbook.md` (§5 target list / worked example)
- Modify: `docs/dentalia-schema-sketch.md` (§5 `document` column; new `item_class_check` view; §7 access matrix note)

**Interfaces:**
- Produces: the contract line every later task implements. Vocabulary: `stated_class ∈ {I, Is, Im, Ir, IIa, IIb, III}`, nullable.

- [x] **Step 1: PRD §5 — add the target.** Find the extraction-target enumeration (search for `cert_number` in §5). Add:

  > `stated_class` — the risk class the document itself states (`I|Is|Im|Ir|IIa|IIb|III`). T0-only: never escalated to T1/T2, never part of GATE scoring, never a VALIDATE flag input. QA/display only — BC (`item_mirror.product_class`) remains the source of truth for item class; comparison surfaces on `/data-quality`, nothing writes back to the mirror. A document naming more than one distinct class abstains (multi-product declarations).

- [x] **Step 2: PRD §9 — add the column** to the `document` row: `stated_class text NULL CHECK (I|Is|Im|Ir|IIa|IIb|III)` with the same one-line QA-only note.
- [x] **Step 3: Handbook §5** — add `stated_class` to the `TARGET` list it quotes and one line to the worked example: `"stated_class": {"value":"IIa","conf":0.95,"tier":"T0","verbatim":"Class: IIa","page":null}`.
- [x] **Step 4: Schema sketch** — §5: add the `document.stated_class` column line; add the `item_class_check` view definition (copy verbatim from Task 6 Step 2 below so the two never drift); §7 matrix: `item_class_check` is SELECT-only for `dentalia_api`, written by nobody (it is a view).
- [x] **Step 5: Commit**

  ```bash
  git add docs/dentalia-pipeline-contract-prd-v3.md docs/dentalia-job-type-handbook.md docs/dentalia-schema-sketch.md
  git commit -m "contract: stated_class, an eleventh extraction target that never escalates and never scores"
  ```

---

### Task 2: Register the field — TARGET, LLM schema, corpus tool; pin no-escalation

> **Executed AFTER Task 3** — see the note on Task 3. Two further deviations,
> both found by reading the code rather than the plan's line numbers:
> (c) `tools/corpus.py` needed NO edit: `COLUMNS` is already derived
> (`_BASE_COLUMNS + tuple(f"t0_{f}" for f in tiers.TARGET)`), as are the
> `t0_yield` aggregates, so `t0_stated_class` appeared on its own.
> (d) an edit the plan did not list was required instead:
> `t0_templates.T0_PRODUCED` gains `stated_class`, because PRD 5 makes
> `T0_PRODUCED`/`T0_EXEMPT` partition `TARGET` exactly and a test enforces it.
> (e) a THIRD such contract, caught only by the full-suite run at the end and
> fixed in a follow-up commit: `tests/test_vocabulary_doc.py` asserts every
> `tiers.TARGET` field is documented in `docs/vocabulary.md`. Three registration
> contracts hang off `TARGET`, and the plan listed none of them — worth knowing
> before the twelfth field is added.

**Files:**
- Modify: `app/extract/tiers.py:28-31` (`TARGET`)
- Modify: `app/extract/llm.py:68-72` (`_FIELD_VALUE` props)
- Modify: `tools/corpus.py` (`COLUMNS`)
- Test: `tests/test_tiers.py`

**Interfaces:**
- Produces: `"stated_class" in tiers.TARGET`; guaranteed absent from `_ESCALATE_ALWAYS`, `_ESCALATE_ITEM_ID`, `_ESCALATE_CERTIFICATE`.
- Consumes: nothing from other tasks (safe to do first among code tasks).

- [x] **Step 1: Write the failing test** in `tests/test_tiers.py`. First read `needs_escalation`'s signature at `tiers.py:102` and mirror how the existing escalation tests in this file call it (grep `needs_escalation` in `tests/test_tiers.py` and copy that call shape exactly):

  ```python
  def test_stated_class_is_a_target_that_never_escalates():
      assert "stated_class" in tiers.TARGET
      assert "stated_class" not in tiers._ESCALATE_ALWAYS
      assert "stated_class" not in tiers._ESCALATE_ITEM_ID
      assert "stated_class" not in tiers._ESCALATE_CERTIFICATE

  def test_a_missing_stated_class_alone_asks_for_nothing():
      # every other target confidently present, stated_class absent
      fields = {f: {"value": "x", "conf": 1.0} for f in tiers.TARGET if f != "stated_class"}
      # call needs_escalation exactly as the neighboring tests do
      assert "stated_class" not in tiers.needs_escalation(fields)  # adapt args to the real signature
  ```

- [x] **Step 2: Run to verify failure.** `$WT docker compose --profile test run --rm test pytest tests/test_tiers.py -k stated_class -v` — expected: FAIL (`stated_class` not in TARGET).
- [x] **Step 3: Implement.** (a) `tiers.py:28-31`: append `"stated_class"` to `TARGET` (do NOT touch any `_ESCALATE_*` set — absence IS the mechanism, `referenced_docs` precedent, `needs_escalation` at `tiers.py:102-124`). (b) `llm.py:68-72`: add `stated_class` to `_FIELD_VALUE` props — inert at runtime (a field absent from every escalate set is never in `missing`, so never requested), but `tests/test_llm_schema_vocabulary.py:112` asserts `set(tiers.TARGET) <= set(props)` and would otherwise fail. (c) `tools/corpus.py`: add `t0_stated_class` to `COLUMNS` (`tests/test_tools_corpus.py:43-45` asserts a column per target).
- [x] **Step 4: Run the three affected suites.** `$WT docker compose --profile test run --rm test pytest tests/test_tiers.py tests/test_llm_schema_vocabulary.py tests/test_tools_corpus.py -v` — expected: PASS.
- [x] **Step 5: Commit** — `git commit -m "extract: stated_class registered as a target that never escalates"`

---

### Task 3: The T0 extractor

> **Executed BEFORE Task 2** (deviation, 2026-08-21). The plan did not account
> for `t0_templates.T0_PRODUCED`/`T0_EXEMPT`, which PRD 5 makes a contract:
> `tests/test_t0.py::test_every_target_field_is_produced_by_t0_or_named_exempt`
> asserts the two partition `tiers.TARGET` exactly. Adding `stated_class` to
> TARGET first (Task 2) would therefore land a RED commit. Producing it first
> and registering it second keeps every commit green and makes the
> `T0_PRODUCED` entry true when it is written.

**Files:**
- Modify: `app/extract/t0_templates.py`
- Test: `tests/test_t0.py`

**Interfaces:**
- Produces: `extract_stated_class(text: str) -> dict | None` returning a `field_ev(...)` dict (`t0_templates.py:41-42` shape) or `None`; wired into `t0_extract` so the key lands in `fields["stated_class"]`.

- [x] **Step 1: Write the failing tests** — the strings are verbatim from the measured corpus (doc_ids noted for provenance):

  ```python
  # measured corpus shapes, dev DB 2026-08-21
  def test_stated_class_reads_the_voco_label():             # docs 600/694
      ev = t0_templates.extract_stated_class("Item Number: see annex Class: IIa Rule: 7 Name and address")
      assert ev["value"] == "IIa" and ev["tier"] == "T0" and ev["conf"] == 0.95
      assert "Class: IIa" in ev["verbatim"]

  def test_stated_class_reads_the_annex_viii_header():      # docs 229/272/115
      ev = t0_templates.extract_stated_class("EU Risk Class (MDR Annex VIII) Class IIa Conformity Assessment")
      assert ev["value"] == "IIa"

  def test_stated_class_reads_the_bilingual_subclass():     # docs 349/394
      ev = t0_templates.extract_stated_class("Klasse/ Class: Is Regel/ Rule: 6 Basic UDI-DI: ++E226")
      assert ev["value"] == "Is"

  def test_a_multi_class_boilerplate_abstains():            # docs 519/522
      assert t0_templates.extract_stated_class(
          "EU Declaration of Conformity Class Is/Ir/IIa/llb/lll EU Konformitaetserklaerung") is None

  def test_a_regulation_quoting_text_abstains():            # doc 260 (ISO cert quoting the reg)
      assert t0_templates.extract_stated_class(
          "placing on the market of Class III devices, and Class IIb implantable devices") is None

  def test_no_class_statement_returns_none():
      assert t0_templates.extract_stated_class("Declaration of Conformity for dental restoratives") is None

  def test_repeated_same_class_is_not_an_abstain():         # multi-product DoC, one class throughout
      ev = t0_templates.extract_stated_class("Class: I Rule: 5 ... Class I, Rule 5 Article List")
      assert ev["value"] == "I"
  ```

- [x] **Step 2: Run to verify failure.** `$WT ... pytest tests/test_t0.py -k stated_class -v` — expected: FAIL (`extract_stated_class` not defined).
- [x] **Step 3: Implement** in `t0_templates.py`, beside the other marker tables (`_TYPE_MARKERS` area, `t0_templates.py:47+` — hardcoded module constants are the established pattern, `config.py` carries no per-field extraction config):

  ```python
  _CLASS_VALUES = {"i": "I", "is": "Is", "im": "Im", "ir": "Ir",
                   "iia": "IIa", "iib": "IIb", "iii": "III"}
  _CLASS_CONF = 0.95
  _CLASS_RE = re.compile(
      r"(?:risk\s+class|class|klasse|razred|classe)\s*[:/]?\s*"
      r"(IIa|IIb|III|Is|Im|Ir|I)\b",
      re.IGNORECASE,
  )

  def extract_stated_class(text: str) -> dict | None:
      seen: dict[str, str] = {}
      for m in _CLASS_RE.finditer(text):
          seen.setdefault(_CLASS_VALUES[m.group(1).lower()], m.group(0))
      if len(seen) != 1:
          return None  # nothing stated, or a multi-class document: abstain
      value, verbatim = next(iter(seen.items()))
      return field_ev(value, _CLASS_CONF, verbatim)
  ```

  Wire it into `t0_extract`: grep the `extract_type` call site inside `t0_extract` and add, following the local merge idiom exactly:

  ```python
  if (ev := extract_stated_class(text)) is not None:
      fields.setdefault("stated_class", ev)
  ```

- [x] **Step 4: Run the whole T0 suite** (not just the new tests — the wiring touches `t0_extract`): `$WT ... pytest tests/test_t0.py tests/test_t0_layout.py tests/test_t0_corpus.py -v` — expected: PASS, corpus fixtures unchanged except any that now legitimately gain a `stated_class` key (inspect and accept those diffs explicitly; a fixture that LOSES a field is a bug).
- [x] **Step 5: Commit** — `git commit -m "extract: T0 reads the class a document states, abstaining on multi-class boilerplate"`

---

### Task 4: Persist it — migration 032 + GATE column write

**Files:**
- Create: `migrations/032_stated_class.sql`
- Modify: `app/handlers/gate.py` (`_upsert_document`, `gate.py:336-375`)
- Test: `tests/test_gate_candidate_handler.py`

**Interfaces:**
- Consumes: `fields["stated_class"]` from Task 3 (via `extraction_attempt`, which GATE re-reads itself at `gate.py:184-189` — VALIDATE needs **zero** changes: it assembles its candidate field-by-field, `validate.py:966-971`, and GATE never depends on the candidate carrying extraction fields).
- Produces: `document.stated_class` column; evidence row arrives for free (`_insert_evidence` iterates every field, `gate.py:523-579`).

- [x] **Step 1: Migration**

  ```sql
  -- 032: document.stated_class — the MDR risk class the document itself states.
  -- QA/display only: never scored, never gated, never written to item_mirror
  -- (BC = source of truth for item class; contract PRD §5, 2026-08-21).
  ALTER TABLE document ADD COLUMN stated_class text
    CHECK (stated_class IN ('I','Is','Im','Ir','IIa','IIb','III'));
  ```

- [x] **Step 2: Write the failing tests.** Grep `tests/test_gate_candidate_handler.py` for the existing production-write test asserting `basic_udi_di` persistence; clone its arrangement, set `stated_class` in the seeded extraction fields, and assert:

  ```python
  assert doc["stated_class"] == "IIa"
  ev = conn.execute(
      "SELECT value, tier FROM evidence WHERE doc_id = %s AND field = 'stated_class'",
      (doc_id,)).fetchone()
  assert ev == {"value": "IIa", "tier": "T0"}

  def test_stated_class_never_enters_the_score():
      assert "stated_class" not in gate.REQUIRED_FIELDS   # gate.py:98 — _score min()s exactly this tuple

  # behavioral pin: identical candidates with and without stated_class dispose identically
  # (clone the nearest disposition test twice; only the stated_class key differs; assert same doc_status)
  ```

- [x] **Step 3: Run to verify failure** — expected: FAIL (column does not exist).
- [x] **Step 4: Implement in `_upsert_document`.** Read `stated_class` the same way `basic_udi_di` is read just above the INSERT; add `stated_class` to the INSERT column list, the VALUES tuple, and the **always-refresh** group of the `ON CONFLICT DO UPDATE SET` clause (same group as `type`/`regulation`/`coverage_scope`, `gate.py:351-366` — a re-extraction should correct a class, and evidence rows are append-only per rev so history is never lost). Do NOT touch `REQUIRED_FIELDS` (`gate.py:98`) or `_score` (`gate.py:247-251`).
- [x] **Step 5: Run the gate suite.** `$WT ... pytest tests/test_gate_candidate_handler.py -v` — expected: PASS, and specifically every pre-existing disposition test unchanged.
- [x] **Step 6: Commit** — `git commit -m "gate: persist stated_class with evidence; scoring untouched"`

---

### Task 5: History replay — append-only, no re-adjudication

**Files:**
- Create: `app/repair_stated_class.py`
- Test: `tests/test_repair_stated_class.py`

**Interfaces:**
- Consumes: `extract_stated_class` (Task 3), `tiers.next_extract_rev` (`tiers.py:305-311`), `tiers.write_extraction_attempt` (`tiers.py:314-332`, signature now includes `playbook_slug=`/`playbook_rev=`).
- Produces: a new `extract_rev` per historic document whose text states a class — nothing else. **Emits no job.** The Task 6 view reads the attempt jsonb, so no GATE re-run is needed for history.

- [x] **Step 1: Read `app/repair_ref_list.py` in full** (it is the shape to mirror: latest-attempt query at :40-53, append-only apply at :82-110, dry-run default). Two deliberate departures, both simplifications: (a) source text comes from `document_text.content` (`migrations/018_document_text.sql` — every registry doc has a row, measured 768/768), so no PDF reopen and no pymupdf; (b) **no `validate.doc` re-emission and no pending-job re-pointing** — this repair must not re-adjudicate anything, so it only appends the rev. That leaves the historic `document` rows without the column value, by design; the Task 6 view COALESCEs from the attempt.
- [x] **Step 2: Write the failing tests:**

  ```python
  def test_repair_appends_a_rev_that_keeps_every_old_field(db):
      # seed: attempt rev 1 with type/ref_list/validity_to; document_text stating "Class: IIa Rule: 7"
      # run: repair_stated_class.apply(conn)
      # assert: rev 2 exists; rev 2.fields keeps ALL rev-1 keys with identical values
      #         (the [latest-extract-rev-can-be-the-poorest] guard); rev 2 adds stated_class == "IIa"

  def test_repair_skips_a_hash_that_already_has_the_key(db): ...
  def test_repair_counts_the_abstains(db):
      # multi-class text -> no new rev, counted under "abstained_multi", never silent
  def test_dry_run_writes_nothing(db): ...
  ```

- [x] **Step 3: Run to verify failure.**
- [x] **Step 4: Implement.** Selection: latest attempt per `content_hash` (the `repair_ref_list.py:40-53` query shape) joined to `document_text`, restricted to hashes with a `document` row and `NOT (fields ? 'stated_class')`. Per row: `ev = extract_stated_class(text)`; if `None`, count (`no_mention` vs `abstained_multi` — distinguish by whether the regex matched at all); else `new_fields = dict(old_fields); new_fields["stated_class"] = ev`, then `write_extraction_attempt(conn, content_hash, ["T0"], new_fields, extract_rev=next_extract_rev(conn, content_hash), playbook_slug=old.playbook_slug, playbook_rev=old.playbook_rev)`. CLI: dry-run by default printing the counts, `--apply` to write (the `repair_ref_list`/`regroup` convention).
- [x] **Step 5: Run tests** — PASS — then **commit** the code before touching the live DB: `git commit -m "repair: backfill stated_class into a fresh extraction rev, append-only, no re-adjudication"`
- [~] **Step 6: DEFERRED — run from the main checkout after merge.** Not run
  here: this slice was built in an isolated worktree against a throwaway test
  database, and the agent executing it was barred from the live stack. Nothing
  else in the plan depends on it — the code is committed and tested, and the
  033 view reads the attempt jsonb, so the backfill can be run whenever.
  **Corrected command** (the module has no `__main__`; the repo exposes
  one-off repairs as `app.cli` subcommands, the `repair-ref-list`/`regroup`
  convention this plan's Step 4 names):
  `docker compose exec worker python -m app.cli repair-stated-class` (dry run) — expected counts ≈ scanned 768, stated ~450-470, remainder no-mention/abstained (baseline: 445 DoC + 8 EC + a few ISO/other). Review, then `--apply`, then verify:

  ```sql
  SELECT count(*) FROM (
    SELECT DISTINCT ON (content_hash) fields ? 'stated_class' AS has
    FROM extraction_attempt ORDER BY content_hash, extract_rev DESC) t WHERE has;
  ```

---

### Task 6: The comparison — view 033 + `/data-quality` section

> Two deviations. (a) The view uses `LEFT JOIN latest`, not `JOIN`: the
> COALESCE makes the column primary and the attempt a fallback, and an inner
> join would invert that by making the fallback mandatory. Identical on
> today's data (768/768 documents have an attempt row); the LEFT JOIN is what
> keeps it identical if `extraction_attempt` is ever pruned.
> (b) Step 5 said runbook/architecture do not enumerate data-quality sections
> — the grep it asked for showed `docs/runbook.md` DOES describe the page's
> content in full, so it was updated too, along with the code-map rows for
> `app/cli.py` and the new `app/repair_stated_class.py`.

**Files:**
- Create: `migrations/033_item_class_check.sql`
- Modify: `web/app.py` (`data_quality` route, `web/app.py:1992-2047`)
- Modify: `web/templates/data_quality.html`
- Test: `tests/test_web.py` (seed via `tests/fixtures/seed_ui.py` patterns)

**Interfaces:**
- Consumes: `document.stated_class` (Task 4) and historic attempt jsonb (Task 5).
- Produces: view `item_class_check(item_ref, bc_class, doc_class, doc_id, type, verdict)`, `verdict ∈ {fillable, agree, agree-family, conflict}`.

- [x] **Step 1: Write the failing web test:** seed one item with `product_class='Ir'` linked to a production doc whose latest attempt states `I` → expect one `agree-family` row; one blank-class item + doc stating `IIa` → `fillable`; one `IIa` item + doc stating `I` → `conflict`; assert the `/data-quality` page renders a "Device class vs BC" section with those counts.
- [x] **Step 2: Migration** (grant line copied in the style of the existing view grants — check how `document_effective_expiry`/`extraction_spend` are granted and mirror it):

  ```sql
  -- 033: item_class_check — BC's item class vs the class its production documents state.
  -- Read-only QA. BC stays source of truth; this view never feeds a write.
  CREATE VIEW item_class_check AS
  WITH latest AS (
    SELECT DISTINCT ON (content_hash) content_hash,
           fields->'stated_class'->>'value' AS attempt_class
      FROM extraction_attempt
     ORDER BY content_hash, extract_rev DESC
  )
  SELECT id.item_ref,
         NULLIF(im.product_class, '')                    AS bc_class,
         COALESCE(d.stated_class, l.attempt_class)       AS doc_class,
         d.doc_id, d.type,
         CASE
           WHEN NULLIF(im.product_class, '') IS NULL     THEN 'fillable'
           WHEN im.product_class = COALESCE(d.stated_class, l.attempt_class)
                                                          THEN 'agree'
           WHEN im.product_class IN ('Is','Im','Ir')
                AND COALESCE(d.stated_class, l.attempt_class) = 'I'
                                                          THEN 'agree-family'
           ELSE 'conflict'
         END AS verdict
    FROM item_document id
    JOIN document d USING (doc_id)
    JOIN latest l ON l.content_hash = d.content_hash
    JOIN item_mirror im ON im.item_ref = id.item_ref
   WHERE d.status = 'production' AND id.status = 'production'
     AND COALESCE(d.stated_class, l.attempt_class) IS NOT NULL;
  GRANT SELECT ON item_class_check TO dentalia_api;
  ```

- [x] **Step 3: Route + template.** In the `data_quality` handler add two queries: `SELECT verdict, count(*) FROM item_class_check GROUP BY 1` and the detail rows for `verdict IN ('conflict','fillable')` ordered by verdict, limit 50 with a total count (the pager-honesty rule — name what is not shown). Template: a new `<h2>Device class vs BC</h2>` section after the existing blocks (`data_quality.html:15` and `:35` are the pattern), summary table + detail table, item_ref linking to `/items/{item_ref}` via the `:path`-safe pattern the other templates use.
- [x] **Step 4: Run** `$WT ... pytest tests/test_web.py -k "data_quality or class_check" -v` — PASS — then the full web suite once.
- [x] **Step 5: Docs sync.** `docs/code-map.md`: if the `web/app.py` row enumerates `/data-quality` content, add the class-check section to that one line. Nothing else changes (runbook/architecture do not enumerate data-quality sections — verify with a grep before touching).
- [x] **Step 6: Commit** — `git commit -m "data-quality: BC class vs the class production documents state"`

---

## Self-review notes

- Regression pins live in: Task 2 Step 1 (never escalates → no new LLM spend), Task 4 Step 2 (never scores → dispositions frozen), Task 5 Step 2 (append-only merge keeps every old field → no rev shadowing), Task 5 by construction (no `validate.doc` → no re-adjudication of 768 historic dispositions).
- VALIDATE is untouched anywhere in this plan — verified: its flags are all keyed on named fields (`validate.py:747-964`) and its candidate payload never carries extraction fields (`validate.py:966-971`).
- Type consistency: `extract_stated_class` (Tasks 3, 5), `document.stated_class` (Tasks 4, 6), `item_class_check` (Tasks 1, 6) — names match across tasks.
