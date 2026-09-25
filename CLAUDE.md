# CLAUDE.md — Dentalia Compliance Pipeline

Compliance document registry for Dentalia's medical-device catalogue (~16k items mirrored today, growing to roughly 100k). **One Business Central, one article numbering, one pipeline** — there is no separate Zagreb system (Denis, 2026-08-19, closing PHASES.md G17/G11); the Zagreb operation adds items, not a second integration. **The code was collapsed to match on 2026-08-26**: no catalogue picker on any form, no `code_source`/`catalogue` parameter to thread, `BcCode` is a bare code, and the KPI panels report one row rather than a per-catalogue list. The five columns holding the tag are kept and single-valued — do not reintroduce a choice over them. Discovers, fetches, extracts, validates, and stores MDR/MDD documents (DoC, EC cert, IFU, ISO) with per-value evidence. Deterministic-by-default, AI-on-exception. Steady-state cost target €10–40/month.

**End state (everything serves these two outputs):**
1. Database linking BC `item_ref` → N evidenced compliance documents (typed, dated, supersession-chained).
2. Deduplicated, hash-addressed file archive under our control (`archive_url`).

## Document map & precedence

Read before writing any code touching the relevant area. On conflict: **contract PRD wins on contracts, ground truth wins on intent.**

| File | Owns | Status |
|---|---|---|
| `docs/dentalia-pipeline-contract-prd-v3.md` | **NORMATIVE**: stages, job types, payloads, data model, drift guards | v3, current |
| `docs/dentalia-schema-sketch.md` | Postgres schema + tag→table access matrix | v2, synced to PRD v3 |
| `docs/dentalia-job-type-handbook.md` | Per-tag handler pseudo-code, payload examples, worked chain | v2, synced to PRD v3 |
| `docs/dentalia-v3-v4-system-design.md` | Queue mechanics rationale, tier ladder, failure containment, runtime | v1 |
| `docs/dentalia-mdr-pipeline-ground-truth.md` | Decision record, rejected options, cost baseline, phasing, **§7 domain traps** (which dates each document type carries, REF/matching pitfalls, fetch reality) — read §7 before touching extraction or validation | v1 |
| `docs/dentalia-workflow-structured-v2.md` | Workflow decisions, email agent, UDI/EUDAMED research | v2 |
| `docs/dentalia-etl-stage-alternatives.md` | Tooling choices + upgrade paths per stage | v1 |
| `PHASES.md` | **Status board**: progress per session, open scope in three buckets (blockers · ours · waiting on someone), headline numbers, gap register | live, v4 2026-09-04 |
| `docs/decisions.md` | **Every ruling** the rest depends on, 99 rows. Check before proposing anything that looks already-decided | live |
| `docs/state/<date>.md` | Dated database readings behind PHASES.md § 3. Never edited after its date; a newer reading gets a new file | dated |
| `docs/build-log.md` | Archive: done-session retrospectives, out-of-band slices, audits, closed gaps. Read for *why*; never plan from it | archive |
| `docs/README.md` | Docs index + operational docs (architecture, runbook, troubleshooting, code-map) | live |

Older PRD versions (`dentalia-master-prd.md`, `dentalia-pipeline-contract-prd.md` v2) are superseded — do not read them for contracts.

## Architecture in one paragraph

Modular monolith. One Postgres holds queue + registry + fetch ledger + audit log — transactional claim/write/audit in one tx. Competing identical stateless workers poll the `job` table (`SELECT … FOR UPDATE SKIP LOCKED`); each handler finishes by enqueueing the next stage's job(s). The topology **is** the enqueue graph:

```
ingest.run → resolve.group → discover.group → fetch.url → extract.doc → validate.doc → gate.candidate
                                   ↘ email.request        ↗ backfill.scan, email.poll enter here
                                   ↘ manual queue          ↖ fetch.url hash-dedupe re-enters at validate.doc
```

The FastAPI process (read API + HTMX review UI) is a **job producer only** — it has zero write grants on `document` / `item_document` / `evidence`. Human decisions enqueue `gate.apply`; only GATE handlers write the registry.

## Invariants — violating any of these is a bug regardless of output correctness

1. Only `gate.candidate` / `gate.apply` handlers write `document`, `item_document`, `evidence`.
2. Every production value carries complete evidence `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)`. GATE rejects candidates without it. `page` is required for T1/T2 evidence only — T0 filename-derived and T3 human evidence are legitimately pageless ([evidence-page] ruling, 2026-07-31).
3. REF gate: auto-write requires `(canonical_manufacturer, catalogue article number)` pair overlap or Basic UDI-DI match. The article number is `mfr_ref` (supplier's, basis `ref-list`) **or** `item_ref` (Dentalia's own, basis `ref-item`) — client ruling C12, restated 2026-08-13: the primary item number is the key and is what is printed on the physical article. Both are production-capable only because the manufacturer is established *before* any number is compared. Links with `match_basis ∈ {name-family, fetch-context, ref-catalogue}` are **always** capped at `staged` — link-level, structurally (migrations 005, 021).
4. Never downgrade — means never made current, not never stored. An older candidate (older candidate flags `older-than-current`, blocking, forces `manual` — unchanged). Task 5 adds a narrow, guarded exception: when both dates are present, VALIDATE ALSO emits `superseded_by_doc_id`/`auto-superseded`, and GATE auto-files straight into the superseded chain (status `superseded`, `superseded_by` set, still archived, still evidenced, no human review) **only if** no other blocking flag survives (e.g. `no-item-identifier` still forces `manual`) AND `validity_from`'s own calibrated confidence clears `cfg.gate.high`. Any guard failure falls back to `older-than-current` forcing `manual`, same as before task 5 — never `production`, never a silent downgrade. Null dates → `downgrade-uncomparable`, never compared, caps at staged. Never delete (supersession append-only, 10-y retention).
5. Supersession only within identical `(coverage subject, type, regulation)`. MDR never supersedes MDD. Persisted at production transition — `gate.candidate` (machine) if the candidate lands directly on production, `gate.apply` `approve` (human) if it was staged first; a staged candidate never supersedes anything.
6. Unchanged content never re-fetched or re-parsed (fetch ledger: ETag / hash / recency window).
7. Job types are a closed enum. New tag = PRD change + migration first, never a string.
8. Dedupe uniqueness scoped to active jobs only (partial unique index, `status IN ('pending','running','failed')`). Terminal jobs never block re-enqueue.
9. Job payloads immutable after enqueue. External long-running state (Batch API ids) lives in side tables (`batch_ref`), never in payload mutation.
10. Audit entries self-contained (`job_snapshot` jsonb, `via_job` soft ref). Deleting every `job` row must leave the audit trail fully interpretable.
11. Nothing downstream of INGEST/FETCH may know which adapter implementation is live.
12. All LLM use: candidate ranking, document parsing, and **inbound email-body summarisation** (`email.poll`, S2.4; cheap tier only via `models.email_summary`; the summary is UI context — never evidence, never a production value, never a GATE or VALIDATE input, and never re-read by another stage). Widened from "ranking + parsing only" on 2026-08-20 so `/emails` can carry what a message asked for without persisting supplier correspondence verbatim — proposed and **ratified by Denis the same day**. LLMs never fetch. Fetching = httpx, Playwright fallback tier.

## Runtime & conventions

- Python 3.12+, sync (no asyncio — workload is polite-rate fetch + batched LLM + cheap logic; async buys nothing, costs debuggability).
- psycopg 3, raw SQL, migrations as numbered `.sql` files in `migrations/` applied by `app/db.py`. No ORM.
- PDF: PyMuPDF (text+coords, rasterize), pdfplumber (tables/REF lists). T0 templates are engine-agnostic data (anchor text + relative regions), never PyMuPDF-specific code.
- Fuzzy: pg_trgm (candidate generation, in-DB) → rapidfuzz (scoring). splink is the upgrade path, not now.
- Queue: hand-rolled, ~150 lines in `app/queue.py`. pgqueuer is the documented escape hatch — do not add it preemptively.
- HTTP: httpx. Browser fallback: playwright-python, only for domains flagged `needs_playwright`.
- UI: FastAPI + Jinja + HTMX. No Node, no build step. Pages: status + KPI board, ingest form, staging review (incl. grouping suggestions), manual queue, dead jobs; plus a read-only JSON API (`/api/*`, production-visibility only; `?include_superseded` additionally serves the supersession chain, document half only — migration 070).
- Models via config: `models.t1` / `models.t2` / `models.rank` (Haiku 4.5 / Sonnet 5 / Haiku 4.5 baseline). Batch API for all sweep work. Any tier swap requires the Phase 0 diff protocol re-run.
- Docker Compose from day one. Two images: worker from `Dockerfile` (`FROM mcr.microsoft.com/playwright/python`), UI from `Dockerfile.web` (slim). Services: postgres, migrate, worker (replicas), web, caddy (S1.6, HTTP Basic reverse proxy — the sole ingress to `web`, gap G3 v0), test. **There is no `scheduler` service.** The standalone loop was deleted 2026-09-02; the crons run ON THE QUEUE as ten perpetual, self-deferring `scheduler.tick` jobs claimed by `worker` (migrations 054/055, then 064 and 065, `app/handlers/scheduler_tick.py`), re-armed on every worker start by `arm_crons()`.
- Tests: pytest, against a real Postgres (compose service or testcontainers). Queue semantics, gate dispositions, and validate rules must have table-driven tests. No mocking Postgres.

## Repo layout (target)

```
app/
  workers/runner.py    # claim → dispatch → finish loop (+ batch self-defer)
  handlers/            # one module per queue tag (ingest.py, resolve.py, fetch.py, ...)
  scheduler.py         # cron bodies; called by the scheduler.tick handler, not by a loop (S1.5)
  cli.py               # enqueue / inspect from shell
  queue.py             # claim/finish/fail/defer, dedupe, backoff, domain lease
  db.py                # psycopg 3, migration runner
  config.py            # all keys from PRD §11; env + toml
  adapters/            # SourceAdapter, StorageAdapter, SearchAdapter, Fetcher
  extract/             # tiers.py, llm.py, t0_templates.py, t0_layout.py (templates now read from playbooks/*.json), batching.py, economics.py, pdf.py, prompts/
  urls.py              # shared URL normalization (fetch dedupe key — DISCOVER + FETCH)
  templates/           # placeholder — UI templates live in web/templates/
web/                   # producer UI + read API (status/KPI board, ingest, staging, manual, dead jobs, /api/*) — kept standalone, G16 closed 2026-07-23
migrations/
playbooks/             # per-manufacturer JSON: identity (bc_codes, aliases, domains), doc_sources, T0 parse templates — live S1.7; crawl-recipe/onboarding authoring is still Phase 2 (S2.1)
tools/                 # dev-time analysis (corpus/catalogue/crossref/claims); NOT the pipeline, excluded from the wheel, read-only
tests/
docs/                  # the design docs above
docker-compose.yml
Dockerfile             # worker image, runs the crons too (Playwright base)
Dockerfile.web         # web UI image (slim)
```

## Working rules for agent sessions

- Check `PHASES.md` § 2 for open scope and § 4 for `[GAP]` markers before starting, and `docs/decisions.md` before proposing anything that sounds already-decided. Fill gaps with a proposal, mark it `[FILLED — needs review]` in PHASES.md, don't silently decide.
- A stage may only emit job types listed in its PRD Emits row. If a task seems to need a new job type, stop and flag — that's a PRD change.
- Payload fields additive-only once Phase 1 starts; consumers tolerate unknown fields, never missing ones. Breaking change strategy: drain or delete-and-re-emit, never payload versioning (queue is regenerable state, registry is truth).
- Any job whose loss loses work is a design bug — recheck against the registry-derives-queue principle.
- When editing design docs, make targeted corrections; never rewrite unaffected sections.
- Keep the operational docs (`docs/README.md` index: architecture, runbook, troubleshooting, code-map), the developer set (`docs/dev/`) and the client guide (`docs/guide/`) current: if your change invalidates something they state (a command, a path, a route, a failure mode) or adds something they should cover (a new feature, flow, handler, service, or config key), update the doc in the same session — never ship with stale docs.
  **Self-document on change.** What you touched decides what you update, in the
  same session:

  | You changed | Update | Then |
  |---|---|---|
  | A screen, a button, a message, a route | `docs/guide/pages/<screen>.md` **and** its `.sl.md` | `python3 scripts/build-guide.py`, commit the bundles too |
  | A job type, handler, adapter, or the queue | `docs/dev/handlers.md`, and `docs/dev/01-lifecycle.md` if the flow moved | — |
  | A config key | `docs/dev/config-reference.md`, incl. blast radius | — |
  | Something deliberately not built, or stubbed | `docs/dev/limits.md` — and delete the row when it becomes untrue | — |
  | A migration, table, or grant | `docs/dev/changing-things.md` if the checklist changed | — |
  | Any of the above, if it invalidates them | `architecture.md`, `runbook.md`, `troubleshooting.md`, `code-map.md`, `vocabulary.md` | — |

  A UI change is not done until the guide matches it in **both** languages and
  the committed HTML agrees with the markdown. `tests/test_docs_sets.py` guards
  what a test can see — job tags, document statuses, VALIDATE flags, EN/SL
  pairing, links. It cannot see wording, so the table above is on you.
  Build commands: [`docs/runbook.md`](docs/runbook.md) § Client guide.
- Skipped rows, missed fields, missing `mfr_ref` → counted and reported on job results, never silent.
- **Every test run goes through `./scripts/test.sh`.** Never a bare `pytest` —
  a PreToolUse hook (`.claude/settings.json`) blocks it, and the wrapper is the
  only path that gets the parallelism and the cross-worktree semaphore right.
  This decides HOW to run; the path-based selection rule below still decides
  WHAT to run, unchanged.

  ```
  ./scripts/test.sh                          # whole suite      -n 4 --dist loadfile
  ./scripts/test.sh tests/test_queue.py      # paths            -n 2 --dist loadfile
  ./scripts/test.sh tests/test_x.py::test_y  # one id           -n 0
  ./scripts/test.sh --heavy                  # machine is busy  -n 2
  ```

  It execs into the long-lived `test` container, so the stack must be up first
  (`docker compose up -d` then `docker compose --profile test up -d test`); it
  prints those commands and exits 1 rather than starting anything itself. It
  never `--build`s — the repo is bind-mounted, so rebuild by hand only when the
  Dockerfile or the dependency set changes. A flock semaphore
  (`/tmp/pytest-slots`, 4 slots) caps concurrent runs across all worktrees.
- **Why `--dist loadfile`.** The reason is wall-clock, not correctness. Measured
  back-to-back 2026-08-26 on a loaded machine, all 2301 green: **loadfile 154s ·
  load 191s · worksteal 191s** · ~490s single-process. `loadfile` wins because
  it keeps a file's tests on one worker and so reuses that worker's warm
  fixtures; xdist's docs favour `worksteal` where test durations differ a lot,
  and it was tried here and did not win. Numbers move with what else is running
  — the machine has 12 cores and several sessions share it; `--heavy` when they
  are busy.
- `test_web.py` is **not** order-dependent, verified under reverse order, two
  shuffled seeds, `--dist load` and `--dist worksteal`. That is a property of
  the current tree, not a law: it holds only while every seeding fixture takes
  `conn` (whose teardown runs conftest's `_RESET_SQL`) and `_RESET_TABLES` names
  every table anything writes. **Both halves have failed before** — on
  2026-08-26 `data_anomaly` and `service_heartbeat` were each missing from that
  list and each produced real order-dependent failures, which is
  what the older wording here was describing. `test_queue.py::
  test_reset_clears_every_table_the_old_cascade_reached` is the guard, and it
  only reaches tables linked by FK to its seed set: a new leaf table needs a
  `seeds` entry too, or it slips past.
- Don't run the full suite in the implementation loop even so — it is ~4100
  tests, and several sessions share this machine. Run the tests that cover what
  you touched, and the full suite once before committing.
  Selection rule, in order:
  - `tests/conftest.py`, `migrations/`, `playbooks/`, `app/config.py`,
    `app/db.py`, `app/queue.py`, `app/urls.py`, `app/playbooks.py` →
    **full suite**. These reach most of the tree, and the two non-Python ones
    (`migrations/*.sql`, `playbooks/*.json`) are read at runtime, so no
    import-graph tool can see them — a coverage-based selector would report a
    false green here.
  - otherwise → `tests/test_<module>*.py` for each module touched, plus
    `tests/test_web.py` if anything under `web/` changed.
  - docs-only change (`docs/`, `PHASES.md`, `tasks/`, `README.md`) → no tests.
  - A subset run is never evidence the change is green; say which subset ran.
- Don't gold-plate: no speculative abstraction. The UI covers exception
  handling (staging, manual, dead jobs), registry visibility (items,
  documents, expiry, in-flight, data quality), and the status board.
  Anything beyond those needs a reason.
- prefer short concise answers, but don't sacrifice clarity. If a question is ambiguous, ask for clarification rather than guessing.