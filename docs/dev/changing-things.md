# Changing things

**Status:** live. Checklists verified against the tree on 2026-08-31.

Five changes that touch more places than they look like they should. Each is a
checklist, not an explanation — the explanations live in the contract docs, which
are linked.

The rule behind all of them: **the queue is regenerable state, the registry is
truth.** Anything you can rebuild by re-running work is cheap to change; anything
that has already been written to `document`, `item_document` or `evidence` is not.

---

## Adding a job type

The enum is closed. A new tag is a **PRD change and a migration first**, never a
string that appears in code and works.

1. **PRD.** Add the tag to `docs/dentalia-pipeline-contract-prd-v3.md`: its stage,
   its payload, and the Emits row of whatever produces it. A stage may only emit
   job types listed in its Emits row.
2. **Migration**, in a file of its own containing nothing else:
   `ALTER TYPE job_type ADD VALUE '…'`. Postgres forbids using a new enum value
   in the same transaction that adds it, which is why every previous one
   (`013_scheduler.sql`, `014_upload.sql`, `041_eudamed_job_types.sql`,
   `041_vendor_import.sql`) is isolated.
3. **`app/handlers/noop.py`** — add it to `JOB_TYPES` (`noop.py:41`). The comment
   there says "keep in sync with `migrations/001_queue.sql`" and it is manual.
   Miss this and the tag has no placeholder, so a job of that type dispatches
   into nothing.
4. **The handler module** — implement it and call `register("your.tag", handler)`
   at the bottom, following any module in `app/handlers/`.
5. **`web/app.py`** — add it to `JOB_TYPES` and to `STAGE_BY_TYPE`, or the UI
   renders a bare slug where a stage label belongs.
6. **`docs/vocabulary.md`** — `tests/test_vocabulary_doc.py:117` reads the live
   `job_type` enum out of Postgres and asserts the doc's job-tag table lists every
   value. The suite fails until you write the row.
7. **Tests** — a handler test module. Anything with dispositions gets a
   table-driven test, not one function per cell.
8. **`docs/dev/handlers.md`** — one index row and one five-line block.

Deciding whether you need one at all: if the work is a poll or a wait, use
`queue.defer` and the `{"_deferred": True}` convention instead. That is how
EXTRACT waits on the Batch API across many cycles without a second tag.

---

## Adding a migration

1. **Numbered `.sql` in `migrations/`.** The runner applies them in
   **lexicographic filename order**, not numeric (`app/db.py:49`). Two
   duplicate-numbered pairs already exist and their order follows from the
   letters after the number, not from when they were written.
2. **Grants, if the migration creates a table.** `007_roles_grants.sql`'s
   `GRANT SELECT ON ALL TABLES` only covered tables that existed when it ran.
   Every table created from `009` onward carries its own explicit grant, and the
   comment saying so is repeated nearly verbatim in each. Forget it and the web
   role gets a permission error at runtime, not at migration time.
3. **Never grant the web role write access** to `document`, `item_document`,
   `evidence` or `audit_log`. That absence is how invariant 1 is enforced
   structurally.
4. **`tests/conftest.py:247`** — add the table to `_RESET_TABLES`, children
   before parents. If it also owns a sequence tests depend on, add it to
   `_RESET_SEQUENCES` at `conftest.py:279`.
5. **`tests/test_queue.py:542`** — if the new table has no foreign key linking it
   to the existing seed set, add it to `seeds`. The guard test walks the FK
   closure from those roots; a table it cannot reach slips past silently, and you
   get order-dependent test failures later rather than a clear failure now. This
   has already happened twice, to `data_anomaly` and `service_heartbeat`.
6. **Run the full suite.** Migrations are one of the paths CLAUDE.md's selection
   rule sends to the whole suite, because `.sql` files are read at runtime and no
   import-graph tool can see them.

Enum value additions get their own file, as above.

### Never edit a migration that has already been applied

`schema_migrations` is keyed on **`filename`** (`app/db.py`), so editing an
applied file never re-runs it. The tree changes, every database built from
scratch afterwards gets the new definition, every long-lived database keeps the
old one, and **the test suite stays green either way** — it builds from scratch,
so it only ever sees the corrected version. Nothing reports the divergence.

This has already cost 8 days once. `043_eudamed_cert_views.sql` was applied
2026-08-26; a change the next day edited it in place to add `SELECT
DISTINCT` to `certificate_drift_candidate`. The dev database kept the un-deduped
view, `/expiry` served 1.43 MiB of duplicate rows inside a collapsed `<details>`,
and the test pinning the corrected shape passed the whole time. Repaired by
`058_cert_view_drift_repair.sql`.

- **Correcting an applied migration = a NEW numbered file** that drops and
  recreates the object from the corrected text. Say in its header which file it
  is repairing and why, as 058 does.
- **The one exception, ruled 2026-09-18: `008_api_role_dev_password.sql`.** It
  creates no schema object; it sets a password on a cluster-global role, and
  it re-runs in every fresh database migrated in the same cluster
  (`schema-drift`, the suite, the rehearsal). A new file cannot change what an
  old file does on a fresh database, and a password is invisible to
  `schema-drift`, so editing it in place diverges nothing. The same test
  applies to any future candidate: no schema object, and harm that happens on
  fresh databases. Anything else gets a new file.
- **A view is the dangerous case**, because `CREATE VIEW` has no `IF NOT EXISTS`
  drift signal and a wrong view returns rows rather than an error.
- **To check a running database against the tree:**

  ```
  docker compose exec -T worker python -m app.cli schema-drift
  ```

  It builds the tree's schema in a scratch database and compares columns,
  views, indexes, constraints, enums and grants object by object, then drops
  the scratch database. Exit 0 means no drift. Run it after a deploy and after
  correcting any applied migration. `--keep` leaves the scratch database behind
  to inspect.

  Do **not** reach for `pg_dump --schema-only | diff` instead: measured on two
  identical schemas it reports ~100 lines of difference, because it orders
  objects by dependency and stamps a per-session `\restrict` token.

---

## Adding or bumping a dependency

Every image installs with `pip install -c constraints.txt`, which pins every
package, direct and transitive, to one exact version shared by `worker`, `web`
and `test`. `pyproject.toml` keeps the floors; `constraints.txt` decides what
is installed.

- **Bump one:** edit its line in `constraints.txt`, rebuild
  (`docker compose build worker web` and `docker compose --profile test build
  test`), run the full suite.
- **Add one:** add it to `pyproject.toml` **and** pin it in `constraints.txt`
  (`tests/test_build_pins.py` fails until both agree), rebuild the test image,
  then pin whatever new transitive packages
  `docker compose exec -T test pip freeze --exclude pip --exclude dentalia`
  shows that the file lacks. An unpinned transitive package floats, and no
  test can see it.
- **`playwright` moves only with the `Dockerfile` base image tag.** The test
  pins the two together.

## Adding a config key

1. **Field on the section dataclass** in `app/config.py`, with its default. The
   default lives here and nowhere else — the one time it was duplicated inside
   `load_config()`, the copy shipped a 90-day renewal horizon against a 30-day
   ruling.
2. **Wire it in `load_config()`** with `_str` / `_int` / `_float` / `_bool`,
   passing the env var name and the TOML path. **Skipping this step is how a key
   ends up doing nothing**, and five already have.
3. **Thread it to the caller.** A key that is loaded but never passed into the
   module that needs it is just as dead. All four `queue.*` keys are in this
   state: loaded, never delivered, shadowed by identical function defaults.
4. **`docs/dev/config-reference.md`** — a row, and a blast-radius note if the key
   can change what reaches production.
5. **PRD §11** if it is a contract-level knob.

Before adding one, ask whether it should be config at all. `app/compliance.py`
deliberately is not: "a legal obligation that can be switched off with `export`
is not an obligation."

---

## Adding a route

1. **`web/` module.** Either in `web/app.py` or in a sub-registrar registered
   from `create_app`.
2. **Access policy.** `web/access.py` is **default-deny**: a path that matches no
   rule in `allowed_classes` is staff-only. That is the safe default, so a new
   route needs a rule only if some other caller class must reach it. If it does,
   pick the refusal code deliberately — the surfaces differ on purpose, and a 403
   where a 404 belongs confirms that a resource exists.
3. **Producer-only.** The web process may enqueue jobs and write a short list of
   narrow tables. It may never write `document`, `item_document` or `evidence`.
   Human decisions enqueue `gate.apply`.
4. **Template**, if it renders. Full pages extend `base.html`; partials do not,
   and a partial fetched without the HTMX header is not a page.
5. **`tests/test_web.py`** — and check the new route against the access policy
   table in `tests/test_access.py:43`, which is table-driven.
6. **`docs/runbook.md`** — it carries the exhaustive route table and is expected
   to stay current in the same session as the change.
7. **The client guide**, if a human will use the screen. `docs/guide/pages/` has
   one page per screen and a fixed template.

---

## Adding a table

Covered by "Adding a migration" above, plus:

- Decide whether the web role needs `SELECT`. Most new tables get it explicitly;
  `robots_cache` (migration 036) deliberately got no grant at all and is the only
  post-`007` table without one.
- Decide whether it is append-only, and enforce that by **withholding the
  `UPDATE`/`DELETE` grant** rather than by convention.
  `manufacturer_playbook_revision` does this: a revert writes the old body
  forward as a new revision and never mutates history.
- Soft references (`via_job`, `group_id`, `doc_id` on some tables) are deliberate.
  Audit entries must stay interpretable after every `job` row is deleted, so they
  carry a `job_snapshot` rather than a foreign key.

---

## Changing a payload

Payload fields are **additive-only** once Phase 1 has started. Consumers tolerate
unknown fields and never tolerate missing ones.

There is no payload versioning. For a breaking change: drain the queue, or delete
and re-emit. The queue is regenerable; the registry is not.

Payloads are immutable after enqueue. External long-running state goes in a side
table — `batch_ref` for Batch API ids — never into a mutated payload.

---

## Before you commit

- Run the tests that cover what you touched, and the full suite once before
  committing. Always through `./scripts/test.sh`; a hook blocks a bare `pytest`.
- Say which subset ran. A subset is never evidence the change is green.
- Update the operational docs in the same session if your change invalidated
  something they state or added something they should cover.
- If your change makes a row in [limits.md](limits.md) untrue, delete the row.

## Related

- [handlers.md](handlers.md) · [config-reference.md](config-reference.md) · [limits.md](limits.md)
- [runbook.md](../runbook.md) — routes, commands, migration ordering
- [test-infra.md](../test-infra.md) — the wrapper and why `--dist loadfile`
- PRD v3 — normative on everything above
