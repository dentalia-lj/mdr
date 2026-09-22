# Playbooks into the Database Implementation Plan

**Goal:** Move per-manufacturer playbooks out of 33 git-tracked JSON files and into the database, hung off the existing `manufacturer` table and linked by foreign key to the BC-imported `vendor_master`, so a non-developer can onboard a manufacturer through the web UI with guardrails that make a wrong edit refusable and a bad edit revertible.

**Architecture:** Identity (`bc_codes`, canonical name, aliases) is shredded into constrained tables so `playbooks.validate()`'s checks become database constraints; behaviour (the other 13 fields plus the T0 parse template) stays a `jsonb` body. `app/playbooks.py` keeps its public API byte-identical and gains a module-level DB source set once per process, so eleven of twelve consumer modules do not change. The web editor is tiered by blast radius — typed controls for safe fields, consequence-showing forms for dangerous ones, read-only for engineer-only rules — never a JSON textarea.

**Tech Stack:** Python 3.12, psycopg 3, raw SQL + numbered migrations, FastAPI + Jinja + HTMX (no build step), pytest against real Postgres, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-26-playbooks-into-the-database-design.md`

## Global Constraints

- **Invariant 1:** `dentalia_api` gains writes on `manufacturer`, `manufacturer_bc_code`, `manufacturer_name`, `manufacturer_playbook_revision`, `playbook_epoch` — and nothing else. Never `document` / `item_document` / `evidence`. 007's `GRANT SELECT ON ALL TABLES` covers only tables existing then, so every grant is explicit (same posture as 016).
- **Invariant 7:** no job type is added. This is a PRD **data-model** change only; the queue enum is untouched.
- **Never-raises, split by failure kind** (Denis's ruling, 2026-08-26 — spec §3.5). A malformed **row** is logged and skipped exactly as a malformed file is; DISCOVER must not dead-letter a group over an authoring typo. A **source failure** (cannot connect, query fails) retries once then **raises**, so the job fails and the queue retries it. Returning `()` on a dead connection would silently strip every manufacturer's rules at once — BACKFILL alone would then archive Neodent's 28 Dentalia invoices, because `pb is None` stops both `skip_backfill` and `exclude` from firing. `validate()` stays the loud operator path.
- **`Playbook.bc_codes` keeps its current meaning** — the codes a human *authored*, i.e. `manufacturer_bc_code` rows with `source='playbook'`. Populating it from all entity rows would silently change `reconcile`. See spec §2.2.
- **One-way move.** Seed once; the directory is never read again. The `:ro` mount is retired only after the no-filesystem-access test is green.
- **Sync only** (CLAUDE.md): no `asyncio`, no `async def`.
- **Additive-only** payloads and response shapes.
- **Test selection** (CLAUDE.md): this work touches `migrations/`, `app/playbooks.py`, `app/config.py` and `playbooks/` — all on the full-suite list. **Every task runs the full suite:** `python -m pytest -q -n 4 --dist loadfile` (~150 s, 2276 tests as of 2026-08-26). Drop to `-n 2` if other sessions are busy. A subset run is never evidence; say which subset ran.
- **Worktree DSN:** pin `DATABASE_URL` / `API_DATABASE_URL` to the scratch/test database explicitly and state it in the first commit message. A worktree agent's DB-touching CLI otherwise defaults to the live DSN.
- **No Claude/AI attribution in commit messages.**

---

# Slice 1 — tables + seed

Nothing reads the DB yet. Independently valuable: it closes the standing
`contact_emails` bug (`manufacturer` has 0 rows, so `discover._contact_known` and
`email_request._contacts` have always returned `[]`).

### Task 1: Migration `049_manufacturer_entity.sql`

**Files:**
- Create: `migrations/049_manufacturer_entity.sql`
- Modify: `docs/dentalia-schema-sketch.md` (tables + the `dentalia_api` access matrix)

DDL exactly as spec §2 (`049` block): `ALTER TABLE manufacturer` adding `slug`,
`playbook_rev`, `updated_at`, `updated_by`; `CREATE TABLE manufacturer_bc_code`
with the composite PK **and** the FK to `vendor_master`; `CREATE TABLE
manufacturer_name` with `name_folded` as PK; two lookup indexes; the grants.

Header comment must state, in this repo's house style:
- what each table is for and who writes it;
- that `vendor_master` is never written by this path — the FK is the link;
- that `PRIMARY KEY (code_source, code)` **is** `validate()`'s duplicate-code check
  and `manufacturer_name.name_folded` **is** its duplicate-name check;
- that all 39 current playbook codes exist in `vendor_master` (measured
  2026-08-26), so the FK grandfathers nothing;
- that `slug` is nullable because ~350 entities have no playbook, and Postgres
  `UNIQUE` permits multiple NULLs;
- that `playbook_ref` is left in place, commented historical, rather than dropped.

- [x] Write the migration
- [x] `dentalia migrate` against the scratch DB; confirm 049 applies and is listed in `schema_migrations`
- [x] Full suite

### Task 2: `app/manufacturer_seed.py`

**Files:**
- Create: `app/manufacturer_seed.py`, `tests/test_manufacturer_seed.py`

One-way, explicit, re-runnable seed from `reconcile._entities(vendor, loaded)`
over `vendor_master` + `load_playbooks()`.

Per entity with a non-`None` canonical:
- upsert `manufacturer (canonical_name, slug)` — `slug` from `entity.slug`;
- insert `manufacturer_bc_code` per code, `source='playbook'` for codes the
  playbook itself named, `'vendor-master'` for codes the union-find added;
- insert `manufacturer_name`: one `canonical` row plus one `alias` row per
  `pb.aliases` entry, keyed on `casefold()`.

Rules:
- **Skip the nameless entity and any empty canonical.** `''` is not a
  manufacturer; 2 `item_group` rows carry it and must be reported, not seeded.
- **Idempotent.** A second run changes nothing and reports `unchanged`.
- **Report, never silently skip** (CLAUDE.md): counts for inserted / unchanged /
  skipped, plus every `item_group.canonical_manufacturer` with no matching
  `manufacturer` row.
- Reuse `reconcile._entities` — do **not** re-derive entity grouping.

Expected on live data (measured 2026-08-26): 384 entities, 383 named, 33
playbooked, 4 multi-code; 39 `source='playbook'` codes; 2 uncovered `item_group`
values (`''` and `3SHAPE MEDICAL A/S`).

Tests:
- seed then re-seed → second run reports 0 inserted, all unchanged
- an unknown BC code is refused by the FK (`ForeignKeyViolation`)
- two manufacturers claiming one code is refused by the PK
- two manufacturers claiming one name (differing only by case) is refused by the `name_folded` PK
- the orphan report names a group whose `canonical_manufacturer` has no row
- an entity with a `None` canonical produces no row

- [x] Write the module and tests
- [x] Full suite

### Task 3: `dentalia manufacturers seed`

**Files:**
- Modify: `app/cli.py` (new `manufacturers` subparser near the existing `playbooks` one at `cli.py:729`), `docs/README.md` runbook

`dentalia manufacturers seed [--apply]`. **Dry run by default**,
`--apply` to write (Denis, 2026-08-26): not because it can do damage, but so
every manufacturer-touching command in this CLI behaves the same way as
`vendor-master` and `regroup` and nobody has to remember the exception.
- [x] Add the subcommand
- [x] dry run against the scratch DB reproduces the expected numbers above, and writes 0 rows
- [x] Full suite

### Task 4: Contact editing in the UI

**Files:**
- Modify: `web/registry.py` (a POST beside `manufacturer_detail_page`), `web/templates/manufacturer_detail.html`

Follow `draft_save` (`web/app.py:2712`) exactly: parse → validate → on failure
`_result.html` with 422 → on success guarded `UPDATE … RETURNING` → commit →
`_result.html`. `updated_by` from `_authenticated_user(request) or DEFAULT_DECIDED_BY`.

Split addresses on `,` and `;` and strip, as `draft_save` does. An empty list is
legal (`email_request._contacts`' docstring: an empty list is not fatal — the
drafts UI refuses to release a draft with no recipient).

Tests in `tests/test_web.py`: save contacts, read them back through
`email_request._contacts`, reject a malformed address, confirm a manufacturer
with no row 404s rather than silently creating one.

- [x] Route + template + tests
- [x] Full suite

---

# Slice 2 — `body` jsonb + loader swap

**The risky one.** Every consumer changes backing store at once.

### Task 5: Migration `050_playbook_body.sql`

**Files:**
- Create: `migrations/050_playbook_body.sql`
- Modify: `docs/dentalia-schema-sketch.md`

DDL as spec §2 (`050` block): `manufacturer.body jsonb`;
`manufacturer_playbook_revision`; `playbook_epoch` seeded with one row; the
`bump_playbook_epoch()` trigger function and three `FOR EACH STATEMENT` triggers;
grants.

`FOR EACH STATEMENT`, not `FOR EACH ROW` — the seed writes hundreds of rows and
the epoch only needs to change, not to count.

**The revision grant is `SELECT, INSERT` with no `UPDATE` and no `DELETE`, on
purpose.** Verified 2026-08-26: `dentalia_api` is refused `DELETE` on
`manufacturer_playbook_revision`, so append-only is enforced by the grant, not by
convention. Do not "fix" this by widening the grant when the revert action is
built — revert writes the old body forward as a **new** revision (Task 10).

- [x] Write the migration; apply against scratch
- [x] Full suite

### Task 6: Seed `body` from the 33 files

**Files:**
- Modify: `app/manufacturer_seed.py`

For each playbook, write the file's JSON **minus** the shredded identity keys
(`manufacturer`, `bc_codes`, `aliases`) into `manufacturer.body`, and write
revision 0 into `manufacturer_playbook_revision` with
`note='seeded from playbooks/{slug}.json'`. `playbook_rev` takes the file's `rev`
(hand-bumped, 0 on most).

The T0 parse keys (`match`, `ref_strategy`, `ref_strategy_config`) are part of the
body — one row feeds both loaders.

- [x] Extend the seed; verify all 33 bodies land and 5 carry `ref_strategy`
- [x] Full suite

### Task 7: `app/playbooks.py` — DB source

**Files:**
- Modify: `app/playbooks.py`
- Modify: `tests/test_playbooks.py`

Add `set_source(conn_factory)` and a module-level source. `load_playbooks()`:

- source unset → today's directory path, unchanged (lets the swap land incrementally and keeps existing tests honest);
- source set → `SELECT epoch FROM playbook_epoch`; on a cache hit return the cached tuple; otherwise `SELECT` the manufacturer rows with a playbook and `_parse()` each.

`_parse()` gains a from-row entry point: `manufacturer` from `canonical_name`,
`bc_codes` from `manufacturer_bc_code WHERE source='playbook'`, `aliases` from
`manufacturer_name WHERE kind='alias'`, the other 13 fields from `body`. Same
frozen `Playbook`, same 16 fields, same validation rules.

`clear_cache()` clears both cache shapes. `for_manufacturer()` and `validate()`
untouched.

**Failure handling is the load-bearing part of this task** (Global Constraints,
spec §3.5). Two distinct paths:

- a row whose `_parse()` raises → `log.warning(..., exc_info=True)`, skip it,
  keep the other 32. Identical to today's per-file behaviour.
- the source itself failing → one retry, then let the exception out. Do **not**
  cache an empty tuple on a source failure; a cached `()` would make one blip
  persist until the next epoch change.

Tests, both required:
- a malformed body among 33 yields 32 playbooks and one logged warning
- a connection factory that raises makes `load_playbooks()` raise, not return `()`
- a source failure does not poison the cache: after the source recovers, the next
  call returns all 33

**Also here** (spec §4.2, finding 3): constrain `extract_hints` keys to
`app.extract.tiers.TARGET` + `"general"` in `_parse()`. It is a correctness fix
independent of the UI — a typo'd key today silently emits a hint for a field the
model was never asked about. Import the list lazily so `app/playbooks.py` keeps
its no-PyMuPDF, no-extractor import footing.

- [x] Implement; keep the public API byte-identical
- [x] Full suite

### Task 8: Fold in `load_templates` and swap `web/registry.py`

**Files:**
- Modify: `app/extract/t0_layout.py`, `web/registry.py`

`load_templates()` becomes a projection over `load_playbooks()`: for each
playbook whose `body` carries `ref_strategy`/`match`, build the same `Template`.
Same never-raises posture — a bad template is logged and skipped, and a playbook
with no parse section is skipped silently (that is normal, not an error).

`web/registry.py`: `playbook_detail` renders `body` directly (drop the `:523` raw
re-read); `load_playbook_index`'s `:73` glob becomes "slugs present in the DB
minus slugs that parsed", rendering the same "N playbook(s) failed to parse"
error.

- [x] Implement both
- [x] Full suite

### Task 9: Wire `set_source` and pin the one-way move

**Files:**
- Modify: `app/workers/runner.py`, `web/app.py`, `app/cli.py`
- Modify: `app/reconcile.py`
- Create: `tests/test_playbooks_db_source.py`

One `set_source()` call per process. Workers and CLI use
`db.connect(cfg.connection.database_url, autocommit=True)`; web uses its existing
`_conn` shape against `cfg.api_database_url` (the `dentalia_api` role) —
`web/app.py:1755`. Short-lived, autocommit, never the caller's transaction.

`app/reconcile.py`: `_entities` and `derive_aliases` read rows instead of files.
**Conflict semantics must survive intact** — `validate()` raises and writes
nothing on a file-level conflict; a cross-catalogue code collision is skipped and
counted, not allowed to block the other 388.

Two pins, both required by the "two sources of truth eats a week" trap:

1. **No filesystem access.** With a source set, monkeypatch `pathlib.Path.glob`
   and `os.scandir` to raise, then call `load_playbooks()` and
   `t0_layout.load_templates()`. Both must succeed.
2. **Parity.** For each of the 33 slugs, the `Playbook` parsed from the DB equals
   the `Playbook` parsed from its file. This is the test that proves the "nine of
   twelve modules unchanged" claim, which is a design intention until it passes.

- [x] Implement and pin
- [x] Full suite
- [x] Run a real `discover.group` and the T0 half of `extract.doc`, file-backed
      vs row-backed, and confirm identical behaviour

**Done 2026-08-27, and NOT against the compose stack** — the images bake the
code in with no source mount, so a container run would have meant rebuilding
the shared `dentalia-app:latest` tag that the running containers and four other
worktrees use. That is the interference the throwaway-stack ruling existed to
avoid, so the isolation went where it mattered: a throwaway database
(`dentalia_pbcheck`, dropped afterwards), the real queue, the real handlers,
this branch's code on the host. The shared `dentalia` database never had 049 or
050 applied and no container was restarted — both re-checked after the run.

What it showed:

- **049 and 050 apply cleanly to a fresh database** (44 migrations, no errors),
  and the seed lands 33 manufacturers / 39 bc_codes / 88 names / 33 bodies.
- **`discover.group`**, enqueued and claimed through `app.queue` and dispatched
  through `HANDLERS`, is byte-identical from files and from rows: same rung
  ladder, same `discovery_log` rows, same emitted `fetch.url`. Run against
  RENFERT deliberately — its playbook carries the only kind of source that
  makes the playbook rung **hit**, and the URL it emitted
  (`.../CONF_6100x000.pdf`) came out of `doc_sources`. A manufacturer whose
  rung merely *missed* would have looked identical even if no playbook
  resolved at all, which is the trap this avoids.
- **T0 extraction** on a real KOMET declaration is identical, including the two
  outputs that exist only because of playbook data: the `komet`/`text-column`
  template matched via `match.anchors`, and `validity_from` read from
  `Rev. Stand 02.03.2026` — KOMET's `date_labels`, the key that took T0 from 1
  document to 106. Also identical: type, regulation, stated_class,
  cert_number, and the playbook-identity manufacturer.

T1/T2 were not run. They are the paid tiers and no playbook key reaches them
except `extract_hints`, which no playbook authors (0 of 33) — spending money
there would have proven nothing this does not.

---

# Slice 3a — Tier A editing, revisions, revert

### Task 10: Save path with revisions and optimistic locking

**Files:**
- Modify: `web/registry.py`, `web/templates/playbook_detail.html`

On save: `playbooks.validate()` on the **proposed** set first (the trap — a UI
save must be refused the way `playbooks sync` refuses a file-level conflict, not
accepted and discovered later by RESOLVE); then insert the new
`manufacturer_playbook_revision` and `UPDATE manufacturer SET body=…,
playbook_rev=playbook_rev+1, updated_by=…, updated_at=now() WHERE id=%s AND
playbook_rev=%s RETURNING id`. A `None` return means someone else saved while the
form was open → 422, same shape as `draft_save`'s "not editable" branch.

`note` is required. `authored_by` / `updated_by` from `_authenticated_user`.

Revision list + a revert action that writes the old body forward as a **new**
revision (append-only; never rewrite history).

- [x] Implement
- [x] Full suite

### Task 11: Tier A controls

**Files:**
- Modify: `web/templates/playbook_detail.html`, `web/registry.py`

Typed controls for `domains`, `doc_sources` kind=`portal`, `date_labels`,
`type_markers`, `source_priority` (spec §4.1). Tier B and Tier C render
read-only in this slice.

`domains` shows a live preview of the `site:` query `discover._query_for` would
build — the one affordance that lets a non-dev reason about a field whose effect
is otherwise invisible. Reuse `_normalize_domain`; do not write a second
normalizer.

**DONE 2026-08-27**, shipped in one change (`app/playbooks.py`, `web/registry.py`,
`web/templates/playbook_detail.html`, `tests/test_playbook_tier_a.py`,
`tests/test_playbooks.py` — 577 insertions). These two boxes were left unticked
by the commit-split repair that produced that commit, and the omission survived
into the "all tasks done" report; the work did not.

Also here and not in the plan: `source_priority` had no vocabulary check.
`_parse` accepted any string while `discover.group` raises `ValueError: unknown
source rung`, which dead-letters DISCOVER for every group of that manufacturer
over a spelling mistake. Now a closed `DISCOVER_RUNGS` set, checked at parse
time, with a test pinning it against `config.default_source_priority`.

- [x] Implement
- [x] Full suite — 2394 passed, 6 skipped at the time

---

# Slice 3b — Tier B controls and the D1/D2 guards

### Task 12: The D2 alias collision guard — DONE 2026-08-27

**Files (as built; the plan named only `web/registry.py` — see why below):**
- Modify: `app/playbooks.py` (`BrandCollision`, `brand_collision`, `validate(brands=)`),
  `app/reconcile.py` (the existing `alias-collision` finding, widened),
  `app/vendor_master.py` (`brand_index`), `app/cli.py` + `app/manufacturer_seed.py`
  + `web/registry.py` (the three write paths), `playbooks/ustomed.json`,
  `playbooks/kuraray-dental.json`
- Modify: `tests/test_manufacturer_seed.py` (one test now asserts the stricter refusal)
- Create: `tests/test_playbook_guards.py` — 25 tests, no database

**Two corrections to this task as written**, both measured rather than argued;
the spec §D2 and §4.4b carry the full record.

1. **Not `_suggest`.** `fuzz.ratio('Sirona Dental Systems GmbH', 'SIRONA')` is
   **12.5**, the Maillefer pair **9.3**, floor 80 — the prescribed scorer scores
   **zero** hits over all 38 playbooks, including the incident it was chosen for.
   The rule is word-boundary **containment** of a master name in a proposed name,
   exempting codes the playbook claims.
2. **Not only `web/registry.py`.** The incident arrived through file authoring +
   `playbooks sync`, so a UI-only guard would not have stopped it. The check
   lives in `playbooks.validate(brands=...)` — fed, never looked up, same shape
   as `refused` — and all three write paths pass it: `sync`, `manufacturers
   seed`, the UI save. `reconcile` reports through the same function.

`playbooks validate` stays a pure file check with no connection; the ladder is
validate (files) → reconcile (joins, reports, exit 1) → sync (refuses).

**First live run found two real collisions**, both resolved by claiming the code
(Denis, 2026-08-27): `ustomed` ← 10111 ULRICH STORZ (6 items), `kuraray-dental`
← 10071 NORITAKE (1 item). **6 `item_group` rows need a `regroup` after the next
`playbooks sync`** — logged as a followup, not run here.

- [x] Implement and test
- [x] Full suite — 2421 passed, 5 skipped

### Task 13: The D1 rename action — DONE 2026-08-27

**Files (as built):**
- Modify: `app/manufacturer_seed.py` (slug-first lookup — shipped separately, see below),
  `app/regroup.py` (`RenameImpact`, `rename_impact`), `web/registry.py`
  (`rename_manufacturer` + the route), `web/templates/manufacturer_detail.html`,
  `web/templates/_result.html` (a `confirm_needed` branch)
- Create: `tests/test_playbook_rename.py` — 19 tests

**"Renames and re-resolves in one transaction" is not possible.** The re-resolve
DELETEs `item_group` / `item_group_member` / `discovery_log` /
`grouping_suggestion`; `dentalia_api` is SELECT-only on all of them (invariant 1),
and there is no job type for a regroup, so enqueueing it is a PRD change. The
action therefore splits along the grant line: the UI writes the identity half
(`manufacturer.canonical_name` + `manufacturer_name`, demoting the old name to an
alias so documents printing it still resolve), prices the rest, and hands over the
exact `dentalia regroup` and `dentalia playbooks sync` lines. Spec §D1 carries the
full record. **`[FILLED — needs review]`.**

`regroup.rename_impact()` is the preview: a subset of `scope()`, because `scope()`
counts undrained `upload_inbox` rows and the web is INSERT-only there on purpose
("it must never read the spool back", migration 014). A test pins both of its
numbers against `scope()`'s.

Also corrected: `archive_path()` reads `item_group.canonical_manufacturer`, not
`manufacturer.canonical_name`, so the archive prefix splits when `playbooks sync`
runs and the groups re-resolve — not when the name is changed.

**Prerequisite, shipped as its own commit**: the seed looked entities
up by `canonical_name`, so a rename made it fall through to an INSERT and hit
`slug`'s UNIQUE constraint — one rename took down the entire import. Now slug-first,
divergence reported as a conflict.

- [x] Implement and test
- [x] Full suite

### Task 14: Tier B controls — DONE 2026-08-27

**Files:**
- Modify: `web/templates/playbook_detail.html`, `web/registry.py`
  (`tier_b_from_form`, `claimable_codes`, `claim_bc_code`, `POST /playbooks/{slug}/codes`),
  `app/playbooks.py` (the hint caps), `tests/test_playbook_save.py` (one
  assertion narrowed — the page now mentions `playbooks sync` truthfully)
- Create: `tests/test_playbook_tier_b.py`

- `bc_codes`: picker over `vendor_master` only, showing each code's item count. Never free text.
- `doc_sources` kind=`direct`: surface `RobotsRefused`'s existing message at 422, not a 500.
- `skip_backfill`: checkbox + mandatory reason.
- `extract_hints`: length cap per value and per playbook; keys already constrained in `_parse()` by Task 7. `_HINT_OVERRIDE` and both existing guards untouched.
- Tier C stays read-only.

Optional, if cheap: persist the existing `hinted` boolean
(`app/handlers/extract.py:175`, log-only today) onto `extraction_attempt` so "was
a hint applied" is answerable without a join. Not required — `(playbook_slug,
playbook_rev)` + the revision table already make the exact hint text recoverable.

**DONE 2026-08-27.** Built as specified, with two things worth recording:

- **`bc_codes` is ADD-ONLY, and that is a grant, not a preference.** Migration
  049 gives `dentalia_api` SELECT/INSERT/UPDATE on `manufacturer_bc_code` and no
  DELETE, so un-claiming stays CLI work. That is the right way round: adding a
  claim is exactly what a `BrandCollision` refusal asks for ("either add 012 to
  bc_codes … or drop the name"), while removing one strands whatever resolved
  under it. The picker excludes codes another playbook claims — `validate`
  refuses that pair anyway, so offering it would offer a save that cannot
  succeed.
- **The hint caps are anchored, not picked.** `HINT_MAX_CHARS = 300` sits just
  above `_HINT_OVERRIDE`'s 263 characters — the sentence that makes the block
  safe to send at all — so one hint may be about as long as the sentence that
  neutralises it and no longer; `HINT_MAX_TOTAL = 1500` keeps the block a note
  rather than a document, since it is billed on every T1/T2 call for that
  manufacturer forever. Zero of the delivered playbooks use `extract_hints`, so
  adoption cost is nil. Both live in `_parse`, so the CLI and the seed refuse
  what the form refuses.

The optional `hinted` column was **not** done: `(playbook_slug, playbook_rev)` on
`extraction_attempt` plus the revision table already recover the exact hint text,
so it would be a second copy of an answerable question.

- [x] Implement and test — `tests/test_playbook_tier_b.py`, 25 tests
- [x] Full suite — 2466 passed, 5 skipped

---

# Slice 4 — retire the files

### Task 14b: "Start a playbook" in the UI — DONE 2026-08-27

**Not in the original plan. Added after a check found the gap** (Denis chose it,
2026-08-27, over dropping the directory without a replacement).

`manufacturer.slug` was written in exactly ONE place — `manufacturer_seed`'s
UPDATE and INSERT — and none of the nine routes in `web/registry.py` created a
playbook. So `/manufacturers?no_playbook=1` listed the ~350 manufacturers that
need one and clicking through offered nothing to author, and task 15 was about
to drop the directory that held the only remaining way in. **The spec's line
"Onboarding worklist — `/manufacturers?no_playbook=1` already ships it —
Nothing to build" was wrong**: the worklist says who needs a playbook; nothing
could start one.

- `POST /manufacturers/{name}/playbook` attaches a slug and an EMPTY body at
  revision 0, then every Tier A/B control already built applies — rather than a
  second authoring surface here that would drift from them.
- The slug is suggested from the canonical name and editable (the delivered
  ones are shorter than their legal names on purpose: `ivoclar`, not
  `ivoclar-vivadent-ag`), case-folded rather than refused, and validated as a
  URL path segment. Refused when it is taken, when the manufacturer already has
  one, or when there is no row to attach to.
- Identity is inherited, never invented: the entity, its BC codes and its
  vendor-master aliases already exist from the seed.

- [x] Implement and test — `tests/test_playbook_start.py`, 27 tests
- [x] Full suite

### Task 15: Drop the mount, move `robots_refused.txt` — DONE 2026-08-27

**Files (as built):**
- Modify: `docker-compose.yml` (the `web` `:ro` mount, removed), `Dockerfile`
  (the COPY, **kept** — see below), `app/config.py` (`playbooks_dir` kept and
  re-documented — see below), `app/playbooks.py` (`load_robots_refused` reads
  rows), `playbooks/README.md`, `tests/test_playbooks_db_source.py`
- Create: `migrations/051_refused_host.sql`, `tests/test_refused_host.py`

**Two deviations, both narrowing the change rather than widening it.**

1. **The `Dockerfile` COPY stays** (Denis, 2026-08-27). `dentalia manufacturers
   seed` imports those files INTO the database, and an import runnable only
   from a git checkout is not an import anyone will run. Only the `web` mount
   goes, so the UI has exactly one store.
2. **`cfg.playbooks_dir` stays**, re-documented as a test/dev seam that must
   not be set in production. Deleting it would have forced
   `tests/test_web.py`'s malformed-playbook regression (finding 2, 2026-08-11)
   off the real wiring and onto a monkeypatch — strictly worse coverage for a
   knob that already defaults off and now carries an explicit warning.

Migration numbering: 042–045 are the EUDAMED branch's, so this is **051**, not
the 045 the plan guessed. Fourth renumber on this branch; `[migration-041-collision]`
still stands.

Only after Task 9's no-filesystem-access test is green — which it is, and it
now also covers `load_robots_refused`, the third loader and the one whose
filesystem fallback fails OPEN.

`robots_refused.txt` becomes a `refused_host (host PK, note, added_at)` table so a
UI save can be refused for the same reason a CLI one is; `load_robots_refused`
reads rows. It is deliberately not `.json` today precisely because
`load_playbooks` globs `*.json` — that constraint disappears with the directory.

The 33 JSON files **stay in git**, dated and marked historical in
`playbooks/README.md`. Deleting them loses the authoring rationale carried in
their comments.

- [x] Implement
- [x] Mount removed, playbooks and the refused list still resolve — verified by
      test, **not** by `docker compose up`. Four worktrees share that stack and
      restarting it is not this session's to do (Denis, 2026-08-27, the same
      ruling that governed the slice-2 compose check).
      `test_neither_loader_touches_the_filesystem_when_a_source_is_set` makes
      `Path.glob`/`is_dir`/`is_file`/`os.scandir` raise and then asserts all
      three loaders answer, which is a stricter condition than an absent mount:
      an absent mount lets a stray filesystem read return empty, this one makes
      it fail loudly. `test_refused_host.py` covers the same for the guard.
- [x] Full suite

### Task 16: Documentation

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (data model — the five new tables; **no job type added**), `docs/dentalia-schema-sketch.md` (tables + access matrix), `PHASES.md` (decision log), `docs/README.md` index docs (architecture, runbook, code-map), `tasks/followups.md`

`PHASES.md` decision log gets both 2026-08-26 rulings: *shred identity, jsonb the
behaviour*, and *`extract_hints` is Tier B — editable with hard limits*.

`tasks/followups.md` gets the drift this work measured but does not fix:
`- [ ] 2026-08-26 [orphaned-groups] 3 item_group rows carry '3SHAPE MEDICAL A/S' and 2 carry '' — re-resolve via regroup`

`tasks/playbooks-into-the-database.md` is marked superseded by the spec.

**DONE 2026-08-27.** `docs/dentalia-schema-sketch.md` gained the 050/051 tables
(`manufacturer.body`, `manufacturer_playbook_revision`, `playbook_epoch`,
`refused_host`) and two access-matrix rows for the UI's write paths — including
what it may NOT write, which is the load-bearing half; a stale `playbook_ref`
comment citing "044" was corrected to 050. `docs/dentalia-pipeline-contract-prd-v3.md`
§9 gained the five tables and an explicit statement that **no job type is
added** — the store changes where a handler reads its rules from, never the
enqueue graph. `PHASES.md`, `docs/runbook.md` and `docs/code-map.md` were kept
current task by task rather than in one pass at the end.

Not updated, and deliberately: `playbooks/README.md`'s pending-codes section and
`tasks/followups.md` beyond my own entry — another session is editing both, so
only my hunks are staged.

- [x] Update every doc listed
- [x] Full suite once more before the final commit

---

## Verification — RUN 2026-08-27

**Everything below was executed against a throwaway database
(`dentalia_e2e_pb`) loaded with the real `vendor_master` (390), `item_mirror`
(15 958), `item_group` (8 211) and `manufacturer_alias` (445), on the current
code. The shared `dentalia` database and its containers were not touched — the
same posture ruled for the slice-2 compose check.** Until this pass the whole
build was evidenced by pytest alone.

| | Result |
|---|---|
| All migrations on a fresh database | **45 applied, no errors**, including `049`/`050`/`051` |
| `manufacturers seed` (dry run) | 382 entities, 381 named, 389 codes, 437 names, **38 bodies** |
| Orphan report | 11 groups over 4 values — `ULRICH STORZ` 5, `3SHAPE MEDICAL` 3, `''` 2, `NORITAKE` 1 |
| `manufacturers seed --apply`, re-run | **0 new / all unchanged** — idempotent against a real database |
| Playbook parity, files vs rows | **38/38**, and 0 fields differ by VALUE (9 differ by the documented `aliases`/`bc_codes` sort only) |
| T0 templates + refused hosts from rows | 5/5 and 5/5, **with `Path.glob`/`is_dir`/`is_file`/`os.scandir` raising** |
| `for_manufacturer("Kuraray Noritake Dental Inc.")` | resolves to `kuraray-dental`; Komet's authored `date_labels` intact |
| 3a — save a `domains` edit over HTTP | 200, rev 0 → 1, body updated |
| 3a — second save from the stale form | **422**, "changed while this form was open", first edit intact |
| 3a — revert | 200, rev 1 → **2 (forward)**, domains restored, **3 revisions kept** |
| 3b — `Sirona Dental Systems GmbH` on dentsply-sirona | **422 naming SIRONA and 012** |
| 3b — rename preview | 200, "93 item group(s) (132 item(s)) still carry 'USTOMED'", names `dentalia regroup` |
| 4 — start a playbook | 200, `3D DENTAL SYSTEMS d.o.o.` → slug `3d-dental-systems-d-o-o`, body `{}`, rev 0 |

**The orphan report independently confirms `[orphaned-groups-ustomed-kuraray]`**:
5 + 1 = the 6 groups that need `dentalia regroup` after the next
`playbooks sync`, measured here rather than carried from an earlier reading.

Two things still NOT exercised, and neither is reachable this way: a real
`discover.group`/`extract.doc` round trip (needs live network and a paid tier),
and a browser session against a running `web` container (needs the shared stack
restarted). Everything between the HTTP layer and Postgres is covered above.

---

Full suite is mandatory for every task (see Global Constraints):

```bash
python -m pytest -q -n 4 --dist loadfile     # ~150s, 2276 tests as of 2026-08-26
```

End-to-end, per slice:

1. **Slice 1** — `dentalia manufacturers seed` (dry run) reports 384 entities /
   383 named / 33 playbooked / 39 authored codes and exactly 2 uncovered
   `item_group` values (`''`, `3SHAPE MEDICAL A/S`). Seed, set a contact on
   `/manufacturers/{name}`, then confirm `email_request._contacts` returns it —
   it has returned `[]` for every manufacturer since the table was declared.
2. **Slice 2** — parity test green (33/33), no-filesystem-access test green, and
   a real `discover.group` + `extract.doc` against the compose stack behaving
   identically to a file-backed run.
3. **Slice 3a** — save a `domains` edit; `playbook_rev` incremented; the revision
   is retrievable; revert restores; a second save from a stale form is refused at
   422.
4. **Slice 3b** — adding `Sirona Dental Systems GmbH` as a DENTSPLY alias **is
   refused, naming SIRONA**. A rename preview matches `regroup.scope()`.
5. **Slice 4** — `docker compose up` with the mount gone; DISCOVER and BACKFILL
   still resolve playbooks.

## Self-review

- **Scope:** four slices, each separately shippable and separately revertible.
  This satisfies CLAUDE.md's three-file rule by coherence, not count.
- **Riskiest task:** Task 9. Every consumer changes backing store at once, and
  the two pins in that task are the only thing standing between this and a
  silent dual-source-of-truth. Do not merge slice 2 without both green.
- **Claim discipline:** "nine of twelve modules unchanged" is a *design
  intention* until Task 9's parity test passes. Do not report it as a fact
  before then.
- **Assumed, not ruled** (spec §8): JSON files stay in git marked historical;
  `robots_refused.txt` becomes a table in slice 4; `note` is mandatory on a UI
  save. Any of these can be reversed without touching slices 1–2.

---

## Merging back to master — what breaks, and the order that avoids it

Written 2026-08-27 after the second merge of master into this branch.
Every figure below was measured, not carried over: the earlier
end-to-end pass ran against a migrations directory that did **not** contain
master's `042`, so nothing it proved covers the interaction that now exists.

### The rehearsal, in the live order

Not a fresh-database run. `042_eudamed_manufacturer.sql` seeds `manufacturer`
from `SELECT DISTINCT canonical_name FROM manufacturer_alias`, and on the live
database it ran against real alias rows — a fresh database has none, so it
inserts nothing there and the interaction stays invisible. Reproduced by
migrating to `041`, copying dev's real tables in, then migrating the rest:

| Step | Result |
|---|---|
| Migrate to 041, load real data | 390 vendor codes, 15 958 items, 8 211 groups, 445 aliases |
| Migrate 042 → 052 | 9 applied, including `046` and `049`–`052` |
| State after 042+049 | `manufacturer` **384 rows**, slug 0, body 0 |
| `manufacturers seed` dry run | 382 entities, 381 named, 1 skipped (`''`) |
| | **0 inserted, 38 updated, 343 unchanged**, 0 conflicts |
| Apply | 389 codes, 437 names, 38 bodies; `manufacturer` slug 38, body 38 |
| Re-run | 0/0/381 unchanged, 0 codes, 0 names, 0 bodies — idempotent |
| Rows vs files | playbooks 38/38, refused hosts 5/5, identical |

**So the seed composes with 042 rather than fighting it**, which is what 049's
header predicted and what had never been run. It inserts no manufacturer at
all; it attaches slugs and bodies to rows 042 already made.

### 1. Four migrations behind, and the gap is not the risk

**There are two Postgres containers on this machine and they are not the same
database.** `playbooks-into-db-postgres-1` belongs to this worktree's compose
project; `dentalia-postgres-1` is the live stack the running `worker`, `web`
and `caddy` are attached to. An earlier draft of this section measured the
first and called it "dev". Corrected, both read 2026-08-27:

| | migrations | highest | `manufacturer` |
|---|---|---|---|
| `dentalia-postgres-1` (**the target**) | 48 | `046_eudamed_gap_view.sql` | 384 |
| `playbooks-into-db-postgres-1` (worktree) | 47 | `045_..._backfill.sql` | — |

So the live database already has `046`. Pending there is **four** files:
`049`, `050`, `051`, `052`. The `047`/`048` gap is cosmetic —
`run_migrations` tracks by filename, never by number.

The rehearsal is unaffected and in fact corroborated: it started from 384
`manufacturer` rows seeded by 042, which is exactly what the live database
holds.

Master's new `db._out_of_order` guard reports **nothing** — every
pending file sorts above the highest applied one, which is the only condition
it fires on.

### 1b. Both images must be rebuilt, and that step sits between merge and migrate

Neither service bind-mounts code. `worker` and `scheduler` run
`dentalia-app:latest` off `Dockerfile`; `web` runs `dentalia-web:latest` off
`Dockerfile.web`. Confirmed on the running container: `/app/migrations` stops
at `046` and there is no `.git` inside it.

A merge therefore changes nothing the live stack can see. `docker compose
build worker web` has to happen after the merge and **before** `migrate`, or
the CLI in the container cannot see `049`–`052` to apply them.

Build, do not `up`. Recreating `worker` before the migration runs starts it
against tables that do not exist yet — which raises rather than corrupts (a
dead source is not an empty one, §2), but it is noise nobody needs mid-deploy.

### 2. The one genuinely dangerous window: migrated but not seeded

`050` creates the structure and the epoch triggers. **It seeds no bodies** — a
`.sql` file cannot read 38 JSON files. The bodies arrive only when
`dentalia manufacturers seed` runs.

Between `migrate` and `seed`, every entrypoint is pointed at empty tables, and
an empty table is not an error. `app/playbooks.py` raises on a DEAD source
precisely so it cannot be confused with this, but genuinely-empty rows return
`()` and mean "nothing is authored". Then `pb is None` everywhere, which
disables both `skip_backfill` and `exclude` — and BACKFILL archives Neodent's
28 Dentalia invoices. `load_robots_refused` has the same shape: empty reads as
"nothing is refused", which is a fetch we ruled against.

**Therefore: migrate and seed as one step, with no worker running between
them.** Stop the workers, `dentalia migrate`, `dentalia manufacturers seed`,
confirm 38 bodies, start the workers. The seed must run from the worker image —
it is the one that still `COPY`s `playbooks/` (only the `web` bind mount was
dropped).

### 3. 042 masks the seed's orphan report — a real regression in signal

`_orphan_groups` LEFT JOINs `manufacturer` on `canonical_manufacturer`. Before
042 that asked "does an entity cover this group's manufacturer". After 042,
`manufacturer` holds a row for every distinct alias canonical name, so the
question has quietly become "does any row exist at all", which is nearly always
yes.

Both halves were measured on ONE database, same tree, same data — the earlier
figure is not quoted from the pre-merge pass, because a number carried across
two trees proves nothing about either. `manufacturer` holds 384 rows post-042;
`manufacturer_name` holds the 437 the seed actually derived from entities:

| Join | Reports |
|---|---|
| `manufacturer` (as shipped) | **1 value / 3 groups** — `3SHAPE MEDICAL A/S` |
| `manufacturer_name` (the fix) | **4 values / 11 groups** — `ULRICH STORZ GMBH & CO. KG` 5, `3SHAPE MEDICAL A/S` 3, `''` 2, `NORITAKE` 1 |

The second reproduces exactly what the report said before 042 existed, which
is what makes it the fix rather than a different question.

Three of the four are now invisible, including the `''` pair that
`[blank-manufacturer-vat-placeholders]` depends on staying visible and the pair
`[orphaned-groups-ustomed-kuraray]` was filed from.

**Fix, one line:** join `manufacturer_name` instead of `manufacturer`. The seed
writes that table only for entities it actually derived, so it restores the
original meaning. Not done here — it is a behaviour change to a reporting query
and belongs in its own commit with its own test.

### 4. Every `manufacturer` write bumps the playbook cache epoch

`050`'s `manufacturer_epoch` trigger is `FOR EACH STATEMENT`, and a
statement-level trigger fires **whether or not it changed a row**. Measured:
five consecutive `INSERT … ON CONFLICT DO NOTHING` no-ops bumped the epoch from
2 to 7.

Master's `eudamed.certregister` handler runs `_MANUFACTURER_UPSERT` inside its
per-actor loop (`app/handlers/eudamed.py:432`), once for every matched actor,
to guarantee the FK target exists. So one register pull bumps the epoch once
per matched actor, and every worker re-reads all 38 playbooks on its next
`load_playbooks()`.

Not a correctness bug — the cache is doing exactly what it was built to do —
but it is cache churn proportional to register size, on a path that has nothing
to do with playbooks. **Cheapest fix is in master's handler, not here:** hoist
the upsert out of the loop and write the distinct canonicals once.

### 5. What is NOT a risk, checked rather than assumed

- **No other branch collides.** All 19 branches were enumerated:
  `playbooks-into-db` is the **only one with unmerged commits** (41 ahead).
  Every other worktree is already in master, so nothing is queued behind this
  that could conflict with it.
- **Reverse merge is clean.** `git merge-tree --write-tree master HEAD` exits 0
  with zero conflicts, re-checked after the second merge.
- **The scheduler needs no wiring.** `app/scheduler.py` does not import
  `playbooks` at all, so the three entrypoints that do (`workers/runner.py:200`,
  `web/app.py:3914`, `app/cli.py`'s `__main__`) are the complete set.
- **Full suite green after the merge:** 2639 passed, 5 skipped.

### Deploy order

Steps 1 and 2 must run in the main checkout (`/srv/dentalia`),
where master is checked out. Everything after them can be driven against the
running containers by name, on network `dentalia_default` with
`DATABASE_URL=postgresql://dentalia:dentalia@postgres:5432/dentalia`.

1. `git merge --ff-only playbooks-into-db` — 42 commits, 0 behind, so a
   fast-forward. `--ff-only` refuses rather than surprising you if that
   checkout has moved.
2. `docker compose build worker web` — **build only, no `up`** (§1b).
3. `docker stop dentalia-worker-1`.
4. `migrate` — expect exactly `049`, `050`, `051`, `052`. Not `046`: the live
   database already has it (§1).
5. `manufacturers seed` **without `--apply` first** — it is dry-run by default
   and prints the orphan report. Expect 382 entities / 381 named / 1 skipped,
   0 inserted / 38 updated / 343 unchanged, 389 codes, 437 names, 38 bodies,
   **0 conflicts**. Anything else: stop and read it.
6. `manufacturers seed --apply`.
7. `SELECT count(*) FROM manufacturer WHERE body IS NOT NULL` = **38**, and
   `count(slug)` = 38. Do not start the worker until both hold (§2).
8. `docker start dentalia-worker-1`, then recreate `web` so it picks up the new
   image.
9. `playbooks sync` — **no `--apply` flag exists; it writes and commits.**
   Expect it to re-point `10111` and `10071` and to report `orphaned_groups`.
10. `regroup --manufacturer "ULRICH STORZ GMBH & CO. KG" --manufacturer
    "NORITAKE"` (dry run), then the same with `--apply` — 6 groups,
    `[orphaned-groups-ustomed-kuraray]`. Note §3: the seed's own report will
    no longer name them, so this step is not self-discovering any more.
