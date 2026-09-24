# Handlers — as-built reference

**Status:** live. Measured against the tree at 2026-08-31.

A navigational index over the 20 queue tags. The **contract** lives in
[dentalia-pipeline-contract-prd-v3.md](../dentalia-pipeline-contract-prd-v3.md)
and the pseudo-code in
[dentalia-job-type-handbook.md](../dentalia-job-type-handbook.md); this file only
tells you where the code is and what it actually does today. On any disagreement
the PRD wins and this file is the bug.

`job_type` is a closed Postgres enum (`migrations/001_queue.sql:11-25`, extended by
`ALTER TYPE ... ADD VALUE` in `013`, `014_upload`, `041_eudamed_job_types` and
`041_vendor_import`). Registration is a plain dict, `app/handlers/__init__.py:16-23`;
`app/handlers/noop.py:90` seeds it with `HANDLERS.setdefault` so import order can
never clobber a real handler.

## Index

| Tag | Entry point | Emits | Tests |
|---|---|---|---|
| `ingest.run` | `handlers/ingest.py:118` | `resolve.group` | `test_ingest_handler.py` |
| `resolve.group` | `handlers/resolve.py:278` | `discover.group` | `test_resolve_handler.py` |
| `discover.group` | `handlers/discover.py:908` | `fetch.url` ×k, `email.request`, `validate.doc` | `test_discover_handler.py` |
| `fetch.url` | `handlers/fetch.py:145` | `extract.doc` or `validate.doc`, **or nothing** when the body is not a document | `test_fetch_handler.py` |
| `extract.doc` | `handlers/extract.py:278` | `validate.doc`, **or nothing** when the doc-class call answers `not-a-compliance-document` | `test_extract_handler.py`, `test_extract_cost.py`, `test_doc_class_gate.py` |
| `validate.doc` | `handlers/validate.py:780` | `gate.candidate` | `test_validate_handler.py` |
| `gate.candidate` | `handlers/gate.py:1002` | nothing (terminal) | `test_gate_candidate_handler.py` |
| `gate.apply` | `handlers/gate.py:1443` | `discover.group` on reject | `test_gate_apply_handler.py` |
| `backfill.scan` | `handlers/backfill.py:270` | `extract.doc` | `test_backfill_handler.py` |
| `upload.ingest` | `handlers/upload.py:33` | `extract.doc` or `validate.doc` | `test_upload_handler.py` |
| `vendor.import` | `handlers/vendor_import.py:40` | nothing, deliberately | `test_vendor_import_handler.py` |
| `email.poll` | `handlers/email_poll.py:361` | `extract.doc` | `test_email_poll_handler.py` |
| `email.request` | `handlers/email_request.py:858` | `email.reminder` | `test_email_request_handler.py` |
| `email.reminder` | `handlers/email_request.py:1025` | nothing; defers its own job to the next rung | `test_email_request_handler.py` |
| `eudamed.sync` | `handlers/eudamed.py:226` | nothing | `test_eudamed_handler.py` |
| `eudamed.certregister` | `handlers/eudamed.py:424` | nothing | `test_eudamed_handler.py` |

**Running the register by hand.** `POST /scheduler/run/eudamed.certregister` (the **Run now** button on `/scheduler`) enqueues one pull with the cron's own empty payload and dedupe key, so a hand-run and the cron collapse into one job. This is currently the ONLY way the register refreshes: `eudamed_certregister_enabled` is off by default, so the `eudamed.certregister` cron tick fires and does nothing. (The reason used to be that the `scheduler` service sat behind compose profile `full`; that service was deleted 2026-09-02 and the crons run on the queue, so the flag is now the whole story.) Everything on `/expiry` that reads `certificate_drift`, `certificate_drift_candidate`, `certificate_status_alert` and `certificate_gap` is therefore as old as the last hand-run.
| `eudamed.sweep` | `handlers/eudamed.py:730` | nothing, never self-emits | `test_eudamed_handler.py` |
| `report.weekly` | `handlers/report.py:156` | nothing | `test_report_handler.py` |
| `scheduler.tick` | `handlers/scheduler_tick.py:91` | itself, forever (`queue.defer`) | `test_scheduler_tick.py` |
| `playbook.reonboard` | `handlers/playbook_probe.py:86` | **nothing** — the only `app.crawl` consumer that emits no `fetch.url` | `test_playbook_probe.py`, `test_playbook_probe_web.py` |
| `bc.push` | `handlers/bc_push.py:106` | nothing — a leaf, like `report.weekly` | `test_bc_push.py`, `test_bc_fields.py` |

## Per tag

Five fixed lines each: **consumes** (payload fields), **writes** (tables),
**emits** (with dedupe key), **fails how**, **note**.

### `ingest.run`
- consumes: `source`, `ref.upload_id` (upload source only), `catalogue`, `dry_run?`; priority from `job`, not payload (`ingest.py:122`)
- writes: `item_mirror` (`ingest.py:73-89`)
- emits: `resolve.group` per changed item, `resolve:{item_ref}:{rev}`, suppressed when `dry_run`
- fails how: generic. Bad rows are counted, not raised (`_skip`, `ingest.py:143`)
- note: the two-phase preview/apply path reads `import_inbox` and leaves the row; only apply consumes it

### `resolve.group`
- consumes: `item_ref`, `group_id?`, `force_new_group?`
- writes: `item_group`, `item_group_member`, `manufacturer_alias`, `grouping_suggestion`
- emits: `discover.group`, `discover:{group_id}:{cycle}`, suppressed if the group already has a current doc or `discovery.hold` is on
- fails how: T1 adjudicator failure is caught and stored as `t1_error` on the suggestion — never lose the suggestion. A forced missing `group_id` raises
- note: writes no registry tables at all

### `discover.group`
- consumes: `group_id`, `ignore_recency?`
- writes: `discovery_log`, `manual_task`
- emits: first rung to hit stops the ladder — `fetch:{normalize_url(url)}` ×k, or `email.request`, or `validate.doc` via `archiving.emit_validate` on the recency rung
- fails how: `SearchNotConfigured` and ranker failure degrade to a logged miss; an unknown rung name raises (misconfiguration must be visible); a crawl index page that will not fetch is a logged miss too, never a dead-lettered job — a manufacturer's site being briefly down must not kill the group's discovery
- note: `manual` and `vendor` are terminal rungs with no emit
- note: the `playbook` rung is the only one that makes an outbound HTTP request of its own and **the only place DISCOVER takes a domain lease** (`queue.try_domain_lease`, and it self-defers the whole job when the lease is held). It reads `robots.txt` through `app/robots.py` before touching an index page — the one unauthored request the pipeline makes, and it exists solely to ask permission — then harvests links with `app.crawl.harvest`. Authored `kind:"direct"` sources and a `crawl` block are **additive**: direct is emitted first (a hand-verified document outranks a harvest), the crawl adds to it, and a crawl miss does not cancel a direct hit. **Authored exclusions are honoured as of 2026-09-04** (`_excluded_by_type_rule`): a `doc_type_from` needle mapped to `null` drops its links BEFORE `_emit_fetch`, so an excluded family costs a log line and nothing else -- no request to the manufacturer, no archive row, no extraction. Until then the key was read by the PROBE alone and a recipe's exclusions changed the operator's screen and nothing else (NSK: 446 safety data sheets, ~$20). The POSITIVE half is deliberately still inert -- T0/T1 type a document from its content, and overriding that with a substring of a URL would be the opposite of invariant 2. A crawl whose every link is excluded is a HIT with `emitted: 0`, never a miss: logging a miss would send the ladder on to search for documents we just decided we do not want. The rung logs one `discovery_log` row carrying the full count breakdown (`anchors_seen`, `links_matched`, `links_capped`, `off_host`, `pages_fetched`, `emitted`, `deduped`, plus `excluded` and per-needle `excluded_by` when anything was dropped), so a crawl that took 3 links off a 40-document page is visible as such rather than as a quiet success.

### `playbook.reonboard`
- consumes: `slug`, `index_url`, `link_pattern?`, `same_host_only?`, `allow_hosts?`, `max_links?`, `doc_type_from?`, `requested_by?`
- writes: `playbook_probe`
- emits: **nothing** — the only consumer of `app.crawl` that produces no `fetch.url`. An operator has to be able to LOOK at a manufacturer's library without committing the pipeline to fetch it; NSK's index alone is 1.329 PDFs.
- fails how: a robots refusal and an unreachable site are both RESULTS, written to the row as `refused`/`failed` — a manufacturer's site being down is something the operator needs to see, not a dead-lettered job. A held domain lease defers the job with the row left `pending`.
- note: it is a fetch, so every fetch rule binds — the `robots_refused.txt` authoring guard, the live `robots.txt` read, and the domain lease, in that order. It writes NO fetch-ledger row and NOTHING to the archive: that is why it is not a `fetch.url` with a flag, since a ledger row would make invariant 6 treat the index URL as already fetched and silently suppress the real crawl later.
- note: **browser escalation since 2026-09-04**, on the SAME trigger the crawl rung uses (§4.2): zero ANCHORS, never zero matches. A page with anchors that matched none of them has a wrong `link_pattern`, and a browser renders the same anchors and matches none again — 30s to reprint the same number. A page with NO anchors is the one that might be a JavaScript shell. Until then `tier` was hardcoded `"static"`, so the column that exists to make "this portal needs a browser" *measured* could only ever say one thing, and `_probe.html` — which has read `probe.tier` since S2.2 — could never print "Read with a browser" (`[probe-browser-tier]`). A render fault degrades to the static answer and `tier` STAYS `"static"`: claiming `"rendered"` would tell the operator we looked with a browser when we did not. A render that succeeds but still finds nothing reports `"rendered"`, because "we tried and there is nothing there" is a different answer from "we never tried". The renderer is constructed lazily on escalation (`PlaywrightFetcher()` does no I/O; only `.render()` launches a browser), so a probe of a normal page never builds one.
- note: the payload branches. `index_url` present means probe; the failure-monitor reonboard path is still unbuilt and RAISES from inside the handler rather than reporting success.
- note: `playbook_probe` rows are debris, not registry — a measurement of somebody else's website at a moment, never evidence. The 10-year append-only rule does not reach them.
- note: the UI half is three routes in `web/registry.py` — `POST /playbooks/{slug}/probe` (enqueues at `interactive` priority, dedupe `playbook.reonboard:{slug}:{normalize_url(index_url)}`), `GET /playbooks/{slug}/probe/{job_id}` (the HTMX partial, polling by `via_job` and re-arming only while there is no result), and `POST /playbooks/{slug}/crawl` (folds the recipe into the body through `save_playbook_body`, unchanged: optimistic lock, revision, revert). The web process enqueues and reads; it never fetches. One `playbook_probe` row per JOB, not per attempt — the runner commits a deferred handler's writes, so a plain INSERT left an orphan `pending` row behind on every politeness deferral.

### `fetch.url`
- consumes: `url`, `domain`, `group_id?`, `ignore_recency?`
- writes: `fetch_log`, `domain_lease`
- emits: `extract.doc` on new content, `validate.doc` on hash-dedupe or recency-link
- fails how: persistent bot-wall (httpx *and* Playwright) raises `FetchError`. **Lease contention is a voluntary self-defer** (`queue.defer`, `fetch.py:131`) and does not consume a retry
- note: the httpx-vs-Playwright tier choice is the handler's job, not the adapter's
- note: **robots.txt is read before the document request** (2026-09-11, `[robots-reader-unwired]`), after the recency skip and inside the lease, over the same httpx → Playwright ladder (`_RobotsTransport`). A refusal finishes the job as `robots-refused` with its `reason`; a transport failure raises `FetchError` into the backoff. `fetch.robots_ttl_hours` is passed here, the one caller that does

### `extract.doc`
- consumes: `content_hash`, `archive_url`, `group_id?`, `companion_archive_url?`
- writes: `extraction_attempt`, `extraction_cost`, `document_text`, `batch_ref`
- emits: `validate.doc`, `validate:{hash}:{rev}:{group_id}`
- fails how: an unopenable PDF is a counted, non-raising result and emits nothing. Batch mode self-defers per tier until the Anthropic batch is done
- note: batch state lives in `batch_ref`, never in the payload (invariant 9)

### `validate.doc`
- consumes: `content_hash`, `extract_rev`, `group_id?`, `archive_url?`
- writes: **nothing** — stateless by design
- emits: `gate.candidate` always, either `gate:{hash}:{rev}:mfr-binding` or `gate:{hash}:{rev}:{group_id}`
- fails how: missing extraction row or incomplete required fields are suppressed and counted, job finishes `done` with no emit
- note: every decision is an additive key on the emitted payload
- note: the extraction-shape flags (`ref-list-possibly-truncated`, `iso-without-standard-number`, both from `tiers.integrity_flags`) are computed BEFORE the C4 manufacturer-binding branch, so they ride BOTH routes. They sat after the binding return until 2026-09-09, which made `iso-without-standard-number` unreachable on exactly the population it exists for — a QMS certificate is manufacturer-scope by definition

### `gate.candidate`
- consumes: `content_hash`, `extract_rev` required; `group_id`, `ref_gate`, `flags`, `links`, `supersedes`, `related_doc_id`, `cert_doc_id`, `superseded_by_doc_id`, `archive_url`, `manufacturer`, `route` optional
- writes: `document`, `item_document`, `evidence`, `manual_task`, `audit_log`
- emits: nothing — terminal
- fails how: a stale `extract_rev` is a counted no-op. Missing extraction raises. Unusable evidence raises. An UNATTRIBUTABLE manufacturer bind raises rather than promoting nothing ([gate-bind-zero-links], `gate.py:1223`) — that is a name resolving to no BC *codes*. A name that resolves fine but whose items carry no MD flag is **not** a fault and never was: since migration 053 it is a legitimate bind that records the manufacturer and links nothing
- note: `TRUSTED_BASES` (`gate.py:47`) is what caps `name-family` / `fetch-context` / `ref-catalogue` at staged, backed structurally by the DB CHECK in migrations 005/021
- note: both GATE handlers set `document.canonical_manufacturer` (migration 053). It is the CONFIRMED manufacturer, not the printed name in evidence; null means undecided, never "none". Sticky under re-upsert via `COALESCE(EXCLUDED..., document....)`

### `gate.apply`
- consumes: `doc_id`, `decision`, `decided_by` required; `edits?`, `manufacturer?`, `group_id?`, `note?`, `item_ref` (required for the three link decisions). Document decisions: `approve`, `reject`, `bind-manufacturer`, `reopen`. Link decisions: `confirm-link`, `reject-link`, `reopen-link`
- note: `note` (2026-09-11, office UI redesign P1a) is the reviewer's reason, `"<reason>"` or `"<reason>: <free text>"`. It goes into the decision's own `audit_log.detail` (`_note_detail`, merged into a link decision's detail) and never onto the cascaded `link-rejected` rows. Optional: without it the audit row is what it always was. The review UI sends it on `reject` only, where one of `REJECT_REASONS` is required and the free text is capped at `REJECT_NOTE_MAX` (500)
- writes: `document`, `item_document`, `manual_task`, `audit_log`
- emits: `discover.group` on reject with a `group_id`, interactive priority
- fails how: every link-decision precondition raises rather than no-opping — a person put their name to it, so it dead-letters where somebody sees it. Idempotent re-delivery returns quietly
- note: `reopen` (2026-09-04) is the only way back out of `rejected` and the only document decision whose precondition is a STATUS — it raises unless the document is currently rejected. It exists because making `rejected` sticky in `_upsert_document` closed the hole where any re-delivery silently un-rejected a document, and that hole was also the only escape. It deliberately does NOT cascade the links back: `reject` cascades them to `rejected` so none is left staged under a rejected document, and re-staging them would recreate exactly that state. Each returns through `reopen-link`
- note: human edits are written as T3 evidence, `tier='T3'`, `model_id=NULL`, `confidence=1.0`, pageless (`gate.py:1133`)
- note: on `bind-manufacturer` the column is written BEFORE the links are derived, so a bind that links nothing still records the decision

### `backfill.scan`
- consumes: `drive_folder`
- writes: `fetch_log`
- emits: `extract.doc`, `extract:{content_hash}`
- fails how: raises `BackfillError` on a `skip_backfill` playbook, missing folder, zero PDFs, fully-excluded corpus, or an unreadable coverage-map index. Per-file `OSError` is counted
- note: never a silent zero-result

### `upload.ingest`
- consumes: `upload_id`, `manual_task_id?`
- writes: `fetch_log`, deletes the `upload_inbox` row, resolves a `discovery-dead-end` task
- emits: `extract.doc` or `validate.doc`
- fails how: a missing spool row is a normal result, not a raise — idempotent after a committed delete
- note: the web role holds INSERT on `upload_inbox` and nothing else

### `vendor.import`
- consumes: `upload_id`, `dry_run?`, `allow_renames?`, `filename` (apply only)
- writes: `vendor_master`
- emits: nothing, deliberately — it does not call `sync_aliases` as a side effect
- fails how: wrong spool `kind` raises. `RenameRefused` is reported as `outcome: "rename-refused"`, not raised — expected, not exceptional

### `email.poll`
- consumes: nothing from the payload
- writes: `email_poll_log`, `fetch_log`
- emits: `extract.doc` per new attachment
- fails how: `EmailNotConfigured` is reported as `not-configured`, not a failure. The zip/archive refusal taxonomy is all counted
- note: **read-only on the server** (2026-09-24). The adapter opens the folder with `EXAMINE`, fetches with `BODY.PEEK[]`, ends with `LOGOUT` and never `CLOSE`, and sends no `STORE`, `EXPUNGE`, `MOVE` or `COPY`; `tests/test_email_adapter.py` records the commands and asserts it. It reads mail that arrived on or after `email.poll_since` (required once configured, else `ValueError`), filters UIDs already in `email_poll_log` before downloading, and takes at most `email.poll_max_messages` per poll, oldest first, counting the rest as `deferred_to_next_poll`. Nothing is marked on the server: Exchange's IMAP stores no custom keywords, and the standard flags belong to the people reading the mailbox
- note: idempotency comes from the durable `email_poll_log` UID guard, not IMAP `\Seen`. The one LLM call is body summarisation — UI context only, never evidence (invariant 12)
- note: **reply matching** (2026-09-11): `_match_request` links a message to a `renewal_request` and stores it on `email_poll_log.renewal_request_id` — by the `[DENT-{id}]` token every draft of a chase carries in its subject (`email_request.subject_for`), else by sender domain when it is a contact domain of exactly one manufacturer with an open request. The polled mailbox's own domain never matches. Counted as `reply_linked_by_reference` / `reply_linked_by_sender` / `reply_unlinked`. The link is shown on `/emails`; it closes nothing and changes no request state

### `email.request` / `email.reminder`
- consumes: `manufacturer?`, `doc_id?`, `reason?` / `request_id`
- writes: `renewal_request`, `renewal_request_document`, `email_draft`
- emits: `email.reminder` scheduled `reminder_after_days` out / nothing: the next rung is the same job, `queue.defer`red by `reminder_after_days` and returned `_deferred` (the `scheduler.tick` contract), until the request closes, is archived, or hits `escalate_after_reminders`, which finishes it. Re-enqueueing under its own key was silently dropped by the active-scope dedupe (fixed 2026-09-11)
- fails how: unresolvable manufacturer, cadence race, missing request — all counted, none raised
- note: **the system never sends.** `sent` is a human record-keeping state
- note: `reason='eudamed-gap'` takes a **separate branch**, checked BEFORE the expiry horizon read. It composes from `eudamed_declaration_gap` -- documents we have NEVER held -- writes ONE `email_draft` of kind `gap-request` per manufacturer covering every group, and creates NO `renewal_request` (a gap request renews nothing, has no `doc_id`, and must not consume a slot in `renewal_request_cadence_key`, which would silence that manufacturer's real renewal mail for a whole period). The ordering matters: the renewal path returns `nothing_expiring` for precisely the manufacturers whose problem is largest -- CARL MARTIN holds zero declarations across 2.389 articles -- so a gap request reached after that guard would never be composed. Counted as `gap_groups` / `gap_articles`, or `no_gap` when there is nothing to ask for. Enqueued by the **Draft request** button on the manufacturer page (`POST /manufacturers/{name}/gap-request`), which is a producer and writes no draft itself

### `eudamed.sync` / `.certregister` / `.sweep`
- consumes: `basic_udi_di` / nothing / `manufacturer`
- writes: `eudamed_mirror` / `eudamed_certificate` + `manufacturer_srn` + `manufacturer` / `eudamed_mirror` + `manufacturer.srn_probed_at` + `manufacturer_srn` + `eudamed_sweep_state`
- emits: nothing, in all three
- fails how: non-200, non-JSON, a filter that stops filtering, exceeding `MAX_DEVICES`, or never reaching a last page all raise — dead-letter rather than silently truncate. `_assert_srn` raises on a mismatched echoed SRN
- note: `eudamed.sweep` is released by a human and never self-emits
- **a sweep that stored zero devices does NOT stamp `last_swept_at`** and stays on the due list, reporting `empty_sweep`. Zero devices for a *trusted* SRN is a failed call, not an empty catalogue — the SRN came off a certificate or a double-gated article probe, so the manufacturer is registered by construction. Before this guard (job 35679, IVOCLAR, 2026-09-02) an empty sweep finished `done`, stamped itself swept and cleared `due_at`, buying 90 days of silence for a run that wrote nothing
- **an empty `content` that contradicts its own envelope raises.** EUDAMED degrades under load to a 200 carrying a valid Spring envelope whose `content` is `[]` before it degrades to an HTTP 307 block page (reproduced live 2026-09-02, host and worker container alike). `_is_last()` used to read that as a clean end of pagination, which is how jobs 35679/35701 swept IVOCLAR on page 0 and stored nothing. A page is now an error when the envelope says there was more (`last: false`, or `number < totalPages - 1` with `totalElements > 0`); a genuinely empty manufacturer (`totalElements: 0`) and a malformed body with no counts both still end the walk quietly
- **paging pauses `fetch.politeness_ms` between pages** (`_pause_between_pages`, all three walks). The loops previously fetched back to back -- 38 requests as fast as the network allowed -- which is what earned the 307s. Read per call, so raising the config value slows a running deployment without a restart. FETCH spends the same budget through the domain lease; a multi-page walk cannot, because the lease path defers the job and this handler runs its walk in one transaction
- **a sweep is one claim and one transaction for every page.** IVOCLAR is 38 pages; at the old 300s `visibility_timeout_s` it was reclaimed mid-flight and every stored page rolled back. The timeout is now 1800s, which is a mitigation — see `[eudamed-sweep-is-one-long-transaction]` for the per-page fix

### `report.weekly`
- consumes: `period_key`
- writes: nothing
- emits: nothing
- fails how: generic

### `scheduler.tick`
- consumes: `cron` — one of seven keys, closed set (`CRONS` in the handler)
- writes: `scheduler_run`, plus whatever the named cron writes
- emits: whatever the named cron emits
- fails how: an unknown `cron` raises `LookupError` — loud, because a tick that
  silently does nothing is indistinguishable from one whose period has not come
  round. Otherwise generic, and the failure is confined to that one cron's job
- note: **it never completes.** The handler ends in `queue.defer`, which returns
  the job to `pending` and refunds the attempt the claim charged, so the row is
  permanently in the active-only dedupe index and cannot be duplicated. Two
  consequences worth holding: `run_after` is a *poll interval*, not a fire time
  — `scheduler_run` stays the sole authority on whether a period has fired, so
  a late poll fires the same period and two polls in one period fire once; and
  because `dead` is terminal and sits *outside* that index, a cron that
  dead-letters frees its key, which is what lets `runner.arm_crons` (every
  worker start) and `/scheduler`'s Re-arm button revive it. Bound: `claim`'s
  visibility timeout (`queue.visibility_timeout_s`, 1800 by default and passed
  through by the runner since 2026-09-04) caps how long any cron may take

## Cross-cutting mechanics

Every handler shares these; they are not repeated above.

- `app/workers/runner.py:40-106` wraps claim → dispatch → finish in one transaction.
- Uncaught exception → `queue.fail` (`queue.py:214`), backoff
  `min(base * 2**(attempts-1), cap)`, dead after `max_attempts`, and a
  `dead-job-followup` manual_task raised in the same transaction.
- A handler returning `{"_deferred": True}` skips `finish` and keeps its writes —
  no retry consumed (`runner.py:76-85`).
- A handler returning `{"anomalies": [...]}` has them written to `data_anomaly` by
  the runner, not by the handler (`results.py:109`).
- Dedupe is scoped to active statuses only:
  `ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')`
  (`queue.py:98-109`).

## Invariant 1, and its two ruled exceptions

Only `gate.candidate` / `gate.apply` write `document`, `item_document`, `evidence`.
Grepping those three tables across `app/` outside `gate.py` returns exactly three
files — `app/repair_archive_urls.py`, `app/repair_mfr_overbind.py` and
`app/repair_document_manufacturer.py` — and **none is a queue handler**. All are
one-off CLI repairs, never registered against a tag, never run by a worker, and
each docstring names itself as an exception: the first two ruled by Denis on
2026-08-24, the third (backfilling `document.canonical_manufacturer` for rows
GATE wrote before migration 053) on 2026-08-31. `app/repair_mfr_task_reason.py`
is a fourth repair tool but touches only `manual_task`, so invariant 1 does not
reach it.


## Is it a document at all? (2026-09-02)

Two guards, added after DISCOVER's first production search run archived four web
pages as compliance documents — one of which a human approved into `production`,
because the metadata looked real.

**Every producer that can reach the archive** requires a document's magic bytes
(`archiving.is_document`) before `store.put`. `bredent.com` answers `HTTP 200 text/html` at a URL ending
`.pdf`; of 24 real search candidates measured that day, 18 answered 200 and 5 of
those were HTML. Whitespace is stripped first — `dentalsky.com` answers a body
beginning `" <!do"`. A refused body is still ledgered, so invariant 6 stops us
paying for it again; it is simply never archived and emits nothing.

**EXTRACT** decides what a file is in two steps. T0 classifies from a two-page
header window only (`_HEADER_PAGES`, `t0_templates.promote_doc_class`) — the
previous rule promoted `unknown` to `compliance-doc` off any marker anywhere in
the full text, which is how a site menu became an `IFU` at confidence 1.00.
Anything still `unknown` goes to a cheap doc-class call (`AnthropicLlm.classify`,
`models.t1`) which may answer `not-a-compliance-document`; that answer is
terminal and suppresses the `validate.doc` emit.

Measured over 1.261 real archived PDFs against 13 PDFs the ranker chose off the
open web: **97% of real documents are still typed free by T0**, 3% take the call.

Two things to know before changing this:

- `doc_class` is **not persisted** and neither VALIDATE nor GATE reads it.
  `business-doc` and `msds` short-circuit extraction but still emit
  `validate.doc` with empty fields. Only `not-a-compliance-document` suppresses
  the emit (followup `[extract-emits-for-refused-classes]`).
- The gate lives in `handlers/archiving.py` (lifted out of `fetch.py` on
  2026-09-02) and is called by FETCH, by `upload.ingest`, and by the upload form
  in `web/app.py` — the form checks the bytes while the person is still there,
  because the handler's refusal is only a job result nobody reads.
  `email_poll._is_pdf` predates it and keeps its own, wider rule: it searches the
  first 1024 bytes, because a mail attachment may legitimately carry a preamble,
  and it refuses container signatures outright. `backfill.scan` is the one
  writer that does not check (followup `[gate-covers-only-backfill]`); its corpus
  is a trusted SFTP dump and EXTRACT refuses a non-document regardless.


## Expiry does two things now (2026-09-02)

`_tick_expiry_scan` used to have exactly one action — an `email.request` asking
the manufacturer — and with `expiry_email_enabled` off (the shipped default, and
how the live worker is configured) it counted documents and took no action at
all. It now also enqueues `discover.group` with `ignore_recency: true` for the
groups those documents cover, because manufacturers republish certificates on
their own sites without telling anyone.

Both halves are independently gated. `expiry_email_enabled` chases by letter;
`expiry_rediscover_enabled` looks again ourselves. The second defaults off and
**must**: `DISCOVER_HOLD` is read only in `resolve.py`, so a cron enqueueing
`discover.group` bypasses it, and this flag is the only thing between a daily
tick and unattended fetching.

`ignore_recency` is deliberate here and nowhere else in the crons: a lapsed
certificate is the one case where the recency window is exactly wrong — it says
"we looked recently", and looking again is the entire point. The dedupe key is
the shape the item button, the manufacturer button and the CLI already use, so a
human pressing any of them today and this tick collapse onto one job.


## The coverage cron (2026-09-02)

`_tick_coverage_scan` sends groups holding no production document through
DISCOVER, 25 a day. Before it, nothing in the scheduler ever noticed a gap:
seven crons were registered and none enqueued `discover.group` — the only
occurrence of the word in that module was a failure-reason string, so coverage
moved only when a person pressed something.

What "uncovered" means lives in `app/coverage.py` and nowhere else. The
manufacturer page's button and this cron ask the same question, and a second
definition drifting from the first is how a button comes to promise work the
cron has already done. `staged` does not count as covered — that is the review
queue.

Ordering is load-bearing, not cosmetic: never-discovered first, then least
recently discovered. 6.693 of the 6.732 uncovered groups have never been
discovered at all, so at 25/day this is a nine-month backfill, and a stable
ordering under a cap re-does the head of the list forever.

Recency is not bypassed here, unlike the expiry re-look — these groups have no
document at all, so there is usually nothing in the ledger to skip.

### `bc.push`
- consumes: `run_id`, `item_refs` (a batch, ~200)
- produced by: the item button and the bulk apply (web), and the `bc.push-drift` cron — a rolling window, oldest-pushed first, capped by `bc.drift_cap`
- writes: `bc_push_log` only. No registry table, so invariant 1 is untouched — this stage reads the registry and writes someone else's system
- emits: nothing
- fails how: a **404 counts `absent`** and is sampled by `item_ref` — `dataitems` is "a subset" and nobody has said of what, so the first real run answers that instead of hiding it behind retries. Any other non-2xx counts `failed` and is kept in the ledger, but is excluded from the next run's diff, so a refused value is retried rather than silently believed. One item failing never stops the batch
- note: the EUDAMED certificate status behind `pteValidCECertificate` is read only under a **trusted SRN of the item's manufacturer**, against the base certificate number (`R2` / `Rev. 2` stripped as `held_certificate` strips it), latest revision only (`_HOLDINGS_SQL`, 2026-09-11). Until then it joined on the printed number alone: any company's certificate with a colliding number could falsify the field, and two revisions let an older `issued` row outvote a newer withdrawal
- note: **`bc.write_enabled` is false by default.** Off, the handler still computes the diff and reports `would_send` — that is what the bulk preview reads. The rule itself is `app/bc_fields.py` and is tested without a database or a connection
