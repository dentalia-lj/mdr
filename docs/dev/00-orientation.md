# Orientation

**Status:** live, written 2026-08-31. Clone to first shipped change, narrative
rather than the seven-heading template — see [README.md](README.md) for what
this folder owns and does not.

## What this is

Dentalia sells medical devices from a catalogue of ~16k items (growing to
~100k) sourced from ~30 manufacturers, and for each one it must be able to
produce the compliance paperwork — Declaration of Conformity, EC certificate,
IFU, ISO cert — on demand, with evidence for every value it asserts. This repo
is the pipeline that finds those documents, reads them, decides whether they
cover a given catalogue item, and serves the answer to a support rep, a
webshop, or a compliance reviewer. One Postgres database is queue, registry,
fetch ledger and audit log at once; competing stateless workers claim jobs off
it; only two handlers (`gate.candidate`, `gate.apply`) ever write the
document registry (`CLAUDE.md` invariant 1). Deterministic rules decide most
outcomes; an LLM is called only where a rule cannot read a PDF for you.

## Prerequisites and startup

Full detail lives in [runbook.md](../runbook.md) — this is the order and the
reason for each step, not the commands themselves.

1. **Docker + Compose.** The stack is compose-first; nothing is meant to run
   on the bare host. On WSL2 the Postgres data dir must sit on a WSL-native
   ext4 path — a `/mnt/...` path breaks Postgres over the 9p filesystem
   (`runbook.md` Prerequisites).
2. **`.env`, copied from `.env.example`.** Almost everything has a dev
   default, but `ANTHROPIC_API_KEY` does not — compose fails fast
   (`${ANTHROPIC_API_KEY:?}`) rather than starting a worker that cannot
   extract, because only two `doc_class` values (`msds`, `business-doc`)
   finish without an LLM call.
3. **`docker compose up -d`.** Brings up `postgres` (healthcheck-gated) →
   `migrate` (one-shot, applies pending `migrations/*.sql`, exits 0) →
   `worker`, `web`, `caddy` (the crons run inside `worker` as queue jobs;
   there is no `scheduler` service since 2026-09-02). This is the only place
   migrations run in
   the normal flow — see below for what "pending" means when two branches
   both add one.
4. **Set web credentials** (`docker compose run --rm caddy caddy
   hash-password ...` into `.env`) before `http://127.0.0.1:8000` behind
   Caddy answers anything but `/healthz`.
5. **Run the tests** (below) before you touch anything, so a red suite you
   didn't cause doesn't get blamed on your first commit.

## Migrations: how they apply, and the ordering rule that bites

`app/db.py:47-49` (`_migration_files`) sorts `migrations/*.sql` by **filename,
ascending, lexicographic** — not by the numeric prefix a human reads off it.
`run_migrations` (`app/db.py:98-148`) applies each pending file inside its own
transaction, records it in `schema_migrations`, and is idempotent — files
already applied are skipped.

The tree currently holds **two duplicate-numbered pairs**:
`014_evidence_rev.sql` / `014_upload.sql` and `041_eudamed_job_types.sql` /
`041_vendor_import.sql` (`migrations/` listing). Lexicographic order breaks
the tie by the letters after the number (`evidence_rev` sorts before
`upload`), which is almost certainly not the order either PR intended. Two
sessions landing files under the same next-available number is treated as
routine here — "we fix it when merging" (Denis, 2026-08-27, quoted in
`runbook.md`) — not a reason to block on renumbering solo.

`migrate` (the CLI, and the compose `migrate` service) **warns, not refuses**,
when a pending file sorts below the highest one this database has already
applied (`app/db.py:126-135`, the `_out_of_order` check) — because that
condition is the routine cost above, fixed at merge time. `run_migrations(...,
strict=True)` turns the same condition into `OutOfOrderMigration` and refuses
before applying anything; only `tests/conftest.py` uses it, because a
freshly-built test database has no legitimate excuse to be out of order. See
`runbook.md`'s "Migration ordering: what the `migrate` warning means" for the
SQL to compare applied-vs-disk order on a live database, and
[changing-things.md § Adding a migration](changing-things.md#adding-a-migration)
for the full checklist when you add one (grants, `_RESET_TABLES`, the
FK-closure test guard) — all of that is written once there, not repeated here.

## Running tests

**Never a bare `pytest`.** A `PreToolUse` hook blocks it —
`.claude/settings.json`, backed by `.claude/hooks/block-bare-pytest.sh` — and
the only way through is `./scripts/test.sh`, which also holds the
parallelism flags and a cross-worktree semaphore a bare invocation would
skip. Mechanics — the flock semaphore, why `--dist loadfile`, per-worktree
container paths — are owned by [test-infra.md](../test-infra.md); this is
just the selection rule, reproduced from `CLAUDE.md` so it can't drift
silently out of sync with what actually gates a commit:

| You touched | Run |
|---|---|
| `tests/conftest.py`, `migrations/`, `playbooks/`, `app/config.py`, `app/db.py`, `app/queue.py`, `app/urls.py`, `app/playbooks.py` | **Full suite.** These reach most of the tree, and the two non-Python ones (`.sql`, `.json`) are read at runtime — no import-graph selector can see them, so anything smarter than this list reports a false green here |
| Anything else | `tests/test_<module>*.py` for each module touched, plus `tests/test_web.py` if anything under `web/` changed |
| `docs/`, `PHASES.md`, `tasks/`, `README.md` only | No tests |

A subset run is never evidence the change is green — say which subset ran.
Run the full suite once before committing regardless of which rule fired for
your edits along the way.

## What to read before touching each area

The full precedence table is in `CLAUDE.md`'s "Document map & precedence" —
this is the short version, keyed by what you're about to do:

| About to touch | Read first |
|---|---|
| A job's stages, payload shape, or what a stage may emit | `docs/dentalia-pipeline-contract-prd-v3.md` (**normative**) |
| A table, column, or the tag→table access matrix | `docs/dentalia-schema-sketch.md` |
| A specific handler's pseudo-code or a worked payload example | `docs/dentalia-job-type-handbook.md` |
| Extraction tiers or a VALIDATE rule | `docs/dentalia-mdr-pipeline-ground-truth.md` §7 ("domain traps") **before anything else** — it lists which dates each doc type carries and where REF matching goes wrong |
| Why a decision was made the way it was, or what was rejected | `docs/dentalia-mdr-pipeline-ground-truth.md` (whole doc) |
| Current session scope, an open `[GAP]`, or the decision log | `PHASES.md` |
| A handler you haven't read the code for yet | [handlers.md](handlers.md) — the as-built index, PRD wins on any disagreement |

On any conflict between a doc here and the code: **contract PRD wins on
contracts, ground truth wins on intent** (`CLAUDE.md`).

## Invariants a newcomer will trip over first

The full list of twelve is in `CLAUDE.md`. These are the ones most likely to
produce a confusing test failure or review comment on a first change:

| Invariant | What trips people up | Where it's enforced |
|---|---|---|
| **1. Only GATE writes the registry.** | A route or handler that looks like it should write `document`/`item_document`/`evidence` directly (a repair script, a web form) cannot get write grants for them | Structural: the web role has no write grant on those tables; `web/access.py` is default-deny. Grepped and closed with two named exceptions in [handlers.md](handlers.md#invariant-1-and-its-two-ruled-exceptions) |
| **3. REF gate is manufacturer-scoped, always.** | A bare article-number match feels sufficient; it is never enough on its own | `app/handlers/validate.py:15-32` (rule 1 docstring) — the comparand is always the pair `(canonical_manufacturer, mfr_ref)` |
| **3b. `name-family`/`fetch-context`/`ref-catalogue` links cap at `staged`, structurally.** | Approving the document does not lift these links to production — that surprises people | `TRUSTED_BASES`, `app/handlers/gate.py:47`, backed by a DB CHECK (migrations 005, 021) |
| **4. Never downgrade.** | An older candidate looks like it should just be dropped; instead it forces `manual` review by default | `BLOCKING_FLAGS` includes `older-than-current`, `app/handlers/gate.py:65-66`; the narrow auto-file exception is guarded four ways in `handle_gate_candidate`, `app/handlers/gate.py:945-979` |
| **7. Job types are a closed enum.** | Adding a tag as a plain string compiles and looks like it works, then a job of that type dead-letters into `noop` | `migrations/001_queue.sql:11-25`; the checklist is [changing-things.md § Adding a job type](changing-things.md#adding-a-job-type) |
| **8. Dedupe is active-jobs-only.** | A completed job's dedupe key does *not* block re-enqueue — expecting permanent dedupe is the wrong mental model | `ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')`, `app/queue.py:98-109` |
| **9. Payloads are immutable after enqueue.** | Reaching for a payload mutation to carry new state (e.g. a Batch API id) is the wrong instinct — it needs a side table | `batch_ref` is the pattern; see `app/handlers/extract.py`'s module docstring |

## Your first change, worked through

A real, currently-open item: `tasks/followups.md:2`,
`[seed-orphan-report-masked-by-042]`. `manufacturer_seed._orphan_groups`
(`app/manufacturer_seed.py:195-203`) `LEFT JOIN`s `item_group` to
`manufacturer` on `canonical_name` to report groups no manufacturer entity
covers. `migrations/049_manufacturer_entity.sql:111-119` added a separate
`manufacturer_name` table (name variants per `manufacturer_id`) after that
join was written, and `migrations/042_eudamed_manufacturer.sql:17-19` seeds a
bare `manufacturer` row for every distinct alias — so the join now asks "does
*any* manufacturer row exist" instead of "does a *named* manufacturer cover
this", and measured on the live database it misses 7 of 11 truly-orphaned
groups (`tasks/followups.md:2`'s own numbers: 1 value / 3 groups against
`manufacturer`, 4 values / 11 groups against `manufacturer_name`).

Walking it through, without actually making the change:

- **Scope.** One file changes: `app/manufacturer_seed.py` — the join target
  moves from `manufacturer.canonical_name` to the `manufacturer_name` table.
  Nothing else calls `_orphan_groups`. That's one file touched, well under
  the CLAUDE.md rule ("more than 3 files → break it down first").
- **Tests to run.** `app/manufacturer_seed.py` isn't on the full-suite
  trigger list above, so the rule says `tests/test_manufacturer_seed.py`
  only, via `./scripts/test.sh tests/test_manufacturer_seed.py`. The
  followup itself specifies the missing case: seed a `manufacturer` row
  042-style with *no* `manufacturer_name` row behind it, and assert the
  group it covers still reports as orphaned — that's the exact case the
  current join hides, so a green suite without that test is not evidence
  the bug is fixed.
- **Docs to update.** None of the maintained docs assert the current (wrong)
  join behaviour, so there's nothing to correct there. What *does* need
  updating is `tasks/followups.md` itself: move the `- [ ]` entry to
  `## Done` with a dated note once it lands, per `CLAUDE.md`'s Task
  Management rule — and check whether closing it un-blocks
  `[orphaned-groups-3shape]` and `[blank-manufacturer-vat-placeholders]`,
  which both say they depend on this report's visibility.

That shape — one small file, a test that encodes the exact regression, a
followup entry closed rather than deleted — is the normal size of a change
here. A first change that touches a handler's queue-emitting behaviour is a
different, larger animal: read [01-lifecycle.md](01-lifecycle.md) first.
