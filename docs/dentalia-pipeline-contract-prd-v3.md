# Dentalia Compliance Warehouse — Pipeline Contract PRD

v3 — 2026-07-03
Supersedes v2. Organized **per pipeline stage**: what each stage consumes, what it emits, what it writes. Rationale/architecture stay in the ground-truth docs; this document is the normative contract for job types, payloads, and the end-state data model.

## Changelog v2 → v3 (design-review fixes)

| # | Change | Sections | Fixes |
|---|---|---|---|
| C1 | `mfr_ref` (manufacturer article number) made an explicit logical field in the INGEST contract, item mirror, and group membership; REF gate comparand defined as the pair `(canonical_manufacturer, mfr_ref)`. Which BC field supplies it is an adapter mapping. **Amended 2026-08-13 (client answer to C12):** the comparand is the pair `(canonical_manufacturer, catalogue article number)`, and the article number is `mfr_ref` **or** `item_ref` — see §6 rule 1 and §9. **Closed 2026-08-14 (Denis):** the adapter mapping is no longer an open question, because `mfr_ref` is not a pivot — BC's `artikli` holds the item-to-manufacturer relation and `mfr_ref` was only ever a check on it. `Ingest.mfr_ref_source_by_code` stays unconfigured; the 44,4% of items carrying no `mfr_ref` is a descriptive statistic, not a gap to close. Rationale and consequences: ground truth §7 trap 2. | §1, §2, §6, §9 | REF gate comparand was implicit — pseudo-code compared extraction `ref_list[]` (manufacturer article numbers) against `item_ref` without establishing that BC item No. contains article numbers; and bare REFs collide across manufacturers |
| C2 | Dedupe uniqueness scoped to **active** jobs (pending/running/failed). Terminal jobs (done/dead) no longer block re-enqueue. | §0 | `fetch:{url_normalized}` and `extract:{content_hash}` permanently blocked re-checks and re-extraction, defeating AC5 change detection |
| C3 | FETCH hash-dedupe path emits `validate.doc` (link candidate against existing document) instead of terminating silently. | §4 | Requesting group orphaned → infinite monthly re-discovery loop for any group whose document was first fetched via another group |
| C4 | Explicit rule for `coverage_scope = manufacturer` documents: one-time human binding approval, then automatic link derivation. New `match_basis` value `mfr-scope`. | §7 | QMS-type manufacturer certificates (no REF list, no Basic UDI-DI) could never pass the gate and would flood staging per requesting group |
| C5 | `item_document` gains link-level `status`. Gate partitions links by match basis; document status and link status are independent. | §7, §9 | Per-link trust invariant ("name-family never auto-writes") was unrepresentable with doc-level status only; also required by C4 mechanics |
| C6 | Supersession subject identity defined: `(coverage subject, type, regulation)`. MDR docs never supersede MDD docs. | §6 | Corpus evidence (IPS e.max CAD: MDD + MDR versions coexisting, both valid) made "newer supersedes older" wrong across regulations |
| C7 | Never-downgrade comparison on null `validity_from` defined: flag `downgrade-uncomparable`, never compare, caps at staging. | §6 | Null-date comparison was undefined on exactly the messy documents the rule protects against |
| C8 | Audit log stores a **job snapshot** (jsonb); `via_job` is a soft reference. Queue retention decoupled from 10-year audit duty. | §10 | Hard FK `audit_log.via_job → job` broke the audit trail on any queue pruning |
| C9 | Batch API tracking moved to `batch_ref` side table keyed by `content_hash`; job payloads stay immutable after enqueue. | §5, §10 | Self-rescheduling handler mutating its own payload violated payload immutability and risked duplicate batch submission on crash-retry |
| C15 | `document.status` gains **`filed`** (migration 025): read correctly, manufacturer resolved, evidence complete, and covering **no item in the catalogue**. A disposition, not a judgement — archived, evidenced, and in nobody's queue. Entry requires an empty `links[]` whose reason is specifically `no-ref-overlap`, no surviving BLOCKING flag, and a resolved manufacturer. Manufacturer-scope documents are excluded — C4 already owns them. Ratified by Denis 2026-08-14 (documents are kept, never dropped); re-entry when the catalogue gains a matching item is deliberately deferred (`[filed-relink]`). | §7, §9 | A manufacturer publishes for their whole line and a distributor buys a slice: on the IVOCLAR backfill Dentalia stocked **6** of the 1.605 REF codes the 181 staged documents named. `staged` means "a human should confirm", so all 181 would be opened, rejected and regenerated every sweep — a queue that can only be rejected through hides the genuine work in it |

**New listed working assumption** (joins MD flag, product class, grouping signals): *a manufacturer article number is obtainable per MD item.* Three candidate sources, unconfirmed which applies (possibly per supplier): (a) BC item No. **is** the manufacturer article number; (b) it lives in the vendor item no. / Item Vendor Catalog field; (c) mixed. Which field supplies `mfr_ref` is an **adapter mapping decision**, resolved by the BC export-format check (workflow v2 §7.2) + sample-doc REF comparison — nothing downstream depends on the answer. If no source is populated for a material share of items, AC1 target must be renegotiated — the REF gate has no key without it.

**End state the whole pipeline serves (unchanged):**
1. A **database** linking BC `item_ref` → N compliance documents (DoC / EC cert / IFU / ISO), each typed, dated, evidenced, superseded-chained.
2. A **sorted file archive** — every referenced document stored under our control, deduplicated by content hash, addressable via `archive_url`.

Every stage exists only to feed those two outputs. Anything not on a path to them is out of scope.

---

## 0. Job envelope (shared by all stages)

```
job(id, type, payload jsonb, dedupe_key, status, priority,
    run_after, attempts, max_attempts, last_error, claimed_by, claimed_at, created_at)
status:   pending | running | done | failed | dead
priority: interactive(0) > delta(1) > sweep(2)
```

Rules:
- At-least-once + idempotent handlers.
- **Dedupe uniqueness is scoped to active jobs (v3/C2):** `UNIQUE (dedupe_key) WHERE status IN ('pending','running','failed')` (partial index). Duplicate enqueue while an active job holds the key = no-op. Once a job reaches `done` or `dead`, the key is free — re-enqueueing the same fetch next cycle or re-extracting after a model swap is a normal operation, not a schema fight. `dead` intentionally does not block: the dead job remains a visible work item, but a URL that failed last month may work this month.
- Exhausted attempts → `dead` (visible in review UI).
- Claim via `SELECT … FOR UPDATE SKIP LOCKED`; claim + result + audit append in one transaction.
- **Job payloads are immutable after enqueue (v3/C9).** Handlers never rewrite their own payload; long-running external state (batch ids) lives in side tables (§5).
- Each stage ends by enqueueing the next stage's job(s) — the topology below **is** the enqueue graph.

```
ingest.run → resolve.group → discover.group → fetch.url → extract.doc → validate.doc → gate.candidate
                                    ↘ email.request        ↗ (backfill.scan, email.poll, upload.ingest enter here)
                                    ↘ manual queue          ↖ (fetch.url hash-dedupe re-enters at validate.doc — C3)
```

**dedupe_key conventions** (semantics per C2 above). Ratified 2026-07-31 against the live code, re-verified 2026-08-27 — every shape a producer actually writes is listed here or in the **operator producers** table that follows; a key shape in neither is drift. The split is deliberate: this table is the enqueue graph, that one is the shell tools an operator runs by hand.

| tag | producer | dedupe_key |
|---|---|---|
| ingest.run | SCHEDULER (monthly tick) | `ingest.run:sched:{catalogue}:{period_key}` |
| ingest.run | web UI (ingest form) | `ingest.run:web:{catalogue}:{source}:{ref}` *(unified 2026-08-14, Denis's sign-off: one `ingest.run:` prefix, then an origin token, same shape as `resolve:manual:` / `discover:gate-reject:` below. The two tails stay different on purpose — the scheduler's `period_key` is what stops a monthly tick re-enqueueing the same watch-dir file on every loop once the previous job leaves active scope, and the ref-keyed web form is what dedupes a resubmitted file. They are not meant to dedupe against each other.)* |
| resolve.group | INGEST | `resolve:{item_ref}:{mirror_rev}` |
| resolve.group | web UI (grouping-suggestion assign) | `resolve:manual:{item_ref}:{group_id \| new}` |
| discover.group | RESOLVE | `discover:{group_id}:{cycle}` *(`cycle` is constant `0` in shipped code — C2 active-scope dedupe supersedes the cycle token; kept for shape stability)* |
| discover.group | GATE (reject path) | `discover:gate-reject:{doc_id}:{group_id}` |
| discover.group | web UI (manual-tab re-run) | `discover:manual:{task_id}` |
| discover.group | web UI (`/items` re-discover) + CLI `discover-item` | `discover:refetch:{group_id}:{date}` *(added 2026-08-24 — admin refetch: `date` is Postgres `current_date`, so a same-day repeat click or CLI call dedupes; payload carries `ignore_recency: true`, see §3)* |
| fetch.url | DISCOVER | `fetch:{url_normalized}` |
| extract.doc | FETCH, backfill.scan | `extract:{content_hash}` |
| validate.doc | EXTRACT, FETCH (C3), DISCOVER (recency C3, 2026-08-24) — **plus two operator CLIs, see the operator table below** | `validate:{content_hash}:{extract_rev}:{group_id}` *(group_id suffix added in v3 — C3 allows the same extraction to be validated against multiple requesting groups; on the backfill path `group_id` is null and stringifies as `None` — an implementation leak; an explicit `:global` sentinel is the proposed cleanup)* |
| gate.candidate | VALIDATE | `gate:{content_hash}:{extract_rev}:{group_id}` (same `None` note as above) |
| gate.candidate | VALIDATE (C4 route) | `gate:{content_hash}:{extract_rev}:mfr-binding` |
| gate.apply | web UI | `apply:{doc_id}:{decision}` |
| gate.apply | web UI (C17 link decision) | `apply:{doc_id}:{item_ref}:{decision}` *(added 2026-08-27 to the table; the shape has shipped since C17 — `web/app.py:3631`. A link ruling is per `(document, item)`, so the item_ref segment is what stops two decisions on two items of the same document deduping onto each other. Same flat keyspace as the document-level row above, one segment longer.)* |
| email.request | DISCOVER (email rung) | `email.request:group:{group_id}` |
| email.request | SCHEDULER (expiry scan) | `email.request:mfr:{canonical_manufacturer}:{period_key}` *(**changed 2026-08-20, client cadence rule — see spec §7.2**: at most ONE renewal mail per manufacturer per week/fortnight, so the key is the manufacturer and the cadence bucket, NOT the document. The former `email.request:doc:{doc_id}` would have sent 52 separate mails to IVOCLAR for 2026-05-04 alone — measured. `period_key` is the cadence bucket, `renewal.request_cadence_days`. The three producers share one flat keyspace — each prefixes its id kind. **Producer synced 2026-08-27** (`app/scheduler.py`): between the ruling and that date the scheduler still wrote the old `email.request:doc:{doc_id}`, one job per expiring document, and only migration 029's cadence index kept the 52 down to one draft. It now resolves the manufacturer at enqueue time through `email_request.manufacturers_for_docs` — the same resolver the handler uses, so the key names the manufacturer the draft will be addressed to.)* |
| email.request | SCHEDULER (expiry scan, no production link) | `email.request:doc:{doc_id}` *(the unaddressable fallback, 2026-08-27. A document with no `item_document` at `production` resolves to no manufacturer, so there is no bucket for it to join; it keeps a document-scoped key rather than collapsing onto a shared `mfr:None:` one, which would drop every unaddressable document but the first. The handler refuses these with `no_manufacturer` and says so on the job result — the refusal is reported, not silent, and the next tick re-derives the set from the registry.)* |
| email.reminder | EMAIL-OUT (the request arms it once; each rung then defers the same job to the next, 2026-09-11) | `email.reminder:req:{request_id}` *(added 2026-08-27 to the table; the shape has shipped since S2.4 — `app/handlers/email_request.py:367,443`. Keyed on the renewal request, not the document: one chase per request is the whole point of the cadence rule, and `run_after` carries `renewal.reminder_after_days`. C2 active-scope dedupe is what stops the chain doubling when a reminder re-arms a request that already has a pending one. A reminder must never re-enqueue under its own key: the running row holds it, so the insert is dropped. It defers itself instead.)* |
| email.poll | SCHEDULER (mailbox tick) · the `/scheduler` panel's Run now | `email.poll:{period_key}` *(added S2.4 inbound — `period_key` is the interval bucket `YYYY-MM-DD:HH-band`, sized by `scheduler.email_poll_interval_hours`; a mailbox wants sub-day cadence, unlike the day-granularity scans. Producer gated off — `scheduler.email_poll_enabled` — until a mailbox is wired, GAP G8)* |
| eudamed.sync | web UI (document detail, EUDAMED lookup button) | `eudamed:{basic_udi_di}:{date}` *(added 2026-08-27 to the table; the shape has shipped with the lookup button — `web/app.py:2090`. `date` is the day, so a repeated click on one document dedupes for the day and next week's is a fresh read. §8 still lists SCHEDULER as the only intended producer and none exists (S2.3-deferred), so this button is the sole live producer of the tag. The handler writes `eudamed_mirror` and never a link — `ref-eudamed` is capped at staged.)* |
| playbook.reonboard | SCHEDULER (failure monitor) | `playbook.reonboard:{manufacturer}:{period_key}` |
| report.weekly | SCHEDULER · the `/scheduler` panel's Run now | `report:{period_key}` *(added S1.5 — `period_key` is the ISO year-week, e.g. `2026-W30`; ratifies PRD §8's "weekly report" prose into a real job type — PHASES.md GAP A. The web panel enqueues this and `email.poll` under the **identical** key rather than an origin-tokened one (`ingest.run`'s `:sched:`/`:web:` split): there the two producers name different work, here they name the same period's work, and collapsing them is what stops a hand-run and the next tick producing two reports for one week)* |
| bc.push | web UI (item button) | `bc.push:item:{item_ref}:{date}` *(added 2026-09-07 — the `discover:refetch` shape for an admin re-run: one push per item per day, and a terminal job never blocks the next)* |
| bc.push | web UI (bulk apply) | `bc.push:bulk:{run_id}:{batch_no}` |
| bc.push | SCHEDULER (drift cron) | `bc.push:drift:{period_key}:{batch_no}` |
| scheduler.tick | itself (migration 055 seeds it; the worker re-arms it on start) | `scheduler.tick:{cron}` *(added 2026-08-31. One perpetual, self-deferring job per cron — it never completes, so it holds its key for its whole life and cannot be duplicated. `run_after` is a POLL interval, not a fire time: `scheduler_run` stays the sole authority on whether a period has fired, which is what lets this coexist with the `app/scheduler.py` loop during the changeover — whichever asks first fires, the other is a no-op)* |
| upload.ingest | web UI (upload form) | `upload:{upload_id}` *(added web-document-upload slice — `upload_id` is the `upload_inbox` row id; the row is deleted on success, so a retry after a committed delete is a no-op rather than a duplicate)* |
| ingest.run | web UI (import upload, preview) | `ingest.run:upload:{upload_id}:preview` |
| ingest.run | web UI (import upload, apply) | `ingest.run:upload:{upload_id}:apply` *(added 2026-08-25, BC import upload slice A — `upload_id` is the `import_inbox` row id. Preview and apply are two jobs with two immutable payloads, so the operator's confirmation is carried rather than a payload mutated (invariant 9). C2 active-scope dedupe means a terminal preview never blocks its own apply, while a double-clicked Apply dedupes.)* |
| vendor.import | web UI (vendor import, preview) | `vendor.import:upload:{upload_id}:preview` |
| vendor.import | web UI (vendor import, apply) | `vendor.import:upload:{upload_id}:apply` *(added 2026-08-26, BC vendor import upload slice B — `upload_id` is the `import_inbox` row id at `kind='vendors'`. Preview and apply are two jobs with two immutable payloads (invariant 9), same two-phase spool as the item import: the preview reads the row and leaves it, the apply consumes it. C2 active-scope dedupe means a terminal preview never blocks its own apply, while a double-clicked Apply dedupes.)*
| eudamed.certregister | SCHEDULER (monthly tick, config-gated) | `eudamed.certregister:{period_key}` *(added 2026-08-27, S2.3 stage 2 — `period_key` is a calendar-day bucket at `scheduler.eudamed_certregister_interval_days`, deliberately not `period_key_interval`'s hour-bucketing, which degenerates to daily for any interval ≥24h)* |
| eudamed.sweep | web UI (`/manufacturers/{name}/sweep` release button) | `eudamed.sweep:{canonical_name}` *(added 2026-08-27, S2.3 stage 2 — §7b. No scheduler and no handler ever enqueues `eudamed.sweep`; the operator's release button is its sole producer. The scheduler marks a manufacturer's sweep due by writing `eudamed_sweep_state.due_at` directly, never through this key)* |

**Operator producers** (added 2026-08-27, Denis's ruling): shell tools an operator runs by hand also enqueue, and they are listed **apart** from the table above rather than beside the stages. A stage is a link in the enqueue graph and its producer row is part of the topology; a repair CLI is a person deciding to re-run something, and putting the two in one column made the graph read as if it had branches nobody wrote. They are in the contract — a shape here is not drift — and they obey every rule the stages do: closed job-type enum, C2 active-scope dedupe, immutable payloads.

| CLI | tag | dedupe_key |
|---|---|---|
| `app/cli.py enqueue` | any tag | operator-supplied. The only unconstrained key in the system; the `job_type` cast still refuses a tag outside the enum (invariant 7), so a typo fails loudly rather than creating a tag |
| `app/cli.py discover-item` | `discover.group` | `discover:refetch:{group_id}:{date}` — same shape and same day-scope as the `/items` button it mirrors, so a click and a CLI call on one day dedupe against each other |
| `app/regroup.py` | `resolve.group` | `resolve:{item_ref}:{mirror_rev}` — INGEST's own shape, deliberately: a regroup is a re-resolution of the same item at the same mirror revision, and colliding with an in-flight INGEST emission is the correct outcome |
| `app/repair_ref_list.py` | `validate.doc` | `validate:{content_hash}:{extract_rev}:{group_id}` — re-validates a stored extraction after a REF-list repair, at the bumped `extract_rev`. Keeps the `group_id` the deleted job carried, since dropping it turns a group-scoped match into an unscoped one |
| `app/komet_coverage.py` | `validate.doc` | same shape and the same scope rule, after a coverage-map append |

---

## 1. INGEST

| | |
|---|---|
| **Consumes** | `ingest.run` — payload: `{source: "csv" \| "bc_odata" \| "upload", ref: file_path \| delta_window \| {upload_id}, catalogue, dry_run?: bool, filename?: str, preview_job_id?: int}` *(`upload` and `dry_run` added 2026-08-25: `/imports` is mounted read-only in both web and worker, so a browser upload lands in `import_inbox` and the bytes are injected into the adapter the same way the OData stub's `records` are. `dry_run` defaults false — the scheduler and the `/ingest` form are unchanged — and when true INGEST diffs and reports but performs no upsert and emits no `resolve.group`. `filename` rides along the same day for the confirm screen — `dentalia_api` has no SELECT on `import_inbox` to read the uploaded name back, so the payload carries it instead; INGEST itself ignores the field, since the adapter already has the bytes off `import_inbox` by `upload_id`. `preview_job_id` joined it 2026-08-26, set by `POST /import/{job_id}/apply` to the preview it was confirmed from and likewise ignored by INGEST: applying re-reads the export and re-diffs against the catalogue as it stands at write time, so the confirm screen reports previewed against applied side by side rather than asserting the preview held. A dangling reference — the preview job pruned — degrades to the single-column report.)* |
| **Produced by** | SCHEDULER (monthly delta), operator (manual run, sweep) |
| **Adapter** | `CsvExportAdapter` (v1, path) / `UploadExportAdapter` (v1, spooled bytes) / `BcApiAdapter` (v2, OData). Config-switched; identical output contract — the path and bytes readers share one `_read_frame`, so they cannot drift. |
| **Logic** | Parse → normalize `{item_ref, name, manufacturer_raw, mfr_ref, md_flag, product_class, udi}` → diff against `item_mirror` → upsert. |
| **Emits** | `resolve.group` per **new or changed** item only. dedupe_key: `resolve:{item_ref}:{mirror_rev}` |
| **Writes** | `item_mirror` (full row + source revision) |
| **Failure path** | Unparseable file / auth failure → job `failed` → retry → `dead`. Row-level anomalies (missing item_ref, garbage encoding) → skipped-row report attached to job result, never silent. |
| **AI** | None. |

**v3/C1 — `mfr_ref`:** the manufacturer's article number for the item — a value that appears in document REF lists and *one* of the two keys the REF gate matches against (**amended 2026-08-13**, see below; it was the only one until then). `mfr_ref` is a **logical field**: the adapter populates it from whichever BC field actually holds the article number (`item_ref` itself if BC item No. is the manufacturer number; the vendor item no. / Item Vendor Catalog field otherwise; per-supplier mapping config if mixed). Downstream stages see only `mfr_ref` and never reason about its origin. `mfr_ref` may be null; an MD item with null `mfr_ref` is **not skipped** but counted in a `missing_mfr_ref` metric on the job result — this number directly bounds achievable AC1 and is reported in the Phase 0/1 findings.

**Amendment 2026-08-13 — `item_ref` is a REF-gate comparand too (client answer to C12).** Nataša Palme: the supplier article number *"ni tako pomembna — glavna je primarna Št., ker je ta tudi na samem fizičnem artiklu navedena"*. So the catalogue's own item number is a first-class matching key, not a fallback. Measured over the live registry (`tools/match_key_analysis.py`, 2026-08-12): `item_ref` collides across manufacturers **0** times against `mfr_ref`'s 34 — structurally, because `item_ref` is `item_mirror`'s PRIMARY KEY, so one value is one item is one manufacturer — carries **0** prose values against `mfr_ref`'s 64, leaves 13.844 of 15.958 values eligible after the §6 guards against `mfr_ref`'s 7.172, and its reach is a strict **superset** (`mfr_only` = 0 over 1.502 distinct extracted REFs).

This is admitted at the **gate**, as a second comparand, and deliberately **not** at the adapter by pointing `mfr_ref_source_by_code` at `item_ref`. The adapter mechanism *replaces* rather than fills (`app/adapters/source.py`), so remapping would overwrite the supplier's real value in the registry — measured to discard 156 genuine vendor values (Straumann pack variants where `061.7312`/`061.7314` map to the base article `061.7310` that the DoC actually names) — and would make `mfr_ref`, whose contract above is "the manufacturer's article number", hold a Dentalia number instead. `mfr_ref` keeps meaning what it says; the gate simply compares against both columns and records which one matched (§9, basis `ref-item`).

Both adapters must produce byte-identical downstream payloads — nothing after INGEST may know which source is live.

## 1b. VENDOR-IMPORT

| | |
|---|---|
| **Consumes** | `vendor.import` — payload: `{upload_id: int, filename: str, dry_run: bool, allow_renames: bool, preview_job_id?: int}` *(added 2026-08-26, BC vendor import upload slice B. Flat, like `upload.ingest`'s — the `ref` nesting belongs to `ingest.run`. `preview_job_id` is set on the apply only, and lets the confirm screen report previewed against applied.)* |
| **Produced by** | web UI (`POST /import/vendors`, then `POST /import/{job_id}/apply`). Never the scheduler: an operator uploads a file. |
| **Adapter** | None. Reads spooled bytes from `import_inbox` at `kind='vendors'` via `app/import_spool.py`, parses through `app/vendor_master.py`, which shares its frame reader with the item path so the two cannot drift. |
| **Logic** | Read spool (assert kind) → parse → `vendor_master.diff` → report. On apply: `vendor_master.apply` → consume spool → GC abandoned spool rows. A `dry_run` writes nothing and does **not** consume, which is why `import_inbox` is two-phase. |
| **Emits** | **Nothing.** A vendor import leaves `manufacturer_alias` stale; `playbooks sync` is a deliberate CLI operation with its own dry-run/apply discipline, and running it as a side effect would hide a second write behind one confirmation. The confirm screen names what is stale and the command to run. |
| **Writes** | `vendor_master` only. Never the registry (invariant 1); `dentalia_api` holds `SELECT` on `vendor_master` and no write grant, so the web structurally cannot do this — the worker does. |
| **Failure path** | Missing/wrong-kind spool row → job `failed` → retry → `dead` (a wrong-kind row would import zero manufacturers and report it as success). A **refused rename** is NOT a failure: the handler catches `RenameRefused`, returns `outcome: "rename-refused"` with the offending codes and `written: false`, and leaves the spool row so the apply can be repeated with the operator's opt-in. |
| **AI** | None (invariant 12). |

**Why renames are guarded.** `item_group.canonical_manufacturer` is effectively write-once — RESOLVE's ladder short-circuits on `_existing_link` and never re-derives it for an item already in a group. Re-pointing a code whose old name is already baked into existing groups produces a split-brain no later import can repair. `apply` therefore refuses **all-or-nothing** unless `allow_renames` is set: the added rows do not slip through alongside. In the browser that opt-in is a checkbox rendered only when the diff actually contains renames.

**Disappeared codes are never deleted.** A code absent from the export keeps its row with a stale `import_batch` — it is a fact to report, and deleting it would strand any alias already derived from it.

## 2. RESOLVE

| | |
|---|---|
| **Consumes** | `resolve.group` — payload: `{item_ref}` |
| **Logic** | (a) manufacturer normalization via `manufacturer_alias` table; (b) item → group assignment, resolution order: existing link → UDI-DI → Basic UDI-DI → name/family heuristic (pg_trgm candidates → rapidfuzz score); (c) intra-/cross-catalogue dedupe. |
| **Emits** | `discover.group` — for each group lacking a current production document **or a staged candidate awaiting review** (staged suppression added 2026-07-27: a C3 link candidate sitting in staging must not re-trigger discovery every cycle). dedupe_key: `discover:{group_id}:{cycle}` |
| **Writes** | `item_group` membership; `match_basis` recorded per assignment. **Group membership carries each member's `mfr_ref` (v3/C1)** — `group.member_mfr_refs` is the REF-gate comparand, materialized here. |
| **Staging path** | Ambiguous name clustering only → T1 LLM suggestion → **staging** (grouping is confidence-gated like everything else; never auto-applied). |
| **AI** | T1, last resort, gated. |

## 3. DISCOVER

| | |
|---|---|
| **Consumes** | `discover.group` — payload: `{group_id, ignore_recency?}` *(`manufacturer_id` and `context` were listed here through v3 and **never existed**: no producer has written either and `app/handlers/discover.py` reads only these two — removed 2026-08-25)* — *`ignore_recency` (optional, default false, additive 2026-08-24): bypass the recency-window checks only — conditional GET and hash dedupe still apply; set by the admin refetch producers, propagated onto every `fetch.url` this run emits.* |
| **Logic** | Walk source-priority list (data, per manufacturer): fetch_log recency → known URL → **playbook crawl** (shipped 2026-09-03, closes G6 — as a DISCOVER rung, not FETCH → FETCH, so no Emits delta and no `hop` guard: the playbook's `crawl` recipe names a document-library page, `app/crawl.py` harvests its links, deterministic triage first — `link_pattern`, `allow_hosts`, `doc_type_from` exclusions, `max_links` spent across all pages — with one domain lease per host and robots.txt before the first request. **Judged collections, ruled and built 2026-09-04 (`crawl_link_rank` 061):** a page with `crawl_rank_floor` (5) or more surviving links is a collection; a T1 link judge (`CrawlLinkRanker`, prompt `rank_crawl_links.md`) answers two things per link from its text and URL — predicted type and the article numbers it names — once per library version, cached on a hash of the harvest. The order is code, never the prompt: a link naming one of the group's own article numbers first, then DoC → EC → ISO → IFU → other, then confidence, then page order; the budget is spent in that order. A ranking, never a filter — under `max_links` it only sets the fetch order; predicted type steers fetching only, the document's type comes from its content (invariant 2). Above `crawl_rank_max` (1500, raised from 600 on 2026-09-04 after NSK measured 1.329 links) the pattern is too broad to pay for and page order applies) → EUDAMED mirror lookup → site search (SearchAdapter + T1 ranking; the ranker's order of value is DoC → EC → ISO → IFU, ruled 2026-09-04, `[discover-should-rank-doc-ec-iso-above-ifu]`) → ~~vendor/distributor~~ (folded into search, G6) → email → manual. |
| **Emits** | One of: `fetch.url` (top-k candidates, with `{url, domain, group_id, source_rank}`) · **`validate.doc` `{content_hash, extract_rev, group_id, archive_url?}`** *(recency rung, added 2026-08-24 — the C3 re-entry without the network: when every known URL is ledger-fresh and the content — incl. a playbook `direct` URL's — is already extracted, the requesting group's link candidate is emitted directly, terminal for the ladder; same payload and dedupe key as FETCH's hash-dedupe emission. Before this the rung only logged `skipped`, so a group whose document was first fetched for another group ended discovery with no link, no fetch and no manual task — measured on RENFERT groups 3138/3139/3140/5254 vs doc 843. Fresh-but-unextracted still falls through; `ignore_recency` bypasses it.)* · `email.request` (no automated source left, contact known, **and** `discovery.email_rung_enabled` — default off until the S2.4 EMAIL handler exists, so requests are never swallowed by the no-op; disabled rung logs `skipped` and falls through to manual) · **manual-queue entry** (prefilled search links). dedupe_keys: `fetch:{url_normalized}` / `validate:{content_hash}:{extract_rev}:{group_id}` |
| **Writes** | Discovery attempt log (per group: which sources tried, hit/miss) — feeds hit-rate stats and failure monitor |
| **AI** | T1 candidate ranking only; the fetch/no-fetch decision is a threshold rule. |

Side producer on its own cron: `eudamed.sync` → refreshes local EUDAMED mirror table (bulk JSON). Lookups against the mirror are free and deterministic.

Note (v3/C2): monthly re-checks of known URLs are ordinary `fetch.url` enqueues — the previous cycle's `done` job no longer blocks them; the fetch ledger's recency window remains the mechanism that keeps them cheap.

## 4. FETCH

| | |
|---|---|
| **Consumes** | `fetch.url` — payload: `{url, domain, group_id, source_rank, ignore_recency?}` — *see §3's note: bypasses only the `_fresh` recency skip, never the conditional GET or hash dedupe.* |
| **Logic** | fetch_log check first (ETag / recency window → skip without network). Then **robots.txt** (`app/robots.py`, cached per host, over the same httpx → Playwright ladder as the document; added 2026-09-11, ruled 2026-08-21). Conditional GET → sha256 → StorageAdapter archive (Drive v1 / S3 v2) → fetch_log append. Playwright fallback if domain flagged bot-walled. |
| **Emits** | See outcome table below. dedupe_key for extract: `extract:{content_hash}` |
| **Writes** | `fetch_log`; archived file |
| **Politeness** | Job carries `domain`; worker claims max one job per domain per interval (domain lease table). |
| **AI** | None. |

**Outcome contract (v3 — C3 changes the hash-dedupe row):**

| Case | fetch_log | Emits |
|---|---|---|
| New content hash | new row + archive | `extract.doc` `{archive_url, content_hash, group_id, source_url}` |
| 304 Not Modified | `last_checked_at` touched | nothing |
| **Same hash, new URL** | URL row linked to existing `doc_id` | **`validate.doc` `{content_hash, extract_rev: latest, group_id, archive_url?}`** — the requesting group is validated against the *already-extracted* document. No re-fetch, no re-extract. The resulting link candidate carries `match_basis = fetch-context` unless the existing extraction's REF list / Basic UDI-DI independently matches the group (then the stronger basis applies). Fetch-context caps at staging (§7). *(`archive_url?` additive 2026-08-24: the stored copy's handle, read from `document` by `content_hash` when the registry already holds one — a C3 payload without it made GATE fall back to `fetch_log.url_normalized`, the remote SOURCE url on a live fetch, and refresh `document.archive_url` + evidence rows to it; measured RENFERT doc 843. Absent only while the first candidate for the hash is still in flight — GATE now also keeps an existing registry handle rather than falling back, so the fallback can no longer overwrite one.)* |
| Recency window fresh, hash already extracted, requesting `group_id` present | untouched (skip without network) | `validate.doc` (outcome `recency-skip-linked`, same payload as the row above incl. `archive_url?`) — the requesting group still gets its link candidate against the fresh, already-extracted document; C3 semantics without the fetch. Fresh but unextracted or no requesting group → outcome `recency-skip`, nothing emitted. *(Row ratified 2026-07-31; shipped in S1.4.)* |
| Bot wall (403/JS) | retry via Playwright; persistent → domain flagged | retry or `dead` |
| robots.txt refuses the URL (`disallow`, or a non-200 robots answer other than 404/410) | untouched (nothing fetched) | nothing (outcome `robots-refused`, with `reason` and `robots_status`); the job finishes, it is not retried |
| robots.txt unreachable (transport failure) | untouched | retry via the queue's backoff — the absence of an answer is not a refusal |

This closes the discovery loop: a group whose document was first fetched on behalf of another group gets its link candidate immediately, and `has_current_production_doc()` (or a staged candidate) is true on the next cycle instead of re-triggering search forever.

**Alternate producers entering here or later (same spine, no special pipelines):**

| Producer | Job | Enters at |
|---|---|---|
| `backfill.scan` | walks existing Drive corpus, hashes, fetch_log `source=backfill` | emits `extract.doc` directly (file already local) |
| `email.poll` | reads dedicated mailbox (durable reprocess guard `email_poll_log`, keyed by mailbox+UIDVALIDITY+UID — not the IMAP `\Seen` flag), keeps PDF attachments, drops MSDS/business noise via `classify_doc_class`, archives + `source=email` | per useful attachment: new content hash → `extract.doc`; seen hash + a requesting group → `validate.doc` (C3-style link candidate, same as `upload.ingest`); seen hash, no group → nothing to link. **Inbound `group_id` is None** (self-identifies from content, like backfill), so the `validate.doc` path is dormant until reply→renewal_request→group matching lands (S2.4 outbound, spec §9). **AI: a cheap-tier body summary only** (invariant 12 widened and ratified 2026-08-20) — stored on `email_poll_log` as UI context for `/emails`, so the message body itself is never persisted; it is never evidence, never reaches VALIDATE or GATE, and the attachment path is unchanged (deterministic `classify_doc_class`, no LLM) |
| `upload.ingest` | web `/upload` form hands off a spooled PDF (`upload_inbox` row: bytes + optional `target_group_id`); handler hashes, archives, upserts fetch_log `source=upload`, deletes the spool row | new content hash → `extract.doc`; seen hash + a requesting group → `validate.doc` (C3-style link candidate); seen hash, no group → nothing to link |

## 5. EXTRACT

| | |
|---|---|
| **Consumes** | `extract.doc` — payload: `{archive_url, content_hash, group_id, source_url \| email_ref, companion_archive_url?}` *(`companion_archive_url` documented 2026-08-27, additive and long-shipped — `app/handlers/backfill.py:423` writes it, `app/handlers/extract.py:268` reads it. It is the annex whose REF list belongs to the primary document: a declaration that carries its device list in a separate file has the list tagged with its own handle, so the extraction can cite the page it actually read. Optional; absent on every path but backfill. `source_url` and `email_ref` are listed by this contract and read by no code — kept because the payload is additive-only and a consumer must tolerate what it does not use.)* |
| **Logic** | Tier ladder, **per-field escalation**: T0 regex/templates (free, conf 1.0) → T1 text LLM → T2 vision LLM → T3 human (manual queue with all tier attempts attached). |
| **Target schema** | `type, regulation (MDR/MDD), validity_from, validity_to?, coverage_scope, ref_list[], basic_udi_di?, referenced_docs[], cert_number?, manufacturer?, stated_class?` — every field with own confidence + evidence `(page, verbatim, tier, model_id, timestamp)`. **`stated_class` added 2026-08-21** — the risk class the document itself states (`I\|Is\|Im\|Ir\|IIa\|IIb\|III`). **T0-only**: never escalated to T1/T2 (absent from every escalate set, the `referenced_docs` mechanism), never part of GATE scoring, never a VALIDATE flag input. QA/display only — BC (`item_mirror.product_class`) remains the source of truth for item class; the comparison surfaces on `/data-quality` and nothing writes back to the mirror. A document naming more than one distinct class **abstains** (multi-product declarations print the whole ladder as boilerplate). **`manufacturer` added 2026-08-13**: the legal entity that manufactures the devices, as printed. Without it nothing produced identity for a corpus document, so §6 rule 1's scoped path could never fire and every backfill document fell to the capped unscoped branch (51 documents at `ref-catalogue`, 3 blocked outright by `multi-manufacturer-ref`). Explicitly NOT the authorised representative — 66 corpus files name `Dentsply IH Limited` in that role — and not a distributor or brand. T0 matches the legal names playbooks declare (never `match.anchors`, never the folder name, which is the supplier); it abstains when two manufacturers appear, and stays below the escalation threshold because a name on the page is not proof of whose it is. **T0/tier parity is a contract**: `t0_templates.T0_PRODUCED` and `T0_EXEMPT` must partition this list exactly, so a future field cannot be half-wired the way `referenced_docs` silently was. |
| **Emits** | `validate.doc` — payload: `{content_hash, extract_rev, group_id, archive_url}`. dedupe_key: `validate:{content_hash}:{extract_rev}:{group_id}` *(`archive_url` threaded through from the extract.doc payload since S0.3; payload row synced 2026-08-24)* |
| **Writes** | Extraction attempts log (all tiers kept — this is what T3 humans see) |
| **AI** | T1/T2, escalation on low per-field confidence only. |

**Batch API tracking (v3/C9):** when T1/T2 runs via the Batch API, the handler records `batch_ref(content_hash, batch_id, tier, submitted_at)` in a side table **before** submission is acknowledged as complete, sets `run_after = now() + poll_interval`, and returns without finishing. On re-claim it consults `batch_ref` by `content_hash`: batch exists and pending → bump `run_after`; done → process results, finish. A crash between submit and record is resolved by the `batch_ref` lookup on retry — an existing row for this `content_hash` + tier means never resubmit. Job payload is never mutated.

## 6. VALIDATE

| | |
|---|---|
| **Consumes** | `validate.doc` — `{content_hash, extract_rev, group_id, archive_url?}` *(`archive_url` is carried through to `gate.candidate`, never consumed here — GATE needs the stored copy's handle for `document`/`evidence.archive_url`)* |
| **Logic** | Pure rules, zero AI, zero discretion — rule set below. |
| **Emits** | `gate.candidate` — payload: write candidate + computed disposition + all flags + `links[]` (one per covered item, each with its `match_basis`) + `supersedes` + `related_doc_id` + `cert_doc_id` + `superseded_by_doc_id` (task 5; emitted for GATE to persist; never written here) |
| **Writes** | Nothing. Validation is stateless. |

**Rule set (v3 revisions marked):**

1. **REF gate (C1):** `overlap(ext.ref_list, group.member_article_numbers)` **or** `ext.basic_udi_di == group.basic_udi_di`. The matching key is always the **pair `(canonical_manufacturer, catalogue article number)`** — bare article numbers collide across manufacturers, and it is the scoping, not the number, that makes a match trustworthy. A member's article numbers are its `mfr_ref` (the supplier's, basis `ref-list`) and its `item_ref` (Dentalia's own, basis `ref-item` — **amended 2026-08-13**, C12 client ruling, §1). Either match passes the gate and both are production-capable, because in this rule the manufacturer is established *before* any number is compared; the basis records which column matched so the provenance stays honest. Where a member's two numbers are equal — 7.450 of 15.958 rows — `ref-list` is recorded, since the supplier's own number matching is the stronger claim. Within the normal gate the scoping is inherited (groups are manufacturer-scoped); for backfill/email documents (`group_id = null`) resolving coverage against the full index, the document's extracted/known manufacturer **must** scope the lookup — an unscoped REF hit is not a gate pass and caps at staging with flag `ref-unscoped`. That extracted manufacturer is **canonicalized through `manufacturer_alias` first** (read-only — VALIDATE never self-seeds the way RESOLVE does, so a garbled OCR name cannot mint a manufacturer), and both the alias lookup and the group lookup fold case, whitespace, punctuation spacing and accents. Folding only, never fuzzy: at a token_set_ratio of 80 the registry's 370 manufacturers collide 30 times and ~28 are wrong merges (`OCO`/`VOCO` 85, `FARMADENT`/`FORMADENT` 88, `MEDIAS`/`MEDIS` 90), and a wrong merge attaches one company's certificate to another's product. When the canonicalized name matches no group at all, flag `manufacturer-unresolved`; when it does resolve but no group's REF list overlaps, flag `no-ref-overlap` (added 2026-08-11 — the two states are different problems and staging must be able to tell them apart).

**Coverage follows the REF list, not our grouping (amended 2026-08-13, Denis).** A document links **every** member it names, across **every** group under the pinned manufacturer — not the members of one resolved group. `item_group` is *our* clustering for discovery: it decides which items need a document searched for, and it never decides what a manufacturer's document covers. A declaration listing twenty articles covers twenty articles. Trust is unaffected because trust never came from the grouping: every group scanned carries the *same* `canonical_manufacturer`, pinned before any number is compared, so each link still rests on the invariant-3 pair. Widening from one group to that manufacturer's groups adds items, never manufacturers — and the cross-manufacturer guards are untouched on both paths (`multi-manufacturer-ref` unscoped, the manufacturer filter scoped). An item reachable through two groups is linked once, at the strongest basis, since `item_document` is keyed `(item_ref, doc_id)`. The lowest matching group stays the document's `group_id` for provenance only. `multi-group-match` is still raised with its count, but it now reports breadth rather than marking a loss: it used to mean "we picked one group and dropped the rest". Measured before the change: 3.118 extracted codes reached 323 catalogue items while 96 links were written — **228 items missed across 23 documents**, document 204 reaching 20 items across 5 groups of one manufacturer and linking 1. The REF comparison additionally applies the owning manufacturer's `ref_normalize` playbook rule to both sides (market-code suffixes; per-manufacturer because a global strip would corrupt space-bearing codes such as Komet's ISO bur numbers).

   **Unscoped REF linking (added 2026-08-11).** A backfill/corpus document with `group_id = null` and *no* extracted manufacturer is not left link-less. Measured over the whole catalogue, only 24 of 5.511 distinct REFs collide across manufacturers (12 prose, 12 genuine article numbers — 0,22%), so a REF match alone identifies the manufacturer well enough to **propose** a link. Such links are written at `match_basis = 'ref-catalogue'`, which §9's CHECK bars from `production` (see C5): the pair `(canonical_manufacturer, mfr_ref)` was never verified — the manufacturer was *inferred* from the number, and catalogue-observed uniqueness is not global uniqueness. `ref_gate` stays **False** on this path, so the document cannot reach production either. Guards, in order: REFs shorter than `cfg.validate.min_unscoped_ref_len` (default 6) are ineligible; prose values (the `mfr_ref_prose` `"!"` rule) are excluded; REFs pointing at more than one manufacturer flag `multi-manufacturer-ref` and link nothing. `ref_normalize` is deliberately **not** applied when inferring the manufacturer — measured, a global strip raises genuine collisions from 12 to 18 while adding zero real matches — but still applies afterwards, once the manufacturer is pinned, because that is the ordinary scoped case. A hit that fails a guard still flags `ref-unscoped` as before.
2. **Manufacturer-scope pre-rule (C4):** if `coverage_scope = manufacturer` and the document carries no item-level identifiers, the REF gate is *not applicable* — the candidate is routed to the manufacturer-binding flow (§7) instead of failing the gate per group. **Device-enumeration guard (added 2026-08-25, Denis ruling 2026-08-24 — "Guard + clean 310"):** on this route VALIDATE additionally reads the stored `document_text` for the candidate's content hash. A QMS/QA-system certificate whose own text enumerates the devices it covers — a model/code annex (`Modello / Model:` + `Codici / Codes:` with article-number tokens), repeated per-device risk-class lines, or the restriction clause "valid only for the above mentioned Medical Devices" (sufficient alone; the other signals fire two-of-three) — carries flag `device-enumeration` on the binding candidate, and §7's C16 machine binding refuses it: the document stages with exactly one review task instead of fanning out manufacturer-wide. Measured basis: doc 310 (Kiwa Cermet EC QA certificate MED 31385, 93/42/EEC Annex V) enumerates exactly three device types with model codes and was machine-bound to 356 GC production items; across all 809 stored texts on 2026-08-25 the detector fires on that one document. The shapes that stay machine-bindable, pinned in tests: a scope-only ISO 13485 (Carl Martin, doc 816), a device-*family* schedule with no codes (BSI "Device Schedule", doc 138), an EMDN *category* list (TÜV Annex IX "Products:", doc 420). A scan or missing text (`document_text.source = 'none'`) is unmeasurable and keeps today's behaviour; `gate.apply bind-manufacturer` (human) is unaffected. The basis is over-binding, **not** expiry — a lapsed certificate changes only the coverage KPI, never bindings (separate ruling, same day).
3. Date sanity (from ≤ to, **plausible ranges** — each date within `[1990, 2100]`; a hallucinated or OCR-garbled year is flagged `date-insane`, never used to drive supersession); "valid N years from issue" arithmetic only on high-confidence rule text.
4. **Never-downgrade (C7):** candidate `validity_from` older than current, **both dates present** → flag `older-than-current` (blocking, unchanged) AND `superseded_by_doc_id = current.doc_id` plus flag `auto-superseded` (additive fast-path signal, task 5) — VALIDATE emits both, never either instead of the other. **Equal dates (added 2026-09-04, Denis ruling the same day):** candidate `validity_from` identical to current's, both present → flag `same-date-revision` (blocking): no ordering exists between two documents issued on one day, so a person picks the current one; VALIDATE emits neither `supersedes` nor `superseded_by_doc_id`. Until then the equal case matched no rule and the second document landed as an unrelated production document. GATE (§7) only takes the fast path straight to `superseded`, without a human, when its own guard passes: no *other* blocking flag survives (e.g. `no-item-identifier` still forces `manual` — an item-less older doc is not "unambiguous", nothing ties it to the covered subject at all) AND `validity_from`'s own calibrated confidence clears `cfg.gate.high` (`validity_from` is not a required/scored field, so a garbled OCR year must not silently drive a permanent write). When the guard fails, `older-than-current` — still in GATE's blocking-flag list — forces `manual` exactly as it did before this task; it never reaches `production` either way. This is the inverse of C6: the *incoming* candidate is older and is itself superseded by the current production document, not the other way round — and the pointer it writes is subject to the same live `(type, regulation)` re-verification as a forward supersession (§7). **If either date is null, no comparison is made** — flag `downgrade-uncomparable`, which caps disposition at staging. Null is never treated as older or newer. Never-downgrade means the candidate is never made current, not that it goes unstored — an auto-filed older document is still archived and fully evidenced under `superseded` status (task 5); a guard-failed one is still archived and evidenced under `staged` pending human review.
5. **Supersession (C6):** a candidate may supersede only a document with the same **coverage subject, same `type`, and same `regulation`**. An MDR DoC never supersedes an MDD DoC — under transition rules both are simultaneously valid; they are parallel chains, not one chain. Cross-regulation pairs are recorded as `related` (VALIDATE emits `related_doc_id`; GATE persists), never `superseded_by`.
6. Stated expiry: a `DoC` carrying a `validity_to` whose evidence verbatim does not name it as an expiry → flag `expiry-on-certless-doc` (name historical, see `docs/vocabulary.md`). **Amended 2026-08-17.** The rule previously asked whether the document cited a certificate; measured across every registry document carrying a `validity_to`, 156 of 156 label the date in their own verbatim, so the certificate proxy over-fired on 15 documents at score 0.97 while letting GC doc 227 reach `production` with a signing line ("Leuven, 12 February 2026") as its expiry. An unrecognised expiry phrase leaves the flag ON, so the phrase list fails safe and may be extended per brand.
7. **No-item-identifier:** a **non**-manufacturer-scope document that yields no `ref_list` and no `basic_udi_di` cannot be linked to the catalogue → flag `no-item-identifier`, forcing manual review (never silently staged). The only legitimate no-item-id document is a manufacturer/catalogue-wide certificate, already routed by C4.
8. **Cited-certificate resolution (task 4):** for a DoC, resolve `cert_number` plus each entry of `referenced_docs[]` against `document.cert_number` for a held `EC`/`ISO` document (`production` or `superseded`) → emit `cert_doc_id`. MDR Annex IV requires only a date of *issue* on a DoC, so most carry no expiry of their own; Article 56 caps a notified-body certificate at five years, so this is where a DoC's real renewal date comes from. Best-effort: no match → `cert_doc_id = null` and flag `cert-unresolved`. Never an error — Class I devices cite no certificate at all, and the cited one may simply not be held yet (GATE back-resolves it later, §7).

**Flag → disposition (enforced by GATE §7):** `older-than-current`, `same-date-revision` (2026-09-04), `no-item-identifier`, `evidence-page-missing`, `date-insane` and `manufacturer-unresolved` (the last two 2026-08-13) are *blocking* — they force `manual`. (`evidence-page-missing` is raised by GATE's own completeness check rather than by VALIDATE — a T1/T2 value with no page cite, invariant 2 — and has been in `gate.BLOCKING_FLAGS` and handbook §7 throughout; listed here 2026-08-25.) `auto-superseded` is neither blocking nor a staging cap on its own; it is a fast-path signal that pre-empts the score/flag decision **only under GATE's three-condition guard** — `superseded_by_doc_id` present, no blocking flag *other than* `older-than-current` itself, and `validity_from` confidence ≥ `cfg.gate.high` — in which case disposition is `superseded` instead of GATE's usual production/staged/manual decision. The chain **pointer** write is guarded separately, and identically on both routes into it: GATE re-reads BOTH documents' `(type, regulation)` from the registry at write time and raises rather than writing a pointer across a mismatch. The payload's `superseded_by_doc_id` / `supersedes` is a snapshot VALIDATE took, and either document can be re-typed while the job waits in the queue — a re-extraction rewrites `type`/`regulation` on upsert (only `status` is sticky) and `gate.apply approve` accepts human edits to both — so a payload-trusted write can cross regulations, which C6 forbids. When any of the three fails, `older-than-current` (still blocking) forces `manual`, the same fallback that existed before task 5; the guard failing must never let an older candidate reach `production`. The remaining flags fall into two classes, **amended 2026-08-13 (Denis's ruling)** — before that amendment every flag capped at `staged`, which made a flag describing CONTEXT gate exactly as hard as one describing a DEFECT:

- ***capping*** — a real unknown about this document that a reviewer can settle: `ref-unscoped`, `ref-catalogue`, `multi-manufacturer-ref`, `downgrade-uncomparable`, `expiry-on-certless-doc`, `device-enumeration` (raised on `mfr-binding` candidates only, where §7's machine bind refuses on it directly; classified here so it holds at `staged` should it ever ride a normal-route candidate). These cap the disposition at `staged`.
- ***informational*** — recorded on the candidate and shown in the UI, but they do **not** gate the disposition: `no-ref-overlap`, `multi-group-match`, `cert-unresolved`, `auto-superseded`, `ref-list-possibly-truncated` (`tiers.REF_TRUNCATION_FLAG`: a T1/T2 `ref_list` came back at exactly the prompt cap, so the list may be cut short. Raised in EXTRACT rather than VALIDATE, which is why it was missed here and in the handbook until 2026-08-25).

`date-insane` and `manufacturer-unresolved` move up to *blocking* in the same amendment: a nonsense date and an unattributable manufacturer are defects a human must fix, not states a reviewer can approve away.

Measured justification, GC corpus 2026-08-13: 43 documents carrying **all 309 live links** sat `staged` on `cert-unresolved` / `multi-group-match` alone — 100% of the registry's coverage — while the 74 documents flagged `no-ref-overlap` held zero links between them, that flag meaning there was nothing to link in the first place. `cert-unresolved` is raised by VALIDATE with the words "caps at staged (non-blocking) and self-corrects once the certificate is gated"; only this rule disagreed. `multi-group-match` lost its reason to cap when a document began linking every matching member across ALL groups under the pinned manufacturer (2026-08-13) — no group is chosen arbitrarily any more.

**An unrecognised flag gates.** GATE tests membership of the informational set, not of the blocking/capping sets, so a flag a future rule adds and nobody classifies behaves like the old conservative rule instead of silently ceasing to gate.

## 7. GATE

| | |
|---|---|
| **Consumes** | `gate.candidate`; `gate.apply` (human decisions from review UI) |
| **Emits** | `discover.group` (reject path only, interactive priority — dedupe_key `discover:gate-reject:{doc_id}:{group_id}`). *(Row added 2026-07-31 — the emission was sanctioned in prose below but absent from the table, making the §10 Emits guard unverifiable for GATE.)* |
| **Writes** | `document` + `item_document` rows; evidence; audit log entry (including `decided_by`); supersession chain update |
| **Invariant** | Rejects any field lacking complete evidence — structurally no unevidenced production value. Staged entities invisible to all consumers. |

**Disposition — two levels (v3/C5):**

*Document level* (unchanged in spirit): `score = min(required-field confidences)` → `production` / `staged` / `manual` per thresholds and flags.

*Link level (new):* `item_document.status ∈ {staged, production, rejected}` — each link is gated **independently by its own `match_basis`**:

| match_basis | max auto status |
|---|---|
| ref-list, ref-item, map-supplier, udi, basic-udi-di | production (if document is production and score ≥ HIGH) |
| mfr-scope (C4, post-binding) | production |
| name-family, fetch-context, ref-catalogue | **staged — always**, regardless of confidence |
| manual | production (human is the authority) — written **only** by `gate.apply confirm-link` (C17) |

`ref-catalogue` (added 2026-08-11) is the basis for a link formed by matching an extracted REF against the **whole catalogue**, with no manufacturer to scope the lookup — the backfill/corpus case where the document carries no usable identity. It is capped for a reason distinct from name similarity: the match itself is exact, but *catalogue-observed uniqueness is not global uniqueness*. Measured over 5.511 distinct catalogue REFs, only 24 collide across manufacturers (12 prose already flagged `mfr_ref_prose`, 12 genuine article numbers, 0,22%) — but that rate describes what Dentalia stocks and says nothing about a manufacturer whose products they do not carry using the same number. Strong enough to *propose* a link, never to *write* one. Three guards apply before the basis is even reachable: a minimum REF length (`cfg.validate.min_unscoped_ref_len`, default 6 — 85 catalogue REFs are 1-3 characters and worthless as identity), exclusion of prose values, and an ambiguity check that flags `multi-manufacturer-ref` rather than choosing when the REFs point at more than one manufacturer.

`ref-item` (added 2026-08-13, C12) is the same match as `ref-list` against a different column: the extracted REF matched the member's `item_ref` (Dentalia's own item number) rather than its `mfr_ref` (the supplier's). It is production-capable for the reason `ref-list` is and `ref-catalogue` is not — the manufacturer was **established before the comparison**, inherited from the requesting group or extracted and canonicalized, so the pair guarantee holds and the number is only being asked to pick items *within* an already-known manufacturer. On the measured evidence it is the stronger of the two keys: 0 cross-manufacturer collisions against `mfr_ref`'s 34 (structural — `item_ref` is `item_mirror`'s PRIMARY KEY), 0 prose values against 64, and a reach that strictly contains `mfr_ref`'s. It is a separate basis rather than an alias of `ref-list` so the registry records *which* number matched: a link reading `ref-list` must keep meaning "the supplier's article number matched". The unscoped path is unaffected — it still rewrites every basis it forms to `ref-catalogue`, so an `item_ref` hit with no manufacturer stays capped exactly as before.

`map-supplier` (added 2026-08-19) is the same REF-overlap match on the same scoped path, recorded separately because the list being compared did not come out of the document: it came from the manufacturer's OWN article index, shipped alongside its declarations and archived as evidence in its own right (VALIDATE sets it when the extraction's `ref_list` carries `source: "coverage-map"`). It is production-capable for exactly the reason `ref-list` and `ref-item` are — the manufacturer is fixed by the source document *before* any article number is compared, so the pair guarantee holds — and it is a separate basis rather than an alias so the registry records that the manufacturer stated this coverage rather than the pipeline reading it off a page. The allow-list is `app/handlers/gate.py` `TRUSTED_BASES`, which both the machine auto-file path and `gate.apply`'s human-approval path read; a basis absent from it stages at any confidence, on every path, silently.

A production document may therefore carry a mix of production links (REF-matched items) and staged links (name-matched stragglers). Consumers (read API, expiry scan, reports) see a document *for an item* only when **both** the document and that item's link are production. This makes the "name similarity never auto-writes" invariant per-link and structurally enforced, which is where the trust claim actually lives. The one widening, 2026-09-16: a read-API caller may ask for the **supersession chain** (`?include_superseded`, `item_document_history`, migration 070), which relaxes the DOCUMENT half to `production, superseded` and leaves the LINK half exactly where it is — so the sentence above still holds for every link, which is the half the trust claim rests on. Staged, rejected and filed documents stay invisible to every consumer.

**Link provenance is immutable at production (added 2026-07-31):** once a link is production, a later candidate for the same `(item_ref, doc_id)` never changes its `status`, `match_basis`, or `udi`. The basis records *how the link was established*; a rev-N+1 re-extraction that lost the REF list legitimately arrives with `fetch-context`, and overwriting the stored basis would both rewrite provenance and violate the C5 CHECK (dead-lettering an otherwise valid job). Staged links still take the newer basis.

**Reject cascade:** `gate.apply` decision `reject` sets the document to `rejected` **and cascades every link to `rejected`** (no link may remain production or staged under a rejected document); each cascaded link is recorded as its own `link-rejected` audit event. **Every staged→production transition writes a `production-write` audit event** — on the machine path (`gate.candidate`) and on the human paths (`approve`, `bind-manufacturer`) alike, once per transition (idempotent under re-delivery).

**Manufacturer-scope binding flow (v3/C4):** a document with `coverage_scope = manufacturer` and no item-level identifiers (typical QMS-type certificate) carries a single **binding candidate**: `doc → manufacturer`. Exactly one review-queue entry regardless of how many groups requested it. Whichever route it takes:
- the document is promoted to production;
- links to all MD items of that canonical manufacturer are derived automatically with `match_basis = mfr-scope`, status production;
- future items resolving to that manufacturer receive the link at RESOLVE time without re-review;
- the binding itself is an audit-log event and is severable (un-bind = reject + link cascade to rejected, append-only trail preserved).

**C16 — machine binding on an unambiguous name (Denis's ruling, 2026-08-17).** The flow above was human-gated end to end, which put a queue in front of a decision with nothing in it to decide: resolving a manufacturer name is a lookup in a curated table, not a judgement. `gate.candidate` now performs the binding itself when **all five** of the conditions below hold (`app/handlers/gate.py:797-815`). The name is matched on either side of the alias table (`raw_name` or `canonical_name`) after normalization, and the resulting link set fans out over **every** BC vendor code under that canonical, since one manufacturer routinely spans several (IVOCLAR is 001, 005 and 275).

1. **One canonical.** The document's manufacturer string resolves through `manufacturer_alias` to **exactly one** canonical. Zero or several is a judgement, and goes to a person.
2. **The name is confidently read.** That field's calibrated confidence clears `cfg.gate.med`.
3. **No `device-enumeration` flag** (§6 rule 2, added 2026-08-25): a certificate whose own text enumerates its covered devices is refused by the machine and staged with one review task, because binding it line-wide would assert coverage the paper itself limits to a device list (doc 310: three enumerated device types, 356 items bound).
4. **The scope is stated, not inferred** (`scope_stated`, documented 2026-08-27 — shipped earlier, in the code since the C4 route). `coverage_scope`'s **own** calibrated confidence must clear `cfg.gate.high`, a higher bar than the manufacturer name's `med`. `derive_coverage_scope` returns `manufacturer` at 1.0 for an ISO or EC certificate but at 0.6 for a declaration whose REF list T0 could not parse, and those two are not the same claim: one document says it covers the line, the other merely failed to say what it covers. This is the guard between an unparsed REF list and a catalogue-wide write.
5. **The document is evidence about a medical device** (`_is_md_document`, documented 2026-08-27 — added the day C16 shipped, `gate.py:714`). `regulation` must be `MDR` or `MDD`, with exactly one exception: `type = ISO` at `regulation = n.a.`, because ISO 13485 is a quality-management standard rather than an instrument issued under either, and a QMS certificate genuinely does cover a manufacturer's whole line. `EC` is deliberately **not** exempt — an EC certificate names its directive, so a missing regulation on one is a reading failure rather than a property of the instrument. Measured basis: doc 1494, a PrograMill PM5 milling-unit declaration citing the Machinery and Radio Equipment directives, was bound to all 1.069 IVOCLAR production items on 2026-08-17. The corpus carries cosmetics, low-voltage, EMC, machinery and RoHS declarations, all read correctly and none of them evidence about a ceramic block.

Conditions 4 and 5 were enforced before they were written down, found by the 2026-08-27 flow-map pass. Both are refusals into review, never rejections: `gate.apply bind-manufacturer` stays open to a person in every case.

Three outcomes, and only the third involves a human:
- one canonical **and** at least one MD item → `production` + mfr-scope links + `bind-manufacturer` / `production-write` audit events, **no review task**;
- one canonical **and** no MD items → `filed` (C15). The binding is correct and links nothing; staging it would park a row nobody can action;
- name absent, unresolved, or ambiguous → `staged` + exactly one review task, the pre-C16 behaviour unchanged.

The guard is the alias hit rather than the model's confidence: a garbled read does not accidentally match a curated table. Confidence is a secondary check at `MED` and deliberately **not** `HIGH`, which is still the uncalibrated 0.95 placeholder (`[gate-thresholds]`) and would reject correct T0 reads at 0.90. `gate.apply` `bind-manufacturer` is unchanged and remains the route for everything that falls to review.

Manufacturer-level *product* certificates that do carry Basic UDI-DI lists (Annex XII style) pass the normal REF gate per group and do not use this flow.

**Cited-certificate persistence + back-resolution (task 4, migration 020):** `gate.candidate`'s `cert_doc_id` is persisted sticky (`COALESCE(EXCLUDED.cert_doc_id, document.cert_doc_id)`), same pattern as `referenced_doc_id`, so a later re-delivery that fails to recompute it never erases an already-resolved certificate. When the document GATE is writing is itself a certificate (`type ∈ {EC, ISO}`), GATE also **back-resolves**: every held `DoC` with `cert_doc_id IS NULL` and a matching `cert_number` is updated to point at it. Without this step, whether a DoC ever gets an expiry would depend on fetch order — declarations are frequently processed before the certificate they cite.

**C17 — link-level human decisions (`confirm-link` / `reject-link` / `reopen-link`), 2026-08-24.** C5 made the link the unit of trust but left it without a human verb. `gate.apply approve` promotes the document and, via `_promote_pending_links`, only those links whose `match_basis` is in the trusted allow-list; a `name-family` / `fetch-context` / `ref-catalogue` link is excluded there and forbidden at production by the `item_document_trusted_basis_ck` CHECK. `manual` was enumerated as a production-capable basis from v3 onward and **nothing has ever written it** — so an untrusted-basis link had no terminal state at all. It could not be published, could not be refused, and sat `staged` indefinitely under a `production` document, indistinguishable from one nobody had looked at yet. Approving the document did not and could not change that, which reads to a reviewer as the decision being ignored.

Three new decisions, each scoped to **one** `(doc_id, item_ref)` pair and none of them touching `document`:

| decision | precondition (link) | effect | audit |
|---|---|---|---|
| `confirm-link` | `staged` | `match_basis → 'manual'`, `status → 'production'` | `link-confirmed` |
| `reject-link` | `staged` | `status → 'rejected'`, `match_basis` unchanged | `link-rejected` |
| `reopen-link` | `rejected` | `status → 'staged'`, `match_basis` unchanged | `link-reopened` |

`confirm-link` is the **sole writer** of `match_basis = 'manual'`; the basis means "a named person asserted this coverage", and nothing else may claim it. `reject-link` leaves the basis alone so the trail still records *how the link was proposed* before it was refused. All three share one document precondition and each carries its own link precondition, and every one of them is a hard error rather than a silent skip: the document must already be `production` — confirming coverage for a document nobody vetted publishes an unreviewed reading through the side door, and re-opening a link under a rejected document would recreate exactly the staged-link-under-a-rejected-doc state the reject cascade exists to eliminate. On the link side, `confirm-link` / `reject-link` require `staged` (`production` is an idempotent no-op per link-provenance-immutability) and `reopen-link` requires `rejected`. `retracted` is never reachable by any of the three: it records "the current extraction stopped claiming this", which is a statement about evidence, not a decision a person may overturn — the route back is a re-extraction that re-proposes the link. Because `confirm-link` overwrites the basis, the audit entry carries the **pre-decision `match_basis`** in `detail`: the row no longer holds it and invariant 10 requires the trail to stand alone.

**Required companion fix — `rejected` becomes sticky on link upsert.** `_upsert_link`'s `ON CONFLICT` treats only `production` as immutable, so a later candidate re-proposing the same pair rewrites a `rejected` row back to `staged` and resurrects a coverage claim a human refused. `rejected` joins `production` in that CASE. `retracted` deliberately does **not** — that is a machine state recording "the current extraction stopped claiming this", and new evidence reviving it is correct. *(The hole predates C17: `gate.apply reject`'s link cascade has written `rejected` links since v3, and any later candidate for the same pair has silently un-rejected them.)*

`reopen-link` exists **because** of that stickiness, not in spite of it (Denis's ruling, 2026-08-24). Making `rejected` terminal for machines closes the resurrection hole but would also make a reviewer's mistake permanent: a coverage map arriving a week later can prove the refusal wrong, and with no verb the only remedy is a hand-written UPDATE, which is an unaudited registry write by definition. So the asymmetry is deliberate and is the whole point — **a machine may never move a link out of `rejected`; a named person always may.** Every such move is its own `link-reopened` audit event, and the reopened link lands back in `staged`, where it must be confirmed again on the merits rather than springing straight to production.

**Why the answer cannot be automated, and what can.** `fetch-context` is by construction the residue left after every automatic route has already failed: it means the document names this item nowhere, and we hold it only because a fetch for this group returned a hash we already had (§6). Each of the four routes that *could* answer the question already runs and already wins — a re-extraction that recovers the REF list (`ref-list` / `ref-item`), a manufacturer coverage map (`map-supplier`), a Basic UDI-DI, or a manufacturer binding (`mfr-scope`) — because `_upsert_link` takes the newer basis on any non-production row, so an upgraded basis promotes on the next gate pass with no human involved. If a machine could settle it, one of those four would have. Auto-promoting on "a sibling in the same `item_group` matched by REF" was considered and **rejected**: `item_group_member.match_basis` admits name-family membership, so the rule would launder name similarity into production coverage through a second hop — precisely what C5 exists to prevent. What is automatable is the *ask*, not the answer: one review row per document carrying N checkboxes rather than N rows, and never surfacing a link whose document is not yet production.

**Measured queue (2026-08-24, 8.563 links).** 280 links are `staged`. **279 of them carry a trusted basis and are waiting on the DOCUMENT, not on a link decision** — `map-supplier` 260, `ref-list` 10, `ref-item` 9 — and `_promote_pending_links` publishes them the instant `approve` runs. Exactly **one** is a link-level question (`fetch-context`, item 2222100 / doc 843). C17's queue is the residue and the residue is 0,01% of links; it is a correctness hole, not a workload. *(17 further `map-supplier` links sit `staged` under a `superseded` document. Investigated and found **correct, not stalled** — the finding first reported here was withdrawn the same day. Docs 375 and 379 are older DoCs the task-5 fast path auto-filed into the `375/379 → 415 → 377` chain: each was `filed` first — C15 requires an empty `links` payload, so those links cannot have existed yet — and reached `superseded` on a later rev whose coverage-map payload carried them. **Neither document was ever `production`.** Their links are `staged` because their document is not published, which is the consistent state and the one the visibility rule demands; `_staged_links_on_production` filters `d.status = 'production'`, so they were never in a review surface or a review count either. Promoting them would assert production-grade coverage under paperwork the registry never published — retroactively manufacturing history. **Ruling (Denis, 2026-08-24): no status change; surface them read-only** so terminal dead state is legible rather than merely absent.)*

**Why the registry carries two staged statuses (the question the client will ask).** `document.status` and `item_document.status` answer different questions and are decided a different number of times:

- `document.status` — *did we read this paper correctly?* Type, regulation, dates, certificate number. Decided **once per document**, however many items it touches.
- `item_document.status` — *does this paper apply to this article?* Decided **once per (document, item) pair**.

They are orthogonal in both directions: a flawlessly-read certificate can be attached to the wrong article, and a badly-read one can be indisputably about the right article. Collapsing them into a single status costs one of two things, and both are worse than the extra column. Document-led, approving the reading would publish every coverage claim proposed alongside it, name-similarity stragglers included — the false-coverage failure the entire REF gate exists to prevent. Link-led, one doubtful straggler would hold a correctly-read DoC back from the 1.150 items that matched it by REF. The deciding argument is what the claim actually is: a regulator or a customer reads "item X is covered by document Y", not "document Y is good" — so the coverage claim is the thing that needs its own decider, its own timestamp and its own audit row.

**Human decisions:** `gate.apply` `{doc_id, decision: approve | reject | bind-manufacturer | confirm-link | reject-link | reopen-link, decided_by, edits?, item_ref?, note?}` — *`note?` added 2026-09-11 (office UI redesign P1a, spec `docs/superpowers/specs/2026-09-11-office-ui-redesign-design.md` § 3): the reviewer's reason, a string `"<reason>"` or `"<reason>: <free text>"`. Optional and additive, so every consumer tolerates its absence, and the dedupe key stays `apply:{doc_id}:{decision}`. The web producer requires one of six fixed reasons on `reject` (`REJECT_REASONS` in `web/app.py`: Wrong manufacturer · Not one of our items · Not a compliance document · Out of date · Duplicate of another document · Other), caps the free text at 500 characters (`REJECT_NOTE_MAX`), and sends it on no other decision. The handler writes it into the decision's own `audit_log.detail` (merged into a link decision's existing detail) and nowhere else, never onto the cascaded `link-rejected` rows, and the Decisions page (`/audit`) shows it. A payload without it writes exactly what it did before.* — *`edit` was listed as a seventh decision through v3 and has never been one: `handle_gate_apply` raises `ValueError` on it and the web producer 422s anything outside the six above. Edited fields ride along with `approve` in the optional `edits` dict, which the next sentence already describes. Corrected 2026-08-25.* — `item_ref` is required by, and only by, the three link-level decisions (C17), which act on that one link and leave `document` untouched. Edited values get tier T3 evidence, verbatim = human input, confidence 1.0. Reject may enqueue `discover.group` at interactive priority. `approve` also applies any pending C6 supersession: `document.supersedes` (staged sticky by `gate.candidate` regardless of disposition, so it survives the review period) is read and, if present, the target document is set to `superseded` — a staged candidate never supersedes anything on its own; only a human `approve` or a candidate that lands directly on production does. *(Persistence path shipped 2026-07-31, closing `[gate-c6]`.)*

## 7b. EUDAMED — certificate register & per-manufacturer sweep

**EUDAMED holds zero declarations of conformity.** Everything in this section
can tell us a certificate exists, that its standing changed, or that a device is
registered — it can never supply the document. Both job types below are leaves:
they write side tables only, never `document` / `item_document` / `evidence`
(invariant 1), and neither emits a next job. What they produce is a request list
or an alert for a person, never evidence and never a registry write.

| | |
|---|---|
| **Consumes** | `eudamed.certregister` — payload `{}`. Pulls the whole EU certificate register (16 calls at `size=300`, ~4.608 rows) and attributes each record's `actorSrn`/`actorName` to a canonical manufacturer locally (pg_trgm → rapidfuzz, never a per-manufacturer query — §6.5 of the design spec measured that route at 384 gated substring queries against 16 unfiltered pages, and a substring `actorName` match would silently miss any registered legal name not containing our canonical string). |
| **Emits** | **Nothing.** A leaf. |
| **Writes** | `eudamed_certificate` (one row per certificate revision) and `manufacturer_srn` (`status='auto'` on an exact name match, `'pending'` on a fuzzy one — the SRN trust rule below). Also upserts `manufacturer` rows for any canonical name discovered that the one-shot migration seed missed. |
| **Produced by** | SCHEDULER, monthly, config-gated off by default (`SCHEDULER_EUDAMED_CERTREGISTER_ENABLED`) — see §8's SCHEDULER row. Never a human action; this is the "bulk download" the 2026-08-20 no-unattended-sweep ruling prefers over a per-manufacturer query. |
| **AI** | None. Name matching is rapidfuzz, not a model (invariant 12). |

| | |
|---|---|
| **Consumes** | `eudamed.sweep` — payload `{manufacturer: canonical_name}`. Walks one manufacturer's entire registered device catalogue via every trusted SRN on file (`srn=<SRN>`, paged at `size=300`), or bootstraps an SRN via the article-probe fallback first when the manufacturer holds no certificate (GC EUROPE: 356 device articles, zero certificates). **This fallback branch exists in `handle_eudamed_sweep` but, today, is reachable only when the job is enqueued directly (CLI) — see Produced by.** |
| **Emits** | **Nothing.** A leaf. |
| **Writes** | `eudamed_mirror` (device rows, keyed on `udi_di`) and `eudamed_sweep_state` (`last_swept_at`, clearing `due_at`/`released_at`/`released_by`). |
| **Produced by** | No scheduler and no handler ever enqueues `eudamed.sweep`. The scheduler's own tick only marks a manufacturer's sweep *due* (`eudamed_sweep_state.due_at`, quarterly staleness threshold, `SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS`) — restoring the 2026-08-20 ruling "no sweep ever runs unattended, an operator releases every run" after a first design draft proposed a self-emitting monthly tick that contradicted it. The web UI's release button (`POST /manufacturers/{canonical_name}/sweep`) is its only operator-facing producer, and `web/registry.py`'s `sweep_release` refuses it with 400 when `trusted_manufacturer_srn` holds nothing for that manufacturer — the exact condition the article-probe fallback above exists to overcome, since a manufacturer that already has a trusted SRN (from `eudamed.certregister`) never needs the probe. **GC EUROPE is therefore not yet operator-reachable**: 356 device articles, zero certificates, no trusted SRN, so the release button 400s before a job is ever enqueued, and the probe fallback above can only run when `eudamed.sweep` is enqueued directly, e.g. `python -m app.cli enqueue eudamed.sweep "eudamed.sweep:GC EUROPE" --payload '{"manufacturer": "GC EUROPE"}'` — an engineer action, not an operator one. Closing this gap (making the probe reachable from the UI) is a follow-on, not shipped here. |
| **AI** | None. |

**The SRN trust rule, stated once.** Only `manufacturer_srn` rows with
`status IN ('auto', 'confirmed')` are ever swept — `pending` (a fuzzy name match
awaiting a human at `/manufacturers/srn-queue`) and `rejected` are never trusted,
because attribution is a name match and a wrong one sweeps another company's
registered catalogue under our manufacturer's name — a mis-attribution that
eventually surfaces as a wrong request sent to a supplier. `auto` is written
only on an exact normalised name match; everything else waits in the confirm
queue until a person decides.

## 7c. BC-PUSH — three compliance fields written back into Business Central

Added 2026-09-07. Not a pipeline stage: it consumes no stage's output and emits
nothing. It reads the registry and writes a system we do not own.

| | |
|---|---|
| **Tag** | `bc.push` |
| **Produced by** | REVIEW-UI (item button · bulk apply) · SCHEDULER (drift cron) |
| **Payload** | `{run_id, item_refs: [...]}` — a batch, ~200 |
| **Logic** | Per `item_ref`: compute the three field values from the registry (`app/bc_fields.py`) → read the last **accepted** value per field from `bc_push_log` → PATCH `dataitems` with only the fields that differ → one ledger row per field sent. An item the pipeline has never processed is skipped and counted, never written. |
| **Emits** | nothing. A leaf, like `report.weekly`. |
| **Writes** | `bc_push_log` (migration 063) only. Never `document`, `item_document` or `evidence` — invariant 1 is untouched. |
| **Failure path** | A non-2xx counts `failed`, is recorded in the ledger, and is **excluded from the next run's diff**, so a refused value is retried rather than believed. One item failing does not stop the batch. Job-level failure takes the ordinary backoff and dead-letters. |
| **AI** | None. |
| **Gate** | `bc.write_enabled`, default **false**. Off, the handler computes and reports the diff (`would_send`) and sends nothing — that report is what the bulk preview reads. |

**The two booleans are basis-sensitive, and that is the contract.** `true`
requires a production document on a production link with
`coverage_scope = 'group'` — the manufacturer scope is not enough, because a
claim about a supplier is not evidence about an article. A passed date falsifies
only when `document_effective_expiry.basis` is `stated` or `inherited`;
`staleness` is Dentalia's own review horizon and never falsifies.
`pteValidCECertificate` takes `type = 'EC'` only, never `ISO`, and an adverse
EUDAMED certificate status falsifies it on its own.

**`allitems` is read-only and must never be a PATCH target.**

---

## 8. Peripheral producers (not pipeline stages)

| Unit | Emits | Note |
|---|---|---|
| SCHEDULER | `ingest.run`, `eudamed.sync` *(S2.3-deferred — no producer exists yet; listed here as target state)*, `eudamed.certregister` (monthly, carved out of the no-unattended-sweep ruling below — a 16-call whole-register pull is the "bulk download" that ruling prefers, not the per-manufacturer sweep it forbids; §7b), `email.request` (expiry horizon scan), `playbook.reonboard` (failure-rate spike), `report.weekly`, `email.poll` (inbound bucket), `bc.push` (drift window, §7c — gated on `bc.write_enabled`, so no job is created while the writeback is off) | Owns crons only, zero business logic — carried since 2026-08-31 by perpetual `scheduler.tick` jobs rather than a separate process — ten of them as of 2026-09-07 (§0). The rule is unchanged: the tick handler calls the same `_tick_*` functions and writes nothing but `scheduler_run`. `email.request` / `playbook.reonboard` emission is config-gated **off** by default (`SCHEDULER_EXPIRY_EMAIL_ENABLED` / `SCHEDULER_FAILURE_REONBOARD_ENABLED`) until their S2.4/S2.1 consumers exist — same producer-side gating as DISCOVER's email rung (§3). `eudamed.certregister` is gated off by default the same way (`SCHEDULER_EUDAMED_CERTREGISTER_ENABLED`), for an operator to enable deliberately rather than for a missing consumer. **The scheduler also marks a manufacturer's device sweep due (quarterly) without ever emitting `eudamed.sweep`** — it writes `eudamed_sweep_state.due_at` only; §7b. |
| EMAIL-OUT | renewal state machine: `due → requested → awaiting → received → parsed`; reminder/escalation branch | `draft-for-approval` config flag; closes when inbound reply's doc reaches production and supersedes |
| REVIEW-UI | `gate.apply`, `interactive`-priority re-runs | One page, three tabs: staging / manual / dead jobs. Staging tab now shows both staged documents and staged **links** on production documents (C5), plus manufacturer-binding candidates (C4). |
| WEB-UPLOAD | `upload.ingest` — payload `{upload_id, manual_task_id?}`; dedupe_key `upload:{upload_id}` | `/upload` route, producer-only (INSERT + sequence USAGE on `upload_inbox`, no SELECT — worker owns SELECT/DELETE; never the registry). Handler (spine-side, not this unit) emits `extract.doc`/`validate.doc` — see §4's alternate-producers table. `manual_task_id` (optional) lets a `/manual` discovery-dead-end link straight to upload; the handler resolves that task worker-side, `dentalia_api` being SELECT-only on `manual_task`. |

---

## 9. End-state data model

The two outputs, plus their support tables. Canonical here.

### Core (the deliverable)

**`document`** — `doc_id, type (DoC|EC|IFU|ISO|SPP|other), regulation (MDR|MDD|n.a.), validity_from, validity_to?, coverage_scope (group|manufacturer), basic_udi_di?, cert_number?, stated_class?, canonical_manufacturer?, content_hash, source_url, archive_url, referenced_doc_id?, cert_doc_id?, superseded_by?, status (staged|production|filed|rejected|superseded)` *(`filed` new — C15)*

`canonical_manufacturer text NULL REFERENCES manufacturer(canonical_name) ON UPDATE CASCADE` (migration 053, 2026-08-31) — the **confirmed** manufacturer, and the only direct `document → manufacturer` edge in the schema. Until it existed a document reached a manufacturer solely by fanning out to catalogue items (group-scope through `item_group`, manufacturer-scope through `mfr-scope` links), so a document with **no item links reached none at all** — 540 documents on 2026-08-31, of which 443 were `filed`, i.e. every filed document by definition (C15 files exactly what we stock nothing for, and the link path runs through what we stock).

Explicitly **not** §201's extracted `manufacturer`, which is the legal entity *as printed*, carrying a confidence and a verbatim. That is a reading; this is a decision. The evidence row remains the provenance for it, and invariant 2 is not engaged — the binding is not a production *value*, its trail is the audit entry.

Written at three points, all inside GATE (invariant 1 unchanged; `dentalia_api` holds `SELECT` only on `document`): `gate.apply bind-manufacturer` (human), C16's machine bind (`_handle_mfr_binding`, on every disposition it reaches including `filed`), and the ordinary group-scope path — the last **only** when `resolve_canonicals` returns exactly one canonical, since guessing between two would attach one company's document to another's products. Sticky on conflict (`COALESCE(EXCLUDED, document.…)`), same class as `supersedes` / `cert_doc_id` / `source_url`: a re-delivery that cannot recompute it must not erase a decision a human made, while a newer non-null binding still wins, which is what makes re-binding the correction path.

Keyed by name rather than a surrogate id because every other manufacturer attribute in the schema is (`item_group.canonical_manufacturer`, `manufacturer_srn.canonical_name`, `eudamed_sweep_state.canonical_name`), and cascading on rename for migration 052's stated reason: a rename does not invalidate the attribution, it renames the thing described. `ON DELETE` stays `NO ACTION`. Null means **undecided**, never "no manufacturer".

**C4 is unchanged in meaning** — it already reads "one-time human binding approval, then automatic link derivation"; this stores the input that derivation never had a home for. Consequences elsewhere: `regroup.rename_impact` gains a `bound_documents` count (the cascade rewrites rows the rename preview could not previously see), and `web.catalogue.manufacturer_documents` takes `include_unlinked` (default **off** — a filed document is real and evidenced but explicitly not coverage, so it must not appear in an existing consumer's payload unasked).

`stated_class text NULL CHECK (stated_class IN ('I','Is','Im','Ir','IIa','IIb','III'))` (migration 032, 2026-08-21) — the risk class the document itself states. QA/display only: never scored, never gated, never written to `item_mirror` (BC stays the source of truth for item class; §5).
Supersession constraint (C6): `superseded_by` may only reference a document with identical `(type, regulation)` and overlapping coverage subject.
`cert_doc_id` (migration 020) is distinct from `referenced_doc_id`: it is the notified-body certificate a DoC cites (Annex IV item 8), never a cross-regulation sibling. MDR Annex IV requires only a date of *issue* on a DoC, so most carry no expiry; Article 56 caps a notified-body certificate at five years, so a DoC's real renewal date is inherited from the certificate `cert_doc_id` points at. Best-effort and null-safe: a certificate we do not hold resolves to null and is flagged, never an error. Sticky on GATE upsert (`COALESCE(EXCLUDED.cert_doc_id, document.cert_doc_id)`), same as `referenced_doc_id`, and back-resolved when the certificate itself is later gated, so citation order never determines whether a DoC ends up with an expiry.

**`item_document`** — `item_ref (BC id), doc_id, udi?, product_class (mirrored, BC = source of truth), match_basis (ref-list|ref-item|map-supplier|udi|basic-udi-di|name-family|fetch-context|ref-catalogue|mfr-scope|manual), status (staged|production|rejected|retracted)` *(status new in v3 — C5; `mfr-scope` new — C4. **Both enumerations corrected 2026-08-25**: `ref-item` (2026-08-13, C12, migration 023) and `map-supplier` (2026-08-19) were added to §7's basis table but never to this line, so §9 contradicted §7 on which bases exist; `retracted` was added by migration 024 and records that the current extraction stopped claiming the link — see C17 for why no machine may move a link out of `rejected` but a re-extraction may revive a `retracted` one.)*
Visibility rule: consumers join on `document.status = 'production' AND item_document.status = 'production'`. The two statuses are decided separately and a different number of times — see C17 for why collapsing them is not an option. `match_basis = 'manual'` has exactly one writer, `gate.apply confirm-link`.

**`evidence`** — per written field: `field, value, archive_url, page, verbatim, tier, model_id?, confidence, extracted_at`

**Audit log** — append-only: every production write, gate decision, human action, binding, supersession. **(C8)** `via_job` is a soft reference; each entry embeds `job_snapshot jsonb` (type, payload, dedupe_key, claimed_by, timestamps) captured at write time. Queue rows may be pruned on any schedule without touching the 10-year audit trail.

### Support

**`item_mirror`** — normalized BC snapshot (diff base for INGEST); **includes `mfr_ref` (C1)**
**`item_group`** / **`item_group_member`** — group membership + basis; membership rows carry `mfr_ref`; group exposes `member_mfr_refs` (C1)
**`fetch_log`** — `url_normalized, etag, last_modified, content_hash, fetched_at, last_checked_at, source (live|backfill|email), doc_id?`
**`batch_ref`** — `content_hash, tier, batch_id, submitted_at, resolved_at?` — Batch API dedup/crash-safety (C9)
**`manufacturer_alias`** — raw name → canonical manufacturer (RESOLVE dependency, Phase 1)
**`manufacturer`** — the entity: contacts, hit-rate stats, quirks — **plus `binding_doc_ids` view derived from mfr-scope bindings (C4)**. Since migrations 049/050 (2026-08-27) it also carries `slug`, `body jsonb` and `playbook_rev`: **the playbook store**. `playbook_ref` is historical — it named a path in a playbook git repo and was never written; that intent is inverted, the database is authoritative and `playbooks/*.json` is the authoring record imported by `dentalia manufacturers seed`.
**`manufacturer_bc_code`** / **`manufacturer_name`** (049) — the identity half of a playbook, shredded into constrained tables so that `playbooks.validate()`'s checks become a primary key each, and a code BC never issued becomes a foreign-key violation. `manufacturer_bc_code` is the LINK to `vendor_master`, which stays a pure BC mirror and is never written from here.
**`manufacturer_playbook_revision`** (050) — every edit to a body, append-only. `extraction_attempt` has carried `(playbook_slug, playbook_rev)` since 031, so a revision that cannot be read back makes those two columns unusable; revert writes the old body FORWARD rather than deleting.
**`playbook_epoch`** (050) — single-row cache watermark for the loader, statement-trigger bumped. Replaces the file loader's `scandir` signature in VALIDATE's per-group hot loop.
**`refused_host`** (051) — hosts we have RULED we may never fetch. Not what a host's robots.txt says (`app/robots.py` reads that at fetch time); this is an operator decision that survives a permissive robots.txt, which is why both exist.
**`renewal_request`** — Phase 2, per EMAIL-OUT state machine.

**No job type is added by any of this.** The playbook store changes where a
handler reads its rules from, never the enqueue graph: `app/playbooks.py` keeps
`load_playbooks` / `for_manufacturer` / `validate` / `clear_cache` unchanged, and
nothing downstream can tell which store answered. The closed `job_type` enum
(Invariant 7) is untouched.

### Storage layout (unchanged)

```
/archive/{manufacturer_canonical}/{doc_type}/{content_hash}__{original_filename}.pdf
```

- Hash prefix guarantees uniqueness and dedupe; original filename preserved for humans.
- One physical file regardless of how many items link to it.
- Behind StorageAdapter: Drive v1 (client-owned), S3 v2 (immutability story). Layout convention identical across both.
- 10-year retention, append-only, never delete — supersession is a DB relation, not a file operation.

---

## 10. Drift guards

- A stage may only emit the job types listed in its **Emits** row. New job types = PRD change first. *(C3 deliberately reuses `validate.doc` rather than adding a tag.)*
- No stage writes `document`/`item_document` except GATE.
- No stage calls another directly — queue only.
- Job payloads are immutable after enqueue (C9); long-running external state lives in side tables.
- Payload fields are additive-only once Phase 1 starts (consumers tolerate unknown fields, never missing ones).
- Any value in production without complete evidence is a bug by definition, regardless of correctness.
- Any `item_document` link at production status whose `match_basis ∈ {name-family, fetch-context, ref-catalogue}` is a bug by definition (C5).
- Any `item_document` row at `match_basis = 'manual'` with no `link-confirmed` audit entry naming its decider is a bug by definition (C17) — the basis asserts a person, so a person must be on record.
- Any link that moves out of `rejected` other than through `gate.apply reopen-link` is a bug by definition (C17): a machine candidate must never resurrect coverage a human refused.
- Any `superseded_by` crossing `type` or `regulation` is a bug by definition (C6).
- Audit entries must be self-contained (C8): deleting every row of `job` must leave the audit trail fully interpretable.
- Queue-row deletion/archival is an operational choice with **no correctness impact** — dedup scoping (C2) and audit snapshots (C8) both hold without historical job rows.

## 11. Config surface

Unchanged from v2 plus: `batch.poll_interval` · `mfr_binding.auto_link` (default on; per-manufacturer overridable — a paranoid manufacturer can be set to per-group review even after binding).

Full list: `gate.threshold.high/med` (0.95/0.75 proposal) · `source_adapter` · `storage_adapter` · `search_adapter` · `models.t1/t2/rank` · `renewal.horizon_days` · `email.send_policy` · per-domain politeness/recency · sweep + monthly budget caps · per-manufacturer source-priority list (data).

`discover.hold` (env `DISCOVER_HOLD`, default **off**) — the corpus-first cold start. RESOLVE necessarily runs before BACKFILL (it builds the groups a `group_id`-null backfill document resolves against, §5), so at first ingest the §2 "already has a current doc or staged candidate" suppression cannot fire and every group would web-search for documents already sitting in the local corpus. With the hold on, RESOLVE builds groups and emits **no** `discover.group`; the corpus is mined; discovery then runs against a registry that knows what it already has. Held emissions are counted on the job result as `discover_held` — distinguishable from the settled §2 suppression, which leaves it 0. Reversible by construction: the `discover:{group_id}:{cycle}` dedupe key uses the constant cycle and dedupe is active-scope only (§0/C2), so `cli regroup --apply` after dropping the hold re-enqueues `resolve.group` and discovery fires for exactly the groups the corpus did not cover. Steady state never sets it.

## 12. Phase 0 verification hooks (new in v3)

Three of the fixed defects were invisible in single-pass testing. Phase 0/1 must include:

1. **`mfr_ref` source identification** — diff the BC sample export's item No. and vendor item no. columns against the sample documents' REF lists. Answers three questions in one pass: which BC field holds the manufacturer article number (fixes the adapter mapping), whether it's consistent across suppliers or mixed, and whether bare numbers collide across manufacturers in practice. The `missing_mfr_ref` rate from this check directly sets the achievable AC1 ceiling — the single highest-value data point in Phase 0.
2. **Two-cycle simulation** — run the pipeline twice over the same corpus with a simulated month gap: verifies C2 (re-enqueue works), C3 (no re-discovery of hash-deduped groups), AC5 (second cycle cost ≈ 0 on unchanged content).
3. **QMS-cert path test** — Ivoclar's manufacturer-level certificate through the C4 binding flow: exactly one review entry, N derived links after approval.

## 13. Document map

Architecture & rejected options → `dentalia-mdr-pipeline-ground-truth.md` · workflow decisions & EUDAMED → `dentalia-workflow-structured-v2.md` · queue mechanics & units detail → `dentalia-v3-v4-system-design.md` · tooling → `dentalia-etl-stage-alternatives.md` · **contracts → this document (v3)** · subtasks & tests → per-phase unit specs.

**Follow-on edits required in companion docs** (not made here): schema sketch — `item_mirror.mfr_ref`, `item_group_member.mfr_ref`, `item_document.status`, `batch_ref` table, partial unique index on `job.dedupe_key`, `audit_log.job_snapshot`; job-type handbook — §4 outcome table, §6 rules, §7 dispositions, new §7b binding flow.
