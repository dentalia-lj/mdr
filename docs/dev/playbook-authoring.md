# Playbook authoring — onboarding a manufacturer

**Status:** live. Measured against the tree at 2026-08-31.

A walkthrough: a new manufacturer arrives, what a developer (or, since
2026-08-27, a non-engineer through the UI) does, in order, until its first
document lands in the registry. The **contract** for the fields lives in
[dentalia-job-type-handbook.md](../dentalia-job-type-handbook.md) and the
schema in [dentalia-schema-sketch.md](../dentalia-schema-sketch.md); this file
only tells you where the code is and what it does today. The design history is
four specs under `docs/superpowers/specs/`
(`2026-07-29-manufacturer-playbooks-design.md`,
`2026-08-10-registry-manufacturers-playbooks-design.md`,
`2026-08-18-playbooks-across-stages-design.md`,
`2026-08-26-playbooks-into-the-database-design.md`) plus the `/playbooks` and
`/manufacturers` notes in [runbook.md](../runbook.md) — read those for *why*,
this file only for *where* and *in what order*.

## Idea

A playbook is per-manufacturer authored config: which BC codes and legal
names are this manufacturer, which domains and document URLs to fetch from,
and — for a handful of manufacturers — extra lexicons that steer T0's
deterministic parse. `app/playbooks.py` owns loading and validating it;
nothing else parses a playbook body. Until 2026-08-26 the whole thing was one
JSON file per manufacturer, hand-edited and reviewed like code
(`app/playbooks.py:1-19`). Since migrations 049–051 the identity half is
constrained Postgres rows, the behaviour half is a `jsonb` column with an
audited revision history, and a non-engineer authors most of it through
`/playbooks/{slug}`. The files still exist (`playbooks/*.json`, 38 as of this
writing — the design docs' "33" is a 2026-08-20/27 snapshot, since grown) and
still matter: they are what `manufacturers seed` imports and what
`playbooks validate` lints, and they are the only way to seed the engineer-only
fields before a playbook's first import.

## Where

| Concern | File |
|---|---|
| Loader/validator, two-store logic, guards | `app/playbooks.py` |
| Identity tables (`manufacturer`, `manufacturer_bc_code`, `manufacturer_name`) | `migrations/049_manufacturer_entity.sql` |
| Behaviour column + revision history + epoch cache | `migrations/050_playbook_body.sql` |
| `refused_host` table | `migrations/051_refused_host.sql` |
| Rename FK cascade fix | `migrations/052_manufacturer_rename_cascade.sql` |
| `extraction_attempt.playbook_slug`/`playbook_rev` | `migrations/031_extraction_playbook_rev.sql` |
| File → database import (`manufacturers seed`) | `app/manufacturer_seed.py` |
| `playbooks validate\|reconcile\|sync`, `manufacturers seed`, `regroup`, `vendor-master` CLI wiring | `app/cli.py` |
| Web routes, form folding, guards, revert | `web/registry.py` |
| Playbook detail/edit page | `web/templates/playbook_detail.html` |
| T0 parse-template engine (reads a playbook's `match`/`ref_strategy` keys) | `app/extract/t0_layout.py` |
| Where T0 lexicons and REF patterns are applied | `app/extract/t0_templates.py` |
| DISCOVER's `playbook` rung (the fetch-target consumer) | `app/handlers/discover.py:499-511` |
| EXTRACT's hint block and steering resolution | `app/handlers/extract.py` |
| `WEB_PLAYBOOKS_DIR` config key | `app/config.py:432-441,455,910` |
| Worker/CLI/web process wiring (`set_source`) | `app/workers/runner.py:210`, `app/cli.py:896-909,917-919`, `web/app.py:3889-3907,3914` |
| Playbook files ship only in the worker image | `Dockerfile:36-50` |

## Walkthrough

1. **Confirm BC has issued the manufacturer a code.** `manufacturer_bc_code`
   foreign-keys `(code_source, code)` to `vendor_master`
   (`migrations/049_manufacturer_entity.sql`) — a code BC has never issued can
   never be claimed. If it is missing, import it first:
   `python -m app.cli vendor-master --file <export> --apply`
   (`app/cli.py:445-493`, dry-run by default).

2. **Create the manufacturer entity row.**
   `python -m app.cli manufacturers seed --apply`
   (`app/cli.py:395-442`, `app/manufacturer_seed.py:253-316`). This unions BC
   codes into one entity (`reconcile._entities`, reused rather than
   re-derived) and writes one `manufacturer` row with `slug IS NULL`,
   `body IS NULL` — this is exactly what `/manufacturers?no_playbook=1` lists.
   Dry-run by default, additive, idempotent, and it **refuses to write on any
   conflict** rather than half-applying (`app/manufacturer_seed.py:86-88`
   `SeedStats.clean`).

3. **Give the entity a playbook.** Two paths, and they converge on the same
   row:
   - **UI (the normal path since 2026-08-27):** open the entity's page from
     `/manufacturers?no_playbook=1`, click **Start a playbook**. Posts to
     `/manufacturers/{name}/playbook` → `start_playbook`
     (`web/registry.py:828-887`), which attaches a slug and an **empty** body
     at revision 0. Everything below then applies to that row. This removed
     the old bottleneck: before it, `manufacturer.slug` had exactly one
     writer (`manufacturers seed`), so starting a playbook meant hand-writing
     `playbooks/{slug}.json` in the repo.
   - **File (still works, and the only route for Tier C — see Limits):**
     hand-write `playbooks/{slug}.json` (`manufacturer`, `bc_codes`,
     `aliases`, `domains`, `doc_sources`, and any Tier C keys), lint it with
     `python -m app.cli playbooks validate` (`app/cli.py:223-289`, exits 2
     naming the offending file), then import it with `manufacturers seed
     --apply` again — `_write_body` (`app/manufacturer_seed.py:206-250`) only
     writes a body when the existing one is `NULL` or byte-identical, so this
     step must happen **before** anyone edits the playbook in the UI.

4. **Author Tier A fields** — `domains`, `doc_sources` kind `portal`,
   `date_labels`, `type_markers`, `source_priority` — on `/playbooks/{slug}`.
   Every save posts to `save_playbook_body` (`web/registry.py:613-708`):
   requires a note, re-parses, re-validates the **whole playbook set**
   (`BrandCollision` included), and is optimistic-locked on the revision the
   form was opened at.

5. **Author Tier B fields if this manufacturer needs them** — a `doc_sources`
   kind `direct` fetch target, `skip_backfill` (with a reason), `extract_hints`
   (key-allowlisted, length-capped). Claim any further BC codes through the
   add-only picker, `POST /playbooks/{slug}/codes` → `claim_bc_code`
   (`web/registry.py:920-984`).

6. **Run `python -m app.cli playbooks sync`** (`app/cli.py:349-393`). This
   writes `manufacturer_alias` from the codes and aliases just claimed —
   RESOLVE and GATE read that table, not `manufacturer_bc_code`/
   `manufacturer_name` directly. Run it **before** the next ingest touches
   this manufacturer's items: it reports `orphaned_groups`, which does not
   retro-fix itself.

7. **If items were already ingested under the old identity**, drop and
   rebuild their groups: `python -m app.cli regroup --manufacturer <name>
   --apply` (`app/cli.py:496-545`, dry-run by default, refuses the whole
   selection if any part is blocked).

8. **Let the pipeline run.** RESOLVE groups the manufacturer's items via
   `manufacturer_alias`; DISCOVER's `playbook` rung
   (`app/handlers/discover.py:499-511`) tries the authored `doc_sources`
   kind `direct` URLs — a `portal` source is never auto-fetched, it is a link
   a human clicks. A hit logs `discovery_log` (`rung='playbook',
   outcome='hit'`) and emits `fetch.url`; a miss logs `outcome='miss'` and
   falls through the ladder. FETCH → EXTRACT → VALIDATE → GATE proceed as for
   any document; EXTRACT resolves the steering playbook itself
   (`app/handlers/extract.py:322-333`, `t0_templates.steering_playbook`) —
   authoritatively from the group's `canonical_manufacturer` when one exists,
   otherwise from T0's own text match on the document (the live path for
   every backfilled document, which carries no `group_id`).

9. **Check whether it worked.** `/playbooks/{slug}`'s history card shows the
   revisions; `discovery_log` rows for the group show which rung fired;
   `extraction_attempt.playbook_slug`/`playbook_rev`
   (`migrations/031_extraction_playbook_rev.sql`) shows whether a playbook
   steered the read that produced the candidate; `/staging`, `/documents` and
   the manufacturer's completeness card on `/manufacturers/{name}` show the
   candidate itself.

## Input → Output

| In | Consumed by | Effect |
|---|---|---|
| `bc_codes`, canonical name, `aliases` | `playbooks sync` → `manufacturer_alias`; RESOLVE, GATE | Which items and which document mentions resolve to this manufacturer |
| `domains` | `discover._query_for`'s `search` rung | Scopes a `site:` search to these hosts (preview: `site_query_preview`, `web/registry.py:1034-1045`) |
| `doc_sources` kind `portal` | Rendered as a link on `/playbooks/{slug}` | Nothing is fetched automatically; a human clicks it |
| `doc_sources` kind `direct` | DISCOVER's `playbook` rung (`app/handlers/discover.py:499-511`, `_direct_urls`) | Enqueues `fetch.url` directly — this is a live outbound request |
| `source_priority` | `_source_priority` (`app/handlers/discover.py:122-129`, called at `:378-379`) | Overrides the TOML `[source_priority]` default ladder for this manufacturer |
| `date_labels`, `type_markers`, `cert_number_pattern`, `ref_pattern` | `app/extract/t0_templates.py` (`extract_dates`, `extract_cert_number`, `_looks_like_ref`) | Additive/narrowing extensions to T0's global lexicon — merged, never replacing it |
| `extract_hints` | `app/handlers/extract.py:51-90` (`_hints_for`) | Appended as a second system block on the T1/T2 call — the **only** playbook value an LLM ever sees |
| `companion`, `coverage_map`, `exclude`, `skip_backfill` | `app/handlers/backfill.py` | Which corpus-dump files pair as annex/primary, which are indexed by a manufacturer's own coverage sheet, which are dropped before archiving, or whether the whole dump is refused |
| `ref_normalize` | `app/handlers/validate.py` (`for_manufacturer` lookup) | Per-manufacturer REF comparison rule for VALIDATE's C1 gate |
| `match.anchors`, `ref_strategy`, `ref_strategy_config` | `app/extract/t0_layout.py:39-93` | T0's page-1 template match and REF-table extraction strategy — **not** `Playbook` dataclass fields; read straight off the raw dict by a second, parallel loader |

## Rules

### Where the data lives, and which source wins

| Reader | Default source | How to force files |
|---|---|---|
| Worker (`app/workers/runner.py:210`) | Database (`manufacturer.body` + identity tables) | n/a — worker never reads files |
| Web (`web/app.py:3889-3907,3914`) | Database | `WEB_PLAYBOOKS_DIR` env var (`app/config.py:432-441,455,910`) |
| CLI, general (`app/cli.py:896-909,917-919`) | Database | pass `dir_path` explicitly |
| `manufacturers seed` | **Always files** (`app/cli.py:414`, `app/manufacturer_seed.py:272`) | n/a — reads its own import source, never its output |
| `playbooks validate` | **Always files** (`app/cli.py:243-249`) | n/a — the operator lint runs against the authored files |
| `playbooks reconcile` / `playbooks sync` | Database (no explicit `dir_path`, `app/cli.py:301,355`) | n/a |

An explicit directory argument always beats the configured source
(`app/playbooks.py:678-716,718-750`), which is the one rule that makes both
of the above true at once. `WEB_PLAYBOOKS_DIR` is a **test/dev seam only**
(`app/config.py:432-441`) — it makes the web process read files *and save
against them*, silently, with no divergence report against the rows the
worker still reads. The directory does not even exist in the web image
(`Dockerfile.web` copies only `app/` and `web/`; `Dockerfile:36-50` is the
one `COPY playbooks/` and it is worker-only) — setting the env var in
production fails in the worst direction rather than erroring.

### Identity model

Migration 049 turned `playbooks.validate()`'s three cross-file checks into
schema:

| Playbook concept | Table | Constraint |
|---|---|---|
| Duplicate BC code across playbooks | `manufacturer_bc_code`, PK `(code_source, code)` | One code, one manufacturer |
| A code BC never issued | `manufacturer_bc_code` FK → `vendor_master` | No equivalent existed before 049 |
| Duplicate name (canonical or alias) across playbooks | `manufacturer_name`, PK `name_folded` (casefolded) | One name, one manufacturer — case-insensitively, matching `for_manufacturer`'s own comparison |

`manufacturer.slug` is the playbook's own name for itself — `UNIQUE`,
nullable (most `manufacturer` rows have no playbook; `slug IS NULL` is
exactly what `/manufacturers?no_playbook=1` lists). Migration 050
adds the behaviour half: `manufacturer.body jsonb`,
`manufacturer_playbook_revision` (append-only, `PRIMARY KEY
(manufacturer_id, rev)`), and `playbook_epoch` (a single-row counter,
statement-triggered on the three identity tables and on `manufacturer`
itself, so the loader's cache invalidates on one indexed read rather than a
sequential scan per `for_manufacturer` call).

### Field tiers and their guards

| Tier | Fields | Editable by | Body key |
|---|---|---|---|
| A | `domains`, `doc_sources` kind `portal`, `date_labels`, `type_markers`, `source_priority` | Anyone, via the form | `web/registry.py:1023-1024` `TIER_A_FIELDS`, folded by `tier_a_from_form` (`:1048-1110`) |
| B | `doc_sources` kind `direct`, `skip_backfill`, `extract_hints`, `bc_codes` | Anyone, with a consequence shown next to the control | `web/registry.py:1124` `TIER_B_FIELDS`, folded by `tier_b_from_form` (`:1127-1195`); `bc_codes` has its own route |
| C | `ref_normalize`, `companion`, `coverage_map`, `exclude`, `cert_number_pattern`, `ref_pattern`, `match.anchors`, `ref_strategy`, `ref_strategy_config` | Engineer only, file + `manufacturers seed` | Rendered read-only; no form folds these — see Limits |

The tier line is drawn on **blast radius**, not typing difficulty
(`docs/superpowers/specs/2026-08-26-playbooks-into-the-database-design.md`
§4.3): Tier C's real failure mode is a syntactically valid rule that means
the wrong thing and surfaces only as a count in a job result a non-dev will
never read, not a crash — the existing parse-time guards (regex compiles,
`companion.key` needs exactly one capture group, `exclude` counts hits per
pattern) are already real, they just are not legible to a non-dev.

### Guards that refuse a save

| Guard | Raised by | Meaning | How to resolve |
|---|---|---|---|
| `BrandCollision` | `app/playbooks.py:75-100,864-904`, checked in `validate()` (`:907-980`) | This playbook's name/alias **contains** another BC brand's master name, on word boundaries, and this playbook does not claim that brand's code | Claim the code (Tier B picker), or drop the name |
| `RobotsRefused` | `app/playbooks.py:93-100,930-943` | A `doc_sources` kind `direct` URL's host is in `refused_host` (`migrations/051_refused_host.sql`) — an operator ruling, not a live robots.txt read | Author it as kind `portal` instead, so a human handles it |
| BC code already claimed | `claim_bc_code` (`web/registry.py:957-963`) | The code belongs to this playbook already, or to a different one | Nothing to do (own case), or resolve with the other playbook's owner (one code, one playbook) |
| Slug already taken / entity already has one | `start_playbook` (`web/registry.py:863-872`) | `manufacturer.slug` is `UNIQUE`; an entity gets exactly one playbook | Pick a different slug, or edit the existing playbook instead of starting a new one |
| Stale form (`StaleForm`) | `save_playbook_body` (`web/registry.py:685-689,704-707`), `revert_playbook` (`:996-1013`) | Someone else saved while this form was open — optimistic lock on `playbook_rev` | Reload and re-apply the edit |
| Missing note | `save_playbook_body` (`web/registry.py:644-645`) | A note is mandatory on every save | Say what changed and why |
| Body fails to parse | `save_playbook_body` (`web/registry.py:668-671`), wraps `app/playbooks.py:250-496` `_parse` | Any of `_parse`'s rules — bad regex, wrong capture-group count, unknown `source_priority` rung, unknown `extract_hints` key, oversized hint | The message names the specific rule; fix the field it names |

All of the above are also enforced by `sync_aliases` (`app/cli.py:161`) and
`manufacturer_seed.seed` (`app/manufacturer_seed.py:279-283`) — the UI and the
CLI cannot disagree about what an authoring error is, because every one of
these callers runs the same `playbooks.validate()`, fed a `brands=` map
(master name → its BC codes) built the same shape either way: `save_playbook_body`
and `sync_aliases` call `vendor_master.brand_index(conn)`
(`web/registry.py:676`, `app/cli.py:161`); `manufacturer_seed.seed` builds its
own from the `vendor_master` rows it already loaded rather than re-querying
(`app/manufacturer_seed.py:277-283`).

### CLI commands

| Command | Flags | Dry-run? |
|---|---|---|
| `python -m app.cli playbooks validate` | none | n/a — read-only lint, writes nothing ever |
| `python -m app.cli playbooks reconcile` | `--export <BC export>`, `--catalogue` (default `LJ`), `--limit` (default 40) | Always read-only; exits 1 on blocking findings |
| `python -m app.cli playbooks sync` | none | No — this is the first write (`manufacturer_alias`) |
| `python -m app.cli manufacturers seed` | `--apply` | **Yes by default**; `--apply` to write (`app/cli.py:801-814`) |
| `python -m app.cli regroup` | `--manufacturer` (repeatable), `--group` (repeatable), `--all`, `--priority`, `--limit`, `--apply` | **Yes by default** |
| `python -m app.cli vendor-master` | `--file`, `--batch`, `--apply`, `--allow-renames` | **Yes by default** |

Run through the worker image (`docker compose run --rm worker python -m
app.cli ...`, `docs/runbook.md:197-199`) — `playbooks/*.json` ships only in
that image (`Dockerfile:36-50`).

### Revision history and revert

`manufacturer_playbook_revision` is `SELECT, INSERT`-granted only —
`dentalia_api` is refused both `UPDATE` and `DELETE`
(`migrations/050_playbook_body.sql`, verified against a throwaway database at
migration-authoring time). Every save inserts a new revision **before**
updating `manufacturer.body` in the same transaction, so a body is never live
without the row that explains it (`web/registry.py:690-697`). **Revert does
not rewrite history — it writes the old body forward as a new revision**
(`revert_playbook`, `web/registry.py:996-1013`, itself calling
`save_playbook_body` with `note=f"revert to revision {to_rev}"`). The
mistake and its undo both stay in the list.

### What happens after authoring

DISCOVER's `playbook` rung is the direct consumer
(`app/handlers/discover.py:499-511`): it is tried at whatever position
`source_priority` (or the config default ladder) puts it, and only fires
`fetch.url` for kind `direct` sources. EXTRACT resolves the steering playbook
independently per document — authoritatively when the group already carries
a `canonical_manufacturer`, otherwise from T0's own text match
(`app/handlers/extract.py:322-333`) — and records `playbook_slug`/
`playbook_rev` on `extraction_attempt` whenever a playbook steered any part
of the read, T0 lexicons included, not only a hint block
(`app/handlers/extract.py:115-136`).

### T0 parse templates

`match.anchors`, `ref_strategy`, `ref_strategy_config` are **not** fields on
the `Playbook` dataclass — they are read by a second, parallel loader,
`app/extract/t0_layout.py:39-83` (`load_templates`), straight off the same
raw dict `app.playbooks.load_raw` returns, so they follow the same
files-vs-rows rule without a second store. They stay data rather than code
because the extractor is engine-agnostic by design
(`docs/dentalia-mdr-pipeline-ground-truth.md` §7, `CLAUDE.md` Runtime &
conventions: "T0 templates are engine-agnostic data ... never
PyMuPDF-specific code") — a template is anchor text plus a named strategy,
matched on page-1 text (`t0_layout.py:86-93`), not a code path per
manufacturer. A playbook with no `match`/`ref_strategy` key is a complete,
valid file; it is silently skipped by the template loader, not an error
(`t0_layout.py:62-66`).

## Limits

- **Tier C has no live edit path after a playbook's first import.**
  `_write_body` (`app/manufacturer_seed.py:206-250`) only writes a body when
  the existing one is `NULL` or **byte-identical** to what is being imported
  — any divergence, including a normal UI edit, is reported as a conflict and
  the import refuses to touch it
  (`tests/test_manufacturer_seed.py:430-452`,
  `test_a_body_edited_since_the_seed_is_reported_never_reverted`). So a Tier
  C field (`ref_normalize`, `companion`, `coverage_map`, `exclude`,
  `cert_number_pattern`, `ref_pattern`, the T0 parse keys) must be authored
  into the JSON file **before** the playbook's first `manufacturers seed
  --apply`, or added later by hand with a direct SQL write against
  `manufacturer.body` and `manufacturer_playbook_revision` — nothing in this
  CLI or UI supports that second path.
- **`doc_sources` kind `portal` is never fetched.** It is a link rendered for
  a human to click; only kind `direct` is a live outbound request
  (`app/playbooks.py:927-931`, enforced at the fetch site by `_direct_urls`,
  `app/handlers/discover.py:281-293`, which excludes `portal` explicitly — a
  portal is a listing page, and fetching one would archive HTML no extractor
  can read). The crawl recipe that turns a library page into documents is
  built since 2026-09-03 (design in
  `docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md`): the
  `crawl` playbook key, `app/crawl.py`, DISCOVER's `_crawl_playbook` rung
  (`app/handlers/discover.py:458`), and `app/robots.py` consulted first at
  `discover.py:491`; `playbooks/edenta.json` authors one, and the probe screen
  proves a recipe before it is saved. A `portal` source is still never
  fetched — a crawl recipe is how it becomes documents. A library page with
  `discovery.crawl_rank_floor` (5) or more surviving links is a collection:
  the T1 ranker judges each link's type and the article numbers it names from
  the link text, and the budget is spent in that order — a link naming one of
  the group's own article numbers first, then DoC before EC before ISO before
  IFU, then confidence (ruled 2026-09-04). A ranking, never a filter: under
  `max_links` it only sets the fetch order. Above `discovery.crawl_rank_max`
  (600) the recipe is too broad to pay for and is capped in page order —
  narrow the pattern. The predicted type steers fetching only; the document's
  real type still comes from its own content at extraction. A library is judged
  once per version (`crawl_link_rank`, keyed on the harvested links) and every
  later group crawling the same page reads the answer back; only the order is
  recomputed per group.
- **`manufacturer_bc_code` and `manufacturer_playbook_revision` grant no
  DELETE to `dentalia_api`.** Un-claiming a BC code, and un-writing a
  revision, both stay CLI/SQL work — `claim_bc_code` is add-only by design
  (`web/registry.py:923-928`).
- **A rename does not retro-fix already-grouped items or re-point
  `manufacturer_alias`.** `dentalia_api` is SELECT-only on `item_group` and
  `manufacturer_alias` (invariant 1) — `rename_manufacturer`
  (`web/registry.py:711-810`) writes only the identity half and names the
  `playbooks sync` / `regroup` commands a human still has to run.
- **`playbook.reonboard` serves the UI probe only.** Since 2026-09-04
  `app/handlers/playbook_probe.py` runs a job that carries
  `index_url`; the failure-monitor path, which the scheduler would enqueue
  without one (`app/scheduler.py:342-355`), raises and dead-letters by design
  (`docs/dev/limits.md`). That is the *re*-onboarding path after a failure,
  not the initial-onboarding path this document covers, and its producer sits
  behind `SCHEDULER_FAILURE_REONBOARD_ENABLED`, default off.
- **`WEB_PLAYBOOKS_DIR` must never be set in production** — see Rules above;
  it is a test/dev seam, not a deployment option.

## Gotchas

- **`manufacturers seed` and `playbooks validate` read files even when
  everything else reads rows.** Both pass an explicit `dir_path` for exactly
  that reason — a bare `load_playbooks()` after `set_source` runs would
  return the database's own output and the import would silently read what
  it just wrote (`app/manufacturer_seed.py:262-268`).
- **Run `playbooks sync` *before* the next ingest, not after.** Re-pointing
  `manufacturer_alias` does not retro-fix `item_group` rows already resolved
  under the old value (`item_group.canonical_manufacturer` is effectively
  write-once — RESOLVE's ladder short-circuits on `_existing_link`); a
  non-zero `orphaned_groups` count is the signal, and `regroup` is the fix.
- **A BC code must already be in `vendor_master` before any playbook can
  claim it.** The FK in `migrations/049_manufacturer_entity.sql` refuses an
  invented code outright — `claimable_codes`
  (`web/registry.py:890-917`) only ever offers codes that already exist
  there.
- **`extract_hints` is the only playbook value an LLM ever sees.** Every
  other key drives deterministic code (`app/playbooks.py:181-183`); this is
  why it alone carries a key allowlist and a length cap
  (`HINT_MAX_CHARS`/`HINT_MAX_TOTAL`, 300/1500,
  `app/playbooks.py:62-63`) anchored on `_HINT_OVERRIDE`'s 263-character
  safety preamble (`app/handlers/extract.py:43-48`).
- **The seed's `rev` and the UI's `rev` mean different things at the start.**
  A file-imported playbook keeps whatever `rev` it was hand-bumped to; a
  UI-started playbook always begins at revision 0 with a synthetic note
  (`start_playbook`, `web/registry.py:881-886`). Both resolve through
  `manufacturer_playbook_revision`, so neither is a special case downstream —
  but do not read the number itself as an edit count.
- **`playbooks/{slug}.json` still exists in git for every delivered
  playbook (38 as of this writing) and is not dead.** It is the authoring
  record for anything imported before 2026-08-26 and remains how a brand-new
  Tier C field is seeded (see Limits) — it is retired as a *runtime* source,
  not as a file.

## Tests

| File | Pins |
|---|---|
| `tests/test_playbooks.py` | `_parse` validation rules, `validate()`'s conflict/collision checks, `for_manufacturer` |
| `tests/test_playbooks_sync.py` | `sync_aliases` — insert/update/unchanged, `orphaned_groups` |
| `tests/test_playbooks_db_source.py` | The two-store loader — `set_source`, epoch cache invalidation, explicit-`dir_path` override |
| `tests/test_migration_playbook_body.py` | Migration 050's schema and grants (`SELECT, INSERT` only on the revision table) |
| `tests/test_manufacturer_seed.py` | `manufacturers seed` — entity union-find, idempotency, every conflict path including the body-diverged-since-import case |
| `tests/test_playbook_start.py` | `start_playbook` — slug validation, already-has-a-playbook, slug-taken |
| `tests/test_playbook_tier_a.py` | `tier_a_from_form` — additive folding, Tier B/C keys survive untouched |
| `tests/test_playbook_tier_b.py` | `tier_b_from_form`, `claim_bc_code`, `RobotsRefused` at the save route |
| `tests/test_playbook_save.py` | `save_playbook_body` — note requirement, optimistic lock, `StaleForm` |
| `tests/test_playbook_guards.py` | `BrandCollision` end to end (save, rename, seed) |
| `tests/test_playbook_rename.py` | `rename_manufacturer` — two-step confirm, blast-radius report, guards |
| `tests/test_t0_layout.py` | `match.anchors`/`ref_strategy` template loading and matching |
| `tests/test_refused_host.py` | `refused_host` table and `is_refused` |
