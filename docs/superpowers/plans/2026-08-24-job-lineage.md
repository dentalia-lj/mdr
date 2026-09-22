# Job Lineage (`caused_by`) Implementation Plan

**Goal:** Every job records which job enqueued it, so a pipeline cascade (discover → fetch → extract → validate → gate) can be walked in one query instead of reconstructed by correlating hashes and timestamps.

**Architecture:** One nullable soft-ref column, `job.caused_by` — no FK, same rationale as `audit_log.via_job` (queue rows are prunable on any schedule; a dangling parent id is acceptable because the queue is regenerable state and the registry's legal trail already lives in `audit_log.job_snapshot`). Stamping is **implicit**: the runner publishes the claimed job's id in a module slot around dispatch, and `queue.enqueue` reads it — zero changes to any handler, no call site can be missed, and producer-only enqueues (CLI, scheduler, web) stay NULL roots by construction. Workers are sync and single-job-per-process-at-a-time (no asyncio, CLAUDE.md runtime), so a module-level variable is race-free.

**Why not the alternatives (decided 2026-08-24 with Denis):** hash-correlation breaks where it matters most — discover→fetch has no content_hash yet, and two jobs sharing one hash are ambiguous (measured on the RENFERT smoke test: two `validate.doc` on one hash). A root/trace id can't represent merge points — dedupe gives a child two semantic parents (validate 35563 ← fetch 35553 AND discover 35551); `caused_by` records the enqueuer and loses the second edge knowingly (the dedupe key still names it).

**Tech Stack:** Python 3.12, psycopg 3 raw SQL, pytest against real Postgres.

**Verified against:** master on 2026-08-24. `queue.enqueue` signature at `app/queue.py:73-81` (`conn, type, payload, dedupe_key, *, priority, run_after, max_attempts`); runner dispatch at `app/workers/runner.py:37-62` (`run_once` claims, `result = handler(conn, job)` at :62); job detail page `web/app.py:2538` (`GET /jobs/{job_id}`); migrations end at `034_item_document_production_expiry.sql`.

## Global Constraints

- Payloads stay untouched (invariant 9) — lineage is a COLUMN, never a payload field.
- No FK on `caused_by` — soft ref; deleting/pruning any job row must never fail on children.
- Producer-only enqueues (CLI `enqueue`, scheduler ticks, web `/upload`, `gate.apply` from the UI) legitimately stamp NULL — they are roots. Do not thread fake ids into them.
- The runner slot must be cleared in a `finally`: a handler that raises must not leak its id onto the next enqueue from a non-job context.
- Migration number 035 assumes nothing lands first; take the next free number at execution time.
- Each task ≤3 files, tests excluded. Isolated test env, once per shell:
  ```bash
  export WT='COMPOSE_PROJECT_NAME=dentalia_wtc POSTGRES_PORT=5436 PGDATA_HOST=/srv/pgdata/dentalia_wtc'
  ```
  (live postgres holds the `dentalia-postgres` container_name; reuse the scratchpad `compose.wtc.yml` override or rename locally.)

---

### Task 1: Column, stamp, runner slot

**Files:**
- Create: `migrations/037_job_lineage.sql` (renumbered from 035 at merge — heartbeat took 035)
- Modify: `app/queue.py` (module slot + `enqueue` stamping)
- Modify: `app/workers/runner.py` (`run_once`, around the `handler(conn, job)` call at :62)
- Test: `tests/test_queue.py`

**Interfaces:**
- Produces: `job.caused_by bigint NULL`; `queue.current_job_id: int | None` (module slot); every `enqueue` during a handler's dispatch stamps the claimed job's id.

- [x] **Step 1: Write the failing tests** in `tests/test_queue.py` (mirror the file's existing enqueue-test arrangement — it already exercises `queue.enqueue` against the real DB):

  ```python
  def test_enqueue_outside_a_job_is_a_root(conn):
      jid = queue.enqueue(conn, "fetch.url", {"url": "x"}, "lineage-root-1")
      row = conn.execute("SELECT caused_by FROM job WHERE id=%s", (jid,)).fetchone()
      assert row["caused_by"] is None

  def test_enqueue_during_dispatch_records_the_claiming_job(conn):
      parent = queue.enqueue(conn, "discover.group", {"group_id": 1}, "lineage-p-1")
      queue.current_job_id = parent          # what run_once sets around dispatch
      try:
          child = queue.enqueue(conn, "fetch.url", {"url": "y"}, "lineage-c-1")
      finally:
          queue.current_job_id = None
      row = conn.execute("SELECT caused_by FROM job WHERE id=%s", (child,)).fetchone()
      assert row["caused_by"] == parent

  def test_runner_clears_the_slot_even_when_the_handler_raises(conn):
      # register a handler that enqueues then raises; after run_once,
      # queue.current_job_id must be None and the enqueued child must still
      # carry the parent id (the enqueue happened inside dispatch).
  ```

- [x] **Step 2: Run to verify failure** — `$WT docker compose -f docker-compose.yml -f <scratchpad>/compose.wtc.yml --profile test run --rm test pytest tests/test_queue.py -k lineage -v` — expected: FAIL (column/attribute missing). Deviation: `-k lineage` matches nothing (none of the three test names contain the substring "lineage" — only their dedupe-key string literals do, which `-k` doesn't see); ran the three tests by explicit node id instead. All 3 failed as expected (`UndefinedColumn: caused_by`).
- [x] **Step 3: Migration**

  ```sql
  -- 035: job.caused_by — which job's handler enqueued this one. Soft ref, no FK
  -- (audit_log.via_job rationale: queue rows prune on any schedule; the queue is
  -- regenerable state, the registry's trail is audit_log.job_snapshot). NULL =
  -- a root: CLI, scheduler tick, web producer.
  ALTER TABLE job ADD COLUMN caused_by bigint;
  CREATE INDEX job_caused_by_idx ON job (caused_by) WHERE caused_by IS NOT NULL;
  ```

- [x] **Step 4: Implement.** `app/queue.py`: module slot `current_job_id: int | None = None` beside the other module state; `enqueue` adds `caused_by` to its INSERT column list, value `current_job_id` (read at call time, no new parameter — handlers stay ignorant). `app/workers/runner.py` `run_once`: set `queue.current_job_id = job["id"]` immediately before `result = handler(conn, job)` and clear it in a `finally:` that wraps exactly the dispatch (not the claim, not the finish — a failure in `finish`/`fail` bookkeeping happens outside any handler and must not carry a stale id).
- [x] **Step 5: Run the queue + runner suites.** `$WT ... pytest tests/test_queue.py tests/test_runner.py -v` — expected: PASS, zero edits to pre-existing tests (the column is additive and every existing enqueue path stamps NULL or a real id without behavioral change). Result: 44 passed.
- [x] **Step 6: Commit** — `git commit -m "queue: a job remembers which job enqueued it"`

---

### Task 2: Show the cascade on `/jobs/{job_id}`

**Files:**
- Modify: `web/app.py` (`job_detail`, `web/app.py:2538`)
- Modify: the job-detail template (`web/templates/` — locate via the `job_detail` handler's template name)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `job.caused_by` (Task 1).
- Produces: the job page shows "caused by: #id (type)" linking to the parent, and a "caused: [#id type status]…" list of direct children.

- [x] **Step 1: Write the failing test** (pattern-match the existing `/jobs/{id}` test in `tests/test_web.py`): seed parent + child rows with `caused_by` set; assert the child's page links `/jobs/{parent}` and the parent's page lists the child id and its status. Added `test_job_detail_shows_the_cascade` and `test_job_detail_root_shows_no_parent`.
- [x] **Step 2: Run to verify failure.** Both failed as expected before implementation.
- [x] **Step 3: Implement.** In `job_detail`: one query for the parent (`SELECT id, type, status FROM job WHERE id = (SELECT caused_by FROM job WHERE id=%s)`), one for children (`SELECT id, type, status FROM job WHERE caused_by=%s ORDER BY id` — the partial index from Task 1 serves this). Render both in the template beside the existing job facts; a root shows "caused by: —". No recursive tree on the page — parent + direct children per click is the walk.
- [x] **Step 4: Run** the web tests for the jobs page. Expected: PASS. Result: full `tests/test_web.py` — 249 passed (no failures, including the two tests named in the orchestrator's "known baseline" list, which passed cleanly here).
- [x] **Step 5: Commit** — `git commit -m "web: a job page names its parent and its children"`

---

### Task 3: Docs sync

**Files:**
- Modify: `docs/dentalia-schema-sketch.md` (§ job table — add the `caused_by` column line with the soft-ref/NULL-root note)
- Modify: `docs/code-map.md` (queue.py / runner.py rows: one line each, only if those rows enumerate columns/behavior this changes — grep first)

- [x] **Step 1:** Schema-sketch: add `caused_by bigint -- soft ref, no FK; NULL = root (CLI/scheduler/web producer); set implicitly by the runner around dispatch` to the job table block. Also added `job_caused_by_idx` to the index block, mirroring the migration.
- [x] **Step 2:** Check `docs/code-map.md` and `docs/architecture.md` for sentences the change invalidates (a claim that jobs carry no provenance, if one exists — grep "lineage\|provenance\|caused"). Update or leave. Grep found no invalidated claim in either file (architecture.md's one "provenance" hit is `manufacturer_alias.source`, unrelated). Updated the `app/queue.py` and `app/workers/runner.py` rows in `docs/code-map.md` — both enumerate the exact behavior this task adds (enqueue stamping, runner slot lifecycle). Left `docs/architecture.md` unchanged: its per-migration table already stops at `031_...sql` (032-034 are also absent, pre-existing staleness outside this task's scope), and nothing there asserts jobs carry no lineage.
- [x] **Step 3: Commit** — `git commit -m "docs: the queue's lineage column"`

---

## Self-review notes

- Deliberately NOT in scope: threading explicit `caused_by` parameters through handlers (the slot makes that dead code), recording second parents at dedupe merge points (the dedupe key already names the collision; a `deduped_hits` side count would be a separate proposal), and any CLI tree-walk command (a recursive CTE in psql serves the rare deep walk: `WITH RECURSIVE c AS (SELECT * FROM job WHERE id=%s UNION ALL SELECT j.* FROM job j JOIN c ON j.caused_by=c.id) SELECT id, type, status FROM c`).
- The one subtle correctness point is the `finally` placement in Task 1 Step 4 — the test in Step 1 pins it.
