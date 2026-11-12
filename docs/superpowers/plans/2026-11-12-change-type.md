# Change Type on Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewer can change a staged document's type, regulation and scope from Review, behind a two-step confirm with a tick box, and a later machine reading never undoes it; plus the T0 marker fix that caused doc 831.

**Architecture:** One shared read-only module `app/retype.py` checks a proposed change and computes its consequences; the web route uses it for the confirm, and the new `gate.apply` decision `retype` uses it again, under a row lock, before writing. GATE writes the fields as T3 (held), rewrites the review task for the new panel, and audits before/after. `gate.candidate` gains a `route-held` branch for documents whose scope a person set.

**Tech Stack:** Python 3.12, psycopg 3 raw SQL, FastAPI + Jinja + HTMX, pytest against real Postgres via `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-12-change-type-design.md` (approved by Denis 2026-11-12). Read it before any task.

## Global Constraints

- Only GATE handlers write `document`, `item_document`, `evidence` (invariant 1). The web route enqueues `gate.apply`; it never writes the registry.
- `retype` is a new value of the existing `gate.apply` `decision` field, not a new job type. PRD v3 is changed first (Task 1).
- Retype never: changes status, promotes, writes `production-write`, makes a link production, enqueues a job, re-reads, runs VALIDATE, calls an LLM, or calls `_resolve_manual_tasks`.
- `cert_number` and `validity_to` are kept on retype, never written by it.
- Vocabularies: type `DoC, EC, ISO, IFU, SPP, other` (`app/extract/checks.py` `TYPES`); regulation `MDR, MDD, n.a.` (`checks.REGULATIONS`); scope `group, manufacturer` (migration 022).
- Scope default by type: DoC, IFU, SPP, other -> `group`; EC, ISO -> `manufacturer`. Set in the browser; the server takes what is posted.
- Dedupe key `apply:{doc_id}:retype`.
- Tests only through `./scripts/test.sh <paths>`. Selection, always the whole glob: `app/extract/*` -> `tests/test_t0*.py`; `app/handlers/gate.py` -> `tests/test_gate*.py`; `app/retype.py` -> `tests/test_retype*.py`; anything under `web/` -> `tests/test_web*.py`. Full suite before the Task 3 commit (it changes `handle_gate_apply`'s shared dispatch) and again in Task 8.
- Branching: Task 0 goes to `main` alone (a bug fix that ships on its own). Tasks 1-8 are built on branch `change-type` in a worktree and merged to `main` as one unit after Task 8. Never deploy a state between them: Task 3 without Task 5 lets a machine reading bind a retyped document to a whole catalogue, and Task 6 removes the Type field only together with the control that replaces it.
- Commit messages: no reference to Claude or AI, no attribution lines (Denis's standing rule).
- Copy: plain words, no em-dashes; every refusal ends "Nothing was changed."
- No server write, deploy or job without Denis's explicit go.

## Review Focus

1. Two people on one document: an `approve` or whole-range approval queued from a screen loaded before a retype. Expected: refused by GATE through the new `expected_scope` check (the only guard for an Approve with no ticks; `_picked_items` raises only when items are sent) and the new `bind-manufacturer` scope guard; job dead-letters visibly, nothing published. Test in Task 4.
2. "Yes, change the type" pressed twice. Expected: second POST dedupes while the first is active; after it ran, `expected` no longer matches and the second is refused as stale. Test in Task 6.
3. A browser tab open from before the deploy posts `edit_type` with Approve. Expected: 422 "Change the type with Change type first. Nothing was changed.", and GATE refuses a `type` edit on approve if a hand-built payload gets through. Test in Task 6.
4. Retype to whole range on a document whose manufacturer is unknown (no `canonical_manufacturer`, no task manufacturer). Expected: task gets `route = mfr-binding` without a manufacturer; the panel shows "unknown" and refuses to bind, as today. Test in Task 3 (GATE) and Task 6 (render).
5. Regulation-only change. Expected: scope, panel and links untouched; only line 1 in the confirm; flags still dropped and "Type changed by" shown. Test in Task 3.

---

### Task 0: T0 marker "izjava eu o skladnosti"

**Files:**
- Modify: `app/extract/t0_templates.py` (`_TYPE_MARKERS`, beside `("izjava o skladnosti", "DoC")`)
- Test: `tests/test_t0.py`

**Interfaces:** none.

- [ ] **Step 1: Write the failing tests** in `tests/test_t0.py`:
  - add `("Izjava EU o skladnosti", "DoC")` to the `test_type_variants` parametrize list;
  - `test_a_slovenian_eu_declaration_citing_its_certificate_is_a_declaration`: text shaped like doc 831's head, `"IZJAVA EU O SKLADNOSTI\nŠtevilka EU certifikata: 028/MDR\nUporabljeni standardi: EN ISO 13485:2016"`; assert `value == "DoC"`, `conf == 1.0`, `verbatim.lower() == "izjava eu o skladnosti"`.
- [ ] **Step 2: Run** `./scripts/test.sh tests/test_t0.py`. Expected: both new cases FAIL (value `ISO`).
- [ ] **Step 3: Add** `("izjava eu o skladnosti", "DoC")` directly after `("izjava o skladnosti", "DoC")`, with a comment citing the 2026-11-12 production measurement (1,559 texts, 12 spellings, moves exactly 831-837). No other spelling.
- [ ] **Step 4: Run** `./scripts/test.sh tests/test_t0.py tests/test_t0_corpus.py tests/test_t0_layout.py`. Expected: PASS.
- [ ] **Step 5: Commit** `extract: read "Izjava EU o skladnosti" as a declaration`.

Ops, after deploy and only on Denis's go: `reextract` for 831-837 on the server (dry run first, show Denis the plan). Must run before anyone retypes them: `reextract` skips edited documents.

---

### Task 1: Contract and records

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (GATE section, the `gate.apply` decisions beside `reopen` / `bind-manufacturer`; disposition list for `gate.candidate`)
- Modify: `docs/decisions.md` (one row, 2026-11-12)
- Modify: `docs/dentalia-job-type-handbook.md` (gate.apply payload examples)

- [ ] **Step 1:** PRD: add `retype` with payload `{doc_id, decision:'retype', decided_by, type, regulation, coverage_scope, expected:{type, regulation, coverage_scope}, note?}`, its preconditions and writes (spec section 6), and the never-list. Add `bind-manufacturer` requires `coverage_scope = 'manufacturer'`; `approve` refuses a `type` edit. Add `gate.candidate` disposition `route-held` (spec section 9 ruling). Targeted edits only.
- [ ] **Step 2:** decisions.md row: "Change type on Review" -> retype decision, keep `cert_number`/`validity_to`, Type out of Correct a fact first, route-held; spec link.
- [ ] **Step 3:** handbook: one `retype` payload example and one `route-held` result example.
- [ ] **Step 4: Commit** `docs: retype decision and route-held in the contract and records`. No tests (docs-only).

---

### Task 2: Mockup (stop for Denis)

**Files:**
- Create: `docs/superpowers/specs/2026-11-12-change-type-mockup.html` (same approach as `2026-10-02-review-item-picker-mockup.html`: static HTML, the real Review CSS copied in)

- [ ] **Step 1:** Draw, for doc 831 and doc 832: the row with "Change type..." placed apart from Approve / Reject; the open form (three selects, pre-filled); the step-1 confirm with its lines, the tick box and "Yes, change the type" / Cancel; the receipt; the row afterwards in the specific-items panel with "Type changed by Nataša on 12.11.2026." Also the group panel's "Correct a fact first" without Type.
- [ ] **Step 2:** Publish it for Denis and STOP. Tasks 6 and 7 wait for his approval of the layout. Tasks 3-5 may proceed meanwhile.

---

### Task 3: `app/retype.py` and GATE `retype`

**Files:**
- Create: `app/retype.py`
- Modify: `app/handlers/gate.py` (`handle_gate_apply`: early dispatch; new `_apply_retype`, `_retarget_task`)
- Test: `tests/test_retype.py` (module), `tests/test_gate_retype.py` (handler, table-driven)

**Interfaces:**
- Produces, in `app/retype.py`:
  - `FIELDS = ("type", "regulation", "coverage_scope")`, `SCOPES = ("group", "manufacturer")`, `CERT_TYPES = frozenset({"EC", "ISO"})`
  - `default_scope(doc_type: str) -> str`
  - `number_class(doc_type: str) -> str` returning `"cites"` (DoC), `"own"` (EC, ISO), `"none"` (IFU, SPP, other)
  - `problems(conn, doc_id: int, after: dict, expected: dict | None, own_job_id: int | None = None) -> list[str]` returning codes from `{"not-found", "not-staged", "bad-type", "bad-regulation", "bad-scope", "unchanged", "stale", "in-flight", "production-link", "in-chain"}`; `expected=None` skips the stale check (step 1)
  - `retype_active(conn, doc_id: int) -> bool` (a `gate.apply` job with `decision = 'retype'` for the doc in `pending/running/failed`; used by Task 4's route refusal)
  - `@dataclass(frozen=True) class Plan`: `before: dict`, `after: dict`, `links_off: list[str]` (staged item_refs, when scope becomes manufacturer), `citations_cleared: list[int]` (DoCs whose `cert_doc_id` is this doc, when it stops being EC/ISO), `citations_set: list[int]` (DoCs `_backresolve_citing_docs` would match, when it becomes EC/ISO), `clears_own_cert_doc: bool` (was DoC, now not), `cert_number: str | None`, `validity_to: date | None`, `number_moves: bool` (`number_class` changes and `cert_number` or `validity_to` set), `panel: str` (`"group"` or `"manufacturer"`)
  - `plan(conn, doc_id: int, after: dict) -> Plan` (read-only)
- Produces, in `gate.py`: `_retarget_task(conn, doc_id: int, scope: str, retyped: dict | None) -> None` (`retyped = {"by", "at"}`, or `None` from Task 5 when no `retype` audit row exists; rewrites or opens the `gate-manual` task per spec write 6; Task 5 calls it with `retyped` read from the document's latest `retype` audit row). `handle_gate_apply` returns for `retype` `{"decision": "retype", "doc_id", "links_off": int, "citations_cleared": list[int], "citations_set": list[int]}`.
- Consumes: `gate._apply_edits`, `gate._backresolve_here`, `gate._audit`, `gate._push_manual` shape.

- [ ] **Step 1: Write failing module tests** `tests/test_retype.py`:
  - `test_default_scope` (parametrize all six types to the spec defaults);
  - `test_number_class` (DoC cites, EC/ISO own, IFU/SPP/other none);
  - `test_problems_table` parametrized: missing doc -> `not-found`; status `production` -> `not-staged`; `type='XX'` -> `bad-type`; same values -> `unchanged`; `expected` differing from row -> `stale`; a `pending` `validate.doc` with the doc's `content_hash` in payload -> `in-flight`; a `failed` one -> `in-flight`; a `dead` one -> no problem; another `pending` `gate.apply` for the doc_id -> `in-flight`; the job passed as `own_job_id` -> no problem; a `production` link -> `production-link`; own `supersedes` set -> `in-chain`; own `superseded_by` set -> `in-chain`; another row's `superseded_by` pointing at it -> `in-chain`;
  - `test_plan_whole_range_takes_staged_links_off` (2 staged + 1 rejected link -> `links_off` has the 2);
  - `test_plan_certificate_to_declaration_lists_citing_docs`;
  - `test_plan_declaration_to_certificate_lists_docs_it_would_answer` (citing DoC with matching `cert_number` and NULL `cert_doc_id`; one with a ` R000` suffix also matches, as `_backresolve_citing_docs` strips it);
  - `test_plan_number_moves_only_when_class_changes_and_a_value_is_set` (ISO->DoC with `028/MDR` -> True; ISO->EC -> False; ISO->DoC with both NULL -> False).
- [ ] **Step 2: Run** `./scripts/test.sh tests/test_retype.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `app/retype.py`.** Active jobs: `status IN ('pending','running','failed')`; `type IN ('extract.doc','validate.doc','gate.candidate') AND payload->>'content_hash' = <hash>`, or `type='gate.apply' AND payload->>'doc_id' = str(doc_id) AND id IS DISTINCT FROM own_job_id` (`doc_id` is a JSON integer in every producer; test jobs may carry no id). Citations-set query: same predicate as `gate._backresolve_citing_docs` (keep them identical; a comment in both points at the other). Read-only: no write statement in the module.
- [ ] **Step 4: Run** `./scripts/test.sh tests/test_retype.py`. Expected: PASS.
- [ ] **Step 5: Write failing handler tests** `tests/test_gate_retype.py` (helpers modelled on `tests/test_gate_apply_items.py`), table-driven over (before, after):
  - Type-only retype ISO -> EC (scope unchanged): T3 rows for BOTH `type` and `coverage_scope`; `regulation` gets none.
  - Two retypes in a row (second after the first is done): the second's T3 rows have the higher `extract_rev` and the columns hold its values (latest retype prevails).
  - Whole range kept, type-only change, 1 staged link: link `rejected` (write 2 applies whenever the resulting scope is `manufacturer`).
  - Whole range, manufacturer with no MD item: task `reason == "md-class-unknown"`; with MD items: no `reason`.
  - A second open task of another kind on the doc: untouched; only the `gate-manual` one is rewritten.
  - ISO/manufacturer -> DoC/group (831): type and scope columns changed; one T3 evidence row each, `confidence 1.0`, `page NULL`; `_reviewer_held` returns both; `cert_number`, `validity_to`, `validity_from` unchanged; status `staged`; task payload has no `route`, no `reason`, no `flags`, has `retyped.by`; audit `retype` detail `before`/`after`; no `production-write`; no job enqueued (`job` count unchanged).
  - DoC/group with 2 staged links -> EC/manufacturer: both links `rejected`, two `link-rejected` rows with `detail.via == "retype"`; task `route == "mfr-binding"`, `manufacturer` from `canonical_manufacturer`; `citations_set` written by back-resolution.
  - EC -> DoC with a citing DoC: its `cert_doc_id` NULL; id in `citations_cleared`.
  - DoC with own `cert_doc_id` -> IFU: own `cert_doc_id` NULL.
  - Regulation only (Review Focus 5): scope, links and route unchanged, flags dropped.
  - No open task: one `gate-manual` task opened with the rewritten payload.
  - Unknown manufacturer -> manufacturer scope (Review Focus 4): route set, no `manufacturer` key.
  - EC with scope `group` (the doc 394 shape, a manual override of the default) is accepted and written as posted.
  - Scope `group` after the retype with 1 staged link: link untouched, no `link-rejected` row.
  - The open `gate-manual` task stays `open` after the retype (`_resolve_manual_tasks` not called).
  - Reject then Reopen of a retyped document: `_reviewer_held` still returns `type` and `coverage_scope`.
  - Each `problems` code raises `ValueError` and writes nothing (row, evidence, audit, links unchanged).
- [ ] **Step 6: Run** `./scripts/test.sh tests/test_gate_retype.py`. Expected: FAIL (`unknown decision 'retype'`).
- [ ] **Step 7: Implement.** In `handle_gate_apply`, before `prev_status`: `if decision == "retype": return _apply_retype(conn, job)` (like `LINK_DECISIONS`, so the shared tail never runs). `_apply_retype`: `SELECT ... FROM document WHERE doc_id=%s FOR UPDATE` first; `retype.problems(..., expected=p["expected"], own_job_id=job.get("id"))` -> raise `ValueError` naming the codes; `retype.plan`; then writes 1-7 from spec section 6 in that order, `_apply_edits` with `type` and `coverage_scope` always and `regulation` only when changed; `_retarget_task(conn, doc_id, scope, {"by": decided_by, "at": <now, ISO>})` rewrites the open `gate-manual` task payload only (or inserts one) per spec write 6, recomputing `reason` for whole range (shared with Task 5); audit `retype` with `{before, after, links_off, citations_cleared, citations_set, note?}`.
- [ ] **Step 8: Run** `./scripts/test.sh tests/test_gate*.py tests/test_retype*.py`. Expected: PASS.
- [ ] **Step 9: Run the full suite** `./scripts/test.sh` (this task changes `handle_gate_apply`'s shared dispatch). Expected: all green; report the count.
- [ ] **Step 10: Commit** `gate: retype a staged document, with its links, citations and task`.

---

### Task 4: Row lock, expected scope, and scope guards

The Type field and the approve type refusal are NOT here: they move with the control that replaces them (Task 6), so no state of the branch leaves a reviewer without a way to change a type, and no template is edited before the mockup is approved.

**Files:**
- Modify: `app/handlers/gate.py` (`handle_gate_apply` lock; `approve` and `bind-manufacturer` branches)
- Modify: `web/app.py` (`staging_apply`; `_staged_doc_detail` panel rule; `_REVIEW_ROWS` new active-link count)
- Modify: `web/templates/_staging_doc_detail.html` (one hidden input `expected_scope` in the decision form; no visible change, so no mockup needed)
- Modify: `docs/dev/handlers.md` (~line 129: the unlocked-read follow-up is closed)
- Test: `tests/test_gate_apply_handler.py`, `tests/test_web_review_safety.py`, `tests/test_web_review_context.py`

- [ ] **Step 1: Write failing tests:**
  - `test_bind_manufacturer_refuses_a_group_scope_document` (Review Focus 1);
  - `test_approve_refuses_when_expected_scope_differs` and the same for `bind-manufacturer` (stale screen after a retype; a group-panel Approve with no items must not publish a now whole-range document);
  - `test_every_document_decision_locks_the_row`: a second connection holding `FOR UPDATE` on the row makes `approve`, `reject`, `reopen`, `bind-manufacturer`, `add-items`, `retype` block (use `SET lock_timeout = '200ms'` in the handler connection and assert the lock-timeout error);
  - `test_approve_with_items_on_a_retyped_whole_range_document_is_refused` (existing `_picked_items` guard, pinned for the retype case);
  - route: `test_whole_range_approval_on_a_group_document_is_refused`;
  - route: `test_approve_and_bind_send_the_scope_the_screen_showed` (`expected_scope` in the enqueued payload);
  - route: `test_approve_reject_and_bind_are_refused_while_a_retype_is_pending` (uses `retype.retype_active`);
  - render: `test_whole_range_document_with_only_rejected_links_opens_the_whole_range_panel` (detail page and board row agree);
  - render: `test_a_reopened_group_document_still_groups_under_its_items_manufacturer` (the board's `group_name` keeps reading rejected links, unchanged).
  Check bind seeds in `test_gate_apply_handler.py`, `test_gate_candidate_handler.py`, `test_web.py`, `test_reextract.py`, `test_repair_mfr_overbind.py` for binds on documents not seeded `manufacturer`, and fix the seed, not the guard.
- [ ] **Step 2: Run** `./scripts/test.sh tests/test_gate*.py tests/test_web*.py`. Expected: the new ones FAIL.
- [ ] **Step 3: Implement.** GATE `handle_gate_apply`: for every non-link decision, `SELECT status, coverage_scope FROM document WHERE doc_id=%s FOR UPDATE` replaces the unlocked `_doc_status` read and runs before `_picked_items`. `approve` and `bind-manufacturer`: raise if `expected_scope` is present and differs from the row (absent = a job queued before the deploy, accepted). `bind-manufacturer`: raise unless the row's `coverage_scope == 'manufacturer'`. Route: send `expected_scope` from the rendered row with approve and bind; refuse approve, reject and bind while `retype.retype_active`. Panel rule: do NOT change the `_REVIEW_ROWS` `links` CTE (its `manufacturers` feeds `group_name`, `web/app.py:~1086`, and a reopened document's links are all `rejected`); add a separate `active_link_count` (links not `retracted` and not `rejected`) and use it in `is_mfr_binding` there; in `_staged_doc_detail` (`web/app.py:~1677`) test a count of non-rejected links, leaving the displayed `links` list as it is.
- [ ] **Step 4: Run** `./scripts/test.sh tests/test_gate*.py tests/test_web*.py`. Expected: PASS.
- [ ] **Step 5: Commit** `gate, review: lock the document for every decision and check the scope the screen showed`.

---

### Task 5: `route-held` on `gate.candidate`

**Files:**
- Modify: `app/handlers/gate.py` (`_gate_candidate`, before the `route == "mfr-binding"` dispatch)
- Test: `tests/test_gate_route_held.py`

**Interfaces:**
- Consumes: `_reviewer_held`, `_retarget_task` (Task 3).
- Produces: candidate result `{"disposition": "route-held", "doc_id", "held_scope"}`.

- [ ] **Step 1: Write failing tests**, each seeding a staged document with T3 `coverage_scope` evidence and no open task:
  - held `group`, candidate `route = "mfr-binding"` with a confidently resolving manufacturer: document stays `staged`, no `item_document` row, no `bind-manufacturer` / `production-write` audit, held columns unchanged, a `gate-manual` task without `route`, result `route-held`;
  - held `manufacturer`, group candidate whose links would match two items at a trusted basis: no link written, status `staged`, task `route == "mfr-binding"`, result `route-held`;
  - held scope agreeing with the route: same branch, still no machine bind;
  - no held scope: existing behaviour unchanged (one existing mfr-binding and one group test re-run as the control);
  - held `group` on a `production` document, confident `mfr-binding` reading with MD items: no link written (today this links every MD item silently), status unchanged, result `route-held`, no task;
  - held scope on a `production` document, group reading proposing staged links: none written (the R1 staged proposals stop for these documents);
  - held scope on `rejected`, `superseded`, `filed`: kept, `route-held`, no task;
  - staged route-held upsert carries none of the reading's `supersedes`, `related_doc_id`, `cert_doc_id`, `canonical_manufacturer`;
  - a superseded reading (not the current rev) is still a no-op, before the branch, on BOTH routes;
  - a staged held-scope document with a reading that would auto-supersede it (`superseded_by_doc_id` in the payload, `auto-superseded` flag): no `superseded` status, no pointer, result `route-held`;
  - held `coverage_scope` with no `retype` audit row (T3 written some other way): task written without `retyped`, and Review shows its usual reason.
- [ ] **Step 2: Run** `./scripts/test.sh tests/test_gate_route_held.py`. Expected: FAIL.
- [ ] **Step 3: Implement** one branch in `_gate_candidate` before the `route == "mfr-binding"` dispatch (`gate.py:~1395`), per spec section 9. That dispatch runs BEFORE today's `_is_current_rev` check (`~1410`; `_handle_mfr_binding` has its own at `~1192`), so the branch calls `_is_current_rev` itself first and returns the existing no-op result when the reading is stale. Then lock the row; `_reviewer_held`; if `coverage_scope` is held: `production/superseded/rejected/filed` -> `_kept(..., {"disposition": "route-held", ...})`; `staged` -> `_read_ext`, `_require_complete_evidence` (it returns flags, it does not raise; they are dropped with the reading's other flags), `_upsert_document(status="staged", held=held)` passing no `supersedes`, `related_doc_id` (column `referenced_doc_id`), `cert_doc_id` or `canonical_manufacturer`, `_insert_evidence(held=held)`, `_backresolve_here`, `_retarget_task` with `retyped` from the latest `retype` audit row, or `None` when there is none (then the task carries no `retyped`); never `_retract_unsupported_links`, `_upsert_link`, `_resolve_manual_tasks`. `_retarget_task`'s `retyped` parameter is therefore `dict | None`.
- [ ] **Step 4: Run** `./scripts/test.sh tests/test_gate*.py`. Expected: PASS.
- [ ] **Step 5: Commit** `gate: a reading never re-routes a document whose scope a person set`.

---

### Task 6: Review control, confirm, route (after Denis approves the mockup)

**Files:**
- Modify: `web/app.py` (new `POST /staging/{doc_id}/retype`; `DECISION_RECEIPTS["retype"]`; `_review_reason` gains `retyped: dict | None = None`, and both callers, the board row and `_staged_doc_detail`, pass the task payload's `retyped`; `staging_apply` refuses a changed type and raw `type`/`coverage_scope` edits)
- Modify: `app/handlers/gate.py` (`approve` refuses `type` and `coverage_scope` edits)
- Modify: `web/words.py` (consequence sentences, tick sentence)
- Modify: `web/templates/_staging_doc_detail.html` (the control and its form, per the approved mockup; remove the "Document type" select from Correct a fact first, keep "Rules")
- Modify: `web/templates/_result.html` (optional `confirm_tick`: a required checkbox `tick` with that label inside the confirm form)
- Create: `web/static/retype.js` (only: on Type change, set "What it covers" to `default_scope`; no other logic)
- Modify: `docs/guide/pages/review.md`, `review.sl.md` (Correct a fact first no longer lists the type; the new section itself is Task 7)
- Test: `tests/test_web_retype.py`, `tests/test_gate_apply_handler.py`, `tests/test_web_review_safety.py`

**Interfaces:**
- Consumes: `app.retype.problems`, `app.retype.plan`, `app.retype.default_scope`, `TYPE_CHOICES`, `REGULATION_CHOICES`.
- Form fields: `type`, `regulation`, `coverage_scope`, `expected_type`, `expected_regulation`, `expected_coverage_scope`, `note`, `confirm`, `tick`.

- [ ] **Step 1: Write failing route tests:**
  - step 1 returns the confirm and enqueues nothing; for an 831-shaped doc the lines are exactly spec section 5 lines 1 and 6; for an 832-shaped doc line 5 names `028/MDR` as "the certificate this declaration cites" and `2027-05-25`;
  - line 2 with 12 staged links names the first 10 and "and 2 more"; with 0 the line is absent;
  - line 3 lists the citing doc ids; absent with none;
  - line 4 with a certificate number gives the count; with none it reads "No declaration can point to it until a certificate number is entered.";
  - `unchanged` -> exactly "Nothing to change. Nothing was changed.";
  - each `problems` code maps to a 422 sentence ending "Nothing was changed." and enqueues nothing; `in-flight` says a reading of this document is in progress and to try again later; `in-chain` says the document is part of a chain of versions and its type cannot be changed here;
  - step 2 without `tick` -> 422 "Tick the box to confirm. Nothing was changed.";
  - step 2 with `tick` enqueues one `gate.apply` with dedupe `apply:{id}:retype` and the payload of Task 3, and the response carries `HX-Retarget: #doc-{id}` / `HX-Reswap: outerHTML`;
  - double submit (Review Focus 2): a second step-2 POST while the first job is pending returns the "Already in progress." receipt; after the first job is marked done and the row changed, a resubmit is `stale`;
  - render: the control appears on a staged document in both panels and on no other status; the form is pre-filled; the board reason reads "Type changed by <name> on <date>." when `payload.retyped` is set;
  - render (Review Focus 4): retyped to whole range with no manufacturer shows the existing "unknown" state;
  - end to end: step 2 POST, then run the enqueued job through `gate.handle_gate_apply`, then GET the detail: the row is in the panel for the new scope with "Type changed by";
  - GATE `test_approve_refuses_a_type_edit` and `test_approve_refuses_a_coverage_scope_edit` (raises, document still staged, no T3 row);
  - route: `test_approve_with_a_changed_edit_type_is_refused_and_enqueues_nothing` (422, "Change the type with Change type first. Nothing was changed.", `_gate_apply_count` unchanged); `test_approve_with_an_unchanged_edit_type_still_passes` (a page loaded before the deploy); `test_raw_edits_with_type_or_coverage_scope_are_refused` (Review Focus 3);
  - render: `test_correct_a_fact_first_has_no_type_field`.
  Rewrite, not delete: `tests/test_gate_reread_guards.py:~318` (approve with `edits={"type": "EC"}`) to set the type with a `retype` job; `tests/test_web_review_safety.py:~518` (expects `edit_type` among the correction fields).
- [ ] **Step 2: Run** `./scripts/test.sh tests/test_web*.py tests/test_gate*.py`. Expected: the new ones FAIL.
- [ ] **Step 3: Implement** the route on the `staging_apply` two-step pattern (identity from `_authenticated_user`, then `DEFAULT_DECIDED_BY`); confirm via `_result.html` with `confirm_fields` carrying the form values and the `expected_*` read at step 1; template and JS per the mockup.
- [ ] **Step 4: Run** `./scripts/test.sh tests/test_web*.py tests/test_gate*.py tests/test_retype*.py`. Expected: PASS.
- [ ] **Step 5:** `python3 scripts/build-guide.py`; `./scripts/test.sh tests/test_docs_sets.py`. Expected: PASS.
- [ ] **Step 6: Commit** `review: change a document's type, with every consequence shown first`.

---

### Task 7: Guide and developer docs

**Files:**
- Modify: `docs/guide/pages/review.md`, `docs/guide/pages/review.sl.md` (new "Changing what kind of document it is" under Step by step: when to use it, the confirm, that it stays on Review, published documents: Reject, Reopen, Change type)
- Modify: `docs/dev/handlers.md` (`gate.apply` row: `retype`; `gate.candidate`: `route-held`)
- Modify: `docs/dev/limits.md` (own forward `cert_doc_id` not resolved when a document becomes a DoC; published documents need Reject and Reopen first)
- Modify: `docs/vocabulary.md` if it lists dispositions or decisions
- Rebuild: `python3 scripts/build-guide.py` (commit `docs/guide/dentalia-guide-EN.html`, `-SL.html`)

- [ ] **Step 1:** Write the EN page section, then the SL one with the same structure.
- [ ] **Step 2:** Dev docs and limits rows.
- [ ] **Step 3:** `python3 scripts/build-guide.py`; `./scripts/test.sh tests/test_docs_sets.py`. Expected: PASS.
- [ ] **Step 4: Commit** `docs: Change type in the guide and developer docs`.

---

### Task 8: Full suite and hand-off

- [ ] **Step 1:** `./scripts/test.sh`. Expected: all green; report the count and any failure with its output.
- [ ] **Step 2:** Walk spec sections 2-10 against the diff (memory: verify against the spec, not the tests) and list any bullet with no code.
- [ ] **Step 3:** Update `tasks/todo.md` (1a, 1b) and `PHASES.md` if it tracks this; append follow-ups to `tasks/followups.md`: the 7 EC/ISO documents mentioning a declaration (2196, 2197, 87, 88, 861, 1164, 1732), and the 4 published suspects (87, 472, 703, 1265).
- [ ] **Step 4:** Stop. Deploy, and the `reextract` of 831-837, only when Denis asks.

## Decisions from review

Fable review 2026-11-12, verified against the code and production (read-only); Denis ruled 1-5 the same day.

1. Retype holds `type` and `coverage_scope` always, `regulation` when changed; held until the next manual retype, latest prevails (Task 3).
2. Route-held at every status; a published retyped document gets no further machine links, including the R1 staged proposals; a person uses add-items (Task 5).
3. Retype refused on supersession-chain members, never clears pointers. Production: 0 staged documents in a chain (Task 3).
4. Approve refuses `type` and `coverage_scope` edits, refused not dropped. Production: 0 queued approvals with a type edit (Task 6, with the control that replaces the field).
5. Every `gate.apply` document decision locks the row first; approve and bind carry `expected_scope`; Review refuses Approve/Reject/bind while a retype is pending (Task 4).

My calls folded in (spec marks them): `md-class-unknown` recomputed, not dropped; own `_review_reason` branch for "Type changed by"; only the `gate-manual` task rewritten; write 2 whenever the resulting scope is whole range. The in-flight check is best-effort (fetch, upload, email, discover and backfill enqueue readings later without a `content_hash`); route-held is the real guard, so Tasks 3 and 5 ship together.

Plan review 2026-11-12 (three fast reviewers: spec coverage, code accuracy, risk and ordering), findings verified against the code: Type-field removal moved from Task 4 to Task 6; branch-and-merge-as-one-unit rule; route-held runs its own `_is_current_rev` (the whole-range dispatch precedes it); the `_REVIEW_ROWS` links CTE is left alone because it feeds `group_name`; test runs use the whole globs, plus a full suite before the Task 3 commit; tests added for consequence lines 2-4, the exact "Nothing to change." text, the EC/group override, group scope touching no link, the task staying open, Reopen keeping the hold, auto-supersede pre-empted, and no-retype-audit fallback. The 4 published suspects in Task 8 (87, 472, 703, 1265) come from the 2026-11-12 production query, not the spec.

## Estimate

| # | Task | Human | Claude Code |
|---|---|---|---|
| 0 | T0 marker | 1 h | 0.3 h |
| 1 | Contract and records | 1.5 h | 0.4 h |
| 2 | Mockup | 1.5 h | 0.4 h |
| 3 | `app/retype.py` and GATE `retype` | 6 h | 2 h |
| 4 | Guards, row lock, expected scope | 4 h | 1.3 h |
| 5 | Route-held | 3 h | 1 h |
| 6 | Review control and route | 3 h | 1.2 h |
| 7 | Guide and developer docs | 1.5 h | 0.4 h |
| 8 | Full suite and hand-off | 0.5 h | 0.5 h |
| | **Total** | **22 h** | **7.5 h** |
