# Playbooks into the database, linked to `manufacturer` — design

**Date:** 2026-08-26
**Status:** proposed, awaiting Denis review
**Supersedes as the plan of record:** `tasks/playbooks-into-the-database.md` (pick-up note, 2026-08-25). That note's §3 sketch and §5 slices are replaced by this spec; its §4 traps are carried forward and corrected here.
**Does not block, and is not blocked by:** `[eudamed-sweep-and-certificate-watch]`.

Normative sources this spec defers to and does not restate:
- CLAUDE.md invariants 1 (producer-only web process), 3 (REF gate), 7 (closed job-type enum).
- `docs/dentalia-pipeline-contract-prd-v3.md` — data model. This spec adds tables; it adds **no job type**.
- `docs/dentalia-schema-sketch.md` — the `dentalia_api` access matrix.
- `migrations/006_phase2_declared.sql` (`manufacturer`), `016_vendor_master.sql`, `017_manufacturer_alias_source.sql`, `031_extraction_playbook_rev.sql`.

---

## 1. Why

Playbooks are per-manufacturer authored rules steering DISCOVER, EXTRACT,
VALIDATE and BACKFILL. Today they are 33 hand-authored JSON files in
`playbooks/`, bind-mounted `:ro`, edited by an engineer in git. Onboarding a
manufacturer is therefore an engineering task, and that is the bottleneck on the
project's first end-state output — `item_ref` → evidenced documents.

Denis's framing (2026-08-26): move the authored body into the database, hang it
off the existing `manufacturer` table, and **link** that to the BC-imported
manufacturer list rather than writing into it.

**Non-devs will edit this in the web UI.** That is a requirement, not a
nice-to-have, and it is what makes §4 (guardrails) the centre of this design
rather than a finishing touch.

### 1.1 Why `manufacturer` is unused today

`manufacturer` (migration 006) has **0 rows** in the live database. It was
declared as Phase 2 — *"no handler writes these until Phase 2"* — and never got a
writer. Grep over `app/ web/ tools/ migrations/` finds inserts only in two test
files.

Two live readers exist and both silently return empty:

- `app/handlers/discover.py:229` — `_contact_known`
- `app/handlers/email_request.py:259` — `_contacts`, whose docstring cites the
  2026-08-20 ruling: *"Contacts live in the database, not in the playbooks …
  they change without an engineer, the person chasing must be able to fix one in
  the UI, and they are personal data that should not sit in git history forever."*

**That ruling has no writer and no data. Every email draft is unaddressed.** It
is a standing bug independent of the playbook question; slice 1 closes it.

`manufacturer.playbook_ref text -- path in playbook git repo` records the
original intent: files as truth, DB as pointer. This spec inverts that, and that
column is the seam where it inverts.

### 1.2 Measured state (live DB and tree, 2026-08-26)

| | |
|---|---|
| `manufacturer` | **0 rows** |
| `vendor_master` | 390 codes, 1 nameless |
| `manufacturer_alias` | 445 rows — 350 `vendor-master`, 94 `playbook`, 1 `self-seed` |
| `item_mirror` | 15 958 |
| `item_group` distinct `canonical_manufacturer` | 369 |
| playbook files | 33, claiming 39 BC codes — **all `LJ`, all present in `vendor_master`** |
| playbooks carrying a T0 parse template | 5 |
| playbooks using `extract_hints` | **0** |
| entities from `reconcile._entities` | 384 (383 named, 1 nameless, 33 playbooked, 4 multi-code, largest 3 codes) |
| `item_group` values covered by an entity canonical | 367 of 369 |

**Re-measured 2026-08-31.** The table above is the state that motivated this
design and is kept as the problem statement, not as current fact — every row of
it that the design changed has changed. Live now: `manufacturer` **384 rows**
(042 seeded it; the row above said 0, which was the whole point), **38**
playbooks carrying **46** BC codes under `manufacturer_bc_code`, 38
`manufacturer_playbook_revision` rows, `manufacturer_alias` 446, distinct
`item_group.canonical_manufacturer` 367. Unchanged: `vendor_master` 390,
`item_mirror` 15 958, playbooks with a T0 parse template **5**, playbooks using
`extract_hints` **0**.

The two uncovered values are `''` (2 groups) and `3SHAPE MEDICAL A/S` (3 groups).
The latter is live `orphaned_groups` drift: `3Shape Medical A/S` **is** correctly
aliased to `3SHAPE TRIOS A/S`, but those 3 groups were resolved before the
playbook repointed the code and were never re-resolved. Logged as a followup; the
seed must tolerate both and report them, never invent rows for them.

### 1.3 The four keyspaces this collapses

| Store | Key | Rows | Role |
|---|---|---|---|
| `vendor_master` | `(code_source, code)` | 390 | BC mirror, never hand-edited |
| `manufacturer_alias` | `raw_name` (flat, case-sensitive) | 445 | projection, 3 writers, not catalogue-namespaced (G11) |
| `playbooks/*.json` | `slug` | 33 | authored truth |
| `manufacturer` | `canonical_name` | **0** | declared, unused |

`item_group.canonical_manufacturer` is a bare text name with no FK — 369 distinct
values against 33 playbooks.

The entity that ties these together is computed today by
`reconcile._entities` (`app/reconcile.py:145`): union-find over vendor codes
grouped by name, joined by playbook `bc_codes` claims. It runs at
`playbooks sync` time and is thrown away. **That entity is the row `manufacturer`
should be.** It currently exists only for the duration of one CLI command.

---

## 2. Schema

Migration `049_manufacturer_entity.sql` (slice 1) and `050_playbook_body.sql`
(slice 2). Numbers skip 040-042, all taken by concurrent worktrees: `040_import_inbox.sql` (merged), `041_eudamed_job_types.sql` and `042_eudamed_manufacturer.sql` (unmerged, both already applied to the live DB). **Re-check the head before merging** — a migration number is global state that concurrent worktrees compete for, and this one moved three times in one session.

```sql
-- 049 --------------------------------------------------------------------
ALTER TABLE manufacturer
  ADD COLUMN slug         text UNIQUE,     -- NULL for the ~350 with no playbook
  ADD COLUMN playbook_rev int NOT NULL DEFAULT 0,
  ADD COLUMN updated_at   timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_by   text;

-- The LINK. `vendor_master` stays a pure BC mirror; nothing in this design
-- ever writes it. The FK is the link Denis asked for.
CREATE TABLE manufacturer_bc_code (
  code_source     text   NOT NULL,
  code            text   NOT NULL,
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  source          text   NOT NULL CHECK (source IN ('vendor-master','playbook')),
  PRIMARY KEY (code_source, code),                        -- one code, one manufacturer
  FOREIGN KEY (code_source, code) REFERENCES vendor_master
);

-- Every string that should resolve to a manufacturer: its canonical name and
-- its authored aliases, in ONE namespace. This is `playbooks.validate()`'s
-- name-uniqueness check expressed as a primary key.
CREATE TABLE manufacturer_name (
  name_folded     text   PRIMARY KEY,      -- casefold(); `for_manufacturer` compares folded
  name            text   NOT NULL,         -- as authored, for display
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  kind            text   NOT NULL CHECK (kind IN ('canonical','alias'))
);

CREATE INDEX manufacturer_bc_code_mfr_idx ON manufacturer_bc_code (manufacturer_id);
CREATE INDEX manufacturer_name_mfr_idx    ON manufacturer_name (manufacturer_id);

GRANT SELECT, INSERT, UPDATE ON manufacturer, manufacturer_bc_code,
      manufacturer_name TO dentalia_api;
GRANT USAGE ON SEQUENCE manufacturer_id_seq TO dentalia_api;
```

```sql
-- 050 --------------------------------------------------------------------
ALTER TABLE manufacturer ADD COLUMN body jsonb;   -- the BEHAVIOUR half

-- Append-only. What makes a bad edit one click to undo, and what keeps
-- `extraction_attempt.(playbook_slug, playbook_rev)` resolvable forever.
CREATE TABLE manufacturer_playbook_revision (
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  rev             int    NOT NULL,
  body            jsonb  NOT NULL,
  authored_at     timestamptz NOT NULL DEFAULT now(),
  authored_by     text,
  note            text,                    -- required on a UI save
  PRIMARY KEY (manufacturer_id, rev)
);

-- Loader cache watermark. Single row, index-only read, bumped by trigger.
CREATE TABLE playbook_epoch (
  one   boolean PRIMARY KEY DEFAULT true CHECK (one),
  epoch bigint  NOT NULL DEFAULT 0
);
INSERT INTO playbook_epoch (one, epoch) VALUES (true, 1);

CREATE FUNCTION bump_playbook_epoch() RETURNS trigger AS $$
BEGIN
  UPDATE playbook_epoch SET epoch = epoch + 1;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER manufacturer_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer          FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();
CREATE TRIGGER manufacturer_bc_code_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer_bc_code  FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();
CREATE TRIGGER manufacturer_name_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer_name     FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();

GRANT SELECT, INSERT ON manufacturer_playbook_revision TO dentalia_api;
GRANT SELECT, UPDATE ON playbook_epoch TO dentalia_api;
```

`manufacturer.playbook_ref` is left in place and commented as historical rather
than dropped: it is a declared-but-never-written column, and dropping it buys
nothing while a `DROP COLUMN` is the one part of 049 that is not trivially
reversible.

### 2.0 This DDL has been executed

Not reviewed by eye — **run**. Both blocks were applied verbatim to a throwaway
database migrated through 039 (PG 16.14), each inside one transaction as
`db.py:65` applies a migration file. Both exited 0. The database was dropped
afterwards; the live `dentalia` database was not touched.

Behaviour tested, not just syntax:

| Claim | Result |
|---|---|
| two manufacturers claiming one BC code | refused — `manufacturer_bc_code_pkey` |
| a code `vendor_master` does not issue | refused — FK, *"not present in table vendor_master"* |
| one name claimed by two manufacturers, casefolded | refused — `manufacturer_name_pkey` |
| `source` / `kind` outside the vocabulary | refused — both CHECKs |
| `slug UNIQUE` permits many NULLs | confirmed (the design depends on ~350) |
| `playbook_epoch` holds exactly one row | second insert refused |
| trigger bumps the epoch | 4 → 7 across 3 writes, one per table |
| `dentalia_api` can write the five new tables | INSERT + UPDATE all succeeded |
| `dentalia_api` cannot write the registry | `permission denied` on `document`, `item_document`, `evidence`; SELECT on `document` still works |

**One finding the DDL gave for free:** the grant is `SELECT, INSERT, UPDATE` with
no `DELETE`, so `dentalia_api` is refused `DELETE` on
`manufacturer_playbook_revision`. **The revision table's append-only property is
enforced at the grant level, not merely by convention.** Keep it that way — do
not add `DELETE` to that grant.

### 2.1 Why shred identity, jsonb the behaviour

**Denis's ruling, 2026-08-26.**

`bc_codes`, `manufacturer` and `aliases` are exactly what `playbooks.validate()`
polices. Shredding turns each of its checks into a constraint:

| `validate()` check today | Constraint after |
|---|---|
| BC code claimed by two playbooks | `manufacturer_bc_code` PK violation |
| name claimed by two playbooks | `manufacturer_name` PK violation |
| *(no check exists)* code BC does not issue | FK to `vendor_master` violation |

That converts *"authoring error discovered later by RESOLVE"* into *"save refused
at 422"*, which is the entire reason to move. All 39 current playbook codes exist
in `vendor_master`, so the FK grandfathers nothing.

The other 13 fields have no cross-row invariant and nothing queries into them.
`_parse()` consumes them unchanged from a dict. Shredding them would be a large
migration that buys nothing.

`source` deliberately reuses migration 017's vocabulary. It answers the same
question — human-authored claim vs entity-derived — and a BC refresh must know
which codes it may re-point.

### 2.2 `Playbook.bc_codes` keeps its current meaning

**The subtlety most likely to break something silently.** Today
`Playbook.bc_codes` holds the codes a human *authored*, not every code of the
entity; `reconcile.derive_aliases` expands to entity level afterwards. If the
loader populated `bc_codes` from all `manufacturer_bc_code` rows, `reconcile`
would change behaviour without any test noticing.

> The loader populates `Playbook.bc_codes` from rows where `source='playbook'`
> only. Entity expansion stays a separate query. Meaning preserved exactly.

### 2.3 Invariant 1 is untouched

`dentalia_api` gains writes on `manufacturer`, `manufacturer_bc_code`,
`manufacturer_name`, `manufacturer_playbook_revision`, `playbook_epoch` — never
`document` / `item_document` / `evidence`. A narrow grant to the web role is the
established pattern: `INSERT, UPDATE ON job` (007), `INSERT ON upload_inbox`
(014), `SELECT, UPDATE ON email_draft` (029), `SELECT ON vendor_master` (016).

Note that 007's `GRANT SELECT ON ALL TABLES IN SCHEMA public` covers only tables
existing at that time, so every grant above is explicit — same posture as 016.

**The other half of this boundary, and this spec missed it (added 2026-08-31).**
Grants police what the web role may *write*. Nothing here polices what the web
image may *import*, and that is the same boundary seen from the other side:
`Dockerfile.web` installs `.[web]`, never `.[extract]`, so the UI has no PyMuPDF.
On 2026-08-27 `_parse` acquired `from app.extract.tiers import TARGET` to
validate `extract_hints` keys (§4.2's own guardrail), which chains to
`app.extract.pdf` and `import pymupdf`. In the web container every playbook row
then raised and was skipped: `/playbooks` rendered "38 playbook file(s) failed to
parse" over an empty table, and with no rows there were no links to the editor
§4 is entirely about. The suite could not see it — the `test` image is built from
`Dockerfile` with the worker's dependency set. Fixed by moving `TARGET` to a leaf
module (`app/extract/target.py`) that imports nothing, and guarded by
`tests/test_web_import_closure.py`, which blocks the worker-only packages and
imports every `web/` module. **Rule this spec should have carried: a module the
web image loads may not import the extractor stack at module level.**

---

## 3. The loader

`app/playbooks.py` keeps `load_playbooks()`, `for_manufacturer()`, `validate()`
and `clear_cache()` with **identical signatures**, which is what keeps the change
inside two modules instead of twelve.

### 3.1 Where the connection comes from

`load_playbooks()` takes no connection today, and is reached from every stage.
Threading one through is precisely the change that touches everything.

> A module-level `set_source(conn_factory)`, called once at process start in
> `app/workers/runner.py`, `web/app.py` and `app/cli.py`. It opens its **own**
> short-lived autocommit connection.

Workers never *write* playbooks, so the loader never needs to see a caller's
transaction. The only consequence is that a worker picks up an edit one
epoch-read late — which is exactly today's behaviour with mtime. When no source
is set (tests that have not opted in), behaviour falls back to the directory, so
the swap can land incrementally.

### 3.2 Cache invalidation

`_dir_signature` is one `scandir`. Its replacement should be comparably cheap.

> `SELECT epoch FROM playbook_epoch` — one row, index-only, trigger-bumped.

`max(updated_at)` was considered and rejected: a sequential scan on every call.

**Corrected 2026-08-26, after measuring.** An earlier draft of this section
called this "VALIDATE's per-group hot loop", inheriting the phrase from
`app/playbooks.py`'s own docstring. The call sites do not justify it:

| Call site | Frequency |
|---|---|
| `validate.py:903`, `:939` → `_ref_normalize_rule` | ≤ 2 per validate job |
| `backfill.py:104,115,122,279` | ~4 per backfill scan job |
| `t0_templates.py:1089` | once per document — commented *"Computed once and shared below"* |
| `discover.py:378` | once per group — *"looked up once"* |
| `extract.py:326` | once per document |

So the loader is called a handful of times per job, not thousands. The epoch
counter is still the right design — it is cheap, deterministic and testable —
but it is not load-bearing for throughput, and no part of this spec should be
justified by a performance argument that does not hold.

### 3.3 The second loader disappears

`app/extract/t0_layout.py::load_templates` is a second, independent glob of the
same directory — the loader the pick-up note correctly calls "easy to miss". It
becomes a **projection over the same cached tuple**, filtered on a `ref_strategy`
key in `body` (5 of 33 today). The duplicate-glob hazard is removed rather than
ported. `Template` is unchanged.

### 3.4 `web/registry.py` loses all three filesystem touches

The pick-up note counted two loaders plus one raw read. There are **three** places
that touch the directory; `web/registry.py:73` was missed. It globs `*.json` and
diffs present slugs against parsed ones to render *"N playbook file(s) failed to
parse and are not shown"* — this repo's "skipped rows are counted and reported,
never silent" contract.

Under a DB store: `playbook_detail` renders `body` directly (no re-read), and the
broken-file diff becomes *rows whose `_parse()` rejected the body*, surfaced
identically.

**Wording corrected 2026-08-31.** "Loses all three filesystem touches" is true of
the production path and overstates the code. The glob is gone — `slugs()` answers
the broken-set question against whichever store is live — but `playbooks_dir`
still threads through `web/registry.py` (six function signatures, 18 references)
and `reads_files()`
still exists to phrase "directory not found", which is meaningless for rows. An
explicit directory always wins over the DB source; that is deliberate and load-
bearing, since the seed must read files or it would import its own output. So
the file path is **dormant behind an explicit override**, not removed.

### 3.5 Never-raises, split by failure kind

**Denis's ruling, 2026-08-26.** An earlier draft said only *"a malformed body must
behave identically to a malformed file"*. That is right for one bad row and
dangerously wrong for a dead source, and the difference is new: a bind-mounted
directory does not vanish mid-run, a database connection does (restart,
connection reset, pool exhaustion).

If a source failure were swallowed the way a parse failure is, `load_playbooks`
would return `()` and **every manufacturer would silently become "no playbook" at
once**:

| Stage | Silent consequence |
|---|---|
| DISCOVER | falls through to unrestricted search — no `domains`, no `doc_sources` |
| VALIDATE | every `ref_normalize` rule gone from the C1 REF gate |
| BACKFILL | `pb is None`, so neither `skip_backfill` nor `exclude` fires — Neodent's 28 Dentalia invoices get archived |
| T0 | every authored lexicon, `cert_number_pattern`, `ref_pattern` and parse template gone |

All of it logged as a warning nobody reads, and the damage lands in the registry.

> **Per-row parse failure** — log and skip, exactly as today. DISCOVER must not
> dead-letter a group over an authoring typo, and one bad body must not take the
> other 32 down.
>
> **Source failure** (cannot connect, query fails) — retry once, then **raise**.
> A worker that cannot read its playbooks fails the job and lets the queue retry
> it. It must never proceed as though no rules exist.

This deliberately narrows the never-raises contract for one case. The trade is
explicit: fail loudly and retry, rather than silently produce wrong output that
`gate.candidate` will then write to the registry.

`validate()` stays the loud operator path, unchanged.

---

## 4. Guardrails

**The governing decision: do not ship a JSON textarea.** `playbook_detail`
renders `raw` JSON read-only today. Making that field editable is the cheapest
thing to build and the worst thing to ship — it hands a non-dev a regex compiler
with production consequences.

Fields are tiered by blast radius, and the tier decides the control.

### 4.1 Tier A — typed control, non-dev safe

| Field | Control | Worst case |
|---|---|---|
| `contact_emails` | email list | unaddressed draft; the drafts UI already refuses to save one with no recipient |
| `domains` | hostname list through `_normalize_domain`, with a live preview of the `site:` query `discover._query_for` would build | junk candidates, or none — and `_search` retries unrestricted on a miss, so this can never be the reason nothing is found |
| `doc_sources` kind=`portal` | URL + `doc_type` dropdown | a dead link a human clicks |
| `date_labels` | additive lists, keys fixed to `from`/`to` | one extra date read; merged with the global lists, never replacing them |
| `type_markers` | additive lists, keys fixed to the four `DOC_TYPES` | a global phrase already wins a tie at the same offset |
| `source_priority` | multi-select over known rungs | a worse ladder, reversible |

### 4.2 Tier B — allowed, but the form must show consequence before saving

**`bc_codes`** — a picker over `vendor_master`, never free text. The FK makes an
invented code impossible; the picker makes it obvious. Shows the item count each
code carries, because that is the blast radius.

**`doc_sources` kind=`direct`** — this is a *fetch target*: DISCOVER's playbook
rung enqueues `fetch.url` for it. `validate()` already raises `RobotsRefused`
naming the host and telling the author to use `kind:"portal"` instead. The form
surfaces that message; it must not become a 500.

**`skip_backfill`** — a checkbox plus a mandatory reason, not a dict. It refuses
an entire corpus folder.

**`extract_hints`** — Denis's ruling, 2026-08-26. Editable with hard limits:

- Length cap per value, and per playbook.
- Keys constrained to `app.extract.tiers.TARGET` + `"general"`: `type`,
  `regulation`, `validity_from`, `validity_to`, `coverage_scope`, `ref_list`,
  `basic_udi_di`, `referenced_docs`, `cert_number`, `manufacturer`,
  `stated_class`.
- `_HINT_OVERRIDE` and both existing guards retained.

Rationale and the three findings behind the limits:

1. It is **the only playbook value an LLM ever sees** (`app/playbooks.py`), and it
   arrives as a *second system block* — `app/extract/llm.py:196`,
   `_system(base, hints)`. Opening it to a form opens the T1/T2 system prompt to
   whoever can reach the page. Existing containment is real (`_HINT_OVERRIDE`:
   *"They are context, not fact … ignore them entirely and report only what the
   document itself says"*, plus the `manufacturer` hint withheld when identity is
   itself being extracted, plus a hedged lead when the manufacturer was only
   inferred) but it was written against an engineer's typo.
2. **Zero of the 33 playbooks use it.** Tier B costs nothing to migrate; the risk
   is entirely prospective.
3. `_parse()` accepts *any* key today and only checks that values are strings, so
   a typo'd key silently emits a hint line for a field the model was never asked
   about. **The key allowlist is a correctness fix that belongs in `_parse()`
   regardless of the UI.**

**Traceability needs no new column.** `extraction_attempt` already carries
`playbook_slug` + `playbook_rev` (031), and `manufacturer_playbook_revision`
makes `(slug, rev)` resolve to the exact body forever. Persisting the existing
`hinted` boolean (log-only today, `app/handlers/extract.py:175`) is a one-column
nicety so "was a hint applied at all" is answerable without a join — optional,
not required by the ruling.

### 4.3 Tier C — rendered read-only, engineer only

`ref_normalize`, `companion`, `coverage_map`, `exclude`, `cert_number_pattern`,
`ref_pattern`, `match.anchors`, `ref_strategy`, `ref_strategy_config`.

**Honest justification.** An earlier draft of this spec claimed these fail
silently and catastrophically. Verification showed the existing guards are better
than that:

- `_parse` refuses an uncompilable regex, and refuses a `companion.key` without
  exactly one capture group (confirmed by execution:
  `ValueError: companion.key needs exactly one capture group`).
- `_apply_exclude` counts hits **per pattern** onto the job result precisely
  because "silent over-matching" is the failure it guards; a malformed playbook
  degrades to "exclude nothing, never a lost document"; and excluding *every* PDF
  under a folder raises.
- Ambiguous `companion` keys are warned and left unpaired.

The real argument for Tier C is therefore narrower and true: **a syntactically
valid rule that means the wrong thing, whose wrongness surfaces only as a count
in a job result or a log line a non-dev will never read.** That is sufficient to
keep them out of a non-dev's hands, and it is the reason to state — not the
overstatement.

### 4.4 Tier D — the two hazards that are not field edits

#### D1. The canonical name is effectively write-once

Verified at `app/handlers/resolve.py:334-339`: the ladder is
`_existing_link(...) or _by_udi(...) or _by_basic_udi_di(...) or _by_name_family(...)`
and short-circuits, so an item already in a group never re-derives its
manufacturer.

Consequences of a rename, both invisible in a form field:

1. It repoints `manufacturer_alias` but does **not** retro-fix existing groups.
   This is live today: 3 groups still carry `3SHAPE MEDICAL A/S`. Note also the
   case difference (`MEDICAL` vs the alias row's `Medical`) — `raw_name` is a
   case-sensitive `text` PK, so the alias cannot rescue them.
2. It changes `archiving.archive_path()`'s first segment for everything fetched
   afterwards, splitting one manufacturer's files across two archive prefixes.
   **Timing corrected 2026-08-27 (task 13):** `archive_path` takes that segment
   from `manufacturer_for_group`, which reads `item_group.canonical_manufacturer`
   — *not* `manufacturer.canonical_name`. So the split does not happen when the
   name is changed; it happens when `playbooks sync` re-points the alias and the
   affected groups are re-resolved. Same outcome, a different moment, and it is
   the moment the warning has to name.

> **A rename is a separate confirmed action, not a field edit.** It shows the
> orphaned-group count first and offers the re-resolve in the same transaction.

**"In the same transaction" is not possible, and the reason is invariant 1
(corrected on implementation, 2026-08-27, task 13).** The re-resolve means
DELETEing `item_group`, `item_group_member`, `discovery_log` and
`grouping_suggestion` rows — that is what `regroup.apply` does. `dentalia_api`
is SELECT-only on all of them, because the UI is a job producer and not a
registry writer. Nor can the UI enqueue the work: there is no job type for a
regroup, and adding one is a PRD change.

So the action splits along the grant line, and says so out loud:

- **The UI writes the identity half** — `manufacturer.canonical_name` and the
  `manufacturer_name` rows, both granted by 049. The old name is *demoted to an
  alias*, never deleted: documents already print it, and dropping the row would
  make every one of them stop resolving. It lands back in the playbook's
  `aliases` on the next load, which is where it belongs.
- **The UI prices the rest and hands over the command.** Step one writes
  nothing and reports the groups that will keep the old name, how many of those
  can never be regrouped (a member already carries an `item_document` link —
  the one irreversible case), and the exact `dentalia regroup --manufacturer
  "<old>" --apply` line. `playbooks sync` is named in the same breath, since
  `manufacturer_alias` is equally unwritable from here.

`regroup.rename_impact()` is that preview: a deliberate **subset** of
`regroup.scope()`, because `scope()` also counts undrained `upload_inbox` rows
and `dentalia_api` is INSERT-only there — *"it must never read the spool back"*
(migration 014), that column holding the uploaded file bytes. Widening a grant
for a count would trade a security decision for a convenience, so the subset
asks only what the web may already read and a test pins both of its numbers
against `scope()`'s so they cannot quietly stop agreeing.

**A prerequisite the plan did not see** (fixed as its own commit): the seed
looked entities up by `canonical_name`, which is not stable across a rename.
The file still said the old name, no row matched, and the seed fell through to
an INSERT — and since `slug` is UNIQUE, that raised and took the whole import
down. The lookup is now slug-first, and a divergence is reported as a conflict.
Without it the rename action would have bricked `manufacturers seed`.

#### D2. `aliases` — the highest-value and highest-risk field

It is the field a non-dev most needs (a supplier signs its DoC with a name we do
not know) and the one that has already gone wrong. `[brand-parent-aliases]`:
`Maillefer Instruments Holding Sàrl` and `Sirona Dental Systems GmbH` were
authored as DENTSPLY aliases and synced live. Both are their own BC brands
(MAILLEFER 022, SIRONA 012). That would have scoped 86 Maillefer documents to
DENTSPLY's groups and risked attaching a Maillefer certificate to a Dentsply
item. It was reverted the same session.

**`validate()` did not catch it** — it checks name *uniqueness*, and both names
were unique. ("and cannot", as this said until 2026-08-27, was wrong: it could
not because it was never given the vendor master. Handed a brand map it can, and
task 12 hands it one on every write path.)

> On save, match each proposed name against all 390 `vendor_master.name` values
> and every other manufacturer's canonical name. Refuse, naming the colliding
> brand.

**Corrected on implementation (2026-08-27, task 12). Both halves of the original
prescription were wrong, and measurement is what showed it.**

*The matcher.* This section said to reuse `reconcile._suggest` — rapidfuzz
`process.extractOne`, `fuzz.ratio`, floor `SUGGEST_MIN_SCORE = 80.0`. Measured
against the real master, that scorer **cannot see this failure**:
`fuzz.ratio('Sirona Dental Systems GmbH', 'SIRONA')` is **12.5** and the
Maillefer pair **9.3**, because the length disparity between a legal name and a
bare brand label dominates `ratio`. Over all 38 delivered playbooks it returns
**zero** hits. The master carries brand labels; playbooks carry legal names; the
relation between them is *containment*, not similarity. The implemented rule is
therefore word-boundary containment of a master name inside a proposed name,
exempting the codes the playbook itself claims — which is also the escape the
refusal offers ("add 012 to bc_codes, or drop the name"). `_suggest` keeps its
own job, where `ratio` is right: a typo of a *whole* name (`DENSTPLY` →
`DENTSPLY`, 88).

*"Exists nowhere today."* `reconcile.build` has had an `alias-collision` finding,
already in `BLOCKING`, since S1.8. It tested string **equality** against a master
name, which is why it too was blind to the incident, and it is a **report** —
`playbooks sync` calls `playbooks.validate`, never `reconcile.build`, so an
operator who skips the `reconcile` command reaches the write unchecked. The gap
was narrower and sharper than stated: not *no check*, but *the wrong comparison,
on a surface that cannot refuse*. Task 12 widens the comparison and moves the
refusal onto the three write paths (`playbooks sync`, `manufacturers seed`, the
UI save), with `reconcile` reporting through the same function so the two
surfaces cannot drift.

**It is still the single highest-value guardrail in this design.** On its first
run over live data it found two collisions nobody had noticed — see §4.4b.

#### 4.4b What the guard found on its first live run (2026-08-27)

Two collisions in the delivered playbooks, both the `[brand-parent-aliases]`
shape, both live, neither previously reported by anything:

| Playbook | Name it claimed | BC brand it swallowed |
|---|---|---|
| `ustomed` (10119, 132 items) | alias `Ustomed Instrumente Ulrich Storz GmbH & Co. KG` | **10111** `ULRICH STORZ GMBH & CO. KG`, 6 items |
| `kuraray-dental` (278, 157 items) | alias `Kuraray Noritake Dental Inc.` | **10071** `NORITAKE`, 1 item |

Both resolved by claiming the code (Denis, 2026-08-27), which is the guard's own
first suggestion and the outcome that keeps the alias *and* covers the items:

- **Ustomed** — the two codes share one catalogue numbering scheme (`03-326-130`,
  `27-743-130` under 10111; `02-030-030`, `03-417-130` under 10119), and the
  playbook's own note already read the alias as "a FORMER company name against
  the same register entry (HRA 451050)". One company, two BC codes.
- **Kuraray** — 10071's single item is `141080 NORI-VEST-SET`, a Nori-Vest
  investment; `Kuraray Noritake Dental Inc.` is the JV that owns that line and,
  per the playbook's note, the legal manufacturer. Dropping the alias instead
  would have cost the legal name every Kuraray DoC is signed with.

**Consequence to run before the next ingest:** `playbooks sync` will re-point
`manufacturer_alias` for both codes (`ULRICH STORZ GMBH & CO. KG` → `USTOMED`,
`NORITAKE` → `KURARAY DENTAL`), and re-pointing does not retro-fix groups already
resolved under the old value. **6 `item_group` rows** carry those values today
(5 + 1) and need a `regroup` pass after the sync. `sync` reports the count as
`orphaned_groups`; it was not run as part of task 12.

### 4.5 Revision history is a guardrail, not a feature

`extraction_attempt.playbook_rev` (031) is hand-bumped and `0` on most files. The
moment a non-engineer can press Save, `rev` must auto-increment **and old bodies
must be retrievable** — otherwise every historical extraction's provenance points
at a body that no longer exists, and invariant 2's "a production value can be
defended later" quietly stops holding.

`manufacturer_playbook_revision` is what makes that true, and it is also what
makes the D2 class of error one click to undo instead of a git archaeology
session. `note` is mandatory on a UI save so the list reads as a history rather
than a stack of anonymous diffs.

---

## 5. Reuse — what must not be rebuilt

| Need | Already exists |
|---|---|
| Validation-error render | `_result.html` + 422 — `draft_save` (`web/app.py:2712`) is the precedent |
| `updated_by` | `_authenticated_user()` (`web/app.py:1763`) via `access.trusted_user`; `Caddyfile:80` sets `header_up X-Forwarded-User {http.auth.user.id}` after `basic_auth`; `DEFAULT_DECIDED_BY = "user:admin"` covers local dev |
| Concurrent-edit lock | `draft_save`'s guarded `UPDATE … WHERE status='draft' RETURNING`. With `playbook_rev` in a hidden field, `WHERE playbook_rev=%s` is optimistic locking for free |
| Pre-write conflict check | `playbooks.validate()` on the proposed set — unchanged |
| Alias collision detection | `reconcile.build`'s `alias-collision` finding — widened from equality to containment and shared with the refusal (§D2). NOT `_suggest`: measured blind to this failure |
| Rename blast radius, and the fix | `regroup.scope()` (read-only) + `regroup` |
| Entity grouping | `reconcile._entities` — becomes the seed, then retires as a runtime concept |
| **Onboarding worklist** | **`/manufacturers?no_playbook=1` already ships it** — `annotate_playbooks` + `filter_and_sort`, sorted gaps-first-then-biggest-first, *"the question this page exists to answer"*. Nothing to build. |
| Pages to host the editor | `/manufacturers`, `/manufacturers/{canonical_name}`, `manufacturer_detail.html` already render `playbook_slug`. No new page needed. |

**One suspicion closed.** `annotate_playbooks` matches by `name.strip().upper()`
or any `bc_code`; `_entities` uses union-find over shared vendor-master names.
Its docstring claims *"Same union rule as `playbooks sync`, so the UI and the CLI
cannot disagree."* Measured against live data: 383 shared names, **0
disagreements**, both report 33 playbooked. Agreement on today's data is not
proof of equivalence — but slice 1 makes the question moot by leaving exactly one
rule.

---

## 6. What does not change

- `app/handlers/resolve.py` — still self-seeds `manufacturer_alias`
  (`resolve.py:77`). **This is why `manufacturer_alias` stays a table and never
  becomes a view**: a view cannot take that insert without a rule or trigger, and
  unwinding the self-seed is its own task.
- `app/handlers/gate.py`, `app/manufacturers.py` (the shared normalizer VALIDATE
  and GATE both import), `app/vendor_master.py`, `app/handlers/archiving.py`.
- **25 `manufacturer_alias` references across 4 web modules** — `web/app.py` 14,
  `web/registry.py` 9, `web/catalogue.py` 1, `web/item_docs.py` 1. Keeping the
  projection a table is what buys that.
- Nine of the twelve playbook-consuming modules, because the loader API is
  unchanged: `discover.py`, `extract.py`, `validate.py`, `backfill.py`,
  `t0_templates.py`, `komet_coverage.py`, and (bar one `set_source()` line each)
  `cli.py`, `web/app.py`.
- **No job type is added.** The queue enum (invariant 7) is untouched, so this is
  a PRD data-model change only.

> **Stated plainly:** every "unchanged" claim in this section is a *design
> intention*, not a measurement. It follows from keeping the `load_playbooks()` /
> `for_manufacturer()` signatures, and it is proven by the slice-2 parity test —
> not before.

---

## 7. Slices

| | | |
|---|---|---|
| **1** | Tables + one-way seed from `_entities` and the 33 files. Nothing reads the DB yet. | Independently valuable: closes the `contact_emails` bug |
| **2** | `body` jsonb + swap `load_playbooks`; `load_templates` folds in | **The risky one** — every consumer changes backing store at once |
| **3a** | Tier A editing, revisions, revert | Safe to hand a non-dev on day one |
| **3b** | Tier B controls + the D1 rename action + the D2 alias guard | Where the guards that prevent an 86-document mis-scope live |
| **4** | Retire the `:ro` mount and the `Dockerfile` COPY; `robots_refused.txt` → `refused_host` table | Only after nothing reads the directory |

Splitting 3 matters: 3a is safe without 3b, but 3b's guards are what make the
dangerous fields safe to expose at all.

**Two sources of truth is the failure that eats a week.** The move is one-way:
seed once, then the directory is never read again, pinned by a test asserting the
loader performs no filesystem access. The `:ro` mount can only be retired after
that test is green — not before.

---

## 8. Open questions

**Settled by Denis, 2026-08-26** — the first two were assumptions when this spec
was written and are now rulings; see the PHASES decision log.

1. **Do the JSON files stay in git once the DB is authoritative?** **Yes — they
   stay, for reference.** The DB is authoritative from slice 2; the files are the
   authoring record and are not deleted in slice 4. Deleting loses the rationale
   carried in 33 files' worth of comments and nothing else carries it.
2. **Is an audit trail of *who* edited required?** **Yes, and it is also the
   revert mechanism.** `updated_by` / `updated_at` shipped in migration 049;
   `manufacturer_playbook_revision` lands in slice 2. Revert writes the old body
   forward as a **new** revision rather than deleting one — which is why the
   `dentalia_api` grant on that table is `SELECT, INSERT` with no `UPDATE` and no
   `DELETE`. Append-only is enforced by the grant, not by convention.

3. **`robots_refused.txt`** is deliberately not `.json` (`load_playbooks` globs
   `*.json` and would parse it as a playbook). **It becomes a `refused_host`
   table in slice 4**, so a UI save can be refused for the same reason a CLI
   one is — otherwise the file must outlive the mount and the editor cannot
   enforce the 2026-08-20 fetch refusal at all.
4. **Is `note` mandatory on a UI save?** **Yes — refuse at 422 without one**,
   and not per-tier. It is what makes the revision list readable and the revert
   obvious, which is the job ruling 2 gave it.

Nothing in this spec is open. Every question above has been put to Denis and
answered; the remaining uncertainty is in the plan, not the design.
