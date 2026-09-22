# Dentalia — V3/V4 System Design: Logical Units & Queue Topology

v1 — 2026-07-03
Implements the decided architecture (`dentalia-mdr-pipeline-ground-truth.md`) with the v2 workflow decisions (`dentalia-workflow-structured-v2.md`). Design goals restated: deterministic-by-default, AI-on-exception, high confidence per step, nothing uncertain reaches production, all coordination via queues.

---

## 0. Shape of the system

**A modular monolith with real queue semantics — not microservices, not a visual orchestrator.**

One deployable (single repo, single runtime), internally split into workers that communicate *only* through a job queue and the registry. Each worker is a logical unit that can be run, tested, retried, and reasoned about independently. This gives the operational benefits of service separation (isolation, backpressure, partial retry, dead-lettering) without the operational cost of running six services — right-sized for one developer and a €10–40/month steady state.

**Queue = PostgreSQL.** Jobs table with `SELECT … FOR UPDATE SKIP LOCKED`. No Redis/RabbitMQ/SQS: at 116k items and a monthly delta of ~1.5k documents, peak throughput is tens of jobs/second on the worst day — three orders of magnitude below where Postgres queueing becomes the bottleneck. One database holds queue + registry + fetch ledger + audit log, which buys transactional writes across all of them (claim job + write result + append audit in one transaction — no distributed consistency problems ever).

```
                 ┌────────────────────────────────────────────────────┐
   INPUTS        │                    JOB QUEUE (Postgres)             │        OUTPUTS
                 └────────────────────────────────────────────────────┘
 BC export ──▶ [INGEST] ─▶ [RESOLVE] ─▶ [DISCOVER] ─▶ [FETCH] ─▶ [EXTRACT] ─▶ [VALIDATE] ─▶ [GATE]
 Drive corpus ─▶ (backfill enters at FETCH: files already local)                              │
 Email inbound ─▶ (enters at EXTRACT: attachment already fetched)                             ▼
 EUDAMED sync ─▶ (feeds DISCOVER as a lookup source, runs on its own cron)          ┌─ production registry
                                                                                    ├─ staging (review queue)
 [SCHEDULER] ─ cron: monthly sweep, expiry horizon, EUDAMED sync, failure monitor   └─ manual queue
 [EMAIL-OUT] ─ renewal requests (state machine, draft-for-approval)
 [REVIEW-UI] ─ humans consume staging + manual queues, decisions feed back as jobs
```

Key property: **multiple entry points, one spine.** Backfill, live fetch, and email attachments are different producers injecting jobs at different stages of the *same* pipeline. No special-case pipelines anywhere.

## 1. Queue mechanics (applies to every worker)

**Job row:**
```
job(id, type, payload jsonb, dedupe_key, status, priority, run_after,
    attempts, max_attempts, last_error, created_at, claimed_by, claimed_at)
status: pending | running | done | failed | dead
```

- **At-least-once + idempotent handlers.** Every handler must be safely re-runnable; the `dedupe_key` (e.g. `extract:{content_hash}`, `fetch:{url_normalized}`) makes duplicate enqueues no-ops. This one rule eliminates the entire class of "did it run twice?" bugs.
- **Retry with exponential backoff** via `run_after`; after `max_attempts` → `dead` (dead-letter). Dead jobs are visible in the review UI — a dead job is a work item, not a log line.
- **Per-domain politeness is queue-native:** FETCH jobs carry `domain`; the fetch worker claims at most one job per domain per politeness interval (domain lease table). Rate limiting is data, not code sprinkled around.
- **Priority lanes:** `interactive` (human clicked re-run in review UI) > `delta` (monthly changes) > `sweep` (initial/backfill bulk). A monthly sweep can never starve a human waiting on a re-check.
- **Job chains are explicit:** each worker finishes by enqueueing the next stage's job(s). There is no orchestrator process; the topology *is* the enqueue graph. Resumability is free — kill the process anywhere, pending jobs remain.

## 2. Logical units

Each unit: input job type(s) → output job type(s) / registry writes. All registry writes carry evidence. No unit calls another directly.

### 2.1 INGEST — `ingest.run`
SourceAdapter (CsvExportAdapter v1 → BcApiAdapter v2, config switch). Normalizes items (ids, names, manufacturer, MD flag, product class, UDI-DI when present), diffs against the item mirror, emits `resolve.group` jobs only for new/changed items. Deterministic. No AI.

### 2.2 RESOLVE — `resolve.group`
Manufacturer normalization (alias table), item→group assignment, cross- and intra-catalogue dedupe. Resolution order: existing link → UDI-DI → Basic UDI-DI (when known) → name/family heuristics. Only the last step may consult T1 (cheap LLM) for ambiguous name clustering, and its output is *grouping suggestions* that go to staging — grouping errors are upstream of everything, so grouping is confidence-gated too. Emits `discover.group` for groups lacking a current document.

### 2.3 DISCOVER — `discover.group`
Walks the per-manufacturer source-priority list (data, not code):

```
fetch_log recency check → known URL → playbook crawl (V4) → EUDAMED mirror lookup
  → manufacturer site search (search API + T1 candidate ranking)
  → vendor/distributor → email request (enqueue email.request, human-gated)
  → manual queue
```

Every step is deterministic except candidate ranking (T1 scores search results; the *decision* to fetch top-k is a threshold rule). Output: `fetch.url` jobs with candidate metadata, or `email.request`, or manual-queue entry with prefilled search links. The EUDAMED mirror is a local table refreshed by cron bulk-sync — lookups against it are free and deterministic.

### 2.4 FETCH — `fetch.url`
Consults fetch ledger first (ETag/hash/recency — never refetch unchanged). Conditional GET, sha256, StorageAdapter archive (Drive v1, S3 v2), fetch_log append. Playwright fallback tier for bot-walled domains (flagged per domain in manufacturer record). Emits `extract.doc` only when content is new (hash unseen) or changed. Backfill producer bypasses network: walks the Drive corpus, hashes, registers in fetch_log with `source=backfill`, emits the same `extract.doc`.

### 2.5 EXTRACT — `extract.doc`
The tier ladder, escalation on low field-confidence:

| Tier | What | Cost | When |
|---|---|---|---|
| T0 | Regex/templates: filename conventions (`(MDD)`, dates in names), playbook layout templates, REF-list table parsing | free | always first |
| T1 | Text LLM (Haiku-class / DeepSeek-class) on extracted text | ~cents | T0 incomplete or low confidence |
| T2 | Vision LLM (Sonnet-class / Qwen-VL-class) on page images | ~cents×10 | scan detected, or T1 low confidence |
| T3 | Human | expensive | manual queue with the doc + all tier attempts attached |

Target schema per doc: `type, regulation (MDR/MDD), validity_from, validity_to (nullable), coverage_scope, ref_list[], basic_udi_di, referenced_docs[], cert_number`. **Every field carries its own confidence and evidence** (page, verbatim string, tier). Escalation is per-field, not per-doc — a doc with a clean date but unreadable REF list escalates only the REF extraction. Email-inbound attachments enter here directly (the mailbox poller is just another producer that archived a file and enqueued `extract.doc`).

### 2.6 VALIDATE — `validate.doc`
Pure rules, no AI, no discretion:

- **REF gate:** extracted REF/Basic UDI-DI must match the item group → required for any auto-write. Name-similarity-only match caps at staging.
- Date sanity (from ≤ to, plausible ranges, "valid N years from issue" arithmetic applied *only* when the rule text was extracted with high confidence).
- **Never-downgrade:** candidate older than current → flag, never write.
- Supersession linking: same subject + newer validity → build `superseded_by` chain candidate.
- Type/regulation consistency (e.g. expiry on a DoC without referenced cert → suspicious, downgrade confidence).

Output: a **write candidate** with computed disposition.

### 2.7 GATE — `gate.candidate`
The single place where anything becomes real. Three dispositions, mechanical:

```
score = min(field confidences on required fields)

score ≥ HIGH  ∧ REF gate passed            → production registry + audit entry
score ≥ MED   ∨ REF gate failed             → STAGING: review queue (one-click approve/reject/edit)
score < MED   ∨ validation flags             → manual queue
```

**Staging is a registry state, not a separate store:** `registry_entry.status ∈ {staged, production, rejected, superseded}`. Staged rows are invisible to every consumer (read API, expiry scheduler, reports) — the system behaves as if they don't exist until a human promotes them. Approve/reject in the review UI enqueues `gate.apply` — even human decisions flow through the queue and land in the audit log with `decided_by`. Thresholds HIGH/MED are config, calibrated from Phase 0 error data, re-calibrated per manufacturer as playbooks mature.

### 2.8 EMAIL-OUT — `email.request`, `email.reminder`
Renewal state machine per v2 §2.1 (`due → requested → awaiting → received → parsed`, reminder/escalation branch). Draft-for-approval flag. SCHEDULER emits `email.request` when `validity_to` enters the horizon; the inbound poller closes the loop when a reply's attachment reaches production and supersedes the expiring doc.

### 2.9 SCHEDULER
Cron producer, owns no logic beyond emitting jobs: monthly delta sweep (`ingest.run`), expiry-horizon scan, EUDAMED bulk-sync, per-manufacturer failure-rate monitor (discovery/extraction failure spike → `playbook.reonboard` + notify), weekly report.

### 2.10 REVIEW-UI
One table page, three tabs: staging queue, manual queue, dead jobs. Each row shows the document, extracted values *with per-field evidence*, tier attempts, and one-click actions. Human decisions are jobs (2.7). This is the whole UI for Phase 1.

## 3. Confidence & evidence model (cross-cutting)

- Confidence is **per field**, produced by the tier that extracted it (T0 template hit = 1.0 by definition; LLM tiers self-report + calibration factor from Phase 0 ground truth).
- Evidence is **mandatory at write time** — the gate rejects any candidate value lacking `(source_url | archive_url, page, verbatim_string, tier, model_id, timestamp)`. Structurally impossible to have an unevidenced production value.
- Audit log is append-only: every production write, every human decision, every supersession. This *is* the product's trust story.

## 4. V4 evolution — playbooks compile determinism

V4 is not a rebuild; it is three additions to the running V3:

1. **Playbook store** — per-manufacturer versioned JSON in git: doc-library URLs/patterns, naming conventions, REF formats, layout templates (anchor text + regions for T0 date/REF extraction), crawl recipe, known quirks (bot wall → Playwright, login portal → manual).
2. **Onboarding agent** (dev-time, frontier model, human-approved output) — explores a manufacturer's site once, drafts the playbook, runs it against that manufacturer's corpus as a self-test, presents diff vs. AI-tier extractions for approval. Runs at onboarding and on `playbook.reonboard` (failure-rate trigger).
3. **Routing changes in DISCOVER/EXTRACT** — playbook present → crawl recipe replaces search, layout templates promote extraction to T0. AI tiers remain as fallback for that manufacturer's exceptions only.

Effect on the gate: playbook-covered manufacturers produce mostly `score = 1.0` candidates → auto-write rate climbs, review queue shrinks. **Deterministic coverage % (playbook + EUDAMED + UDI hit rate) is the monthly KPI** — the number the client watches go up. Pareto expectation: top 20–30 manufacturers cover the majority of 116k items.

## 5. Failure containment (worst-class scenarios)

| Failure | Containment |
|---|---|
| Wrong doc, high confidence | REF gate — name similarity alone can never auto-write |
| Issue date recorded as expiry | Type-aware validation (2.6); DoC-with-expiry-no-cert flagged |
| Site redesign breaks playbook | Failure-rate monitor → re-onboard; meanwhile AI-tier fallback keeps flowing at higher cost, correctness preserved |
| Model regression after swap | Gate thresholds per model_id; Phase 0 diff protocol re-run before any tier swap |
| Runaway AI spend | Per-run budget counter in scheduler; sweep pauses (jobs stay pending) at cap — queue makes pausing free |
| Duplicate processing | dedupe_key + content_hash idempotency |
| Queue poison message | attempts → dead-letter → visible in UI |

## 6. Build order (maps to agreed phasing)

| Phase | Units built | Note |
|---|---|---|
| 0 | Skeleton: job table + EXTRACT (T1/T2) + GATE + minimal review page, run over Euronda+Ivoclar corpus | The spike *is* the spine — ~40% reuse as planned |
| 1 | INGEST, RESOLVE, DISCOVER, FETCH, VALIDATE, SCHEDULER, full REVIEW-UI; T0 templates; Ljubljana sweep | Backfill first (zero fetch risk), then live discovery |
| 2 | Playbook store + onboarding agent, EUDAMED mirror, EMAIL-OUT state machine, Zagreb adapter, hardening | V4 turns on per-manufacturer, incrementally |

---

*Open design decisions to settle before Phase 1 code: runtime language (TS vs Python — extraction ecosystem favors Python, single-language repo favors whichever the review UI is built in); horizon config for renewal requests; HIGH/MED threshold starting values (proposal: 0.95/0.75, recalibrate from Phase 0).*
