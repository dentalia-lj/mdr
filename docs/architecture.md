# Architecture

What the system is and how the pieces fit. Contracts (job types, payloads, table shapes) are owned by [dentalia-pipeline-contract-prd-v3.md](dentalia-pipeline-contract-prd-v3.md); the 12 hard invariants are owned by [CLAUDE.md](../CLAUDE.md). This page is the orientation layer, not the normative source.

## Purpose

Compliance document registry for Dentalia's medical-device catalogue (Ljubljana ~16k items, Zagreb ~100k). The pipeline discovers, fetches, extracts, validates, and stores MDR/MDD documents (DoC, EC cert, IFU, ISO) with per-value evidence. Everything serves two outputs:

1. A database linking BC `item_ref` -> N evidenced compliance documents (typed, dated, supersession-chained).
2. A deduplicated, hash-addressed file archive under our control (`archive_url`).

Deterministic-by-default, AI-on-exception. Steady-state cost target EUR 10-40/month.

## Shape

Modular monolith. One Postgres holds the queue, the registry, the fetch ledger, and the audit log, so a handler can claim a job, write results, and audit the write in a single transaction. There is no orchestrator: competing identical stateless workers poll the `job` table (`SELECT ... FOR UPDATE SKIP LOCKED`), and each handler finishes by enqueueing the next stage's job(s). The pipeline topology **is** the enqueue graph:

```
ingest.run → resolve.group → discover.group → fetch.url → extract.doc → validate.doc → gate.candidate
                                   ↘ email.request        ↗ backfill.scan, email.poll, upload.ingest enter here
                                   ↘ manual queue          ↖ fetch.url hash-dedupe re-enters at validate.doc
```

Two job types sit off that graph entirely, produced by a cron or by a person rather than by a predecessor stage, emitting nothing: `report.weekly` and — since 2026-09-07 — `bc.push`, which reads the registry and writes three compliance fields back into Business Central over OData (`dataitems`: a valid-declaration boolean, a valid-CE-certificate boolean, and the item's warehouse link). It writes `bc_push_log` and no registry table, so Invariant 1 is untouched, and it is gated off by `bc.write_enabled`. That closes the loop the read side opens: `ingest.run` mirrors BC's catalogue in, `bc.push` asserts our answer back out.

Job types are a closed enum (created in migration `001_queue.sql` and extended by `013`, `041`, `054` and `062` — read the enum, not 001); adding one is a PRD change plus a migration, never a string. Dedupe is scoped to active jobs only, so terminal jobs never block re-enqueue. The queue is regenerable state; the registry is truth. Any job whose loss would lose work is a design bug.

## Processes (compose services)

| Service | Image | Runs | Notes |
|---|---|---|---|
| `postgres` | postgres:16 | the one database | data bind-mounted to a WSL-native ext4 path |
| `migrate` | Dockerfile (`app` stage) | `python -m app.cli migrate`, one-shot | worker/web wait on it |
| `worker` | Dockerfile (`app` stage) | `python -m app.workers.runner` | scale with `--scale worker=N`; claim -> dispatch -> finish loop; writes fetched documents to the `archive_data` volume (`/archive`, S1.8 Task 1); **also runs the crons** — see below |
| `web` | Dockerfile.web (slim) | `python -m web.app` (uvicorn) | producer UI + JSON read API, see below. No published port — `caddy` is the sole ingress (S1.6, G3 v0); serves `archive_data` read-only via `GET /archive/{path}` |
| `caddy` | `caddy:2.8` (official image) | HTTP Basic reverse proxy in front of `web` | publishes `API_PORT` (default 8000) on loopback; see `Caddyfile` |
| `test` | Dockerfile (`test` stage) | `pytest` in-container | gated behind `--profile test` |

**There is no `scheduler` service.** The cron producer (S1.5) was a separate process until 2026-09-02; its cadence now runs on the queue as ten perpetual, self-deferring `scheduler.tick` jobs (migrations `054`/`055`, plus `064` and `065` for the two added since, handler `app/handlers/scheduler_tick.py`), claimed by `worker` like any other job. `app/scheduler.py` survives as a library holding the cron bodies. The point was containment: a cron that throws now retries with backoff and dead-letters visibly instead of being swallowed by a loop nobody was watching, and `run_after` is a poll interval only — `scheduler_run` remains the sole authority on whether a period has fired. The ledger and the weekly report are visible at `/scheduler`.

The worker image is `mcr.microsoft.com/playwright/python` so the Playwright fetch fallback tier needs no second image. `web` is a deliberate, user-directed deviation from "one image" (see the gap note in PHASES.md): a separate slim image with no PDF/Playwright deps.

## Write-path separation (who may touch the registry)

Only `gate.candidate` / `gate.apply` handlers write `document`, `item_document`, `evidence` (Invariant 1). The web process is a job producer only: it connects as the `dentalia_api` role (migrations `007`/`008`), which has zero write grants on registry tables. Human review decisions on the staging page do not write the registry directly; they enqueue `gate.apply` jobs that a worker executes. The registry-visibility pages (`/items`, `/documents`, `/expiry`, `/inflight`, `/data-quality`) are SELECT-only for the same reason — they answer "what's true right now", never change it. Every production value carries complete evidence `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)`; GATE rejects candidates without it.

## Auth (S1.6, gap G3 v0)

`caddy` terminates HTTP Basic auth (bcrypt, `caddy hash-password`) in front of `web` and is the only service publishing a host port for the UI — `web` itself is unreachable from the host, so `X-Forwarded-User` (set by Caddy after a successful auth check) is trustworthy. `web` reads it via `Web.require_authenticated_user` / `Web.trusted_user_header` (`app/config.py`): when true (compose sets it), `decided_by` on a `gate.apply` decision comes from that header, never a client-supplied form field, and a missing header is a 403 (the request bypassed the proxy). Local/host dev runs with `WEB_REQUIRE_AUTHENTICATED_USER=false` (default) and keeps the typed-in `decided_by` field. Two non-browser caller classes were added 2026-08-25 (`docs/superpowers/specs/2026-08-25-item-document-access-design.md`): the webshop backend authenticating with `X-API-Key` (`WEB_API_KEYS`), and a Business Central item-card hyperlink carrying a shared static key (`/item/{ref}?k=`, `WEB_BC_LINK_KEY` — BC builds links by string concatenation and cannot compute a signature). `basic_auth` cannot express "Basic **or** a key", so the `Caddyfile` excludes those two from the `@protected` matcher and **the app validates their credentials itself**: `web/access.py` classifies every request as `staff` / `service` / `bc` / `anon` and applies a default-deny route table as a sync FastAPI dependency. So the JSON read API (`GET /api/*`, production-visibility only -- plus, since 2026-09-16, the supersession chain behind `?include_superseded`, which widens the DOCUMENT status filter to production+superseded and moves the link filter not at all: `docs/specs/read-api.md` §2) is now reachable either behind the proxy as `staff` or with a service key, and `/archive/{path}` stays staff-only at both layers because it serves the archive by walkable path. There is deliberately **no public, unauthenticated tier** — the webshop fetches server-side and relays to its own logged-in clinics. Since 2026-09-15 the `staff` class has one axis inside it (office UI redesign spec § 2, D2/D3): Caddy's one `basic_auth` block takes a `<username> <bcrypt hash>` line per person, `import`ed from `./caddy/users/*.caddy` alongside the `.env` pair, so the audit trail names a person; and `WEB_OPERATOR_USERS` names which of those logins are operators. A non-operator loses the collapsed **Operator** menu group and is refused every write behind it with a 403 (`web/access.py::operator_guard`, a route dependency): the playbook save, code claim, revert, probe and crawl, the four onboarding writes, `/bc-push/apply`, the scheduler run and arm, the two `/dead` per-cause re-runs, and `POST /ingest`. `/import` apply and the office's own two failure buttons are never refused. The pages stay reachable by URL on purpose: this is a division of labour, not a second security boundary, and an EMPTY list makes every login an operator so a running system cannot lock its office out.

## Data model

Thirty-two migration files (numbered through `031_extraction_playbook_rev.sql`), applied in ascending filename order by `app/db.py` (tracked in `schema_migrations`). Two files share the `014` prefix: `014_evidence_rev.sql` sorts and applies before `014_upload.sql`, both independently numbered by different sessions:

| Migration | Owns |
|---|---|
| `001_queue.sql` | queue-tag registry + `job` table (claim, backoff, dead-letter, dedupe, domain lease) + `batch_ref` for long-running Batch API state |
| `002_ingest.sql` | ingest side: item mirror |
| `003_discovery_fetch.sql` | discovery and fetch ledger (ETag / hash / recency window) |
| `004_extraction.sql` | extraction side |
| `005_registry.sql` | the deliverable: `document`, `item_document`, `evidence`, supersession |
| `006_phase2_declared.sql` | Phase 2 tables (declared, not used in Phase 1) |
| `007_roles_grants.sql` | `dentalia_api` role, zero registry write grants |
| `008_api_role_dev_password.sql` | dev password for `dentalia_api` |
| `009_manual_task.sql` | manual-queue persistence (gap G13) |
| `010_extraction_cost.sql` | economics ledger: one row per LLM call, measured tokens + cost, `extraction_spend` view |
| `011_ingest_mirror_rev.sql` | ingest side: `mirror_rev` sequence for item-mirror diffing |
| `012_resolve.sql` | resolve side: pg_trgm name index on `item_mirror` (candidate generation) + `grouping_suggestion` staging table |
| `013_scheduler.sql` | `report.weekly` job type + `scheduler_run(name, period_key)` restart-safety ledger (PHASES.md GAP A/B) |
| `014_evidence_rev.sql` | `evidence.extract_rev`: a re-extraction's evidence is scoped to the revision that produced it, never a stale rev (gap `gate-evidence`) |
| `014_upload.sql` | `upload.ingest` job type + `upload_inbox` transient spool (web-document-upload slice) — `dentalia_api` gets INSERT + sequence USAGE only, worker keeps SELECT/DELETE |
| `015_supersession.sql` | `document.supersedes`: persists VALIDATE's computed supersession target at production transition (gap `gate-c6`) |
| `016_vendor_master.sql` | `vendor_master`: mirror of BC's manufacturer code -> name master (390 rows); read only by `playbooks sync` |
| `017_manufacturer_alias_source.sql` | `manufacturer_alias.source`: provenance tag (`self-seed` / `vendor-master` / `playbook`) for each alias row |
| `018_document_text.sql` | `document_text`: parsed PDF text per content hash, written by `extract.doc` before the tier ladder runs (`app/extract/text_store.py`) |
| `019_job_result.sql` | `job.result` jsonb: a handler's `Result` envelope (counts/samples/notes/anomalies), persisted by `queue.finish` in the same transaction as the status flip; `data_anomaly`: standing ledger of catalogue weirdness keyed `(kind, subject)`, `kind` a closed vocabulary mirrored in `app/results.py` `ANOMALY_KINDS` |
| `020_cert_reference.sql` | `document.cert_doc_id`: the notified-body certificate a DoC cites (Annex IV item 8) — distinct from `referenced_doc_id` (C6 cross-regulation sibling). VALIDATE resolves it best-effort against held `EC`/`ISO` documents; GATE persists it sticky and back-resolves it onto already-held DoCs when the certificate itself arrives later. `expiring_documents` (report/scheduler) follows it so a DoC inherits the certificate's expiry (Article 56 caps it at five years) |
| `021_ref_catalogue_basis.sql` | `ref-catalogue` match basis: a REF matched against the whole catalogue with no manufacturer to scope it (corpus/backfill documents carrying no usable identity). Barred from `production` at link level, structurally — invariant 3 |
| `022_coverage_scope_vocabulary.sql` | `document.coverage_scope` becomes a closed two-value vocabulary (`group` \| `manufacturer`); `item` retired (Denis's ruling, 2026-08-12) after 37 of the first 57 registered documents were written with a third value the ground truth never assigns |
| `023_ref_item_basis.sql` | `ref-item` match basis: a scoped REF match against the catalogue's OWN item number (`item_mirror.item_ref`) rather than the supplier-article column, per client ruling C12 (restated 2026-08-13) — production-capable, because the manufacturer is established before any number is compared |
| `024_link_retracted.sql` | `item_document.status = 'retracted'`: a link the CURRENT extraction no longer supports, distinct from `rejected` (which records a human). Coverage can now shrink when the evidence does |
| `025_doc_status_filed.sql` | `document.status = 'filed'`: read correctly, attributed correctly, and covering no item Dentalia stocks. A disposition, not a judgement — archived, evidenced and dated, but in nobody's queue |
| `026_job_finished_at.sql` | `job.finished_at`: when a job stopped, stamped on the terminal transitions only (`done`, `dead`) so a retry is never mistaken for a finish. The queue recorded when work was queued and claimed and nothing about when it ended, so "did it finish, and how long did it take" was unanswerable from the UI |
| `027_effective_expiry.sql` | `document_effective_expiry` view: one definition of when a document lapses — `LEAST` of stated expiry, the cited certificate's expiry, and issue + 5 years — so `/expiry`, the scheduler scan and `report.weekly` cannot disagree (closes G9). `basis` names which of the three won, so no screen mistakes the review horizon for a legal expiry a DoC never carries by regulation |
| `028_email.sql` | `email_poll_log`: the S2.4 inbound processed-email ledger — the durable reprocess guard for `email.poll`, keyed by (mailbox, uid_validity, uid), NOT the IMAP `\Seen` flag; `dentalia_api` gets SELECT only. `email.poll` was already in the `001` job-type enum, so no `ALTER TYPE` |
| `029_email_outbound.sql` | S2.4 outbound: `renewal_request` extended, `renewal_request_document`, `email_draft`, and the cadence guard `renewal_request_cadence_key` — one renewal mail per manufacturer per period, enforced by a partial unique index rather than by handler logic. `dentalia_api` gets SELECT + UPDATE on `email_draft` and nothing else: a person edits and releases a draft, the system never sends it (client ruling, spec §7.1) |
| `030_email_body_summary.sql` | `body_summary` + `summary_intent` / `summary_status` / `summary_model` / tokens / `summary_cost_usd` on `email_poll_log`: what an inbound message ASKED FOR, without keeping what it said. The body is read at poll time, compressed by the cheap tier and dropped — a compliance registry keeps documents for ten years, and supplier correspondence is not a document. UI context only, never joined, never re-read (invariant 12, widened + ratified 2026-08-20) |
| `031_extraction_playbook_rev.sql` | `extraction_attempt.playbook_slug`/`playbook_rev`: which playbook, if any, steered this read, and at what revision. `model_id` already records which MODEL read the document, but from S1.7 onward a playbook's lexicons and hints shape the read too, and evidence that does not name the rules that shaped it is not reproducible; `extract_rev` does not cover this, since it is a per-`content_hash` attempt counter, not a record of which rules were in force. Both nullable — most documents are claimed by no playbook, and NULL is the honest answer for them |

Field-level shapes live in [dentalia-schema-sketch.md](dentalia-schema-sketch.md); the tag -> table access matrix there says which handler may touch which table.

## Extraction tier ladder

Per-field escalation, cheapest first; a field a tier already answered is never re-asked:

- **T0**, deterministic: PyMuPDF text + coords, pdfplumber tables, layout templates (engine-agnostic JSON — page-1 anchor match selects a manufacturer's `ref_strategy`, `app/extract/t0_layout.py`, S1.5). Free. Its date-label, doc-type, certificate-number, and REF-shape lexicons are playbook-extendable (`date_labels`/`type_markers`/`cert_number_pattern`/`ref_pattern`, read by `app/extract/t0_templates.py` out of `manufacturer.body` since slice 2, 2026-08-27 — `playbooks/*.json` is the authoring format the seed imports, no longer what the pipeline reads) — additive or narrowing only, never a replacement of the global patterns. Komet is the first manufacturer to author one: its declarations print the issue date as `Rev. Stand:`/`Revisionsstand`, which no global label carried, so 155 of its 156 readable declarations were escalating to a paid tier to read a date already in the text layer (measured 2026-08-21: T0 reads 1 without the labels, 106 with them). T0 also *verifies* where the identifier carries its own check digits: a Basic UDI-DI ends in a MOD 1021,32 check pair, so `app/extract/udi.py` scores it offline and `extract_basic_udi` sets confidence from the verdict instead of asserting 1.0 on whatever the label regex caught. A code that fails its pair falls under the escalation threshold — the document goes to a tier that can re-read it — and raises `basic_udi_check_failed`. This is the only quality gate in the ladder that costs nothing and needs no external source, which is why it runs first and independently of the EUDAMED work (`docs/2026-08-20-udi-identifiers-and-registries.md` §2).
- **T1**, text model (`models.t1`, Haiku-class): missing fields only, from extracted text.
- **T2**, vision model (`models.t2`, Sonnet-class): scanned documents (~23% of the corpus) and what T1 could not answer, from rasterized pages.

T1 and T2 additionally accept an optional per-manufacturer hint block (`playbook.extract_hints`) carried as a SECOND system message — `T1_SYSTEM`/`T2_SYSTEM` themselves stay byte-identical whether a hint is sent or not. Four guards keep it safe (`app/handlers/extract.py`): every block opens with an instruction telling the model to ignore it if the document disagrees; the `manufacturer` hint specifically is withheld whenever `manufacturer` is itself being asked, so the model is never handed the answer to the one field a wrong hint could most dangerously confirm — every other field's hint reaches the model regardless of whether that field is being asked, since it could not answer an unasked field anyway; the block only asserts the manufacturer as fact when it came from the requesting group rather than T0's own text guess (hedged otherwise, all three in `_hints_for`); and which playbook — and revision — shaped the read is recorded on `extraction_attempt` regardless (`playbook_slug`/`playbook_rev`, migration 028).

Sweep work goes through the Anthropic Batch API; batch ids live in the `batch_ref` side table, never in job payloads (payloads are immutable after enqueue). Every LLM call is metered into `extraction_cost` (migration 010). LLMs rank candidates and parse documents only; they never fetch. Model choice is config (`MODELS_T1` / `MODELS_T2` / `MODELS_RANK`), and any tier swap requires the Phase 0 diff protocol re-run.

## Runtime conventions

Python 3.12+, synchronous throughout (no asyncio: the workload is polite-rate fetching plus batched LLM plus cheap logic). psycopg 3 with raw SQL, no ORM. Queue is hand-rolled (~150 lines, `app/queue.py`); pgqueuer is the documented escape hatch, not to be added preemptively. UI is FastAPI + Jinja + HTMX, no Node, no build step. Tests run against a real Postgres, never a mock.

## Where the boundaries are

- Nothing downstream of INGEST/FETCH may know which adapter implementation is live (`app/adapters/`).
- A stage may only emit the job types listed in its PRD Emits row.
- Never downgrade, never delete: older candidates flag rather than overwrite; supersession is append-only within identical `(coverage subject, type, regulation)`; MDR never supersedes MDD.
- Unchanged content is never re-fetched or re-parsed (fetch ledger).
- Audit entries are self-contained (`job_snapshot` jsonb); deleting every `job` row must leave the audit trail interpretable.
