# One document's lifecycle, as built

**Status:** live, written 2026-08-31. As-built, not as-designed — where the
code diverges from the PRD/handbook, this file says so and the PRD still
wins on the contract itself (`CLAUDE.md`).

## Idea

A catalogue row arrives from Business Central. Nothing about it proves the
device is compliant until a document — a Declaration of Conformity, an EC
certificate, an IFU, an ISO cert — has been found, fetched, read, checked
against deterministic rules, and either machine-approved or handed to a
human. This is that document's journey: from the BC row that first implies
it should exist, to a support rep or the webshop seeing it on
`/item/{item_ref}`, to the day it lapses and a renewal request goes out —
and, if a newer document arrives first, to the day it is quietly filed into
a supersession chain instead.

## Where

The chain this file narrates, left to right. Full per-tag detail (payload
fields, every failure mode, test file) is [handlers.md](handlers.md) — this
table exists only to anchor the walkthrough below.

| Stage | Entry point |
|---|---|
| `ingest.run` | `app/handlers/ingest.py:118` |
| `resolve.group` | `app/handlers/resolve.py:278` |
| `discover.group` | `app/handlers/discover.py:373` |
| `fetch.url` | `app/handlers/fetch.py:114` |
| `extract.doc` | `app/handlers/extract.py:258` |
| `validate.doc` | `app/handlers/validate.py:759` |
| `gate.candidate` | `app/handlers/gate.py:910` |
| `gate.apply` | `app/handlers/gate.py:1311` |
| `email.request` / `email.reminder` (renewal) | `app/handlers/email_request.py:407` / `:525` |

## Input → Output: the walkthrough

**1. `ingest.run` — a BC row becomes a mirrored item.**
Triggered by the scheduler's monthly tick (`app/scheduler.py:77-101`,
dedupe `ingest.run:sched:{catalogue}:{period_key}`) or by hand from
`/ingest` / `/import`. Reads a CSV or upload spool row through a
`SourceAdapter` (BC OData is wired but always raises —
[limits.md](limits.md), not a live source today). Upserts `item_mirror`
comparing a fixed column tuple (`_DIFF_COLS`, `app/handlers/ingest.py:65-66`)
to decide "changed"; unchanged rows are counted, not re-emitted. Every
changed item enqueues one `resolve.group`, dedupe `resolve:{item_ref}:{rev}`
— suppressed under `dry_run` (the two-phase BC-import preview).

**2. `resolve.group` — the item joins (or forms) a manufacturer-scoped
group.**
A stop-at-first-hit ladder, each rung scoring 1.0 except the last:
existing-link → UDI → Basic UDI-DI → name-family (pg_trgm candidates +
rapidfuzz scoring, only rung that can land in a human-review "suggest" band
rather than auto-joining — invariant 3, groups are never auto-formed on a
name match alone). `app/handlers/resolve.py:1-22` (module docstring) names
the ladder and RESOLVE's write set: `item_group`, `item_group_member`,
`manufacturer_alias`, `grouping_suggestion` — never the registry (invariant
1). If a T1 adjudicator isn't injected, ambiguous name-family candidates
still stage a `grouping_suggestion` rather than losing the item — the
adjudicator call itself raises `NotImplementedError`
(`app/handlers/resolve.py:263-273`) and the caller catches it, storing
`t1_error` on the suggestion. Emits `discover.group`
(`discover:{group_id}:{cycle}`) **unless** the group already has a current
production document, or `discovery.hold` is set.

**3. `discover.group` — find candidate URLs, or give up cleanly.**
Walks a per-manufacturer source-priority ladder (`app/handlers/discover.py:4-9`
module docstring): a playbook's direct `doc_sources` first, then a
`site:`-restricted search (T1 ranking not live — degrades to logging a miss
and going to `manual`; [limits.md](limits.md)), then contact-based
`email.request`, then a **recency** rung. The first rung to produce
candidates emits `fetch.url` per URL and stops; `manual`/`vendor` are
terminal with no emit. **The recency rung is also a re-entry point, not
just a guard**: when everything the group knows is ledger-fresh and already
extracted, it emits `validate.doc` directly
(`archiving.emit_validate`) — the same hash-dedupe re-entry FETCH performs
in step 4, reached here without a network call at all
(`test_recency_rung_links_a_fresh_extracted_known_url_via_validate`,
`tests/test_discover_handler.py:175`). `ignore_recency`
([admin-refetch-item]) bypasses only this rung — conditional GET and hash
dedupe downstream still run (`test_ignore_recency_bypasses_the_recency_c3_link`,
`tests/test_discover_handler.py:262`).

**4. `fetch.url` — bytes arrive, or this content is already known.**
A politeness lease gates one fetch per domain per interval; contention is a
voluntary self-defer, not a failed attempt (`app/handlers/fetch.py:131-133`).
Then, in order (`app/handlers/fetch.py:114-184`):
   - recency-fresh + already extracted → link the group via `validate.doc`,
     no network call (mirrors step 3's recency re-entry).
   - conditional GET returns 304 → ledger touched, nothing else happens.
   - new bytes, but the **sha256 already has an extraction** (C3 hash-dedupe:
     the same PDF reached us by a second URL, e.g. mirrored on two domains) →
     `archiving.link_existing_doc` points this URL's ledger row at the
     existing `document`, and `validate.doc` is emitted directly —
     **`extract.doc` is skipped entirely**. This is the re-entry named in
     the architecture diagram in `CLAUDE.md` (`fetch.url` hash-dedupe
     re-enters at `validate.doc`). Test:
     `test_hash_dedupe_emits_validate_no_archive`,
     `tests/test_fetch_handler.py:279`.
   - genuinely new content → archived through the `StorageAdapter`,
     `extract.doc` emitted (`extract:{content_hash}`).
   A persistent bot-wall (httpx *and* the Playwright fallback both fail)
   raises `FetchError` — this is the one branch that consumes a retry rather
   than deferring or completing.

**5. `extract.doc` — read the PDF, tier by tier.**
Opens the archived file once and hands it to both the text-store sidecar and
the tier ladder. An unopenable PDF is counted (`unreadable_pdf`) and
**nothing downstream is emitted** — there's nothing to validate
(`app/handlers/extract.py:283-300`). Otherwise: T0 (deterministic,
playbook-templated or pdfplumber) runs first; any field it leaves blank or
low-confidence escalates per-field to T1 (Haiku), then T2 (Sonnet vision) —
never per-document (`app/extract/tiers.py:265-301`, `merge()` docstring at
`:157-189`: a later tier only ever fills a gap or grows a value, never
shrinks or erases one). In batch mode the same job self-defers via
`queue.defer` across polling cycles until the Anthropic batch completes —
no second job type, batch state lives in `batch_ref`, never in the payload
(invariant 9; `app/handlers/extract.py:1-14` module docstring). Emits
`validate.doc` (`validate:{hash}:{rev}:{group_id}`).

**6. `validate.doc` — pure rules, zero AI, zero writes.**
Stateless by design (`app/handlers/validate.py:1-12` module docstring lists
the full evaluation order). Two outcomes matter most:
   - **The manufacturer-binding short circuit (C4).** A document whose
     `coverage_scope` reads `manufacturer` and carries *no* item-level
     identifiers (a QMS certificate, a brand-wide DoC) cannot be REF-matched
     at all — it isn't about one item. `handle_validate_doc` special-cases
     this before any of the REF-gate logic runs
     (`app/handlers/validate.py:816-856`): it emits exactly **one**
     `gate.candidate`, tagged `route: "mfr-binding"`, dedupe
     `gate:{hash}:{rev}:mfr-binding` — regardless of how many groups
     requested the document. A certificate whose own text enumerates the
     specific devices it covers ("valid only for the above mentioned
     Medical Devices") carries an additive `device-enumeration` flag that
     GATE later refuses to auto-bind on.
   - **The normal path** runs the REF gate (C1: the pair
     `(canonical_manufacturer, mfr_ref)`, never a bare number — see
     `00-orientation.md`'s invariant table), date sanity, the never-downgrade
     check (C7 — an older candidate, both dates present, additively emits
     `superseded_by_doc_id` *and keeps* the blocking `older-than-current`
     flag), and same-subject supersession (C6 — MDR never supersedes MDD).
   Always emits `gate.candidate` — the mfr-binding route above, or
   `gate:{hash}:{rev}:{group_id}` otherwise.

**7. `gate.candidate` — the only place the registry gets written (besides
`gate.apply`).**
A stale extraction revision is a silent no-op, decided before any read or
write (`app/handlers/gate.py:915-929`) — prevents an old rev's stale field
from re-triggering a permanent dead-letter loop. Otherwise, two dispositions
run in parallel:
   - **Document-level**, a 5-way if/elif chain checked in this priority
     order (`app/handlers/gate.py:980-994`): the narrow **auto-supersede
     fast path** → `superseded` directly, skipping every disposition below
     it, when all four hold — `superseded_by_doc_id` present, no *other*
     blocking flag survives, `date-insane` absent, and `validity_from`'s
     own calibrated confidence clears `cfg.gate.high`
     (`app/handlers/gate.py:945-979`) — then `production` (score/ref_gate/no
     blocking flags clear high), then `filed` (C15: correctly read,
     correctly attributed, covers nothing we sell), then `staged`, else
     `manual`. Any guard failure on the fast path falls through to the
     ordinary chain, where
     `older-than-current` (still in `BLOCKING_FLAGS`,
     `app/handlers/gate.py:65-66`) forces `manual` — **never** production,
     never a silent downgrade (invariant 4). Tests:
     `test_auto_superseded_candidate_files_without_a_manual_task`
     (`tests/test_gate_candidate_handler.py:1487`),
     `test_auto_superseded_still_blocked_by_no_item_identifier` (`:1549`),
     `test_auto_supersede_never_stamps_a_production_document` (`:1774`).
   - **Link-level, independently**, each by its own `match_basis`:
     `name-family`/`fetch-context`/`ref-catalogue` cap at `staged`
     *structurally* — `TRUSTED_BASES` (`app/handlers/gate.py:47`) plus a DB
     CHECK (migrations 005, 021) — no approval ever lifts them; only a C17
     human link decision can.
   - **The mfr-binding route** (`_handle_mfr_binding`,
     `app/handlers/gate.py:743-908`) resolves the document's manufacturer
     string against the curated canonical table and takes one of three
     paths: exactly one canonical + at least one MD-flagged item →
     `production`, linked to every MD item under every BC code of that
     manufacturer, no task at all
     (`test_mfr_binding_auto_binds_when_the_name_resolves_to_one_manufacturer`,
     `tests/test_gate_candidate_handler.py:461`); one canonical + zero MD
     items → `filed` (C15, nothing to link); anything ambiguous, unresolved,
     low-confidence, or carrying `device-enumeration` → `staged` + exactly
     one review task, however many groups asked for it.
   `manual`/`staged` writes are still archived and evidenced — only the
   *disposition* differs, never the completeness of what's recorded
   (invariant 2).

**8. Visible on a product.** A `production` document is now servable:
`/item/{item_ref}?k=` (the BC hyperlink target, `docs/runbook.md`'s Web UI
table) and `/api/items/{item_ref}/documents` both read straight off
`item_document`/`document` at `status='production'`. A `staged` or `manual`
document is not — it sits in `/staging` or `/manual` until a human acts.

**9. `gate.apply` — a human decides.**
Three decisions that matter here (C17 link-only decisions —
`confirm-link`/`reject-link`/`reopen-link` — return before touching
`document` at all, `app/handlers/gate.py:1320-1321`):
   - **`approve`**: promotes the document to `production`
     (`_promote`, `gate.py:1142-1143`), promotes any staged link whose basis
     is in `TRUSTED_BASES` alongside it (`_promote_pending_links`,
     `:1146-1152`) — `name-family`/`fetch-context` links stay staged even
     now. **This is the human half of supersession (C6)**: a staged
     candidate that carried a `supersedes` target (written sticky at
     candidate time, inert while merely staged) only acts on it here, via
     `_apply_supersession` (`gate.py:604`) —
     `test_staged_candidate_does_not_supersede_until_gate_apply_approves`
     (`tests/test_gate_candidate_handler.py:1370`) is the contrast case to
     step 7's machine fast path
     (`test_production_candidate_supersedes_prior_production_doc`, `:1348`).
   - **`reject`**: sets the document `rejected` and **cascades every one of
     its links to `rejected`** — un-binding is reject-plus-cascade, no link
     may stay `production` or `staged` under a rejected document
     (`app/handlers/gate.py:1338-1348`,
     `test_reject_cascades_link_statuses_with_audit`,
     `tests/test_gate_apply_handler.py:159`). If the rejected candidate
     carried a `group_id`, `gate.apply` **re-enqueues `discover.group`** at
     interactive priority (`dedupe: discover:gate-reject:{doc_id}:{group_id}`,
     `gate.py:1349-1355`) — a human "no" restarts discovery for that group
     rather than leaving it stuck on the rejected document
     (`test_reject_marks_rejected_and_enqueues_interactive_discover`,
     `tests/test_gate_apply_handler.py:88`). Human edits are written as T3
     evidence (`tier='T3'`, `model_id=NULL`, `confidence=1.0`, pageless —
     [evidence-page] ruling).
   - **`bind-manufacturer`**: the person's version of step 7's mfr-binding
     route, for the `staged` case the machine declined to settle itself.

**10. Renewal, and the loop closing on its own.**
The SCHEDULER's daily expiry-scan tick (`app/scheduler.py:116-143`, gated by
`cfg.expiry_email_enabled`) finds documents lapsing inside the configured
horizon and enqueues `email.request` — one draft per manufacturer per cadence
period, never one per document (`docs/runbook.md`'s `/drafts` row).
`handle_email_request`/`handle_email_reminder`
(`app/handlers/email_request.py:407`, `:525`) write `renewal_request` and an
`email_draft`; the request arms one `email.reminder` (dedupe
`email.reminder:req:{request_id}`) at `RENEWAL_REMINDER_AFTER_DAYS` out, and each
reminder then DEFERS its own job by the same interval rather than enqueueing a
successor (a successor under the same key collided with the running job and was
dropped, so until 2026-09-11 every chase stopped at its first reminder). **The
system never sends** — a person reviews the draft at `/drafts/{id}` and sends
by hand from `mdr@dentalia.si`; `sent` is a human record-keeping state, not a
side effect of any job. If a manufacturer responds with a newer document
before or after that draft goes out, it re-enters this same walkthrough from
step 3/4 and, once it validates as covering the *same* (coverage subject,
type, regulation) as the current production document, step 7's C6/auto-file
logic or step 9's human-approve path is what actually retires the old one
into the superseded chain — there is no separate "renewal" write path.

## Rules

The invariants load-bearing for this specific journey (full 12 in
`CLAUDE.md`):

- **Invariant 1** — only `gate.candidate`/`gate.apply` write
  `document`/`item_document`/`evidence`. Every other stage above either
  writes its own producer-side table (`item_mirror`, `item_group`,
  `discovery_log`, `fetch_log`, `extraction_attempt`) or nothing at all.
- **Invariant 2** — every value GATE writes carries complete evidence;
  `staged`/`manual` differ from `production` in disposition, never in
  evidence completeness.
- **Invariant 3** — REF-gate auto-write requires the
  `(canonical_manufacturer, article number)` pair or a Basic UDI-DI match;
  `name-family`/`fetch-context`/`ref-catalogue` cap at `staged`,
  structurally, forever.
- **Invariant 4** — never downgrade. The task-5 auto-file exception is
  narrow and guarded four ways (step 7); any guard failure is
  `older-than-current` forcing `manual`, exactly as if the exception didn't
  exist.
- **Invariant 5** — supersession only within identical (coverage subject,
  type, regulation); MDR never supersedes MDD; a staged candidate never
  supersedes anything until a human approves it.
- **Invariant 6** — unchanged content is never re-fetched or re-parsed (the
  fetch ledger + hash-dedupe in steps 3-4).

## Limits

Stubbed, deferred, or unwired points this journey passes through:

| Point in the journey | What's actually there |
|---|---|
| Step 1, BC OData source | `BcApiAdapter.read` (`app/adapters/source.py:301`) raises unless records are injected in tests. The Ingest form offers `bc_odata` and it always dead-letters — [limits.md](limits.md), followup `[ingest-bc-odata-always-fails]` |
| Step 2, live T1 name-family ranking | `_default_adjudicator` (`app/handlers/resolve.py:263-273`) raises `NotImplementedError` unless injected; ambiguous matches always stage for human review, never auto-adjudicate live. Followup `resolve-t1-ranking` |
| Step 2, C4 binding at RESOLVE time | `_apply_manufacturer_bindings` (`app/handlers/resolve.py:250-260`) is **inert — always returns 0**. A newly-resolved item under a manufacturer that already has a production mfr-scope binding does **not** automatically inherit it; the handbook's "receives the link at RESOLVE time" is not built. Deferred to followup `resolve-c4` |
| Step 3, live T1 search-candidate ranking | `_default_rank` (`app/handlers/discover.py:167`) raises; the search rung degrades to logging a miss and going to `manual` until followup `discover-t1-ranking` lands |
| Step 10, sending renewal email | Nothing anywhere sends. `email.send_policy` was meant to gate draft-vs-auto sending and is read by nothing ([limits.md](limits.md)) |

## Gotchas

- **`archive_url` has to ride the payload, or GATE guesses wrong.**
  `emit_validate` (`app/handlers/archiving.py:121-137`) carries the
  registry's stored handle forward when one already exists; without it GATE
  falls back to `fetch_log.url_normalized`, which for a backfilled corpus
  document is a path meaningful only on the scanning machine — this is what
  put a dead `/imports/...` URL on every one of the GC pilot's 130
  documents before the 2026-08-24 fix.
- **A stale extraction revision is a silent no-op at GATE**, not an error
  (`app/handlers/gate.py:927-929`) — a repair that appends a new rev and
  re-emits will see the old rev's candidate simply do nothing, which is the
  point (rev N-1 must never overwrite rev N's type/regulation/dates).
- **`device-enumeration` blocks the machine mfr-bind even at high
  confidence** — a certificate that names its own covered devices is not
  manufacturer-wide by its own words, whatever `coverage_scope` says
  (`app/handlers/validate.py:816-840`, `app/handlers/gate.py:802-815`). A
  human can still bind it via `gate.apply bind-manufacturer` — the machine
  path alone refuses.
- **A document's links are gated independently of the document.** A
  `production` document routinely carries a mix of `production` and
  `staged` links — approving the document does not touch the capped ones.

## Tests

The per-tag test file for each stage above is listed in
[handlers.md](handlers.md)'s index — not repeated here. The specific
branch-crossing tests cited inline above (`test_hash_dedupe_emits_validate_no_archive`,
`test_recency_rung_links_a_fresh_extracted_known_url_via_validate`,
`test_mfr_binding_auto_binds_when_the_name_resolves_to_one_manufacturer`,
`test_reject_cascades_link_statuses_with_audit`,
`test_auto_superseded_candidate_files_without_a_manual_task`,
`test_staged_candidate_does_not_supersede_until_gate_apply_approves`) are the
ones that actually exercise the seams between stages, as opposed to one
stage's internal rules — start there if a change touches how two stages hand
off to each other. Run selection follows the same rule as everywhere else
(`00-orientation.md` § Running tests): a change to any single handler above
runs its own `tests/test_<name>_handler.py`; a change to `app/queue.py`,
`app/db.py`, or anything else on the full-suite trigger list runs the whole
suite.
